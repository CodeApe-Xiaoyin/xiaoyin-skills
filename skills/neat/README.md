# Neat

Neat is a context-continuity Skill for AI coding agents. It preserves the current conversation and verified workspace state in one bounded handoff so a new conversation can continue without replaying the full history.

This page is for humans browsing the package. The executable agent instructions live in `SKILL.md`; keep them concise because they are loaded into model context when the Skill triggers.

## What It Does

- captures current intent, decisions, incomplete work, failed attempts, validation evidence, and the next safe action
- writes one validated handoff, normally `docs/HANDOFF.md`
- keeps the handoff under a 16 KiB hard limit
- preserves one recovery-only last-good copy at `.neat/HANDOFF.last-good.md`
- blocks likely secrets, observed concurrent edits, stale candidates, unsafe paths, and invalid handoff schemas

## What It Does Not Do

- does not manage general project documentation
- does not create session logs or archives
- does not append long-term history into handoff files
- does not edit README, AGENTS, CLAUDE, source, or general docs as part of a Neat run

## Package Contents

```text
skills/neat/
├── SKILL.md
├── agents/openai.yaml
├── scripts/neat_guard.py
└── tests/test_neat_guard.py
```

`SKILL.md` defines the model-facing workflow. `neat_guard.py` provides deterministic validation, fingerprinting, locking, cleanup, and atomic finalize behavior. The test suite covers the guard's production-critical behavior.

## Install

Copy the whole package directory into the target Skill directory:

```bash
mkdir -p ~/.codex/skills
cp -R skills/neat ~/.codex/skills/neat
```

For Claude Code:

```bash
mkdir -p ~/.claude/skills
cp -R skills/neat ~/.claude/skills/neat
```

Do not copy only `SKILL.md`; the guard script is required.

## Validate

Run from the repository root:

```bash
python3 -m unittest discover -s skills/neat/tests -v
git diff --check
```

The Skill validator can also be run when its Python environment has `PyYAML` installed:

```bash
python3 /path/to/skill-creator/scripts/quick_validate.py skills/neat
```

## Release

Neat uses Skill-level tags. After merging the 0.5 release, tag it as:

```text
neat-v0.5.0
```
