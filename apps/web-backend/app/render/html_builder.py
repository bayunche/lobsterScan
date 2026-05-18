"""把 copywriting 出来的 slides + narrations 渲染成可独立打开的 HTML 汇报页

输入数据从 pipeline 内存里直接传入(无需依赖 agent 的副作用),输出一个
self-contained 的 index.html 到 data/outputs/<task>/web-presentation/。
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

# 简洁现代的演示页样式,避免 Tailwind / shadcn 通用 AI 味
_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<style>
  :root {
    --ink: #1a1a1f;
    --ink-soft: #5a5a66;
    --bg: #f7f4ee;
    --bg-card: #ffffff;
    --accent: #c6543c;
    --accent-soft: #f1d8cf;
    --line: #e6e1d6;
    --shadow: 0 18px 60px -28px rgba(60, 30, 10, 0.25);
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body {
    font: 16px/1.6 "PingFang SC", "Noto Sans SC", -apple-system, BlinkMacSystemFont, sans-serif;
    color: var(--ink);
    background: var(--bg);
    overflow: hidden;
  }

  /* 进度条 */
  .progress {
    position: fixed; top: 0; left: 0; right: 0; height: 3px;
    background: var(--line); z-index: 50;
  }
  .progress > i { display: block; height: 100%; background: var(--accent); width: 0%;
                   transition: width 280ms ease; }

  /* 舞台 */
  .stage {
    position: relative;
    min-height: 100vh;
    display: grid;
    place-items: center;
    padding: 56px 64px 96px;
  }
  .slide {
    width: min(960px, 100%);
    background: var(--bg-card);
    border: 1px solid var(--line);
    border-radius: 22px;
    padding: 56px 64px;
    box-shadow: var(--shadow);
    display: none;
    animation: rise 320ms ease both;
  }
  .slide.is-active { display: block; }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); }
                    to   { opacity: 1; transform: translateY(0); } }

  /* 各 slide type 微调 */
  .slide[data-type="cover"] {
    background: linear-gradient(135deg, #14141a 0%, #2a1f1c 100%);
    color: #f5ede4; border-color: transparent;
  }
  .slide[data-type="cover"] .meta { color: #c0b7ab; }
  .slide[data-type="cover"] .accent { color: var(--accent-soft); }
  .slide[data-type="risks"] { border-left: 6px solid var(--accent); }
  .slide[data-type="next_steps"] { border-left: 6px solid #5a7d4a; }

  .eyebrow {
    font-size: 12px; letter-spacing: 2px; text-transform: uppercase;
    color: var(--ink-soft); margin-bottom: 18px;
  }
  .slide[data-type="cover"] .eyebrow { color: #a89a8a; }
  h1.slide-title {
    font-size: 40px; line-height: 1.2; margin: 0 0 28px;
    font-weight: 700;
  }
  .slide[data-type="cover"] h1.slide-title { font-size: 52px; }
  .bullets {
    list-style: none; padding: 0; margin: 0;
    display: grid; gap: 16px;
  }
  .bullets li {
    font-size: 22px; line-height: 1.45;
    padding-left: 28px; position: relative;
  }
  .bullets li::before {
    content: ""; position: absolute; left: 0; top: 12px;
    width: 12px; height: 12px; border-radius: 50%;
    background: var(--accent);
  }
  .slide[data-type="cover"] .bullets li::before { background: var(--accent-soft); }
  .meta {
    margin-top: 32px; font-size: 14px; color: var(--ink-soft);
    display: flex; gap: 18px; flex-wrap: wrap;
  }
  .meta span { display: inline-flex; align-items: center; gap: 6px; }
  .meta span::before { content: "•"; color: var(--accent); }

  /* 旁白 / 备注 */
  .narration {
    margin-top: 28px; padding: 16px 18px;
    background: rgba(198, 84, 60, 0.06);
    border-left: 3px solid var(--accent);
    font-size: 15px; color: var(--ink-soft);
    border-radius: 4px;
  }
  .slide[data-type="cover"] .narration {
    background: rgba(255,255,255,0.05); color: #c0b7ab;
    border-left-color: var(--accent-soft);
  }

  /* 媒体嵌入 */
  .media {
    margin-top: 24px;
    display: grid; gap: 12px;
  }
  .media video, .media audio {
    width: 100%; border-radius: 12px; background: #000;
  }
  .media .media-label {
    font-size: 12px; color: var(--ink-soft); letter-spacing: 1px;
  }

  /* 末页 · 审校 */
  .slide[data-type="review"] { background: #fdfaf4; }
  .review-block { margin-top: 16px; }
  .review-block h3 { margin: 24px 0 8px; font-size: 16px; color: var(--ink-soft);
                      letter-spacing: 1px; text-transform: uppercase; }
  .review-suggestions { list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }
  .review-suggestions li {
    background: #fff; padding: 12px 16px; border-radius: 10px;
    border: 1px solid var(--line); font-size: 16px;
  }
  .checks { display: flex; flex-wrap: wrap; gap: 8px; }
  .checks span {
    font-size: 13px; padding: 4px 10px; border-radius: 999px;
    background: #e8f1e3; color: #3d5b32;
  }
  .checks span.fail { background: #f7dbd1; color: #8a3422; }

  /* 控件 */
  .controls {
    position: fixed; left: 0; right: 0; bottom: 0;
    display: flex; align-items: center; justify-content: center;
    gap: 14px; padding: 18px;
    background: linear-gradient(to top, rgba(247,244,238,0.95), transparent);
    z-index: 30;
  }
  .controls button {
    border: 1px solid var(--line); background: var(--bg-card);
    color: var(--ink); padding: 10px 18px;
    border-radius: 999px; cursor: pointer; font: inherit;
    transition: transform 120ms ease;
  }
  .controls button:hover:not(:disabled) { transform: translateY(-1px); }
  .controls button:disabled { opacity: 0.4; cursor: not-allowed; }
  .dots { display: flex; gap: 6px; padding: 0 8px; }
  .dots i {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--line); cursor: pointer; transition: all 200ms;
  }
  .dots i.is-active { background: var(--accent); width: 24px; border-radius: 4px; }
  .step-count { font-size: 13px; color: var(--ink-soft); min-width: 56px; text-align: center; }

  @media (max-width: 720px) {
    .stage { padding: 40px 16px 96px; }
    .slide { padding: 32px 28px; }
    h1.slide-title { font-size: 28px; }
    .slide[data-type="cover"] h1.slide-title { font-size: 32px; }
    .bullets li { font-size: 18px; }
  }
</style>
</head>
<body>
<div class="progress"><i id="bar"></i></div>
<main class="stage" id="stage"></main>
<nav class="controls">
  <button id="prev">← 上一页</button>
  <div class="dots" id="dots"></div>
  <span class="step-count" id="count">1 / 1</span>
  <button id="next">下一页 →</button>
</nav>

<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  const data = JSON.parse(document.getElementById('data').textContent);
  const stage = document.getElementById('stage');
  const dots = document.getElementById('dots');
  const bar = document.getElementById('bar');
  const count = document.getElementById('count');
  const prevBtn = document.getElementById('prev');
  const nextBtn = document.getElementById('next');

  const slides = data.slides;
  let cur = 0;

  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) Object.entries(attrs).forEach(([k, v]) => {
      if (k === 'class') e.className = v;
      else if (k === 'dataset') Object.entries(v).forEach(([dk, dv]) => e.dataset[dk] = dv);
      else if (k === 'html') e.innerHTML = v;
      else e.setAttribute(k, v);
    });
    (children || []).forEach(c => {
      if (typeof c === 'string') e.appendChild(document.createTextNode(c));
      else if (c) e.appendChild(c);
    });
    return e;
  }

  function renderSlide(slide, idx) {
    const isCover = slide.type === 'cover' || idx === 0;
    const isReview = slide.type === 'review';
    const children = [];
    children.push(el('div', { class: 'eyebrow' }, [slide.eyebrow || (isCover ? data.meta.report_type : `第 ${idx} 页`)]));
    children.push(el('h1', { class: 'slide-title' }, [slide.title || '']));
    if (slide.content && slide.content.length) {
      children.push(el('ul', { class: 'bullets' },
        slide.content.map(c => el('li', null, [c]))));
    }
    if (isCover && data.meta) {
      const meta = data.meta;
      children.push(el('div', { class: 'meta' }, [
        el('span', null, [`场景:${meta.report_type}`]),
        el('span', null, [`时长:${meta.duration}`]),
        el('span', null, [`听众:${meta.audience}`]),
        el('span', null, [`风格:${meta.style}`]),
      ]));
    }
    if (slide.narration) {
      children.push(el('div', { class: 'narration' }, ['🎙  ' + slide.narration]));
    }
    if (slide.media && (slide.media.video || slide.media.audio)) {
      const media = el('div', { class: 'media' }, []);
      if (slide.media.video) {
        media.appendChild(el('div', { class: 'media-label' }, ['数字人开场镜头']));
        const v = el('video', { controls: '', preload: 'metadata', src: slide.media.video }, []);
        media.appendChild(v);
      }
      if (slide.media.audio) {
        media.appendChild(el('div', { class: 'media-label' }, ['本页配音']));
        media.appendChild(el('audio', { controls: '', preload: 'metadata', src: slide.media.audio }, []));
      }
      children.push(media);
    }
    if (isReview && slide.review) {
      const block = el('div', { class: 'review-block' }, []);
      if (slide.review.suggestions && slide.review.suggestions.length) {
        block.appendChild(el('h3', null, ['可执行建议']));
        block.appendChild(el('ul', { class: 'review-suggestions' },
          slide.review.suggestions.map(s => el('li', null, [s]))));
      }
      if (slide.review.checks) {
        block.appendChild(el('h3', null, ['质量检查']));
        const checks = el('div', { class: 'checks' }, []);
        const labels = {
          has_summary: '总结', key_points_ok: '重点', has_risks: '风险',
          risks_have_impact: '影响', has_next_steps: '下一步',
          support_clear: '诉求', length_ok: '时长', audience_fit: '听众',
        };
        Object.entries(slide.review.checks).forEach(([k, v]) => {
          checks.appendChild(el('span', { class: v ? '' : 'fail' }, [(labels[k] || k) + (v ? ' ✓' : ' ✗')]));
        });
        block.appendChild(checks);
      }
      if (slide.review.estimated_duration) {
        block.appendChild(el('div', { class: 'meta' }, [
          el('span', null, [`预估时长:${slide.review.estimated_duration}`]),
          ...(slide.review.ai_signal_score != null ? [el('span', null, [`AI 套话:${slide.review.ai_signal_score}`])] : []),
        ]));
      }
      children.push(block);
    }
    return el('section', {
      class: 'slide' + (idx === 0 ? ' is-active' : ''),
      dataset: { type: slide.type || 'detail', idx: String(idx) }
    }, children);
  }

  slides.forEach((s, i) => stage.appendChild(renderSlide(s, i)));
  slides.forEach((_, i) => {
    const dot = el('i', { class: i === 0 ? 'is-active' : '' }, []);
    dot.addEventListener('click', () => go(i));
    dots.appendChild(dot);
  });

  function go(i) {
    cur = Math.max(0, Math.min(slides.length - 1, i));
    stage.querySelectorAll('.slide').forEach((s, idx) => {
      s.classList.toggle('is-active', idx === cur);
    });
    dots.querySelectorAll('i').forEach((d, idx) => {
      d.classList.toggle('is-active', idx === cur);
    });
    count.textContent = (cur + 1) + ' / ' + slides.length;
    bar.style.width = ((cur + 1) / slides.length * 100) + '%';
    prevBtn.disabled = cur === 0;
    nextBtn.disabled = cur === slides.length - 1;
  }

  prevBtn.addEventListener('click', () => go(cur - 1));
  nextBtn.addEventListener('click', () => go(cur + 1));
  document.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') go(cur - 1);
    if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') { e.preventDefault(); go(cur + 1); }
    if (e.key === 'Home') go(0);
    if (e.key === 'End') go(slides.length - 1);
  });

  go(0);
})();
</script>
</body>
</html>
"""


