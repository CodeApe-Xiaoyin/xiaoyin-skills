from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "neat_guard.py"
SPEC = importlib.util.spec_from_file_location("neat_guard", MODULE_PATH)
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(guard)


def run(*args: str, cwd: Path) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def handoff(metadata: dict[str, str], *, goal: str = "Ship the context handoff.", manual: str = "") -> bytes:
    values = {
        "branch": metadata.get("branch", "not-git"),
        "captured_head": metadata.get("captured_head", metadata.get("head", "not-git")),
        "captured_worktree": metadata.get("captured_worktree", metadata.get("worktree", "snapshot")),
        "captured_fingerprint": metadata.get(
            "captured_fingerprint", metadata.get("fingerprint", "0" * 64)
        ),
    }
    text = f"""---
schema: neat/0.5
updated: 2026-07-11T12:00:00+08:00
handoff_status: complete
reason: checkpoint
project: fixture
scope: .
branch: {values['branch']}
captured_head: {values['captured_head']}
captured_worktree: {values['captured_worktree']}
captured_fingerprint: {values['captured_fingerprint']}
---

## Goal
{goal}

## Acceptance
- A receiver can continue without the old conversation.

## Constraints
- Keep one bounded current handoff.

## Decisions
- Use a transactional guard — it protects concurrent work.

## State
The candidate is ready for validation.

## Done
- The contract was captured.

## Pending
- Install the candidate.

## Risks
None.

## Failed
None.

## Validation
- tests: passed — unit fixture
- build: not run — no build exists
- typecheck: not run — no type checker exists
- manual: passed — candidate inspected

## Files
- `docs/HANDOFF.md` — current context target.

## Next
1. Install the validated candidate.

<!-- manual:start -->{manual}<!-- manual:end -->
"""
    return text.encode()


class NeatGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def git(self) -> dict:
        run("git", "init", "-q", cwd=self.root)
        run("git", "config", "user.email", "test@example.invalid", cwd=self.root)
        run("git", "config", "user.name", "Neat Test", cwd=self.root)
        (self.root / "tracked.txt").write_text("base\n")
        run("git", "add", "tracked.txt", cwd=self.root)
        run("git", "commit", "-qm", "base", cwd=self.root)
        return guard.git_fingerprint(
            self.root,
            ["docs/HANDOFF.md", "docs/HANDOFF.md.next", ".neat/HANDOFF.last-good.md", ".neat/neat.lock"],
        )

    def write_candidate(self, metadata: dict[str, str], **kwargs) -> Path:
        path = self.root / "docs/HANDOFF.md.next"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(handoff(metadata, **kwargs))
        path.chmod(0o600)
        return path

    def finalize_args(
        self,
        candidate: Path,
        fingerprint: dict,
        target_sha: str = "missing",
        *,
        target: str = "docs/HANDOFF.md",
        candidate_sha: str | None = None,
        allow_oversized_target: bool = False,
    ) -> Namespace:
        plan_id = "a" * 16
        candidate_relative = str(candidate.relative_to(self.root))
        approval = {
            "schema": "neat/continuity-approval/0.5",
            "plan_id": plan_id,
            "root_id": guard.sha256_bytes(str(self.root.resolve()).encode()),
            "target": target,
            "candidate": candidate_relative,
            "backup": ".neat/HANDOFF.last-good.md",
            "expected_target_sha": target_sha,
            "expected_candidate_sha": candidate_sha or guard.sha256_file(candidate),
            "candidate_bytes": candidate.stat().st_size,
            "captured_head": fingerprint["head"],
            "captured_fingerprint": fingerprint["fingerprint"],
            "evidence": [],
            "allow_oversized_target": allow_oversized_target,
        }
        return Namespace(
            root=str(self.root),
            candidate=candidate_relative,
            target=target,
            backup=".neat/HANDOFF.last-good.md",
            approval_json=json.dumps(approval, sort_keys=True),
            approved_plan_id=plan_id,
            approved_plan_digest=guard.canonical_json_sha(approval),
        )

    def finalize(self, candidate: Path, fingerprint: dict, target_sha: str = "missing", **kwargs):
        return guard.finalize(
            self.finalize_args(
                candidate,
                fingerprint,
                target_sha,
                allow_oversized_target=kwargs.get("allow_oversized_target", False),
            )
        )

    def test_validates_complete_handoff(self):
        result = guard.validate_bytes(handoff({}))
        self.assertTrue(result["ok"], result["errors"])

    def test_rejects_empty_metadata_fenced_heading_and_secret(self):
        bad = handoff({}).decode().replace("project: fixture", "project:")
        bad = bad.replace("## Goal\n", "```md\n## Goal\n```\n", 1)
        bad += "\napi_token = abcdefghijklmnopqrstuvwxyz123456\n"
        result = guard.validate_bytes(bad.encode())
        self.assertFalse(result["ok"])
        joined = " ".join(result["errors"])
        self.assertIn("empty frontmatter", joined)
        self.assertIn("missing required sections: Goal", joined)
        self.assertIn("possible secret", joined)

    def test_rejects_generic_high_entropy_assignment(self):
        bad = handoff({}).decode().replace(
            "The candidate is ready for validation.",
            "The candidate is ready.\nnonce = Zk9vQ2h4cW5TQm1WcXR5TjRkM0E9",
        )
        result = guard.validate_bytes(bad.encode())
        self.assertFalse(result["ok"])
        self.assertIn("possible secret", " ".join(result["errors"]))

    def test_auth_header_is_not_secret_allowlisted(self):
        bad = handoff({}).decode().replace(
            "The candidate is ready for validation.",
            "The candidate is ready.\nauth_header = QWxwaGEvQmV0YStHYW1tYT1EZWx1ZQ==",
        )
        result = guard.validate_bytes(bad.encode())
        self.assertFalse(result["ok"])
        self.assertIn("possible secret", " ".join(result["errors"]))

    def test_high_entropy_branch_is_allowed_only_in_frontmatter(self):
        valid = handoff({"branch": "feature/context-handoff-12345"}).replace(
            b"project: fixture", b"project: enterprise-context-handoff-service"
        )
        result = guard.validate_bytes(valid)
        self.assertTrue(result["ok"], result["errors"])
        bad = valid.decode().replace(
            "The candidate is ready for validation.",
            "The candidate is ready.\ncaptured_head: QWxwaGEvQmV0YStHYW1tYT1EZWx1ZQ==",
        )
        result = guard.validate_bytes(bad.encode())
        self.assertFalse(result["ok"])
        self.assertIn("possible secret", " ".join(result["errors"]))

        hex_alias = valid.decode().replace(
            "The candidate is ready for validation.",
            "The candidate is ready.\ndigest: 0123456789abcdef0123456789abcdef01234567",
        )
        result = guard.validate_bytes(hex_alias.encode())
        self.assertFalse(result["ok"])
        self.assertIn("possible secret", " ".join(result["errors"]))

    def test_rejects_semantically_empty_goal_and_malformed_files(self):
        bad = handoff({}).decode().replace("Ship the context handoff.", "None.")
        bad = bad.replace("- `docs/HANDOFF.md` — current context target.", "/tmp/HANDOFF.md")
        result = guard.validate_bytes(bad.encode())
        joined = " ".join(result["errors"])
        self.assertIn("must be meaningful: Goal", joined)
        self.assertIn("invalid Files entry", joined)

    def test_rejects_oversize_even_without_custom_limit(self):
        result = guard.validate_bytes(handoff({}) + b"x" * guard.HARD_LIMIT)
        self.assertFalse(result["ok"])
        self.assertIn("hard limit", " ".join(result["errors"]))

    def test_measure_enforces_per_file_and_total_limits(self):
        large = self.root / "AGENTS.md"
        small = self.root / "CLAUDE.md"
        large.write_bytes(b"x" * (20 * 1024))
        small.write_bytes(b"y" * 1024)
        completed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "measure",
                str(large),
                str(small),
                "--max-each-bytes",
                str(16 * 1024),
                "--max-total-bytes",
                str(32 * 1024),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertEqual([str(large)], payload["over_each"])
        self.assertFalse(payload["total_bytes"] > 32 * 1024)

    def test_non_git_state_is_explicit(self):
        result = guard.git_fingerprint(self.root, [])
        self.assertRegex(result["fingerprint"], r"^[a-f0-9]{64}$")
        self.assertEqual("snapshot", result["worktree"])

    def test_non_git_workspace_change_changes_fingerprint(self):
        before = guard.git_fingerprint(self.root, [])
        (self.root / "task.txt").write_text("changed\n")
        after = guard.git_fingerprint(self.root, [])
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])

    def test_non_git_finalize(self):
        state = guard.git_fingerprint(self.root, [])
        candidate = self.write_candidate(state)
        result = self.finalize(candidate, state)
        self.assertTrue(result["ok"])
        self.assertTrue((self.root / "docs/HANDOFF.md").exists())

    def test_finalize_and_timestamp_only_idempotency(self):
        state = self.git()
        candidate = self.write_candidate(state)
        first = self.finalize(candidate, state)
        self.assertFalse(first["checked_no_change"])
        target = self.root / "docs/HANDOFF.md"
        original_sha = guard.sha256_file(target)

        candidate = self.write_candidate(state)
        candidate.write_text(candidate.read_text().replace("12:00:00", "12:01:00"))
        second = self.finalize(candidate, state, original_sha)
        self.assertTrue(second["checked_no_change"])
        self.assertEqual("missing", second["backup_state"])
        self.assertEqual(original_sha, guard.sha256_file(target))

    def test_semantic_update_creates_only_valid_backup(self):
        state = self.git()
        candidate = self.write_candidate(state, goal="First goal.")
        self.finalize(candidate, state)
        target = self.root / "docs/HANDOFF.md"
        old = target.read_bytes()
        old_sha = guard.sha256_file(target)
        candidate = self.write_candidate(state, goal="Updated goal.")
        result = self.finalize(candidate, state, old_sha)
        self.assertEqual("updated_from_valid_target", result["backup_result"])
        backup = self.root / ".neat/HANDOFF.last-good.md"
        self.assertEqual(old, backup.read_bytes())
        self.assertTrue(guard.validate_path(backup)["ok"])

    def test_invalid_target_does_not_poison_existing_backup(self):
        state = self.git()
        backup = self.root / ".neat/HANDOFF.last-good.md"
        backup.parent.mkdir(parents=True)
        backup.write_bytes(handoff(state))
        backup_sha = guard.sha256_file(backup)
        target = self.root / "docs/HANDOFF.md"
        target.parent.mkdir(parents=True)
        target.write_text("invalid current handoff\n")
        target_sha = guard.sha256_file(target)
        candidate = self.write_candidate(state)
        result = self.finalize(candidate, state, target_sha)
        self.assertEqual("skipped_invalid_or_oversized_target", result["backup_result"])
        self.assertEqual(backup_sha, guard.sha256_file(backup))

    def test_manual_block_must_be_preserved(self):
        state = self.git()
        candidate = self.write_candidate(state, manual="\nuser note\n")
        self.finalize(candidate, state)
        target = self.root / "docs/HANDOFF.md"
        candidate = self.write_candidate(state, manual="\nchanged\n")
        with self.assertRaisesRegex(guard.GuardError, "manual block"):
            self.finalize(candidate, state, guard.sha256_file(target))

    def test_concurrent_workspace_change_is_rejected(self):
        state = self.git()
        candidate = self.write_candidate(state)
        (self.root / "tracked.txt").write_text("changed\n")
        with self.assertRaisesRegex(guard.GuardError, "worktree changed"):
            self.finalize(candidate, state)
        self.assertFalse((self.root / "docs/HANDOFF.md").exists())

    def test_candidate_hash_and_metadata_are_bound(self):
        state = self.git()
        candidate = self.write_candidate(state)
        expected_sha = guard.sha256_file(candidate)
        candidate.write_bytes(handoff(state, goal="Changed after validation."))
        args = self.finalize_args(candidate, state, candidate_sha=expected_sha)
        with self.assertRaisesRegex(guard.GuardError, "candidate changed"):
            guard.finalize(args)

        candidate.write_bytes(handoff({**state, "branch": "fabricated"}))
        with self.assertRaisesRegex(guard.GuardError, "metadata"):
            self.finalize(candidate, state)

    def test_continuity_plan_is_zero_write_and_binds_approval(self):
        state = self.git()
        candidate_data = handoff(state)
        tracked = self.root / "tracked.txt"
        os.utime(tracked, None)

        def snapshot() -> dict:
            return {
                path.relative_to(self.root).as_posix(): (
                    guard.sha256_file(path),
                    path.stat().st_mtime_ns,
                    path.stat().st_mode,
                )
                for path in self.root.rglob("*")
                if path.is_file()
            }

        before = snapshot()
        result = guard.continuity_plan_context(
            Namespace(
                root=str(self.root),
                plan_id="b" * 16,
                target="docs/HANDOFF.md",
                candidate_sha=guard.sha256_bytes(candidate_data),
                candidate_bytes=len(candidate_data),
                evidence=["tracked.txt"],
                allow_oversized_target=False,
            )
        )
        after = snapshot()
        self.assertEqual(before, after)
        self.assertEqual(result["phase_a_writes"], 0)
        self.assertEqual(result["approval"]["expected_target_sha"], "missing")
        self.assertEqual(result["approval"]["expected_candidate_sha"], guard.sha256_bytes(candidate_data))
        self.assertEqual(result["approval"]["evidence"][0]["path"], "tracked.txt")

    def test_finalize_requires_approved_plan_and_rejects_swapped_candidate(self):
        state = self.git()
        candidate = self.write_candidate(state)
        process = subprocess.run(
            [
                sys.executable, str(MODULE_PATH), "finalize", "--root", str(self.root),
                "--candidate", "docs/HANDOFF.md.next", "--target", "docs/HANDOFF.md",
            ],
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse((self.root / "docs/HANDOFF.md").exists())

        args = self.finalize_args(candidate, state)
        approval = json.loads(args.approval_json)
        approval["expected_candidate_sha"] = "f" * 64
        args.approval_json = json.dumps(approval, sort_keys=True)
        with self.assertRaisesRegex(guard.GuardError, "digest"):
            guard.finalize(args)
        self.assertFalse((self.root / "docs/HANDOFF.md").exists())

    def test_invalid_candidate_is_cleaned_by_finalize(self):
        state = self.git()
        candidate = self.write_candidate(state)
        candidate.write_text(candidate.read_text() + "\napi_token = abcdefghijklmnopqrstuvwxyz123456\n")
        args = self.finalize_args(candidate, state)
        with self.assertRaisesRegex(guard.GuardError, "validation failed"):
            guard.finalize(args)
        self.assertFalse(candidate.exists())

    def test_validate_can_make_candidate_private_and_clean_invalid(self):
        candidate = self.write_candidate({})
        candidate.chmod(0o644)
        result = guard.validate_managed_candidate(
            self.root, "docs/HANDOFF.md.next", cleanup_invalid=False, chmod_private=True
        )
        self.assertTrue(result["ok"])
        self.assertEqual(0o600, candidate.stat().st_mode & 0o777)
        candidate.write_text(candidate.read_text() + "\nsecret = abcdefghijklmnopqrstuvwxyz123456\n")
        result = guard.validate_managed_candidate(
            self.root, "docs/HANDOFF.md.next", cleanup_invalid=True, chmod_private=True
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["cleaned_invalid"])
        self.assertFalse(candidate.exists())

    def test_managed_validation_cannot_delete_arbitrary_file(self):
        readme = self.root / "README.md"
        readme.write_text("ordinary file\n")
        with self.assertRaisesRegex(guard.GuardError, r"\.md\.next"):
            guard.validate_managed_candidate(
                self.root, "README.md", cleanup_invalid=True, chmod_private=True
            )
        self.assertEqual("ordinary file\n", readme.read_text())

    def test_oversized_candidate_is_cleaned_without_loading_it(self):
        candidate = self.write_candidate({})
        candidate.write_bytes(b"secret-material\n" * 2000)
        result = guard.validate_managed_candidate(
            self.root, "docs/HANDOFF.md.next", cleanup_invalid=True, chmod_private=True
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result["cleaned_invalid"])
        self.assertFalse(candidate.exists())

    def test_path_roles_symlinks_and_hardlinks_are_rejected(self):
        state = self.git()
        candidate = self.write_candidate(state)
        args = self.finalize_args(candidate, state, target="AGENTS.md")
        with self.assertRaisesRegex(guard.GuardError, "candidate must"):
            guard.finalize(args)

        candidate.unlink()
        outside = self.root / "real-candidate"
        outside.write_bytes(handoff(state))
        candidate.symlink_to(outside)
        symlink_args = self.finalize_args(candidate, state, candidate_sha=guard.sha256_file(outside))
        with self.assertRaisesRegex(guard.GuardError, "symlink"):
            guard.finalize(symlink_args)

        candidate.unlink()
        candidate.write_bytes(handoff(state))
        target = self.root / "docs/HANDOFF.md"
        os.link(candidate, target)
        with self.assertRaisesRegex(guard.GuardError, "alias"):
            self.finalize(candidate, state, guard.sha256_file(target))

    def test_shell_hostile_relative_path_is_rejected(self):
        state = self.git()
        candidate = self.write_candidate(state)
        args = self.finalize_args(candidate, state, target="docs/HANDOFF;touch-PWNED.md")
        with self.assertRaisesRegex(guard.GuardError, "unsafe relative path"):
            guard.finalize(args)

    def test_absolute_managed_path_is_rejected(self):
        with self.assertRaisesRegex(guard.GuardError, "absolute managed path"):
            guard.lexical_inside(self.root, str(self.root / "docs/HANDOFF.md"))

    def test_oversized_target_requires_explicit_compact_and_skips_backup(self):
        state = self.git()
        target = self.root / "docs/HANDOFF.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x" * (guard.HARD_LIMIT + 1))
        target_sha = guard.sha256_file(target)
        candidate = self.write_candidate(state)
        with self.assertRaisesRegex(guard.GuardError, "oversized"):
            self.finalize(candidate, state, target_sha)
        candidate.write_text(candidate.read_text().replace("reason: checkpoint", "reason: compact"))
        result = self.finalize(candidate, state, target_sha, allow_oversized_target=True)
        self.assertEqual("skipped_invalid_or_oversized_target", result["backup_result"])
        self.assertFalse((self.root / ".neat/HANDOFF.last-good.md").exists())

    def test_oversized_flag_requires_compact_reason(self):
        state = self.git()
        target = self.root / "docs/HANDOFF.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x" * (guard.HARD_LIMIT + 1))
        candidate = self.write_candidate(state)
        with self.assertRaisesRegex(guard.GuardError, "requires reason: compact"):
            self.finalize(
                candidate, state, guard.sha256_file(target), allow_oversized_target=True
            )

    def test_oversized_target_manual_block_must_be_preserved(self):
        state = self.git()
        target = self.root / "docs/HANDOFF.md"
        target.parent.mkdir(parents=True)
        target.write_bytes(
            b"invalid\n<!-- manual:start -->\nuser-owned\n<!-- manual:end -->\n"
            + b"x" * guard.HARD_LIMIT
        )
        candidate = self.write_candidate(state)
        candidate.write_text(candidate.read_text().replace("reason: checkpoint", "reason: compact"))
        with self.assertRaisesRegex(guard.GuardError, "manual block"):
            self.finalize(
                candidate, state, guard.sha256_file(target), allow_oversized_target=True
            )

    def test_stale_candidate_is_excluded_from_fingerprint(self):
        state = self.git()
        candidate = self.write_candidate(state)
        after = guard.git_fingerprint(
            self.root,
            ["docs/HANDOFF.md", "docs/HANDOFF.md.next", ".neat/HANDOFF.last-good.md", ".neat/neat.lock"],
        )
        self.assertEqual(state["fingerprint"], after["fingerprint"])
        self.assertTrue(candidate.exists())

    def test_git_root_must_be_top_level(self):
        self.git()
        nested = self.root / "packages/app"
        nested.mkdir(parents=True)
        with self.assertRaisesRegex(guard.GuardError, "top-level"):
            guard.git_fingerprint(nested, [])

    def test_concurrent_neat_writer_is_rejected_by_lock(self):
        state = self.git()
        candidate = self.write_candidate(state)
        lock = self.root / ".neat/neat.lock"
        lock.parent.mkdir(parents=True)
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            guard.acquire_lock(descriptor)
            with self.assertRaisesRegex(guard.GuardError, "another Neat"):
                self.finalize(candidate, state)
        finally:
            guard.release_lock(descriptor)
            os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
