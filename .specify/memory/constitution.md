<!--
Sync Impact Report — Constitution Amendment
============================================
Version change: 1.0.0 → 1.1.0 (MINOR — 扩展原则 IV 的边界，新增受限 LLM 例外)
Ratification date: 2026-05-28 (unchanged)
Last amended: 2026-05-30

Modified principles:
- IV. Coordinator 与 Reviewer 的职责边界 — 新增「drift 判断的受限 LLM 例外」子条款：
  放宽 docs §9.4.7 决策 1 隐含的「Coordinator 是纯规则引擎（无 LLM）」约束，
  允许 observer 的「跑题 drift 判断」调用一次受限 LLM（minimal context / 只发声 /
  不路由 / 不审质量 / 不改产物）。原则 IV 其余红线（不路由 next-speaker、不审内容
  质量、不重写产物）全部保留；Coordinator 的路由/兜底/收尾 gatekeeper 仍是纯规则。

Added sections: 无（在既有原则 IV 内扩展子条款）
Removed sections: 无

Templates / docs requiring updates:
- ✅ .specify/templates/plan-template.md — Constitution Check 为通用占位，无需改
- ✅ .specify/templates/spec-template.md — 无 mandatory section 增删，无需改
- ✅ .specify/templates/tasks-template.md — 无 principle-driven 任务类型增删，无需改
- ✅ docs/开发文档.md §9.4.7 决策 1 — 由本宪章在 P3 显式放宽（引用本修订）；
     正文为设计规范，trust-the-code 原则下不强制同步改写，已在此 Report 记录差异
- ✅ specs/003-coordinator-transform/ — plan.md / spec.md / tasks.md 已预置 Phase 0
     宪章修订前置（drift/US4 阻塞解除即依此修订）

Follow-up TODOs: 无
-->

# lobsterScan / 会汇报 Constitution

> 展示名：**会汇报** · 底座：**OpenClaw 多 Agent 集群** · 面向：基层主管 / 项目小负责人
>
> 本宪章统辖整个 monorepo（`web-frontend` / `web-backend` / `admin-frontend` /
> `admin-backend` / `packages/ui` / agent prompts / skills）。它**不是设计文档**，
> 而是「不可妥协的底线」。详细设计走 `docs/开发文档.md`；架构演进按 §9.4 路线图。

---

## Core Principles

### I. 用户可见层脱敏（NON-NEGOTIABLE）

任何**用户可见**的文本、界面元素、错误提示中，**绝不**出现技术标识符：
- 禁词：`task_id` / `agent_id` / `run_id` / 原始 error code / stack trace / 路径片段
- UI 必须把 agent 渲染为有中文展示名的「同事」（`AGENT_DISPLAY` / `AGENT_AVATAR`）
- 错误一律翻译为业务化中文（例：`生成时间较长，请稍后重试…`）
- 原始 ID / 错误码只允许进 `data/.logs/`、`events.jsonl`、admin 控制台

违反这一条等于产品事故。任何 PR 引入新的用户可见字符串都必须在 review 时被这条扫一遍。

### II. 中文为产品语言

- agent 显示名、聊天气泡、导出文件名（`<标题>_YYYYMMDD.<ext>`）、用户可见错误：**全部中文**
- 代码标识符、英文注释、变量名：可英文（贴近原意优先）
- 控制台日志、内部事件：可中英混

### III. 降级而非崩溃（Graceful Degradation）

子组件可以失败，整任务**绝不**因此 `failed`。
- 视频生成失败 → 状态 `partial`，前端给可重试入口，HTML + 讲稿 + 摘要 + 审校照常交付
- material 解析失败 → 落回 LLM 兜底，提示用户「建议复制核心内容到文本框」
- 任何下游 agent 缺数据 → 走 `needs_help`，由 Coordinator 在群里协调，不让管线挂死

### IV. Coordinator 与 Reviewer 的职责边界（架构红线）

源自 `docs/开发文档.md` §4.1 / §4.9 / §9.4.7，**已拍板不可漂移**：

**Coordinator 只做两件事**：
1. **流程纠偏**（observer）— 死循环 / 停滞 / 跑题 drift / 预算逼近时在群里发声
2. **输出管控**（gatekeeper）— task 收尾时校验 artifact 完整性 + Reviewer verdict；决定 `task.end` 状态码

Coordinator **不做的事**（红线）：
- 不路由 next-speaker（让 agent 自由 `@`）
- 不审查内容质量（Reviewer 的活）
- 不重写 / 不接管任何 agent 的产物

**Reviewer 只做两件事**：
1. **实际输出质量验证**（PRD §10.8 + humanizer + artifact 自洽 + `supplement` 落地）
2. **流程逻辑验证**（依赖图 / 版本一致 / agent 参与度 / 跨引用一致）

Reviewer **不做的事**（红线）：
- 不直接 `@` 其他 agent（`verdict.fail` 由 Coordinator 转写）
- 不重写产物（只给建议，重写由对应 agent 自己做）

**drift 判断的受限 LLM 例外（v1.1.0 修订，自 P3 起生效）**：

`docs/开发文档.md` §9.4.7 决策 1 原定「Coordinator 是**纯**规则引擎（无 LLM）」。
自 P3（`specs/003-coordinator-transform/`）起，本宪章**放宽**该约束的唯一一处：
observer 的**跑题 drift 判断**（原则 IV 已列为 Coordinator 合法的「流程纠偏」职责）
允许调用**一次受限 LLM**，但 MUST 严格满足以下全部限定：

