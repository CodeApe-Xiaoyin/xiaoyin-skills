#!/usr/bin/env python3
"""Validate, fingerprint, and transactionally install Neat handoffs."""

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
from pathlib import Path, PurePosixPath

if os.name == "nt":
    import msvcrt
else:
    import fcntl


HARD_LIMIT = 16 * 1024
MANUAL_SCAN_LIMIT = 64 * 1024 * 1024
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
)
ASSIGNMENT = re.compile(
    r"(?i)(?:^|[\s,{;])([a-z][a-z0-9_.-]*(?:key|token|secret|password|passwd|credential|cookie|authorization)[a-z0-9_.-]*)\s*[:=]\s*['\"]?([^\s,'\";}]{8,})"
)
GENERIC_ASSIGNMENT = re.compile(
    r"(?:^|[\s,{;])([A-Za-z_][A-Za-z0-9_.-]*)\s*[:=]\s*['\"]?([A-Za-z0-9_+./=-]{20,})"
)
SAFE_RELATIVE_PATH = re.compile(r"[A-Za-z0-9._/-]+")


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
    ).stdout


def normalize_relative(value: str) -> str:
    normalized = PurePosixPath(value).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


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
            if relative in excluded:
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
    entries = [(status, path) for status, path in parse_status(raw) if path not in excluded]
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


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
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


def write_atomic_at(directory_fd: int | None, directory: Path, name: str, data: bytes) -> None:
    if directory_fd is None:
        write_atomic(directory / name, data)
        return
    temporary = f".{name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd)
    try:
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
        if candidate_validation["sha256"] != args.expected_candidate_sha:
            raise GuardError("candidate changed after validation")

        actual_target_sha = sha256_at(target_directory_fd, target.parent, target.name)
        if actual_target_sha != args.expected_target_sha:
            raise GuardError(
                f"target changed after planning: expected {args.expected_target_sha}, got {actual_target_sha}"
            )

        excluded = [
            target.relative_to(root).as_posix(),
            candidate.relative_to(root).as_posix(),
            backup.relative_to(root).as_posix(),
            LOCK_PATH.as_posix(),
        ]
        current = git_fingerprint(root, excluded)
        if current["fingerprint"] != args.expected_worktree:
            raise GuardError(
                f"worktree changed after planning: expected {args.expected_worktree}, got {current['fingerprint']}"
            )
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
        if args.allow_oversized_target and captured.get("reason") != "compact":
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

        if target_info is not None and target_info.st_size > HARD_LIMIT and not args.allow_oversized_target:
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

    finalize_parser = subparsers.add_parser("finalize", help="Install a validated handoff transactionally")
    finalize_parser.add_argument("--root", required=True)
    finalize_parser.add_argument("--candidate", required=True)
    finalize_parser.add_argument("--target", required=True)
    finalize_parser.add_argument("--backup", default=BACKUP_PATH.as_posix())
    finalize_parser.add_argument("--expected-target-sha", required=True)
    finalize_parser.add_argument("--expected-candidate-sha", required=True)
    finalize_parser.add_argument("--expected-worktree", required=True)
    finalize_parser.add_argument("--allow-oversized-target", action="store_true")
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
        if args.command == "finalize":
            emit(finalize(args))
        raise GuardError(f"unknown command: {args.command}")
    except (GuardError, OSError) as exc:
        emit({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    main()
