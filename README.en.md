# Xiaoyin Skills

[中文](README.md) | English

A personal collection of AI Agent Skills that I use, iterate, and publish.

This repository is a Skill collection, not a single-Skill project. Each Skill lives under `skills/<name>/` with its own `SKILL.md`, scripts, references, tests, and human documentation. The root README is only a catalog, install guide, and release index.

## Catalog

| Skill | Version | Path | Summary |
|---|---:|---|---|
| Neat | 0.5.0 | [`skills/neat/`](skills/neat/) | Prevent context overflow, preserve handoff-ready state, and govern existing project knowledge so it does not grow stale, duplicated, or unbounded. |
| More skills | Planned | `skills/<name>/` | Future Skills will be added as separate packages. |

## Install

The easiest option is to ask a Skill-aware agent:

```text
Install this skill: https://github.com/CodeApe-Xiaoyin/xiaoyin-skills/tree/main/skills/neat
```

### Option 1: Git

Clone the full Skill collection, then copy Neat:

```bash
git clone https://github.com/CodeApe-Xiaoyin/xiaoyin-skills.git
mkdir -p ~/.codex/skills
cp -R xiaoyin-skills/skills/neat ~/.codex/skills/neat
```

To fetch only the Neat subdirectory, use Git sparse checkout:

```bash
git clone --filter=blob:none --sparse https://github.com/CodeApe-Xiaoyin/xiaoyin-skills.git
cd xiaoyin-skills
git sparse-checkout set skills/neat
mkdir -p ~/.codex/skills
cp -R skills/neat ~/.codex/skills/neat
```

Upgrade an existing install with a mirror sync so removed files do not stay behind:

```bash
mkdir -p ~/.codex/skills/neat
rsync -a --delete xiaoyin-skills/skills/neat/ ~/.codex/skills/neat/
```

For Claude Code:

```bash
mkdir -p ~/.claude/skills
cp -R xiaoyin-skills/skills/neat ~/.claude/skills/neat
```

### Option 2: npx

If Node.js is available, `npx degit` can download only the Neat subdirectory without Git history:

```bash
npx degit CodeApe-Xiaoyin/xiaoyin-skills/skills/neat ~/.codex/skills/neat
```

There is no standalone npm package yet, so `npm install neat` is not supported. A dedicated npm package or install script can be added later if a cross-platform one-command installer becomes useful.

Do not copy only `SKILL.md`. Neat requires its guard and scanner scripts.

## Neat

> When context is nearly full, work needs a handoff, or project docs are starting to rot, Neat helps the agent leave the workspace in a state the next conversation can trust.

Neat is a context-continuity and knowledge-governance Skill. It does not build a new knowledge base from scratch. It keeps existing project knowledge bounded, reconciled, and safe to load.

Neat handles three recurring problems:

- Current conversation exit: preserve intent, decisions, unresolved work, failed attempts, validation evidence, and the next safe action.
- Next conversation entry: resume from one small validated handoff and compare it with the current workspace.
- Existing knowledge cleanup: reconcile stale, duplicated, conflicting, misplaced, and oversized README, rule, docs, runbook, architecture, API, and integration content.

Neat deliberately does not:

- store full chat transcripts;
- create session logs or archives;
- append long-term history into the handoff;
- run a hidden full-repository documentation audit during an ordinary checkpoint;
- design a brand-new knowledge base structure. That belongs in a separate Skill.

### Triggers

```text
$neat checkpoint
上下文快满了
保存对话进度
额度快没了需要交接

$neat handoff
上下文交接
交接给新对话

$neat resume
继续上个对话
读取交接继续

$neat reconcile
整理文档
同步知识库
清理记忆
规范体检
知识有冲突

$neat compact knowledge
清理文档膨胀
压缩交接文档

$neat finish
收尾并交接
```

Every mutation starts with a read-only plan. Neat may write only after the user approves the same Plan ID and approval digest.

See the [Neat package docs](skills/neat/README.en.md) for full usage.

## Release Package

Each Skill is a self-contained package. Neat 0.5.0 ships as:

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

`SKILL.md` is the compact model-facing router. `references/` are loaded progressively for the selected operation. `scripts/` provide deterministic scanning, validation, transactions, and recovery. `tests/` cover the release behavior.

## Versioning

This repository uses Skill-level tags instead of one repository-wide version.

After merge, Neat 0.5.0 should be tagged:

```text
neat-v0.5.0
```

Future Skills can use their own tag series, such as:

```text
some-skill-v0.1.0
```

## Release Notes

### Neat 0.5.0

Neat 0.5.0 is a greenfield rewrite and does not preserve the 0.4 file model.

Highlights:

- moves from the old single-file entry to an independent `skills/neat/` package;
- keeps Chinese trigger phrases while using English for model-facing instructions and generated handoffs;
- replaces ever-growing summaries with one 16 KiB bounded handoff;
- adds explicit routing for checkpoint, handoff, resume, reconcile, compact, and finish;
- adds a bounded scanner for local size, temporal, link, and secret-risk discovery;
- adds approval digests, concurrency checks, manual-block preservation, atomic finalization, and recoverable multi-file governance transactions;
- keeps the original knowledge-editor spirit while removing exhaustive reads, automatic broad writes, session logs, archives, `full`, and `reset`.

Validation:

- `python3 -m unittest discover -s skills/neat/tests -v`
- `git diff --check`
- Skill validator
- production acceptance for handoff, resume, knowledge governance, large files, concurrent edits, rollback, idempotency, secret/link/locator/temporal gates, Chinese routing, and non-Git projects.

## Attribution

Neat was inspired by [KKKKhazix/khazix-skills/neat-freak](https://github.com/KKKKhazix/khazix-skills/tree/main/neat-freak). Version 0.5 keeps the idea of cleaning stale project knowledge while rebuilding the workflow around bounded context, explicit planning, and production-grade safety gates.
