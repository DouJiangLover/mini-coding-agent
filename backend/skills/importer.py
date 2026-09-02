from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from backend.skills.router import AVAILABLE_SKILL_TOOLS


MAX_SKILL_UPLOAD_BYTES = 1_000_000
MAX_ARCHIVE_FILES = 32
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512_000

DEFAULT_IMPORTED_TOOLS = [
    "list_files",
    "read_file",
    "search_text",
    "apply_patch",
    "run_command",
    "finish",
]

TOOL_ALIASES = {
    "read": "read_file",
    "readfile": "read_file",
    "grep": "search_text",
    "search": "search_text",
    "glob": "list_files",
    "ls": "list_files",
    "list": "list_files",
    "write": "create_file",
    "create": "create_file",
    "edit": "apply_patch",
    "patch": "apply_patch",
    "bash": "run_command",
    "shell": "run_command",
    "terminal": "run_command",
    "command": "run_command",
}


class SkillImportError(ValueError):
    pass


@dataclass(frozen=True)
class ImportedSkill:
    display_name: str
    description: str
    keywords: list[str]
    allowed_tools: list[str]
    prompt: str
    source_format: str

    def create_kwargs(self) -> dict[str, object]:
        return {
            "display_name": self.display_name,
            "description": self.description,
            "keywords": self.keywords,
            "allowed_tools": self.allowed_tools,
            "prompt": self.prompt,
        }


def parse_skill_upload(filename: str, payload: bytes) -> ImportedSkill:
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not safe_name:
        raise SkillImportError("缺少 Skill 文件名")
    if not payload:
        raise SkillImportError("Skill 文件为空")
    if len(payload) > MAX_SKILL_UPLOAD_BYTES:
        raise SkillImportError("Skill 文件不能超过 1 MB")

    suffix = Path(safe_name).suffix.casefold()
    if suffix == ".zip":
        return _parse_zip(payload)
    if suffix == ".json":
        return _parse_json_text(_decode_text(payload), source_format="JSON")
    if suffix in {".md", ".markdown"}:
        return _parse_markdown(_decode_text(payload), fallback_name=Path(safe_name).stem)
    raise SkillImportError("仅支持 .zip、.json、.md 或 SKILL.md 文件")


def _parse_zip(payload: bytes) -> ImportedSkill:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise SkillImportError("ZIP 文件已损坏或格式不正确") from exc

    with archive:
        files = [item for item in archive.infolist() if not item.is_dir()]
        if not files or len(files) > MAX_ARCHIVE_FILES:
            raise SkillImportError(f"ZIP 中必须包含 1–{MAX_ARCHIVE_FILES} 个文件")
        if sum(item.file_size for item in files) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise SkillImportError("ZIP 解压后的文件总大小不能超过 512 KB")

        for item in files:
            normalized = item.filename.replace("\\", "/")
            path = PurePosixPath(normalized)
            file_mode = (item.external_attr >> 16) & 0o170000
            if path.is_absolute() or ".." in path.parts or (path.parts and path.parts[0].endswith(":")):
                raise SkillImportError("ZIP 中包含越界路径，已拒绝导入")
            if file_mode == 0o120000:
                raise SkillImportError("ZIP 中不能包含符号链接")
            if item.flag_bits & 0x1:
                raise SkillImportError("不支持加密的 ZIP 文件")

        metadata_files = [item for item in files if _archive_path(item).name.casefold() == "skill.json"]
        markdown_files = [item for item in files if _archive_path(item).name.casefold() == "skill.md"]
        if len(metadata_files) > 1 or (not metadata_files and len(markdown_files) > 1):
            raise SkillImportError("ZIP 中发现多个 Skill 定义，请每次只导入一个 Skill")

        if metadata_files:
            metadata_file = metadata_files[0]
            metadata = _load_json(_decode_text(archive.read(metadata_file)))
            prompt = _first_archive_text(
                archive,
                files,
                metadata_file,
                names=("prompt.md", "skill.md"),
            )
            return _from_mapping(metadata, prompt_override=prompt, source_format="ZIP")
        if markdown_files:
            item = markdown_files[0]
            return _parse_markdown(
                _decode_text(archive.read(item)),
                fallback_name=_archive_path(item).parent.name or "Imported Skill",
                source_format="ZIP / SKILL.md",
            )
    raise SkillImportError("ZIP 中未找到 skill.json 或 SKILL.md")


def _first_archive_text(
    archive: zipfile.ZipFile,
    files: list[zipfile.ZipInfo],
    metadata_file: zipfile.ZipInfo,
    *,
    names: tuple[str, ...],
) -> str | None:
    parent = _archive_path(metadata_file).parent
    for name in names:
        item = next(
            (
                candidate for candidate in files
                if _archive_path(candidate).parent == parent
                and _archive_path(candidate).name.casefold() == name.casefold()
            ),
            None,
        )
        if item is not None:
            return _decode_text(archive.read(item))
    return None


def _archive_path(item: zipfile.ZipInfo) -> PurePosixPath:
    return PurePosixPath(item.filename.replace("\\", "/"))


