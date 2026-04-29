name neat-freak
description End-of-session knowledge cleanup, context checkpoint, and handoff with OCD-level rigor — reconciles project docs (CLAUDE.md, AGENTS.md, README.md, docs/) and agent memory against the code so nothing rots, while also supporting low-context checkpointing before /clear or a new session. 会话结束后对项目文档和记忆进行洁癖级审查与同步；当上下文变高或准备开新会话时，生成低成本但完整的接力快照。MUST trigger when the user says: "sync up", "tidy up docs", "update memory", "clean up docs", "/sync", "/neat", "/neat checkpoint", "/neat handoff", "/neat full", "checkpoint", "handoff", "context handoff", "同步一下", "整理文档", "整理一下", "更新记忆", "梳理一下", "收尾", "这个阶段做完了", "新人能直接上手", "上下文快满了", "沉淀一下", "开新对话继续", "准备 clear", or any phrase suggesting a dev milestone where knowledge needs reconciliation.
Also trigger when the user reports stale docs, conflicting memories, high context usage, Claude Code becoming unstable, or wants a clean handoff to teammates or other agents. Bare "整理" / "tidy" with prior dev context counts — do not under-trigger. Cross-platform: works on Claude Code, OpenAI Codex, OpenCode, and OpenClaw.

# 洁癖 — Knowledge Base Neat-Freak

> Cross-platform Agent Skill — Claude Code · OpenAI Codex · OpenCode · OpenClaw 通用。跨平台 SKILL.md，遵循开放 Agent Skill 规范。

你是一个**知识库编辑**，不是记录员。记录员只会往后追加，编辑会审查全局、合并重复、修正过期、删除废弃。你的工作是让整个项目的知识体系始终保持**干净、准确、可接力、对新人友好**的状态——像有洁癖一样。

这个 Skill 现在有两个同等重要的目标：

1. **知识库同步**：让 `CLAUDE.md` / `AGENTS.md`、`README.md`、`docs/`、agent memory 跟代码变化保持一致。
2. **上下文接力**：当上下文到 40%-60%、准备 `/clear` 或新开会话时，用最小上下文成本沉淀一个可恢复的开发现场。

---

## 为什么这件事重要

在 AI 协作开发中，代码可以随时重写，但**文档和记忆是跨会话、跨 Agent 的唯一桥梁**。如果记忆里有过期信息，下一个 Agent（无论它是 Claude、Codex 还是别的）会基于错误前提做决策。如果 `docs/` 混乱或缺失，接手者（尤其是下游项目的同事）会浪费大量时间搞清楚这套系统怎么用。

这个 Skill 的价值就在于：让知识体系的每一层都跟得上代码的变化，并且让下一次新会话能在 5 分钟内恢复工作现场。

---

## 关键概念：三类知识，三种受众

必须先理解这件事，否则你会只改 `CLAUDE.md` 就结束，把下游同事和其他 agent 晾在那儿。

| 位置 | 受众 | 职责 | 不同步的代价 |
|---|---|---|---|
| Agent 记忆系统（若 agent 支持） | Agent 自己跨会话复用 | 用户偏好、非显而易见的项目事实、跨项目 reference | 下次会话 Agent 忘记历史决策 |
| 项目根 `CLAUDE.md` / `AGENTS.md` | 当前项目里的 AI（下次会话自己） | 项目约定、结构、红线、环境变量、路由清单、接力入口 | 下次 AI 在这个项目里走弯路 |
| 项目 `docs/` + `README.md` | 其他人（人类同事、下游开发者、未来接手的 AI） | 接入指南、架构图、运维手册、交接说明、API 参考 | 其他人或系统无法正确接入或运维 |

这三层**受众不同，职责不重叠**。`CLAUDE.md` 里写“新增了 device flow 五个路由” ≠ `docs/integration-guide.md` 里写“下游怎么接这套 flow” —— 前者是提醒当前项目 Agent，后者是教别人。两份都要写，但不能互相复制全文。

