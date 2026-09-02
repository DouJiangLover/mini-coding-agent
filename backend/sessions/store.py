from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


@dataclass
class SessionEntry:
    entry_id: str
    parent_id: str | None
    run_id: str
    task: str
    root_task: str
    created_at: str = field(default_factory=_now)
    status: str = "created"
    summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    successful_commands: list[str] = field(default_factory=list)
    structured_summary: dict[str, Any] | None = None
    steering_messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SessionRecord:
    session_id: str
    workspace: str
    root_task: str
    created_at: str = field(default_factory=_now)
    entries: dict[str, SessionEntry] = field(default_factory=dict)
    latest_entry_id: str | None = None


class SessionStore:
    """Append-only JSONL session storage with tree-shaped message entries."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, SessionRecord] = {}
        self._run_index: dict[str, tuple[str, str]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        for path in sorted(self.root.glob("session_*.jsonl")):
            record: SessionRecord | None = None
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                event_type = item.get("type")
                if event_type == "session_created":
                    record = SessionRecord(
                        session_id=str(item.get("session_id", path.stem)),
                        workspace=str(item.get("workspace", ".")),
                        root_task=str(item.get("root_task", "")),
                        created_at=str(item.get("created_at", "")) or _now(),
                    )
                elif event_type == "entry_created" and record:
                    payload = item.get("entry", {})
                    if not isinstance(payload, dict):
                        continue
                    try:
                        entry = SessionEntry(
                            entry_id=str(payload["entry_id"]),
                            parent_id=str(payload["parent_id"]) if payload.get("parent_id") else None,
                            run_id=str(payload["run_id"]),
                            task=str(payload.get("task", "")),
                            root_task=str(payload.get("root_task", record.root_task)),
                            created_at=str(payload.get("created_at", "")) or _now(),
                            status=str(payload.get("status", "created")),
                            summary=str(payload.get("summary", "")),
                            changed_files=[str(value) for value in payload.get("changed_files", [])],
                            successful_commands=[str(value) for value in payload.get("successful_commands", [])],
                            structured_summary=payload.get("structured_summary") if isinstance(payload.get("structured_summary"), dict) else None,
                            steering_messages=[dict(value) for value in payload.get("steering_messages", []) if isinstance(value, dict)],
                        )
                    except KeyError:
                        continue
                    record.entries[entry.entry_id] = entry
                    record.latest_entry_id = entry.entry_id
                elif event_type == "entry_updated" and record:
                    entry = record.entries.get(str(item.get("entry_id", "")))
                    updates = item.get("updates", {})
                    if not entry or not isinstance(updates, dict):
                        continue
                    self._apply_updates(entry, updates)
                elif event_type == "context_compacted" and record:
                    entry = record.entries.get(str(item.get("entry_id", "")))
                    summary = item.get("summary")
                    if entry and isinstance(summary, dict):
                        entry.structured_summary = summary
                elif event_type == "steering_received" and record:
                    entry = record.entries.get(str(item.get("entry_id", "")))
                    message = item.get("message")
                    if entry and isinstance(message, dict):
                        entry.steering_messages.append(dict(message))
                elif event_type == "steering_applied" and record:
                    entry = record.entries.get(str(item.get("entry_id", "")))
                    steering_id = str(item.get("steering_id", ""))
                    if entry:
                        for message in entry.steering_messages:
                            if message.get("steering_id") == steering_id:
                                message["status"] = "applied"
                                message["applied_at"] = str(item.get("applied_at", ""))
            if not record:
                continue
            self._sessions[record.session_id] = record
            for entry in record.entries.values():
                self._run_index[entry.run_id] = (record.session_id, entry.entry_id)

    def create(self, workspace: str, root_task: str) -> SessionRecord:
        session_id = f"session_{uuid.uuid4().hex[:12]}"
        record = SessionRecord(session_id=session_id, workspace=workspace, root_task=root_task)
        with self._lock:
            self._sessions[session_id] = record
            self._append(session_id, {
                "type": "session_created",
                "session_id": session_id,
                "workspace": workspace,
                "root_task": root_task,
                "created_at": record.created_at,
            })
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def by_run(self, run_id: str) -> tuple[SessionRecord, SessionEntry] | None:
        location = self._run_index.get(run_id)
        if not location:
            return None
        record = self._sessions.get(location[0])
        if not record:
            return None
        entry = record.entries.get(location[1])
        return (record, entry) if entry else None

    def create_entry(
        self,
        session_id: str,
        *,
        run_id: str,
        task: str,
        root_task: str,
        parent_id: str | None = None,
    ) -> SessionEntry:
        record = self._sessions.get(session_id)
        if not record:
            raise KeyError(session_id)
        if parent_id and parent_id not in record.entries:
            raise KeyError(parent_id)
        entry = SessionEntry(
            entry_id=f"entry_{uuid.uuid4().hex[:12]}",
            parent_id=parent_id,
            run_id=run_id,
            task=task,
            root_task=root_task,
        )
        with self._lock:
            record.entries[entry.entry_id] = entry
            record.latest_entry_id = entry.entry_id
            self._run_index[run_id] = (session_id, entry.entry_id)
            self._append(session_id, {"type": "entry_created", "entry": asdict(entry)})
        return entry

    def update_entry(
        self,
        entry_id: str,
        *,
        status: str,
        summary: str,
        changed_files: list[str],
        successful_commands: list[str],
        structured_summary: dict[str, Any] | None = None,
    ) -> None:
        located = self._entry_location(entry_id)
        if not located:
            return
        record, entry = located
        updates = {
            "status": status,
            "summary": summary,
            "changed_files": list(changed_files),
            "successful_commands": list(successful_commands),
            "structured_summary": structured_summary,
        }
        with self._lock:
            self._apply_updates(entry, updates)
            self._append(record.session_id, {
                "type": "entry_updated",
                "entry_id": entry_id,
                "updates": updates,
                "timestamp": _now(),
            })

    def append_compaction(self, session_id: str, entry_id: str, summary: dict[str, Any]) -> None:
        record = self._sessions.get(session_id)
        if not record or entry_id not in record.entries:
            return
        with self._lock:
            record.entries[entry_id].structured_summary = summary
            self._append(session_id, {
                "type": "context_compacted",
                "entry_id": entry_id,
                "summary": summary,
                "timestamp": _now(),
            })

    def append_steering(self, session_id: str, entry_id: str, message: dict[str, Any]) -> None:
        record = self._sessions.get(session_id)
        if not record or entry_id not in record.entries:
            return
        with self._lock:
            record.entries[entry_id].steering_messages.append(dict(message))
            self._append(session_id, {
                "type": "steering_received",
                "entry_id": entry_id,
                "message": message,
                "timestamp": _now(),
            })

    def mark_steering_applied(
        self,
        session_id: str,
        entry_id: str,
        steering_id: str,
        applied_at: str,
    ) -> None:
        record = self._sessions.get(session_id)
        if not record or entry_id not in record.entries:
            return
        with self._lock:
            for message in record.entries[entry_id].steering_messages:
                if message.get("steering_id") == steering_id:
                    message["status"] = "applied"
                    message["applied_at"] = applied_at
                    break
            self._append(session_id, {
                "type": "steering_applied",
                "entry_id": entry_id,
                "steering_id": steering_id,
                "applied_at": applied_at,
                "timestamp": _now(),
            })

    def path(self, session_id: str, entry_id: str | None = None) -> list[SessionEntry]:
        record = self._sessions.get(session_id)
        if not record:
            return []
        cursor = entry_id or record.latest_entry_id
        result: list[SessionEntry] = []
        visited: set[str] = set()
        while cursor and cursor not in visited:
            visited.add(cursor)
            entry = record.entries.get(cursor)
            if not entry:
                break
            result.append(entry)
            cursor = entry.parent_id
        result.reverse()
        return result

    def public_tree(self, session_id: str, active_entry_id: str | None = None) -> dict[str, Any] | None:
        record = self._sessions.get(session_id)
        if not record:
            return None
        children: dict[str | None, list[str]] = {}
        for entry in record.entries.values():
            children.setdefault(entry.parent_id, []).append(entry.entry_id)
        items: list[dict[str, Any]] = []

        def walk(entry_id: str, depth: int) -> None:
            entry = record.entries[entry_id]
            items.append({
                **asdict(entry),
                "depth": depth,
                "child_count": len(children.get(entry_id, [])),
                "active": entry.entry_id == active_entry_id,
            })
            for child_id in sorted(children.get(entry_id, []), key=lambda value: record.entries[value].created_at):
                walk(child_id, depth + 1)

        for root_id in sorted(children.get(None, []), key=lambda value: record.entries[value].created_at):
            walk(root_id, 0)
        return {
            "session_id": record.session_id,
            "workspace": record.workspace,
            "root_task": record.root_task,
            "created_at": record.created_at,
            "active_entry_id": active_entry_id or record.latest_entry_id,
            "entries": items,
        }

    def latest_summary(self, session_id: str, entry_id: str | None = None) -> dict[str, Any] | None:
        for entry in reversed(self.path(session_id, entry_id)):
            if entry.structured_summary:
                return entry.structured_summary
        return None

    def migrate_runs(self, run_records: Iterable[Any]) -> None:
        records = sorted(run_records, key=lambda item: item.created_at)
        pending = list(records)
        while pending:
            progressed = False
            for run in list(pending):
                existing = self.by_run(run.run_id)
                if existing:
                    run.session_id = existing[0].session_id
                    run.session_entry_id = existing[1].entry_id
                    run.parent_entry_id = existing[1].parent_id
                    pending.remove(run)
                    progressed = True
                    continue
                parent_location = self.by_run(run.parent_run_id) if run.parent_run_id else None
                if run.parent_run_id and not parent_location:
                    continue
                session = parent_location[0] if parent_location else self.create(run.workspace, run.root_task)
                parent_id = parent_location[1].entry_id if parent_location else None
                entry = self.create_entry(
                    session.session_id,
                    run_id=run.run_id,
                    task=run.task,
                    root_task=run.root_task,
                    parent_id=parent_id,
                )
                self.update_entry(
                    entry.entry_id,
                    status=run.status,
                    summary=run.summary,
                    changed_files=run.changed_files,
                    successful_commands=run.successful_commands,
                )
                run.session_id = session.session_id
                run.session_entry_id = entry.entry_id
                run.parent_entry_id = parent_id
                pending.remove(run)
                progressed = True
            if progressed:
                continue
            # Broken legacy parent links become independent recoverable sessions.
            run = pending.pop(0)
            session = self.create(run.workspace, run.root_task)
            entry = self.create_entry(
                session.session_id,
                run_id=run.run_id,
                task=run.task,
                root_task=run.root_task,
            )
            run.session_id = session.session_id
            run.session_entry_id = entry.entry_id

    def _entry_location(self, entry_id: str) -> tuple[SessionRecord, SessionEntry] | None:
        for record in self._sessions.values():
            entry = record.entries.get(entry_id)
            if entry:
                return record, entry
        return None

    @staticmethod
    def _apply_updates(entry: SessionEntry, updates: dict[str, Any]) -> None:
        entry.status = str(updates.get("status", entry.status))
        entry.summary = str(updates.get("summary", entry.summary))
        entry.changed_files = [str(value) for value in updates.get("changed_files", entry.changed_files)]
        entry.successful_commands = [str(value) for value in updates.get("successful_commands", entry.successful_commands)]
        if isinstance(updates.get("structured_summary"), dict):
            entry.structured_summary = updates["structured_summary"]

    def _append(self, session_id: str, payload: dict[str, Any]) -> None:
        path = self.root / f"{session_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
