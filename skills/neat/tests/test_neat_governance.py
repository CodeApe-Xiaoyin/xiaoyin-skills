from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "neat_guard.py"


def command(*args: str, cwd: Path, ok: bool = True, env: dict | None = None) -> dict:
    process = subprocess.run(
        ["python3", str(SCRIPT), *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )
    if ok and process.returncode != 0:
        raise AssertionError(process.stdout + process.stderr)
    if not ok and process.returncode == 0:
        raise AssertionError("command unexpectedly succeeded: " + process.stdout)
    return json.loads(process.stdout)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class GovernanceTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "neat@example.test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Neat Test"], check=True)
        (self.root / "docs").mkdir()
        (self.root / "docs/a.md").write_text("# A\nold\n")
        (self.root / "docs/b.md").write_text("# B\nold\n")
        (self.root / "app.py").write_text("PORT = 8080\n")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)
        self.plan_digests: dict[str, str] = {}

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, specs: list[dict], plan_id: str = "a" * 16, validation: list | None = None) -> dict:
        args = ["plan-context", "--root", ".", "--plan-id", plan_id]
        for spec in specs:
            requested = {
                "action": spec["action"],
                "target": spec["target"],
                "role": spec.get("role", "architecture"),
                "max_bytes": spec.get("max_bytes", 48 * 1024),
                "evidence": spec.get("evidence", ["app.py"]),
            }
            if spec["action"] != "delete":
                requested["candidate_sha"] = sha(spec["data"])
                requested["candidate_bytes"] = len(spec["data"])
            requested.update(spec.get("extra", {}))
            args.extend(["--action-json", json.dumps(requested, sort_keys=True)])
        if validation is not None:
            args.extend(["--validation-json", json.dumps(validation)])
        return command(*args, cwd=self.root)

    def make_manifest(
        self, specs: list[dict], plan_id: str = "a" * 16, validation: list | None = None
    ) -> tuple[str, Path]:
        planned = self.plan(specs, plan_id, validation)
        actions = []
        for spec, action_with_display in zip(specs, planned["actions"]):
            action = {key: value for key, value in action_with_display.items() if key != "target_bytes"}
            if spec["action"] != "delete":
                candidate = self.root / action["candidate"]
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(spec["data"])
            actions.append(action)
        manifest = {
            "schema": "neat/governance-plan/0.5",
            "plan_id": plan_id,
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "root_id": planned["root_id"],
            "captured_head": planned["captured_head"],
            "captured_fingerprint": planned["captured_fingerprint"],
            "approval_digest": planned["approval_digest"],
            "actions": actions,
            "validation": planned["validation"],
        }
        path = self.root / planned["manifest"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, sort_keys=True))
        self.plan_digests[plan_id] = planned["approval_digest"]
        return plan_id, path

    def apply(
        self,
        plan_id: str,
        path: Path,
        *,
        ok: bool = True,
        env: dict | None = None,
        digest: str | None = None,
    ) -> dict:
        return command(
            "apply-manifest",
            "--root",
            ".",
            "--manifest",
            path.relative_to(self.root).as_posix(),
            "--approved-plan-id",
            plan_id,
            "--approved-plan-digest",
            digest or self.plan_digests[plan_id],
            cwd=self.root,
            ok=ok,
            env=env,
        )

    def commit(self, plan_id: str, *, ok: bool = True, env: dict | None = None) -> dict:
        return command(
            "commit-transaction",
            "--root",
            ".",
            "--plan-id",
            plan_id,
            "--approved-plan-digest",
            self.plan_digests[plan_id],
            "--validation-passed",
            cwd=self.root,
            ok=ok,
            env=env,
        )

    def commit_while_mutating(self, plan_id: str, mutate) -> dict:
        environment = {**os.environ, "NEAT_TEST_DELAY_BEFORE_FINAL_REVALIDATION": "0.35"}
        process = subprocess.Popen(
            [
                "python3", str(SCRIPT), "commit-transaction", "--root", ".",
                "--plan-id", plan_id,
                "--approved-plan-digest", self.plan_digests[plan_id],
                "--validation-passed",
            ],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        time.sleep(0.15)
        mutate()
        stdout, stderr = process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0, stdout + stderr)
        return json.loads(stdout)

    def test_applies_then_commits_multi_file_transaction(self):
        plan_id, path = self.make_manifest(
            [
                {"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"},
                {"action": "replace", "target": "docs/b.md", "data": b"# B\nnew\n"},
            ],
            validation=[["python3", "-m", "unittest"]],
        )
        validated = command(
            "validate-manifest", "--root", ".", "--manifest", path.relative_to(self.root).as_posix(), cwd=self.root
        )
        self.assertEqual(validated["action_count"], 2)
        self.assertEqual(validated["approval_digest"], self.plan_digests[plan_id])
        result = self.apply(plan_id, path)
        self.assertEqual(result["status"], "awaiting_validation")
        self.assertTrue((self.root / ".neat/recovery" / plan_id).exists())
        committed = self.commit(plan_id)
        self.assertEqual(committed["status"], "committed")
        self.assertEqual((self.root / "docs/a.md").read_text(), "# A\nnew\n")
        self.assertEqual((self.root / "docs/b.md").read_text(), "# B\nnew\n")
        self.assertFalse(path.exists())
        self.assertFalse((self.root / ".neat/stage" / plan_id).exists())
        self.assertFalse((self.root / ".neat/recovery" / plan_id).exists())

    def test_plan_context_binds_actions_preimages_and_candidate(self):
        specs = [
            {"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"},
            {"action": "create", "target": "docs/new.md", "data": b"# New\n"},
        ]
        result = self.plan(specs, "c" * 16)
        self.assertEqual(result["plan_id"], "c" * 16)
        self.assertEqual(result["actions"][0]["expected_sha"], sha((self.root / "docs/a.md").read_bytes()))
        self.assertEqual(result["actions"][1]["expected_sha"], "missing")
        self.assertEqual(result["actions"][1]["candidate_sha"], sha(b"# New\n"))
        self.assertEqual(len(result["approval_digest"]), 64)

    def test_approval_id_and_digest_are_bound(self):
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"}]
        )
        result = command(
            "apply-manifest",
            "--root", ".",
            "--manifest", path.relative_to(self.root).as_posix(),
            "--approved-plan-id", "b" * 16,
            "--approved-plan-digest", self.plan_digests[plan_id],
            cwd=self.root,
            ok=False,
        )
        self.assertIn("approved plan ID", result["error"])
        result = self.apply(plan_id, path, ok=False, digest="b" * 64)
        self.assertIn("approved plan digest", result["error"])

    def test_manifest_target_or_action_swap_breaks_approval_digest(self):
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"}]
        )
        payload = json.loads(path.read_text())
        candidate = self.root / payload["actions"][0]["candidate"]
        payload["actions"][0]["action"] = "delete"
        payload["actions"][0]["candidate"] = None
        payload["actions"][0]["candidate_sha"] = None
        payload["actions"][0]["candidate_bytes"] = 0
        candidate.unlink()
        path.write_text(json.dumps(payload))
        result = self.apply(plan_id, path, ok=False)
        self.assertIn("differs from the approved plan", result["error"])
        self.assertTrue((self.root / "docs/a.md").exists())

    def test_evidence_change_invalidates_plan(self):
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"}]
        )
        (self.root / "app.py").write_text("PORT = 9000\n")
        result = self.apply(plan_id, path, ok=False)
        self.assertIn("evidence changed", result["error"])

    def test_injected_failure_rolls_back_all_targets(self):
        plan_id, path = self.make_manifest(
            [
                {"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"},
                {"action": "replace", "target": "docs/b.md", "data": b"# B\nnew\n"},
            ]
        )
        result = self.apply(plan_id, path, ok=False, env={"NEAT_TEST_FAIL_AFTER": "1"})
        self.assertIn("transaction_recovered=true", result["error"])
        self.assertEqual((self.root / "docs/a.md").read_text(), "# A\nold\n")
        self.assertEqual((self.root / "docs/b.md").read_text(), "# B\nold\n")
        self.assertFalse((self.root / ".neat/recovery" / plan_id).exists())
        self.assertFalse((self.root / ".neat/stage" / plan_id).exists())
        self.assertFalse(path.exists())

    def test_commit_revalidates_phase_a_evidence_and_scope(self):
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"}]
        )
        self.apply(plan_id, path)
        (self.root / "app.py").write_text("PORT = 9000\n")
        result = self.commit(plan_id, ok=False)
        self.assertTrue(
            "scope changed" in result["error"] or "evidence changed" in result["error"]
        )
        (self.root / "app.py").write_text("PORT = 8080\n")
        recovered = command(
            "recover-transaction", "--root", ".", "--plan-id", plan_id, cwd=self.root
        )
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual((self.root / "docs/a.md").read_text(), "# A\nold\n")

    def test_final_revalidation_rejects_target_change_during_post_scan_window(self):
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"}]
        )
        self.apply(plan_id, path)
        result = self.commit_while_mutating(
            plan_id, lambda: (self.root / "docs/a.md").write_text("# A\nconcurrent\n")
        )
        self.assertIn("target changed", result["error"])
        self.assertTrue((self.root / ".neat/recovery" / plan_id).exists())

    def test_final_revalidation_rejects_evidence_change_during_post_scan_window(self):
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"}]
        )
        self.apply(plan_id, path)
        result = self.commit_while_mutating(
            plan_id, lambda: (self.root / "app.py").write_text("PORT = 9001\n")
        )
        self.assertTrue("scope changed" in result["error"] or "evidence changed" in result["error"])
        self.assertTrue((self.root / ".neat/recovery" / plan_id).exists())

    def test_final_revalidation_rejects_scoped_change_during_post_scan_window(self):
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"}]
        )
        self.apply(plan_id, path)
        result = self.commit_while_mutating(
            plan_id, lambda: (self.root / "docs/b.md").write_text("# B\nconcurrent\n")
        )
        self.assertIn("scope changed", result["error"])
        self.assertTrue((self.root / ".neat/recovery" / plan_id).exists())

    def test_second_pending_transaction_is_rejected(self):
        first_id, first_path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nfirst\n"}],
            plan_id="1" * 16,
        )
        self.apply(first_id, first_path)
        requested = {
            "action": "replace",
            "target": "docs/b.md",
            "role": "architecture",
            "max_bytes": 48 * 1024,
            "evidence": [],
            "candidate_sha": sha(b"# B\nsecond\n"),
            "candidate_bytes": len(b"# B\nsecond\n"),
        }
        result = command(
            "plan-context",
            "--root", ".",
            "--plan-id", "2" * 16,
            "--action-json", json.dumps(requested),
            cwd=self.root,
            ok=False,
        )
        self.assertIn("requires commit or recovery before planning", result["error"])
        command("recover-transaction", "--root", ".", "--plan-id", first_id, cwd=self.root)
        self.assertEqual((self.root / "docs/a.md").read_text(), "# A\nold\n")
        self.assertEqual((self.root / "docs/b.md").read_text(), "# B\nold\n")

    def test_post_write_link_failure_can_recover(self):
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\n[bad](missing.md)\n"}]
        )
        self.apply(plan_id, path)
        result = self.commit(plan_id, ok=False)
        self.assertIn("new broken links", result["error"])
        recovered = command("recover-transaction", "--root", ".", "--plan-id", plan_id, cwd=self.root)
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual((self.root / "docs/a.md").read_text(), "# A\nold\n")

    def test_no_change_does_not_touch_target(self):
        target = self.root / "docs/a.md"
        before = target.stat().st_mtime_ns
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": target.read_bytes()}]
        )
        time.sleep(0.01)
        result = self.apply(plan_id, path)
        self.assertEqual(result["status"], "checked_no_change")
        self.assertEqual(target.stat().st_mtime_ns, before)

    def test_frontmatter_timestamp_only_change_is_semantic_noop(self):
        target = self.root / "docs/a.md"
        target.write_text("---\nupdated: 2026-07-10\n---\n# A\nstable\n")
        before = target.stat().st_mtime_ns
        candidate = b"---\nupdated: 2026-07-11\n---\n# A\nstable\n"
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": candidate}]
        )
        time.sleep(0.01)
        result = self.apply(plan_id, path)
        self.assertEqual(result["status"], "checked_no_change")
        self.assertEqual(target.stat().st_mtime_ns, before)

    def test_temporal_candidate_is_rejected(self):
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nRecently TODO 2024-01-01.\n"}]
        )
        result = self.apply(plan_id, path, ok=False)
        self.assertIn("relative time", result["error"])

    def test_secret_candidate_is_rejected(self):
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\napi_token=ghp_abcdefghijklmnopqrstuvwxyz123456\n"}]
        )
        result = self.apply(plan_id, path, ok=False)
        self.assertIn("possible secret", result["error"])

    def test_secret_in_link_query_is_rejected(self):
        plan_id, path = self.make_manifest(
            [
                {
                    "action": "replace",
                    "target": "docs/a.md",
                    "data": b"# A\n[x](missing.md?token=abcdefghijklmnopqrstuvwxyz123456)\n",
                }
            ]
        )
        result = self.apply(plan_id, path, ok=False)
        self.assertIn("possible secret", result["error"])

    def test_delete_manual_block_requires_explicit_approval(self):
        target = self.root / "docs/a.md"
        target.write_text("# A\n<!-- manual:start -->\nkeep\n<!-- manual:end -->\n")
        subprocess.run(["git", "-C", str(self.root), "add", "docs/a.md"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "manual"], check=True)
        plan_id, path = self.make_manifest([{"action": "delete", "target": "docs/a.md"}])
        result = self.apply(plan_id, path, ok=False)
        self.assertIn("manual block", result["error"])

    def test_target_policy_blocks_source_mdx_and_active_handoff(self):
        (self.root / "package.json").write_text("{}\n")
        (self.root / "view.mdx").write_text("# Executable\n")
        (self.root / "docs/HANDOFF.md").write_text("handoff\n")
        for target in ("package.json", "view.mdx", "docs/HANDOFF.md"):
            requested = {
                "action": "replace",
                "target": target,
                "role": "architecture",
                "max_bytes": 1024,
                "evidence": [],
                "candidate_sha": sha(b"new\n"),
                "candidate_bytes": 4,
            }
            result = command(
                "plan-context",
                "--root", ".",
                "--plan-id", "e" * 16,
                "--action-json", json.dumps(requested),
                cwd=self.root,
                ok=False,
            )
            self.assertFalse(result["ok"])

    def test_nested_instruction_marker_protects_package_handoff(self):
        package = self.root / "pkg"
        package.mkdir()
        (package / "AGENTS.md").write_text(
            "<!-- neat:handoff_path=pkg/HANDOFF.md -->\n"
        )
        (package / "HANDOFF.md").write_text("handoff\n")
        requested = {
            "action": "replace",
            "target": "pkg/HANDOFF.md",
            "role": "architecture",
            "max_bytes": 1024,
            "evidence": [],
            "candidate_sha": sha(b"new\n"),
            "candidate_bytes": 4,
        }
        result = command(
            "plan-context",
            "--root", ".",
            "--plan-id", "9" * 16,
            "--action-json", json.dumps(requested),
            cwd=self.root,
            ok=False,
        )
        self.assertIn("active handoff", result["error"])

    @unittest.skipIf(os.name == "nt", "symlink fixture differs on Windows")
    def test_scanner_error_cannot_be_treated_as_zero_broken_links(self):
        outside = self.root / "outside.md"
        outside.write_text("# Outside\n")
        (self.root / "linked.md").symlink_to(outside)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "symlink"], check=True)
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"}]
        )
        result = self.apply(plan_id, path, ok=False)
        self.assertIn("scanner failed", result["error"])
        self.assertEqual((self.root / "docs/a.md").read_text(), "# A\nold\n")

    def test_locator_requires_dedicated_schema_and_current_handoff_sha(self):
        handoff = self.root / "docs/HANDOFF.md"
        handoff.write_text("handoff\n")
        bad = json.dumps({"schema": "wrong", "handoff": "docs/HANDOFF.md"}).encode()
        plan_id, path = self.make_manifest(
            [{"action": "create", "target": ".neat/active.json", "role": "locator", "max_bytes": 1024, "data": bad}]
        )
        result = self.apply(plan_id, path, ok=False)
        self.assertIn("locator candidate keys", result["error"])

    def test_project_document_mode_is_preserved(self):
        if os.name == "nt":
            self.skipTest("POSIX modes are not portable to Windows")
        target = self.root / "docs/a.md"
        target.chmod(0o664)
        plan_id, path = self.make_manifest(
            [{"action": "replace", "target": "docs/a.md", "data": b"# A\nnew\n"}]
        )
        self.apply(plan_id, path)
        self.assertEqual(target.stat().st_mode & 0o777, 0o664)
        self.commit(plan_id)

    @unittest.skipIf(os.name == "nt", "POSIX dirfd semantics required")
    def test_anchored_write_does_not_follow_swapped_parent_symlink(self):
        spec = importlib.util.spec_from_file_location("neat_guard_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        original_dir = self.root / "docs"
        directory_fd = module.ensure_directory_beneath(self.root, original_dir)
        moved = self.root / "docs-old"
        outside = self.root / "outside"
        outside.mkdir()
        original_dir.rename(moved)
        original_dir.symlink_to(outside, target_is_directory=True)
        try:
            module.write_atomic_at(directory_fd, original_dir, "anchored.md", b"safe\n", 0o644)
        finally:
            os.close(directory_fd)
        self.assertEqual((moved / "anchored.md").read_bytes(), b"safe\n")
        self.assertFalse((outside / "anchored.md").exists())

    @unittest.skipIf(os.name == "nt", "hardlink semantics differ on Windows")
    def test_candidate_hardlink_alias_is_rejected(self):
        specs = [{"action": "replace", "target": "docs/a.md", "data": (self.root / "docs/a.md").read_bytes()}]
        planned = self.plan(specs)
        action = {key: value for key, value in planned["actions"][0].items() if key != "target_bytes"}
        candidate = self.root / action["candidate"]
        candidate.parent.mkdir(parents=True)
        os.link(self.root / "docs/a.md", candidate)
        manifest = {
            "schema": "neat/governance-plan/0.5",
            "plan_id": planned["plan_id"],
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "root_id": planned["root_id"],
            "captured_head": planned["captured_head"],
            "captured_fingerprint": planned["captured_fingerprint"],
            "approval_digest": planned["approval_digest"],
            "actions": [action],
            "validation": [],
        }
        path = self.root / planned["manifest"]
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(manifest))
        self.plan_digests[planned["plan_id"]] = planned["approval_digest"]
        result = self.apply(planned["plan_id"], path, ok=False)
        self.assertIn("hardlink", result["error"])

    def test_standalone_recovery_rejects_external_changes(self):
        plan_id = "d" * 16
        target = self.root / "docs/a.md"
        original = target.read_bytes()
        applied = b"# A\nnew\n"
        target.write_bytes(applied)
        recovery = self.root / ".neat/recovery" / plan_id
        backup = recovery / "files/docs/a.md"
        backup.parent.mkdir(parents=True)
        backup.write_bytes(original)
        state = {
            "schema": "neat/governance-recovery/0.5",
            "plan_id": plan_id,
            "manifest_sha256": "e" * 64,
            "approval_digest": "f" * 64,
            "validation": [],
            "baseline_links": [],
            "baseline_locator": {"ok": True, "exists": False},
            "status": "applying",
            "applied": ["docs/a.md"],
            "actions": [
                {
                    "operation": "replace",
                    "target": "docs/a.md",
                    "original_sha": sha(original),
                    "applied_sha": sha(applied),
                    "original_mode": 0o644,
                    "applied_mode": 0o644,
                    "backup": backup.relative_to(self.root).as_posix(),
                }
            ],
        }
        (recovery / "state.json").write_text(json.dumps(state))
        result = command("recover-transaction", "--root", ".", "--plan-id", plan_id, cwd=self.root)
        self.assertEqual(result["status"], "recovered")
        self.assertEqual(target.read_bytes(), original)

        target.write_bytes(applied)
        backup.parent.mkdir(parents=True)
        backup.write_bytes(original)
        (recovery / "state.json").write_text(json.dumps(state))
        target.write_text("external change\n")
        result = command(
            "recover-transaction", "--root", ".", "--plan-id", plan_id, cwd=self.root, ok=False
        )
        self.assertIn("outside the interrupted transaction", result["error"])
        self.assertEqual(target.read_text(), "external change\n")


if __name__ == "__main__":
    unittest.main()