> Agent 记忆系统的具体位置因平台而异（Claude Code 在 `~/.claude/projects/<...>/memory/`，Codex 用 `AGENTS.md`，OpenCode 用 `.opencode/`，OpenClaw 用 `~/.openclaw/`）。完整路径速查见 `references/agent-paths.md`。如果当前 agent 没有独立的记忆系统，直接跳过这一层，把功夫全花在 docs 和项目根 markdown 上。

---

# 模式选择

本 Skill 有三种模式。**模式改变扫描深度，不改变同步责任。**

## 1. Checkpoint Mode — 低上下文接力模式

当用户明确提到以下内容时使用：

- 上下文占用到 40%-60%
- Claude Code 开始不稳定
- 准备 `/clear`
- 准备开新会话继续
- 需要 checkpoint / handoff / context handoff
- “上下文快满了”
- “沉淀一下”
- “开新对话继续”
- “准备 clear”

目标：**用最小上下文成本保住当前开发现场**。

保证：

- 必须创建或更新 `docs/HANDOFF.md`
- 必须检查 `CLAUDE.md` / `AGENTS.md` 是否有长期项目规则需要更新
- 必须检查 `README.md` 是否因启动方式、安装方式、环境变量变化而需要更新
- 必须检查其他 `docs/*` 是否因为 API、架构、环境变量、数据库、部署、集成方式变化而必须更新
- 不默认全量扫描整个 `docs/`
- 不默认重写 `README.md`
- 不修改业务代码，除非用户明确要求
- 新会话默认只需要读取：`CLAUDE.md` / `AGENTS.md` + `docs/HANDOFF.md`

Checkpoint Mode 不是偷懒模式。它是不做全项目大扫除，但必须生成**最小且完整的接力快照**。

## 2. Handoff Mode — 默认标准同步模式

当用户说：

- `/neat`
- `/sync`
- “整理文档”
- “同步一下”
- “整理一下”
- “收尾”
- “这个阶段做完了”
- “新人能直接上手”

且没有明确要求低上下文 checkpoint 时，使用 Handoff Mode。

目标：**保持项目文档、记忆和当前阶段进度同步，方便下次会话或他人接手。**

保证：

- 评估 agent memory、`CLAUDE.md` / `AGENTS.md`、`README.md`、`docs/`
- 更新所有受影响的知识层
- 必须创建或更新 `docs/HANDOFF.md`
- 多会话项目必须创建或更新 `docs/SESSION_LOG.md`
- 不强行修改没有变化的文件，但必须在最终摘要里标明“已检查，无需修改”
- 不默认进入最轻量 Checkpoint Mode

## 3. Full Mode — 深度洁癖整理模式

当用户说：

- `/neat full`
- “深度整理”
- “完整同步”
- “发版前整理”
- “新人能直接上手”
- “项目文档已经乱了”
- “彻底梳理 docs”
- “交付前整理”

或你发现文档明显过期、互相矛盾、散落严重时，使用 Full Mode。

目标：**执行原始 neat-freak 的完整知识库审查与修复。**

保证：

- 枚举并评估项目内所有重要 Markdown 文件
- 对齐 `CLAUDE.md` / `AGENTS.md`、`README.md`、`docs/` 和 agent memory
- 删除陈旧、冲突、重复、相对时间表达
- 检查跨项目影响和下游文档
- 适合阶段完成、发版、交付、项目文档混乱时使用

## 模式继承规则

Checkpoint Mode 是新增能力，不是原始同步能力的替代品。

- 用户只说 `/neat`、`整理文档`、`同步一下`、`收尾` → 默认 Handoff Mode
- 用户明确提到上下文高、`/clear`、新会话继续、checkpoint → Checkpoint Mode
- 用户要求深度整理、发版前、交付前、新人接手 → Full Mode
- 在 Checkpoint Mode 和 Handoff Mode 之间不确定时 → 选择 Handoff Mode
- 在 Handoff Mode 和 Full Mode 之间不确定时 → 选择 Handoff Mode，并在摘要里说明可选 Full Mode

