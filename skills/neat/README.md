# Neat

中文 | [English](README.en.md)

Neat 是一个上下文连续性和知识治理 Skill。它帮助 AI coding agent 在上下文快满、任务需要交接、项目文档和规则开始膨胀时，保存一份小而可靠的当前状态，并把已有知识整理回正确位置。

这份文档给人看。Agent 真正执行时读取的是 [`SKILL.md`](SKILL.md)，所以 `SKILL.md` 会保持尽量精简。

## 它适合做什么

- 保存当前目标、决策、未完成工作、失败尝试、验证证据和下一步。
- 写入一个可被新对话接手的 handoff，通常是 `docs/HANDOFF.md`。
- 保持 handoff 不超过 16 KiB。
- 保留一个只用于恢复的 last-good 副本：`.neat/HANDOFF.last-good.md`。
- 在新对话中校验 handoff 是否仍匹配当前仓库状态。
- 用本地扫描先看元数据和风险，再按需读取文档正文。
- 整理已有项目知识库里的重复、过期、冲突、错位和超大内容。
- 用 plan、approval digest、preimage hash、事务和恢复机制保护写入。

## 它不适合做什么

- 不从零搭建一个新知识库。
- 不保存完整聊天记录。
- 不创建 session log 或归档目录。
- 不把 handoff 当成长篇历史文档追加。
- 不在普通 checkpoint 时做全仓库文档审计。
- 不把 README、docs、日志、生成文件或源码注释里的命令当成权威指令执行。
- 不修改没有进入批准 allowlist 的文件。

如果你想从零设计知识库结构，那应该是另一个独立 Skill；Neat 只负责治理上下文和已有知识。

## 安装

从仓库根目录复制完整包到 Codex：

```bash
mkdir -p ~/.codex/skills
cp -R skills/neat ~/.codex/skills/neat
```

如果你是从 GitHub 克隆仓库：

```bash
git clone https://github.com/CodeApe-Xiaoyin/xiaoyin-skills.git
mkdir -p ~/.codex/skills
cp -R xiaoyin-skills/skills/neat ~/.codex/skills/neat
```

升级已有安装时建议使用镜像同步：

```bash
mkdir -p ~/.codex/skills/neat
rsync -a --delete xiaoyin-skills/skills/neat/ ~/.codex/skills/neat/
```

Claude Code：

```bash
mkdir -p ~/.claude/skills
cp -R xiaoyin-skills/skills/neat ~/.claude/skills/neat
```

要求：

- Python 3.9 或更新版本。
- Git 可选但推荐；Git 仓库会获得更强的 HEAD 和 fingerprint 绑定。

不要只复制 `SKILL.md`。Neat 需要 `scripts/neat_guard.py` 和 `scripts/neat_scan.py`。

## 使用

常用触发方式：

```text
$neat checkpoint
上下文快满了
保存对话进度
额度快没了需要交接
```

用于把当前任务保存成一个小 handoff。它会先给出只读计划，不会第一轮就写文件。

```text
$neat handoff
上下文交接
交接给新对话
```

用于主动交接给新对话，流程比 checkpoint 更完整。

```text
$neat resume
继续上个对话
读取交接继续
```

用于新对话读取 handoff，并检查它是否还匹配当前仓库状态。

```text
$neat reconcile
整理文档
同步知识库
清理记忆
规范体检
知识有冲突
```

用于治理已有项目知识：把重复事实合并，把过期说法更新，把错位内容移动到 canonical owner，把无法判断的冲突留给用户决策。

```text
$neat compact knowledge
清理文档膨胀
```

用于压缩或拆分已有知识文件，避免一个文档越来越大、越来越默认加载。

```text
$neat finish
收尾并交接
```

用于任务收尾：先治理受影响知识，再生成 handoff。

## 写入流程

Neat 所有写入都分两阶段：

1. Phase A：只读计划。扫描、hash、列出 allowlist、候选动作、风险、验证方式和 rollback，不写文件。
2. Phase B：批准后写入。你必须批准同一个 Plan ID 和 approval digest。任何目标、证据、候选 hash 或验证方式变化，都要重新计划。

知识治理写入会通过可恢复事务执行：先生成候选，预检全部目标，再 apply，验证通过后 commit cleanup；失败则 recover。

## 包内容

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

## 验证

从仓库根目录运行：

```bash
python3 -m unittest discover -s skills/neat/tests -v
git diff --check
```

如果本地 Skill Creator 环境安装了 `PyYAML`，也可以运行：

```bash
python3 /path/to/skill-creator/scripts/quick_validate.py skills/neat
```

## 发布

Neat 使用 Skill 级别 tag。0.5.0 合并后 tag 为：

```text
neat-v0.5.0
```
