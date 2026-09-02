from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re
from typing import Any

from backend.tools.registry import ToolResult


VERIFICATION_COMMAND_MARKERS = {
    "test", "tests", "pytest", "spec", "check", "typecheck", "lint", "build",
    "compile", "compileall", "mypy", "ruff", "eslint", "vitest", "jest",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


@dataclass
class RequirementEvidence:
    evidence_id: str
    evidence_type: str
    tool: str
    passed: bool
    summary: str
    artifact: str = ""
    command: str = ""
    association_source: str = "plan_fallback"
    sequence: int = 0
    timestamp: str = field(default_factory=_now)


@dataclass
class TracedRequirement:
    requirement_id: str
    description: str
    priority: str = "must"
    verification: str = "automated_test"
    status: str = "pending"
    last_modified_sequence: int = 0
    last_verified_sequence: int = 0
    latest_verification_passed: bool | None = None
    evidence: list[RequirementEvidence] = field(default_factory=list)


class TraceabilityLedger:
    """Deterministic requirement-to-evidence ledger for confirmed product criteria."""

    def __init__(self, criteria: list[Any], restored: dict[str, Any] | None = None) -> None:
        self.requirements: dict[str, TracedRequirement] = {}
        self.sequence = 0
        self._load_criteria(criteria)
        if restored:
            self._restore(restored)

    @property
    def active(self) -> bool:
        return bool(self.requirements)

    @property
    def requirement_ids(self) -> list[str]:
        return list(self.requirements)

    def observe(self, tool: str, arguments: dict[str, Any], result: ToolResult) -> bool:
        if not self.active or tool not in {"create_file", "apply_patch", "run_command", "read_file"}:
            return False
        explicit_ids = self._valid_ids(arguments.get("requirement_ids"))
        association_source = "explicit" if explicit_ids else "plan_fallback"

        if tool in {"create_file", "apply_patch"}:
            if not result.ok:
                return False
            # A broad edit must not silently claim every requirement. We only
            # infer the association when the task has one unambiguous criterion.
            target_ids = explicit_ids or (self.requirement_ids if len(self.requirements) == 1 else [])
            if not target_ids:
                return False
            self.sequence += 1
            artifact = str(result.data.get("path", arguments.get("path", "")))
            for requirement_id in target_ids:
                requirement = self.requirements[requirement_id]
                requirement.last_modified_sequence = self.sequence
                requirement.latest_verification_passed = None
                requirement.evidence.append(RequirementEvidence(
                    evidence_id=f"EV-{self.sequence:04d}-{requirement_id}",
                    evidence_type="implementation",
                    tool=tool,
                    passed=True,
                    summary=result.summary,
                    artifact=artifact,
                    association_source=association_source,
                    sequence=self.sequence,
                ))
                self._recompute(requirement)
            return bool(target_ids)

        if tool == "run_command":
            command = str(result.data.get("command", arguments.get("command", "")))
            if not self._is_verification_command(command):
                return False
            implemented = [
                requirement.requirement_id
                for requirement in self.requirements.values()
                if requirement.last_modified_sequence > 0 and requirement.verification != "human_review"
            ]
            target_ids = [
                requirement_id
                for requirement_id in (explicit_ids or implemented)
                if self.requirements[requirement_id].verification != "human_review"
            ]
            if not target_ids:
                return False
            self.sequence += 1
            for requirement_id in target_ids:
                requirement = self.requirements[requirement_id]
                requirement.latest_verification_passed = result.ok
                if result.ok:
                    requirement.last_verified_sequence = self.sequence
                requirement.evidence.append(RequirementEvidence(
                    evidence_id=f"EV-{self.sequence:04d}-{requirement_id}",
                    evidence_type="verification",
                    tool=tool,
                    passed=result.ok,
                    summary=result.summary if result.ok else (result.error or result.summary),
                    command=command,
                    association_source=association_source,
                    sequence=self.sequence,
                ))
                self._recompute(requirement)
            return bool(target_ids)

        if tool == "read_file" and result.ok and explicit_ids:
            self.sequence += 1
            artifact = str(result.data.get("path", arguments.get("path", "")))
            for requirement_id in explicit_ids:
                requirement = self.requirements[requirement_id]
                if requirement.verification == "human_review" and requirement.last_modified_sequence > 0:
                    requirement.latest_verification_passed = True
                    requirement.last_verified_sequence = self.sequence
                requirement.evidence.append(RequirementEvidence(
                    evidence_id=f"EV-{self.sequence:04d}-{requirement_id}",
                    evidence_type="review",
                    tool=tool,
                    passed=True,
                    summary=result.summary,
                    artifact=artifact,
                    association_source="explicit",
                    sequence=self.sequence,
                ))
                self._recompute(requirement)
            return True
        return False

    def blocking_gaps(self) -> list[dict[str, str]]:
        gaps: list[dict[str, str]] = []
        for requirement in self.requirements.values():
            if requirement.priority != "must" or requirement.status == "verified":
                continue
            if requirement.status == "pending":
                reason = "尚未找到关联的实现文件"
            elif requirement.status == "implemented":
                reason = "已有实现证据，但修改后缺少成功验证"
            else:
                reason = "最近一次关联验证失败"
            gaps.append({
                "requirement_id": requirement.requirement_id,
                "description": requirement.description,
                "status": requirement.status,
                "reason": reason,
            })
        return gaps

    def gap_instruction(self) -> str:
        lines = ["以下已确认需求仍缺少闭环证据："]
        for gap in self.blocking_gaps():
            lines.append(f"- {gap['requirement_id']} {gap['description']}：{gap['reason']}")
        lines.append("请继续实现或运行关联验证；再次调用工具时可用 requirement_ids 标注对应验收项。")
        return "\n".join(lines)

    def public_dict(self) -> dict[str, Any]:
        requirements = []
        counts = {"pending": 0, "implemented": 0, "verified": 0, "failed": 0}
        for requirement in self.requirements.values():
            counts[requirement.status] += 1
            requirements.append({
                **asdict(requirement),
                "id": requirement.requirement_id,
            })
        total = len(requirements)
        verified = counts["verified"]
        return {
            "active": self.active,
            "total": total,
            "verified": verified,
            "coverage_percent": round((verified / total) * 100) if total else 100,
            "counts": counts,
            "requirements": requirements,
            "sequence": self.sequence,
        }

    def _load_criteria(self, criteria: list[Any]) -> None:
        for index, item in enumerate(criteria, start=1):
            if isinstance(item, dict):
                requirement_id = str(item.get("id") or f"AC-{index:02d}").strip()
                description = str(item.get("description") or item.get("text") or "").strip()
                priority = str(item.get("priority", "must")).strip() or "must"
                verification = str(item.get("verification", "automated_test")).strip() or "automated_test"
            else:
                requirement_id = f"AC-{index:02d}"
                description = str(item).strip()
                priority = "must"
                verification = "automated_test"
            if not description or requirement_id in self.requirements:
                continue
            self.requirements[requirement_id] = TracedRequirement(
                requirement_id=requirement_id,
                description=description,
                priority=priority if priority in {"must", "should"} else "must",
                verification=verification,
            )

    def _restore(self, snapshot: dict[str, Any]) -> None:
        raw_requirements = snapshot.get("requirements", [])
        if not isinstance(raw_requirements, list):
            return
        for raw in raw_requirements:
            if not isinstance(raw, dict):
                continue
            requirement_id = str(raw.get("requirement_id") or raw.get("id") or "")
            requirement = self.requirements.get(requirement_id)
            if not requirement:
                continue
            requirement.last_modified_sequence = int(raw.get("last_modified_sequence", 0) or 0)
            requirement.last_verified_sequence = int(raw.get("last_verified_sequence", 0) or 0)
            latest = raw.get("latest_verification_passed")
            requirement.latest_verification_passed = latest if isinstance(latest, bool) else None
            evidence_items: list[RequirementEvidence] = []
            for evidence in raw.get("evidence", []):
                if not isinstance(evidence, dict):
                    continue
                try:
                    evidence_items.append(RequirementEvidence(
                        evidence_id=str(evidence["evidence_id"]),
                        evidence_type=str(evidence["evidence_type"]),
                        tool=str(evidence.get("tool", "")),
                        passed=bool(evidence.get("passed")),
                        summary=str(evidence.get("summary", "")),
                        artifact=str(evidence.get("artifact", "")),
                        command=str(evidence.get("command", "")),
                        association_source=str(evidence.get("association_source", "plan_fallback")),
                        sequence=int(evidence.get("sequence", 0) or 0),
                        timestamp=str(evidence.get("timestamp", "")) or _now(),
                    ))
                except KeyError:
                    continue
            requirement.evidence = evidence_items[-30:]
            self.sequence = max(
                self.sequence,
                requirement.last_modified_sequence,
                requirement.last_verified_sequence,
                *(item.sequence for item in evidence_items),
            )
            self._recompute(requirement)

    def _valid_ids(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(
            str(item) for item in value if str(item) in self.requirements
        ))

    @staticmethod
    def _is_verification_command(command: str) -> bool:
        normalized = command.lower().replace("-", "_")
        tokens = {token for token in re.split(r"[^a-z0-9_]+", normalized) if token}
        return bool(tokens & VERIFICATION_COMMAND_MARKERS)

    @staticmethod
    def _recompute(requirement: TracedRequirement) -> None:
        if requirement.latest_verification_passed is False:
            requirement.status = "failed"
        elif (
            requirement.last_modified_sequence > 0
            and requirement.last_verified_sequence > requirement.last_modified_sequence
            and requirement.latest_verification_passed is True
        ):
            requirement.status = "verified"
        elif requirement.last_modified_sequence > 0:
            requirement.status = "implemented"
        else:
            requirement.status = "pending"