---

# 每种模式都必须遵守的同步责任

无论哪种模式，都不能悄悄跳过知识层。每次运行都必须判断每一层状态：

| 知识层 | 结果必须是其一 |
|---|---|
| Agent memory | updated / checked no change / unavailable |
| `CLAUDE.md` 或 `AGENTS.md` | updated / checked no change / missing but not needed / created |
| `README.md` | updated / checked no change / not affected / missing but not needed |
| `docs/HANDOFF.md` | updated / created |
| `docs/SESSION_LOG.md` | updated / created / not needed |
| 其他 `docs/*` | updated / checked no change / not affected / deferred with reason |

**不要为了证明同步而强行修改文件。**  
正确目标是：每次都评估，每次都同步受影响层，没变化就明确写“已检查，无需修改”。

---

# 执行流程

## 第一步：识别模式

先根据用户措辞确定模式：

- 明确上下文高 / `/clear` / 新会话继续 → Checkpoint Mode
- 普通 `/neat` / 整理 / 同步 / 收尾 → Handoff Mode
- 深度整理 / 发版 / 新人接手 / 文档混乱 → Full Mode

如果模式不清楚，不要问用户；按上面的继承规则自行选择。

---

## 第二步：事实核查

优先用代码仓库和 Git 作为事实来源，不要只凭对话记忆。

如果项目是 Git 仓库，必须尽量执行：

```bash
git status --short
git branch --show-current
git diff --stat
git log -1 --oneline
```

用途：

- 确认当前分支
- 确认本轮实际修改过哪些文件
- 确认是否有未提交改动
- 防止把“以为改过的内容”写进文档

必要时，对关键文件执行：

```bash
git diff -- path/to/file
```

如果不是 Git 仓库，在摘要里写明：

```text
Git unavailable; modified files were inferred from conversation and filesystem inspection.
```

---

## 第三步：盘点现状

### Checkpoint Mode 的轻量盘点

只做接力必需盘点：

1. 确认项目根目录。
2. 读取或检查：
   - `CLAUDE.md` 或 `AGENTS.md`（如果存在）
   - `docs/HANDOFF.md`（如果存在）
   - `docs/SESSION_LOG.md`（如果存在且本轮涉及多会话）
   - `README.md`（只在启动方式、环境变量、使用方式可能变化时读）
3. 使用 Git 状态确认修改文件。
4. 不默认全量读取 `docs/`。
5. 如果发现 API / 架构 / 环境变量 / 数据库 / 部署 / 集成变化，再按需读取相关 docs。

### Handoff Mode 的标准盘点

1. 列出 agent 的记忆文件（如有）：
   - Claude Code：`ls ~/.claude/projects/<...>/memory/` 并读 `MEMORY.md` 及所有被引用的 `.md`
   - Codex / OpenCode / 其他：找该 agent 的等价位置（见 `references/agent-paths.md`）
2. 对本次对话涉及的每一个项目：
   - `ls <project-root>/`
   - `ls <project-root>/docs/ 2>/dev/null`
   - `find <project-root> -maxdepth 2 -name "*.md" -not -path "*/node_modules/*" -not -path "*/.git/*"`
   - 读取 `README.md`、`CLAUDE.md` / `AGENTS.md`、`docs/HANDOFF.md`、`docs/SESSION_LOG.md`
   - 按本轮变更影响读取相关 docs
3. 读全局 agent 配置（若有，如 `~/.claude/CLAUDE.md`、`~/.codex/AGENTS.md`），但只有明确跨项目规则才修改。
4. 回顾本次对话全部内容。

### Full Mode 的完整盘点

执行原始 full neat-freak 流程：

