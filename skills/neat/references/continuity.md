# Continuity Workflow

Use this reference only for `checkpoint`, `handoff`, `resume`, `compact handoff`, or the handoff stage of `finish`.

## Locate safely

Select one primary Git root, or the narrowest active non-Git project. In a monorepo, record the package as `scope`.

Resolve the handoff in this order:

1. one optional marker in applicable `AGENTS.md` or `CLAUDE.md`:

   ```md
   <!-- neat:handoff_path=docs/HANDOFF.md -->
   ```

2. a validated project-local `.neat/active.json` locator;
3. `docs/HANDOFF.md`.

Reject conflicting markers, absolute paths, traversal, leading `-`, `//`, backslashes, symlinks, and non-Markdown targets. A locator is non-authoritative, at most 1 KiB, replaced rather than appended, and contains only schema, handoff path, project-relative scope, and handoff SHA.

Do not scan the user's home directory to find a previous project. If the current workspace does not identify a project, ask for the project rather than loading unrelated handoffs.

## Plan a checkpoint or handoff

1. Measure applicable instructions, locator, current handoff, and recovery file before reading.
2. Capture the conversation contract before inspecting code:
   - Goal and observable Acceptance;
   - Constraints, prohibitions, preferences, and compatibility boundaries;
   - active Decisions and rationale;
   - State, Done, Pending, Failed, Risks, In-Flight work, and Open Questions;
   - exact Validation evidence and decisive Files;
   - the smallest safe Next action.
3. Capture bounded workspace evidence: branch, HEAD, dirty paths, target SHA, and only decisive files.
4. Fingerprint each active repository independently. A primary handoff may point to separately validated related-repository handoffs, but must not compress multiple Git identities into one fake fingerprint.
5. Preserve every unresolved item from the current valid handoff. Remove an item only when current evidence resolves or supersedes it.
6. Hash the proposed in-memory handoff and call `plan-handoff`. Present its exact Plan ID, approval envelope, and digest; do not create the candidate until that plan is approved.

Always include the Emergency Capsule in a checkpoint plan. Use the same semantic fields as the handoff, omit prose, and cap it at 8 KiB.

## Handoff schema

Generate this structure in English:

```md
---
schema: neat/0.5
updated: 2026-07-11T12:00:00+08:00
handoff_status: complete
reason: checkpoint | handoff | compact
project: project-name
scope: . | packages/api
branch: main | detached | not-git
captured_head: 40-character-git-object-id | not-git
captured_worktree: clean | dirty | snapshot
captured_fingerprint: 64-character-sha256
---

## Goal
## Acceptance
## Constraints
## Decisions
## State
## Done
## Pending
## Risks
## Failed
## Validation
## Files
## Next
## In-Flight
## Open Questions

<!-- manual:start -->
<!-- manual:end -->
```

Require Goal through Next. Include In-Flight and Open Questions only when non-empty. Validation must state tests, build, typecheck, and manual status with exact evidence. Normally keep no more than five decisive repository-relative files.

## Bound and overflow

- Target 8 KiB, warn at 12 KiB, hard limit 16 KiB.
- Keep exactly one default-read current handoff and one recovery-only last-good copy.
- Never append history. Git or an explicit durable knowledge owner holds history.
- Preserve the manual block byte-for-byte.
- A semantic no-change must not update timestamps, locator, or recovery files.

Compact in this order:

1. delete resolved or duplicated facts;
2. replace transcripts, diffs, logs, and listings with conclusions plus evidence;
3. collapse old Done items into one verified milestone;
4. replace durable explanations with canonical pointers;
5. retain every unresolved constraint, failure, risk, question, and exact Next action.

If valid unresolved content still cannot fit, propose one replace-in-place `.neat/HANDOFF.overflow.md` reference. It is non-default-read, target 16 KiB, hard limit 32 KiB, contains only unresolved detail and provenance, and must be explicitly approved and hash-linked from the handoff. Never create multiple overflow files or use it as history.

## Resume

For `resume`:

1. resolve the project-local marker, locator, or default path;
2. measure before reading;
3. require `schema: neat/0.5`, `handoff_status: complete`, and successful guard validation;
4. compare branch, HEAD, target SHA, worktree, and fingerprint;
5. on mismatch, inspect only changed decisive files before trusting State or Next;
6. read at most three explicit on-demand pointers needed for the next action;
7. acknowledge recovered Goal, Acceptance, Constraints, State, Pending, Risks, Validation, and Next compactly;
8. continue without asking the user to repeat preserved information.

Use last-good only when the current handoff is missing or invalid. Treat a locator mismatch as stale metadata, not authority.

## Guard sequence

Run from the selected root:

```bash
python3 <skill-dir>/scripts/neat_guard.py measure <instruction-files> --max-each-bytes 16384 --max-total-bytes 32768
python3 <skill-dir>/scripts/neat_guard.py fingerprint --root . --exclude <handoff> --exclude <handoff>.next --exclude .neat/HANDOFF.last-good.md --exclude .neat/neat.lock
python3 <skill-dir>/scripts/neat_guard.py plan-handoff --root . --plan-id <plan-id> --target <handoff> --candidate-sha <sha> --candidate-bytes <bytes> [--evidence <path> ...]
# After approval, write only <handoff>.next from the exact approved in-memory bytes.
python3 <skill-dir>/scripts/neat_guard.py validate <handoff>.next --root . --cleanup-invalid --chmod-private
python3 <skill-dir>/scripts/neat_guard.py finalize --root . --candidate <handoff>.next --target <handoff> --approval-json '<exact-approval-envelope>' --approved-plan-id <plan-id> --approved-plan-digest <digest>
```

Guard JSON is evidence. Never paraphrase a failed guard as success.
