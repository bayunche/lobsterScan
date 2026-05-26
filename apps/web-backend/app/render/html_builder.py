"""把 copywriting 出来的 slides + narrations 渲染成可独立打开的 HTML 汇报页

输入数据从 pipeline 内存里直接传入(无需依赖 agent 的副作用),输出
self-contained 的 index.html + broadcast.html 到 data/outputs/<task>/web-presentation/。
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any


# 上限 1000MB/段 — 实际语音 mp3 单段不可能这么大(128kbps × 1000MB ≈ 17h),
# 只是兜底防止 path 指向异常巨型文件
_AUDIO_MAX_BYTES = 1000 * 1024 * 1024


def _audio_file_url(path: Path) -> str:
    """把 mp3 绝对路径转成 file:// URL · broadcast.html 给同机 playwright 录屏用。

    之前是 base64 inline,4MB/段上限对长 narration 经常静音。改 file:// 后:
    broadcast.html 被 playwright file:// 打开,file://→file:// 同源不受 CORS 限制,
    chromium autoplay 已在 launch args 放开(broadcast_recorder.py L65)。

    失败返回空串,前端 audio.src='' 静默跳过该段。
    """
    try:
        if not path.exists() or path.stat().st_size == 0:
            return ""
        if path.stat().st_size > _AUDIO_MAX_BYTES:
            return ""
        return path.resolve().as_uri()
    except Exception:  # noqa: BLE001
        return ""

# 设计:档案室 / 公文校对 / 印刷工艺 — 米黄棉纸 + 朱砂印章 + 配准十字 + ruler 进度
# 数字 count-up + tick 印章替代圆点 + paper-shift 翻页 + stagger reveal
_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__</title>
<style>
  :root {
    --paper: #f1ece1;
    --paper-deep: #ebe4d3;
    --paper-card: #faf5e9;
    --ink: #1f1a14;
    --ink-soft: #6b5e4a;
    --ink-mute: #9a8e76;
    --rule: #d8cdb2;
    --rule-soft: #e6dfc8;
    --vermilion: #b03021;
    --vermilion-soft: rgba(176, 48, 33, 0.12);
    --olive: #5a6b3a;
    --olive-soft: rgba(90, 107, 58, 0.12);
    --display: "Source Han Serif SC", "Noto Serif SC", "STSong",
               "SimSun", "Songti SC", "Times New Roman", serif;
    --sans: "PingFang SC", "Noto Sans SC", "Source Han Sans SC",
            "Microsoft YaHei", -apple-system, BlinkMacSystemFont, sans-serif;
    --mono: "IBM Plex Mono", "JetBrains Mono", "SF Mono",
            "Cascadia Mono", Consolas, monospace;
  }

  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body {
    font: 16px/1.7 var(--sans);
    color: var(--ink);
    background:
      radial-gradient(circle at 18% 12%, rgba(176,48,33,0.04), transparent 38%),
      radial-gradient(circle at 82% 78%, rgba(90,107,58,0.05), transparent 42%),
      var(--paper);
    overflow: hidden;
    letter-spacing: 0.01em;
  }
  /* 纸纹噪点 — 不依赖资源,纯 CSS 渐变模拟 */
  body::before {
    content: "";
    position: fixed; inset: 0;
    background-image:
      repeating-linear-gradient(0deg, rgba(31,26,20,0.018) 0 1px, transparent 1px 3px),
      repeating-linear-gradient(90deg, rgba(31,26,20,0.014) 0 1px, transparent 1px 4px);
    pointer-events: none; z-index: 1;
    mix-blend-mode: multiply;
  }

  /* ─── 顶端 ruler 进度 — 刻度型,不是 fill bar */
  .ruler {
    position: fixed; top: 0; left: 0; right: 0; height: 28px;
    z-index: 60;
    display: flex; align-items: flex-end;
    padding: 0 24px;
    background: linear-gradient(to bottom, var(--paper) 60%, transparent);
    border-bottom: 1px solid var(--rule-soft);
  }
  .ruler .ticks { flex: 1; display: flex; align-items: flex-end; gap: 0; }
  .ruler .ticks i {
    flex: 1; height: 8px; border-left: 1px solid var(--rule);
    position: relative; cursor: pointer;
    transition: height 200ms ease;
  }
  .ruler .ticks i:nth-child(5n+1) { height: 14px; }
  .ruler .ticks i.is-passed { border-left-color: var(--vermilion); }
  .ruler .ticks i.is-current {
    height: 22px; border-left: 2px solid var(--vermilion);
  }
  .ruler .ticks i.is-current::after {
    content: ""; position: absolute; left: -5px; top: -5px;
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--vermilion);
    box-shadow: 0 0 0 4px var(--paper);
  }
  .ruler .label {
    font: 11px/1 var(--mono);
    letter-spacing: 0.18em;
    color: var(--ink-mute);
    padding: 0 12px 4px;
    border-left: 1px solid var(--rule);
  }

  /* ─── 配准十字 regmark 四角 */
  .regmark {
    position: fixed; width: 22px; height: 22px;
    z-index: 55; pointer-events: none;
    opacity: 0.35;
  }
  .regmark::before, .regmark::after {
    content: ""; position: absolute; background: var(--vermilion);
  }
  .regmark::before { left: 50%; top: 0; bottom: 0; width: 1px; margin-left: -0.5px; }
  .regmark::after  { top: 50%; left: 0; right: 0; height: 1px; margin-top: -0.5px; }
  .regmark.tl { top: 40px;  left: 28px; }
  .regmark.tr { top: 40px;  right: 28px; }
  .regmark.bl { bottom: 96px; left: 28px; }
  .regmark.br { bottom: 96px; right: 28px; }

  /* ─── 舞台 */
  .stage {
    position: relative;
    min-height: 100vh;
    padding: 70px 84px 110px;
    display: grid;
    place-items: center;
    z-index: 2;
  }
  .slide {
    width: min(1080px, 100%);
    position: relative;
    display: none;
  }
  .slide.is-active { display: grid; }
  .slide.is-active {
    grid-template-columns: 88px 1fr 112px;
    column-gap: 36px;
    align-items: start;
  }

  /* 左竖条 · 章节编号(罗马数字 + 中文章) */
  .gutter {
    border-right: 1px solid var(--rule);
    padding-right: 22px;
    text-align: right;
    color: var(--ink-mute);
    font: 12px/1.4 var(--mono);
    letter-spacing: 0.22em;
    min-height: 320px;
  }
  .gutter .roman {
    font-family: var(--display);
    font-size: 56px;
    line-height: 1;
    color: var(--ink);
    margin-bottom: 14px;
    font-weight: 400;
    letter-spacing: 0.04em;
  }
  .gutter .seg { display: block; margin-bottom: 6px; }
  .gutter .total { color: var(--ink-mute); font-size: 11px; }

  /* 右侧档案标签 */
  .filetag {
    border-left: 1px solid var(--rule);
    padding-left: 22px;
    font: 11px/1.5 var(--mono);
    letter-spacing: 0.18em;
    color: var(--ink-mute);
    text-transform: uppercase;
  }
  .filetag .tag-num {
    font-family: var(--display);
    font-size: 28px;
    color: var(--ink); line-height: 1; margin-bottom: 8px;
    letter-spacing: 0;
  }
  .filetag .tag-row { display: block; margin-bottom: 4px; }
  .filetag .stamp {
    margin-top: 24px;
    display: inline-block;
    padding: 6px 10px 5px;
    border: 1.5px solid var(--vermilion);
    color: var(--vermilion);
    font-family: var(--display);
    font-size: 13px; letter-spacing: 0.4em;
    transform: rotate(-3deg);
    line-height: 1;
  }
  .filetag .stamp.olive {
    border-color: var(--olive); color: var(--olive);
  }

  /* 主体 */
  .body { min-width: 0; }
  .eyebrow {
    font: 11px/1 var(--mono);
    letter-spacing: 0.3em;
    color: var(--vermilion);
    margin-bottom: 24px;
    display: flex; align-items: center; gap: 12px;
  }
  .eyebrow::before {
    content: ""; width: 26px; height: 1px; background: var(--vermilion);
  }
  .slide-title {
    font-family: var(--display);
    font-size: 56px; line-height: 1.15; margin: 0 0 36px;
    font-weight: 600; letter-spacing: 0.01em;
    color: var(--ink);
    position: relative;
  }
  .slide-title::after {
    content: ""; display: block; width: 56px; height: 3px;
    background: var(--vermilion); margin-top: 22px;
  }

  /* ─── 数字 hero — auto-detect 在 bullets 里的数字,放大成主视觉 */
  .nums {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 28px 36px; margin: 0 0 36px;
  }
  .num-cell {
    border-top: 1px solid var(--rule);
    padding-top: 16px;
  }
  .num-cell .num-val {
    font-family: var(--display);
    font-size: 64px; line-height: 1;
    color: var(--ink); font-weight: 600;
    letter-spacing: -0.02em;
    display: inline-flex; align-items: baseline; gap: 6px;
  }
  .num-cell .num-unit {
    font-family: var(--sans);
    font-size: 20px; font-weight: 400;
    color: var(--ink-soft);
  }
  .num-cell .num-label {
    margin-top: 10px;
    font: 13px/1.5 var(--sans);
    color: var(--ink-soft); letter-spacing: 0.04em;
  }

  /* ─── tick 印章作 bullet — 不是圆点 */
  .ticklist { list-style: none; padding: 0; margin: 0;
              display: grid; gap: 18px; }
  .ticklist li {
    font-size: 20px; line-height: 1.55;
    padding-left: 44px; position: relative;
    color: var(--ink);
    border-bottom: 1px dashed transparent;
    padding-bottom: 4px;
  }
  .ticklist li::before {
    content: ""; position: absolute; left: 0; top: 8px;
    width: 24px; height: 24px;
    background:
      linear-gradient(45deg, transparent 46%, var(--vermilion) 46% 54%, transparent 54%),
      linear-gradient(-45deg, transparent 64%, var(--vermilion) 64% 72%, transparent 72%);
    background-size: 100% 100%;
    background-position: center;
    border: 1.5px solid var(--vermilion);
    border-radius: 3px;
    transform: rotate(-2deg);
  }
  /* 风险页:tick 换成红斜杠警示 */
  .slide[data-type="risks"] .ticklist li::before,
  .slide[data-type="risk"] .ticklist li::before {
    background:
      linear-gradient(45deg, transparent 46%, var(--vermilion) 46% 54%, transparent 54%),
      linear-gradient(135deg, transparent 46%, var(--vermilion) 46% 54%, transparent 54%);
  }
  .slide[data-type="next_steps"] .ticklist li::before,
  .slide[data-type="next-steps"] .ticklist li::before {
    border-color: var(--olive);
    background:
      linear-gradient(45deg, transparent 46%, var(--olive) 46% 54%, transparent 54%),
      linear-gradient(-45deg, transparent 64%, var(--olive) 64% 72%, transparent 72%);
  }

  /* meta 行(cover 用) */
  .meta {
    margin-top: 38px;
    display: grid; grid-template-columns: repeat(2, 1fr);
    gap: 14px 28px;
    padding-top: 22px; border-top: 1px solid var(--rule);
  }
  .meta dt {
    font: 11px/1 var(--mono); letter-spacing: 0.22em;
    color: var(--ink-mute); margin-bottom: 6px;
  }
  .meta dd {
    margin: 0; font-size: 16px; color: var(--ink);
    font-family: var(--display);
  }

  /* 旁白 */
  .narration {
    margin-top: 32px; padding: 20px 24px;
    background: var(--vermilion-soft);
    border-left: 2px solid var(--vermilion);
    font-size: 15px; line-height: 1.75;
    color: var(--ink-soft);
    font-family: var(--display);
    position: relative;
  }
  .narration::before {
    content: "讲";
    position: absolute; left: -1px; top: -14px;
    width: 26px; height: 26px;
    background: var(--vermilion); color: #fff;
    font-family: var(--display); font-size: 14px;
    display: grid; place-items: center;
    letter-spacing: 0;
  }

  /* 媒体 */
  .media {
    margin-top: 28px; display: grid; gap: 14px;
  }
  .media-label {
    font: 11px/1 var(--mono); letter-spacing: 0.22em;
    color: var(--ink-mute);
  }
  .media video, .media audio {
    width: 100%; background: #000;
    border: 1px solid var(--rule);
  }
  .media video { border-radius: 2px; }
  .media audio { border-radius: 999px; }

  /* ─── cover 页特殊处理 — 深色 + 竖排标题 */
  .slide[data-type="cover"] {
    grid-template-columns: 88px 1fr 112px;
  }
  .slide[data-type="cover"] .body {
    background: #14110b;
    color: #f1ece1;
    padding: 56px 60px;
    margin: -8px -8px;
    border-radius: 2px;
    position: relative;
  }
  .slide[data-type="cover"] .body::before {
    content: ""; position: absolute; inset: 14px;
    border: 1px solid rgba(241,236,225,0.16);
    pointer-events: none;
  }
  .slide[data-type="cover"] .eyebrow { color: var(--vermilion); }
  .slide[data-type="cover"] .eyebrow::before { background: var(--vermilion); }
  .slide[data-type="cover"] .slide-title {
    font-size: 72px; line-height: 1.08; color: #f1ece1;
  }
  .slide[data-type="cover"] .slide-title::after { background: var(--vermilion); }
  .slide[data-type="cover"] .meta {
    border-color: rgba(241,236,225,0.18);
  }
  .slide[data-type="cover"] .meta dt { color: rgba(241,236,225,0.5); }
  .slide[data-type="cover"] .meta dd { color: #f1ece1; }
  .slide[data-type="cover"] .nums {
    grid-template-columns: repeat(2, 1fr);
    gap: 32px 48px;
  }
  .slide[data-type="cover"] .num-cell { border-color: rgba(241,236,225,0.2); }
  .slide[data-type="cover"] .num-cell .num-val { color: #f1ece1; }
  .slide[data-type="cover"] .num-cell .num-unit { color: rgba(241,236,225,0.55); }
  .slide[data-type="cover"] .num-cell .num-label { color: rgba(241,236,225,0.55); }
  .slide[data-type="cover"] .ticklist li { color: #f1ece1; }
  .slide[data-type="cover"] .narration {
    background: rgba(176,48,33,0.16);
    color: #d8d0bf;
  }

  /* ─── review 页 */
  .review-block { margin: 36px 0 0; }
  .review-block h3 {
    margin: 28px 0 14px;
    font: 11px/1 var(--mono);
    letter-spacing: 0.32em;
    color: var(--vermilion);
    padding-bottom: 8px;
    border-bottom: 1px solid var(--rule);
  }
  .review-suggestions { list-style: none; padding: 0; margin: 0;
                        display: grid; gap: 8px; }
  .review-suggestions li {
    padding: 14px 18px 14px 50px;
    background: var(--paper-card);
    border: 1px solid var(--rule);
    font-size: 16px; line-height: 1.6;
    position: relative;
    counter-increment: rev;
  }
  .review-block { counter-reset: rev; }
  .review-suggestions li::before {
    content: counter(rev, decimal-leading-zero);
    position: absolute; left: 14px; top: 14px;
    font: 12px/1 var(--mono);
    letter-spacing: 0.12em;
    color: var(--vermilion);
    background: var(--paper);
    padding: 4px 6px;
    border: 1px solid var(--vermilion);
  }
  .checks {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 10px;
  }
  .checks span {
    font: 12px/1 var(--mono);
    letter-spacing: 0.16em;
    padding: 10px 12px;
    border: 1px solid var(--rule);
    background: var(--paper-card);
    color: var(--ink);
    display: flex; justify-content: space-between; align-items: center;
  }
  .checks span.fail { border-color: var(--vermilion); color: var(--vermilion); }
  .checks span.ok::after  { content: "✓"; color: var(--olive); font-size: 14px; }
  .checks span.fail::after { content: "✗"; color: var(--vermilion); font-size: 14px; }
  .review-meta {
    margin-top: 24px; padding-top: 14px;
    border-top: 1px solid var(--rule);
    display: flex; gap: 28px; flex-wrap: wrap;
    font: 12px/1 var(--mono); letter-spacing: 0.16em;
    color: var(--ink-mute);
  }
  .review-meta b { font-family: var(--display); font-weight: 600;
                   color: var(--ink); letter-spacing: 0; }

  /* ─── 控件 · 底部 */
  .controls {
    position: fixed; left: 0; right: 0; bottom: 0;
    display: flex; align-items: center; justify-content: space-between;
    gap: 14px; padding: 20px 36px;
    background: linear-gradient(to top, var(--paper) 60%, transparent);
    z-index: 40;
    border-top: 1px solid var(--rule-soft);
  }
  .controls .ctl-left, .controls .ctl-right {
    display: flex; align-items: center; gap: 12px;
    font: 12px/1 var(--mono);
    letter-spacing: 0.2em;
    color: var(--ink-mute);
  }
  .controls button {
    border: 1px solid var(--rule);
    background: var(--paper-card);
    color: var(--ink);
    padding: 12px 22px;
    font: 12px/1 var(--mono);
    letter-spacing: 0.22em;
    cursor: pointer;
    transition: all 160ms ease;
  }
  .controls button:hover:not(:disabled) {
    border-color: var(--vermilion);
    color: var(--vermilion);
  }
  .controls button:disabled { opacity: 0.32; cursor: not-allowed; }
  .controls .count {
    font-family: var(--display);
    font-size: 18px; color: var(--ink);
    letter-spacing: 0;
  }
  .controls .count em {
    color: var(--ink-mute); font-style: normal;
    font-size: 13px;
  }
  .controls .kbd {
    display: inline-flex; gap: 4px;
  }
  .controls .kbd kbd {
    font: 10px/1 var(--mono);
    padding: 4px 6px; border: 1px solid var(--rule);
    color: var(--ink-mute); background: var(--paper-card);
    border-radius: 2px;
  }

  /* ─── stagger reveal */
  .slide.is-active .reveal {
    animation: rise 520ms cubic-bezier(0.2, 0.7, 0.2, 1) both;
  }
  .slide.is-active .reveal:nth-child(1) { animation-delay: 40ms; }
  .slide.is-active .reveal:nth-child(2) { animation-delay: 120ms; }
  .slide.is-active .reveal:nth-child(3) { animation-delay: 220ms; }
  .slide.is-active .reveal:nth-child(4) { animation-delay: 320ms; }
  .slide.is-active .reveal:nth-child(5) { animation-delay: 420ms; }
  .slide.is-active .reveal:nth-child(6) { animation-delay: 520ms; }
  @keyframes rise {
    from { opacity: 0; transform: translateY(14px); filter: blur(4px); }
    to   { opacity: 1; transform: none;             filter: none; }
  }
  /* tick 印章逐个盖下 */
  .slide.is-active .ticklist li {
    animation: stamp 360ms cubic-bezier(0.2, 1.6, 0.4, 1) both;
  }
  .slide.is-active .ticklist li:nth-child(1) { animation-delay: 380ms; }
  .slide.is-active .ticklist li:nth-child(2) { animation-delay: 460ms; }
  .slide.is-active .ticklist li:nth-child(3) { animation-delay: 540ms; }
  .slide.is-active .ticklist li:nth-child(4) { animation-delay: 620ms; }
  .slide.is-active .ticklist li:nth-child(5) { animation-delay: 700ms; }
  .slide.is-active .ticklist li:nth-child(6) { animation-delay: 780ms; }
  @keyframes stamp {
    0%   { opacity: 0; transform: translateX(8px) scale(1.02); }
    60%  { opacity: 1; transform: translateX(-1px) scale(0.998); }
    100% { opacity: 1; transform: none; }
  }
  /* gutter 罗马数字渗墨 */
  .slide.is-active .gutter .roman {
    animation: ink 600ms ease both;
    animation-delay: 200ms;
  }
  @keyframes ink {
    from { opacity: 0; filter: blur(8px); transform: translateY(6px); }
    to   { opacity: 1; filter: none;       transform: none; }
  }
  /* 数字 count-up:通过 JS 设置 --num-target,CSS 用 counter() 跟踪 */
  .num-val .num-display {
    display: inline-block;
    font-variant-numeric: tabular-nums;
  }

  /* ─── 翻页过渡:页脚标尺 + 主体 paper-shift */
  .slide.is-leaving .body { animation: leave 220ms ease both; }
  @keyframes leave {
    to { opacity: 0; transform: translateY(-8px); filter: blur(3px); }
  }

  /* prefers-reduced-motion */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 0.01ms !important;
    }
  }

  /* 响应式 */
  @media (max-width: 960px) {
    .stage { padding: 60px 28px 110px; }
    .slide.is-active { grid-template-columns: 1fr; row-gap: 24px; }
    .gutter { border-right: none; border-bottom: 1px solid var(--rule);
              padding-right: 0; padding-bottom: 16px;
              text-align: left; min-height: auto;
              display: flex; align-items: baseline; gap: 18px; }
    .gutter .roman { font-size: 40px; margin-bottom: 0; }
    .filetag { border-left: none; border-top: 1px solid var(--rule);
               padding: 16px 0 0; }
    .filetag .stamp { margin-top: 12px; }
    .slide-title { font-size: 40px; }
    .slide[data-type="cover"] .slide-title { font-size: 48px; }
    .num-cell .num-val { font-size: 48px; }
    .regmark { display: none; }
  }
  @media (max-width: 560px) {
    .controls { padding: 14px 16px; }
    .controls .kbd, .controls .ctl-left .meta-tag { display: none; }
    .slide-title { font-size: 32px; }
    .ticklist li { font-size: 17px; padding-left: 36px; }
  }
</style>
</head>
<body>
  <div class="ruler">
    <div class="ticks" id="ticks"></div>
    <span class="label" id="rulerLabel">第 01 / 08 页</span>
  </div>

  <i class="regmark tl"></i><i class="regmark tr"></i>
  <i class="regmark bl"></i><i class="regmark br"></i>

  <main class="stage" id="stage"></main>

  <nav class="controls">
    <div class="ctl-left">
      <button id="prev">前 一 页</button>
      <span class="kbd"><kbd>←</kbd></span>
    </div>
    <div class="count" id="count">01 <em>/ 08</em></div>
    <div class="ctl-right">
      <span class="kbd"><kbd>→</kbd><kbd>SPACE</kbd></span>
      <button id="next">下 一 页</button>
    </div>
  </nav>

<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  const data = JSON.parse(document.getElementById('data').textContent);
  const slides = data.slides;
  const meta   = data.meta || {};

  const ROMAN = ["Ⅰ","Ⅱ","Ⅲ","Ⅳ","Ⅴ","Ⅵ","Ⅶ","Ⅷ","Ⅸ","Ⅹ","Ⅺ","Ⅻ",
                 "ⅩⅢ","ⅩⅣ","ⅩⅤ","ⅩⅥ"];

  // 类型 → 标签 / 印章字 / accent 颜色
  const TYPE_META = {
    cover:       { label: "封 · COVER",     stamp: "封",   stampClass: "" },
    summary:     { label: "总 · SUMMARY",   stamp: "总",   stampClass: "" },
    detail:      { label: "析 · DETAIL",    stamp: "析",   stampClass: "" },
    completed:   { label: "成 · DELIVERED", stamp: "成",   stampClass: "olive" },
    progress:    { label: "进 · PROGRESS",  stamp: "进",   stampClass: "" },
    risk:        { label: "险 · RISK",      stamp: "险",   stampClass: "" },
    risks:       { label: "险 · RISK",      stamp: "险",   stampClass: "" },
    next_steps:  { label: "续 · NEXT",      stamp: "续",   stampClass: "olive" },
    "next-steps":{ label: "续 · NEXT",      stamp: "续",   stampClass: "olive" },
    review:      { label: "校 · REVIEW",    stamp: "校",   stampClass: "" },
    closing:     { label: "结 · CLOSING",   stamp: "结",   stampClass: "" }
  };

  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) Object.entries(attrs).forEach(([k, v]) => {
      if (v == null) return;
      if (k === 'class') e.className = v;
      else if (k === 'dataset') Object.entries(v).forEach(([dk, dv]) => e.dataset[dk] = dv);
      else if (k === 'html') e.innerHTML = v;
      else e.setAttribute(k, v);
    });
    (children || []).forEach(c => {
      if (c == null || c === false) return;
      if (typeof c === 'string') e.appendChild(document.createTextNode(c));
      else e.appendChild(c);
    });
    return e;
  }

  // ─── 数字识别:从 bullet 文本里抽数字+单位+剩余说明
  // 例:"抽取报告320份"      → {val:"320", unit:"份", label:"抽取报告"}
  //     "识别异常87万元"      → {val:"87",  unit:"万元", label:"识别异常"}
  //     "智能响应1240次"      → {val:"1240",unit:"次",   label:"智能响应"}
  //     "35→11秒"            → {val:"35→11", unit:"秒",  label:""}
  //     "进展正常无延期"      → null(无主数字 → 走 ticklist)
  const NUM_RE = /(\\d+(?:[\\.,]\\d+)?(?:[→\\-~/]\\d+(?:[\\.,]\\d+)?)?)\\s*(份|万元|万|元|次|秒|分钟|分|小时|个|项|条|场|%|‰|h|s)?/;
  function pickNumeric(text) {
    const m = text.match(NUM_RE);
    if (!m) return null;
    const val = m[1]; const unit = m[2] || "";
    // 文本太短就不要拆分 label
    const before = text.slice(0, m.index).trim();
    const after = text.slice(m.index + m[0].length).trim();
    const label = (before || after).replace(/^[，。、,;:：]\\s*/, "").trim();
    return { val, unit, label };
  }
  function bulletsToCells(bullets) {
    const cells = [];
    const fallback = [];
    bullets.forEach(b => {
      const t = (b || "").trim();
      if (!t) return;
      const n = pickNumeric(t);
      if (n && n.val.length <= 8) cells.push(n);
      else fallback.push(t);
    });
    return { cells, fallback };
  }

  // 数字 count-up
  function animateNumber(el, target) {
    // 只处理纯数字(int/小数)— 含箭头的不动画
    const pure = /^\\d+(?:\\.\\d+)?$/.test(target);
    if (!pure) { el.textContent = target; return; }
    const final = parseFloat(target);
    const dur = Math.min(900, 380 + Math.log10(Math.max(final,10)) * 220);
    const start = performance.now();
    const isInt = !target.includes(".");
    function tick(now) {
      const p = Math.min(1, (now - start) / dur);
      // ease-out cubic
      const e = 1 - Math.pow(1 - p, 3);
      const v = final * e;
      el.textContent = isInt ? Math.round(v).toString() : v.toFixed(1);
      if (p < 1) requestAnimationFrame(tick);
      else el.textContent = target;
    }
    requestAnimationFrame(tick);
  }

  function renderSlide(slide, idx, total) {
    const type = (slide.type || (idx === 0 ? "cover" : "detail")).toLowerCase();
    const tmeta = TYPE_META[type] || TYPE_META.detail;
    const isCover  = type === "cover";
    const isReview = type === "review";

    // ── 左竖条 · 章节
    const gutter = el('div', { class: 'gutter' }, [
      el('span', { class: 'roman' }, [ROMAN[idx] || String(idx + 1)]),
      el('span', { class: 'seg' }, [tmeta.label]),
      el('span', { class: 'total' }, [`共 ${String(total).padStart(2, '0')} 页`])
    ]);

    // ── 右档案标签
    const filetag = el('div', { class: 'filetag' }, [
      el('div', { class: 'tag-num' }, [String(idx + 1).padStart(2, '0')]),
      el('span', { class: 'tag-row' }, [meta.duration ? `时长 / ${meta.duration}` : null]),
      el('span', { class: 'tag-row' }, [meta.audience ? `受众 / ${meta.audience}` : null]),
      el('span', { class: 'tag-row' }, [meta.report_type ? `性质 / ${meta.report_type}` : null]),
      el('span', { class: 'stamp' + (tmeta.stampClass ? ' ' + tmeta.stampClass : '') }, [tmeta.stamp])
    ]);

    // ── 主体
    const bodyChildren = [];
    bodyChildren.push(el('div', { class: 'eyebrow reveal' }, [
      isCover ? '汇报正文 / REPORT' : tmeta.label
    ]));
    bodyChildren.push(el('h1', { class: 'slide-title reveal' }, [slide.title || `第 ${idx + 1} 页`]));

    // 数据分流:从 bullets 抽数字 → nums + ticklist
    const { cells, fallback } = bulletsToCells(slide.content || []);

    if (cells.length) {
      const numWrap = el('div', { class: 'nums reveal' }, []);
      cells.forEach(c => {
        const valSpan = el('span', { class: 'num-display' }, []);
        numWrap.appendChild(el('div', { class: 'num-cell' }, [
          el('span', { class: 'num-val' }, [
            valSpan,
            c.unit ? el('span', { class: 'num-unit' }, [c.unit]) : null
          ]),
          c.label ? el('div', { class: 'num-label' }, [c.label]) : null
        ]));
        valSpan.dataset.target = c.val;
      });
      bodyChildren.push(numWrap);
    }
    if (fallback.length) {
      bodyChildren.push(el('ul', { class: 'ticklist reveal' },
        fallback.map(t => el('li', null, [t]))));
    }
    // cover 页加 meta
    if (isCover && meta) {
      bodyChildren.push(el('dl', { class: 'meta reveal' }, [
        el('dt', null, ["性质 · TYPE"]), el('dd', null, [meta.report_type || ""]),
        el('dt', null, ["听众 · AUDIENCE"]), el('dd', null, [meta.audience || ""]),
        el('dt', null, ["时长 · DURATION"]), el('dd', null, [meta.duration || ""]),
        el('dt', null, ["风格 · STYLE"]), el('dd', null, [meta.style || ""])
      ]));
    }
    if (slide.narration) {
      bodyChildren.push(el('div', { class: 'narration reveal' }, [slide.narration]));
    }
    if (slide.media && (slide.media.video || slide.media.audio)) {
      const media = el('div', { class: 'media reveal' }, []);
      if (slide.media.video) {
        media.appendChild(el('div', { class: 'media-label' }, ["数字人开场 / VIDEO"]));
        media.appendChild(el('video', { controls: '', preload: 'metadata', src: slide.media.video }, []));
      }
      if (slide.media.audio) {
        media.appendChild(el('div', { class: 'media-label' }, ["本页配音 / AUDIO"]));
        media.appendChild(el('audio', { controls: '', preload: 'metadata', src: slide.media.audio }, []));
      }
      bodyChildren.push(media);
    }
    if (isReview && slide.review) {
      const r = slide.review;
      const block = el('div', { class: 'review-block reveal' }, []);
      if (r.suggestions && r.suggestions.length) {
        block.appendChild(el('h3', null, ["可执行建议 · ACTIONS"]));
        block.appendChild(el('ul', { class: 'review-suggestions' },
          r.suggestions.map(s => el('li', null, [typeof s === 'string' ? s : (s.what || JSON.stringify(s))]))));
      }
      if (r.checks) {
        block.appendChild(el('h3', null, ["质量校验 · CHECKLIST"]));
        const checks = el('div', { class: 'checks' }, []);
        const labels = {
          has_summary: '总结', key_points_ok: '重点', has_risks: '风险',
          risks_have_impact: '影响', has_next_steps: '下一步',
          support_clear: '诉求', length_ok: '时长', audience_fit: '听众',
          supplement_check: '补充对账'
        };
        Object.entries(r.checks).forEach(([k, v]) => {
          checks.appendChild(el('span', { class: v ? 'ok' : 'fail' },
            [labels[k] || k]));
        });
        block.appendChild(checks);
      }
      const metaRow = [];
      if (r.estimated_duration) metaRow.push(el('span', null, ["预估 ", el('b', null, [r.estimated_duration])]));
      if (r.ai_signal_score != null) metaRow.push(el('span', null, ["AI 信号 ", el('b', null, [String(r.ai_signal_score)])]));
      if (metaRow.length) block.appendChild(el('div', { class: 'review-meta' }, metaRow));
      bodyChildren.push(block);
    }

    const body = el('div', { class: 'body' }, bodyChildren);
    return el('section', {
      class: 'slide' + (idx === 0 ? ' is-active' : ''),
      dataset: { type, idx: String(idx) }
    }, [gutter, body, filetag]);
  }

  const stage = document.getElementById('stage');
  const ticksEl = document.getElementById('ticks');
  const count = document.getElementById('count');
  const rulerLabel = document.getElementById('rulerLabel');
  const prevBtn = document.getElementById('prev');
  const nextBtn = document.getElementById('next');

  slides.forEach((s, i) => stage.appendChild(renderSlide(s, i, slides.length)));
  // 顶端 ruler 刻度 — 每页一刻
  // 总刻度 = 5 × pages,使 5n+1 刻自然落到页起点
  const totalTicks = slides.length * 5;
  for (let i = 0; i < totalTicks; i++) {
    const tk = el('i', null, []);
    // 5n+1 那一刻是 page 起点,点击跳转
    if (i % 5 === 0) {
      tk.addEventListener('click', () => go(i / 5));
      tk.style.cursor = 'pointer';
    }
    ticksEl.appendChild(tk);
  }

  let cur = 0;
  function paintTicks() {
    const ticks = ticksEl.querySelectorAll('i');
    ticks.forEach((t, i) => {
      const page = Math.floor(i / 5);
      t.classList.toggle('is-passed',  page < cur);
      t.classList.toggle('is-current', page === cur && (i % 5 === 0));
    });
  }
  function fireNumberAnims(slideEl) {
    slideEl.querySelectorAll('.num-display').forEach(span => {
      const t = span.dataset.target || "";
      // 延迟 280ms 让 reveal 先到位
      setTimeout(() => animateNumber(span, t), 320);
    });
  }
  function go(i) {
    cur = Math.max(0, Math.min(slides.length - 1, i));
    stage.querySelectorAll('.slide').forEach((s, idx) => {
      const active = idx === cur;
      s.classList.toggle('is-active', active);
      if (active) fireNumberAnims(s);
    });
    count.innerHTML = String(cur + 1).padStart(2, '0') + ' <em>/ ' +
                       String(slides.length).padStart(2, '0') + '</em>';
    rulerLabel.textContent = '第 ' + String(cur + 1).padStart(2, '0') +
                              ' / ' + String(slides.length).padStart(2, '0') + ' 页';
    prevBtn.disabled = cur === 0;
    nextBtn.disabled = cur === slides.length - 1;
    paintTicks();
  }

  prevBtn.addEventListener('click', () => go(cur - 1));
  nextBtn.addEventListener('click', () => go(cur + 1));
  document.addEventListener('keydown', e => {
    if (e.target && /input|textarea/i.test(e.target.tagName)) return;
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') go(cur - 1);
    if (e.key === 'ArrowRight' || e.key === 'PageDown' || e.key === ' ') {
      e.preventDefault(); go(cur + 1);
    }
    if (e.key === 'Home') go(0);
    if (e.key === 'End') go(slides.length - 1);
  });
  // 触屏 swipe
  let tx0 = null;
  document.addEventListener('touchstart', e => { tx0 = e.touches[0].clientX; }, { passive: true });
  document.addEventListener('touchend', e => {
    if (tx0 == null) return;
    const dx = e.changedTouches[0].clientX - tx0;
    if (Math.abs(dx) > 60) go(cur + (dx < 0 ? 1 : -1));
    tx0 = null;
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
        # 首页 → intro_video(数字人开场),末页 → outro_video(数字人结尾)
        if video_meta:
            is_first = (idx == 0)
            is_last  = (idx == total - 1)
            avatar_key = "intro_video" if is_first else ("outro_video" if is_last else None)
            if avatar_key:
                av = video_meta.get(avatar_key) or {}
                av_path = av.get("path") if isinstance(av, dict) and av.get("ok") else None
                if av_path:
                    p = Path(av_path)
                    if p.is_absolute() and p.exists():
                        video_url = p.resolve().as_uri()
                    else:
                        video_url = _relpath_to(av_path, output_root / "web-presentation")
                    media = {
                        "video": video_url,
                        "video_duration": float(av.get("duration") or 0),
                        "kind": "avatar",
                    }
                    # 数字人外接配音(Kling 视频本身无声,backend TTS 跑出来的 mp3)
                    audio_path = av.get("audio_path") if isinstance(av, dict) else None
                    if audio_path:
                        ap = Path(audio_path)
                        if ap.is_absolute() and ap.exists():
                            media["audio"] = ap.resolve().as_uri()
                            media["audio_duration"] = float(av.get("audio_duration") or 0)
                    slide["media"] = media
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

    # ─── 写 presenter view(讲者用,带 narration / controls / review)
    project_dir = output_root / "web-presentation"
    project_dir.mkdir(parents=True, exist_ok=True)

    presenter_html = (
        _HTML_TEMPLATE
        .replace("__TITLE__", escape(title or "会汇报"))
        .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
    )
    presenter_path = project_dir / "index.html"
    presenter_path.write_text(presenter_html, encoding="utf-8")

    presenter_audit = _audit_presentation(payload, presenter_html)
    presenter_audit["view"] = "presenter"
    audit_path = project_dir / "audit.json"
    audit_path.write_text(json.dumps(presenter_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    audit = presenter_audit  # 兼容旧字段

    # ─── 写 broadcast view(录屏用,纯画面 + 自动翻页 + 内嵌音频)
    # broadcast 不显示 review block / narration / controls / kbd
    broadcast_payload = dict(payload)
    broadcast_slides: list[dict] = []
    for s in slides_out:
        bs = dict(s)
        bs.pop("review", None)          # review 末页给讲者看,录屏不要
        bs["_narration_hidden"] = bs.pop("narration", "")  # 保留时长但不显示
        broadcast_slides.append(bs)
    broadcast_payload["slides"] = broadcast_slides
    # 给 audio 段挂 file:// URL — 同机录屏专用,broadcast.html 不再 self-contained
    # 但能避开 base64 4MB 限制,长 narration 不再静音
    audio_seg_meta = []
    referenced_total_bytes = 0
    if video_meta and isinstance(video_meta, dict):
        for seg in (video_meta.get("audio_segments") or []):
            if not isinstance(seg, dict):
                continue
            p = seg.get("path") or ""
            data_url = ""
            seg_path: Path | None = None
            if p:
                pp = Path(p)
                if pp.is_absolute() and pp.exists():
                    seg_path = pp
                else:
                    # 归一化:audio_dir/<name> 或 repo-root 相对
                    candidate = output_root / "audio" / pp.name
                    if candidate.exists():
                        seg_path = candidate
                    else:
                        repo_root = output_root.parent.parent
                        candidate2 = (repo_root / pp).resolve()
                        if candidate2.exists():
                            seg_path = candidate2
            if seg_path and seg.get("ok"):
                data_url = _audio_file_url(seg_path)
                if data_url:
                    referenced_total_bytes += seg_path.stat().st_size
            audio_seg_meta.append({
                "index": seg.get("index"),
                "src": data_url,        # file:///abs/path/01.mp3  或空串(无音/失败)
                "duration": seg.get("duration_estimate_sec") or seg.get("duration") or 0,
                "ok": bool(data_url),
            })
    broadcast_payload["audio_segments"] = audio_seg_meta
    broadcast_payload["_audio_total_kb"] = round(referenced_total_bytes / 1024, 1)

    broadcast_html = (
        _BROADCAST_TEMPLATE
        .replace("__TITLE__", escape(title or "会汇报"))
        .replace("__DATA__", json.dumps(broadcast_payload, ensure_ascii=False))
    )
    broadcast_path = project_dir / "broadcast.html"
    broadcast_path.write_text(broadcast_html, encoding="utf-8")

    broadcast_audit = _audit_presentation(broadcast_payload, broadcast_html)
    broadcast_audit["view"] = "broadcast"
    broadcast_audit_path = project_dir / "broadcast_audit.json"
    broadcast_audit_path.write_text(
        json.dumps(broadcast_audit, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "path": str(presenter_path),
        "rel_path": "web-presentation/index.html",
        "broadcast_path": str(broadcast_path),
        "broadcast_rel_path": "web-presentation/broadcast.html",
        "audit_path": str(audit_path),
        "audit_rel_path": "web-presentation/audit.json",
        "broadcast_audit_path": str(broadcast_audit_path),
        "broadcast_audit_rel_path": "web-presentation/broadcast_audit.json",
        "pages": len(slides_out),
        "bytes": presenter_path.stat().st_size,
        "broadcast_bytes": broadcast_path.stat().st_size,
        "audit": audit,
        "broadcast_audit": broadcast_audit,
    }


# ────────────── broadcast.html · 录屏专用纯画面 view ──────────────
# 设计差异(相对 _HTML_TEMPLATE):
#   - 锁 1920×1080 viewport(便于 playwright 录屏一次到位)
#   - 无 narration / review block / controls / kbd hints / ruler-label
#   - 数字 hero + tick + 印章语汇全保留 + 章节 gutter / regmark 保留
#   - 自动翻页(每页停留时长 = audio_segments[i].duration,无音 6s)
#   - inline audio 串联 autoplay(playwright 能录到声音)
#   - 暴露 window.__broadcastReady / __broadcastDone 给 playwright 等候
_BROADCAST_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>__TITLE__</title>
<style>
  :root {
    --paper: #f1ece1;
    --paper-card: #faf5e9;
    --ink: #1f1a14;
    --ink-soft: #6b5e4a;
    --ink-mute: #9a8e76;
    --rule: #d8cdb2;
    --rule-soft: #e6dfc8;
    --vermilion: #b03021;
    --vermilion-soft: rgba(176, 48, 33, 0.12);
    --olive: #5a6b3a;
    --display: "Source Han Serif SC", "Noto Serif SC", "STSong",
               "SimSun", "Songti SC", "Times New Roman", serif;
    --sans: "PingFang SC", "Noto Sans SC", "Source Han Sans SC",
            "Microsoft YaHei", -apple-system, BlinkMacSystemFont, sans-serif;
    --mono: "IBM Plex Mono", "JetBrains Mono", "SF Mono",
            "Cascadia Mono", Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  html, body { width: 1920px; height: 1080px; overflow: hidden; }
  body {
    font: 28px/1.55 var(--sans);
    color: var(--ink);
    background:
      radial-gradient(circle at 18% 12%, rgba(176,48,33,0.04), transparent 38%),
      radial-gradient(circle at 82% 78%, rgba(90,107,58,0.05), transparent 42%),
      var(--paper);
    letter-spacing: 0.01em;
  }
  body::before {
    content: "";
    position: fixed; inset: 0;
    background-image:
      repeating-linear-gradient(0deg, rgba(31,26,20,0.018) 0 1px, transparent 1px 3px),
      repeating-linear-gradient(90deg, rgba(31,26,20,0.014) 0 1px, transparent 1px 4px);
    pointer-events: none; z-index: 1;
    mix-blend-mode: multiply;
  }

  /* 顶部刻度条(无文字 label,只刻度) */
  .ruler {
    position: fixed; top: 0; left: 0; right: 0; height: 6px;
    z-index: 60; display: flex; gap: 2px;
    padding: 0 36px;
    background: linear-gradient(to bottom, var(--paper) 70%, transparent);
  }
  .ruler i {
    flex: 1; background: var(--rule);
    transition: background 320ms ease;
  }
  .ruler i.is-passed { background: var(--vermilion); }
  .ruler i.is-current {
    background: var(--vermilion);
    box-shadow: 0 0 12px rgba(176,48,33,0.6);
  }

  /* 配准十字 — 录屏可见 */
  .regmark {
    position: fixed; width: 28px; height: 28px;
    z-index: 55; pointer-events: none; opacity: 0.4;
  }
  .regmark::before, .regmark::after {
    content: ""; position: absolute; background: var(--vermilion);
  }
  .regmark::before { left: 50%; top: 0; bottom: 0; width: 1px; margin-left: -0.5px; }
  .regmark::after  { top: 50%; left: 0; right: 0; height: 1px; margin-top: -0.5px; }
  .regmark.tl { top: 32px;  left: 36px; }
  .regmark.tr { top: 32px;  right: 36px; }
  .regmark.bl { bottom: 32px; left: 36px; }
  .regmark.br { bottom: 32px; right: 36px; }

  /* ── 数字人 overlay — 首末页 avatar 视频全屏盖在 slide 内容上(录屏时是主舞台) */
  .avatar-overlay {
    position: absolute; inset: 0;
    z-index: 50;
    background: var(--paper);
    display: flex; align-items: center; justify-content: center;
    overflow: hidden;
  }
  .avatar-overlay .avatar-video {
    width: 100%; height: 100%;
    object-fit: cover;
    background: #0c0a08;
  }
  /* 数字人页 — 隐藏底层 slide content + ruler + regmark(视觉只剩数字人画面) */
  .slide.has-avatar .body,
  .slide.has-avatar .gutter,
  .slide.has-avatar .filetag {
    visibility: hidden;
  }

  /* 舞台 — 全屏单 slide */
  .stage { position: relative; width: 100%; height: 100%;
           padding: 92px 120px 92px; z-index: 2; }
  .slide {
    display: none;
    height: 100%;
    grid-template-columns: 120px 1fr 140px;
    column-gap: 56px;
    align-items: start;
  }
  .slide.is-active { display: grid; }

  /* 章节 gutter */
  .gutter {
    border-right: 1px solid var(--rule);
    padding-right: 28px; text-align: right;
    color: var(--ink-mute);
    font: 14px/1.4 var(--mono);
    letter-spacing: 0.22em;
  }
  .gutter .roman {
    font-family: var(--display); font-size: 84px; line-height: 1;
    color: var(--ink); margin-bottom: 16px; letter-spacing: 0.04em;
  }
  .gutter .seg { display: block; margin-bottom: 6px; }
  .gutter .total { color: var(--ink-mute); font-size: 13px; }

  /* 档案标签 — 录屏页:保留印章 + 页码,小字 meta 也保留点纯印刷感 */
  .filetag {
    border-left: 1px solid var(--rule); padding-left: 28px;
    font: 13px/1.5 var(--mono);
    letter-spacing: 0.18em; color: var(--ink-mute);
    text-transform: uppercase;
  }
  .filetag .tag-num {
    font-family: var(--display); font-size: 42px; color: var(--ink);
    line-height: 1; margin-bottom: 12px;
  }
  .filetag .tag-row { display: block; margin-bottom: 6px; }
  .filetag .stamp {
    margin-top: 32px; display: inline-block;
    padding: 8px 14px 7px;
    border: 2px solid var(--vermilion); color: var(--vermilion);
    font-family: var(--display); font-size: 18px; letter-spacing: 0.4em;
    transform: rotate(-3deg); line-height: 1;
  }
  .filetag .stamp.olive { border-color: var(--olive); color: var(--olive); }

  /* 主体 */
  .body { min-width: 0; padding-top: 8px; }
  .eyebrow {
    font: 16px/1 var(--mono);
    letter-spacing: 0.3em; color: var(--vermilion);
    margin-bottom: 32px;
    display: flex; align-items: center; gap: 16px;
  }
  .eyebrow::before { content: ""; width: 40px; height: 1px; background: var(--vermilion); }
  .slide-title {
    font-family: var(--display);
    font-size: 80px; line-height: 1.12; margin: 0 0 48px;
    font-weight: 600; letter-spacing: 0.01em; color: var(--ink);
    position: relative;
  }
  .slide-title::after {
    content: ""; display: block; width: 76px; height: 4px;
    background: var(--vermilion); margin-top: 28px;
  }

  /* 数字 hero — 录屏页字号更大 */
  .nums { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
          gap: 36px 56px; margin: 0 0 48px; }
  .num-cell { border-top: 1px solid var(--rule); padding-top: 20px; }
  .num-cell .num-val {
    font-family: var(--display); font-size: 96px; line-height: 1;
    color: var(--ink); font-weight: 600; letter-spacing: -0.02em;
    display: inline-flex; align-items: baseline; gap: 8px;
  }
  .num-cell .num-unit {
    font-family: var(--sans); font-size: 30px; font-weight: 400; color: var(--ink-soft);
  }
  .num-cell .num-label {
    margin-top: 14px; font: 18px/1.5 var(--sans);
    color: var(--ink-soft); letter-spacing: 0.04em;
  }

  /* tick list */
  .ticklist { list-style: none; padding: 0; margin: 0;
              display: grid; gap: 22px; }
  .ticklist li {
    font-size: 30px; line-height: 1.5;
    padding-left: 56px; position: relative; color: var(--ink);
  }
  .ticklist li::before {
    content: ""; position: absolute; left: 0; top: 10px;
    width: 32px; height: 32px;
    background:
      linear-gradient(45deg, transparent 46%, var(--vermilion) 46% 54%, transparent 54%),
      linear-gradient(-45deg, transparent 64%, var(--vermilion) 64% 72%, transparent 72%);
    background-size: 100% 100%; background-position: center;
    border: 2px solid var(--vermilion); border-radius: 4px;
    transform: rotate(-2deg);
  }
  .slide[data-type="risk"] .ticklist li::before,
  .slide[data-type="risks"] .ticklist li::before {
    background:
      linear-gradient(45deg, transparent 46%, var(--vermilion) 46% 54%, transparent 54%),
      linear-gradient(135deg, transparent 46%, var(--vermilion) 46% 54%, transparent 54%);
  }
  .slide[data-type="next_steps"] .ticklist li::before,
  .slide[data-type="next-steps"] .ticklist li::before {
    border-color: var(--olive);
    background:
      linear-gradient(45deg, transparent 46%, var(--olive) 46% 54%, transparent 54%),
      linear-gradient(-45deg, transparent 64%, var(--olive) 64% 72%, transparent 72%);
  }

  /* meta(cover 用) */
  .meta {
    margin-top: 56px;
    display: grid; grid-template-columns: repeat(2, 1fr);
    gap: 20px 36px;
    padding-top: 28px; border-top: 1px solid var(--rule);
  }
  .meta dt {
    font: 14px/1 var(--mono); letter-spacing: 0.22em;
    color: var(--ink-mute); margin-bottom: 8px;
  }
  .meta dd { margin: 0; font-size: 22px; color: var(--ink); font-family: var(--display); }

  /* cover 深色版 */
  .slide[data-type="cover"] .body {
    background: #14110b; color: #f1ece1;
    padding: 72px 80px; border-radius: 4px; position: relative;
    height: 100%;
  }
  .slide[data-type="cover"] .body::before {
    content: ""; position: absolute; inset: 18px;
    border: 1px solid rgba(241,236,225,0.16);
    pointer-events: none;
  }
  .slide[data-type="cover"] .eyebrow { color: var(--vermilion); }
  .slide[data-type="cover"] .eyebrow::before { background: var(--vermilion); }
  .slide[data-type="cover"] .slide-title { font-size: 108px; line-height: 1.08; color: #f1ece1; }
  .slide[data-type="cover"] .slide-title::after { background: var(--vermilion); }
  .slide[data-type="cover"] .nums { grid-template-columns: repeat(2, 1fr); gap: 44px 64px; }
  .slide[data-type="cover"] .num-cell { border-color: rgba(241,236,225,0.2); }
  .slide[data-type="cover"] .num-cell .num-val { color: #f1ece1; }
  .slide[data-type="cover"] .num-cell .num-unit { color: rgba(241,236,225,0.55); }
  .slide[data-type="cover"] .num-cell .num-label { color: rgba(241,236,225,0.55); }
  .slide[data-type="cover"] .ticklist li { color: #f1ece1; }
  .slide[data-type="cover"] .meta { border-color: rgba(241,236,225,0.18); }
  .slide[data-type="cover"] .meta dt { color: rgba(241,236,225,0.5); }
  .slide[data-type="cover"] .meta dd { color: #f1ece1; }

  /* 入场动画(stagger) */
  .slide.is-active .reveal {
    animation: rise 560ms cubic-bezier(0.2, 0.7, 0.2, 1) both;
  }
  .slide.is-active .reveal:nth-child(1) { animation-delay: 60ms; }
  .slide.is-active .reveal:nth-child(2) { animation-delay: 140ms; }
  .slide.is-active .reveal:nth-child(3) { animation-delay: 240ms; }
  .slide.is-active .reveal:nth-child(4) { animation-delay: 340ms; }
  .slide.is-active .reveal:nth-child(5) { animation-delay: 440ms; }
  @keyframes rise {
    from { opacity: 0; transform: translateY(18px); filter: blur(5px); }
    to   { opacity: 1; transform: none; filter: none; }
  }
  .slide.is-active .ticklist li {
    animation: stamp 380ms cubic-bezier(0.2, 1.6, 0.4, 1) both;
  }
  .slide.is-active .ticklist li:nth-child(1) { animation-delay: 460ms; }
  .slide.is-active .ticklist li:nth-child(2) { animation-delay: 560ms; }
  .slide.is-active .ticklist li:nth-child(3) { animation-delay: 660ms; }
  .slide.is-active .ticklist li:nth-child(4) { animation-delay: 760ms; }
  @keyframes stamp {
    0%   { opacity: 0; transform: translateX(8px) scale(1.02); }
    60%  { opacity: 1; transform: translateX(-1px) scale(0.998); }
    100% { opacity: 1; transform: none; }
  }
  .num-val .num-display { display: inline-block; font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
  <div class="ruler" id="ruler"></div>
  <i class="regmark tl"></i><i class="regmark tr"></i>
  <i class="regmark bl"></i><i class="regmark br"></i>
  <main class="stage" id="stage"></main>
  <audio id="audio" preload="auto"></audio>

<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  const data = JSON.parse(document.getElementById('data').textContent);
  const slides = data.slides || [];
  const meta = data.meta || {};
  const audioSegs = data.audio_segments || [];

  const ROMAN = ["Ⅰ","Ⅱ","Ⅲ","Ⅳ","Ⅴ","Ⅵ","Ⅶ","Ⅷ","Ⅸ","Ⅹ","Ⅺ","Ⅻ"];
  const TYPE_META = {
    cover: { label: "封 · COVER", stamp: "封", stampClass: "" },
    summary: { label: "总 · SUMMARY", stamp: "总", stampClass: "" },
    detail: { label: "析 · DETAIL", stamp: "析", stampClass: "" },
    completed: { label: "成 · DELIVERED", stamp: "成", stampClass: "olive" },
    completed_detail: { label: "成 · DETAIL", stamp: "成", stampClass: "olive" },
    progress: { label: "进 · PROGRESS", stamp: "进", stampClass: "" },
    risk: { label: "险 · RISK", stamp: "险", stampClass: "" },
    risks: { label: "险 · RISK", stamp: "险", stampClass: "" },
    next_steps: { label: "续 · NEXT", stamp: "续", stampClass: "olive" },
    "next-steps": { label: "续 · NEXT", stamp: "续", stampClass: "olive" },
    closing: { label: "结 · CLOSING", stamp: "结", stampClass: "" },
  };

  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) Object.entries(attrs).forEach(([k, v]) => {
      if (v == null) return;
      if (k === 'class') e.className = v;
      else if (k === 'dataset') Object.entries(v).forEach(([dk, dv]) => e.dataset[dk] = dv);
      else if (k === 'html') e.innerHTML = v;
      else e.setAttribute(k, v);
    });
    (children || []).forEach(c => {
      if (c == null || c === false) return;
      if (typeof c === 'string') e.appendChild(document.createTextNode(c));
      else e.appendChild(c);
    });
    return e;
  }

  const NUM_RE = /(\\d+(?:[\\.,]\\d+)?(?:[→\\-~/]\\d+(?:[\\.,]\\d+)?)?)\\s*(份|万元|万|元|次|秒|分钟|分|小时|个|项|条|场|%|‰|h|s)?/;
  function pickNumeric(text) {
    const m = text.match(NUM_RE); if (!m) return null;
    const val = m[1]; const unit = m[2] || "";
    const before = text.slice(0, m.index).trim();
    const after = text.slice(m.index + m[0].length).trim();
    const label = (before || after).replace(/^[，。、,;:：]\\s*/, "").trim();
    return { val, unit, label };
  }
  function bulletsToCells(bullets) {
    const cells = [], fallback = [];
    bullets.forEach(b => {
      const t = (b || "").trim(); if (!t) return;
      const n = pickNumeric(t);
      if (n && n.val.length <= 8) cells.push(n); else fallback.push(t);
    });
    return { cells, fallback };
  }
  function animateNumber(el, target) {
    const pure = /^\\d+(?:\\.\\d+)?$/.test(target);
    if (!pure) { el.textContent = target; return; }
    const final = parseFloat(target);
    const dur = Math.min(900, 380 + Math.log10(Math.max(final, 10)) * 220);
    const start = performance.now();
    const isInt = !target.includes(".");
    function tick(now) {
      const p = Math.min(1, (now - start) / dur);
      const e = 1 - Math.pow(1 - p, 3);
      const v = final * e;
      el.textContent = isInt ? Math.round(v).toString() : v.toFixed(1);
      if (p < 1) requestAnimationFrame(tick); else el.textContent = target;
    }
    requestAnimationFrame(tick);
  }

  function renderSlide(slide, idx, total) {
    const type = (slide.type || (idx === 0 ? "cover" : "detail")).toLowerCase();
    const tmeta = TYPE_META[type] || TYPE_META.detail;

    const gutter = el('div', { class: 'gutter' }, [
      el('span', { class: 'roman' }, [ROMAN[idx] || String(idx + 1)]),
      el('span', { class: 'seg' }, [tmeta.label]),
      el('span', { class: 'total' }, ["共 " + String(total).padStart(2, '0') + " 页"])
    ]);
    const filetag = el('div', { class: 'filetag' }, [
      el('div', { class: 'tag-num' }, [String(idx + 1).padStart(2, '0')]),
      el('span', { class: 'tag-row' }, [meta.duration ? "时长 / " + meta.duration : null]),
      el('span', { class: 'tag-row' }, [meta.audience ? "受众 / " + meta.audience : null]),
      el('span', { class: 'stamp' + (tmeta.stampClass ? ' ' + tmeta.stampClass : '') }, [tmeta.stamp])
    ]);

    const bodyKids = [
      el('div', { class: 'eyebrow reveal' }, [
        (idx === 0) ? '汇报正文 / REPORT' : tmeta.label
      ]),
      el('h1', { class: 'slide-title reveal' }, [slide.title || ('第 ' + (idx + 1) + ' 页')])
    ];
    const { cells, fallback } = bulletsToCells(slide.content || []);
    if (cells.length) {
      const numWrap = el('div', { class: 'nums reveal' }, []);
      cells.forEach(c => {
        const valSpan = el('span', { class: 'num-display' }, []);
        numWrap.appendChild(el('div', { class: 'num-cell' }, [
          el('span', { class: 'num-val' }, [
            valSpan,
            c.unit ? el('span', { class: 'num-unit' }, [c.unit]) : null
          ]),
          c.label ? el('div', { class: 'num-label' }, [c.label]) : null
        ]));
        valSpan.dataset.target = c.val;
      });
      bodyKids.push(numWrap);
    }
    if (fallback.length) {
      bodyKids.push(el('ul', { class: 'ticklist reveal' },
        fallback.map(t => el('li', null, [t]))));
    }
    if (idx === 0 && meta) {
      bodyKids.push(el('dl', { class: 'meta reveal' }, [
        el('dt', null, ["性质 · TYPE"]), el('dd', null, [meta.report_type || ""]),
        el('dt', null, ["听众 · AUDIENCE"]), el('dd', null, [meta.audience || ""]),
        el('dt', null, ["时长 · DURATION"]), el('dd', null, [meta.duration || ""]),
        el('dt', null, ["风格 · STYLE"]), el('dd', null, [meta.style || ""])
      ]));
    }
    // 首末页有数字人镜头 → 加 avatar overlay 全屏盖在 slide 上
    const isAvatarSlide = slide.media && slide.media.video && slide.media.kind === 'avatar';
    const children = [gutter, el('div', { class: 'body' }, bodyKids), filetag];
    if (isAvatarSlide) {
      const vid = el('video', {
        class: 'avatar-video',
        src: slide.media.video,
        preload: 'auto',
        playsinline: '',
        muted: '',          // 初始静音保证 autoplay 不被拦,加载后 unmute
      }, []);
      children.push(el('div', { class: 'avatar-overlay', dataset: { idx: String(idx) } }, [vid]));
    }
    return el('section', {
      class: 'slide' + (idx === 0 ? ' is-active' : '') + (isAvatarSlide ? ' has-avatar' : ''),
      dataset: { type, idx: String(idx) }
    }, children);
  }

  const stage = document.getElementById('stage');
  const ruler = document.getElementById('ruler');
  slides.forEach((s, i) => stage.appendChild(renderSlide(s, i, slides.length)));
  slides.forEach(() => ruler.appendChild(el('i', null, [])));

  let cur = -1;
  function paint() {
    const ticks = ruler.querySelectorAll('i');
    ticks.forEach((t, i) => {
      t.classList.toggle('is-passed', i < cur);
      t.classList.toggle('is-current', i === cur);
    });
  }
  function fireNumberAnims(slideEl) {
    slideEl.querySelectorAll('.num-display').forEach(span => {
      const t = span.dataset.target || "";
      setTimeout(() => animateNumber(span, t), 380);
    });
  }
  function go(i) {
    if (i >= slides.length) {
      window.__broadcastDone = true;
      return;
    }
    cur = i;
    stage.querySelectorAll('.slide').forEach((s, idx) => {
      const active = idx === i;
      s.classList.toggle('is-active', active);
      if (active) fireNumberAnims(s);
    });
    paint();
  }

  // ── 自动翻页 · 三种情况:
  //   1) avatar slide(首末页):用 slide 内的 <video> 元素,onended → 翻下一页
  //   2) 中间页 + audio_segments[i] 可用:audio.src = file://...,onplaying 才开始翻页倒计时,
  //      实际翻页用 audio.onended 触发(精度高)
  //   3) 无 audio:用 duration_estimate_sec fallback timer
  const audioEl = document.getElementById('audio');
  audioEl.muted = false;
  audioEl.preload = 'auto';
  const DEFAULT_DUR = 6.0;

  // ── audio 段预加载 + 拿真实 duration(估算的 duration_estimate_sec 偏差大)
  // 预热到 ArrayBuffer 而不只是 metadata,切换时无 buffering 卡顿(对齐精度关键)
  const audioCache = new Map();   // i → preloaded HTMLAudioElement(已 readyState=4)
  async function preloadAudios() {
    const probes = audioSegs.map((seg, i) => new Promise((resolve) => {
      if (!seg || !seg.src) { resolve({i, d: 0}); return; }
      const a = new Audio();
      a.preload = 'auto';
      a.src = seg.src;
      const done = (d) => { audioCache.set(i, a); resolve({i, d}); };
      a.addEventListener('canplaythrough', () => done(a.duration || 0), {once: true});
      a.addEventListener('error', () => resolve({i, d: 0}), {once: true});
      setTimeout(() => done(a.duration || 0), 3500);    // 3.5s 超时,放宽给 file:// 大文件
    }));
    const results = await Promise.all(probes);
    results.forEach(({i, d}) => {
      if (d > 0 && audioSegs[i]) audioSegs[i].duration = d;
    });
  }

  // 预探 avatar 视频时长(写到 slides[i].media.duration)
  async function preloadAvatarDurations() {
    const probes = slides.map((s, i) => new Promise((resolve) => {
      if (!s.media || !s.media.video || s.media.kind !== 'avatar') { resolve({i, d: 0}); return; }
      // 实际 slide DOM 里已有 video 元素,直接读
      const sect = stage.querySelectorAll('.slide')[i];
      const v = sect && sect.querySelector('video.avatar-video');
      if (!v) { resolve({i, d: 0}); return; }
      if (v.readyState >= 1 && v.duration > 0) { resolve({i, d: v.duration}); return; }
      v.addEventListener('loadedmetadata', () => resolve({i, d: v.duration || 0}), {once: true});
      v.addEventListener('error', () => resolve({i, d: 0}), {once: true});
      setTimeout(() => resolve({i, d: v.duration || 0}), 3500);
    }));
    const results = await Promise.all(probes);
    results.forEach(({i, d}) => {
      if (d > 0 && slides[i] && slides[i].media) slides[i].media.duration = d;
    });
  }

  function playSlide(i) {
    if (i >= slides.length) { window.__broadcastDone = true; return; }
    go(i);

    const slide = slides[i];
    const isAvatar = slide.media && slide.media.video && slide.media.kind === 'avatar';

    if (isAvatar) {
      // 数字人页 — video 是无音的"嘴动"镜头,音频走外接 audio(backend TTS 配音)
      // 翻页时机以 audio(真实讲话长度)为准,video 短(5s)就 loop 直到 audio 结束
      const sect = stage.querySelectorAll('.slide')[i];
      const vid = sect && sect.querySelector('video.avatar-video');
      if (!vid) { setTimeout(() => playSlide(i + 1), 500); return; }
      vid.muted = true;
      vid.loop = true;
      vid.currentTime = 0;
      const vP = vid.play();
      if (vP && vP.catch) vP.catch(() => {});

      let advanced = false;
      const advance = () => { if (!advanced) { advanced = true; vid.loop = false; playSlide(i + 1); } };

      if (slide.media.audio) {
        // 用独立 Audio 元素播配音
        const a = new Audio();
        a.src = slide.media.audio;
        a.preload = 'auto';
        a.onended = advance;
        // 兜底:audio_duration + 1s,防 onended 不触发
        const audur = (slide.media.audio_duration || 0) || 25.0;
        setTimeout(advance, (audur + 1.0) * 1000);
        const aP = a.play();
        if (aP && aP.catch) aP.catch(() => {});
      } else {
        // 没配音 — fallback 用 video.onended 触发(loop=true 永不结束 → 用 timer)
        vid.loop = false;
        vid.onended = advance;
        const vdur = (slide.media.video_duration || 0) || 5.0;
        setTimeout(advance, (vdur + 0.5) * 1000);
      }
      return;
    }

    // 中间页 — audio 旁白
    const seg = audioSegs[i];
    if (seg && seg.ok && seg.src) {
      // 优先用 preloaded HTMLAudioElement(零 buffering 切换)
      const pre = audioCache.get(i);
      const useEl = pre || audioEl;
      if (useEl !== audioEl) {
        // 把预加载的元素挂为当前播放器(替换 src 用法以保持 onended 行为)
        audioEl.src = '';
        useEl.currentTime = 0;
        useEl.muted = false;
        let advanced = false;
        const advance = () => { if (!advanced) { advanced = true; playSlide(i + 1); } };
        useEl.onended = advance;
        // 兜底
        const dur = (seg.duration || 0) || DEFAULT_DUR;
        setTimeout(advance, (dur + 0.4) * 1000);
        const p = useEl.play();
        if (p && p.catch) p.catch(() => {});
      } else {
        audioEl.src = seg.src;
        let advanced = false;
        const advance = () => { if (!advanced) { advanced = true; playSlide(i + 1); } };
        audioEl.onended = advance;
        const dur = (seg.duration || 0) || DEFAULT_DUR;
        setTimeout(advance, (dur + 0.4) * 1000);
        const p = audioEl.play();
        if (p && p.catch) p.catch(() => {});
      }
    } else {
      const dur = (seg && seg.duration > 0) ? seg.duration : DEFAULT_DUR;
      setTimeout(() => playSlide(i + 1), dur * 1000);
    }
  }

  // 先 preload audio + avatar,再开 broadcastReady 信号
  Promise.all([preloadAudios(), preloadAvatarDurations()]).then(() => {
    // 中间页:audio_segments.duration
    const audioSec = audioSegs.reduce((a, s) => a + ((s && s.duration) || 0), 0);
    // 数字人页:audio_duration 优先(配音才是真实讲话长度),没有再退 video_duration
    const avatarSec = slides.reduce((a, s) => {
      if (!s.media || s.media.kind !== 'avatar') return a;
      return a + (s.media.audio_duration || s.media.video_duration || 0);
    }, 0);
    const unknownPages = slides.filter((s, i) => {
      if (s.media && s.media.kind === 'avatar') {
        return !(s.media.audio_duration || s.media.video_duration);
      }
      return !(audioSegs[i] && audioSegs[i].duration);
    }).length;
    window.__broadcastTotalSec = audioSec + avatarSec + unknownPages * DEFAULT_DUR + 1.5;
    window.__broadcastReady = true;
    setTimeout(() => playSlide(0), 500);
  });
})();
</script>
</body>
</html>
"""