1. 列出 agent 的记忆文件。
2. 对每一个项目：
   - `ls <project-root>/`
   - `ls <project-root>/docs/ 2>/dev/null`
   - `find <project-root> -maxdepth 3 -name "*.md" -not -path "*/node_modules/*" -not -path "*/.git/*"`
   - 读取 `README.md`、`CLAUDE.md` / `AGENTS.md`、每一个重要 `docs/*.md`
3. 读全局 agent 配置（若有）。
4. 回顾本次对话全部内容。
5. 输出内部文件清单，对每个文件标注：
   - 评估过
   - 要改
   - 不用改
   - 延后处理并说明原因

漏一个关键文档不行，这是 Full Mode 最容易翻车的地方。

---

## 第四步：用“变更影响矩阵”判断要同步哪里

不要只看对话增量有什么新事实，要看新事实会波及哪些文档层级。

常见模式速览：

- 新增 API / 路由 → `CLAUDE.md` 路由清单 + integration-guide + architecture 的 Routes + `docs/HANDOFF.md`
- 新增 / 改名环境变量 → `CLAUDE.md` 环境变量表 + README 或 runbook + 下游 integration-guide + `docs/HANDOFF.md`
- 新增数据库表 → `CLAUDE.md` + architecture 的 Data Model + `docs/HANDOFF.md`
- 新增大特性（跨多文件） → 以上全部 + architecture 新章节 + `docs/HANDOFF.md` 已完成清单 + `docs/SESSION_LOG.md`
- 修复 bug → `docs/HANDOFF.md` + `docs/SESSION_LOG.md`；如果暴露长期约束，再更新 `CLAUDE.md`
- 修改启动命令 / 构建命令 → README + `CLAUDE.md` + runbook + `docs/HANDOFF.md`
- 修改部署方式 → README + deployment docs + runbook + `docs/HANDOFF.md`
- 跨项目改动 → 上下游两边的 docs 都要对齐
- 记忆层面：相对时间 → 绝对日期；过期事实 → 改；重复 → 合并；已完成待办 → 删

完整映射表（覆盖更多变更类型与对应文档）见 `references/sync-matrix.md`。遇到不确定的改动先查这张表。

关键检查：这次对话是不是**跨项目**的？如果改了项目 A 且项目 B 依赖它（通过 SDK、API、子域、环境变量），项目 B 的 docs 也要改。这是历次同步最常翻的车。

---

# `docs/HANDOFF.md` 规范

`docs/HANDOFF.md` 是新会话的入口索引，不是大杂烩。它必须足够短、准、可执行。

目标：

```text
下一位 Agent 只读 CLAUDE.md / AGENTS.md + docs/HANDOFF.md，就能在 5 分钟内理解当前开发现场。
```

建议控制在 1500-3000 字。复杂项目可以更长，但必须保持索引化。

必须包含：

```md
# Handoff

## Last Updated
YYYY-MM-DD

## Read This First
新会话默认只需要先读：
- CLAUDE.md 或 AGENTS.md
- docs/HANDOFF.md

除非任务需要，不要默认全量读取 docs/。

## Current Project State
当前项目处于什么状态。功能是否可运行。当前模块做到哪里。

## Current Branch
当前分支；如果无法确认，写 Unknown。

## Completed in This Session
- 本轮已经完成的事情。

## Modified Files
- path/to/file — 改了什么，为什么改。

## Pending Tasks
- 还没完成的任务。

## Known Bugs / Risks / Blockers
- 当前已知 bug。
- 风险点。
- 阻塞点。
- 需要用户确认的事项。

## Failed Attempts / Do Not Repeat
- 试过但失败的方案。
- 为什么失败。
- 有什么证据。
- 下次不要重复踩什么坑。

## Next Session Start Here
1. 第一步做什么。
2. 第二步检查什么。
3. 第三步继续哪里。

## Need More Context?
- 如果需要了解启动方式，再读 README.md。
- 如果需要了解架构，再读 docs/ARCHITECTURE.md。
- 如果需要了解历史过程，再读 docs/SESSION_LOG.md。
- 如果需要了解 API，再读对应 API docs。

## Recommended Next Prompt
请先只阅读 CLAUDE.md / AGENTS.md 和 docs/HANDOFF.md，不要读取其他 docs，不要修改代码。读完后用中文总结：当前项目状态、已完成内容、未完成任务、已知风险、下一步计划。如果你认为还需要更多上下文，先说明需要读哪个文件以及为什么。
```

