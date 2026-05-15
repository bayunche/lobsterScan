# 会汇报 · 智能汇报材料生成助手

> 面向基层主管 / 小组长 / 项目小负责人的 **OpenClaw 集群**汇报助手
> 让基层管理者把工作讲到重点上

仓库代号：`lobsterScan` 🦞
展示名：**会汇报**

---

## 它做什么

把基层主管手里零散的工作记录、任务清单、问题反馈、会议纪要，自动整理成：

1. 一句话工作摘要 + 3 个重点
2. 1 / 3 / 5 分钟汇报口播稿
3. PPT 样式 HTML 汇报页（可投屏）
4. 数字人配音汇报视频
5. 审校建议（缺什么、像不像流水账、怎么修改）

底层是 **OpenClaw 多 Agent 集群**：8 位「同事」（资料员 / 分析师 / 结构设计师 / 表达教练 / 文书 / 设计师 / 视频制作 / 质量检查员）分工协作。

---

## 仓库结构

```
docs/                      ← 全部文档（必读：开发文档.md）
  开发文档.md               ← 核心，包含架构 / Agent / Skill / API / 数据模型 / 里程碑
  Skill接入清单.md
  OpenClaw集群接入说明.md
  管理平台规格.md
  API接口规范.md
  Agent-Prompts/           ← 8 个 Agent 的 SOUL / AGENTS / USER 模板
  原始资料/                 ← PRD / 方案 / 一页纸

apps/
  web-frontend/            ← 会汇报 · 用户端 (Next.js)
  web-backend/             ← 会汇报 · 任务编排后端 + Channel Plugin (FastAPI)
  admin-frontend/          ← OpenClaw 管理平台 · 前端 (Next.js)
  admin-backend/           ← OpenClaw 管理平台 · 后端 (FastAPI)

packages/
  ui/                      ← 前端共享组件
  schemas/                 ← TS + Py 共享 JSON Schema
  openclaw-client/         ← 调 Gateway 的 SDK 封装

openclaw/                  ← 集群配置与 workspaces（开发期镜像 ~/.openclaw）
  openclaw.json            ← Gateway / agents / bindings 模板
  workspaces/<agentId>/    ← SOUL/AGENTS/USER + .agents/skills/

skills/
  third-party/             ← 第三方 Skill（submodule）
    garden-skills/         ← web-design-engineer / web-video-presentation / gpt-image-2 / kb-retriever
    humanizer/             ← AI 套话识别
    heygen-skills/         ← 数字人 avatar / video / translate
    claude-code-video-toolkit/  ← Remotion / ElevenLabs / SadTalker / FFmpeg / Playwright
  custom/                  ← 自研 Skill：material-parser / point-extractor / report-structure / upward-translator / copywriter / report-reviewer

infra/                     ← docker-compose 与 Dockerfile
scripts/                   ← bootstrap / install-skills / demo-seed
tests/                     ← unit / integration / acceptance(PRD §14)
data/                      ← uploads / outputs / sqlite
```

---

## 快速开始（开发期）

```bash
# 1. 装依赖
npm install -g openclaw@latest
openclaw onboard --install-daemon
pnpm install
uv venv && uv pip install -e apps/web-backend -e apps/admin-backend

# 2. 初始化 OpenClaw 集群（8 个 Agent + 默认 Skill）
bash scripts/bootstrap-openclaw.sh
bash scripts/install-skills.sh --all

# 3. 启动
cp .env.example .env
bash scripts/dev.sh
```

访问：
- 用户端：http://localhost:3000
- 管理平台：http://localhost:3100
- OpenClaw Gateway：ws://127.0.0.1:7800

---

## 文档导航

| 想做… | 看哪个文档 |
| --- | --- |
| 拿到全局架构、Agent 分工、里程碑 | `docs/开发文档.md` |
| 知道用了哪些 Skill、怎么挂载 | `docs/Skill接入清单.md` |
| 安装 / 配置 OpenClaw 集群 | `docs/OpenClaw集群接入说明.md` |
| 开发管理平台 | `docs/管理平台规格.md` |
| 对接 API | `docs/API接口规范.md` |
| 改某个 Agent 的人格或协议 | `docs/Agent-Prompts/<agent-id>.md` |
| 看产品需求原文 | `docs/原始资料/PRD.docx` |

---

## 版本

| 版本 | 状态 | 内容 |
| --- | --- | --- |
| V0.1 原型 | 计划中 | 文本输入 → 重点 → 讲稿 |
| **V0.5 比赛 MVP** | 计划中 | 完整 8 Agent + HTML + 数字人视频 + 审校 + 管理平台 MVP |
| V1.0 可用版 | — | 多模板 / 多时长 / 局部重写 / 自托管视频 |
| V2.0 增强版 | — | 汇报能力评分 / 团队汇总 |

详见 `docs/开发文档.md` §13。

