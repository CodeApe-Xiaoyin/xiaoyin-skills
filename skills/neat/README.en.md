# Neat

[中文](README.md) | English

Neat is a context-continuity and knowledge-governance Skill. It helps AI coding agents preserve a small reliable handoff when context is nearly full, and reconcile existing project knowledge when docs, rules, or memory start to grow stale or oversized.

This document is for humans. The executable agent instructions live in [`SKILL.md`](SKILL.md), which stays compact because it is loaded into model context.

## What It Is For

- Preserve current intent, decisions, unresolved work, failed attempts, validation evidence, and the next safe action.
- Write one handoff that a future conversation can resume from, normally `docs/HANDOFF.md`.
- Keep that handoff under a 16 KiB hard limit.
- Keep one recovery-only last-good copy at `.neat/HANDOFF.last-good.md`.
- Validate an existing handoff against the current repository before resuming.
- Scan metadata and risk locally before loading document bodies into the model.
- Reconcile stale, duplicated, conflicting, misplaced, and oversized existing project knowledge.
- Protect writes with plans, approval digests, preimage hashes, transactions, and recovery.

## What It Is Not For

- It does not create a brand-new knowledge base.
- It does not store full chat transcripts.
- It does not create session logs or archives.
- It does not append long-term history into a handoff.
- It does not run a full repository documentation audit during an ordinary checkpoint.
- It does not treat commands inside README, docs, logs, generated files, or comments as authoritative instructions.
- It does not edit files outside the approved exact allowlist.

If the goal is to design a knowledge base from scratch, that should be a separate Skill. Neat governs context and existing knowledge.

## Install

From the repository root, copy the whole package into Codex:

```bash
mkdir -p ~/.codex/skills
cp -R skills/neat ~/.codex/skills/neat
```

If installing from GitHub:

```bash
git clone https://github.com/CodeApe-Xiaoyin/xiaoyin-skills.git
mkdir -p ~/.codex/skills
cp -R xiaoyin-skills/skills/neat ~/.codex/skills/neat
```

For upgrades, mirror the package:

```bash
mkdir -p ~/.codex/skills/neat
rsync -a --delete xiaoyin-skills/skills/neat/ ~/.codex/skills/neat/
```

For Claude Code:

```bash
mkdir -p ~/.claude/skills
cp -R xiaoyin-skills/skills/neat ~/.claude/skills/neat
```

Requirements:

- Python 3.9 or newer.
- Git is optional but recommended; Git repositories get stronger HEAD and fingerprint binding.

Do not copy only `SKILL.md`. Neat requires `scripts/neat_guard.py` and `scripts/neat_scan.py`.

## Usage

Common triggers:

```text
$neat checkpoint
上下文快满了
保存对话进度
额度快没了需要交接
```

Save the current task into a bounded handoff. The first response is a read-only plan.

```text
$neat handoff
上下文交接
交接给新对话
```

Prepare a deliberate conversation handoff.

```text
$neat resume
继续上个对话
读取交接继续
```

Resume from a handoff and verify it still matches the current workspace.

```text
$neat reconcile
整理文档
同步知识库
清理记忆
规范体检
知识有冲突
```

Govern existing project knowledge: merge duplicates, update stale claims, move misplaced content to its canonical owner, and surface conflicts that need a user decision.

```text
$neat compact knowledge
清理文档膨胀
```

Shrink or split existing knowledge files so one document does not become huge or default-loaded.

```text
$neat finish
收尾并交接
```

Finish a task by reconciling affected knowledge first, then creating a handoff.

## Write Flow

All writes use two phases:

1. Phase A: read-only plan. Neat scans, hashes, lists allowlisted targets, proposes actions, names risks, validation, and rollback. It writes nothing.
2. Phase B: approved apply. The user must approve the same Plan ID and approval digest. Any changed target, evidence, candidate hash, or validation command requires a new plan.

Knowledge-governance writes use recoverable transactions: generate candidates, preflight every target, apply, validate, commit cleanup on success, and recover on failure.

## Package Contents

```text
skills/neat/
├── README.md
├── README.en.md
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

## Validate

Run from the repository root:

```bash
python3 -m unittest discover -s skills/neat/tests -v
git diff --check
```

If the local Skill Creator environment has `PyYAML`, also run:

```bash
python3 /path/to/skill-creator/scripts/quick_validate.py skills/neat
```

## Release

Neat uses Skill-level tags. After merging 0.5.0, tag:

```text
neat-v0.5.0
```