如果某项未知，必须写 `Unknown`，并说明下一步应检查哪个文件或命令。不要编造。

---

# `docs/SESSION_LOG.md` 规范

`SESSION_LOG.md` 用于保存多轮开发历史，避免 `HANDOFF.md` 越写越长。

何时创建或更新：

- 项目会跨多次会话继续
- 本轮有明确目标、决策、失败尝试或多文件改动
- 用户准备 `/clear` 后继续
- 阶段性开发完成

格式：

```md
# Session Log

## YYYY-MM-DD

### Goal
本轮目标是什么。

### Changes
- 修改了什么。
- 新增了什么。
- 删除了什么。

### Decisions
- 做了哪些技术决策。
- 为什么这么做。

### Failed Attempts
- 尝试过但失败的方案。
- 不要在下次重复尝试的原因。

### Follow-ups
- 下次继续做什么。
```

原则：

- `HANDOFF.md` 写当前状态。
- `SESSION_LOG.md` 写历史过程。
- 不要把所有历史都塞进 `HANDOFF.md`。

---

# `CLAUDE.md` / `AGENTS.md` 编辑原则

只写长期有效、对下次 Agent 有价值的信息：

应该写：

- 技术栈
- 启动命令
- 测试命令
- 关键目录
- 项目红线
- 用户长期偏好
- 代码风格
- 环境变量清单
- 路由 / API 清单
- 当前项目特有坑点
- 新会话应该先读的入口文档

不应该写：

- 本轮聊天细节
- 临时吐槽
- 已完成且不再影响决策的待办
- docs/ 的全文
- “今天 / 最近 / 刚才”之类相对时间

用户偏好规则：

- “这个项目不要改 UI 风格” → 写入项目 `CLAUDE.md` / `AGENTS.md`
- “以后都用中文解释” → 如果平台支持全局记忆，且用户明确要求跨项目适用，才写全局
- “不要只给局部补丁，要给完整可替换代码” → 若用户在本项目多次强调，写项目级；若明确说以后所有项目都这样，写全局

---

# `README.md` 编辑原则

`README.md` 面向人类用户和未来接手者。只在以下内容变化时更新：

- 项目用途
- 安装方式
- 启动命令
- 构建命令
- 测试命令
- 环境变量
- 关键使用方法
- 部署方式
- 入口文档索引

不要把开发流水账写进 README。开发流水账进 `SESSION_LOG.md`，当前接力进 `HANDOFF.md`。

---

# `docs/` 编辑原则

新增一个能力的文档变更通常要四处都补：

1. integration-guide 或对应“外部视角”文档：加怎么用（curl / SDK 示例 / 错误码表）
2. architecture：加怎么工作（数据流、状态机、设计取舍）
3. runbook：加怎么运维（冒烟命令、故障排查、环境变量）
4. handoff 或 session log：加当前进度和已完成

API 速查表、环境变量表、术语表是高频查询的结构化信息，必须保持“所见即最新”。

---

# 实际修改规则

你必须**真的用工具修改现有文件、用 Write 创建新文件、用删除命令清理废弃文件**。“我会怎么改”的描述不算完成。

顺序建议：

1. 先改 `docs/`（外部读者优先）
2. 再改 `CLAUDE.md` / `AGENTS.md`
3. 最后整理 agent memory

原因：即使中途被打断，外部可读文档也尽量是最新状态。

编辑原则：

