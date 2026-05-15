# OpenClaw 集群接入说明

> 配套：`docs/开发文档.md` §3
> 目标读者：负责集群初始化、Agent 配置、Channel Plugin、CI 自动化的工程师

## 一、底层概念回顾

OpenClaw 采用 **Actor 模型**：
- 每个 Agent = 一个独立 Actor（独立 workspace、独立 `agentDir`、独立 session 历史、独立 auth profile）
- Agent 之间通过 **Gateway** 路由的消息通信（不共享内存）
- Gateway 是 WebSocket Server，把来自外部 Channel 的消息按 `bindings` 路由给目标 Agent
- 每个 Agent 的"大脑"= **Brain**（ReAct loop + LLM Provider）

关键文件：
| 文件 | 路径 | 作用 |
| --- | --- | --- |
| `openclaw.json` | `~/.openclaw/openclaw.json` | Gateway / agents 列表 / channels / bindings 总配置 |
| `SOUL.md` | `~/.openclaw/workspaces/<id>/SOUL.md` | 人格 / 价值观，最高优先级注入 system prompt |
| `AGENTS.md` | `~/.openclaw/workspaces/<id>/AGENTS.md` | 操作手册 / 模型 / 工具 / 跨 Agent 协议 |
| `USER.md` | `~/.openclaw/workspaces/<id>/USER.md` | 用户/领域偏好（动态写入） |
| `.agents/skills/` | `~/.openclaw/workspaces/<id>/.agents/skills/<name>/SKILL.md` | 该 Agent 可见的 Skill |
| `auth/` `models/` `sessions/` | `~/.openclaw/agents/<id>/` | 鉴权、模型注册、会话历史 |

⚠️ **官方约束**：`agentDir` 严禁跨 Agent 复用（auth/session 会串扰）。

## 二、初始化 8 个 Agent

```bash
# 1. 安装 CLI
npm install -g openclaw@latest
openclaw onboard --install-daemon

# 2. 初始化集群（脚本封装在 scripts/bootstrap-openclaw.sh）
for id in coordinator material point-extractor structure upward-opt copywriter html-designer video-producer reviewer; do
  openclaw agent create --id "$id" \
    --workspace "$HOME/.openclaw/workspaces/$id" \
    --agent-dir "$HOME/.openclaw/agents/$id"
done

# 3. 把 docs/Agent-Prompts/ 下的 SOUL/AGENTS/USER 模板拷贝过去
bash scripts/bootstrap-openclaw.sh apply-prompts

# 4. 挂载 Skill
bash scripts/install-skills.sh --all

# 5. 注册 Channel Plugin（自研 huihuibao-web）
openclaw channels add --type ws-custom \
  --name huihuibao-web \
  --listen 127.0.0.1:7860

# 6. 写入 bindings
# (脚本里直接 patch openclaw.json)
```

## 三、`openclaw.json` 示例（节选）

```jsonc
{
  "gateway": {
    "host": "127.0.0.1",
    "port": 7800,
    "log_level": "info"
  },
  "agents": {
    "list": [
      { "id": "coordinator",     "workspace": "~/.openclaw/workspaces/coordinator",     "agentDir": "~/.openclaw/agents/coordinator" },
      { "id": "material",        "workspace": "~/.openclaw/workspaces/material",        "agentDir": "~/.openclaw/agents/material" },
      { "id": "point-extractor", "workspace": "~/.openclaw/workspaces/point-extractor", "agentDir": "~/.openclaw/agents/point-extractor" },
      { "id": "structure",       "workspace": "~/.openclaw/workspaces/structure",       "agentDir": "~/.openclaw/agents/structure" },
      { "id": "upward-opt",      "workspace": "~/.openclaw/workspaces/upward-opt",      "agentDir": "~/.openclaw/agents/upward-opt" },
      { "id": "copywriter",      "workspace": "~/.openclaw/workspaces/copywriter",      "agentDir": "~/.openclaw/agents/copywriter" },
      { "id": "html-designer",   "workspace": "~/.openclaw/workspaces/html-designer",   "agentDir": "~/.openclaw/agents/html-designer" },
      { "id": "video-producer",  "workspace": "~/.openclaw/workspaces/video-producer",  "agentDir": "~/.openclaw/agents/video-producer" },
      { "id": "reviewer",        "workspace": "~/.openclaw/workspaces/reviewer",        "agentDir": "~/.openclaw/agents/reviewer" }
    ]
  },
  "channels": [
    { "name": "huihuibao-web", "type": "ws-custom", "listen": "127.0.0.1:7860" }
  ],
  "bindings": [
    {
      "agentId": "coordinator",
      "match": { "channel": "huihuibao-web" }
    }
  ],
  "providers": {
    "default": "anthropic",
    "anthropic": { "model": "claude-sonnet-4-6" }
  }
}
```

