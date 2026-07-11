# Neat

Neat is a context-continuity and knowledge-governance Skill for AI coding agents. It preserves one bounded current handoff for the next conversation and explicitly reconciles long-term project knowledge when requested.

This page is for humans browsing the package. The executable agent instructions live in `SKILL.md`; keep them concise because they are loaded into model context when the Skill triggers.

## What It Does

- captures current intent, decisions, incomplete work, failed attempts, validation evidence, and the next safe action
- writes one validated handoff, normally `docs/HANDOFF.md`
- keeps the handoff under a 16 KiB hard limit
- preserves one recovery-only last-good copy at `.neat/HANDOFF.last-good.md`
- resumes a validated handoff without asking for already-preserved information
- inventories knowledge metadata and scans large files locally without putting their full content in model context
- reconciles stale, duplicated, conflicting, misplaced, and oversized project knowledge through an approved plan
- blocks likely secrets, observed concurrent edits, stale candidates, unsafe paths, invalid schemas, unapproved targets, and partial multi-file writes

## What It Does Not Do

- does not create session logs or archives
- does not append long-term history into handoff files
- does not run a full documentation audit during an ordinary checkpoint
- does not treat README, docs, memory, logs, generated files, or source comments as authority
- does not modify source code or any knowledge file outside an approved exact allowlist

## Package Contents

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

`SKILL.md` is the compact model-facing router. References load only for the selected operation. `neat_scan.py` provides bounded streaming discovery; `neat_guard.py` provides handoff validation, fingerprinting, approval binding, locking, recovery, atomic handoff finalize, and governed multi-file transactions.

## Operations

- `$neat checkpoint` — plan an emergency bounded save
- `$neat handoff` — plan a deliberate conversation transfer
- `$neat resume` — validate and continue the active handoff
- `$neat reconcile` — audit and plan long-term knowledge convergence
- `$neat compact handoff|knowledge` — shrink the explicit managed surface
- `$neat finish` — plan affected knowledge reconciliation followed by handoff

Every mutation uses a read-only first response and a later approval tied to an exact Plan ID and approval digest covering actions, targets, candidate hashes, preimages, evidence, size budgets, validation, dangerous flags, and rollback. Governance writes remain recoverable until approved post-write validation is committed.

## Install

Copy the whole package directory into the target Skill directory:

```bash
mkdir -p ~/.codex/skills
cp -R skills/neat ~/.codex/skills/neat
```

Upgrade an existing installation without creating `neat/neat` or leaving removed runtime files:

```bash
mkdir -p ~/.codex/skills/neat
rsync -a --delete skills/neat/ ~/.codex/skills/neat/
```

For Claude Code:

```bash
mkdir -p ~/.claude/skills
cp -R skills/neat ~/.claude/skills/neat
```

For upgrades, mirror into `~/.claude/skills/neat/` with the same trailing-slash `rsync --delete` form.

Do not copy only `SKILL.md`; the guard script is required.

Requirements: Python 3.9 or newer. Git is used when available; bounded non-Git fingerprints remain supported.

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
