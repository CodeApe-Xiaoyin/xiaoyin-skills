#!/usr/bin/env python3
"""Validate and transactionally protect Neat handoffs and governed knowledge."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

if os.name == "nt":
    import msvcrt
else:
    import fcntl


HARD_LIMIT = 16 * 1024
MANUAL_SCAN_LIMIT = 64 * 1024 * 1024
MANIFEST_LIMIT = 32 * 1024
GOVERNANCE_TOPIC_LIMIT = 48 * 1024
GOVERNANCE_INDEX_LIMIT = 32 * 1024
GOVERNANCE_OVERFLOW_LIMIT = 32 * 1024
GOVERNANCE_TOTAL_LIMIT = 128 * 1024
GOVERNANCE_RECOVERY_TOTAL_LIMIT = 64 * 1024 * 1024
GOVERNANCE_ACTION_LIMIT = 100
NON_GIT_FILE_LIMIT = 10_000
NON_GIT_BYTE_LIMIT = 256 * 1024 * 1024
BACKUP_PATH = PurePosixPath(".neat/HANDOFF.last-good.md")
LOCK_PATH = PurePosixPath(".neat/neat.lock")
REQUIRED_FRONTMATTER = {
    "schema",
    "updated",
    "handoff_status",
    "reason",
    "project",
    "scope",
    "branch",
    "captured_head",
    "captured_worktree",
    "captured_fingerprint",
}
REQUIRED_SECTIONS = (
    "Goal",
    "Acceptance",
    "Constraints",
    "Decisions",
    "State",
    "Done",
    "Pending",
    "Risks",
    "Failed",
    "Validation",
    "Files",
    "Next",
)
ALLOWED_SECTIONS = set(REQUIRED_SECTIONS) | {"In-Flight", "Open Questions"}
PLACEHOLDERS = re.compile(r"(?i)^(?:tbd|todo|placeholder|fill me|unknown content)[.!]?$|^\[.+\]$")
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\b(?:sk-|gh[pousr]_|xox[baprs]-|whsec_)[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s<]+"),
    re.compile(
        r"(?i)[?&](?:api[_-]?key|token|secret|password|credential|authorization)=[^\s&#)<]{8,}"
    ),
)
ASSIGNMENT = re.compile(
    r"(?i)(?:^|[\s,{;])([a-z][a-z0-9_.-]*(?:key|token|secret|password|passwd|credential|cookie|authorization)[a-z0-9_.-]*)\s*[:=]\s*['\"]?([^\s,'\";}]{8,})"
)
GENERIC_ASSIGNMENT = re.compile(
    r"(?:^|[\s,{;])([A-Za-z_][A-Za-z0-9_.-]*)\s*[:=]\s*['\"]?([A-Za-z0-9_+./=-]{20,})"
)
SAFE_RELATIVE_PATH = re.compile(r"[A-Za-z0-9._/-]+")
PLAN_ID = re.compile(r"[a-f0-9]{16,64}")
TEMPORAL_RELATIVE = re.compile(
    r"(?i)\b(today|yesterday|tomorrow|recently|last week|next week|soon)\b|"
    r"今天|昨天|明天|最近|上周|下周|稍后"
)
TEMPORAL_OPEN = re.compile(r"(?i)\b(TODO|FIXME|pending|deferred)\b|待办|未决|暂缓|搁置")
TEMPORAL_DATE = re.compile(r"\b20\d{2}-(?:0[1-9]|1[0-2])-(?:[0-2]\d|3[01])\b")


class GuardError(RuntimeError):
    pass


def acquire_lock(descriptor: int) -> None:
    if os.name == "nt":
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise GuardError("another Neat finalization is in progress") from exc
    else:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GuardError("another Neat finalization is in progress") from exc


def release_lock(descriptor: int) -> None:
    if os.name == "nt":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def emit(payload: dict, exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    raise SystemExit(exit_code)


def run_git(root: Path, *args: str, text: bool = False):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=text,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    ).stdout


def normalize_relative(value: str) -> str:
    normalized = PurePosixPath(value).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def excluded_path(relative: str, exclusions: set[str]) -> bool:
    normalized = normalize_relative(relative)
    if normalized in exclusions:
        return True
    managed_prefixes = tuple(
        value.rstrip("/") + "/"
        for value in exclusions
        if value.startswith((".neat/stage/", ".neat/recovery/"))
    )
    return normalized.startswith(managed_prefixes) if managed_prefixes else False


def lexical_inside(root: Path, raw: str) -> Path:
    if Path(raw).is_absolute():
        raise GuardError(f"absolute managed path is not allowed: {raw}")
    if (
        not SAFE_RELATIVE_PATH.fullmatch(raw)
        or raw.startswith("-")
        or "//" in raw
        or not raw.lower().endswith((".md", ".md.next"))
    ):
        raise GuardError(f"unsafe relative path syntax: {raw}")
    value = Path(raw)
    candidate = value if value.is_absolute() else root / value
    candidate = Path(os.path.abspath(candidate))
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise GuardError(f"path escapes project root: {raw}") from exc
    return candidate


def reject_symlink_components(root: Path, path: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise GuardError(f"symlink path is not allowed: {relative}")


def read_regular_snapshot(path: Path, limit: int = HARD_LIMIT) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise GuardError(f"file does not exist: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise GuardError(f"not a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
        after = os.fstat(descriptor)
        if len(data) > limit:
            raise GuardError(f"file exceeds the {limit}-byte hard limit: {path}")
        if before.st_dev != after.st_dev or before.st_ino != after.st_ino:
            raise GuardError(f"file identity changed while reading: {path}")
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise GuardError(f"file changed while reading: {path}")
        if len(data) != after.st_size:
            raise GuardError(f"file size changed while reading: {path}")
        current = path.lstat()
        if current.st_dev != after.st_dev or current.st_ino != after.st_ino:
            raise GuardError(f"file path changed while reading: {path}")
        return data, after
    finally:
        os.close(descriptor)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "missing"
    if not stat.S_ISREG(mode):
        raise GuardError(f"not a regular file: {path}")
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or current.st_dev != after.st_dev
            or current.st_ino != after.st_ino
        ):
            raise GuardError(f"file changed while hashing: {path}")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def metrics(path: Path) -> dict:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {"path": str(path), "exists": False, "bytes": 0, "lines": 0}
    if not stat.S_ISREG(mode):
        raise GuardError(f"not a regular file: {path}")
    size = path.lstat().st_size
    lines = 0
    last = b""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                lines += chunk.count(b"\n")
                last = chunk[-1:]
    finally:
        os.close(descriptor)
    if size and last != b"\n":
        lines += 1
    return {"path": str(path), "exists": True, "bytes": size, "lines": lines}


def parse_frontmatter(text: str) -> tuple[dict[str, str], int]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise GuardError("handoff must start with YAML frontmatter")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise GuardError("frontmatter closing delimiter is missing") from exc
    result: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise GuardError(f"invalid frontmatter line: {line}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if not key or key in result:
            raise GuardError(f"invalid or duplicate frontmatter key: {key}")
        result[key] = value
    return result, end


def parse_sections(text: str, body_start: int) -> tuple[dict[str, str], list[str]]:
    lines = text.splitlines()[body_start + 1 :]
    sections: dict[str, list[str]] = {}
    errors: list[str] = []
    current: str | None = None
    fence: str | None = None
    for line in lines:
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in {"```", "~~~"}:
            fence = None if fence == marker else marker if fence is None else fence
        if fence is None and line.startswith("## "):
            name = line[3:].strip()
            if name in sections:
                errors.append(f"duplicate section: {name}")
            sections.setdefault(name, [])
            current = name
        elif current is not None:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}, errors


def entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())


def contains_secret(text: str, frontmatter_end: int = -1) -> bool:
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        return True
    for line_number, line in enumerate(text.splitlines()):
        for match in ASSIGNMENT.finditer(line):
            value = match.group(2)
            if value != "<redacted>" and (len(value) >= 20 and entropy(value) >= 3.5):
                return True
            if value != "<redacted>" and len(value) >= 8:
                return True
        if 0 < line_number < frontmatter_end:
            continue
        for match in GENERIC_ASSIGNMENT.finditer(line):
            _, value = match.groups()
            if value != "<redacted>" and entropy(value) >= 3.7:
                return True
    return False


def validate_bytes(data: bytes, source: str = "candidate") -> dict:
    errors: list[str] = []
    if len(data) > HARD_LIMIT:
        errors.append(f"{source} exceeds the {HARD_LIMIT}-byte hard limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "bytes": len(data), "errors": [f"{source} is not UTF-8"]}

    try:
        frontmatter, body_start = parse_frontmatter(text)
    except GuardError as exc:
        frontmatter, body_start = {}, -1
        errors.append(str(exc))

    missing = sorted(REQUIRED_FRONTMATTER - set(frontmatter))
    if missing:
        errors.append(f"missing frontmatter keys: {', '.join(missing)}")
    unexpected_frontmatter = sorted(set(frontmatter) - REQUIRED_FRONTMATTER)
    if unexpected_frontmatter:
        errors.append(f"unexpected frontmatter keys: {', '.join(unexpected_frontmatter)}")
    empty = sorted(key for key in REQUIRED_FRONTMATTER if key in frontmatter and not frontmatter[key].strip())
    if empty:
        errors.append(f"empty frontmatter values: {', '.join(empty)}")
    if frontmatter.get("schema") != "neat/0.5":
        errors.append("schema must be neat/0.5")
    if frontmatter.get("handoff_status") != "complete":
        errors.append("handoff_status must be complete")
    if frontmatter.get("reason") not in {"checkpoint", "handoff", "compact"}:
        errors.append("reason must be checkpoint, handoff, or compact")
    if frontmatter.get("captured_worktree") not in {"clean", "dirty", "snapshot"}:
        errors.append("captured_worktree must be clean, dirty, or snapshot")
    if frontmatter.get("updated") and not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", frontmatter["updated"]
    ):
        errors.append("updated must be ISO 8601 with an offset")
    fingerprint = frontmatter.get("captured_fingerprint")
    if fingerprint and not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        errors.append("captured_fingerprint must be SHA-256")
    head = frontmatter.get("captured_head")
    if head and not re.fullmatch(r"(?:[a-f0-9]{40,64}|not-git)", head):
        errors.append("captured_head must be a Git object ID or not-git")
    branch = frontmatter.get("branch")
    if branch and (branch.startswith("/") or "\x00" in branch or branch in {"unavailable", "unknown"}):
        errors.append("branch must be a Git branch name, detached, or not-git")

    scope = frontmatter.get("scope", "")
    if scope:
        parsed_scope = PurePosixPath(scope)
        if (
            parsed_scope.is_absolute()
            or ".." in parsed_scope.parts
            or re.match(r"^[A-Za-z]:[\\/]", scope)
            or "\\" in scope
        ):
            errors.append("scope must be a POSIX repository-relative path")

    sections, section_errors = parse_sections(text, body_start)
    errors.extend(section_errors)
    missing_sections = sorted(set(REQUIRED_SECTIONS) - set(sections))
    if missing_sections:
        errors.append(f"missing required sections: {', '.join(missing_sections)}")
    unexpected = sorted(set(sections) - ALLOWED_SECTIONS)
    if unexpected:
        errors.append(f"unexpected sections: {', '.join(unexpected)}")
    for name in REQUIRED_SECTIONS:
        body = sections.get(name, "").strip()
        if not body:
            errors.append(f"required section is empty: {name}")
        elif PLACEHOLDERS.fullmatch(body):
            errors.append(f"required section is a placeholder: {name}")
    for name in ("Goal", "Acceptance", "State"):
        if sections.get(name, "").strip().lower() in {"none", "none.", "unknown", "unknown."}:
            errors.append(f"required section must be meaningful: {name}")

    validation = sections.get("Validation", "")
    fields = re.findall(r"(?m)^- (tests|build|typecheck|manual):\s*(passed|failed|not run|unknown)\s+—\s+\S.*$", validation)
    names = [name for name, _ in fields]
    if sorted(names) != sorted(["tests", "build", "typecheck", "manual"]):
        errors.append("Validation must contain exactly four status-and-evidence fields")
    if not re.search(r"(?m)^1\.\s+\S", sections.get("Next", "")):
        errors.append("Next must contain a numbered first action")

    files = sections.get("Files", "")
    file_lines = [line for line in files.splitlines() if line.strip()]
    if not 1 <= len(file_lines) <= 5:
        errors.append("Files must contain one to five entries")
    for line in file_lines:
        match = re.fullmatch(r"- `([^`]+)`\s+—\s+\S.*", line)
        if not match:
            errors.append(f"invalid Files entry: {line}")
            continue
        value = match.group(1)
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value or re.match(r"^[A-Za-z]:", value):
            errors.append(f"Files path must be repository-relative: {value}")

    start_count = text.count("<!-- manual:start -->")
    end_count = text.count("<!-- manual:end -->")
    if start_count != 1 or end_count != 1:
        errors.append("exactly one balanced manual block is required")
    elif text.index("<!-- manual:start -->") > text.index("<!-- manual:end -->"):
        errors.append("manual block markers are reversed")
    if contains_secret(text, body_start):
        errors.append(f"{source} contains a possible secret")

    return {
        "ok": not errors,
        "bytes": len(data),
        "lines": data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0),
        "sha256": sha256_bytes(data),
        "frontmatter": frontmatter,
        "sections": sections,
        "errors": errors,
    }


def validate_path(path: Path) -> dict:
    try:
        data, info = read_regular_snapshot(path)
    except GuardError as exc:
        return {"ok": False, "path": str(path), "bytes": 0, "errors": [str(exc)]}
    result = validate_bytes(data)
    result["path"] = str(path)
    result.pop("frontmatter", None)
    result.pop("sections", None)
    return result


def parse_status(raw: bytes) -> list[tuple[bytes, str]]:
    records = raw.split(b"\0")
    result: list[tuple[bytes, str]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4:
            raise GuardError("unexpected Git status record")
        status = record[:2]
        result.append((status, normalize_relative(os.fsdecode(record[3:]))))
        if status[:1] in {b"R", b"C"} or status[1:2] in {b"R", b"C"}:
            if index < len(records) and records[index]:
                index += 1
    return result


def hash_worktree_path(digest, root: Path, relative: str) -> None:
    path = root / relative
    try:
        info = path.lstat()
    except FileNotFoundError:
        digest.update(b"missing")
        return
    digest.update(str(stat.S_IMODE(info.st_mode)).encode())
    if stat.S_ISLNK(info.st_mode):
        digest.update(b"symlink\0" + os.fsencode(os.readlink(path)))
    elif stat.S_ISREG(info.st_mode):
        digest.update(b"file\0" + bytes.fromhex(sha256_file(path)))
    elif stat.S_ISDIR(info.st_mode):
        digest.update(b"directory")
        try:
            nested = run_git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all")
            nested_head = run_git(path, "rev-parse", "HEAD")
            digest.update(hashlib.sha256(nested_head + b"\0" + nested).digest())
            for nested_status, nested_relative in parse_status(nested):
                digest.update(b"\0nested\0" + nested_status + b"\0" + os.fsencode(nested_relative))
                hash_worktree_path(digest, path, nested_relative)
                nested_index = run_git(path, "ls-files", "--stage", "--", nested_relative)
                digest.update(hashlib.sha256(nested_index).digest())
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    else:
        digest.update(b"other")


def non_git_fingerprint(root: Path, excludes: list[str]) -> dict:
    excluded = {normalize_relative(value) for value in excludes}
    digest = hashlib.sha256(b"neat-non-git-v1\0")
    file_count = 0
    byte_count = 0
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        relative_directory = directory_path.relative_to(root)
        names[:] = sorted(name for name in names if name != ".git")
        symlink_directories = [name for name in names if (directory_path / name).is_symlink()]
        for name in sorted(symlink_directories + files):
            path = directory_path / name
            relative = (relative_directory / name).as_posix()
            if excluded_path(relative, excluded):
                continue
            try:
                info = path.lstat()
            except FileNotFoundError as exc:
                raise GuardError(f"non-Git workspace changed while fingerprinting: {relative}") from exc
            file_count += 1
            if file_count > NON_GIT_FILE_LIMIT:
                raise GuardError(f"non-Git workspace exceeds the {NON_GIT_FILE_LIMIT}-entry fingerprint limit")
            digest.update(b"\0" + os.fsencode(relative) + b"\0" + str(stat.S_IMODE(info.st_mode)).encode())
            if stat.S_ISLNK(info.st_mode):
                digest.update(b"symlink\0" + os.fsencode(os.readlink(path)))
            elif stat.S_ISREG(info.st_mode):
                byte_count += info.st_size
                if byte_count > NON_GIT_BYTE_LIMIT:
                    raise GuardError(
                        f"non-Git workspace exceeds the {NON_GIT_BYTE_LIMIT}-byte fingerprint limit"
                    )
                digest.update(b"file\0" + bytes.fromhex(sha256_file(path)))
            else:
                digest.update(b"other")
    return {
        "root": str(root),
        "git": False,
        "branch": "not-git",
        "head": "not-git",
        "worktree": "snapshot",
        "fingerprint": digest.hexdigest(),
        "status_count": file_count,
        "fingerprinted_bytes": byte_count,
    }


def git_fingerprint(root: Path, excludes: list[str]) -> dict:
    root = root.resolve()
    try:
        inside = run_git(root, "rev-parse", "--is-inside-work-tree", text=True).strip()
    except FileNotFoundError as exc:
        if (root / ".git").exists():
            raise GuardError("Git executable is unavailable for a Git project") from exc
        return non_git_fingerprint(root, excludes)
    except subprocess.CalledProcessError:
        if (root / ".git").exists():
            raise GuardError("unable to establish Git state for a Git project")
        return non_git_fingerprint(root, excludes)
    if inside != "true":
        raise GuardError("unable to establish Git worktree state")
    try:
        top = Path(run_git(root, "rev-parse", "--show-toplevel", text=True).strip()).resolve()
        if top != root:
            raise GuardError("--root must be the Git top-level directory")
        head = run_git(root, "rev-parse", "HEAD", text=True).strip()
        branch = run_git(root, "branch", "--show-current", text=True).strip() or "detached"
        raw = run_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    except subprocess.CalledProcessError as exc:
        raise GuardError("Git fingerprint failed") from exc

    excluded = {normalize_relative(value) for value in excludes}
    entries = [
        (status, path) for status, path in parse_status(raw) if not excluded_path(path, excluded)
    ]
    digest = hashlib.sha256()
    digest.update(b"neat-worktree-v2\0" + head.encode() + b"\0" + branch.encode())
    for status_value, relative in sorted(entries, key=lambda item: (item[1], item[0])):
        digest.update(b"\0" + status_value + b"\0" + os.fsencode(relative) + b"\0")
        hash_worktree_path(digest, root, relative)
        try:
            staged_meta = run_git(root, "ls-files", "--stage", "--", relative)
        except subprocess.CalledProcessError:
            staged_meta = b""
        digest.update(b"\0index\0" + hashlib.sha256(staged_meta).digest())
    return {
        "root": str(root),
        "git": True,
        "branch": branch,
        "head": head,
        "worktree": "dirty" if entries else "clean",
        "fingerprint": digest.hexdigest(),
        "status_count": len(entries),
    }


def manual_block_bytes(data: bytes, source: str) -> bytes | None:
    start = b"<!-- manual:start -->"
    end = b"<!-- manual:end -->"
    starts = data.count(start)
    ends = data.count(end)
    if starts == 0 and ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise GuardError(f"{source} has ambiguous manual markers")
    start_at = data.index(start) + len(start)
    end_at = data.index(end)
    if start_at > end_at:
        raise GuardError(f"{source} has reversed manual markers")
    return data[start_at:end_at]


def semantic_hash(text: str) -> str:
    normalized = re.sub(r"(?m)^updated:\s*.*$", "updated: <ignored>", text, count=1)
    return hashlib.sha256(normalized.encode()).hexdigest()


def governance_semantic_hash(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return sha256_bytes(data)
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return sha256_bytes(data)
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            break
        if re.match(r"^updated:\s*", lines[index]):
            ending = "\n" if lines[index].endswith("\n") else ""
            lines[index] = f"updated: <ignored>{ending}"
            break
    return sha256_bytes("".join(lines).encode("utf-8"))


def recovery_state(path: Path) -> str:
    if not path.exists():
        return "missing"
    try:
        if path.lstat().st_size > HARD_LIMIT:
            return "invalid"
        data, _ = read_regular_snapshot(path)
        return "valid" if validate_bytes(data, "backup")["ok"] else "invalid"
    except GuardError:
        return "invalid"


def write_atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(descriptor)
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        os.close(descriptor)


def open_directory_beneath(root: Path, directory: Path) -> int | None:
    """Anchor a project directory without following components on POSIX."""
    if os.name == "nt":
        return None
    relative = directory.relative_to(root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    try:
        for part in relative.parts:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def ensure_directory_beneath(root: Path, directory: Path) -> int | None:
    """Create and anchor a directory tree without following symlinks on POSIX."""
    if os.name == "nt":
        directory.mkdir(parents=True, exist_ok=True)
        reject_symlink_components(root, directory)
        return None
    relative = directory.relative_to(root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(root, flags)
    try:
        for part in relative.parts:
            try:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                os.mkdir(part, 0o700, dir_fd=descriptor)
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def stat_at(directory_fd: int | None, directory: Path, name: str) -> os.stat_result | None:
    try:
        if directory_fd is None:
            return (directory / name).lstat()
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def read_snapshot_at(
    directory_fd: int | None, directory: Path, name: str, limit: int
) -> tuple[bytes, os.stat_result]:
    if directory_fd is None:
        return read_regular_snapshot(directory / name, limit)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError as exc:
        raise GuardError(f"file does not exist: {directory / name}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise GuardError(f"not a regular file: {directory / name}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
        after = os.fstat(descriptor)
        current = stat_at(directory_fd, directory, name)
        if len(data) > limit:
            raise GuardError(f"file exceeds the {limit}-byte hard limit: {directory / name}")
        if (
            current is None
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or current.st_dev != after.st_dev
            or current.st_ino != after.st_ino
            or len(data) != after.st_size
        ):
            raise GuardError(f"file changed while reading: {directory / name}")
        return data, after
    finally:
        os.close(descriptor)


def sha256_at(directory_fd: int | None, directory: Path, name: str) -> str:
    info = stat_at(directory_fd, directory, name)
    if info is None:
        return "missing"
    if directory_fd is None:
        return sha256_file(directory / name)
    if not stat.S_ISREG(info.st_mode):
        raise GuardError(f"not a regular file: {directory / name}")
    descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
        current = stat_at(directory_fd, directory, name)
        if (
            current is None
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or current.st_dev != after.st_dev
            or current.st_ino != after.st_ino
        ):
            raise GuardError(f"file changed while hashing: {directory / name}")
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def write_atomic_at(
    directory_fd: int | None,
    directory: Path,
    name: str,
    data: bytes,
    mode: int = 0o600,
) -> None:
    if directory_fd is None:
        write_atomic(directory / name, data, mode)
        return
    temporary = f".{name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=directory_fd)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(descriptor)
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(descriptor)


def cleanup_candidate_at(
    directory_fd: int | None,
    directory: Path,
    name: str,
    original: os.stat_result,
    expected_sha: str,
) -> None:
    current = stat_at(directory_fd, directory, name)
    if current is None or current.st_dev != original.st_dev or current.st_ino != original.st_ino:
        return
    if sha256_at(directory_fd, directory, name) == expected_sha:
        if directory_fd is None:
            (directory / name).unlink()
        else:
            os.unlink(name, dir_fd=directory_fd)


def unlink_identity_at(
    directory_fd: int | None, directory: Path, name: str, original: os.stat_result
) -> bool:
    current = stat_at(directory_fd, directory, name)
    if (
        current is None
        or current.st_dev != original.st_dev
        or current.st_ino != original.st_ino
        or current.st_size != original.st_size
        or current.st_mtime_ns != original.st_mtime_ns
    ):
        return False
    if directory_fd is None:
        (directory / name).unlink()
    else:
        os.unlink(name, dir_fd=directory_fd)
    return True


def validate_managed_candidate(
    root: Path, raw_path: str, *, cleanup_invalid: bool, chmod_private: bool
) -> dict:
    root = root.resolve()
    candidate = lexical_inside(root, raw_path)
    if not candidate.as_posix().lower().endswith(".md.next"):
        raise GuardError("managed validation requires a .md.next candidate")
    reject_symlink_components(root, candidate)
    directory_fd = open_directory_beneath(root, candidate.parent)
    try:
        info = stat_at(directory_fd, candidate.parent, candidate.name)
        if info is None:
            return {"ok": False, "path": str(candidate), "bytes": 0, "errors": ["candidate does not exist"]}
        if not stat.S_ISREG(info.st_mode):
            raise GuardError(f"not a regular candidate: {candidate}")
        if chmod_private:
            if directory_fd is None:
                os.chmod(candidate, 0o600, follow_symlinks=False)
            else:
                os.chmod(candidate.name, 0o600, dir_fd=directory_fd, follow_symlinks=False)
        if info.st_size > HARD_LIMIT:
            cleaned = cleanup_invalid and unlink_identity_at(
                directory_fd, candidate.parent, candidate.name, info
            )
            return {
                "ok": False,
                "path": str(candidate),
                "bytes": info.st_size,
                "errors": [f"candidate exceeds the {HARD_LIMIT}-byte hard limit"],
                "cleaned_invalid": cleaned,
            }
        data, snapshot = read_snapshot_at(directory_fd, candidate.parent, candidate.name, HARD_LIMIT)
        result = validate_bytes(data)
        result["path"] = str(candidate)
        if result["ok"]:
            result["mode"] = "0600" if chmod_private else f"{stat.S_IMODE(snapshot.st_mode):04o}"
        elif cleanup_invalid:
            cleanup_candidate_at(
                directory_fd, candidate.parent, candidate.name, snapshot, result["sha256"]
            )
            result["cleaned_invalid"] = stat_at(
                directory_fd, candidate.parent, candidate.name
            ) is None
        result.pop("frontmatter", None)
        result.pop("sections", None)
        return result
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def assert_distinct(paths: list[Path]) -> None:
    if len(set(paths)) != len(paths):
        raise GuardError("candidate, target, and backup paths must be distinct")
    identities: dict[tuple[int, int], Path] = {}
    for path in paths:
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        identity = (info.st_dev, info.st_ino)
        if identity in identities:
            raise GuardError(f"hardlink/path alias is not allowed: {path}")
        identities[identity] = path


def governance_path(root: Path, raw: str, *, candidate: bool = False) -> Path:
    """Resolve a governance target or candidate without following path aliases."""
    if Path(raw).is_absolute() or not SAFE_RELATIVE_PATH.fullmatch(raw) or raw.startswith("-") or "//" in raw:
        raise GuardError(f"unsafe governance path syntax: {raw}")
    normalized = normalize_relative(raw)
    parsed = PurePosixPath(normalized)
    if ".." in parsed.parts or "\\" in raw or not normalized:
        raise GuardError(f"governance path escapes project root: {raw}")
    if ".git" in parsed.parts:
        raise GuardError(f"Git internals cannot be governed: {raw}")
    if candidate:
        if not normalized.lower().endswith((".md.next", ".json.next")):
            raise GuardError(f"unsupported governance candidate type: {raw}")
    elif not normalized.lower().endswith((".md", ".json")):
        raise GuardError(f"unsupported governance target type: {raw}")
    if not candidate and normalized.lower().endswith(".json") and not normalized.startswith(".neat/"):
        raise GuardError(f"only dedicated .neat JSON knowledge targets are allowed: {raw}")
    if parsed.parts and parsed.parts[0] == ".neat" and not candidate:
        allowed_json = normalized == ".neat/active.json" or re.fullmatch(
            r"\.neat/knowledge-index(?:\.[A-Za-z0-9._-]+)?\.json", normalized
        )
        if normalized != ".neat/HANDOFF.overflow.md" and not allowed_json:
            raise GuardError(f"reserved .neat target is not governable: {raw}")
    path = Path(os.path.abspath(root / normalized))
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GuardError(f"governance path escapes project root: {raw}") from exc
    reject_symlink_components(root, path)
    return path


def active_handoff_paths(root: Path) -> set[str]:
    result = {"docs/HANDOFF.md"}
    marker = re.compile(r"<!--\s*neat:handoff_path=([A-Za-z0-9._/-]+)\s*-->")
    found: list[str] = []
    instruction_files: list[Path] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        names[:] = sorted(
            name
            for name in names
            if name not in {".git", "node_modules", ".venv", "venv", "__pycache__"}
            and not (Path(directory).relative_to(root).as_posix() == ".neat" and name in {"stage", "recovery", "plans"})
        )
        for name in ("AGENTS.md", "CLAUDE.md"):
            if name in files:
                instruction_files.append(Path(directory) / name)
        if len(instruction_files) > NON_GIT_FILE_LIMIT:
            raise GuardError("instruction discovery exceeds the bounded file limit")
    for path in instruction_files:
        if path.is_symlink() or not path.is_file():
            raise GuardError(f"instruction path is not a regular file: {path.relative_to(root)}")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                for raw_line in handle:
                    try:
                        line = raw_line.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    match = marker.search(line)
                    if match:
                        found.append(normalize_relative(match.group(1)))
        finally:
            os.close(descriptor)
    locator = root / ".neat" / "active.json"
    if locator.exists() and locator.is_file() and not locator.is_symlink() and locator.lstat().st_size <= 1024:
        try:
            payload = json.loads(locator.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        handoff = payload.get("handoff")
        if isinstance(handoff, str) and SAFE_RELATIVE_PATH.fullmatch(handoff):
            found.append(normalize_relative(handoff))
    result.update(found)
    return result


def validate_governance_target_policy(root: Path, relative: str, role: str) -> None:
    name = PurePosixPath(relative).name
    if relative in active_handoff_paths(root):
        raise GuardError(f"active handoff must use the continuity guard, not governance: {relative}")
    if role == "rules" and name not in {"AGENTS.md", "CLAUDE.md"}:
        raise GuardError(f"rules role requires AGENTS.md or CLAUDE.md: {relative}")
    if name in {"AGENTS.md", "CLAUDE.md"} and role != "rules":
        raise GuardError(f"instruction files require role=rules: {relative}")
    if role == "readme" and name != "README.md":
        raise GuardError(f"readme role requires README.md: {relative}")
    if relative == ".neat/active.json" and role != "locator":
        raise GuardError(".neat/active.json requires role=locator")
    if relative.startswith(".neat/knowledge-index") and role != "index":
        raise GuardError("knowledge indexes require role=index")
    if relative == ".neat/HANDOFF.overflow.md" and role != "handoff-overflow":
        raise GuardError("handoff overflow requires role=handoff-overflow")
    if relative.endswith(".json") and role not in {"locator", "index"}:
        raise GuardError(f"JSON governance targets require a dedicated role: {relative}")


def governed_limit(relative: str, declared: int) -> int:
    if declared <= 0:
        raise GuardError(f"invalid candidate budget for {relative}")
    name = PurePosixPath(relative).name
    if name in {"AGENTS.md", "CLAUDE.md"}:
        maximum = HARD_LIMIT
    elif relative == ".neat/active.json":
        maximum = 1024
    elif relative == ".neat/HANDOFF.overflow.md":
        maximum = GOVERNANCE_OVERFLOW_LIMIT
    elif relative.startswith(".neat/knowledge-index") and relative.endswith(".json"):
        maximum = GOVERNANCE_INDEX_LIMIT
    else:
        maximum = GOVERNANCE_TOPIC_LIMIT
    if declared > maximum:
        raise GuardError(f"declared budget exceeds the {maximum}-byte policy for {relative}")
    return declared


def read_manifest(path: Path, limit: int = MANIFEST_LIMIT) -> tuple[dict, bytes]:
    data, _ = read_regular_snapshot(path, limit)
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardError(f"invalid governance manifest JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise GuardError("governance manifest must be an object")
    return manifest, data


def read_manifest_anchored(root: Path, path: Path, limit: int = MANIFEST_LIMIT) -> tuple[dict, bytes]:
    directory_fd = open_directory_beneath(root, path.parent)
    try:
        data, _ = read_snapshot_at(directory_fd, path.parent, path.name, limit)
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardError(f"invalid governance manifest JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise GuardError("governance manifest must be an object")
    return manifest, data


def internal_managed_path(root: Path, raw: str, prefix: PurePosixPath) -> Path:
    if Path(raw).is_absolute() or not SAFE_RELATIVE_PATH.fullmatch(raw) or raw.startswith("-") or "//" in raw:
        raise GuardError(f"unsafe managed internal path: {raw}")
    normalized = normalize_relative(raw)
    parsed = PurePosixPath(normalized)
    if ".." in parsed.parts or tuple(parsed.parts[: len(prefix.parts)]) != prefix.parts:
        raise GuardError(f"managed path must stay under {prefix}: {raw}")
    path = Path(os.path.abspath(root / normalized))
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GuardError(f"managed path escapes root: {raw}") from exc
    reject_symlink_components(root, path)
    return path


def governance_evidence_path(root: Path, raw: str) -> Path:
    if Path(raw).is_absolute() or not SAFE_RELATIVE_PATH.fullmatch(raw) or raw.startswith("-") or "//" in raw:
        raise GuardError(f"unsafe evidence path: {raw}")
    normalized = normalize_relative(raw)
    parsed = PurePosixPath(normalized)
    if ".." in parsed.parts or ".git" in parsed.parts or "\\" in raw:
        raise GuardError(f"evidence path escapes project scope: {raw}")
    path = Path(os.path.abspath(root / normalized))
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GuardError(f"evidence path escapes root: {raw}") from exc
    reject_symlink_components(root, path)
    return path


def validate_governance_text(
    root: Path, data: bytes, relative: str, *, allow_temporal: bool
) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GuardError(f"governance candidate is not UTF-8: {relative}") from exc
    if contains_secret(text):
        raise GuardError(f"governance candidate contains a possible secret: {relative}")
    if relative.endswith(".json"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GuardError(f"invalid JSON candidate {relative}: {exc}") from exc
        if not isinstance(payload, (dict, list)):
            raise GuardError(f"JSON candidate must contain an object or array: {relative}")
        if relative == ".neat/active.json":
            if not isinstance(payload, dict) or set(payload) != {
                "schema",
                "handoff",
                "scope",
                "handoff_sha256",
            }:
                raise GuardError("locator candidate keys are invalid")
            if payload.get("schema") != "neat/locator/0.5":
                raise GuardError("locator candidate schema must be neat/locator/0.5")
            handoff = payload.get("handoff")
            if not isinstance(handoff, str):
                raise GuardError("locator handoff must be a string")
            handoff_path = governance_path(root, handoff)
            if not handoff.endswith(".md"):
                raise GuardError("locator handoff must be Markdown")
            actual = sha256_file(handoff_path)
            if actual == "missing" or payload.get("handoff_sha256") != actual:
                raise GuardError("locator candidate must bind the current handoff SHA")
            scope = payload.get("scope")
            if (
                not isinstance(scope, str)
                or Path(scope).is_absolute()
                or ".." in PurePosixPath(scope).parts
                or "\\" in scope
            ):
                raise GuardError("locator scope must be repository-relative")
        elif relative.startswith(".neat/knowledge-index"):
            if not isinstance(payload, dict) or set(payload) - {"schema", "shard", "entries"}:
                raise GuardError("knowledge index candidate keys are invalid")
            if payload.get("schema") != "neat/knowledge-index/0.5":
                raise GuardError("knowledge index schema must be neat/knowledge-index/0.5")
            entries = payload.get("entries")
            if not isinstance(entries, list) or len(entries) > 5000:
                raise GuardError("knowledge index entries must be a bounded list")
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"path", "role", "sha256"}:
                    raise GuardError("knowledge index entry keys are invalid")
                governance_evidence_path(root, str(entry.get("path", "")))
                governance_role(entry.get("role"), "knowledge index entry")
                if not re.fullmatch(r"(?:missing|[a-f0-9]{64})", str(entry.get("sha256", ""))):
                    raise GuardError("knowledge index entry SHA is invalid")
    if relative == ".neat/HANDOFF.overflow.md" and not re.search(
        r"(?m)^schema:\s*neat/handoff-overflow/0\.5\s*$", text
    ):
        raise GuardError("handoff overflow must declare schema neat/handoff-overflow/0.5")
    if not allow_temporal:
        if TEMPORAL_RELATIVE.search(text):
            raise GuardError(f"candidate contains relative time requiring review: {relative}")
        for line in text.splitlines():
            if TEMPORAL_OPEN.search(line) and TEMPORAL_DATE.search(line):
                raise GuardError(f"candidate contains a dated open item requiring classification: {relative}")


def canonical_json_sha(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(data)


def governance_role(value, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:rules|readme|architecture|api|integration|runbook|index|locator|memory|handoff-overflow|other)",
        value,
    ):
        raise GuardError(f"invalid governance role for {label}")
    return value


def approval_envelope(source: dict) -> dict:
    return {
        "schema": "neat/governance-approval/0.5",
        "plan_id": source["plan_id"],
        "root_id": source["root_id"],
        "captured_head": source["captured_head"],
        "captured_fingerprint": source["captured_fingerprint"],
        "actions": [
            {
                "action": item["action"],
                "target": item["target"],
                "expected_sha": item["expected_sha"],
                "role": item["role"],
                "max_bytes": item["max_bytes"],
                "evidence": item["evidence"],
                "candidate": item.get("candidate"),
                "candidate_sha": item.get("candidate_sha"),
                "candidate_bytes": item.get("candidate_bytes", 0),
                "preserve_manual": bool(item.get("preserve_manual", True)),
                "allow_delete_manual": bool(item.get("allow_delete_manual", False)),
                "allow_temporal": bool(item.get("allow_temporal", False)),
            }
            for item in source["actions"]
        ],
        "validation": source.get("validation", []),
    }


def continuity_plan_context(args) -> dict:
    """Build a zero-write approval envelope for one handoff installation."""
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise GuardError("project root is not a directory")
    plan_id = args.plan_id or secrets.token_hex(16)
    if not PLAN_ID.fullmatch(plan_id):
        raise GuardError("plan_id must be 16-64 lowercase hexadecimal characters")
    target = lexical_inside(root, args.target)
    candidate = Path(str(target) + ".next")
    backup = lexical_inside(root, BACKUP_PATH.as_posix())
    if target.suffix.lower() != ".md":
        raise GuardError("target must be a Markdown file")
    for path in (target, candidate, backup):
        reject_symlink_components(root, path)
    assert_distinct([candidate, target, backup])
    if not re.fullmatch(r"[a-f0-9]{64}", args.candidate_sha):
        raise GuardError("candidate_sha must be SHA-256")
    if args.candidate_bytes < 0 or args.candidate_bytes > HARD_LIMIT:
        raise GuardError(f"candidate_bytes must be within the {HARD_LIMIT}-byte hard limit")

    evidence = []
    seen = set()
    for raw in args.evidence:
        path = governance_evidence_path(root, raw)
        relative = path.relative_to(root).as_posix()
        if relative in seen:
            raise GuardError(f"duplicate continuity evidence: {relative}")
        seen.add(relative)
        evidence.append({"path": relative, "sha256": sha256_file(path)})
    if len(evidence) > 50:
        raise GuardError("continuity evidence must contain at most 50 paths")

    target_relative = target.relative_to(root).as_posix()
    candidate_relative = candidate.relative_to(root).as_posix()
    exclusions = [
        target_relative,
        candidate_relative,
        BACKUP_PATH.as_posix(),
        LOCK_PATH.as_posix(),
    ]
    captured = git_fingerprint(root, exclusions)
    approval = {
        "schema": "neat/continuity-approval/0.5",
        "plan_id": plan_id,
        "root_id": sha256_bytes(str(root).encode()),
        "target": target_relative,
        "candidate": candidate_relative,
        "backup": BACKUP_PATH.as_posix(),
        "expected_target_sha": sha256_file(target),
        "expected_candidate_sha": args.candidate_sha,
        "candidate_bytes": args.candidate_bytes,
        "captured_head": captured["head"],
        "captured_fingerprint": captured["fingerprint"],
        "evidence": evidence,
        "allow_oversized_target": bool(args.allow_oversized_target),
    }
    return {
        "ok": True,
        "schema": "neat/continuity-context/0.5",
        "plan_id": plan_id,
        "approval": approval,
        "approval_digest": canonical_json_sha(approval),
        "captured_worktree": captured["worktree"],
        "phase_a_writes": 0,
    }


def validate_continuity_approval(root: Path, args) -> dict:
    raw = args.approval_json
    if len(raw.encode("utf-8")) > MANIFEST_LIMIT:
        raise GuardError("continuity approval JSON exceeds the bounded limit")
    try:
        approval = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuardError(f"invalid continuity approval JSON: {exc}") from exc
    required = {
        "schema", "plan_id", "root_id", "target", "candidate", "backup",
        "expected_target_sha", "expected_candidate_sha", "candidate_bytes",
        "captured_head", "captured_fingerprint", "evidence", "allow_oversized_target",
    }
    if not isinstance(approval, dict) or set(approval) != required:
        raise GuardError("continuity approval keys are invalid")
    if approval["schema"] != "neat/continuity-approval/0.5":
        raise GuardError("continuity approval schema is invalid")
    if approval["plan_id"] != args.approved_plan_id or not PLAN_ID.fullmatch(str(approval["plan_id"])):
        raise GuardError("approved continuity plan ID does not match")
    digest = canonical_json_sha(approval)
    if digest != args.approved_plan_digest:
        raise GuardError("approved continuity plan digest does not match")
    if approval["root_id"] != sha256_bytes(str(root).encode()):
        raise GuardError("continuity approval belongs to a different root")
    if approval["target"] != args.target or approval["candidate"] != args.candidate:
        raise GuardError("continuity target or candidate differs from the approved plan")
    if approval["backup"] != args.backup:
        raise GuardError("continuity backup differs from the approved plan")
    if not re.fullmatch(r"(?:missing|[a-f0-9]{64})", str(approval["expected_target_sha"])):
        raise GuardError("approved target SHA is invalid")
    if not re.fullmatch(r"[a-f0-9]{64}", str(approval["expected_candidate_sha"])):
        raise GuardError("approved candidate SHA is invalid")
    if not isinstance(approval["candidate_bytes"], int) or not 0 <= approval["candidate_bytes"] <= HARD_LIMIT:
        raise GuardError("approved candidate byte count is invalid")
    if not isinstance(approval["allow_oversized_target"], bool):
        raise GuardError("approved oversized-target flag is invalid")
    if not isinstance(approval["evidence"], list) or len(approval["evidence"]) > 50:
        raise GuardError("approved continuity evidence is invalid")
    for item in approval["evidence"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise GuardError("approved continuity evidence entry is invalid")
        governance_evidence_path(root, item["path"])
        if not re.fullmatch(r"(?:missing|[a-f0-9]{64})", str(item["sha256"])):
            raise GuardError("approved continuity evidence SHA is invalid")
    return approval


def governance_plan_context(
    root: Path,
    raw_plan_id: str | None,
    raw_actions: list[str],
    raw_validation: str | None,
) -> dict:
    if not raw_actions or len(raw_actions) > GOVERNANCE_ACTION_LIMIT:
        raise GuardError(f"plan context requires 1-{GOVERNANCE_ACTION_LIMIT} actions")
    plan_id = raw_plan_id or secrets.token_hex(16)
    if not PLAN_ID.fullmatch(plan_id):
        raise GuardError("plan_id must be 16-64 lowercase hexadecimal characters")
    pending = pending_governance_plans(root)
    if pending:
        raise GuardError(
            "an existing governance transaction requires commit or recovery before planning: "
            + ", ".join(pending)
        )
    try:
        validation = json.loads(raw_validation) if raw_validation else []
    except json.JSONDecodeError as exc:
        raise GuardError(f"invalid validation JSON: {exc}") from exc
    if not isinstance(validation, list) or len(validation) > 20:
        raise GuardError("validation must be a bounded JSON list")
    for command in validation:
        if (
            not isinstance(command, list)
            or not command
            or len(command) > 20
            or any(not isinstance(part, str) or len(part) > 512 for part in command)
        ):
            raise GuardError("each validation command must be a bounded argv list")

    actions = []
    target_relatives = []
    for index, raw in enumerate(raw_actions):
        try:
            requested = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GuardError(f"invalid action JSON at index {index}: {exc}") from exc
        if not isinstance(requested, dict):
            raise GuardError(f"plan action {index} must be an object")
        allowed = {
            "action",
            "target",
            "role",
            "max_bytes",
            "evidence",
            "candidate_sha",
            "candidate_bytes",
            "preserve_manual",
            "allow_delete_manual",
            "allow_temporal",
        }
        required = {"action", "target", "role", "max_bytes", "evidence"}
        if set(requested) - allowed or required - set(requested):
            raise GuardError(f"plan action {index} keys are invalid")
        operation = requested["action"]
        if operation not in {"create", "replace", "delete"}:
            raise GuardError(f"unsupported governance action: {operation}")
        target = governance_path(root, requested["target"])
        relative = target.relative_to(root).as_posix()
        if relative in target_relatives:
            raise GuardError(f"duplicate governance target: {relative}")
        target_relatives.append(relative)
        expected_sha = sha256_file(target)
        if operation == "create" and expected_sha != "missing":
            raise GuardError(f"create target already exists: {relative}")
        if operation in {"replace", "delete"} and expected_sha == "missing":
            raise GuardError(f"{operation} target is missing: {relative}")
        role = governance_role(requested["role"], f"plan action {index}")
        validate_governance_target_policy(root, relative, role)
        maximum = requested["max_bytes"]
        if not isinstance(maximum, int):
            raise GuardError(f"max_bytes must be an integer: {relative}")
        governed_limit(relative, maximum)
        evidence_paths = requested["evidence"]
        if not isinstance(evidence_paths, list) or len(evidence_paths) > 50:
            raise GuardError(f"evidence must be a bounded path list: {relative}")
        evidence = []
        for raw_evidence in evidence_paths:
            if not isinstance(raw_evidence, str):
                raise GuardError(f"evidence path must be a string: {relative}")
            evidence_path = governance_evidence_path(root, raw_evidence)
            evidence.append(
                {
                    "path": evidence_path.relative_to(root).as_posix(),
                    "sha256": sha256_file(evidence_path),
                }
            )
        candidate_sha = requested.get("candidate_sha")
        candidate_bytes = requested.get("candidate_bytes", 0)
        candidate = None
        if operation == "delete":
            if candidate_sha is not None or candidate_bytes not in {0, None}:
                raise GuardError(f"delete cannot declare candidate content: {relative}")
            candidate_bytes = 0
        else:
            if not isinstance(candidate_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", candidate_sha):
                raise GuardError(f"candidate_sha is required for {operation}: {relative}")
            if not isinstance(candidate_bytes, int) or candidate_bytes < 0 or candidate_bytes > maximum:
                raise GuardError(f"candidate_bytes exceeds the approved budget: {relative}")
            candidate = f".neat/stage/{plan_id}/{relative}.next"
        actions.append(
            {
                "action": operation,
                "target": relative,
                "expected_sha": expected_sha,
                "target_bytes": target.lstat().st_size if target.exists() else 0,
                "role": role,
                "max_bytes": maximum,
                "evidence": evidence,
                "candidate": candidate,
                "candidate_sha": candidate_sha,
                "candidate_bytes": candidate_bytes,
                "preserve_manual": bool(requested.get("preserve_manual", True)),
                "allow_delete_manual": bool(requested.get("allow_delete_manual", False)),
                "allow_temporal": bool(requested.get("allow_temporal", False)),
            }
        )
    exclusions = [
        ".neat/neat.lock",
        f".neat/plans/{plan_id}.json",
        f".neat/stage/{plan_id}",
        f".neat/recovery/{plan_id}",
        *target_relatives,
        *[item["candidate"] for item in actions if item["candidate"]],
    ]
    captured = git_fingerprint(root, exclusions)
    result = {
        "ok": True,
        "schema": "neat/governance-context/0.5",
        "plan_id": plan_id,
        "root_id": sha256_bytes(str(root).encode()),
        "manifest": f".neat/plans/{plan_id}.json",
        "actions": actions,
        "validation": validation,
        "captured_head": captured["head"],
        "captured_fingerprint": captured["fingerprint"],
        "captured_worktree": captured["worktree"],
        "exclusions": exclusions,
    }
    result["approval_digest"] = canonical_json_sha(approval_envelope(result))
    return result


def validate_governance_manifest(root: Path, manifest_path: Path) -> dict:
    manifest, manifest_data = read_manifest_anchored(root, manifest_path)
    required = {
        "schema",
        "plan_id",
        "created",
        "root_id",
        "captured_head",
        "captured_fingerprint",
        "approval_digest",
        "actions",
        "validation",
    }
    if set(manifest) != required:
        raise GuardError("governance manifest keys are invalid")
    if manifest.get("schema") != "neat/governance-plan/0.5":
        raise GuardError("governance manifest schema must be neat/governance-plan/0.5")
    plan_id = manifest.get("plan_id")
    if not isinstance(plan_id, str) or not PLAN_ID.fullmatch(plan_id):
        raise GuardError("governance plan_id must be 16-64 lowercase hexadecimal characters")
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", str(manifest.get("created", ""))
    ):
        raise GuardError("governance manifest created must be ISO 8601 with an offset")
    expected_manifest = root / ".neat" / "plans" / f"{plan_id}.json"
    if manifest_path != expected_manifest:
        raise GuardError(f"manifest must be .neat/plans/{plan_id}.json")
    actual_root_id = sha256_bytes(str(root).encode())
    if manifest.get("root_id") != actual_root_id:
        raise GuardError("governance manifest root identity does not match")
    if contains_secret(manifest_data.decode("utf-8")):
        raise GuardError("governance manifest contains a possible secret")
    if not re.fullmatch(r"[a-f0-9]{64}", str(manifest.get("approval_digest", ""))):
        raise GuardError("governance approval_digest must be SHA-256")
    validation = manifest.get("validation")
    if not isinstance(validation, list) or len(validation) > 20:
        raise GuardError("manifest validation must be a bounded list")
    for command in validation:
        if (
            not isinstance(command, list)
            or not command
            or len(command) > 20
            or any(not isinstance(part, str) or len(part) > 512 for part in command)
        ):
            raise GuardError("each manifest validation command must be a bounded argv list")
    actions = manifest.get("actions")
    if not isinstance(actions, list) or not actions or len(actions) > GOVERNANCE_ACTION_LIMIT:
        raise GuardError(f"governance actions must contain 1-{GOVERNANCE_ACTION_LIMIT} entries")

    resolved: list[dict] = []
    targets: set[str] = set()
    total_candidate_bytes = 0
    total_recovery_bytes = 0
    evidence_hashes: dict[str, str] = {}
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise GuardError(f"governance action {index} must be an object")
        required_action = {
            "action",
            "target",
            "expected_sha",
            "role",
            "max_bytes",
            "evidence",
            "candidate",
            "candidate_sha",
            "candidate_bytes",
            "preserve_manual",
            "allow_delete_manual",
            "allow_temporal",
        }
        if set(action) != required_action:
            raise GuardError(f"governance action {index} keys are invalid")
        operation = action.get("action")
        if operation not in {"create", "replace", "delete"}:
            raise GuardError(f"unsupported governance action: {operation}")
        role = governance_role(action.get("role"), f"action {index}")
        for flag in ("preserve_manual", "allow_delete_manual", "allow_temporal"):
            if not isinstance(action.get(flag), bool):
                raise GuardError(f"{flag} must be boolean for action {index}")
        target_raw = action.get("target")
        if not isinstance(target_raw, str):
            raise GuardError(f"governance action {index} target must be a string")
        target = governance_path(root, target_raw)
        target_relative = target.relative_to(root).as_posix()
        validate_governance_target_policy(root, target_relative, role)
        if target_relative in targets:
            raise GuardError(f"duplicate governance target: {target_relative}")
        targets.add(target_relative)
        expected_sha = action.get("expected_sha")
        if not isinstance(expected_sha, str) or not re.fullmatch(r"(?:missing|[a-f0-9]{64})", expected_sha):
            raise GuardError(f"invalid expected SHA for {target_relative}")
        actual_sha = sha256_file(target)
        if operation == "create" and expected_sha != "missing":
            raise GuardError(f"create requires expected_sha=missing: {target_relative}")
        if operation in {"replace", "delete"} and expected_sha == "missing":
            raise GuardError(f"{operation} requires an existing preimage: {target_relative}")
        if actual_sha != expected_sha:
            raise GuardError(
                f"target changed after planning: {target_relative}; expected {expected_sha}, got {actual_sha}"
            )
        declared_limit = action.get("max_bytes")
        if not isinstance(declared_limit, int):
            raise GuardError(f"max_bytes must be an integer: {target_relative}")
        limit = governed_limit(target_relative, declared_limit)

        evidence = action.get("evidence")
        if not isinstance(evidence, list) or len(evidence) > 50:
            raise GuardError(f"evidence must be a bounded list: {target_relative}")
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                raise GuardError(f"invalid evidence entry for {target_relative}")
            evidence_path = governance_evidence_path(root, item["path"])
            evidence_relative = evidence_path.relative_to(root).as_posix()
            expected_evidence = item["sha256"]
            if not re.fullmatch(r"(?:missing|[a-f0-9]{64})", str(expected_evidence)):
                raise GuardError(f"invalid evidence SHA: {evidence_relative}")
            actual_evidence = sha256_file(evidence_path)
            if actual_evidence != expected_evidence:
                raise GuardError(
                    f"evidence changed after planning: {evidence_relative}; expected {expected_evidence}, got {actual_evidence}"
                )
            prior = evidence_hashes.setdefault(evidence_relative, expected_evidence)
            if prior != expected_evidence:
                raise GuardError(f"conflicting evidence hashes: {evidence_relative}")

        candidate_data = None
        candidate = None
        if operation != "delete":
            candidate_raw = action.get("candidate")
            candidate_sha = action.get("candidate_sha")
            if not isinstance(candidate_raw, str) or not re.fullmatch(r"[a-f0-9]{64}", str(candidate_sha)):
                raise GuardError(f"candidate and candidate SHA are required: {target_relative}")
            candidate = governance_path(root, candidate_raw, candidate=True)
            expected_candidate = root / ".neat" / "stage" / plan_id / Path(target_relative + ".next")
            if candidate != expected_candidate:
                raise GuardError(f"candidate path does not match plan staging path: {target_relative}")
            candidate_directory_fd = open_directory_beneath(root, candidate.parent)
            try:
                candidate_data, candidate_info = read_snapshot_at(
                    candidate_directory_fd, candidate.parent, candidate.name, limit
                )
            finally:
                if candidate_directory_fd is not None:
                    os.close(candidate_directory_fd)
            if candidate_info.st_size > limit:
                raise GuardError(f"candidate exceeds declared budget: {target_relative}")
            if sha256_bytes(candidate_data) != candidate_sha:
                raise GuardError(f"candidate changed after planning: {target_relative}")
            if action.get("candidate_bytes") != len(candidate_data):
                raise GuardError(f"candidate byte count differs from the approved plan: {target_relative}")
            validate_governance_text(
                root,
                candidate_data,
                target_relative,
                allow_temporal=bool(action.get("allow_temporal", False)),
            )
            total_candidate_bytes += len(candidate_data)
            if total_candidate_bytes > GOVERNANCE_TOTAL_LIMIT:
                raise GuardError(
                    f"governance candidates exceed the {GOVERNANCE_TOTAL_LIMIT}-byte transaction limit"
                )
        elif (
            action.get("candidate") is not None
            or action.get("candidate_sha") is not None
            or action.get("candidate_bytes") != 0
        ):
            raise GuardError(f"delete must not define a candidate: {target_relative}")

        original_data = None
        original_mode = None
        if actual_sha != "missing":
            original_data, original_info = read_regular_snapshot(target, MANUAL_SCAN_LIMIT)
            original_mode = stat.S_IMODE(original_info.st_mode)
            total_recovery_bytes += len(original_data)
            if total_recovery_bytes > GOVERNANCE_RECOVERY_TOTAL_LIMIT:
                raise GuardError(
                    f"governance preimages exceed the {GOVERNANCE_RECOVERY_TOTAL_LIMIT}-byte recovery limit"
                )
            old_manual = manual_block_bytes(original_data, f"target {target_relative}")
            if operation == "delete" and old_manual is not None and not action.get("allow_delete_manual", False):
                raise GuardError(f"deleting a manual block requires explicit approval: {target_relative}")
            if operation == "replace" and old_manual is not None:
                new_manual = manual_block_bytes(candidate_data or b"", f"candidate {target_relative}")
                if old_manual != new_manual:
                    raise GuardError(f"candidate does not preserve the manual block byte-for-byte: {target_relative}")
        resolved.append(
            {
                "operation": operation,
                "target": target,
                "target_relative": target_relative,
                "expected_sha": expected_sha,
                "candidate": candidate,
                "candidate_data": candidate_data,
                "candidate_sha": action.get("candidate_sha"),
                "original_data": original_data,
                "original_mode": original_mode,
                "applied_mode": original_mode
                if original_mode is not None
                else (0o600 if target_relative.startswith(".neat/") else 0o644),
                "no_change": operation == "replace"
                and (
                    action.get("candidate_sha") == expected_sha
                    or governance_semantic_hash(candidate_data or b"")
                    == governance_semantic_hash(original_data or b"")
                ),
            }
        )

    exclusions = [
        ".neat/neat.lock",
        f".neat/plans/{plan_id}.json",
        f".neat/stage/{plan_id}",
        f".neat/recovery/{plan_id}",
        *targets,
        *[
            item["candidate"].relative_to(root).as_posix()
            for item in resolved
            if item["candidate"] is not None
        ],
    ]
    current = git_fingerprint(root, exclusions)
    if current["head"] != manifest.get("captured_head"):
        raise GuardError(
            f"repository HEAD changed after planning: expected {manifest.get('captured_head')}, got {current['head']}"
        )
    if current["fingerprint"] != manifest.get("captured_fingerprint"):
        raise GuardError("repository scope changed after planning")
    assert_distinct(
        [item["target"] for item in resolved]
        + [item["candidate"] for item in resolved if item["candidate"] is not None]
    )
    computed_approval = canonical_json_sha(approval_envelope(manifest))
    if computed_approval != manifest["approval_digest"]:
        raise GuardError(
            f"manifest differs from the approved plan: expected {manifest['approval_digest']}, got {computed_approval}"
        )
    return {
        "ok": True,
        "plan_id": plan_id,
        "manifest": manifest,
        "manifest_sha256": sha256_bytes(manifest_data),
        "approval_digest": computed_approval,
        "actions": resolved,
        "action_count": len(resolved),
        "candidate_bytes": total_candidate_bytes,
        "recovery_bytes": total_recovery_bytes,
        "fingerprint": current,
        "exclusions": exclusions,
    }


def remove_empty_parents(path: Path, stop: Path) -> None:
    current = path
    while current != stop:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def scanner_payload(root: Path, command: str) -> dict:
    script = Path(__file__).with_name("neat_scan.py")
    process = subprocess.run(
        [sys.executable, str(script), command, "--root", str(root)],
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise GuardError(f"{command} scanner returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise GuardError(f"{command} scanner returned a non-object result")
    if process.returncode not in {0, 1} or payload.get("error"):
        raise GuardError(f"{command} scanner failed: {payload.get('error', process.stderr.strip())}")
    if command == "links" and not isinstance(payload.get("broken"), list):
        raise GuardError("link scanner did not return a complete broken-link set")
    if command == "validate-locator" and "exists" not in payload:
        raise GuardError("locator scanner did not return a complete locator state")
    if payload.get("truncated"):
        raise GuardError(f"{command} scanner could not establish a bounded result")
    return payload


def link_issue_keys(payload: dict) -> list[str]:
    broken = payload.get("broken", [])
    if not isinstance(broken, list):
        raise GuardError("link scanner result is malformed")
    return sorted(
        canonical_json_sha(
            {
                "path": item.get("path"),
                "destination": item.get("destination"),
                "issue": item.get("issue"),
            }
        )
        for item in broken
    )


def write_recovery_state(
    root: Path,
    plan_id: str,
    actions: list[dict],
    manifest_sha: str,
    approval_digest: str,
    validation: list,
    baseline_links: list[str],
    baseline_locator: dict,
    captured_head: str,
    captured_fingerprint: str,
    exclusions: list[str],
    evidence: list[dict],
) -> Path:
    recovery = root / ".neat" / "recovery" / plan_id
    if recovery.exists():
        if (recovery / "state.json").exists():
            raise GuardError(f"unresolved recovery state already exists for plan {plan_id}")
        remove_managed_tree(recovery)
    recovery_directory_fd = ensure_directory_beneath(root, recovery)
    if recovery_directory_fd is not None:
        os.close(recovery_directory_fd)
    state_actions = []
    for item in actions:
        backup_relative = None
        if item["original_data"] is not None:
            backup = recovery / "files" / item["target_relative"]
            backup_directory_fd = ensure_directory_beneath(root, backup.parent)
            try:
                write_atomic_at(
                    backup_directory_fd,
                    backup.parent,
                    backup.name,
                    item["original_data"],
                    0o600,
                )
            finally:
                if backup_directory_fd is not None:
                    os.close(backup_directory_fd)
            backup_relative = backup.relative_to(root).as_posix()
        state_actions.append(
            {
                "operation": item["operation"],
                "target": item["target_relative"],
                "original_sha": item["expected_sha"],
                "applied_sha": "missing" if item["operation"] == "delete" else item["candidate_sha"],
                "original_mode": item["original_mode"],
                "applied_mode": item["applied_mode"],
                "backup": backup_relative,
            }
        )
    state = {
        "schema": "neat/governance-recovery/0.5",
        "plan_id": plan_id,
        "manifest_sha256": manifest_sha,
        "approval_digest": approval_digest,
        "validation": validation,
        "baseline_links": baseline_links,
        "baseline_locator": baseline_locator,
        "captured_head": captured_head,
        "captured_fingerprint": captured_fingerprint,
        "exclusions": exclusions,
        "evidence": evidence,
        "status": "prepared",
        "applied": [],
        "actions": state_actions,
    }
    recovery_directory_fd = open_directory_beneath(root, recovery)
    try:
        write_atomic_at(
            recovery_directory_fd,
            recovery,
            "state.json",
            json.dumps(state, sort_keys=True).encode() + b"\n",
            0o600,
        )
    finally:
        if recovery_directory_fd is not None:
            os.close(recovery_directory_fd)
    return recovery


def load_recovery_state(root: Path, plan_id: str) -> tuple[Path, dict]:
    if not PLAN_ID.fullmatch(plan_id):
        raise GuardError("invalid recovery plan ID")
    recovery = root / ".neat" / "recovery" / plan_id
    state_path = recovery / "state.json"
    state, _ = read_manifest_anchored(root, state_path, GOVERNANCE_TOTAL_LIMIT)
    if state.get("schema") != "neat/governance-recovery/0.5" or state.get("plan_id") != plan_id:
        raise GuardError("invalid recovery state")
    return recovery, state


def update_recovery_state(recovery: Path, state: dict) -> None:
    root = recovery.parents[2]
    recovery_directory_fd = open_directory_beneath(root, recovery)
    try:
        write_atomic_at(
            recovery_directory_fd,
            recovery,
            "state.json",
            json.dumps(state, sort_keys=True).encode() + b"\n",
            0o600,
        )
    finally:
        if recovery_directory_fd is not None:
            os.close(recovery_directory_fd)


def recover_governance(root: Path, plan_id: str) -> dict:
    recovery, state = load_recovery_state(root, plan_id)
    recovered: list[str] = []
    for item in reversed(state.get("actions", [])):
        target = governance_path(root, item["target"])
        target_directory_fd = ensure_directory_beneath(root, target.parent)
        try:
            current_sha = sha256_at(target_directory_fd, target.parent, target.name)
            allowed = {item["original_sha"], item["applied_sha"]}
            if current_sha not in allowed:
                raise GuardError(
                    f"target changed outside the interrupted transaction: {item['target']}"
                )
            backup_relative = item.get("backup")
            if backup_relative:
                backup_path = internal_managed_path(
                    root, backup_relative, PurePosixPath(f".neat/recovery/{plan_id}/files")
                )
                backup_directory_fd = open_directory_beneath(root, backup_path.parent)
                try:
                    data, _ = read_snapshot_at(
                        backup_directory_fd,
                        backup_path.parent,
                        backup_path.name,
                        MANUAL_SCAN_LIMIT,
                    )
                finally:
                    if backup_directory_fd is not None:
                        os.close(backup_directory_fd)
                if sha256_bytes(data) != item["original_sha"]:
                    raise GuardError(f"recovery backup hash mismatch: {item['target']}")
                write_atomic_at(
                    target_directory_fd,
                    target.parent,
                    target.name,
                    data,
                    item.get("original_mode") or 0o600,
                )
            elif current_sha != "missing":
                if target_directory_fd is None:
                    target.unlink()
                else:
                    os.unlink(target.name, dir_fd=target_directory_fd)
        finally:
            if target_directory_fd is not None:
                os.close(target_directory_fd)
        recovered.append(item["target"])
    state["status"] = "recovered"
    update_recovery_state(recovery, state)
    return {"ok": True, "plan_id": plan_id, "status": "recovered", "targets": recovered}


def remove_managed_tree(base: Path) -> None:
    if not base.exists():
        return
    for directory, names, files in os.walk(base, topdown=False, followlinks=False):
        directory_path = Path(directory)
        for name in files:
            path = directory_path / name
            if path.is_symlink() or not path.is_file():
                raise GuardError(f"unsafe managed cleanup entry: {path}")
            path.unlink()
        for name in names:
            child = directory_path / name
            if child.is_symlink():
                raise GuardError(f"unsafe managed cleanup directory: {child}")
            child.rmdir()
    base.rmdir()


def cleanup_recovery(root: Path, plan_id: str) -> None:
    remove_managed_tree(root / ".neat" / "recovery" / plan_id)
    try:
        (root / ".neat" / "recovery").rmdir()
    except OSError:
        pass


def cleanup_governance(root: Path, plan_id: str) -> None:
    remove_managed_tree(root / ".neat" / "stage" / plan_id)
    remove_managed_tree(root / ".neat" / "recovery" / plan_id)
    manifest = root / ".neat" / "plans" / f"{plan_id}.json"
    if manifest.exists():
        if manifest.is_symlink() or not manifest.is_file():
            raise GuardError("unsafe manifest cleanup target")
        manifest.unlink()
    for directory in (
        root / ".neat" / "stage",
        root / ".neat" / "recovery",
        root / ".neat" / "plans",
    ):
        try:
            directory.rmdir()
        except OSError:
            pass


def open_governance_lock(root: Path) -> int:
    neat = root / ".neat"
    neat_directory_fd = ensure_directory_beneath(root, neat)
    try:
        if neat_directory_fd is None:
            return os.open(neat / "neat.lock", os.O_RDWR | os.O_CREAT, 0o600)
        return os.open("neat.lock", os.O_RDWR | os.O_CREAT, 0o600, dir_fd=neat_directory_fd)
    finally:
        if neat_directory_fd is not None:
            os.close(neat_directory_fd)


def pending_governance_plans(root: Path, exclude_plan: str | None = None) -> list[str]:
    recovery_root = root / ".neat" / "recovery"
    if not recovery_root.exists():
        return []
    reject_symlink_components(root, recovery_root)
    pending = []
    for child in sorted(recovery_root.iterdir(), key=lambda path: path.name):
        if child.name == exclude_plan:
            continue
        if child.is_symlink() or not child.is_dir() or not PLAN_ID.fullmatch(child.name):
            raise GuardError(f"unsafe governance recovery entry: {child.name}")
        state_path = child / "state.json"
        if not state_path.exists():
            raise GuardError(f"incomplete governance recovery state: {child.name}")
        state, _ = read_manifest_anchored(root, state_path, GOVERNANCE_TOTAL_LIMIT)
        if state.get("status") in {"prepared", "applying", "awaiting_validation"}:
            pending.append(child.name)
    return pending


def recover_governance_command(root: Path, plan_id: str) -> dict:
    descriptor = open_governance_lock(root)
    locked = False
    try:
        acquire_lock(descriptor)
        locked = True
        result = recover_governance(root, plan_id)
        cleanup_governance(root, plan_id)
        result["managed_state_cleaned"] = True
        return result
    finally:
        if locked:
            release_lock(descriptor)
        os.close(descriptor)


def revalidate_governance_commit_state(root: Path, plan_id: str, state: dict) -> set[str]:
    """Recheck every approval-bound input using anchored file access where possible."""
    manifest = root / ".neat" / "plans" / f"{plan_id}.json"
    manifest_directory_fd = open_directory_beneath(root, manifest.parent)
    try:
        if sha256_at(manifest_directory_fd, manifest.parent, manifest.name) != state.get("manifest_sha256"):
            raise GuardError("governance manifest changed before commit")
    finally:
        if manifest_directory_fd is not None:
            os.close(manifest_directory_fd)

    current_scope = git_fingerprint(root, state.get("exclusions", []))
    if current_scope["head"] != state.get("captured_head"):
        raise GuardError("repository HEAD changed before governance commit")
    if current_scope["fingerprint"] != state.get("captured_fingerprint"):
        raise GuardError("repository scope changed before governance commit")

    for evidence in state.get("evidence", []):
        evidence_path = governance_evidence_path(root, evidence["path"])
        evidence_directory_fd = open_directory_beneath(root, evidence_path.parent)
        try:
            if sha256_at(evidence_directory_fd, evidence_path.parent, evidence_path.name) != evidence["sha256"]:
                raise GuardError(f"evidence changed before commit: {evidence['path']}")
        finally:
            if evidence_directory_fd is not None:
                os.close(evidence_directory_fd)

    target_relatives = set()
    for item in state.get("actions", []):
        target = governance_path(root, item["target"])
        target_relatives.add(item["target"])
        target_directory_fd = open_directory_beneath(root, target.parent)
        try:
            if sha256_at(target_directory_fd, target.parent, target.name) != item["applied_sha"]:
                raise GuardError(f"target changed before commit: {item['target']}")
            info = stat_at(target_directory_fd, target.parent, target.name)
            if item["applied_sha"] != "missing" and os.name != "nt":
                if info is None or stat.S_IMODE(info.st_mode) != item["applied_mode"]:
                    raise GuardError(f"target mode changed before commit: {item['target']}")
        finally:
            if target_directory_fd is not None:
                os.close(target_directory_fd)
    return target_relatives


def commit_governance_command(args) -> dict:
    root = Path(args.root).resolve()
    plan_id = args.plan_id
    if not PLAN_ID.fullmatch(plan_id):
        raise GuardError("invalid commit plan ID")
    descriptor = open_governance_lock(root)
    locked = False
    try:
        acquire_lock(descriptor)
        locked = True
        _, state = load_recovery_state(root, plan_id)
        if state.get("status") != "awaiting_validation":
            raise GuardError("transaction is not awaiting validation")
        if state.get("approval_digest") != args.approved_plan_digest:
            raise GuardError("approved plan digest does not match the pending transaction")
        if not args.validation_passed:
            raise GuardError("approved external validation must pass before commit")
        target_relatives = revalidate_governance_commit_state(root, plan_id, state)

        current_links = link_issue_keys(scanner_payload(root, "links"))
        new_link_issues = sorted(set(current_links) - set(state.get("baseline_links", [])))
        if new_link_issues:
            raise GuardError(f"post-write validation found {len(new_link_issues)} new broken links")

        baseline_locator = state.get("baseline_locator", {})
        current_locator = scanner_payload(root, "validate-locator")
        baseline_handoff = (baseline_locator.get("locator") or {}).get("handoff")
        current_handoff = (current_locator.get("locator") or {}).get("handoff")
        locator_relevant = ".neat/active.json" in target_relatives or any(
            value in target_relatives for value in (baseline_handoff, current_handoff) if value
        )
        if locator_relevant and not current_locator.get("ok"):
            raise GuardError("post-write locator validation failed")

        delay = os.environ.get("NEAT_TEST_DELAY_BEFORE_FINAL_REVALIDATION")
        if delay:
            time.sleep(float(delay))

        # Scans can be slow. Treat their entire duration as an untrusted concurrency
        # window and recheck every approval-bound input immediately before cleanup.
        final_targets = revalidate_governance_commit_state(root, plan_id, state)
        if final_targets != target_relatives:
            raise GuardError("governance target set changed before commit")

        cleanup_governance(root, plan_id)
        return {
            "ok": True,
            "plan_id": plan_id,
            "approval_digest": args.approved_plan_digest,
            "status": "committed",
            "targets": sorted(target_relatives),
            "validation_attested": True,
            "link_issues_added": 0,
        }
    finally:
        if locked:
            release_lock(descriptor)
        os.close(descriptor)


def apply_governance_manifest(args) -> dict:
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise GuardError("project root is not a directory")
    manifest_path = internal_managed_path(root, args.manifest, PurePosixPath(".neat/plans"))
    validated = validate_governance_manifest(root, manifest_path)
    plan_id = validated["plan_id"]
    if args.approved_plan_id != plan_id:
        raise GuardError("approved plan ID does not match the manifest")
    if args.approved_plan_digest != validated["approval_digest"]:
        raise GuardError("approved plan digest does not match the manifest")
    descriptor = open_governance_lock(root)
    locked = False
    recovery = None
    target_handles: list[int] = []
    try:
        acquire_lock(descriptor)
        locked = True
        pending = pending_governance_plans(root, exclude_plan=plan_id)
        if pending:
            raise GuardError(
                "another governance transaction requires commit or recovery: " + ", ".join(pending)
            )
        validated = validate_governance_manifest(root, manifest_path)
        active_actions = [item for item in validated["actions"] if not item["no_change"]]
        if not active_actions:
            cleanup_governance(root, plan_id)
            return {
                "ok": True,
                "plan_id": plan_id,
                "status": "checked_no_change",
                "targets": [],
                "action_count": 0,
                "candidate_bytes": validated["candidate_bytes"],
            }
        for item in active_actions:
            directory_fd = ensure_directory_beneath(root, item["target"].parent)
            item["directory_fd"] = directory_fd
            if directory_fd is not None:
                target_handles.append(directory_fd)
            if sha256_at(directory_fd, item["target"].parent, item["target"].name) != item["expected_sha"]:
                raise GuardError(f"target changed while anchoring: {item['target_relative']}")
        baseline_links = link_issue_keys(scanner_payload(root, "links"))
        baseline_locator = scanner_payload(root, "validate-locator")
        evidence_map: dict[str, str] = {}
        for action in validated["manifest"]["actions"]:
            for item in action["evidence"]:
                evidence_map[item["path"]] = item["sha256"]
        recovery = write_recovery_state(
            root,
            plan_id,
            active_actions,
            validated["manifest_sha256"],
            validated["approval_digest"],
            validated["manifest"]["validation"],
            baseline_links,
            baseline_locator,
            validated["fingerprint"]["head"],
            validated["fingerprint"]["fingerprint"],
            validated["exclusions"],
            [
                {"path": path, "sha256": value}
                for path, value in sorted(evidence_map.items())
            ],
        )
        _, state = load_recovery_state(root, plan_id)
        changed: list[str] = []
        for index, item in enumerate(active_actions):
            if sha256_at(
                item["directory_fd"], item["target"].parent, item["target"].name
            ) != item["expected_sha"]:
                raise GuardError(f"target changed during transaction: {item['target_relative']}")
            if item["operation"] == "delete":
                if item["directory_fd"] is None:
                    item["target"].unlink()
                else:
                    os.unlink(item["target"].name, dir_fd=item["directory_fd"])
            else:
                write_atomic_at(
                    item["directory_fd"],
                    item["target"].parent,
                    item["target"].name,
                    item["candidate_data"],
                    item["applied_mode"],
                )
            changed.append(item["target_relative"])
            state["applied"] = changed.copy()
            state["status"] = "applying"
            update_recovery_state(recovery, state)
            fail_after = os.environ.get("NEAT_TEST_FAIL_AFTER")
            if fail_after and int(fail_after) == index + 1:
                raise GuardError("injected governance transaction failure")
        for item in active_actions:
            expected = "missing" if item["operation"] == "delete" else item["candidate_sha"]
            if sha256_at(
                item["directory_fd"], item["target"].parent, item["target"].name
            ) != expected:
                raise GuardError(f"post-write hash mismatch: {item['target_relative']}")
        state["status"] = "awaiting_validation"
        update_recovery_state(recovery, state)
        result = {
            "ok": True,
            "plan_id": plan_id,
            "approval_digest": validated["approval_digest"],
            "status": "awaiting_validation",
            "targets": changed,
            "action_count": len(changed),
            "candidate_bytes": validated["candidate_bytes"],
            "validation": validated["manifest"]["validation"],
            "next": "run approved validation, then commit-transaction; recover-transaction on failure",
        }
        return result
    except Exception as exc:
        if recovery is not None and recovery.exists():
            try:
                recover_governance(root, plan_id)
                cleanup_governance(root, plan_id)
            except Exception as recovery_exc:
                raise GuardError(
                    f"{exc}; automatic recovery failed: {recovery_exc}"
                ) from recovery_exc
            raise GuardError(f"{exc}; transaction_recovered=true") from exc
        raise
    finally:
        for target_handle in target_handles:
            os.close(target_handle)
        if locked:
            release_lock(descriptor)
        os.close(descriptor)


def cleanup_candidate(path: Path, original: os.stat_result, expected_sha: str) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if current.st_dev != original.st_dev or current.st_ino != original.st_ino:
        return
    if sha256_file(path) == expected_sha:
        path.unlink()


def finalize(args) -> dict:
    root = Path(args.root).resolve()
    if not root.is_dir():
        raise GuardError("project root is not a directory")
    approval = validate_continuity_approval(root, args)
    target = lexical_inside(root, args.target)
    candidate = lexical_inside(root, args.candidate)
    backup = lexical_inside(root, args.backup)
    expected_candidate = Path(str(target) + ".next")
    if target.suffix.lower() != ".md":
        raise GuardError("target must be a Markdown file")
    if candidate != expected_candidate:
        raise GuardError("candidate must be exactly <target>.next")
    if backup.relative_to(root).as_posix() != BACKUP_PATH.as_posix():
        raise GuardError(f"backup must be {BACKUP_PATH}")
    for path in (candidate, target, backup):
        reject_symlink_components(root, path)
    assert_distinct([candidate, target, backup])

    lock = root / LOCK_PATH
    lock.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink_components(root, lock)
    target_directory_fd = open_directory_beneath(root, target.parent)
    backup_directory_fd = open_directory_beneath(root, backup.parent)
    if backup_directory_fd is None:
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    else:
        descriptor = os.open(lock.name, os.O_RDWR | os.O_CREAT, 0o600, dir_fd=backup_directory_fd)
    locked = False
    try:
        acquire_lock(descriptor)
        locked = True

        candidate_preflight = stat_at(target_directory_fd, target.parent, candidate.name)
        if candidate_preflight is None:
            raise GuardError("candidate does not exist")
        if candidate_preflight.st_size > HARD_LIMIT:
            cleaned = unlink_identity_at(
                target_directory_fd, target.parent, candidate.name, candidate_preflight
            )
            raise GuardError(
                f"candidate exceeds the {HARD_LIMIT}-byte hard limit; cleaned_invalid={str(cleaned).lower()}"
            )
        candidate_data, candidate_info = read_snapshot_at(
            target_directory_fd, target.parent, candidate.name, HARD_LIMIT
        )
        candidate_validation = validate_bytes(candidate_data)
        if not candidate_validation["ok"]:
            cleanup_candidate_at(
                target_directory_fd,
                target.parent,
                candidate.name,
                candidate_info,
                candidate_validation["sha256"],
            )
            raise GuardError("candidate validation failed: " + "; ".join(candidate_validation["errors"]))
        if os.name != "nt" and stat.S_IMODE(candidate_info.st_mode) != 0o600:
            raise GuardError("candidate mode must be 0600; validate with --chmod-private")
        if candidate_validation["sha256"] != approval["expected_candidate_sha"]:
            raise GuardError("candidate changed after validation")
        if len(candidate_data) != approval["candidate_bytes"]:
            raise GuardError("candidate byte count differs from the approved plan")

        actual_target_sha = sha256_at(target_directory_fd, target.parent, target.name)
        if actual_target_sha != approval["expected_target_sha"]:
            raise GuardError(
                f"target changed after planning: expected {approval['expected_target_sha']}, got {actual_target_sha}"
            )

        excluded = [
            target.relative_to(root).as_posix(),
            candidate.relative_to(root).as_posix(),
            backup.relative_to(root).as_posix(),
            LOCK_PATH.as_posix(),
        ]
        current = git_fingerprint(root, excluded)
        if current["head"] != approval["captured_head"]:
            raise GuardError("repository HEAD changed after continuity planning")
        if current["fingerprint"] != approval["captured_fingerprint"]:
            raise GuardError(
                "worktree changed after continuity planning: "
                f"expected {approval['captured_fingerprint']}, got {current['fingerprint']}"
            )
        for evidence in approval["evidence"]:
            evidence_path = governance_evidence_path(root, evidence["path"])
            evidence_directory_fd = open_directory_beneath(root, evidence_path.parent)
            try:
                if sha256_at(evidence_directory_fd, evidence_path.parent, evidence_path.name) != evidence["sha256"]:
                    raise GuardError(f"continuity evidence changed after planning: {evidence['path']}")
            finally:
                if evidence_directory_fd is not None:
                    os.close(evidence_directory_fd)
        captured = candidate_validation["frontmatter"]
        bindings = {
            "captured_fingerprint": current["fingerprint"],
            "captured_head": current["head"],
            "captured_worktree": current["worktree"],
            "branch": current["branch"],
        }
        mismatches = [key for key, value in bindings.items() if captured.get(key) != value]
        if mismatches:
            raise GuardError("candidate Git metadata does not match finalization: " + ", ".join(mismatches))
        if approval["allow_oversized_target"] and captured.get("reason") != "compact":
            raise GuardError("--allow-oversized-target requires reason: compact")

        target_data: bytes | None = None
        target_validation: dict | None = None
        target_info = stat_at(target_directory_fd, target.parent, target.name)
        if target_info is not None:
            try:
                target_data, _ = read_snapshot_at(
                    target_directory_fd, target.parent, target.name, MANUAL_SCAN_LIMIT
                )
                if len(target_data) <= HARD_LIMIT:
                    target_validation = validate_bytes(target_data, "current target")
            except GuardError:
                target_data = None
                target_validation = None
        if target_info is not None and target_data is None:
            raise GuardError(
                f"current target cannot be inspected within the {MANUAL_SCAN_LIMIT}-byte recovery limit"
            )
        if target_data is not None:
            old_manual = manual_block_bytes(target_data, "current target")
            new_manual = manual_block_bytes(candidate_data, "candidate")
            if old_manual is not None and old_manual != new_manual:
                raise GuardError("candidate does not preserve the manual block byte-for-byte")
        if target_validation and target_validation["ok"]:
            old_text = target_data.decode("utf-8")
            new_text = candidate_data.decode("utf-8")
            if semantic_hash(old_text) == semantic_hash(new_text):
                cleanup_candidate_at(
                    target_directory_fd,
                    target.parent,
                    candidate.name,
                    candidate_info,
                    candidate_validation["sha256"],
                )
                return {
                    "ok": True,
                    "checked_no_change": True,
                    "target": str(target),
                    "target_sha256": actual_target_sha,
                    "backup": str(backup) if backup.exists() else None,
                    "backup_state": recovery_state(backup),
                    "bytes": len(target_data),
                }

        if target_info is not None and target_info.st_size > HARD_LIMIT and not approval["allow_oversized_target"]:
            raise GuardError("current target is oversized; an approved compact run must allow replacement")

        if sha256_at(target_directory_fd, target.parent, target.name) != actual_target_sha:
            raise GuardError("target changed before backup")
        current_before_backup = git_fingerprint(root, excluded)
        if current_before_backup["fingerprint"] != current["fingerprint"]:
            raise GuardError("worktree changed before backup")

        backup_result = "unchanged"
        if target_validation and target_validation["ok"] and target_data is not None:
            write_atomic_at(backup_directory_fd, backup.parent, backup.name, target_data)
            backup_result = "updated_from_valid_target"
        elif target_info is not None:
            backup_result = "skipped_invalid_or_oversized_target"

        if sha256_at(target_directory_fd, target.parent, target.name) != actual_target_sha:
            raise GuardError("target changed during finalization")
        current_again = git_fingerprint(root, excluded)
        if current_again["fingerprint"] != current["fingerprint"]:
            raise GuardError("worktree changed during finalization")

        write_atomic_at(target_directory_fd, target.parent, target.name, candidate_data)
        cleanup_candidate_at(
            target_directory_fd,
            target.parent,
            candidate.name,
            candidate_info,
            candidate_validation["sha256"],
        )
        return {
            "ok": True,
            "checked_no_change": False,
            "target": str(target),
            "target_sha256": sha256_bytes(candidate_data),
            "backup": str(backup) if backup.exists() else None,
            "backup_state": recovery_state(backup),
            "backup_result": backup_result,
            "bytes": len(candidate_data),
        }
    finally:
        try:
            if locked:
                release_lock(descriptor)
        finally:
            os.close(descriptor)
            if target_directory_fd is not None:
                os.close(target_directory_fd)
            if backup_directory_fd is not None:
                os.close(backup_directory_fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    measure = subparsers.add_parser("measure", help="Measure files without loading them into model context")
    measure.add_argument("paths", nargs="+")
    measure.add_argument("--max-total-bytes", type=int)
    measure.add_argument("--max-each-bytes", type=int)

    sha = subparsers.add_parser("sha", help="Return a regular file SHA-256 or missing")
    sha.add_argument("path")

    validate = subparsers.add_parser("validate", help="Validate a Neat handoff")
    validate.add_argument("path")
    validate.add_argument("--root")
    validate.add_argument("--cleanup-invalid", action="store_true")
    validate.add_argument("--chmod-private", action="store_true")

    fingerprint = subparsers.add_parser("fingerprint", help="Fingerprint Git state and dirty content")
    fingerprint.add_argument("--root", required=True)
    fingerprint.add_argument("--exclude", action="append", default=[])

    plan_context_parser = subparsers.add_parser(
        "plan-context", help="Capture a read-only governance plan binding"
    )
    plan_context_parser.add_argument("--root", required=True)
    plan_context_parser.add_argument("--plan-id")
    plan_context_parser.add_argument("--action-json", action="append", required=True)
    plan_context_parser.add_argument("--validation-json")

    plan_handoff_parser = subparsers.add_parser(
        "plan-handoff", help="Capture a zero-write approval binding for one handoff"
    )
    plan_handoff_parser.add_argument("--root", required=True)
    plan_handoff_parser.add_argument("--plan-id")
    plan_handoff_parser.add_argument("--target", required=True)
    plan_handoff_parser.add_argument("--candidate-sha", required=True)
    plan_handoff_parser.add_argument("--candidate-bytes", required=True, type=int)
    plan_handoff_parser.add_argument("--evidence", action="append", default=[])
    plan_handoff_parser.add_argument("--allow-oversized-target", action="store_true")

    finalize_parser = subparsers.add_parser("finalize", help="Install a validated handoff transactionally")
    finalize_parser.add_argument("--root", required=True)
    finalize_parser.add_argument("--candidate", required=True)
    finalize_parser.add_argument("--target", required=True)
    finalize_parser.add_argument("--backup", default=BACKUP_PATH.as_posix())
    finalize_parser.add_argument("--approval-json", required=True)
    finalize_parser.add_argument("--approved-plan-id", required=True)
    finalize_parser.add_argument("--approved-plan-digest", required=True)

    validate_manifest_parser = subparsers.add_parser(
        "validate-manifest", help="Validate a governance plan and all bound preimages"
    )
    validate_manifest_parser.add_argument("--root", required=True)
    validate_manifest_parser.add_argument("--manifest", required=True)

    apply_manifest_parser = subparsers.add_parser(
        "apply-manifest", help="Apply an approved governance plan as a recoverable transaction"
    )
    apply_manifest_parser.add_argument("--root", required=True)
    apply_manifest_parser.add_argument("--manifest", required=True)
    apply_manifest_parser.add_argument("--approved-plan-id", required=True)
    apply_manifest_parser.add_argument("--approved-plan-digest", required=True)

    recover_parser = subparsers.add_parser(
        "recover-transaction", help="Recover a governance transaction after an interrupted apply"
    )
    recover_parser.add_argument("--root", required=True)
    recover_parser.add_argument("--plan-id", required=True)

    commit_parser = subparsers.add_parser(
        "commit-transaction", help="Commit a validated governance transaction and remove recovery data"
    )
    commit_parser.add_argument("--root", required=True)
    commit_parser.add_argument("--plan-id", required=True)
    commit_parser.add_argument("--approved-plan-digest", required=True)
    commit_parser.add_argument("--validation-passed", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "measure":
            files = [metrics(Path(path)) for path in args.paths]
            total = sum(item["bytes"] for item in files)
            over_total = args.max_total_bytes is not None and total > args.max_total_bytes
            over_each = [
                item["path"]
                for item in files
                if args.max_each_bytes is not None and item["bytes"] > args.max_each_bytes
            ]
            over = over_total or bool(over_each)
            emit(
                {
                    "ok": not over,
                    "files": files,
                    "total_bytes": total,
                    "total_lines": sum(item["lines"] for item in files),
                    "max_total_bytes": args.max_total_bytes,
                    "max_each_bytes": args.max_each_bytes,
                    "over_each": over_each,
                    "over_limit": over,
                },
                1 if over else 0,
            )
        if args.command == "sha":
            emit({"ok": True, "path": args.path, "sha256": sha256_file(Path(args.path))})
        if args.command == "validate":
            if args.cleanup_invalid or args.chmod_private:
                if not args.root:
                    raise GuardError("managed validation requires --root")
                result = validate_managed_candidate(
                    Path(args.root),
                    args.path,
                    cleanup_invalid=args.cleanup_invalid,
                    chmod_private=args.chmod_private,
                )
            else:
                result = validate_path(Path(args.path))
            emit(result, 0 if result["ok"] else 1)
        if args.command == "fingerprint":
            emit({"ok": True, **git_fingerprint(Path(args.root), args.exclude)})
        if args.command == "plan-context":
            emit(
                governance_plan_context(
                    Path(args.root).resolve(),
                    args.plan_id,
                    args.action_json,
                    args.validation_json,
                )
            )
        if args.command == "plan-handoff":
            emit(continuity_plan_context(args))
        if args.command == "finalize":
            emit(finalize(args))
        if args.command == "validate-manifest":
            root = Path(args.root).resolve()
            manifest_path = internal_managed_path(
                root, args.manifest, PurePosixPath(".neat/plans")
            )
            result = validate_governance_manifest(root, manifest_path)
            emit(
                {
                    "ok": True,
                    "plan_id": result["plan_id"],
                    "manifest_sha256": result["manifest_sha256"],
                    "approval_digest": result["approval_digest"],
                    "action_count": result["action_count"],
                    "candidate_bytes": result["candidate_bytes"],
                    "captured_head": result["fingerprint"]["head"],
                    "captured_fingerprint": result["fingerprint"]["fingerprint"],
                }
            )
        if args.command == "apply-manifest":
            emit(apply_governance_manifest(args))
        if args.command == "recover-transaction":
            emit(recover_governance_command(Path(args.root).resolve(), args.plan_id))
        if args.command == "commit-transaction":
            emit(commit_governance_command(args))
        raise GuardError(f"unknown command: {args.command}")
    except (GuardError, OSError) as exc:
        emit({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    main()