> 8 个 Agent 中只有 `coordinator` 直接绑定外部 Channel；其它 Agent 通过 coordinator 内部 `@mention` 唤起，路由由 Gateway 完成（agent-to-agent 消息也走 Gateway）。

## 四、Channel Plugin · huihuibao-web

业务后端 `apps/web-backend` 自带 Channel Plugin，开机时连 Gateway：

```py
# packages/openclaw-client/python/channel.py
import asyncio, json, websockets

async def run_channel(gateway_url="ws://127.0.0.1:7800"):
    async with websockets.connect(gateway_url) as ws:
        await ws.send(json.dumps({
            "type": "channel.register",
            "name": "huihuibao-web"
        }))
        async for raw in ws:
            msg = json.loads(raw)
            # 转 SSE 推给前端 / 持久化到 TaskRun 表
            await dispatch(msg)
```

把任务转写成消息发往 coordinator：

```py
await ws.send(json.dumps({
    "type": "message.inbound",
    "channel": "huihuibao-web",
    "accountId": user_id,
    "peerId": f"task:{task_id}",
    "content": prompt_text,                       # coordinator SOUL+AGENTS 已经知道怎么处理
    "metadata": {
        "task_id": task_id,
        "report_type": report_type,
        "audience": audience,
        "duration": duration,
        "style":    style
    }
}))
```

`peerId = task:<task_id>` 让 Gateway 把同一任务的回流消息路由进同一 session：`agent:coordinator:huihuibao-web:peer:task:<task_id>`。

## 五、Agent 间通信约定

- coordinator 发起：`@material 请整理以下材料：<json>`
- material 完成：`@coordinator 结果如下：<MaterialPool json>`
- 当 coordinator 切换阶段：`@point-extractor 请提炼重点：<MaterialPool>`

所有消息正文用 §3.4 的 JSON 结构包裹（OpenClaw 透传文本，约定靠 SOUL/AGENTS 强制）。

为防止 ReAct 把 JSON 包裹拆乱，AGENTS.md 里给每个 Agent 加一段：

```
## 输入/输出协议
- 输入永远是一段 ```json ... ``` 代码块，字段见下表
- 输出必须是一段 ```json ... ``` 代码块，且只能包含 schema 中列出的字段
- 任何错误一律输出 {"msg_type":"task.step.error","error":{"code":"...","biz_message":"..."}}
- biz_message 是给前端看的中文短句，不得包含 code/agent_id/task_id
```

## 六、Provider / Model 配置

- 默认 Anthropic `claude-sonnet-4-6`
- 长文本规划任务（coordinator）可切 `claude-opus-4-7`
- 视频/图像类不调 LLM
- Provider 切换在管理平台 Config 中心做，写回 `openclaw.json` `providers.*` 并广播 `system.reload-provider`

## 七、热更新

- 改 SOUL/AGENTS/USER → 发 `system.reload-agent` 到目标 Agent
- 挂/卸 Skill → 发 `system.reload-skills`
- 改 Provider → 发 `system.reload-provider`
- 改 Binding → Gateway 主动重读 `openclaw.json`（管理平台调用 `openclaw gateway reload`）

## 八、健康检查

| 命令 | 用途 |
| --- | --- |
| `openclaw agents list --bindings` | 列出 agent + 绑定关系 |
| `openclaw channels status --probe` | 探测 channel 连通性 |
| `openclaw gateway` | 启动/查看 gateway 状态 |
| `openclaw sessions list --agent <id>` | 列出某 agent 的 session |

管理平台 Health 页通过执行以上命令（或 OpenClaw HTTP API，如果有）聚合展示。

## 九、CI / 部署

- `infra/docker-compose.yml` 启动：
  - `gateway`（暴露 7800）
  - `web-backend`（连 gateway，监听 8000，承载 SSE）
  - `admin-backend`（监听 8100）
  - `web-frontend`（监听 3000）
  - `admin-frontend`（监听 3100）
  - `video-worker`（Playwright + ffmpeg）
- 卷挂载：`~/.openclaw/`、`data/`
- 首次启动：进入容器执行 `bash scripts/bootstrap-openclaw.sh && bash scripts/install-skills.sh --all`

