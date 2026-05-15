#!/usr/bin/env bash
# 初始化 OpenClaw 集群：创建 8 个 Agent + 拷贝 SOUL/AGENTS/USER 模板
# Usage:
#   bash scripts/bootstrap-openclaw.sh             # 完整初始化
#   bash scripts/bootstrap-openclaw.sh apply-prompts  # 仅同步 docs/Agent-Prompts → workspaces

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS=(coordinator material point-extractor structure upward-opt copywriter html-designer video-producer reviewer)

create_agents() {
  for id in "${AGENTS[@]}"; do
    ws="$REPO_ROOT/openclaw/workspaces/$id"
    ad="$HOME/.openclaw/agents/$id"
    mkdir -p "$ws/.agents/skills" "$ws/notes" "$ad"
    echo "✓ ensured agent dir for $id"
  done

  if command -v openclaw >/dev/null; then
    for id in "${AGENTS[@]}"; do
      openclaw agent create --id "$id" \
        --workspace "$REPO_ROOT/openclaw/workspaces/$id" \
        --agent-dir "$HOME/.openclaw/agents/$id" \
        2>/dev/null || true
    done
  else
    echo "⚠️  openclaw CLI 未安装。请先： npm install -g openclaw@latest && openclaw onboard --install-daemon"
  fi
}

apply_prompts() {
  # 从 docs/Agent-Prompts/<id>.md 提取 SOUL / AGENTS / USER 三段，写入 workspaces/<id>/
  python3 - <<'PY'
import re, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent if False else pathlib.Path('.').resolve()
prompt_dir = ROOT / "docs" / "Agent-Prompts"
ws_root    = ROOT / "openclaw" / "workspaces"
agents = ["coordinator","material","point-extractor","structure","upward-opt",
          "copywriter","html-designer","video-producer","reviewer"]

def extract(md_text):
    """返回 {SOUL.md|AGENTS.md|USER.md: content}"""
    pat = re.compile(r"##\s+([A-Z]+\.md)[^\n]*\n+```(?:markdown)?\n(.*?)```", re.S)
    out = {}
    for m in pat.finditer(md_text):
        out[m.group(1).strip()] = m.group(2).rstrip() + "\n"
    return out

for aid in agents:
    src = prompt_dir / f"{aid}.md"
    if not src.exists():
        print(f"  skip {aid}: 模板缺失 {src}")
        continue
    parts = extract(src.read_text(encoding="utf-8"))
    dst = ws_root / aid
    dst.mkdir(parents=True, exist_ok=True)
    for fname, content in parts.items():
        (dst / fname).write_text(content, encoding="utf-8")
    print(f"✓ applied prompts for {aid} → {[k for k in parts]}")
PY
}

cmd="${1:-all}"
case "$cmd" in
  all)
    create_agents
    apply_prompts
    cp -n "$REPO_ROOT/openclaw/openclaw.json" "$HOME/.openclaw/openclaw.json" 2>/dev/null || true
    echo "✅ bootstrap done. 下一步： bash scripts/install-skills.sh --all"
    ;;
  apply-prompts) apply_prompts ;;
  *) echo "usage: $0 [all|apply-prompts]"; exit 1 ;;
esac
