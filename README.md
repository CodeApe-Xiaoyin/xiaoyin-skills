# Xiaoyin Skills

A personal collection of AI coding-agent Skills.

This repository is a home for reusable Skills that help AI agents work across long projects, repeated workflows, and handoffs between conversations. The root README is intentionally a catalog and release index. Each Skill keeps its own executable instructions, scripts, tests, and implementation details inside its package directory.

## Skills

| Skill | Package | Status | Purpose |
|---|---|---|---|
| Neat | `skills/neat/` | `0.5.0` | Preserve bounded conversation state and reconcile long-term project knowledge without allowing managed context to grow without limit. |
| More skills | `skills/<name>/` | Planned | Future personal Skills will be added as separate packages. |

## Neat

Neat is a context-continuity and knowledge-governance Skill for AI coding agents. It handles both the volatile state needed by the next conversation and the durable project knowledge that must stay current across many conversations.

Neat optimizes for:

- current-conversation exit: intent, decisions, incomplete work, failed attempts, validation evidence, and next action
- next-conversation entry: one small validated handoff, compared against the current workspace before continuing
- bounded growth: one default-read handoff with a 16 KiB hard limit, plus one last-good recovery copy
- knowledge convergence: targeted reconciliation of stale, duplicated, conflicting, misplaced, and oversized project knowledge
- progressive loading: metadata and local streaming scans before the model reads affected sections
- safe persistence: plan-before-write, approval binding, concurrent-change detection, manual-content preservation, secret scanning, atomic handoff finalize, and recoverable multi-file governance transactions

Neat does not preserve full transcripts, append session logs, maintain per-session archives, or treat generated documentation as authority. Git remains the default history store. Knowledge governance is an explicit operation and never runs as a hidden full-repository side effect of a checkpoint.

## Install Neat

Clone the repository and copy the complete Neat package directory. Do not copy only `SKILL.md`; the guard script is part of the release package.

```bash
git clone https://github.com/CodeApe-Xiaoyin/xiaoyin-skills.git
mkdir -p ~/.codex/skills
cp -R xiaoyin-skills/skills/neat ~/.codex/skills/neat
```

For an existing installation, mirror the package so removed runtime files do not remain stale:

```bash
mkdir -p ~/.codex/skills/neat
rsync -a --delete xiaoyin-skills/skills/neat/ ~/.codex/skills/neat/
```

For Claude Code, copy the same package directory to its Skill location:

```bash
mkdir -p ~/.claude/skills
cp -R xiaoyin-skills/skills/neat ~/.claude/skills/neat
```

Use the same trailing-slash `rsync --delete` form for upgrades under `~/.claude/skills/neat/`.

Requirements:

- Python 3.9 or newer
- Git for Git-aware state binding; non-Git projects use a bounded content fingerprint

## Release Packages

Each Skill is published as a self-contained directory under `skills/`.

The Neat 0.5.0 release package is:

```text
skills/neat/
├── README.md
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── continuity.md
│   ├── governance.md
│   └── knowledge-model.md
├── scripts/
│   ├── neat_guard.py
│   └── neat_scan.py
└── tests/
    ├── test_neat_guard.py
    ├── test_neat_governance.py
    └── test_neat_scan.py
```

The package includes a small model-facing router, operation-specific references, deterministic scanning and transaction scripts, UI metadata, and tests. Implementation details belong inside the package; the repository root stays focused on discovery, installation, and release notes.

## Versioning

This repository uses Skill-level release tags instead of repository-wide release tags. Neat 0.5.0 should be tagged after merge as:

```text
neat-v0.5.0
```

Future Skills can use their own tag series, such as `example-skill-v0.1.0`.

## Release Notes

### Neat 0.5.0

Neat 0.5.0 is a greenfield rewrite. It does not preserve the 0.4 workflow or file model.

Highlights:

- replaces the old `neat-freak-xy` single-file entry with `skills/neat/`
- keeps the core purpose focused on preventing context loss while restoring bounded long-term knowledge governance
- switches model-facing instructions and generated handoffs to English while retaining Chinese trigger phrases
- enforces one bounded handoff with a 16 KiB hard limit and one recovery-only last-good copy
- separates checkpoint, handoff, resume, reconcile, compact, and finish through progressive reference loading
- moves deterministic validation, fingerprinting, approval binding, secret scanning, locking, cleanup, atomic finalize, and recoverable governance transactions into `scripts/neat_guard.py`
- adds bounded streaming inventory, temporal findings, redacted source-secret detection, link checks, and project-local locator validation through `scripts/neat_scan.py`
- restores the original editor-not-recorder, knowledge ownership, promotion, rule hygiene, and change-impact principles without restoring full-repository reads, automatic fixes, session logs, archives, `full`, or `reset`

Validation:

- `python3 -m unittest discover -s skills/neat/tests -v`
- `git diff --check`
- production acceptance covers writer-to-receiver handoff, knowledge reconciliation, large-file bounded scanning, concurrent edit rejection, multi-file rollback, idempotency, last-good recovery, oversized compaction, manual-byte preservation, redacted source scanning, stale candidates, cross-project authorization, and Chinese routing

## Attribution

Neat originated from [KKKKhazix/khazix-skills/neat-freak](https://github.com/KKKKhazix/khazix-skills/tree/main/neat-freak). Neat 0.5 retains its knowledge-editor principles while replacing exhaustive reads and automatic broad writes with bounded discovery, explicit plans, deterministic safety gates, and a dedicated continuity workflow.
