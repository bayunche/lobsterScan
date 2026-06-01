# Research: P5 — Transcript-Aware Prompt + speak/silent/done 输出契约

Phase 0 技术决策。spec 已无 NEEDS_CLARIFICATION(6 项已 brainstorm 敲定);本文记录
实现层 5 个技术决策 + 派生发现。

## 决策 1 · transcript 数据源:复用 observer 的 _recent + _artifact_log

- **Decision**: `_transcript_block` 从 `HarnessState.observer` 的 `_recent`(最近 agent.speak
  文本,P3 已收集,见 coordinator_observer.py:191 `_collect_speak`)+ `_artifact_log`
  (artifact.update 时序,P4 已收集,coordinator_observer.py:202 `_collect_artifact`)读取。
- **Rationale**: FR-005 明确"不新增重复订阅源"。observer 已挂 bus 收这两类事件;P5 复用即可。
  observer 在 is_v2 路径必然存在(run_harness 构造)。
- **Alternatives**: ①新建独立 transcript 收集器挂 bus —— 重复订阅,违 FR-005;②从
  events.jsonl 读 —— 磁盘 IO + 解析开销,且 emit_v2 写盘与内存不同步窗口。均否。
- **降级**: observer 为 None(理论上 v2 必有,但防御)或读取异常 → transcript_block 返回空串
  (FR-004/016),prompt 照常构造。

## 决策 2 · 信封解析:_unwrap_envelope 双格式 + 容错降级

- **Decision**: `extract_json(res.text)` 得到 dict 后,`_unwrap_envelope(parsed)` 判定:
  - 有 `action` 键 → 信封格式:取 `artifact` 回填 `s.output_json`;返回 (action, mentions,
    intent, reason)。`artifact` 缺失/非 dict → 视作空产物 + action 保留。
  - 无 `action` 键 → 旧格式:整个 parsed 作 artifact(`s.output_json`),action 推断为
    `speak`,mentions 取 `parsed.get("handoff",{}).get("to")`(兼容 v1)。
  - parsed 为 None(解析彻底失败)→ action=speak、artifact={}、走既有 needs_retry/失败路径。
- **Rationale**: FR-008/012/016。LLM 不保证遵守信封;双格式 + 推断让 envelope 模式下旧格式
  输出也不挂(渐进迁移),同时复用宪章工程约束要求的 `extract_json` 容错链。
- **Alternatives**: 严格信封校验(畸形即 fail)—— 违 FR-016 + 宪章原则 III,否。
- **派生发现**: 现有 `_run_step` 有 needs_retry/needs_help 信号读取(line ~2396 从
  `payload`/`output_json` 取)。信封模式下这些信号应从 unwrap 后的 artifact 里读 —— unwrap
  把 artifact 回填到 `s.output_json` 后,既有信号读取逻辑**无需改**(它读 s.output_json)。

## 决策 3 · flag 读取位置:与既有 V2_* 常量同风格

- **Decision**: `V2_PROMPT_MODE = os.environ.get("V2_PROMPT_MODE", "legacy")`,放在 pipeline.py
  顶部(或复用 subscription.py 的 env 常量区,与 V2_MENTION_LIMIT/V2_LOCK_WAIT_SEC 并列)。
  `V2_TRANSCRIPT_K = int(os.environ.get("V2_TRANSCRIPT_K", "8"))`。
  helper `_envelope_enabled() -> bool` 每次读(支持测试 monkeypatch,同 V2_LOCK_WAIT_SEC 模式)。
- **Rationale**: 项目既有 env flag 惯例(subscription.py:61-63)。每次读而非模块级固化,
  便于测试在 envelope/legacy 间切换(同 P2 V2_LOCK_WAIT_SEC monkeypatch 先例)。
- **Alternatives**: TaskRun 字段 / openclaw.json 配置 —— 前者要改请求契约,后者读盘开销,
  且 flag 是研发/灰度开关非业务参数。否。
- **派生发现**: `harness_version`(TaskRun 已有 v1/v2 字段)控制 is_v2;V2_PROMPT_MODE 是
  **正交**的第二维(is_v2=True 下再分 legacy/envelope prompt)。legacy prompt 模式 + is_v2
  = 今天 P4 真 LLM 跑通的状态。

## 决策 4 · transcript 渲染格式:中文群聊片段

- **Decision**: 渲染成中文段落,例:
  ```
  # 群聊上下文(最近发言)
  - 资料员:✓ 素材池就绪:完成 3 项工作、2 项下一步
  - 分析师:✓ 一句话总结:本周完成 A 客户续约…
  # 当前可见产物
  - ReportCore v1(分析师):point_extraction 产物
  ```
  发言人用 AGENT_DISPLAY 中文名(原则 I/II:prompt 内部其实可英文,但中文更贴产品语境且
  与 agent SOUL 一致)。
- **Rationale**: FR-001/002。中文 + 显示名让 LLM 以"群聊同事"语境理解上下文,贴合 P8 角色对立铺垫。
- **Alternatives**: JSON dump transcript —— 占 token 且不利 LLM 阅读语境。否。

## 决策 5 · done 语义落点:不强制结束,交 gatekeeper

- **Decision**: `action=done` → overlay 不点名下游、不产 artifact(若该 step 仍产了 artifact
  则照常版本化),仅作信号。实际收尾仍由 observer `_on_quiescence` 的 gatekeeper 综合
  (artifact 完整性 + reviewer verdict)判定(P3/P4 已实现)。done 不直接 set state.done。
- **Rationale**: FR-011 + 宪章原则 IV(收尾管控是 Coordinator gatekeeper 的活,agent 无权
  单方结束)。done 只是"我认为可收尾"的表态。
- **Alternatives**: done 直接结束任务 —— 越过 gatekeeper,违原则 IV。否。
- **派生发现**: 现有 overlay 用 handoff.to=="DONE" 时 mentions 为空(pipeline.py:1836)。
  envelope 的 done 等价该路径:mentions 空 → 无下游唤醒 → 自然走向 quiescence → gatekeeper
  收尾。语义天然对齐,实现成本低。

## 综合:改动面与零回归保证

- 改动集中在 pipeline.py 的 3 个函数(_step_prompt / _run_step / _emit_v2_step_overlay)
  + 2 个新 helper(_transcript_block / _unwrap_envelope)+ 1 个 rule 常量(_ENVELOPE_RULE)
  + flag 常量。
- legacy 模式:`_envelope_enabled()=False` → _step_prompt 走 JSON_RULE 老路 + 不注入
  transcript;_run_step 的 unwrap 对旧格式是恒等(action 推断 speak、artifact=整体),
  与今天 `s.output_json = extract_json(...)` 字段级等价。→ SC-001 零回归。
- 不动:typed artifact schema、observer/reviewer/subscription 决策、4 artifact 通信、
  导出、_persist_step。
