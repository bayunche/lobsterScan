"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

const TYPES = [
  { value: "daily",            label: "日常工作汇报", hint: "周报 / 日报 / 例会同步" },
  { value: "project_progress", label: "项目进度汇报", hint: "项目周会 / 阶段节点" },
  { value: "review",           label: "基层管理述职", hint: "转正 / 季度 / 半年 / 年度" },
  { value: "introduction",     label: "工作介绍",     hint: "新人 / 跨部门 / 外部" },
];
const AUDIENCE = ["直属领导", "团队内部", "跨部门", "客户"] as const;
const DURATION = ["1分钟", "3分钟", "5分钟"] as const;
const STYLE    = ["简洁正式", "成果突出", "问题导向", "述职风"] as const;

const DEMO = `本周做了客户回访，完成 18 个客户沟通，其中 12 个确认继续推进。活动页面设计完成了初稿，产品和运营都看过一轮。测试发现 3 个问题，其中 1 个比较影响上线。数据整理完成 80%。客户最终确认时间还没定，希望领导帮忙协调一下。下周准备完成问题修复、客户确认和上线前验收。`;

const SESSION_KEY = "lobster-chat-session";

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

function iconForName(name: string): string {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["png","jpg","jpeg","gif","webp","bmp","svg"].includes(ext))  return "🖼";
  if (["mp3","wav","m4a","flac","aac","ogg"].includes(ext))         return "🎵";
  if (["mp4","mov","webm","mkv","avi"].includes(ext))               return "🎬";
  if (["pdf"].includes(ext))                                         return "📕";
  if (["doc","docx"].includes(ext))                                  return "📘";
  if (["xls","xlsx","csv"].includes(ext))                            return "📗";
  if (["ppt","pptx"].includes(ext))                                  return "📙";
  if (["zip","rar","7z","tar","gz","bz2"].includes(ext))             return "🗜";
  return "📄";
}

