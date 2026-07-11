#!/usr/bin/env python3
"""Bounded, streaming inventory and risk scans for Neat governance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import unquote


DEFAULT_MAX_FILES = 20_000
DEFAULT_MAX_ENTRIES = 500
DEFAULT_MAX_FINDINGS = 200
DEFAULT_MAX_HEADINGS = 20
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
LOCATOR_LIMIT = 1024
SKIP_DIRECTORIES = {".git", "node_modules", ".venv", "venv", "__pycache__"}
NEAT_INTERNAL_DIRECTORIES = {"plans", "stage", "recovery"}
GENERATED_DIRECTORIES = {"dist", "build", "coverage", "vendor", "generated", ".next"}
SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]+")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
RELATIVE_TIME = re.compile(
    r"(?i)\b(today|yesterday|tomorrow|recently|last week|next week|currently|soon)\b|"
    r"今天|昨天|明天|最近|上周|下周|当前|稍后"
)
TODO_MARKER = re.compile(r"(?i)\b(TODO|FIXME|pending|deferred|unscheduled)\b|待办|未决|暂缓|搁置|待评估")
ISO_DATE = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])-([0-2]\d|3[01])\b")
PROMPT_INJECTION = re.compile(
    r"(?i)(ignore (?:all |any )?(?:previous|prior|system) instructions|"
    r"disregard (?:the )?(?:previous|system) prompt|"
    r"reveal (?:the )?(?:system prompt|secret|credential)|"
    r"忽略(?:之前|以上|系统)指令|泄露(?:系统提示|密钥|凭据))"
)
SECRET_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    ("provider-token", re.compile(r"\b(?:sk-|gh[pousr]_|xox[baprs]-|whsec_)[A-Za-z0-9_-]{12,}\b")),
    ("connection-string", re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s<]+")),
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([a-z][a-z0-9_.-]*(?:key|token|secret|password|passwd|credential|cookie|authorization)[a-z0-9_.-]*)\s*[:=]\s*\S+"
)


class ScanError(RuntimeError):
    pass


def json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def safe_heading(value: str) -> str:
    if SECRET_ASSIGNMENT.search(value) or any(pattern.search(value) for _, pattern in SECRET_PATTERNS):
        return "<redacted-heading>"
    if PROMPT_INJECTION.search(value):
        return "<untrusted-heading>"
    return value[:160]


def emit(payload: dict, exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    raise SystemExit(exit_code)


def resolve_root(value: str) -> Path:
    root = Path(value).resolve()
    if not root.is_dir():
        raise ScanError(f"root is not a directory: {value}")
    return root


def sha256_file(path: Path) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing"
    if not stat.S_ISREG(info.st_mode):
        raise ScanError(f"not a regular file: {path}")
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = path.lstat()
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or current.st_dev != after.st_dev
        or current.st_ino != after.st_ino
    ):
        raise ScanError(f"file changed while hashing: {path}")
    return digest.hexdigest()


def safe_relative(root: Path, raw: str) -> tuple[Path, str]:
    if Path(raw).is_absolute() or not SAFE_PATH.fullmatch(raw) or raw.startswith("-") or "//" in raw:
        raise ScanError(f"unsafe relative path: {raw}")
    normalized = PurePosixPath(raw).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if ".." in PurePosixPath(normalized).parts or "\\" in raw:
        raise ScanError(f"path escapes root: {raw}")
    path = Path(os.path.abspath(root / normalized))
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ScanError(f"path escapes root: {raw}") from exc
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise ScanError(f"symlink path is not allowed: {normalized}")
        except FileNotFoundError:
            break
    return path, normalized


def streaming_metadata(
    path: Path, relative: str, max_headings: int, default_read_paths: set[str]
) -> dict:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ScanError(f"file disappeared during inventory: {relative}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ScanError(f"not a regular file: {relative}")
    digest = hashlib.sha256()
    lines = 0
    headings: list[dict] = []
    decode_errors = False
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for raw_line in handle:
                digest.update(raw_line)
                lines += 1
                if len(headings) >= max_headings:
                    continue
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    decode_errors = True
                    continue
                match = HEADING.match(line.rstrip("\r\n"))
                if match:
                    headings.append(
                        {"line": lines, "level": len(match.group(1)), "text": safe_heading(match.group(2))}
                    )
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = path.lstat()
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or current.st_dev != after.st_dev
        or current.st_ino != after.st_ino
    ):
        raise ScanError(f"file changed during inventory: {relative}")
    parts = PurePosixPath(relative).parts
    generated = any(part in GENERATED_DIRECTORIES for part in parts)
    default_read = relative in default_read_paths
    role = "generated" if generated else "default-read" if default_read else "on-demand"
    return {
        "path": relative,
        "bytes": after.st_size,
        "lines": lines,
        "sha256": digest.hexdigest(),
        "role": role,
        "headings": headings,
        "headings_truncated": len(headings) >= max_headings,
        "utf8_warning": decode_errors,
    }


def iter_governed_files(root: Path, max_files: int):
    seen = 0
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        names[:] = sorted(name for name in names if name not in SKIP_DIRECTORIES)
        if directory_path.relative_to(root).as_posix() == ".neat":
            names[:] = [name for name in names if name not in NEAT_INTERNAL_DIRECTORIES]
        for name in sorted(files):
            path = directory_path / name
            relative = path.relative_to(root).as_posix()
            if relative == ".neat/HANDOFF.last-good.md":
                continue
            if path.suffix.lower() not in {".md", ".mdx"}:
                continue
            seen += 1
            if seen > max_files:
                raise ScanError(f"governed file count exceeds {max_files}")
            yield path, relative


def applicable_default_read_paths(root: Path, scope: str, extras: list[str]) -> set[str]:
    _, normalized_scope = safe_relative(root, scope)
    scope_parts = () if normalized_scope in {"", "."} else PurePosixPath(normalized_scope).parts
    result = {"AGENTS.md", "CLAUDE.md", "docs/HANDOFF.md"}
    current = PurePosixPath()
    for part in scope_parts:
        current /= part
        result.add((current / "AGENTS.md").as_posix())
        result.add((current / "CLAUDE.md").as_posix())
    for raw in extras:
        _, relative = safe_relative(root, raw)
        result.add(relative)
    return result


def output_limit(value: int) -> int:
    if value < 4096:
        raise ScanError("max output bytes must be at least 4096")
    return min(value, DEFAULT_MAX_OUTPUT_BYTES)


def enforce_output_budget(payload: dict, maximum: int, keys: list[tuple[str, str]]) -> dict:
    for key, truncated_key in keys:
        values = payload.get(key)
        while isinstance(values, list) and values and json_size(payload) > maximum:
            values.pop()
            payload[truncated_key] = True
    if json_size(payload) > maximum:
        raise ScanError(f"bounded scanner summary exceeds the {maximum}-byte output limit")
    return payload


def inventory(args) -> dict:
    root = resolve_root(args.root)
    maximum_output = output_limit(args.max_output_bytes)
    default_paths = applicable_default_read_paths(root, args.scope, args.default_read)
    entries: list[dict] = []
    total_files = 0
    total_bytes = 0
    role_counts: dict[str, int] = {}
    oversized: list[str] = []
    default_read_bytes = 0
    output_bytes = 2048
    entries_truncated = False
    oversized_truncated = False
    for path, relative in iter_governed_files(root, args.max_files):
        item = streaming_metadata(path, relative, args.max_headings, default_paths)
        total_files += 1
        total_bytes += item["bytes"]
        role_counts[item["role"]] = role_counts.get(item["role"], 0) + 1
        if item["role"] == "default-read":
            default_read_bytes += item["bytes"]
        limit = args.default_read_limit if item["role"] == "default-read" else args.topic_limit
        if item["role"] != "generated" and item["bytes"] > limit:
            if len(oversized) < args.max_entries and output_bytes + len(relative.encode("utf-8")) + 16 <= maximum_output:
                oversized.append(relative)
                output_bytes += len(relative.encode("utf-8")) + 16
            else:
                oversized_truncated = True
        item_bytes = json_size(item) + 2
        if len(entries) < args.max_entries and output_bytes + item_bytes <= maximum_output:
            entries.append(item)
            output_bytes += item_bytes
        else:
            entries_truncated = True
    default_read_over = default_read_bytes > args.default_read_total_limit
    payload = {
        "ok": not oversized and not oversized_truncated and not entries_truncated and not default_read_over,
        "root": str(root),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "role_counts": role_counts,
        "default_read_bytes": default_read_bytes,
        "default_read_over_aggregate": default_read_over,
        "entries": entries,
        "entries_truncated": entries_truncated,
        "oversized": oversized,
        "oversized_truncated": oversized_truncated,
        "output_budget_bytes": maximum_output,
        "limits": {
            "max_files": args.max_files,
            "max_entries": args.max_entries,
            "default_read_bytes": args.default_read_limit,
            "default_read_total_bytes": args.default_read_total_limit,
            "topic_bytes": args.topic_limit,
        },
    }
    return enforce_output_budget(
        payload,
        maximum_output,
        [("entries", "entries_truncated"), ("oversized", "oversized_truncated")],
    )


def selected_paths(root: Path, raw_paths: list[str], max_files: int) -> list[tuple[Path, str]]:
    if raw_paths:
        result = []
        for raw in raw_paths:
            path, relative = safe_relative(root, raw)
            if not path.exists():
                raise ScanError(f"file does not exist: {relative}")
            if not path.is_file():
                raise ScanError(f"not a regular file: {relative}")
            result.append((path, relative))
        return result
    return list(iter_governed_files(root, max_files))


def redact_secrets(line: str) -> str:
    sanitized = SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", line)
    for _, pattern in SECRET_PATTERNS:
        sanitized = pattern.sub("<redacted-secret>", sanitized)
    return sanitized


def redact_line(line: str, finding_type: str, key: str | None = None) -> str:
    if finding_type.startswith("secret"):
        return f"{key or 'secret'}=<redacted>"
    return redact_secrets(line).strip()[:240]


def findings(args) -> dict:
    root = resolve_root(args.root)
    maximum_output = output_limit(args.max_output_bytes)
    results: list[dict] = []
    scanned_files = 0
    scanned_bytes = 0
    truncated = False
    today = date.today()
    for path, relative in selected_paths(root, args.paths, args.max_files):
        scanned_files += 1
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ScanError(f"not a regular file: {relative}")
        scanned_bytes += info.st_size
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                for line_number, raw_line in enumerate(handle, 1):
                    try:
                        line = raw_line.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    candidates: list[tuple[str, str | None]] = []
                    assignment = SECRET_ASSIGNMENT.search(line)
                    if assignment:
                        candidates.append(("secret-assignment", assignment.group(1)))
                    for name, pattern in SECRET_PATTERNS:
                        if pattern.search(line):
                            candidates.append((f"secret-{name}", None))
                    if RELATIVE_TIME.search(line):
                        candidates.append(("relative-time", None))
                    date_match = ISO_DATE.search(line)
                    if TODO_MARKER.search(line) and date_match:
                        try:
                            due = date.fromisoformat(date_match.group(0))
                        except ValueError:
                            due = today
                        if due < today:
                            candidates.append(("overdue-open-item", None))
                    if PROMPT_INJECTION.search(line):
                        candidates.append(("untrusted-instruction", None))
                    for finding_type, key in candidates:
                        if len(results) >= args.max_findings:
                            truncated = True
                            break
                        finding = {
                            "path": relative,
                            "line": line_number,
                            "type": finding_type,
                            "snippet": redact_line(line, finding_type, key),
                        }
                        if 2048 + json_size(results) + json_size(finding) > maximum_output:
                            truncated = True
                            break
                        results.append(finding)
                    if truncated:
                        break
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = path.lstat()
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or current.st_dev != after.st_dev
            or current.st_ino != after.st_ino
        ):
            raise ScanError(f"file changed during findings scan: {relative}")
        if truncated:
            break
    secret_count = sum(item["type"].startswith("secret") for item in results)
    payload = {
        "ok": secret_count == 0 and not truncated,
        "root": str(root),
        "scanned_files": scanned_files,
        "scanned_bytes": scanned_bytes,
        "findings": results,
        "finding_count": len(results),
        "secret_risk_count": secret_count,
        "truncated": truncated,
        "max_findings": args.max_findings,
        "output_budget_bytes": maximum_output,
    }
    return enforce_output_budget(payload, maximum_output, [("findings", "truncated")])


def github_anchor(text: str) -> str:
    value = text.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return re.sub(r"[\s-]+", "-", value).strip("-")


def anchors_for(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for raw_line in handle:
                try:
                    line = raw_line.decode("utf-8").rstrip("\r\n")
                except UnicodeDecodeError:
                    continue
                match = HEADING.match(line)
                if not match:
                    continue
                base = github_anchor(match.group(2))
                if not base:
                    continue
                index = counts.get(base, 0)
                counts[base] = index + 1
                anchors.add(base if index == 0 else f"{base}-{index}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    current = path.lstat()
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or current.st_dev != after.st_dev
        or current.st_ino != after.st_ino
    ):
        raise ScanError(f"anchor target changed during scan: {path}")
    return anchors


def links(args) -> dict:
    root = resolve_root(args.root)
    maximum_output = output_limit(args.max_output_bytes)
    broken: list[dict] = []
    checked = 0
    truncated = False
    anchor_cache: dict[Path, set[str]] = {}
    for path, relative in selected_paths(root, args.paths, args.max_files):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                for line_number, raw_line in enumerate(handle, 1):
                    try:
                        line = raw_line.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    for match in MARKDOWN_LINK.finditer(line):
                        destination = match.group(1)
                        if re.match(r"(?i)^(?:https?://|mailto:|tel:|data:)", destination):
                            continue
                        checked += 1
                        target_text, _, fragment = destination.partition("#")
                        target_text = unquote(target_text.split("?", 1)[0])
                        fragment = unquote(fragment)
                        target_path = path if not target_text else path.parent / target_text
                        target_path = Path(os.path.abspath(target_path))
                        try:
                            target_relative = target_path.relative_to(root).as_posix()
                        except ValueError:
                            issue = "path-escapes-root"
                        else:
                            if not target_path.exists():
                                issue = "missing-target"
                            elif fragment:
                                if target_path not in anchor_cache:
                                    anchor_cache[target_path] = anchors_for(target_path)
                                issue = "missing-anchor" if fragment not in anchor_cache[target_path] else ""
                            else:
                                issue = ""
                        if issue:
                            if len(broken) >= args.max_findings:
                                truncated = True
                                break
                            finding = {
                                "path": relative,
                                "line": line_number,
                                "destination": redact_secrets(destination)[:240],
                                "issue": issue,
                            }
                            if 2048 + json_size(broken) + json_size(finding) > maximum_output:
                                truncated = True
                                break
                            broken.append(finding)
                    if truncated:
                        break
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = path.lstat()
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or current.st_dev != after.st_dev
            or current.st_ino != after.st_ino
        ):
            raise ScanError(f"link source changed during scan: {relative}")
        if truncated:
            break
    payload = {
        "ok": not broken and not truncated,
        "root": str(root),
        "checked_links": checked,
        "broken": broken,
        "broken_count": len(broken),
        "truncated": truncated,
        "output_budget_bytes": maximum_output,
    }
    return enforce_output_budget(payload, maximum_output, [("broken", "truncated")])


def validate_locator(args) -> dict:
    root = resolve_root(args.root)
    path, relative = safe_relative(root, args.path)
    if not path.exists():
        return {"ok": True, "exists": False, "path": relative, "state": "missing"}
    info = path.lstat()
    if info.st_size > LOCATOR_LIMIT:
        return {"ok": False, "exists": True, "path": relative, "bytes": info.st_size, "errors": ["locator exceeds 1024 bytes"]}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "exists": True, "path": relative, "bytes": info.st_size, "errors": [f"invalid locator JSON: {exc}"]}
    errors = []
    if set(data) != {"schema", "handoff", "scope", "handoff_sha256"}:
        errors.append("locator keys must be schema, handoff, scope, and handoff_sha256")
    if data.get("schema") != "neat/locator/0.5":
        errors.append("locator schema must be neat/locator/0.5")
    handoff = data.get("handoff", "")
    try:
        _, handoff_relative = safe_relative(root, handoff)
        if not handoff_relative.endswith(".md"):
            errors.append("locator handoff must be Markdown")
    except ScanError as exc:
        errors.append(str(exc))
    scope = data.get("scope", "")
    if not isinstance(scope, str) or Path(scope).is_absolute() or ".." in PurePosixPath(scope).parts:
        errors.append("locator scope must be repository-relative")
    if not re.fullmatch(r"[a-f0-9]{64}", str(data.get("handoff_sha256", ""))):
        errors.append("locator handoff_sha256 must be SHA-256")
    handoff_state = "invalid"
    actual_handoff_sha = "unknown"
    if not errors:
        handoff_path, _ = safe_relative(root, handoff)
        actual_handoff_sha = sha256_file(handoff_path)
        if actual_handoff_sha == "missing":
            errors.append("locator handoff is missing")
            handoff_state = "missing"
        elif actual_handoff_sha != data["handoff_sha256"]:
            errors.append("locator handoff SHA is stale")
            handoff_state = "stale"
        else:
            handoff_state = "current"
    return {
        "ok": not errors,
        "exists": True,
        "path": relative,
        "bytes": info.st_size,
        "locator": data if not errors else None,
        "handoff_state": handoff_state,
        "actual_handoff_sha256": actual_handoff_sha,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory_parser = subparsers.add_parser("inventory", help="Stream governed file metadata")
    inventory_parser.add_argument("--root", required=True)
    inventory_parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    inventory_parser.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    inventory_parser.add_argument("--max-headings", type=int, default=DEFAULT_MAX_HEADINGS)
    inventory_parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    inventory_parser.add_argument("--scope", default=".")
    inventory_parser.add_argument("--default-read", action="append", default=[])
    inventory_parser.add_argument("--default-read-limit", type=int, default=16 * 1024)
    inventory_parser.add_argument("--default-read-total-limit", type=int, default=32 * 1024)
    inventory_parser.add_argument("--topic-limit", type=int, default=48 * 1024)

    findings_parser = subparsers.add_parser("findings", help="Find redacted temporal, secret, and instruction risks")
    findings_parser.add_argument("--root", required=True)
    findings_parser.add_argument("paths", nargs="*")
    findings_parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    findings_parser.add_argument("--max-findings", type=int, default=DEFAULT_MAX_FINDINGS)
    findings_parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)

    links_parser = subparsers.add_parser("links", help="Check repository-local Markdown links")
    links_parser.add_argument("--root", required=True)
    links_parser.add_argument("paths", nargs="*")
    links_parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    links_parser.add_argument("--max-findings", type=int, default=DEFAULT_MAX_FINDINGS)
    links_parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)

    locator_parser = subparsers.add_parser("validate-locator", help="Validate a bounded project locator")
    locator_parser.add_argument("--root", required=True)
    locator_parser.add_argument("--path", default=".neat/active.json")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "inventory":
            result = inventory(args)
        elif args.command == "findings":
            result = findings(args)
        elif args.command == "links":
            result = links(args)
        elif args.command == "validate-locator":
            result = validate_locator(args)
        else:
            raise ScanError(f"unknown command: {args.command}")
        emit(result, 0 if result["ok"] else 1)
    except (OSError, ScanError) as exc:
        emit({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    main()
