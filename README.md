# Neat-Freak Enhanced

> 基于原 `khazix-skills/tree/main/neat-freak` Skill 的二次整理与增强版本。  
> 本项目不是原作者仓库，仅用于个人学习、工作流优化和上下文交接实践。

## 免责声明

本项目并非 `neat-freak` Skill 的原始作者作品，也不代表原作者立场。  
本仓库内容是在原思路基础上，围绕 **Claude Code / Codex 等 AI 编程 Agent 的上下文管理、项目文档同步、会话交接** 场景进行的二次整理、补充和优化。

如果你正在寻找原始项目，请以原作者仓库为准。

---

## 项目简介

`Neat-Freak Enhanced` 是一个面向 AI 编程 Agent 的项目知识库整理 Skill。

它的核心目标是：

- 在一次开发会话结束时，整理项目当前状态
- 同步 `CLAUDE.md` / `AGENTS.md`、`README.md`、`docs/` 等项目文档
- 在上下文占用较高时，生成轻量级交接文档
- 帮助新会话快速接手当前项目进度
- 避免 AI 在长会话后遗忘约定、重复踩坑或误读项目状态

简单来说，它不是单纯的“总结聊天记录”，而是一个用于 AI 协作开发的 **项目接力与文档同步工具**。

---

## 为什么需要这个 Skill？

在使用 Claude Code、Codex 或其他 AI 编程工具时，长会话经常会遇到这些问题：

- 上下文占用到 40%～60% 后，Agent 开始不稳定
- 前面已经讨论过的约定被遗忘
- 已经失败过的方案被重复尝试
- 项目文档和真实代码状态不一致
- 新开会话后，Agent 不知道当前项目进度
- `README.md`、`CLAUDE.md`、`docs/` 之间信息重复、过期或冲突

这个 Skill 的作用，就是在合适的时候把当前项目状态沉淀下来，让下一次新会话可以快速恢复现场。

---

## 核心能力

### 1. 项目文档同步

根据当前代码和开发进度，检查并同步：

- `CLAUDE.md`
- `AGENTS.md`
- `README.md`
- `docs/HANDOFF.md`
- `docs/SESSION_LOG.md`
- 其他相关 `docs/*` 文档
- Agent memory，若当前工具支持

它不是每次强行改所有文件，而是每次都判断：

- 哪些文档需要更新
- 哪些文档已经检查但无需修改
- 哪些信息需要延后确认
- 哪些旧内容应该删除或合并

---

### 2. 上下文接力

当 Claude Code 或其他 Agent 的上下文占用较高时，可以使用轻量接力模式，把当前工作现场沉淀到 `docs/HANDOFF.md`。

新会话默认只需要先读取：

```text
CLAUDE.md / AGENTS.md
docs/HANDOFF.md
```

这样可以避免新会话一开始就读取整个 `docs/` 目录，导致上下文被大量占用。

---

### 3. 三种运行模式

#### Checkpoint Mode

适合上下文已经偏高、准备 `/clear` 或新开会话时使用。

触发示例：

```text
/neat checkpoint
```

适合场景：

- 上下文到 40%～60%
- Agent 开始不稳定
- 准备清空会话
- 当前任务还没有完全结束
- 只想保住当前开发现场

主要输出：

- 更新 `docs/HANDOFF.md`
- 必要时更新 `CLAUDE.md` / `AGENTS.md`
- 不默认全量扫描所有 docs
- 不默认修改业务代码

---

#### Handoff Mode

默认模式，适合阶段性开发完成后的标准同步。

触发示例：

```text
/neat
```

适合场景：

- 一个阶段做完了
- 想同步当前项目进度
- 想让下一轮会话或其他 Agent 能够接手
- 希望项目文档和真实代码状态一致

主要输出：

- 更新 `docs/HANDOFF.md`
- 更新 `docs/SESSION_LOG.md`
- 检查并同步 `CLAUDE.md` / `AGENTS.md`
- 检查并同步 `README.md`
- 按需更新相关 `docs/*`

---

#### Full Mode

深度整理模式，适合发版前、交付前或文档已经混乱时使用。

触发示例：

```text
/neat full
```

适合场景：

