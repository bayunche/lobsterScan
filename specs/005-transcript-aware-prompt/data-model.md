# Data Model: P5 — Transcript-Aware Prompt + speak/silent/done 输出契约

本期不新增持久化实体、不改 typed artifact schema。以下三个是"过程态/协议态"实体。

## 1. transcript_tail(群聊上下文片段 · 内存态,不持久化)

每次构造 agent prompt 时实时渲染,注入后即丢弃。

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| recent_speaks | list[(speaker_display, text)] | observer._recent(最近 agent.speak.text) | 取最近 K 条(K=V2_TRANSCRIPT_K,默认 8) |
| visible_artifacts | list[(artifact_id, version, producer_display, delta_summary)] | observer._artifact_log | 当前可见核心 artifact 摘要 |

**渲染规则**:
- speaker / producer 用 `AGENT_DISPLAY` 中文名(缺则 fallback「同事」,守原则 I)。
- 发言数 > K → 只取末尾 K 条(FR-003)。
- 两者皆空 → 整段省略(返回空串,FR-004)。
- 渲染或读取异常 → 返回空串(FR-016 降级)。

**校验**: 无写校验(只读渲染)。K 取值非法 → 回退默认 8。

## 2. envelope(群聊信封 · agent 输出协议态)

agent 在 envelope 模式下输出的 JSON 结构(信封外壳 + artifact 内核)。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| action | str | 是 | `speak` \| `silent` \| `done`;缺失 → 推断 speak(FR-012) |
| mentions | list[str] | speak 时 | 点名的下游 agent_id;done/silent 应为空 |
| intent | str | 否 | `propose` \| `ask` \| `review`;缺省 propose |
| reason | str | silent 时 | 沉默原因(进 AgentSilent.reason) |
| artifact | object | speak 时 | **内含各 step 现有业务产物,schema 不变**(MaterialPool / ReportCore / Outline / {script_md,slides,narrations} / …) |

**状态机(action)**:
```
speak  → unwrap artifact 回填 s.output_json → overlay emit AgentSpeak(mentions,intent)+ArtifactUpdate → 唤醒下游
silent → 不产 artifact、不点名 → emit AgentSilent(reason)
done   → 不点名(mentions 空)→ 自然走向 quiescence → gatekeeper 综合收尾(不直接结束)
```

**解析校验(_unwrap_envelope)**:
- 有 `action` 键 = 信封格式;取 `artifact`(非 dict → 空 {})。
- 无 `action` 键 = 旧格式:整体当 artifact,action=speak,mentions 取 handoff.to(向后兼容)。
- action 取值非 speak/silent/done → 当 speak(FR-012 容错)。
- 解析彻底失败(extract_json→None)→ 走既有失败/needs_retry 路径(不新增崩溃点)。

**不变量**: unwrap 后 `s.output_json` == legacy 模式下该 step 的 typed JSON(SC-005 字段级一致)。

## 3. 契约模式开关(V2_PROMPT_MODE · 配置态)

| 名称 | 类型 | 默认 | 取值 | 作用 |
|---|---|---|---|---|
| V2_PROMPT_MODE | env str | `legacy` | `legacy` \| `envelope` | legacy=今天行为;envelope=注入 transcript + 信封 rule + unwrap |
| V2_TRANSCRIPT_K | env int | `8` | ≥1 | transcript 注入的最近发言条数上限 |

- 与 `harness_version`(is_v2)**正交**:is_v2 决定走 v2 harness;V2_PROMPT_MODE 在 is_v2 下
  再分 prompt 写法。`is_v2=True + legacy` = 今天 P4 真 LLM 跑通态。
- `_envelope_enabled()` 每次读 env(支持测试 monkeypatch)。
- legacy 时:不注入 transcript、走 JSON_RULE、unwrap 对旧格式恒等 → 零回归(FR-014/SC-001)。

## 关系图

```
TaskRun(harness_version=v2) ──is_v2──> HarnessState ──observer──> _recent + _artifact_log
                                                                        │
V2_PROMPT_MODE=envelope ──> _step_prompt 注入 _transcript_block ◄───────┘
                                  │
                          agent LLM 输出 envelope
                                  │
                          _run_step: extract_json → _unwrap_envelope
                                  │
                    ┌─────────────┼──────────────┐
                speak           silent          done
              (artifact回填    (AgentSilent    (无mention→
               + overlay        reason)         quiescence→
               emit speak)                      gatekeeper)
```
