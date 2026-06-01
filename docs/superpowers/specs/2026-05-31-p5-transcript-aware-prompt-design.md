# P5 — Transcript-Aware Prompt + speak/silent/done 输出契约 · 设计

> 设计日期:2026-05-31
> 状态:已与用户敲定 6 项决策,待 specify→plan→tasks
> 前置:P1-P4 均已合 main;真 LLM 链路已跑通(task `tsk_dd6e7e90c1d1` status=partial,8 step 全 done)
> roadmap 出处:`docs/开发文档.md` §9.4.5 P5 行

## 1. 目标

把 8 个 step 的 prompt 从"孤立串行"升级为"群聊感知",并把 agent 输出从 v1 的
"prose + JSON(含 handoff)"改为群聊信封 `speak / silent / done`。

两件可分离但本期一起做的事:
- **A · transcript 感知**:每个 agent 的 prompt 注入 `transcript_tail`(最近 K 条群聊发言
  原文 + 当前可见 artifact 的 delta_summary),让 agent 互相感知,为 P8 的 yes-man 防御 /
  角色对立铺路。这是"从串行管线 → 群聊"的真正质变。
- **B · 输出契约改造**:输出从 prose+JSON handoff 改为信封 `{action, mentions, intent,
  reason, artifact}`,`action ∈ {speak, silent, done}`。

非目标(明确不做,留后续阶段):
- rolling summary / transcript 压缩 → **P8**
- EventBus fan-out 并发 → **P6**
- `@` 高亮 / silent 灰显气泡等 UX → **P7**

## 2. 已敲定决策(与用户确认)

| # | 决策点 | 选择 |
|---|---|---|
| 1 | P5 范围 | A + B 都做 |
| 2 | 回归安全策略 | 新旧解析器并存 + env flag `V2_PROMPT_MODE`(下沉为设计推导结论) |
| 3 | transcript_tail 内容 | 最近 K 条发言原文 + 当前可见 artifact 的 delta_summary |
| 4 | 契约结构 | 信封包 typed payload(typed schema 原封不动) |
| 5 | 验收基线 | ScriptedBackend 测试级全绿 + 1 次真 LLM 端到端 |
| 6 | 压缩 | 不做,留 P8 |

## 3. 输出契约(信封包 typed payload)

agent 输出仍是「思考过程(prose)+ 一段 ```json``` 代码块」两段式(prose 段保留 —— 真 LLM
实测它能提升 JSON 质量,见 JSON_RULE)。JSON 段从 v1 的 typed+handoff 改为信封:

```json
{
  "action": "speak",                 // speak | silent | done
  "mentions": ["copywriter"],        // speak 时点名下游(替代 v1 handoff.to)
  "intent": "propose",               // propose | ask | review(群聊语气)
  "reason": "",                      // silent 时说明为何不开口;speak/done 可空
  "artifact": { /* 原 typed payload 原样 */ }
}
```

语义映射(与现有事件 + work-driver 对齐):
- `action=speak` → 产出 artifact + 点名下游;对应今天 `_emit_v2_step_overlay` 的 AgentSpeak+ArtifactUpdate。
- `action=silent` → 依赖未就绪 / 无话可说;对应现有 `AgentSilent`(reason 带过去)。
- `action=done` → 本角色认为整体可收尾;对应 v1 `handoff.to=DONE`,交 observer gatekeeper 综合判断。

**关键不变量**:`artifact` 子对象 = 今天各 step 的 typed JSON(`script_md`/`slides`/
`narrations`/MaterialPool…),schema **零改动**。解析后 unwrap 取出 `artifact` 喂回
`s.output_json`,**pipeline 后续处理 + 4 核心 artifact 抽取 + 导出全部零改动**。

## 4. 架构改动点(全部 is_v2 / flag 守红线)

### 4.1 transcript_tail 渲染(新增,A)
- 新增 `_transcript_block(state, k) -> str`:从 `HarnessState` 取最近 K 条 v2 事件
  (agent.speak 文本 + artifact.update 的 delta_summary),渲染成中文群聊片段塞进 prompt。
