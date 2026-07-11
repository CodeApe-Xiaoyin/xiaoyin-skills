# Xiaoyin Skills

A personal collection of AI coding-agent Skills.

This repository is a home for reusable Skills that help AI agents work across long projects, repeated workflows, and handoffs between conversations. The root README is intentionally a catalog and release index. Each Skill keeps its own executable instructions, scripts, tests, and implementation details inside its package directory.

## Skills

| Skill | Package | Status | Purpose |
|---|---|---|---|
| Neat | `skills/neat/` | `0.5.0` | Preserve current conversation context in one bounded handoff so a new conversation can continue without replaying the full history. |
| More skills | `skills/<name>/` | Planned | Future personal Skills will be added as separate packages. |

## Neat

Neat is a context-continuity Skill for AI coding agents. It is designed for the moment when the context window, quota, or working session is about to run out and the next conversation needs a precise continuation point.

Neat optimizes for:

- current-conversation exit: intent, decisions, incomplete work, failed attempts, validation evidence, and next action
- next-conversation entry: one small validated handoff, compared against the current workspace before continuing
- bounded growth: one default-read handoff with a 16 KiB hard limit, plus one last-good recovery copy
- safe persistence: plan-before-write, concurrent-change detection, manual-content preservation, secret scanning, and atomic finalize behavior

Neat is not a general documentation-management system. It does not append session logs, maintain archives, or grow project documents over time. Git remains the long-term history store.

## Install Neat

Clone the repository and copy the complete Neat package directory. Do not copy only `SKILL.md`; the guard script is part of the release package.

```bash
git clone https://github.com/CodeApe-Xiaoyin/xiaoyin-skills.git
mkdir -p ~/.codex/skills
cp -R xiaoyin-skills/skills/neat ~/.codex/skills/neat
```

For Claude Code, copy the same package directory to its Skill location:

```bash
mkdir -p ~/.claude/skills
cp -R xiaoyin-skills/skills/neat ~/.claude/skills/neat
```

Requirements:

- Python 3
- Git for Git-aware state binding; non-Git projects use a bounded content fingerprint

## Release Packages

Each Skill is published as a self-contained directory under `skills/`.

The Neat 0.5.0 release package is:

```text
skills/neat/
├── SKILL.md
├── agents/openai.yaml
├── scripts/neat_guard.py
└── tests/test_neat_guard.py
```

The package includes the model-facing Skill instructions, UI metadata, deterministic guard script, and tests. Implementation details belong inside the package; the repository root stays focused on discovery, installation, and release notes.

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
- keeps the core purpose focused on preventing context loss and handing state to the next conversation
- switches model-facing instructions and generated handoffs to English while retaining Chinese trigger phrases
- enforces one bounded handoff with a 16 KiB hard limit and one recovery-only last-good copy
- moves deterministic validation, fingerprinting, secret scanning, locking, cleanup, and atomic finalize behavior into `scripts/neat_guard.py`
- removes the old documentation-governance model, including session logs, archives, durable context blocks, `full`, and `reset`

Validation:

- `python3 -m unittest discover -s skills/neat/tests -v`
- `git diff --check`
- production acceptance covered writer-to-receiver handoff, concurrent edit rejection, idempotency, last-good recovery, oversized compaction, manual-byte preservation, secret cleanup, stale candidates, and Chinese false-positive no-ops

## Attribution

Neat originated from [KKKKhazix/khazix-skills/neat-freak](https://github.com/KKKKhazix/khazix-skills/tree/main/neat-freak). Neat 0.5 keeps the context-handoff purpose while replacing the previous documentation-management architecture.