def _parse_json_text(text: str, *, source_format: str) -> ImportedSkill:
    return _from_mapping(_load_json(text), source_format=source_format)


def _load_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SkillImportError("Skill JSON 格式不正确") from exc
    if isinstance(data, dict) and isinstance(data.get("skill"), dict):
        data = data["skill"]
    if not isinstance(data, dict):
        raise SkillImportError("Skill JSON 顶层必须是对象")
    return data


def _parse_markdown(text: str, *, fallback_name: str, source_format: str = "SKILL.md") -> ImportedSkill:
    metadata: dict[str, Any] = {}
    body = text.strip()
    if body.startswith("---"):
        lines = body.splitlines()
        closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
        if closing is None:
            raise SkillImportError("SKILL.md 的元数据区缺少结束分隔线 ---")
        metadata = _parse_frontmatter(lines[1:closing])
        body = "\n".join(lines[closing + 1:]).strip()

    heading = re.search(r"(?m)^#\s+(.+?)\s*$", body)
    display_name = str(metadata.get("display_name") or metadata.get("name") or (heading.group(1) if heading else fallback_name))
    description = str(metadata.get("description") or _first_plain_paragraph(body) or f"用于执行 {display_name} 相关编程任务。")
    metadata = {
        **metadata,
        "display_name": display_name,
        "description": description,
        "prompt": body,
    }
    return _from_mapping(metadata, source_format=source_format)


def _parse_frontmatter(lines: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") and current_list_key:
            value = stripped[1:].strip().strip("'\"")
            if value:
                result.setdefault(current_list_key, []).append(value)
            continue
        if ":" not in stripped:
            continue
        raw_key, raw_value = stripped.split(":", 1)
        key = raw_key.strip().casefold().replace("-", "_")
        value = raw_value.strip()
        current_list_key = key if not value else None
        if not value:
            result[key] = []
        elif value.startswith("[") and value.endswith("]"):
            result[key] = _split_values(value[1:-1])
        else:
            result[key] = value.strip("'\"")
    return result


def _from_mapping(
    data: dict[str, Any],
    *,
    prompt_override: str | None = None,
    source_format: str,
) -> ImportedSkill:
    display_name = str(data.get("display_name") or data.get("title") or data.get("name") or "").strip()
    if len(display_name) < 2:
        display_name = f"{display_name or 'Imported'} Skill"
    description = str(data.get("description") or data.get("summary") or f"用于执行 {display_name} 相关编程任务。").strip()
    prompt = str(
        prompt_override
        or data.get("prompt")
        or data.get("instructions")
        or data.get("system_prompt")
        or data.get("strategy")
        or ""
    ).strip()
    keywords = _normalize_keywords(
        data.get("keywords")
        or data.get("trigger_keywords")
        or data.get("triggers")
        or display_name
    )
    allowed_tools = _normalize_tools(data.get("allowed_tools") or data.get("tools"))

    if not 2 <= len(display_name) <= 60:
        raise SkillImportError("导入的 Skill 名称需为 2–60 个字符")
    if not 4 <= len(description) <= 500:
        raise SkillImportError("导入的适用场景说明需为 4–500 个字符")
    if not 10 <= len(prompt) <= 4_000:
        raise SkillImportError("导入的执行策略需为 10–4000 个字符")

    return ImportedSkill(
        display_name=display_name,
        description=description,
        keywords=keywords,
        allowed_tools=allowed_tools,
        prompt=prompt,
        source_format=source_format,
    )


def _normalize_keywords(value: Any) -> list[str]:
    candidates = _as_string_list(value)
    normalized = list(dict.fromkeys(item.strip()[:40] for item in candidates if item.strip()))[:20]
    if not normalized:
        raise SkillImportError("导入的 Skill 缺少可用触发词")
    return normalized


def _normalize_tools(value: Any) -> list[str]:
    if value is None:
        return list(DEFAULT_IMPORTED_TOOLS)
    normalized: list[str] = []
    for item in _as_string_list(value):
        key = re.sub(r"[^a-z0-9_]", "", item.casefold().replace("-", "_").replace(" ", "_"))
        compact = key.replace("_", "")
        tool = key if key in AVAILABLE_SKILL_TOOLS else TOOL_ALIASES.get(compact)
        if tool and tool not in normalized:
            normalized.append(tool)
    if not any(tool in normalized for tool in {"list_files", "read_file", "search_text"}):
        normalized = ["list_files", "read_file", "search_text", *normalized]
    if "finish" not in normalized:
        normalized.append("finish")
    return normalized


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return _split_values(value)
    return []


def _split_values(value: str) -> list[str]:
    return [item.strip().strip("'\"") for item in re.split(r"[,，\n]", value) if item.strip().strip("'\"")]


def _first_plain_paragraph(body: str) -> str:
    for block in re.split(r"\n\s*\n", body):
        cleaned = " ".join(line.strip() for line in block.splitlines() if line.strip() and not line.lstrip().startswith("#"))
        if cleaned and not cleaned.startswith("```"):
            return cleaned[:500]
    return ""


def _decode_text(payload: bytes) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SkillImportError("Skill 文本文件必须使用 UTF-8 编码") from exc
