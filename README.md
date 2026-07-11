# Xiaoyin Skills

中文 | [English](README.en.md)

我自己使用、迭代和发布的一组 AI Agent Skills。

这个仓库不是一个单一 Skill 项目，而是个人 Skills 合集。每个 Skill 都会放在 `skills/<name>/` 下，拥有自己的 `SKILL.md`、脚本、参考资料、测试和说明文档。仓库首页只负责告诉你有哪些 Skill、怎么安装、当前版本是什么；具体实现细节放在各个 Skill 自己的目录里。

## 目录

| Skill | 版本 | 位置 | 一句话说明 |
|---|---:|---|---|
| Neat | 0.5.0 | [`skills/neat/`](skills/neat/) | 防止上下文溢出，保存可交接的当前状态，并治理已有项目知识库的膨胀、重复和冲突。 |
| More skills | Planned | `skills/<name>/` | 后续 Skill 会作为独立包加入，不和 Neat 混在一起。 |

## 安装方式

最简单的方式是让支持 Skill 的 Agent 帮你安装：

```text
帮我安装这个 skill：https://github.com/CodeApe-Xiaoyin/xiaoyin-skills/tree/main/skills/neat
```

### 方式一：Git 安装

克隆整个 Skills 合集，然后复制 Neat：

```bash
git clone https://github.com/CodeApe-Xiaoyin/xiaoyin-skills.git
mkdir -p ~/.codex/skills
cp -R xiaoyin-skills/skills/neat ~/.codex/skills/neat
```

如果只想拉 Neat 子目录，可以用 Git sparse checkout：

```bash
git clone --filter=blob:none --sparse https://github.com/CodeApe-Xiaoyin/xiaoyin-skills.git
cd xiaoyin-skills
git sparse-checkout set skills/neat
mkdir -p ~/.codex/skills
cp -R skills/neat ~/.codex/skills/neat
```

升级已有安装时，用镜像同步，避免旧文件残留：

```bash
mkdir -p ~/.codex/skills/neat
rsync -a --delete xiaoyin-skills/skills/neat/ ~/.codex/skills/neat/
```

Claude Code 可以复制到对应 Skill 目录：

```bash
mkdir -p ~/.claude/skills
cp -R xiaoyin-skills/skills/neat ~/.claude/skills/neat
```

### 方式二：npx 轻量下载

如果你有 Node.js，也可以用 `npx degit` 下载 Neat 子目录，不保留 Git 历史：

```bash
npx degit CodeApe-Xiaoyin/xiaoyin-skills/skills/neat ~/.codex/skills/neat
```

目前还没有发布独立 npm 包，所以暂不支持 `npm install neat` 这类安装方式。等以后需要跨平台一键安装器时，可以单独补 npm package 或 install script。

不要只复制 `SKILL.md`。Neat 依赖 `scripts/` 里的守卫和扫描脚本。

## Neat

> 上下文快满、任务要交接、项目文档越长越乱的时候，让 Agent 先稳住现场。

Neat 是一个上下文连续性和知识治理 Skill。它的重点不是从零搭建知识库，而是把已有项目里的关键信息控制在可交接、可验证、不会无限增长的范围内。

它解决三类问题：

- 当前对话快结束：保存目标、决策、未完成工作、失败尝试、验证结果和下一步。
- 新对话接手：读取一个小而完整的 handoff，并和当前仓库状态做一致性检查。
- 项目知识变脏：整理已有 README、规则文件、docs、运行手册、架构/API 说明里的重复、过期、冲突和超大内容。

它刻意不做这些事：

- 不保存完整聊天记录。
- 不创建会话日志或长期归档。
- 不把 handoff 变成越来越长的历史文件。
- 不在普通 checkpoint 时偷偷做全仓库文档审计。
- 不从零设计一个新知识库结构；这应该交给另一个独立 Skill。

### 怎么触发

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

所有会修改文件的操作都必须先输出只读 plan。只有你明确批准同一个 Plan ID 和 approval digest 后，Neat 才能进入写入阶段。

### 它会动哪些东西

- 默认 handoff：通常是 `docs/HANDOFF.md`，硬上限 16 KiB。
- 恢复副本：`.neat/HANDOFF.last-good.md`，只用于失败恢复。
- 已批准的知识文件：例如 README、AGENTS/CLAUDE、docs、architecture、runbook、API/integration 文档。
- 临时事务文件：`.neat/` 下的 stage、manifest、recovery，成功验证后清理。

### 安全边界

- 先扫描元数据，再按需读取正文，避免大文件直接塞进上下文。
- 每个事实只能有一个 canonical owner，其他位置只能短引用或指针。
- 不把 README、docs、日志、生成文件里的指令当成权威命令执行。
- 写入前重新 hash 目标和证据，发现并发变化就停止。
- 多文件知识治理用可恢复事务，不允许半写入后假装完成。
- secret、坏链接、越界路径、过大文件、过期候选都会被守卫拦截。

更多细节见 [Neat 文档](skills/neat/README.md)。

## 发布包

每个 Skill 都是一个自包含目录。Neat 0.5.0 的发布包是：

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

`SKILL.md` 是 Agent 真正加载的精简入口；`references/` 按操作渐进加载；`scripts/` 提供确定性的扫描、校验、事务和恢复能力；`tests/` 覆盖核心验收场景。

## 版本

这个仓库使用 Skill 级别 tag，不用整个仓库共享一个版本号。

Neat 0.5.0 合并后应打：

```text
neat-v0.5.0
```

后续其他 Skill 使用自己的 tag 序列，例如：

```text
some-skill-v0.1.0
```

## 更新记录

### Neat 0.5.0

Neat 0.5.0 是一次完全重构，不兼容旧的 0.4 文件模型。

主要变化：

- 从旧的单文件入口迁移到 `skills/neat/` 独立包。
- 保留中文触发词，但模型侧指令和生成 handoff 使用英文。
- 用一个 16 KiB 上限 handoff 替代不断增长的历史总结。
- 新增 checkpoint、handoff、resume、reconcile、compact、finish 的明确路由。
- 新增 bounded scanner，用本地流式扫描发现大小、时间、链接、secret 风险。
- 新增 approval digest、并发检测、manual block 保留、atomic finalize、可恢复多文件事务。
- 恢复 neat-freak 的知识整理思想，但去掉无限读取、自动广泛写入、session log、archive、`full` 和 `reset`。

验收：

- `python3 -m unittest discover -s skills/neat/tests -v`
- `git diff --check`
- Skill validator
- 生产验收覆盖 handoff、resume、知识治理、大文件、并发修改、回滚、幂等、secret/link/locator/temporal gate、中文路由和非 Git 项目。

## 致谢

Neat 的灵感来自 [KKKKhazix/khazix-skills/neat-freak](https://github.com/KKKKhazix/khazix-skills/tree/main/neat-freak)。0.5 保留了“整理知识、纠正文档、避免 Agent 被过期信息误导”的核心方向，同时把实现重构为更强调上下文边界、显式计划和生产安全的工作流。