- 数据来源:复用 observer 已有的 `_recent`(agent.speak 收集)+ `_artifact_log`
  (artifact.update 收集),或新建轻量 transcript 收集器挂 bus。**复用优先**,避免重复订阅。
- K 默认 8,env `V2_TRANSCRIPT_K` 可配。
- 注入位置:`_build_global_ctx` 之后、各 step body 之前(所有 8 个 prompt 统一)。

### 4.2 输出契约 + 双解析器(B,回归安全网)
- 新增 `_ENVELOPE_RULE`(替代 JSON_RULE 的信封版),含 action/mentions/intent/artifact 说明。
- prompt builder 按 flag 选 `JSON_RULE`(v1)或 `_ENVELOPE_RULE`(v2 prompt mode)。
- 解析(`_run_step` line ~2367):`extract_json` 后新增 `_unwrap_envelope(parsed) -> (action, mentions, intent, reason, artifact)`:
  - 信封格式(有 `action` 键)→ 取 `artifact` 回填 `s.output_json`;action/mentions 驱动 overlay。
  - 旧格式(无 `action`,有 typed 字段 / handoff)→ 原样,action 推断为 speak、mentions=handoff.to。
  - **双向兼容** = 一键回退:flag 关 → 旧 prompt + 旧解析路径,完全等价今天的行为。
- `_emit_v2_step_overlay` 改造:mentions/intent 来自信封(而非从 handoff 合成);silent/done
  分支按 action 走。**overlay 不拆除**(它仍是 artifact 版本化 + 事件 emit 的执行点),
  只是输入来源从"合成"变"信封直读"。

### 4.3 env flag
- `V2_PROMPT_MODE`(默认 `legacy`;`envelope` 开启信封契约 + transcript 注入)。
- 守红线:`legacy` 模式下 P5 全部新代码短路,等价今天行为(同 P1-P4 的 is_v2 双轨先例)。

## 5. 回归策略(决策 #2 推导结论)

新旧解析器并存 + `V2_PROMPT_MODE` flag。理由:
- 输出契约是真 LLM 最敏感区(LLM JSON 不可靠,刚跳通的链路脆弱)。
- 双轨是项目已验证的模式(is_v2 贯穿 P1-P4)。
- 回退成本 = 改 1 个 env,不丢任何已跑通的能力。
- 代价:一段双轨代码;P5 收尾或 P6 起点可评估拆除 legacy。

## 6. 测试计划(验收基线 = 测试级绿 + 1 次真 LLM)

- 单元:`_unwrap_envelope` 双格式(信封 / 旧格式 / 畸形降级)、`_transcript_block` 渲染、
  flag 切换两条路径。
- ScriptedBackend e2e:envelope 模式跑 8 step 链式闭环(同 test_v2_workdriver 风格),
  断言 artifact 正确 unwrap、mentions 驱动下游、done→gatekeeper 收尾。
- v1 零回归:flag=legacy 时 98 tests 全绿 + 字段级与 main 一致。
- 真 LLM:`V2_PROMPT_MODE=envelope` 跑 1 次 `tsk_*` 端到端(deepseek 已通),
  断言 status∈{done,partial} + 8 step 全 done + 无 KeyError / 解析失败。

## 7. 风险

| 风险 | 对策 |
|---|---|
| 真 LLM 不遵守信封格式(漏 action / artifact 嵌套错) | `_unwrap_envelope` 容错 + 回退旧格式推断;extract_json 现有容错复用 |
| transcript 注入撑大 token | K 默认 8 + 可配;不喂全量(压缩留 P8) |
| 改坏刚跳通的真 LLM 链路 | flag 默认 legacy;真 LLM 验证用独立 task,出问题一键回退 |
| 8 个 prompt 改造量大 | 信封 + transcript 是共享 block(_ENVELOPE_RULE / _transcript_block),非逐个重写 step 业务逻辑 |

## 8. 不做 / 边界

- 不动 typed artifact schema(信封只是外壳)。
- 不动 observer / reviewer / subscription 的决策逻辑(它们消费的事件类型不变)。
- 不做并发 / 压缩 / UX(P6/P7/P8)。
- 宪章:无需修订(transcript-aware 是 prompt 工程,不引入新 LLM 决策权;Coordinator 仍规则引擎)。