- **输入受限**：仅「原始汇报目标 + 最近 K 条发言」（minimal context），**不**喂全量 transcript
- **输出受限**：仅「是否跑题（布尔）+ 复诵原始目标的文案」，无其他指令
- **行为受限**：仍守原则 IV 全部红线 —— **不**路由 next-speaker、**不**审内容质量
  （Reviewer 的活）、**不**重写 / 不接管任何 agent 的产物
- **范围受限**：仅 drift 这一项语义判断破例用 LLM；Coordinator 的**路由 / 兜底 /
  收尾 gatekeeper / stagnation 检测**逻辑仍是**纯规则**（无 LLM）
- **降级**：drift LLM 调用失败 / 超时 MUST 跳过本次判断（仅 log），不得让任务 `failed`（原则 III）

Coordinator 的「主体规则引擎」定位不变；本例外不构成「Coordinator 全面 LLM 化」，
不得据此引申到路由 / 收尾 / 兜底等其他职责。真 LLM 实现受运行环境约束
（见 `docs/issues/windows-real-pipeline-runnability.md`），可先以可注入 mock 形态落地接口。

### V. Agent 自治与隔离

- Agent 进程模型：`openclaw agent --local --json` **subprocess-per-turn**，一次一回合（详 §9.4.7 决策 2）
- 每个 agent 独立 `agentDir`（`~/.openclaw/agents/<agent-id>`）+ 独立 workspace（`openclaw/workspaces/<agent-id>`）
- **绝不共享 agentDir**（auth / session crosstalk → 安全事故）
- Agent 之间只通过事件总线 + 4 个 typed versioned artifact 通信：
  `MaterialPool` / `ReportCore` / `Outline` / `Script`（详 §9.4.7 决策 3）
- 保留 `agent_id` 与角色 1:1 绑定（9 位天然分工），去掉 `STEP_TO_AGENT` 强映射（详 §9.4.7 决策 4）

---

## 工程约束（Technical Constraints）

- **LLM JSON 输出不可靠**：`apps/web-backend/app/openclaw/client.py` 的 `extract_json()` / `_fix_inner_quotes`
  容错链是必需品。**不要"简化"这个解析器**（CLAUDE.md 原话）。
- **CORS allowlist 必须双写**：同时包含 `localhost` 与 `127.0.0.1`（两个 `main.py` 各自维护）。
- **Secrets 单源**：`admin-backend` 是 secrets 唯一持久化位置（`data/admin.db` Fernet 加密）；
  `web-backend` 通过 `GET /admin/api/secrets/internal/values`（30s 缓存）拉明文，自己**绝不存** API key。
- **设计语言 SSOT**：`packages/ui/tokens.css`（Memory Glass：翡翠玻璃材质 + 亮/暗双主题）是所有视觉的源头。
  既有 styled-jsx 与 Tailwind 兼容层都引用其中的 CSS 变量，避免硬编码颜色 / 圆角 / 阴影。
- **文档与代码不一致时**：**trust the code, not the docs**。`docs/开发文档.md` 与 `README.md` 是设计规范，
  几处已与实现偏离（详 CLAUDE.md §"⚠️ Docs describe the plan"）。

---

## 开发流程（Development Workflow）

### Spec-Driven Development（spec-kit）

非 trivial 的新 feature / 架构演进按以下循环：

1. `/speckit-constitution` — 复诵 / 修订宪章（如本次架构定调）
2. `/speckit-specify` — 写 spec（**关注 what + why**，不写技术栈）
3. `/speckit-clarify`（可选）— 解决 spec 里的歧义
4. `/speckit-plan` — 出技术实现方案（技术栈、依赖、关键接口）
5. `/speckit-tasks` — 拆 plan 为可执行任务清单
6. `/speckit-analyze`（可选）— 跨 artifact 一致性体检
7. `/speckit-implement` — 执行

### v2 群聊化 Harness 演进

按 `docs/开发文档.md` §9.4 路线图分阶段（P1 协议+状态 → P8 运营兜底），**v1 / v2 双轨并存**：
- 现有任务走 v1 串行管线（已落地）
- 新功能 / 实验任务可走 v2 群聊路径（feature flag 路由）
- 完整切换不早于 P5 完成（8 个 step prompt 全部重写为 transcript-aware）

### 改动准入

- 用户可见字符串改动 → 必须过原则 I 与 II
- harness / pipeline / coordinator / reviewer 改动 → 必须过原则 IV
- agent 进程、agentDir、secrets 改动 → 必须过原则 V
- 任何会让任务 `failed` 状态码增加的改动 → 必须过原则 III（设计降级路径）

---

## Governance

- 本宪章**凌驾于所有局部决定**之上。某 PR 与宪章某条原则冲突时，优先级是：**宪章 > 设计文档 > 代码现状**。
- 宪章修订只能通过显式 PR + 提升版本号，commit 信息中标注 `constitution: vX.Y.Z → vA.B.C` 与变更原因。
- §9.4.7 架构定调的 5 条（决策 1-5）属于**强约束**，改动前必须先升级本宪章的对应章节。
- 所有 PR review 都应回到本文件检查这五条核心原则；CI 暂未拦截，靠 reviewer 把关。
- 灰度策略：v1/v2 双轨期，任何 v2-only 行为必须有回退到 v1 的开关。

---

**Version**: 1.1.0 | **Ratified**: 2026-05-28 | **Last Amended**: 2026-05-30

<!-- 本宪章自 docs/开发文档.md v1.1 §9.4 与 CLAUDE.md 的约定整理而来。 -->
