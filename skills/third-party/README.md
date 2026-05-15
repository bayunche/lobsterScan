# 第三方 Skill 来源

> 此目录用 git submodule 维护。首次使用：`git submodule update --init --recursive`

| submodule path | repo | version |
| --- | --- | --- |
| `garden-skills/`              | https://github.com/ConardLi/garden-skills        | main |
| `humanizer/`                  | https://github.com/blader/humanizer              | v2.5.1 |
| `heygen-skills/`              | https://github.com/heygen-com/skills             | main |
| `claude-code-video-toolkit/`  | https://github.com/digitalsamba/claude-code-video-toolkit | main |

挂载到各 Agent 的命令：

```bash
bash scripts/install-skills.sh --all
```