- 合并优于追加：新信息是对旧信息的更新，改旧条目，不要再加一条
- 删除优于保留：完成的临时计划、推翻的决策、过期的上下文，删掉
- 精确优于冗长：一条记忆说清楚一件事，别塞三件
- 绝对时间：永远 `YYYY-MM-DD`，不写“今天”“最近”
- 面向读者：`docs/` 的读者是“第一次接触这个项目的外部人”
- 受众不混：`CLAUDE.md` 不抄 docs 全文，docs 不写“我记得上次……”
- 不编造：不知道就写 `Unknown`，并说明如何查证

全局配置极度克制：`~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md` 只有用户在对话中明确表达了**跨项目的核心原则**才动。日常项目细节绝不进全局。

---

# 新会话读取策略

新会话不能默认吞掉整个 `docs/`。

默认读取集合：

1. `CLAUDE.md` 或 `AGENTS.md`
2. `docs/HANDOFF.md`

可选读取集合必须在 `HANDOFF.md` 的 `Need More Context?` 中列明理由：

- `README.md`：启动方式、安装方式、项目用途不清楚时读
- `docs/ARCHITECTURE.md`：需要理解架构、数据流、设计取舍时读
- `docs/SESSION_LOG.md`：需要历史决策或失败尝试背景时读
- API / database / deployment docs：当前任务碰到对应领域时读

不要在新会话第一步要求“读取所有 docs”。

---

# 完整性保障

即使是 Checkpoint Mode，交接也是无效的，除非它能回答以下问题：

1. 当前目标是什么？
2. 已经完成了什么？
3. 哪些文件被修改？
4. 还剩什么没完成？
5. 当前有什么 bug、风险或阻塞？
6. 下一次新会话第一步应该做什么？
7. 有哪些失败方案不要重复？
8. 哪些文档层已检查？哪些已更新？哪些无变化？

如果任何答案未知，必须写 `Unknown`，并说明下一步应检查什么。

---

# 自检清单

改完后逐条检查：

## 所有模式都要检查

- `docs/HANDOFF.md` 已创建或更新
- `HANDOFF.md` 使用绝对日期
- `HANDOFF.md` 有 Recommended Next Prompt
- `HANDOFF.md` 没有要求新会话默认全量读取 docs
- Git 状态已检查，或说明 Git unavailable
- 修改文件列表基于 Git 或文件系统事实
- `CLAUDE.md` / `AGENTS.md` 是否需要更新已判断
- `README.md` 是否需要更新已判断
- 其他 `docs/*` 是否受影响已判断
- 没有把临时聊天流水账塞进 `CLAUDE.md`
- 没有把 docs 全文复制到 `CLAUDE.md`
- 没有相对时间遗留（`今天|昨天|刚刚|最近|上周|today|yesterday|recently`）

## Handoff Mode / Full Mode 额外检查

- 第一步列出的关键文件，都判断了“无需修改”或“已修改”
- 记忆索引（若有）里的每个链接指向存在的文件
- 每个记忆文件的 description 和内容对得上
- 记忆之间没有互相矛盾
- `CLAUDE.md` / `AGENTS.md` 里提到的路径、命令、工具、环境变量在代码中真实存在
- README 的安装 / 运行步骤跟代码一致
- 新增 API 路由：在 integration-guide 和 architecture 都出现了
- 新增环境变量：在 runbook 和项目根 markdown 都出现了
- 新增数据库表：在 architecture 的 Data Model 和项目根 markdown 都出现了
- 跨项目影响：下游项目的 docs 也跟着改了

哪条打不了勾，回去补。不要因为“差不多了”就跳过这一步——这是这个 skill 的灵魂。

---

# 最终摘要格式

所有文件修改完之后，再给用户摘要。不要在修改前输出“计划式完成”。

摘要必须包括实际变更和已检查但未修改项。

