# Skill 接入清单

> 配套：`docs/开发文档.md` §5
> 维护：每次新增/升级 Skill，更新本文档与 `skills/third-party/.versions.lock`。

## 一、第三方 Skill

### 1. garden-skills（ConardLi/garden-skills）

| Skill | 路径 | 接入 Agent | 触发场景 | 关键产物 |
| --- | --- | --- | --- | --- |
| `web-video-presentation` | `skills/web-video-presentation/` | `html-designer` / `video-producer` | 把讲稿+大纲变成可投屏、可录制的 16:9 演示工程 | `outputs/<task>/web-presentation/` (Vite + React)；`narrations.ts` 是 TTS / 步数 SSOT |
| `web-design-engineer` | `skills/web-design-engineer/` | `html-designer` | 设计 token、反 AI 套配色、6 步流程；做 v0 → 全量构建 | 设计系统声明 + HTML/JSX 组件 |
| `gpt-image-2` | `skills/gpt-image-2/` | `html-designer` | 封面、信息图、海报、人像底图 | `garden-gpt-image-2/prompt/*.md`、（Mode A）`/image/*.png` |
| `kb-retriever` | `skills/kb-retriever/` | `material` | 多文件素材池中检索字段 | 引用来源 + 抽取片段 |

> 安装：以 git submodule 方式纳入 `skills/third-party/garden-skills`，再由 `scripts/install-skills.sh` 复制/软链到对应 Agent 的 `.agents/skills/`。

### 2. humanizer（blader/humanizer v2.5.1）
- 路径：`skills/third-party/humanizer/`
- 接入 Agent：`upward-opt`（第二轮重写）、`reviewer`（只检测不重写）
- 28 类 AI 套话识别 + "还像不像 AI"自审 + voice mimic
- 在 `upward-opt/.agents/skills/humanizer/SKILL.md` 旁追加 `CHINESE-AI-PHRASES.md`（赋能 / 抓手 / 闭环 / 链路 / 落地 / 沉淀 / 心智 …）作为 voice extension

### 3. HeyGen Skills（heygen-com/skills）
| Skill | 接入 Agent | 用途 |
| --- | --- | --- |
| `heygen-avatar` | `video-producer` | 上传/选择头像→生成 `AVATAR-<NAME>.md` 持久身份 |
| `heygen-video` | `video-producer` | 讲稿 + 头像 + voice → MP4 + share link |
| `heygen-translate` | `video-producer`（V2） | 多语言配音 / 唇形同步 |

- 鉴权优先级：`HEYGEN_API_KEY` env → MCP OAuth → 浏览器登录
- `AVATAR-*.md` 写到 `workspaces/video-producer/avatars/`，管理平台可上传/选择

### 4. Claude Code Video Toolkit（digitalsamba/claude-code-video-toolkit）
- 接入 Agent：`video-producer`（自托管路径）
- 包含 10 个子 skill：`remotion` / `elevenlabs` / `ffmpeg` / `playwright-recording` / `frontend-design` / `qwen-edit` / `acestep` / `ltx2` / `moviepy` / `runpod`
- 我们用到的核心命令：
  - `voiceover.py`（ElevenLabs 或 Qwen3-TTS）
  - `sadtalker.py`（数字人头部画面）
  - `playwright-recording`（录制 `?auto=1` web-presentation）
  - `ffmpeg`（合成 + 字幕）
  - `addmusic.py` / `redub.py`（V1.0）
- 部署：Modal（$30/mo 免费额度）或 RunPod

---

## 二、自研 Skill（统一放 `skills/custom/`）

每个自研 Skill 都遵循 `SKILL.md` + `README.md` 的标准结构。

### 1. `material-parser`
- 接入 Agent：`material`
- 输入：`raw_text` + 文件路径列表
- 输出：`MaterialPool`（见开发文档 §8.2）
- 触发：当 user message 含原始材料或附件
- 关键内容：
  - md/txt → 直接 LLM 抽字段
  - docx → 用 python-docx 提取段落 → LLM 抽字段
  - pdf → pdfminer 文本，失败兜底 PRD §9.2 文案
  - xlsx → pandas read_excel → 转 markdown 表 → LLM 抽指标
- 失败降级：返回 `parsing_partial`，列出已成功字段 + 建议用户补充

### 2. `point-extractor`
- 接入 Agent：`point-extractor`
- 规则：
  1. 一句话总结 ≤80 字
  2. 重点 ≤3 条（每条 ≤30 字，必须包含动词+对象+结果数据）
  3. 进度判断从枚举 `正常 / 有轻微风险 / 有风险 / 已延期` 选
  4. 风险条目必须 `{item, impact}`
  5. 支持诉求必须 `{item, owner}`
  6. 缺数据 → 输出 `data_gaps[]`，不要编造

### 3. `report-structure`
- 接入 Agent：`structure`
- 模板（YAML）：
  - `daily.yaml`：完成 → 进行中 → 问题 → 支持 → 计划
  - `project_progress.yaml`：目标 → 进度 → 成果 → 风险 → 协调 → 计划
  - `review.yaml`：职责 → 管理动作 → 成果 → 案例 → 改进 → 计划
  - `introduction.yaml`：背景 → 目标 → 内容 → 成果 → 问题 → 后续

### 4. `upward-translator`
- 接入 Agent：`upward-opt`
- 转换原则：
  - 结论前置
  - 动词改主动 + 量化
  - 问题改 `问题 + 影响`
  - 计划改 `动作 + 时间 + 责任人`
- 句式库：`references/upward-phrases.md`
- 反例库：`references/anti-patterns.md`

### 5. `copywriter`
- 接入 Agent：`copywriter`
- 输出：
  - `script.md`（1 / 3 / 5 分钟，3 分钟 500-750 字）
  - `narrations.ts` 草案（章节内 step → 文本）
  - 页面文案（每页 ≤30 字标题 + 3 条要点 ≤15 字/条）
- 关键约束：
  - 句长 ≤25 字
  - 不出现 emoji / 英文括号 / 全角空格
  - 每段以"完成…/推进…/建议…"动词开头

### 6. `report-reviewer`
- 接入 Agent：`reviewer`
- 检查项（来自 PRD §10.8）：
  1. 一句话总结存在
  2. ≤3 重点
  3. 包含风险
  4. 风险有影响
  5. 有下一步计划
  6. 有支持诉求
  7. 字数 / 时长合理
  8. 适合当前 audience
- 同时跑 `humanizer` 二次扫描，给 `ai_signal_score`
- 输出：建议列表 + quick_actions 启用矩阵

---

## 三、安装与发布

### 3.1 安装方式
- **第三方**：`git submodule add <repo> skills/third-party/<name>`，再 `scripts/install-skills.sh` 把需要的 SKILL.md + 资源复制到 `openclaw/workspaces/<agent>/.agents/skills/`
- **自研**：放 `skills/custom/<name>/`，安装脚本同样复制到对应 Agent

### 3.2 版本锁
`skills/third-party/.versions.lock`：
```
garden-skills@<commit-sha>
humanizer@v2.5.1
heygen-skills@<commit-sha>
claude-code-video-toolkit@<commit-sha>
```

CI：每个 Skill 一个最小集成测试，检测 SKILL.md frontmatter 必填字段（`name`, `description`, `version`）。