def _pick_narration(narrations: list[dict], page_no: int) -> str:
    """从 narrations 中挑出与 page_no 相关的旁白合并成一段."""
    if not narrations:
        return ""
    matched: list[str] = []
    for n in narrations:
        if not isinstance(n, dict):
            continue
        chapter = n.get("chapter")
        step = n.get("step")
        text = (n.get("text") or "").strip()
        if not text:
            continue
        # narrations 的 chapter 字段可能就是 page_no(早期 SOUL 文档对齐过)
        if chapter == page_no or step == page_no:
            matched.append(text)
    return " ".join(matched)


def _slide_type(s: dict, idx: int, total: int) -> str:
    """slide.type 字段可能缺,做点 fallback."""
    t = (s.get("type") or "").strip()
    if t:
        return t
    if idx == 0:
        return "cover"
    if idx == total - 1:
        return "next_steps"
    return "detail"


def build_presentation(
    *,
    task_id: str,
    title: str,
    audience: str,
    duration: str,
    style: str,
    report_type: str,
    copywriting: dict | None,
    review: dict | None = None,
    video_meta: dict | None = None,
    output_root: Path,
) -> dict[str, Any]:
    """生成单文件 HTML 演示。返回 {ok, path, pages, bytes}.

    output_root 是 data/outputs/<task_id>/,在它下面创建 web-presentation/index.html。
    """
    copywriting = copywriting or {}
    raw_slides: list[dict] = list(copywriting.get("slides") or [])
    narrations: list[dict] = list(copywriting.get("narrations") or [])
    script_md: str = (copywriting.get("script_md") or "").strip()

    # 没有 slides 时,退化:把 script 切段当 slides
    if not raw_slides and script_md:
        chunks = [c.strip() for c in script_md.split("\n\n") if c.strip()]
        raw_slides = [
            {
                "page_no": 1, "title": title or "汇报", "type": "cover",
                "content": [chunks[0][:60]] if chunks else [],
            },
            *[
                {"page_no": i + 2, "title": f"要点 {i + 1}", "type": "detail",
                 "content": [c[:80] for c in chunk.split("。") if c.strip()][:4]}
                for i, chunk in enumerate(chunks[1:5])
            ],
        ]

    total = max(1, len(raw_slides))
    slides_out: list[dict] = []
    for idx, s in enumerate(raw_slides):
        page_no = int(s.get("page_no") or idx + 1)
        narration = _pick_narration(narrations, page_no)
        slide = {
            "page_no": page_no,
            "title": s.get("title") or f"第 {idx + 1} 页",
            "type": _slide_type(s, idx, total),
            "content": [c for c in (s.get("content") or []) if isinstance(c, str)],
            "narration": narration,
        }
        # 第 1 页贴上数字人开场视频(如果有)
        if idx == 0 and video_meta:
            iv = video_meta.get("intro_video") or {}
            iv_path = iv.get("path") if isinstance(iv, dict) and iv.get("ok") else None
            if iv_path:
                # 用相对路径,html 和 video 在同一 task 输出目录下
                slide["media"] = {"video": _relpath_to(iv_path, output_root / "web-presentation")}
        slides_out.append(slide)

    # 末页拼上审校结果(如果有)
    if review and isinstance(review, dict):
        slides_out.append({
            "page_no": len(slides_out) + 1,
            "title": "审校建议",
            "type": "review",
            "content": [],
            "narration": "",
            "review": {
                "suggestions": review.get("suggestions") or [],
                "checks": review.get("checks") or {},
                "estimated_duration": review.get("estimated_duration"),
                "ai_signal_score": review.get("ai_signal_score"),
            },
        })

    payload = {
        "meta": {
            "task_id": task_id,
            "title": title,
            "report_type": report_type,
            "audience": audience,
            "duration": duration,
            "style": style,
        },
        "slides": slides_out,
    }

    html = (
        _HTML_TEMPLATE
        .replace("__TITLE__", escape(title or "会汇报"))
        .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    )

    project_dir = output_root / "web-presentation"
    project_dir.mkdir(parents=True, exist_ok=True)
    out = project_dir / "index.html"
    out.write_text(html, encoding="utf-8")

    return {
        "ok": True,
        "path": str(out),
        "rel_path": "web-presentation/index.html",
        "pages": len(slides_out),
        "bytes": out.stat().st_size,
    }


def _relpath_to(target: str, base: Path) -> str:
    """target 是 'data/outputs/<task>/video/intro.mp4',base 是
    'data/outputs/<task>/web-presentation/'。计算相对引用."""
    try:
        t = Path(target).resolve()
        b = base.resolve()
        return str(Path.relative_to(t, b)) if str(t).startswith(str(b)) else _walk_up(t, b)
    except Exception:  # noqa: BLE001
        return target


def _walk_up(t: Path, b: Path) -> str:
    """简单实现 os.path.relpath 的等价物."""
    import os
    return os.path.relpath(str(t), str(b))
