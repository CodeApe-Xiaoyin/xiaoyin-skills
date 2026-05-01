---
name: neat
description: "Project documentation, memory, checkpoint, and handoff cleanup skill for AI coding agents. Use for /neat, /neat checkpoint, /neat full, /neat reset, session handoff, context checkpointing before /clear, stale docs cleanup, and end-of-session knowledge synchronization."
---

# Neat 0.4 — Project Knowledge Handoff & Doc Sync

同步项目文档、Agent 记忆和交接状态。确保下一个 Agent 用最少的上下文接手当前进度。

---

## 核心目标

1. **知识库同步** — `CLAUDE.md`/`AGENTS.md`、`README.md`、`docs/`、Agent memory 与代码保持一致
2. **上下文接力** — 最小 token 成本生成交接快照，下一个 Agent 只需读取 CLAUDE.md + HANDOFF.md 即可接手

---

## 基本原则

| # | 原则 | 违规示例 | 正确做法 |
|---|------|---------|---------|
| 1 | 检查 ≠ 强改 | 为证明同步而改 README | 检查 → 判断 → 必要时改 → 无需改则说明 |
| 2 | 不写未验证的事实 | "测试已通过"（未运行） | 标注 `not run` |
| 3 | 不写密钥 | 写入 API Key / Token | 用 `<redacted>` 占位 |
| 4 | 不让文档变长 | HANDOFF 无限追加 | 每次重写当前状态快照 |
| 5 | 不跨分支误导 | 省略分支/commit | 必须记录 Git 上下文 |
| 6 | 不默认全读 docs | 新会话读取所有文件 | 只读 CLAUDE.md + HANDOFF.md |
| 7 | 不存储可推导的完整列表 | HANDOFF 写完整文件列表或目录树 | 只列 3-5 个关键改动文件 + 原因 |
| 8 | 稳态不进 HANDOFF | HANDOFF 重复技术栈信息 | 稳态只存 CLAUDE.md |

---

## 触发与模式映射

关键词直接映射到模式，一步到位：

```yaml
trigger_map:
  checkpoint:
    commands: ["/neat checkpoint"]
    keywords:
      zh: [checkpoint, 上下文快满了, 开新对话继续, 准备 clear, 保存进度, 额度快没了, 存档]
      en: [save progress, context full, before clear]

  handoff:
    commands: ["/neat", "/sync"]
    keywords:
      zh: [整理文档, 收尾, 这阶段做完了, handoff, 同步文档, 更新 CLAUDE.md]
      en: [sync docs, wrap up]

  full:
    commands: ["/neat full"]
    keywords:
      zh: [深度整理, 新人接手, 发版前整理, 文档乱了, 彻底梳理]
      en: [full cleanup, pre-release docs]

  reset:
    commands: ["/neat reset"]
    keywords:
      zh: [重建文档, 从零整理]
      en: [reset docs]

  fallback:
    - 关键词匹配多个模式 → 选更保守的（checkpoint < handoff < full）
    - 不确定 → handoff

  not_triggered_by:
    - 整理函数/CSS/布局/代码格式/import/组件结构
    - 除非明确提到文档/记忆/handoff/checkpoint/会话收尾
```

---

## 模式定义

```yaml
modes:
  checkpoint:
    goal: 最小成本保住开发现场
    scope: HANDOFF.md 为主，必要时更新 CLAUDE.md
    budget: HANDOFF 正文 ≤ 30 行

  handoff:
    goal: 阶段性同步项目文档和交接状态
    scope: 全部知识层评估 + SESSION_LOG
    budget: HANDOFF 正文 ≤ 60 行

  full:
    goal: 完整知识库治理
    scope: 扫描 docs/ 文件名列表，按变更影响矩阵判断哪些需要读正文，深度对齐
    budget: HANDOFF 正文 ≤ 100 行

  reset:
    goal: 归档现有 neat 产出物，从当前代码状态重新生成
    definition: reset = 归档旧文档 + 执行一次 full 模式生成新文档
    difference_from_full: full 在现有文档基础上整理；reset 先清空再重建，适用于文档已严重过期或混乱的情况
    steps:
      1: 将现有 HANDOFF.md 移动到 docs/archive/HANDOFF-YYYY-MM-DD.md
      2: 将现有 SESSION_LOG.md 移动到 docs/archive/SESSION_LOG-YYYY-MM-DD.md
      3: 在归档文件头部添加 "Status: Archived" 标记
      4: 按 full 模式流程从当前 Git 状态和代码重新生成 HANDOFF.md
      5: 检查 CLAUDE.md 是否需要更新
      6: 输出摘要说明归档了什么、重建了什么
    budget: 同 full
```