export default function FeatReportPage() {
  const router = useRouter();
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [sid, setSid] = useState<string | null>(null);
  const [reportType, setReportType] = useState("project_progress");
  const [title, setTitle] = useState("本周项目进度汇报");
  const [audience, setAudience] = useState<(typeof AUDIENCE)[number]>("直属领导");
  const [duration, setDuration] = useState<(typeof DURATION)[number]>("3分钟");
  const [style, setStyle] = useState<(typeof STYLE)[number]>("简洁正式");
  const [rawText, setRawText] = useState("");
  const [files, setFiles] = useState<{ name: string; file_id: string; size: number; mime: string }[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let s = sessionStorage.getItem(SESSION_KEY);
    if (!s) {
      s = "ses_" + Math.random().toString(36).slice(2, 14);
      sessionStorage.setItem(SESSION_KEY, s);
    }
    setSid(s);
  }, []);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 480) + "px";
  }, [rawText]);

  async function pickFiles(selected: FileList | null) {
    if (!selected || selected.length === 0) return;
    setUploading(u => u + selected.length);
    for (const f of Array.from(selected)) {
      const fd = new FormData();
      fd.append("file", f);
      try {
        const r = await fetch("/api/files", { method: "POST", body: fd });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          setErr(j?.detail?.error?.biz_message || `${f.name} 上传失败`);
          continue;
        }
        const j = await r.json();
        setFiles((arr) => [...arr, { name: j.filename, file_id: j.file_id, size: j.size, mime: j.mime }]);
      } catch {
        setErr(`${f.name} 上传失败`);
      } finally {
        setUploading(u => u - 1);
      }
    }
  }

  function removeFile(file_id: string) {
    setFiles(arr => arr.filter(x => x.file_id !== file_id));
  }

  async function submit() {
    setErr(null);
    if (!rawText.trim() && files.length === 0) {
      setErr("请粘贴一段材料或上传文件");
      return;
    }
    if (!sid) return;
    setSubmitting(true);
    try {
      const r = await fetch("/api/cluster/feat/report", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sid, raw_text: rawText,
          title, report_type: reportType, audience, duration, style,
          file_ids: files.map(f => f.file_id),
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setErr(j?.detail?.error?.biz_message || "提交失败");
        return;
      }
      router.push("/");
    } finally {
      setSubmitting(false);
    }
  }

  const canSubmit = (rawText.trim().length > 0 || files.length > 0) && !submitting && uploading === 0;

  return (
    <div className="page">
      <header className="top">
        <Link href="/" className="back">⟵ 返回集群对话</Link>
        <span className="serial top-tag">FEAT · 汇报材料生成</span>
      </header>

      <div className="shell">
        <section className="hero">
          <h1 className="hero-title">交一份材料，集群帮你做完汇报</h1>
          <p className="hero-sub">
            原文 / 表格 / 截图 / 录音 / 视频都行 — 资料员先拆，<span className="ink">分析师</span>挑重点、
            <span className="ink">表达教练</span>换视角、文书写讲稿、设计师出 HTML、
            <span className="ink">视频制作</span>配音、质量检查员做最后审校。
          </p>
        </section>

        {/* ① 上传区（视觉焦点） */}
        <section
          className={"drop " + (dragOver ? "drop-over" : "")}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); pickFiles(e.dataTransfer.files); }}
          onClick={() => fileInput.current?.click()}
        >
          <div className="drop-icon">
            <span>📂</span>
          </div>
          <div className="drop-text">
            <div className="drop-title">
              {uploading > 0 ? `正在上传 · ${uploading} 个文件` :
               files.length > 0 ? `已添加 ${files.length} 个文件，继续添加…` :
               "点击或拖拽上传文件"}
            </div>
            <div className="drop-hint">
              <span className="kbd">文档</span>
              <span className="kbd">表格</span>
              <span className="kbd">图片</span>
              <span className="kbd">音频</span>
              <span className="kbd">视频</span>
              <span className="kbd">压缩包</span>
              <span className="drop-sep">·</span>
              <span>单文件 ≤ 64 MB</span>
            </div>
          </div>
          <input
            ref={fileInput} type="file" multiple
            className="hidden"
            onChange={(e) => { pickFiles(e.target.files); e.currentTarget.value = ""; }}
          />
        </section>

        {/* 已上传文件 */}
        {files.length > 0 && (
          <ul className="files">
            {files.map((f) => (
              <li key={f.file_id}>
                <span className="file-icon">{iconForName(f.name)}</span>
                <span className="file-name">{f.name}</span>
                <span className="serial file-size">{bytes(f.size)}</span>
                <button onClick={() => removeFile(f.file_id)} className="file-x" aria-label="移除">×</button>
              </li>
            ))}
          </ul>
        )}

        {/* ② 文字（可选） */}
        <section className="text-block">
          <div className="text-head">
            <span className="serial">或者，粘贴一段文字</span>
            <button type="button" onClick={() => setRawText(DEMO)} className="text-demo">
              填入示例
            </button>
          </div>
          <textarea
            ref={taRef}
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
            rows={6}
            placeholder="工作记录 / 任务清单 / 会议纪要 / 数据片段……"
            className="text-area"
          />
          <div className="text-foot">
            <span className="serial">{rawText.length} 字</span>
          </div>
        </section>

        {/* ③ 配置 */}
        <section className="cfg">
          <div className="cfg-head serial">§ 汇报设置</div>

          <Row label="标题">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="text-input"
            />
          </Row>

          <Row label="类型">
            <div className="chip-row">
              {TYPES.map(t => (
                <button key={t.value}
                  type="button"
                  onClick={() => setReportType(t.value)}
                  className={"chip2 " + (reportType === t.value ? "chip2-on" : "")}
                  title={t.hint}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </Row>

          <Row label="对象">
            <ChipRow value={audience} options={AUDIENCE} onPick={setAudience} />
          </Row>

          <Row label="时长">
            <ChipRow value={duration} options={DURATION} onPick={setDuration} />
          </Row>

          <Row label="风格">
            <ChipRow value={style} options={STYLE} onPick={setStyle} />
          </Row>
        </section>

        {err && <div className="err">{err}</div>}

        <section className="submit-bar">
          <div className="submit-meta serial">
            <span>SESSION · {(sid || "—").slice(0, 14)}</span>
            <span className="dot dot-on" /> 9 / 9 在线
          </div>
          <button onClick={submit} disabled={!canSubmit} className="submit-btn">
            <span>{submitting ? "正在派发任务" : "发送并分派任务"}</span>
            <span className="submit-arrow">→</span>
          </button>
        </section>
      </div>

      <style jsx>{`
        .page {
          min-height: 100vh;
          display: flex;
          flex-direction: column;
        }
        .top {
          padding: 1.1rem 2rem;
          border-bottom: 1px solid var(--line);
          display: flex; justify-content: space-between; align-items: center;
          background: var(--paper);
        }
        :global(.top .back) {
          color: var(--ink-mute);
          text-decoration: none;
          font-size: var(--t-sm);
          transition: color var(--t-base) var(--ease);
        }
        :global(.top .back:hover) { color: var(--seal); }
        .top-tag { color: var(--ink-mute); }

        .shell {
          max-width: 760px;
          margin: 0 auto;
          padding: 3rem 2rem 4rem;
          width: 100%;
          display: flex;
          flex-direction: column;
          gap: 2rem;
        }

        /* === hero === */
        .hero { display: flex; flex-direction: column; gap: 0.65rem; }
        .hero-title {
          font-family: var(--font-serif);
          font-size: 2rem;
          font-weight: 600;
          letter-spacing: -0.01em;
          margin: 0;
          color: var(--ink);
        }
        .hero-sub {
          margin: 0;
          color: var(--ink-soft);
          line-height: 1.7;
          font-size: 0.95rem;
        }
        .hero-sub .ink { color: var(--ink); font-weight: 500; }

        /* === 上传区（视觉焦点） === */
        .drop {
          display: flex;
          align-items: center;
          gap: 1.25rem;
          padding: 1.75rem 1.5rem;
          border: 2px dashed var(--line);
          border-radius: 14px;
          background: var(--paper-warm);
          cursor: pointer;
          transition: all var(--t-base) var(--ease);
        }
        .drop:hover {
          border-color: var(--ink-soft);
          background: var(--paper);
        }
        .drop-over {
          border-color: var(--seal) !important;
          background: var(--seal-soft) !important;
        }
        .drop-icon {
          width: 64px; height: 64px;
          flex-shrink: 0;
          display: flex; align-items: center; justify-content: center;
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: 14px;
          font-size: 1.85rem;
        }
        .drop-text {
          flex: 1; min-width: 0;
          display: flex; flex-direction: column; gap: 0.4rem;
        }
        .drop-title {
          font-family: var(--font-serif);
          font-size: 1.05rem;
          color: var(--ink);
        }
        .drop-hint {
          display: flex; flex-wrap: wrap; align-items: center;
          gap: 0.35rem;
          font-size: 0.75rem;
          color: var(--ink-mute);
        }
        .kbd {
          font-family: var(--font-mono);
          font-size: 0.68rem;
          padding: 0.1rem 0.4rem;
          background: var(--paper);
          border: 1px solid var(--line-soft);
          border-radius: 3px;
          color: var(--ink-soft);
        }
        .drop-sep { margin: 0 0.25rem; opacity: 0.5; }
        .hidden { display: none; }

        /* === 已上传文件 === */
        .files {
          list-style: none; padding: 0; margin: 0;
          display: flex; flex-direction: column; gap: 0.4rem;
        }
        .files li {
          display: flex; align-items: center; gap: 0.65rem;
          padding: 0.6rem 0.85rem;
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: var(--r-2);
        }
        .file-icon {
          width: 32px; height: 32px;
          display: inline-flex; align-items: center; justify-content: center;
          background: var(--paper-warm);
          border-radius: 6px;
          font-size: 1.05rem;
          flex-shrink: 0;
        }
        .file-name {
          flex: 1; min-width: 0;
          color: var(--ink);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
          font-size: var(--t-sm);
        }
        .file-size { color: var(--ink-mute); }
        .file-x {
          width: 22px; height: 22px;
          border: 0; background: transparent;
          color: var(--ink-mute);
          font-size: 1.1rem;
          cursor: pointer;
          border-radius: 50%;
          display: inline-flex; align-items: center; justify-content: center;
          transition: all var(--t-fast) var(--ease);
        }
        .file-x:hover { background: var(--seal); color: var(--paper); }

        /* === 文字 === */
        .text-block {
          display: flex; flex-direction: column; gap: 0.45rem;
        }
        .text-head {
          display: flex; justify-content: space-between; align-items: center;
          color: var(--ink-mute);
        }
        :global(.text-demo) {
          font-size: var(--t-sm);
          background: transparent;
          border: 0;
          color: var(--ink);
          border-bottom: 1px solid var(--ink);
          padding: 0 0 1px;
          cursor: pointer;
          transition: all var(--t-fast) var(--ease);
        }
        :global(.text-demo:hover) { color: var(--seal); border-color: var(--seal); }
        :global(.text-area) {
          width: 100%;
          padding: 0.9rem 1rem;
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: 12px;
          font-family: var(--font-sans);
          font-size: 0.95rem;
          line-height: 1.65;
          color: var(--ink);
          resize: vertical;
          min-height: 140px;
          transition: border-color var(--t-base) var(--ease);
        }
        :global(.text-area:focus) {
          outline: none;
          border-color: var(--ink-soft);
        }
        :global(.text-area::placeholder) { color: var(--ink-mute); }
        .text-foot { color: var(--ink-mute); }

        /* === 配置 === */
        .cfg {
          display: flex; flex-direction: column; gap: 1rem;
          padding: 1.25rem 1.35rem;
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: 12px;
        }
        .cfg-head { color: var(--ink-mute); margin-bottom: 0.15rem; }
        :global(.text-input) {
          width: 100%;
          padding: 0.55rem 0.8rem;
          background: var(--paper-warm);
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          font-family: var(--font-sans);
          font-size: var(--t-base);
          color: var(--ink);
          transition: border-color var(--t-base) var(--ease);
        }
        :global(.text-input:focus) {
          outline: none;
          border-color: var(--ink-soft);
          background: var(--paper);
        }
        .chip-row { display: flex; flex-wrap: wrap; gap: 0.4rem; }
        :global(.chip2) {
          padding: 0.42rem 0.85rem;
          background: var(--paper-warm);
          border: 1px solid var(--line);
          border-radius: var(--r-pill);
          font-family: var(--font-sans);
          font-size: var(--t-sm);
          color: var(--ink-soft);
          cursor: pointer;
          transition: all var(--t-fast) var(--ease);
        }
        :global(.chip2:hover) { border-color: var(--ink-soft); color: var(--ink); }
        :global(.chip2-on) {
          background: var(--ink); color: var(--paper); border-color: var(--ink);
        }

        /* === 错误条 === */
        .err {
          padding: 0.7rem 1rem;
          background: var(--fail-soft);
          color: var(--fail);
          border: 1px solid var(--fail);
          border-radius: var(--r-2);
          font-size: var(--t-sm);
        }

        /* === 提交 === */
        .submit-bar {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 1rem;
          padding: 1rem 0 0;
        }
        .submit-meta {
          display: flex; align-items: center; gap: 0.5rem;
          color: var(--ink-mute);
        }
        .submit-btn {
          display: inline-flex; align-items: center; gap: 0.65rem;
          padding: 0.85rem 1.6rem;
          background: var(--ink);
          color: var(--paper);
          border: 0;
          border-radius: 12px;
          font-family: var(--font-sans);
          font-size: 0.95rem;
          font-weight: 500;
          cursor: pointer;
          transition: all var(--t-base) var(--ease);
          box-shadow: 0 2px 4px oklch(0.20 0.02 250 / 0.08),
                      0 16px 32px -20px oklch(0.20 0.02 250 / 0.5);
        }
        .submit-btn:hover:not(:disabled) {
          background: var(--seal);
          transform: translateY(-1px);
        }
        .submit-btn:disabled { opacity: 0.35; cursor: not-allowed; }
        .submit-arrow {
          font-family: var(--font-mono);
          transition: transform var(--t-base) var(--ease);
        }
        .submit-btn:hover:not(:disabled) .submit-arrow { transform: translateX(3px); }

        /* ───── 响应式 ───── */
        @media (max-width: 768px) {
          .top { padding: 0.9rem 1.1rem; }
          .shell { padding: 1.75rem 1.1rem 3rem; gap: 1.5rem; }
          .hero-title { font-size: 1.5rem; }
          .hero-sub { font-size: 0.9rem; line-height: 1.65; }
          .drop {
            flex-direction: column;
            text-align: center;
            padding: 1.5rem 1.1rem;
            gap: 0.85rem;
          }
          .drop-icon { width: 56px; height: 56px; font-size: 1.6rem; }
          .drop-text { align-items: center; }
          .drop-hint { justify-content: center; }
          .cfg { padding: 1rem; }
          .submit-bar {
            flex-direction: column;
            align-items: stretch;
            gap: 0.85rem;
          }
          .submit-btn { width: 100%; justify-content: center; padding: 0.85rem 1rem; }
          .submit-meta { justify-content: center; }
        }
        @media (max-width: 480px) {
          .top-tag { display: none; }
          .hero-title { font-size: 1.35rem; }
          .hero-sub { font-size: 0.85rem; }
          :global(.row) { grid-template-columns: 1fr; gap: 0.35rem; }
          :global(.row .row-label) { margin-bottom: 2px; }
          .drop { padding: 1.25rem 0.9rem; }
          .drop-title { font-size: 0.95rem; }
          .kbd { font-size: 0.62rem; padding: 0.05rem 0.32rem; }
        }
      `}</style>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="row">
      <div className="row-label serial">{label}</div>
      <div className="row-body">{children}</div>
      <style jsx>{`
        .row {
          display: grid;
          grid-template-columns: 60px 1fr;
          gap: 1rem;
          align-items: center;
        }
        .row-label { color: var(--ink-mute); }
        .row-body { min-width: 0; }
      `}</style>
    </div>
  );
}

function ChipRow<T extends string>({
  value, options, onPick,
}: { value: T; options: readonly T[]; onPick: (v: T) => void }) {
  return (
    <div className="chip-row">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onPick(o)}
          className={"chip2 " + (o === value ? "chip2-on" : "")}
        >
          {o}
        </button>
      ))}
      <style jsx>{`
        .chip-row { display: flex; flex-wrap: wrap; gap: 0.4rem; }
      `}</style>
    </div>
  );
}
