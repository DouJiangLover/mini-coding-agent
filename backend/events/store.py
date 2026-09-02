from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class EventChannel:
    run_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    terminal: bool = False
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


class EventStore:
    def __init__(self, log_root: Path) -> None:
        self._channels: dict[str, EventChannel] = {}
        self._log_root = log_root
        self._log_root.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    def create(self, run_id: str) -> None:
        self._channels[run_id] = EventChannel(run_id=run_id)

    def get(self, run_id: str) -> EventChannel | None:
        return self._channels.get(run_id)

    def list_channels(self) -> list[EventChannel]:
        return list(self._channels.values())

    def _load_existing(self) -> None:
        for log_file in sorted(self._log_root.glob("run_*.jsonl")):
            run_id = log_file.stem
            restored: list[dict[str, Any]] = []
            try:
                for line in log_file.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and event.get("run_id") == run_id:
                        restored.append(event)
            except OSError:
                continue
            if not restored:
                continue

            terminal = any(event.get("type") == "run_finished" for event in restored)
            channel = EventChannel(run_id=run_id, events=restored, terminal=terminal)
            self._channels[run_id] = channel
            if terminal:
                continue

            changed_files = list(dict.fromkeys(
                str(event.get("payload", {}).get("path", ""))
                for event in restored
                if event.get("type") == "file_changed"
                and event.get("payload", {}).get("path")
            ))
            successful_commands = list(dict.fromkeys(
                str(event.get("payload", {}).get("command", ""))
                for event in restored
                if event.get("type") == "tool_finished"
                and event.get("status") == "success"
                and event.get("payload", {}).get("tool") == "run_command"
                and event.get("payload", {}).get("command")
            ))
            interrupted = {
                "event_id": len(restored) + 1,
                "run_id": run_id,
                "type": "run_finished",
                "phase": "failed",
                "status": "failed",
                "title": "任务已中断",
                "summary": "后端服务重启导致本次执行中断，可以在原工作区继续完成。",
                "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
                "payload": {
                    "status": "failed",
                    "changed_files": changed_files,
                    "successful_commands": successful_commands,
                    "interrupted": True,
                },
            }
            channel.events.append(interrupted)
            channel.terminal = True
            self._append_log(run_id, interrupted)

    async def emit(
        self,
        run_id: str,
        event_type: str,
        phase: str,
        status: str,
        title: str,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        channel = self._channels[run_id]
        event = {
            "event_id": len(channel.events) + 1,
            "run_id": run_id,
            "type": event_type,
            "phase": phase,
            "status": status,
            "title": title,
            "summary": summary,
            "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
            # Events are audit records. Keep a snapshot instead of references
            # to mutable plan lists that continue changing later in the run.
            "payload": deepcopy(payload or {}),
        }
        async with channel.condition:
            channel.events.append(event)
            if event_type == "run_finished":
                channel.terminal = True
            self._append_log(run_id, event)
            channel.condition.notify_all()
        return event

    def _append_log(self, run_id: str, event: dict[str, Any]) -> None:
        log_file = self._log_root / f"{run_id}.jsonl"
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def wait_for_events(self, run_id: str, cursor: int) -> tuple[list[dict[str, Any]], bool]:
        channel = self._channels[run_id]
        async with channel.condition:
            await channel.condition.wait_for(lambda: len(channel.events) > cursor or channel.terminal)
            return channel.events[cursor:], channel.terminal
