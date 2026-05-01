# Neat — AI Coding Agent 项目知识库管理 Skill

> **让 AI Agent 在多轮对话、多次会话、多人协作中不丢失项目进度。下一个 Agent 只需读取 CLAUDE.md + HANDOFF.md 即可接手。**

Neat 是一个面向 AI 编程 Agent（Claude Code、OpenAI Codex、OpenCode、OpenClaw 等）的 **Skill 插件**，用于自动同步项目文档、管理上下文交接和控制文档膨胀。

---

## 致谢 & 原始出处

本项目基于 **[KKKKhazix/khazix-skills/neat-freak](https://github.com/KKKKhazix/khazix-skills/tree/main/neat-freak)** 进行二次开发和优化。

感谢原作者的设计理念和初始实现。当前版本（0.4）在原版基础上进行了以下改进：
- 全面结构化（YAML 格式），降低 Skill 自身 token 消耗
- 触发关键词直接映射到执行模式，消除两步推理
- 新增 `reset` 模式、`sync_status` 防中断机制、连续 checkpoint 保留规则
- Next Session Prompt 从 5 个合并为 2 个，去除重复
- 新增 monorepo 支持、跨平台能力降级、并发写入检测

如需查看原版实现，请访问：**https://github.com/KKKKhazix/khazix-skills/tree/main/neat-freak**

---

## 它解决什么问题？

AI 编程 Agent 存在三个核心痛点：

| 痛点 | 表现 | Neat 的解决方式 |
|------|------|----------------|
| **上下文丢失** | 对话过长后 Agent 忘记之前做了什么 | Checkpoint 模式：用最少 token 保存当前开发现场 |
| **跨会话断档** | 新会话完全不知道上一轮进度 | HANDOFF.md：结构化交接文档，新 Agent 只读 2 个文件即可接手 |
| **文档膨胀** | CLAUDE.md / docs 越写越长越乱 | 行数预算 + 重写策略 + 自动归档 |

---

## 四种模式

### 1. Checkpoint — 轻量保存

**适用场景：** 上下文快满了、准备 `/clear`、额度不足、要开新对话

```
/neat checkpoint
```

- 只更新 `docs/HANDOFF.md`（≤ 30 行正文）
- 必要时更新 `CLAUDE.md`
- 不全量扫描 docs
- 不修改业务代码

### 2. Handoff — 阶段同步（默认模式）

**适用场景：** 一个阶段开发完成、需要整理文档、准备交接

```
/neat
```

- 评估并更新所有知识层
- 更新 `docs/HANDOFF.md`（≤ 60 行正文）
- 更新 `docs/SESSION_LOG.md`
- 按需更新 `CLAUDE.md`、`README.md`、其他 docs

### 3. Full — 深度治理

**适用场景：** 发版前整理、新人接手、文档已经混乱

```
/neat full
```

- 扫描所有 docs 文件名，按变更影响矩阵判断哪些需要读取
- 深度对齐所有文档
- 检查跨项目影响
- `HANDOFF.md` ≤ 100 行正文

### 4. Reset — 从零重建

**适用场景：** 文档体系已经乱了，需要推倒重来

```
/neat reset
```

- 将现有 `HANDOFF.md`、`SESSION_LOG.md` 归档到 `docs/archive/`
- 从当前 Git 状态和代码重新生成干净文档

---

## 触发方式

除了显式命令，Neat 也支持自然语言触发。每个关键词直接映射到对应模式：

| 模式 | 命令 | 中文关键词 | 英文关键词 |
|------|------|-----------|-----------|
| Checkpoint | `/neat checkpoint` | 保存进度、存档、上下文快满了、准备 clear、额度快没了 | save progress, before clear |
| Handoff | `/neat` `/sync` | 整理文档、收尾、这阶段做完了、同步文档 | sync docs, wrap up |
| Full | `/neat full` | 深度整理、新人接手、发版前整理、文档乱了 | full cleanup, pre-release docs |
| Reset | `/neat reset` | 重建文档、从零整理 | reset docs |

**不会触发的情况：** "整理函数"、"整理 CSS"、"整理代码格式" 等不涉及文档/记忆/会话管理的请求。

---

## 安装方式

### 方式一：curl 远程拉取（推荐）

**全局安装（所有项目可用）：**

```bash
mkdir -p ~/.claude/skills
curl -fsSL https://raw.githubusercontent.com/CodeApe-Xiaoyin/xiaoyin-skills/main/neat-freak-xy/SKILL.md \
  -o ~/.claude/skills/neat.md
```

**项目级安装（仅当前项目）：**

```bash
mkdir -p .claude/skills
curl -fsSL https://raw.githubusercontent.com/CodeApe-Xiaoyin/xiaoyin-skills/main/neat-freak-xy/SKILL.md \
  -o .claude/skills/neat.md
```

**Windows（PowerShell）：**

```powershell
# 全局安装
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\skills" | Out-Null
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/CodeApe-Xiaoyin/xiaoyin-skills/main/neat-freak-xy/SKILL.md" `
  -OutFile "$env:USERPROFILE\.claude\skills\neat.md"

# 项目级安装
New-Item -ItemType Directory -Force -Path ".claude\skills" | Out-Null
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/CodeApe-Xiaoyin/xiaoyin-skills/main/neat-freak-xy/SKILL.md" `
  -OutFile ".claude\skills\neat.md"
```

### 方式二：克隆仓库

```bash
git clone https://github.com/CodeApe-Xiaoyin/xiaoyin-skills.git
# 全局安装
mkdir -p ~/.claude/skills
cp xiaoyin-skills/neat-freak-xy/SKILL.md ~/.claude/skills/neat.md
# 或项目级安装
mkdir -p .claude/skills
cp xiaoyin-skills/neat-freak-xy/SKILL.md .claude/skills/neat.md
```

### 方式三：手动下载

直接访问 Skill 文件页面，复制内容保存为 `neat.md`：

👉 [neat-freak-xy/SKILL.md](https://github.com/CodeApe-Xiaoyin/xiaoyin-skills/blob/main/neat-freak-xy/SKILL.md)

放入：
- 全局：`~/.claude/skills/neat.md`
- 项目：`.claude/skills/neat.md`

### 安装后验证

重启 Claude Code，输入以下任意命令验证 Skill 已加载：

```
/neat
```

或直接说："整理文档" / "收尾" / "保存进度"

### 其他 Agent（Codex / OpenCode / OpenClaw 等）

将 Skill 文件内容作为系统提示词或 Agent 指令加载即可。Neat 内置了能力探测，会自动适配：
- 不支持 bash → 跳过 Git 检查
- 不支持文件写入 → 只输出摘要
- 不支持 Agent memory → 标注 unavailable

---

## 工作原理

### 执行流程

```
能力探测 → 前置判断 → 确认项目根 → Git 状态
    ↓
读取现有 HANDOFF.md（检查 commit 一致性）
    ↓
写入 HANDOFF.md (sync_status: partial)
    ↓
更新 SESSION_LOG → docs/* → CLAUDE.md → README
    ↓
更新 Agent memory → sync_status 改为 complete
    ↓
自检 → 输出摘要 + 下一轮 Prompt
```

### 知识层同步

每次执行都会对以下知识层给出明确状态，不会静默跳过：

| 知识层 | 状态值 |
|-------|--------|
| Agent memory | `updated` / `checked-no-change` / `unavailable` |
| CLAUDE.md / AGENTS.md | `updated` / `checked-no-change` / `created` |
| README.md | `updated` / `checked-no-change` / `not-affected` |
| docs/HANDOFF.md | `updated` / `created` |
| docs/SESSION_LOG.md | `updated` / `created` / `not-needed` |
| 其他 docs/* | `updated` / `checked-no-change` / `deferred:原因` |

---

## 生成的文件示例

### docs/HANDOFF.md

```yaml
---
schema: neat/0.4
updated: 2025-06-15 14:30 UTC+8
sync_status: complete
project: my-app
path: /home/user/my-app
branch: feat/user-auth
commit: a1b2c3d
worktree: clean
source: committed
---
```

```markdown
## State
用户认证模块基本完成，JWT 签发和验证已实现，中间件已挂载

## Done
- JWT 签发/验证工具函数
- auth middleware
- /api/login 和 /api/register 路由
- 密码 bcrypt 加密

## Pending
- refresh token 轮换
- 登出黑名单

## Risks
- JWT secret 硬编码在 .env.example 中，生产环境需替换

## Failed — Do Not Retry
- Active: 尝试用 cookie 存 JWT，Safari SameSite 问题无法解决，改用 Authorization header

## Validation
build: passed (verified: npm run build output clean)
tests: passed 12/12 (verified: npm test output)
typecheck: passed (verified: tsc --noEmit exit 0)
manual: not done (unknown)

## Next
1. 实现 refresh token 轮换逻辑
2. 补充登出黑名单（Redis 或内存）
3. 手动测试完整登录流程
```

---

## 核心安全机制

### 防信息丢失

- **连续 checkpoint 保留规则：** 连续多次 checkpoint 时，`Done` 和 `Failed` 条目在 HANDOFF 中累积，直到下一次 handoff/full 时才转入 SESSION_LOG
- **Failed 条目保护：** 重写 HANDOFF 时，`Active` 状态的失败记录必须保留，防止后续 Agent 重蹈覆辙

### 防中断损坏

- **sync_status 机制：** 写入 HANDOFF 时先标记 `partial`，全部同步完成后改为 `complete`。下一个 Agent 检测到 `partial` 会先完成同步

### 防文档过期

- **commit 一致性检查：** 新会话读取 HANDOFF 时，对比 `commit` 字段与当前 `git log -1`，不一致则提醒代码可能已变更

### 防密钥泄露

- 自动检测 `sk-`、`Bearer`、`ghp_`、`DATABASE_URL=`、`api_key` 等近 20 种敏感关键词（不含过于宽泛的 `token`，避免误脱敏）
- 高熵随机字符串启发式检测（32+ 字符 base64/hex 在赋值上下文中）
- 写入任何文件前自动扫描

### 防文档膨胀

| 文件 | 行数预算 |
|------|---------|
| HANDOFF（checkpoint） | ≤ 30 行 |
| HANDOFF（handoff） | ≤ 60 行 |
| HANDOFF（full） | ≤ 100 行 |
| CLAUDE.md（小项目） | ≤ 80 行 |
| CLAUDE.md（中项目） | ≤ 150 行 |
| CLAUDE.md（大项目） | ≤ 250 行 |
| SESSION_LOG | > 10 条时自动压缩，> 500 行时归档 |

---

## 新会话如何接手

Neat 执行完毕后会输出一段可复制的 **下一轮 Prompt**。在新会话中直接粘贴即可接手：

### 标准版（checkpoint / handoff / full 通用）

```
请先阅读 CLAUDE.md / AGENTS.md 和 docs/HANDOFF.md。
不要修改代码。

请先总结：
1. 项目状态和 Git 信息
2. 已完成/未完成任务
3. 风险和验证状态
4. 下一步建议

如果 HANDOFF 的 commit 与当前 git log -1 不一致，先指出。
需要更多文件请先说明理由，等确认后再读。
```

### 应急版（异常中断后恢复）

```
异常中断后恢复。不要修改代码。

先执行：
1. git branch --show-current
2. git status --short
3. git log -1 --oneline
4. 读取 docs/HANDOFF.md（如存在）

总结：当前分支、工作区状态、中断前任务、风险、下一步验证。
如果 HANDOFF.md 不存在，先根据 Git 状态建议创建最小 handoff。
```

---

## 可选配置

在 `CLAUDE.md` 中添加以下配置块即可自定义 Neat 行为：

```yaml
neat:
  handoff_path: docs/HANDOFF.md     # HANDOFF 文件路径
  session_log: true                  # 是否生成 SESSION_LOG
  language: auto                     # 输出语言（auto = 跟随项目语言）
```

---

## 与其他工具的关系

| 概念 | Neat 的定位 |
|------|------------|
| CLAUDE.md | 项目长期真相（技术栈、命令、红线）→ Neat 按需更新 |
| AGENTS.md | 跨 Agent 通用规则 → Neat 确保与 CLAUDE.md 不冲突 |
| docs/HANDOFF.md | 当前会话状态快照 → **Neat 核心产出物** |
| docs/SESSION_LOG.md | 多轮历史记录 → Neat 管理压缩和归档 |
| README.md | 面向用户的说明 → Neat 仅在启动/使用方式变化时更新 |

---

## 版本历史

| 版本 | 主要变化 |
|------|---------|
| 0.2 (原版) | 基于 khazix-skills/neat-freak 的初始实现 |
| 0.3 | 全面结构化（YAML），新增能力探测、monorepo 支持、token 预算 |
| **0.4** | 触发词→模式一步映射，删除冗余上下文阈值，Next Session Prompt 合并精简，新增 sync_status / Failed 保留 / reset 模式完整步骤，行数预算替代 token 预算，修复密钥误检测、Validation 格式统一、checkpoint 步骤明确化、HANDOFF 区块分级 |

---

## License

本项目基于 [KKKKhazix/khazix-skills/neat-freak](https://github.com/KKKKhazix/khazix-skills/tree/main/neat-freak) 二次开发。请遵循原项目的许可协议。