---

## 能力探测

执行前先判断平台能力，不可用时自动降级：

```yaml
capability_probe:
  git:
    check: git rev-parse --show-toplevel
    if_unavailable: 跳过 Git Context，HANDOFF 标注 "git: unavailable"
  file_write:
    if_unavailable: 只输出摘要，提示用户手动创建
  agent_memory:
    if_unavailable: 标注 "agent_memory: unavailable"
```

---

## 前置判断

```yaml
skip_check:
  if_all_true:
    - 无 git history
    - 无 docs/ 目录
    - 无 CLAUDE.md / AGENTS.md
    - 本轮改动极少（< 3 文件，< 50 行）
  then: |
    输出："当前项目状态尚早，建议积累到一定进度再执行 neat。如需执行，请再次确认。"
  override: 用户再次确认后正常执行
```

---

## 知识层同步责任

每种模式都必须给出每层状态，不能静默跳过：

| 知识层 | 有效状态值 |
|-------|-----------|
| Agent memory | `updated` / `checked-no-change` / `unavailable` |
| CLAUDE.md / AGENTS.md | `updated` / `checked-no-change` / `created` / `missing-not-needed` |
| README.md | `updated` / `checked-no-change` / `not-affected` / `missing-not-needed` |
| docs/HANDOFF.md | `updated` / `created` |
| docs/SESSION_LOG.md | `updated` / `created` / `not-needed` |
| 其他 docs/* | `updated` / `checked-no-change` / `not-affected` / `deferred:原因` |

---

## 事实核查

### Git 检查（如可用）

```bash
git rev-parse --show-toplevel   # 项目根
git branch --show-current       # 当前分支
git status --short              # 工作区状态
git log -1 --oneline            # 最新 commit
git diff --stat                 # 改动概览
```

### 证据等级

```yaml
evidence_levels:
  verified: 命令输出 | 测试结果 | 构建结果 | Git 状态 | 文件内容 | 用户确认
  inferred: 合理推断，未直接验证
  unknown: 证据不足
  rule: 不把 inferred 写成 verified
```

---

## 密钥保护

```yaml
sensitive_check:
  keywords: [sk-, Bearer, Authorization, password, passwd, secret, cookie, PRIVATE KEY, DATABASE_URL=, refresh_token, access_token, ghp_, ghu_, whsec_, xoxb-, rk-, ssm_, api_key, apikey]
  note: 不含独立的 "token"（太宽泛），具体 token 类型已由 refresh_token/access_token 覆盖，其余由 heuristic 规则捕获
  heuristic: 32+ 字符高熵随机字符串（base64/hex）出现在赋值上下文中 → 脱敏
  action: 替换为 <redacted>
  timing: 写入任何文件前扫描新内容
```

---

## 项目根目录

```yaml
root_detection:
  primary: git rev-parse --show-toplevel
  markers: [package.json, pyproject.toml, Cargo.toml, go.mod, README.md, CLAUDE.md, .git/]
  monorepo:
    markers: [pnpm-workspace.yaml, lerna.json, nx.json, turbo.json, packages/]
    rule: HANDOFF.md 放在用户当前工作的 package 根目录，不是 repo 根
  multi_project: 分别创建对应 handoff，不把 A 项目事实写进 B 项目
```

---

## HANDOFF.md 规则

```yaml
handoff_rules:
  nature: 当前状态快照，不是历史日志
  
  rewrite_strategy:
    default: 每次重写覆盖
    must_preserve:
      - "Failed — Do Not Retry" 中 Active 状态的条目（除非已标记 Resolved）
      - "Risks" 中未解决的条目
      - "<!-- manual:start/end -->" 手写区块
    continuous_checkpoint: |
      连续 checkpoint 时，新 HANDOFF 必须保留前次 checkpoint 的 Done 和 Failed 条目。
      这些条目在下一次 handoff/full 执行时才转入 SESSION_LOG。
  
  staleness_check: |
    仅在新会话启动读取 HANDOFF 时检查（不在写入时检查，写入时 commit 不一致是本轮正常开发的预期行为）。
    检查逻辑：对比 HANDOFF 的 commit 字段与当前 git log -1。
    如果不一致 → 提醒用户"HANDOFF 可能过期，代码在上次 handoff 后被修改过"。
    由新会话启动协议的步骤 4 执行。
  
  no_repeat_from: CLAUDE.md 中已有的稳态信息
  no_store_derivable:
    exclude: [完整 git log, 完整 diff 内容, 完整文件列表, 完整目录树, 大段代码]
    exception: 本轮关键修改文件（3-5 个）+ 改动原因应保留在 Modified Files 区块
  no_template_boilerplate: 不含固定指令文字（"Read This First" 等写入 CLAUDE.md）

  sync_status: |
    HANDOFF frontmatter 包含 sync_status 字段。
    写入 HANDOFF 时先设为 partial，全部知识层同步完成后改为 complete。
    下一个 Agent 如检测到 partial → 先完成同步再开始新任务。
```

---

## SESSION_LOG.md 规则

```yaml
session_log_rules:
  recent_detailed: 最近 5-10 次 session
  compress_trigger:
    - 超过 10 条详细记录 → 压缩最早 5 条为 milestone
    - 单文件超过 500 行 → 归档到 docs/archive/session-log-YYYY-MM.md
  no_repeat: 不复制 HANDOFF.md 的当前状态
  checkpoint_boundary: |
    checkpoint 不更新 SESSION_LOG。
    连续 checkpoint 的 Done/Failed 条目在 HANDOFF 中累积，
    直到下一次 handoff/full 执行时批量转入 SESSION_LOG。
```

---

## CLAUDE.md / AGENTS.md 规则

```yaml
claude_md_rules:
  inclusion_test: 跨会话复用率 > 80% 的信息才放入
  should_contain:
    - 技术栈 | 启动/构建/测试命令 | 关键目录（3-5个）
    - 项目红线 | 用户长期偏好 | 重要坑点
    - 环境变量清单（不含真实值）
    - 新会话入口 + 启动协议（见下方）
  should_not_contain:
    - 本轮细节 | 临时进度 | 已完成 todo | 长日志 | 报错全文
  line_budget:
    small: ≤ 80 行
    medium: ≤ 150 行
    large: ≤ 250 行
```

新会话启动协议（写入 CLAUDE.md 一次，不在 HANDOFF.md 重复）：

```md
## 新会话启动协议
1. 读 docs/HANDOFF.md（先看 frontmatter 的 branch 和 sync_status）
2. 如果 branch 与当前 git branch 不一致 → 告知用户 handoff 可能过期
3. 如果 sync_status 为 partial → 先完成上次未完成的同步
4. 如果 commit 与当前 git log -1 不一致 → 提醒代码可能已变更
5. 读本文件（CLAUDE.md）获取项目背景
6. 不读其他文件，直到任务需要
```

---

## README.md 规则

```yaml
readme_rules:
  modify_only_when_changed:
    - 项目用途 | 安装方式 | 启动/构建/测试命令
    - 环境变量 | 使用方式 | 部署方式 | 文档入口
  never_include:
    - 开发流水账 | AI 修改记录 | 失败尝试 | 临时 debug
```

---

## 受保护文件

```yaml
protected_files:
  list: [README.md, CHANGELOG.md, LICENSE, CONTRIBUTING.md, SECURITY.md, API docs, migration docs, deployment docs]
  rules:
    checkpoint: 除非当前变更直接影响，否则不修改
    handoff: 仅当启动/使用/API/部署/环境变量/对外行为变化时修改
    full: 可整理，但摘要必须说明改了什么、为什么
```

---

## 跨 Agent / 跨项目一致性

```yaml
cross_consistency:
  shared_facts: [package manager, 启动/构建/测试命令, 项目结构, 环境变量, coding constraints, handoff entrypoint]
  conflict_resolution:
    1: 优先相信代码、配置文件和命令输出
    2: 更新冲突双方的文件
    3: 无法判断 → 写入 HANDOFF Risks
  cross_project: 确认每条事实属于哪个项目，不混写
  skill_vs_claude_md: Skill 被显式触发时 Skill 规则优先，摘要中告知冲突
```

---

## 变更影响矩阵

| 变更类型 | 应同步文档 |
|---------|-----------|
| 新增 API/路由 | CLAUDE.md 路由清单, API docs, architecture, HANDOFF |
| 新增环境变量 | CLAUDE.md, README/runbook, deployment docs, HANDOFF |
| 修改启动命令 | README, CLAUDE.md, runbook, HANDOFF |
| 修改构建流程 | README, CI docs, deployment docs, HANDOFF |
| 新增数据库表 | architecture, migration docs, CLAUDE.md, HANDOFF |
| 新增大功能 | architecture, integration guide, HANDOFF, SESSION_LOG |
| 修复 bug | HANDOFF, SESSION_LOG; 长期约束 → CLAUDE.md |
| UI 重构 | HANDOFF, SESSION_LOG; 影响使用方式 → README |
| 部署方式变化 | README, deployment docs, runbook, HANDOFF |
| 跨项目协议变化 | 上下游项目文档都同步 |

---

## HANDOFF.md 模板

Frontmatter（YAML）：

```yaml
---
schema: neat/0.4
updated: YYYY-MM-DD HH:mm UTC+8
sync_status: complete
project: xxx
path: /path/to/project
branch: xxx
commit: abc1234
worktree: clean | dirty | unknown
source: committed | uncommitted | mixed | unknown
---
```

正文区块优先级：

```yaml
handoff_sections:
  required_all_modes: [State, Done, Pending, Validation, Next]
  required_handoff_full: [Modified Files, Risks, Failed]
  optional: [Manual Notes, Reference]
  rule: checkpoint 时只写 required_all_modes，其余按需；handoff/full 写全部 required + 按需 optional
```

正文（Markdown）：

```md
## State
一句话描述当前项目状态（必须包含项目名 + 当前功能模块，让不读 CLAUDE.md 也能大致理解上下文）

## Done
- 已完成事项（连续 checkpoint 时保留前次条目）

## Modified Files
- path/to/key-file — 一句话说明改了什么（仅列 3-5 个关键文件）

## Pending
- 未完成任务

## Risks
- 风险/阻塞/需确认事项

## Failed — Do Not Retry
- Active: 仍相关的失败尝试（重写时必须保留）
- Resolved: 已解决（下次更新时可移除）

## Validation
build: status (verified/inferred/unknown: source)
tests: status (verified/inferred/unknown: source)
typecheck: status (verified/inferred/unknown: source)
manual: status (verified/inferred/unknown: source)

## Next
1. 第一步
2. 第二步

## Manual Notes
<!-- manual:start -->
<!-- manual:end -->

## Reference (do not read unless task requires)
- docs/ARCHITECTURE.md — 改架构时读
- docs/API.md — 加接口时读
```

未知项写 `unknown`，不编造。

---

## SESSION_LOG.md 模板

```md
# Session Log

## Recent Sessions

### YYYY-MM-DD HH:mm UTC+8
goal: 本轮目标
changes: [修改项1, 修改项2]
decisions: [决策1:原因]
failed: [方案1:原因]
validation: {build: passed, tests: not run}
next: [下一步1]

## Older Milestones
- YYYY-MM — 简要摘要
```

---

## Agent Memory 原则

```yaml
agent_memory:
  should_write: [用户长期偏好, 项目特殊约束（非显而易见）, 跨会话稳定事实]
  should_not_write: [临时进度, 一次性错误, 短期分支状态, 密钥/账号, 可从文件直接读到的内容]
  if_unavailable: "agent_memory: unavailable"
```

---

## 归档与手写保护

```yaml
archive:
  location: docs/archive/
  header: "Status: Archived — Do not use as current project truth. See ../HANDOFF.md"
  default_read: false

manual_protection:
  markers: ["<!-- manual:start -->", "<!-- manual:end -->"]
  rule: 不重写/删除/移动，除非用户明确要求

style_preservation:
  existing_docs: 融入原风格（语言、标题、语气、命名、emoji）
  new_docs: 使用本 Skill 标准模板
```

---

## 可选配置

用户可在 CLAUDE.md 中自定义：

```yaml
neat:
  handoff_path: docs/HANDOFF.md     # 默认路径
  session_log: true                  # 是否生成 SESSION_LOG
  language: auto                     # auto = 跟随 CLAUDE.md 语言，否则跟随对话主语言
```

---

## 执行流程

```yaml
execution:
  1: 能力探测（Git / 文件写入 / Agent memory）
  2: 前置判断（项目是否过早，是否跳过）
  3: 确认项目根目录（含 monorepo 检测）
  4: Git 状态检查
  5: 读取现有 HANDOFF.md（检查 commit 一致性 + sync_status）
  6: 写入 HANDOFF.md（sync_status: partial）
  7: 更新 SESSION_LOG（如需要，checkpoint 跳过此步）
  8: 按变更影响矩阵更新相关 docs/*
  9: 更新 CLAUDE.md / AGENTS.md
  10: 更新 README（仅直接受影响时）
  11: 更新 Agent memory（如可用）
  12: 将 HANDOFF.md 的 sync_status 改为 complete
  13: 自检 + 输出摘要

  mode_skip:
    checkpoint: 跳过步骤 7（SESSION_LOG）、8（其他 docs）、10（README），保证 HANDOFF.md 完整即可
    handoff: 全部执行
    full: 全部执行 + full_scan_rule
  full_scan_rule: docs/ 文件多时先扫描文件名列表（ls docs/），按变更影响矩阵判断哪些需读正文
```

---

## 自检清单

### 所有模式（核心项）

```yaml
core_checklist:
  - HANDOFF.md 已创建或更新
  - 使用绝对日期和时区，无"今天/昨天/最近"等相对时间
  - 未把推断写成已验证
  - 未写入密钥
  - 未无限追加旧内容
  - 每个知识层都给出了状态
  - Failed Active 条目未被误删
  - sync_status 最终为 complete
```

### Handoff / Full 追加项

```yaml
extended_checklist:
  - SESSION_LOG 已按需更新
  - CLAUDE.md 与 AGENTS.md 共享事实不冲突
  - README 启动/安装/使用与代码一致
  - 新增 API / 环境变量 / 数据库结构已同步
  - 跨项目影响已检查
  - 归档内容已标记
  - 手写区块未被覆盖
  - 受保护文件未被无故修改
```

---

## 最终摘要格式

### Checkpoint（精简版）

```yaml
sync_complete:
  mode: checkpoint
  reason: xxx
  git: {branch: xxx, commit: xxx, worktree: clean}
  layers:
    handoff: updated
    claude_md: checked-no-change
    readme: not-affected
    memory: unavailable
  validation: {build: not run, tests: not run, typecheck: not run, manual: not done}
  risks: [如有]
  next_prompt: （输出下一轮 Prompt）
```

### Handoff / Full（标准版）

```yaml
sync_complete:
  mode: handoff | full
  reason: xxx
  git: {root: xxx, branch: xxx, commit: xxx, worktree: clean}
  memory: {status: updated, detail: xxx}
  docs_changed:
    - docs/HANDOFF.md — 更新接力状态
    - docs/SESSION_LOG.md — 追加本轮记录
  docs_checked_no_change:
    - README.md — 原因
  validation: {build: xxx, tests: xxx, typecheck: xxx, manual: xxx}
  risks: [如有]
  next_prompt: （输出下一轮 Prompt）
```

---

## Next Session Prompt

下一轮 Prompt 语言规则：跟随 CLAUDE.md 语言；如不存在，跟随本轮对话主语言。

### 标准版（checkpoint / handoff 使用）

```text
请先阅读 CLAUDE.md / AGENTS.md 和 docs/HANDOFF.md。不要修改代码。

请先总结：
1. 项目状态和 Git 信息
2. 已完成/未完成任务
3. 风险和验证状态
4. 下一步建议

如果 HANDOFF 的 commit 与当前 git log -1 不一致，先指出。
需要更多文件请先说明理由，等确认后再读。
```

### Full 版（full 模式使用）

```text
请先阅读 CLAUDE.md / AGENTS.md、README.md 和 docs/HANDOFF.md。不要修改代码。

请先总结：
1. 项目状态和 Git 信息
2. 已完成/未完成任务
3. 风险和验证状态
4. 下一步建议
5. 技术栈概况
6. 文档体系组织

如果 HANDOFF 的 commit 与当前 git log -1 不一致，先指出。
需要更多文件请先说明理由，等确认后再读。
```

### 应急版（异常中断后恢复）

```text
异常中断后恢复。不要修改代码。

先执行：
1. git branch --show-current
2. git status --short
3. git log -1 --oneline
4. 读取 docs/HANDOFF.md（如存在）

总结：当前分支、工作区状态、中断前任务、已保存改动、风险、下一步验证。
如果 HANDOFF.md 不存在，先根据 Git 状态建议创建最小 handoff。
```

### Continue Development

```text
按确认的计划继续开发。
修改前先说明文件。不擅自重构无关代码。不乱改 README/CLAUDE.md/docs。
每阶段给出改动摘要和验证状态。不确定标 unknown。
当前分支与 HANDOFF 不匹配时先停止报告。
```

### Prompt Selection

```yaml
prompt_output:
  checkpoint: 标准版
  handoff: 标准版
  full: Full 版
  emergency: 应急版
  rule: 最终摘要必须输出可复制的完整 Prompt，不要只写"请读 HANDOFF.md"
```