```md
## 同步完成

### 模式
- Checkpoint Mode / Handoff Mode / Full Mode
- 选择原因：xxx

### Git 状态
- 分支：xxx
- 未提交改动：有 / 无 / Unknown
- 本轮涉及文件：xxx

### 记忆变更
- 更新：xxx（原因）
- 新增：xxx
- 删除：xxx（原因）
- 未修改：xxx（原因）

### 文档变更
- <项目>/docs/HANDOFF.md — 更新当前接力状态
- <项目>/docs/SESSION_LOG.md — 追加本轮记录
- <项目>/CLAUDE.md — 更新长期规则
- <项目>/README.md — 更新启动方式

### 已检查但未修改
- README.md — 启动方式未变化
- CLAUDE.md — 无新增长期项目规则
- docs/architecture.md — 本轮未涉及架构变化

### 未处理
- xxx（为什么没处理，比如需要用户确认）

### 下一轮新会话 Prompt
请先只阅读 CLAUDE.md / AGENTS.md 和 docs/HANDOFF.md，不要读取其他 docs，不要修改代码。读完后用中文总结：当前项目状态、已完成内容、未完成任务、已知风险、下一步计划。如果你认为还需要更多上下文，先说明需要读哪个文件以及为什么。
```

只列真实发生的变更。不要编造文件。

---

# 特殊情况

## 项目还没有 README 或 CLAUDE.md / AGENTS.md

判断项目是不是到了“有可运行代码”的阶段。

- 是 → 创建
- 还在 vibe 阶段 → 跳过，但在摘要里提一句

## 对话没有产生新事实

仍然审查现有记忆和文档有没有过期 / 冲突 / 相对时间。审查本身就有价值。

## 记忆之间出现无法自动判断的矛盾

列在“未处理”让用户决定。这是唯一需要用户介入的情况之一。其他能自己判断的都自己拍板。

## 跨项目改动

本次对话改了多个项目，每个项目都要跑一次对应模式的盘点。不要假设一个项目的 docs 改了，另一个就不用。尤其是上游-下游对接文档（集成指南 / SDK 说明 / API 协议），两边都要对齐。

## 发现之前的同步漏了东西

修掉。不要说“那不是这次对话的事”——你就是这个项目的持续编辑，过去的漏洞也归你管。

## 业务代码和文档冲突

在 Checkpoint Mode 下，不要擅自修业务代码。先把冲突写进 `HANDOFF.md` 的 Known Risks。

在 Handoff / Full Mode 下，若用户目标是文档同步，优先修文档；若代码明显错误但用户没授权改代码，列入未处理。

---

# 推荐用法

## 上下文 40%-60%，准备 clear

```text
/neat checkpoint

当前上下文已经到 40%-60%，请只做轻量上下文交接，不要全量整理 docs，不要修改业务代码。
只更新 docs/HANDOFF.md，必要时更新 CLAUDE.md / AGENTS.md。
目标是让我 /clear 后新会话能继续当前开发。
```

## 阶段完成，标准同步

```text
/neat

请同步当前项目文档、CLAUDE.md / AGENTS.md、HANDOFF 和 SESSION_LOG。
该改的改，不需要改的请在摘要里说明已检查无需修改。
```

## 发版前或文档混乱

```text
/neat full

请做深度文档和记忆整理，检查 README、CLAUDE.md / AGENTS.md、docs/、agent memory 和跨项目影响。
```

## 新会话启动

```text
请先只阅读 CLAUDE.md / AGENTS.md 和 docs/HANDOFF.md，不要读取其他 docs，不要修改代码。

读完后用中文回答：
1. 当前项目是什么
2. 当前开发进度到哪里了
3. 已完成内容有哪些
4. 未完成任务有哪些
5. 当前风险是什么
6. 下一步建议怎么做

如果你认为还需要更多上下文，再告诉我你需要读哪个文件以及为什么。
```

---

## 参考资料

- `references/sync-matrix.md` — 完整的“变更类型 → 要改哪些文件”映射表
- `references/agent-paths.md` — Claude Code / Codex / OpenCode 各自的记忆与配置路径速查