# 反 AI slop 关键词扫描 — 命中即视为违规
_ANTI_SLOP_PATTERNS: dict[str, str] = {
    "purple_pink_gradient": r"linear-gradient.*?(purple|fuchsia|pink|#[a-fA-F0-9]*[a-fA-F][a-fA-F0-9]{2}.*?#[fF][a-fA-F0-9]{2}[fF])",
    "glassmorphism": r"backdrop-filter\s*:\s*blur",
    "generic_inter_roboto": r"font-family\s*:[^;]*(Inter|Roboto|Arial)\b",
    "emoji_as_icon": r">[^<]*?[\U0001F300-\U0001FAFF\U00002600-\U000027BF][^<]*?</",
    "title_case_en": r">[A-Z][a-z]+ [A-Z][a-z]+ [A-Z][a-z]+",
}

# 数字识别(同前端 JS)
_NUM_RE_PY = (
    r"(\d+(?:[\.,]\d+)?(?:[→\-~/]\d+(?:[\.,]\d+)?)?)\s*"
    r"(份|万元|万|元|次|秒|分钟|分|小时|个|项|条|场|%|‰|h|s)?"
)


def _audit_presentation(payload: dict, html: str) -> dict:
    """逐页 + 全局诊断,供 reviewer 审计 HTML 视觉质量."""
    import re as _re
    pat_num = _re.compile(_NUM_RE_PY)
    slides = payload.get("slides") or []
    meta = payload.get("meta") or {}

    pages_audit: list[dict] = []
    type_counter: dict[str, int] = {}
    total_num_cells = 0
    total_bullets = 0
    cover_idx = None
    review_idx = None
    risk_idx: list[int] = []

    for idx, s in enumerate(slides):
        st = (s.get("type") or "detail").lower()
        # 第 0 页若没声明 type=cover,前端 JS 也会按 cover 渲染 — audit 跟着对齐
        if idx == 0 and st != "cover":
            st = "cover"
        type_counter[st] = type_counter.get(st, 0) + 1
        if st == "cover":
            cover_idx = idx
        if st == "review":
            review_idx = idx
        if st in ("risk", "risks"):
            risk_idx.append(idx)

        bullets = [b for b in (s.get("content") or []) if isinstance(b, str) and b.strip()]
        num_cells = sum(1 for b in bullets if pat_num.search(b))
        text_bullets = len(bullets) - num_cells
        title = (s.get("title") or "").strip()
        narration = (s.get("narration") or "").strip()
        media = s.get("media") or {}
        review = s.get("review") or {}

        # density signal — 内容字符总数(title + bullets + narration 前 200)
        density = (
            len(title)
            + sum(len(b) for b in bullets)
            + min(len(narration), 200)
        )

        pages_audit.append({
            "page_no": idx + 1,
            "type": st,
            "title": title,
            "has_num_hero": num_cells > 0,
            "num_cells": num_cells,
            "text_bullets": text_bullets,
            "bullet_total": len(bullets),
            "has_narration": bool(narration),
            "narration_chars": len(narration),
            "has_media_video": bool(media.get("video")),
            "has_media_audio": bool(media.get("audio")),
            "has_review_block": bool(review),
            "density_score": density,
        })
        total_num_cells += num_cells
        total_bullets += len(bullets)

    # 全局 anti-slop 扫描 — strip <style>/<script> 块避免 CSS pseudo-content (✓✗等) 误命中
    body_html = _re.sub(r"<style[^>]*>.*?</style>", "", html, flags=_re.DOTALL | _re.IGNORECASE)
    body_html = _re.sub(r"<script[^>]*>.*?</script>", "", body_html, flags=_re.DOTALL | _re.IGNORECASE)
    slop_hits: dict[str, bool] = {}
    for name, pat in _ANTI_SLOP_PATTERNS.items():
        try:
            target = html if name in ("purple_pink_gradient", "glassmorphism", "generic_inter_roboto") else body_html
            slop_hits[name] = bool(_re.search(pat, target, flags=_re.IGNORECASE))
        except Exception:  # noqa: BLE001
            slop_hits[name] = False

    # 节奏诊断:章节段落感
    rhythm_ok = (
        cover_idx == 0
        and (review_idx is None or review_idx == len(slides) - 1)
        and len(type_counter) >= 2  # 至少 2 种 type 才有差异化
    )

    # 详略得当:density 标准差(越大说明详略明显,平均反而是 wall-of-text)
    if pages_audit:
        ds = [p["density_score"] for p in pages_audit]
        avg = sum(ds) / len(ds)
        variance = sum((d - avg) ** 2 for d in ds) / len(ds)
        density_std = variance ** 0.5
    else:
        density_std = 0
        avg = 0

    findings: list[str] = []
    if not pages_audit:
        findings.append("无 slides — 页面空白")
    if cover_idx != 0:
        findings.append(f"封面页位置异常(cover_idx={cover_idx},应为 0)")
    if total_num_cells == 0:
        findings.append("**无任何数字 hero** — 全是文字 bullet,数据没有起来")
    if total_num_cells < max(1, len(slides) // 3):
        findings.append(f"数字 hero 偏少({total_num_cells} 个 / {len(slides)} 页),数据驱动感不足")
    full_text_pages = [p["page_no"] for p in pages_audit if p["bullet_total"] >= 4 and p["num_cells"] == 0]
    if full_text_pages:
        findings.append(f"以下页面纯文字 bullet ≥4 条且无数字:{full_text_pages},考虑拆数字 hero")
    if not rhythm_ok:
        findings.append("章节节奏异常:封面 / 收尾 / type 分布需检查")
    type_only_detail = all((p["type"] == "detail") for p in pages_audit[1:-1] if len(pages_audit) > 2)
    if type_only_detail and len(pages_audit) > 3:
        findings.append("中间页面 type 全是 detail,缺乏 summary/completed/risk/next_steps 差异化")
    if density_std < avg * 0.18 and avg > 30:
        findings.append(f"详略未拉开(density_std={density_std:.1f}, avg={avg:.1f})— 每页信息量太平均")
    for name, hit in slop_hits.items():
        if hit:
            findings.append(f"反 AI slop 命中:{name} — 视觉失格")

    overall_score = 100
    overall_score -= 12 * sum(1 for v in slop_hits.values() if v)
    overall_score -= 8 if total_num_cells == 0 else 0
    overall_score -= 6 if not rhythm_ok else 0
    overall_score -= 5 if density_std < avg * 0.18 and avg > 30 else 0
    overall_score -= 4 if full_text_pages else 0
    overall_score = max(0, min(100, overall_score))

    return {
        "summary": {
            "task_id": meta.get("task_id"),
            "title": meta.get("title"),
            "pages": len(slides),
            "type_distribution": type_counter,
            "total_num_cells": total_num_cells,
            "total_bullets": total_bullets,
            "cover_idx": cover_idx,
            "review_idx": review_idx,
            "risk_idx": risk_idx,
            "rhythm_ok": rhythm_ok,
            "density_std": round(density_std, 2),
            "density_avg": round(avg, 2),
            "anti_slop_hits": slop_hits,
            "overall_visual_score": overall_score,
        },
        "findings": findings,
        "pages": pages_audit,
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