- 项目准备交付
- 新人即将接手
- 文档已经明显过期
- 多个文档之间存在冲突
- 需要完整整理项目知识库

主要输出：

- 全面检查项目 Markdown 文档
- 对齐 README、CLAUDE.md、AGENTS.md、docs
- 删除过期、重复、冲突内容
- 检查环境变量、启动命令、API、架构说明等是否一致

---

## 推荐工作流

### 上下文偏高时

当 Claude Code 上下文占用达到 40%～60%，或者你感觉它开始不稳定时：

```text
/neat checkpoint

当前上下文已经偏高，请只做轻量上下文交接，不要全量整理 docs，不要修改业务代码。
只更新 docs/HANDOFF.md，必要时更新 CLAUDE.md / AGENTS.md。
目标是让我 /clear 后新会话能继续当前开发。
```

然后新开会话后输入：

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

### 阶段开发完成时

```text
/neat

请同步当前项目文档、CLAUDE.md / AGENTS.md、HANDOFF 和 SESSION_LOG。
该改的改，不需要改的请在摘要里说明已检查无需修改。
```

---

### 发版或交付前

```text
/neat full

请做深度文档和记忆整理，检查 README、CLAUDE.md / AGENTS.md、docs、agent memory 和跨项目影响。
```

---

## 主要文件说明

| 文件 | 作用 |
|---|---|
| `SKILL.md` | Skill 主体说明文件 |
| `README.md` | 当前项目说明文档 |
| `docs/HANDOFF.md` | 新会话接力入口 |
| `docs/SESSION_LOG.md` | 多轮开发历史记录 |
| `CLAUDE.md` | Claude Code 项目记忆与规则 |
| `AGENTS.md` | Codex / 通用 Agent 项目规则 |

---

## 设计原则

### 1. 不强行修改所有文档

每次运行都应该评估所有关键知识层，但不代表每次都必须修改所有文件。

正确行为是：

```text
检查 → 判断是否受影响 → 必要时修改 → 无需修改则说明原因
```

---

### 2. 交接文档要短而准

`docs/HANDOFF.md` 不应该变成流水账。

它应该回答：

- 当前目标是什么
- 已经完成了什么
- 哪些文件被修改
- 还剩什么没完成
- 当前有什么风险
- 下次会话从哪里开始
- 哪些失败方案不要重复

---

### 3. 新会话不要默认读取所有 docs

默认只读：

```text
CLAUDE.md / AGENTS.md
docs/HANDOFF.md
```

其他文件按需读取。

这样既能保证接续准确，又不会在新会话刚开始就消耗大量上下文。

---

### 4. 事实优先于记忆

如果项目是 Git 仓库，应优先使用：

```bash
git status --short
git branch --show-current
git diff --stat
git log -1 --oneline
```

用 Git 状态确认真实修改，而不是只依赖聊天记忆。

---

## 适用工具

理论上适用于：

- Claude Code
- OpenAI Codex
- OpenCode
- OpenClaw
- 其他支持 Skill / AGENTS.md / 项目规则文件的 AI 编程 Agent

不同工具的记忆文件和项目规则文件路径可能不同，请根据实际环境调整。

---

## 与原版的关系

本版本基于原 `neat-freak` 的思路进行二次整理，主要增强点包括：

- 增加 `Checkpoint Mode`
- 增加 `Handoff Mode`
- 增加 `Full Mode`
- 强化 `docs/HANDOFF.md` 作为新会话入口
- 增加新会话读取策略
- 增加 Git 状态核查
- 增加同步责任清单
- 增加“已检查但未修改”的摘要要求
- 避免新会话默认全量读取 docs

再次说明：本项目不是原作者发布的官方版本。

---

## 使用建议

如果你只是日常开发，不建议频繁使用 Full Mode。

推荐节奏：

```text
上下文偏高 → /neat checkpoint
阶段完成 → /neat
发版交付 → /neat full
```

这样既能保持项目文档同步，又不会因为过度整理占用大量上下文。

---

## License

请根据原项目许可证和你自己的使用场景决定如何发布。  
如果你基于原作者内容进行二次分发，建议保留原作者署名、原项目链接和许可证说明。
