#!/usr/bin/env bash
# 把 skills/ 下的 Skill 按映射表挂载到各 Agent 的 .agents/skills/
# Usage:
#   bash scripts/install-skills.sh --all          # 全部
#   bash scripts/install-skills.sh --agent html-designer

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# === Skill → Agent 映射（与 docs/Skill接入清单.md 一致） ===
declare -A MAP
MAP[material]="kb-retriever material-parser"
MAP[point-extractor]="point-extractor"
MAP[structure]="report-structure"
MAP[upward-opt]="upward-translator humanizer"
MAP[copywriter]="copywriter web-video-presentation"
MAP[html-designer]="web-design-engineer web-video-presentation gpt-image-2"
MAP[video-producer]="web-video-presentation elevenlabs playwright-recording ffmpeg moviepy minimax-tts minimax-video minimax-music klingai heygen"
MAP[reviewer]="report-reviewer humanizer"

find_skill_src() {
  local name="$1"
  # 候选源:每条都必须用 $name 参与匹配,避免「固定路径恒命中」导致的静默错链。
  # humanizer 是单 skill 仓库(skills/third-party/humanizer/SKILL.md),只有当请求的
  # name 恰好等于 "humanizer" 时才走那条分支——否则会把任意未匹配的 skill 错链到它。
  local candidates=(
    "$REPO_ROOT/skills/custom/$name"
    "$REPO_ROOT/skills/third-party/garden-skills/skills/$name"
    "$REPO_ROOT/skills/third-party/heygen-skills/$name"
    "$REPO_ROOT/skills/third-party/claude-code-video-toolkit/.claude/skills/$name"
    "$REPO_ROOT/skills/third-party/claude-code-video-toolkit/skills/$name"
  )
  if [[ "$name" == "humanizer" ]]; then
    candidates+=("$REPO_ROOT/skills/third-party/humanizer")
  fi
  for base in "${candidates[@]}"; do
    if [[ -d "$base" && -f "$base/SKILL.md" ]]; then
      echo "$base"; return
    fi
  done
  return 1
}

install_for_agent() {
  local agent="$1"
  local skills="${MAP[$agent]:-}"
  [[ -z "$skills" ]] && { echo "  (no skills for $agent)"; return; }
  local dst_root="$REPO_ROOT/openclaw/workspaces/$agent/.agents/skills"
  mkdir -p "$dst_root"
  for s in $skills; do
    if src=$(find_skill_src "$s"); then
      ln -sfn "$src" "$dst_root/$s"
      echo "  ✓ $agent ⇐ $s   ($src)"
    else
      echo "  ⚠️  $agent ⇐ $s   (源未找到，请先 git submodule update --init 或放入 skills/custom/$s)"
    fi
  done
}

if [[ "${1:-}" == "--all" ]]; then
  for a in "${!MAP[@]}"; do
    echo "▸ $a"
    install_for_agent "$a"
  done
elif [[ "${1:-}" == "--agent" && -n "${2:-}" ]]; then
  install_for_agent "$2"
else
  echo "usage: $0 --all | --agent <id>"
  exit 1
fi
