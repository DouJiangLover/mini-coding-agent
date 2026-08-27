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

    def create(self, run_id: str) -> None:
        self._channels[run_id] = EventChannel(run_id=run_id)

    def get(self, run_id: str) -> EventChannel | None:
        return self._channels.get(run_id)

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
