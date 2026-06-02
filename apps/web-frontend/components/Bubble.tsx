"use client";

// P7(spec 007-chat-ux):群聊气泡组件,从 app/page.tsx 抽出独立文件
// (Next.js page 文件不允许 default 以外的 named export;抽出后可被组件测试 import)。
// 含:Bubble + 子组件(ArtifactChip/TypingDots/Attach)+ 渲染辅助 + 类型 + MEMBERS。

import Link from "next/link";
import { useEffect, useState } from "react";

export type Attachment = {
  file_id: string;
  filename: string;
  size: number;
  mime: string;
  url: string;
};

export type ReportMeta = {
  title: string;
  audience: string;
  duration: string;
  style: string;
  report_type: string;
  preview?: string;
};

export type Artifact = {
  label: string;
  url: string;
  kind: string;
  open_in_new?: boolean;
  probe?: boolean;
};

// P7 US3:agent 更新已有产物时的 diff 信息
export type ArtifactDelta = { id: string; version: number; summary: string };

export type ChatMsg = {
  id: string;
  agent: string;
  display_name: string;
  seal: string;
  ts: number;
  kind: "user" | "intro" | "result" | "system" | "error" | "silent";  // P7: +silent
  text: string;
  tokens?: number;
  attachments?: Attachment[];
  artifacts?: Artifact[];
  analysis?: string;
  task_id?: string;
  report_meta?: ReportMeta;
  // P7 additive 字段(后端 _chat_msg 透传;旧消息无此字段,走原渲染)
  mentions?: string[];
  silent_reason?: string;
  artifact_delta?: ArtifactDelta;
};

// P7(US4):常用调整 prompt 模板。点击填入输入框(可编辑后发送),
// 替代原 5 个固定 refine 按钮(FR-009/010)。
export const PROMPT_TEMPLATES: { label: string; text: string }[] = [
  { label: "再短一点", text: "把讲稿再压缩 30%,保留核心结论和关键数据。" },
  { label: "更突出问题", text: "更突出风险和问题,把影响说清楚。" },
  { label: "更正式一点", text: "语气调得更正式书面一些,适合向上汇报。" },
  { label: "更突出成果", text: "强化已落地成果的量化数据和亮点。" },
  { label: "重新生成", text: "重新生成讲稿,换一个表达角度。" },
];

// 9 位成员(中文显示名 + 印章)。@高亮匹配源。
export const MEMBERS = [
  { id: "coordinator",     name: "汇报总控",   seal: "调" },
  { id: "material",        name: "资料员",     seal: "料" },
  { id: "point-extractor", name: "分析师",     seal: "析" },
  { id: "structure",       name: "结构师",     seal: "纲" },
  { id: "upward-opt",      name: "表达教练",   seal: "译" },
  { id: "copywriter",      name: "文书",       seal: "文" },
  { id: "html-designer",   name: "设计师",     seal: "设" },
  { id: "video-producer",  name: "视频制作",   seal: "影" },
  { id: "reviewer",        name: "质量检查员", seal: "校" },
];

// P7(US1):把文本里的 @<成员中文名> 渲染为高亮 chip。
// 只匹配 9 位成员的精确中文名(MEMBERS),裸 @词不高亮(FR-005)。
const MEMBER_NAMES = MEMBERS.map((m) => m.name);
const MENTION_RE = new RegExp(
  "@(" + MEMBER_NAMES.slice().sort((a, b) => b.length - a.length)
    .map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")",
  "g",
);

export function renderWithMentions(text: string): React.ReactNode {
  if (!text || text.indexOf("@") === -1) return text;
  const out: React.ReactNode[] = [];
  let last = 0;
  let mi = 0;
  text.replace(MENTION_RE, (match, _name: string, offset: number) => {
    if (offset > last) out.push(text.slice(last, offset));
    out.push(<span key={`m-${mi++}`} className="mention">{match}</span>);
    last = offset + match.length;
    return match;
  });
  if (last < text.length) out.push(text.slice(last));
  return out.length ? out : text;
}

export function Bubble({ msg, prev }: { msg: ChatMsg; prev?: ChatMsg }) {
  const isUser = msg.agent === "user";
  const isSystem = msg.agent === "system";
  const sameAgent = prev && prev.agent === msg.agent && msg.kind !== "system" && !isUser;

  // P7(US2):silent — agent 这轮没开口,灰色弱化「掠过」小气泡(FR-006)。
  if (msg.kind === "silent") {
    return (
      <div className="bb-silent animate-ink-rise">
        <span className="seal seal-sm" data-agent={msg.agent}>{msg.seal}</span>
        <span className="bb-silent-text serial">
          {msg.display_name} 掠过{msg.silent_reason ? ` · ${msg.silent_reason}` : ""}
        </span>
        <style jsx>{`
          .bb-silent {
            display: flex; align-items: center; gap: 0.5rem;
            padding: 0.2rem 0.2rem 0.2rem 0;
            opacity: 0.6;
          }
          :global(.bb-silent .seal) { font-size: 0.5em; transform: rotate(-2deg); filter: grayscale(0.5); }
          .bb-silent-text {
            font-size: 0.74rem;
            color: var(--ink-mute);
            font-style: italic;
            border: 1px dashed var(--line);
            border-radius: var(--r-pill);
            padding: 0.12rem 0.6rem;
            background: transparent;
          }
        `}</style>
      </div>
    );
  }

  if (isSystem) {
    const hasMeta = msg.report_meta || (msg.attachments && msg.attachments.length);

    if (hasMeta) {
      return (
        <div className="bb-launch animate-ink-rise">
          <div className="bb-launch-head">
            <span className="seal seal-sm" data-agent="user">{msg.seal}</span>
            <span className="serial">你 · {fmtTs(msg.ts)} · 启动了功能</span>
            {msg.task_id && (
              <Link href={`/tasks/${msg.task_id}`} className="bb-launch-link">
                查看任务 →
              </Link>
            )}
          </div>
          <div className="bb-launch-body">
            <div className="bb-launch-title">{msg.report_meta?.title || msg.text}</div>
            {msg.report_meta && (
              <div className="bb-launch-tags">
                <span className="tag">{reportTypeLabel(msg.report_meta.report_type)}</span>
                <span className="tag">{msg.report_meta.audience}</span>
                <span className="tag">{msg.report_meta.duration}</span>
                <span className="tag">{msg.report_meta.style}</span>
              </div>
            )}
            {msg.report_meta?.preview && (
              <div className="bb-launch-preview">{msg.report_meta.preview}{msg.report_meta.preview.length >= 200 ? "…" : ""}</div>
            )}
            {msg.attachments && msg.attachments.length > 0 && (
              <div className="bb-att">
                <div className="serial bb-att-label">附件 · {msg.attachments.length}</div>
                <div className="bb-att-grid">
                  {msg.attachments.map((a) => (
                    <Attach key={a.file_id} a={a} />
                  ))}
                </div>
              </div>
            )}
          </div>

          <style jsx>{`
            .bb-launch {
              border: 1px solid var(--line);
              background: var(--glass-surface-1);
              backdrop-filter: blur(var(--glass-blur-2));
              -webkit-backdrop-filter: blur(var(--glass-blur-2));
              border-radius: var(--r-lg);
              padding: 0.85rem 1rem;
              box-shadow: var(--shadow-glass);
              max-width: 92%;
              align-self: center;
              width: 100%;
            }
            .bb-launch-head {
              display: flex; align-items: center; gap: 0.5rem;
              font-size: var(--t-sm);
              color: var(--ink-mute);
              padding-bottom: 0.5rem;
              border-bottom: 1px dashed var(--line-soft);
              margin-bottom: 0.6rem;
            }
            :global(.bb-launch-head .seal) { font-size: 0.55em; transform: rotate(-3deg); }
            :global(.bb-launch-link) {
              margin-left: auto;
              color: var(--ink);
              text-decoration: none;
              border-bottom: 1px solid var(--ink);
              padding-bottom: 1px;
            }
            :global(.bb-launch-link:hover) { color: var(--seal); border-color: var(--seal); }
            .bb-launch-body { display: flex; flex-direction: column; gap: 0.5rem; }
            .bb-launch-title {
              font-family: var(--font-serif);
              font-size: var(--t-lg);
              color: var(--ink);
            }
            .bb-launch-tags { display: flex; flex-wrap: wrap; gap: 0.3rem; }
            .bb-launch-preview {
              font-size: var(--t-sm);
              color: var(--ink-soft);
              line-height: 1.55;
              padding: 0.5rem 0.65rem;
              background: var(--paper);
              border: 1px solid var(--line-soft);
              border-radius: var(--r-2);
            }
            .bb-att { margin-top: 0.3rem; }
            :global(.bb-att-label) { color: var(--ink-mute); margin-bottom: 0.4rem; }
            :global(.bb-att-grid) {
              display: grid;
              grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
              gap: 0.4rem;
            }
          `}</style>
        </div>
      );
    }

    return (
      <div className="bb-sys animate-ink-rise">
        <span className="bb-sys-tick" />
        <span className="serial">{msg.text}</span>
        <span className="bb-sys-tick" />
        <style jsx>{`
          .bb-sys {
            display: flex; align-items: center; gap: 0.75rem;
            justify-content: center;
            padding: 0.8rem 0 0.4rem;
            color: var(--ink-mute);
          }
          .bb-sys-tick { height: 1px; width: 36px; background: var(--line); }
          :global(.bb-sys .serial) {
            font-size: 0.7rem;
            color: var(--ink-mute);
            letter-spacing: 0.08em;
          }
        `}</style>
      </div>
    );
  }

  if (isUser) {
    return (
      <div className="bb-user animate-ink-rise">
        <div className="bb-user-body">{msg.text}</div>
        <div className="bb-user-meta serial">
          <span>{fmtTs(msg.ts)}</span>
          <span>你</span>
          <span className="seal seal-sm" data-agent="user">{msg.seal}</span>
        </div>
        <style jsx>{`
          .bb-user {
            align-self: flex-end;
            max-width: 78%;
            display: flex; flex-direction: column;
            align-items: flex-end; gap: 0.35rem;
          }
          .bb-user-body {
            background: linear-gradient(135deg, var(--color-primary-500) 0%, var(--color-primary-600) 100%);
            color: #fff;
            padding: 0.85rem 1.1rem;
            border-radius: 1.1rem 1.1rem 0.25rem 1.1rem;
            font-size: 0.95rem;
            line-height: 1.55;
            box-shadow: 0 2px 12px rgba(13, 148, 136, 0.22);
            white-space: pre-wrap;
          }
          .bb-user-meta {
            display: flex; align-items: center; gap: 0.45rem;
            color: var(--ink-mute);
          }
          :global(.bb-user .seal) { transform: rotate(2deg); }
        `}</style>
      </div>
    );
  }

  return (
    <article className={"bb-agent animate-ink-rise" + (sameAgent ? " bb-cont" : "")}>
      {!sameAgent && (
        <span className="seal" data-agent={msg.agent}>{msg.seal}</span>
      )}
      {sameAgent && <span className="bb-spacer" />}
      <div className="bb-body-col">
        {!sameAgent && (
          <header className="bb-head">
            <span className="bb-name">{msg.display_name}</span>
            <span className="serial bb-id">{idShort(msg.agent)}</span>
            <span className="bb-rule" />
            <span className="serial bb-ts">{fmtTs(msg.ts)}</span>
            {msg.tokens ? <span className="serial">{msg.tokens.toLocaleString()} TOK</span> : null}
          </header>
        )}
        <div className={"bb-body" + (msg.kind === "intro" ? " bb-intro" : "") + (msg.text === "…" ? " bb-typing" : "")}>
          {/* P7(US1):@<成员名> 高亮 chip(FR-005)*/}
          {msg.text === "…" ? <TypingDots /> : renderWithMentions(msg.text)}
          {/* P7(US3):更新已有产物(version≥2)→ 内联 diff 行(FR-007)*/}
          {msg.artifact_delta && (
            <div className="bb-diff">
              📝 改了 {msg.artifact_delta.id} 第 {msg.artifact_delta.version} 版
              {msg.artifact_delta.summary ? `:${msg.artifact_delta.summary}` : ""}
            </div>
          )}
        </div>
        {/* agent 思考过程 — 折叠默认收起,不抢戏 */}
        {msg.analysis && (
          <details className="bb-analysis">
            <summary><span className="serial">🔍 思考过程</span></summary>
            <div className="bb-analysis-body">{msg.analysis}</div>
          </details>
        )}
        {/* agent 产出的文件 — chip 行,浏览器能开就开,不能就 download */}
        {msg.artifacts && msg.artifacts.length > 0 && (
          <div className="bb-artifacts">
            {msg.artifacts.map((a) => <ArtifactChip key={a.url} a={a} />)}
          </div>
        )}
      </div>
      <style jsx>{`
        .bb-agent {
          display: flex; gap: 0.65rem;
          align-items: flex-start;
        }
        :global(.bb-agent .seal) { margin-top: 0.15rem; flex-shrink: 0; }
        .bb-spacer { width: 2.1em; flex-shrink: 0; }
        .bb-body-col {
          flex: 1; min-width: 0;
          display: flex; flex-direction: column;
          gap: 0.3rem;
          max-width: 78%;
        }
        .bb-head {
          display: flex; align-items: baseline; gap: 0.45rem;
          line-height: 1;
        }
        .bb-name {
          font-family: var(--font-serif);
          font-size: 0.92rem;
          font-weight: 600;
          color: var(--ink);
        }
        :global(.bb-head .bb-id),
        :global(.bb-head .bb-ts) { color: var(--ink-mute); }
        .bb-rule {
          flex: 1;
          height: 1px;
          background: var(--line-soft);
          margin: 0 0.25rem;
          align-self: center;
        }
        .bb-body {
          background: var(--glass-surface-1);
          backdrop-filter: blur(var(--glass-blur-1));
          -webkit-backdrop-filter: blur(var(--glass-blur-1));
          border: 1px solid var(--line-soft);
          padding: 0.8rem 1rem;
          border-radius: 0.25rem 1.1rem 1.1rem 1.1rem;
          font-size: 0.95rem;
          line-height: 1.6;
          color: var(--ink);
          white-space: pre-wrap;
          box-shadow: var(--shadow-bubble);
        }
        .bb-cont .bb-body { border-top-left-radius: 1.1rem; }
        .bb-intro {
          background: transparent;
          border-style: dashed;
          color: var(--ink-soft);
        }
        .bb-typing { padding: 0.9rem 1rem; }

        /* P7 US1:@提及高亮 chip */
        :global(.bb-body .mention) {
          color: var(--seal);
          background: var(--seal-soft);
          border-radius: 4px;
          padding: 0 3px;
          font-weight: 600;
        }
        /* P7 US3:artifact diff 内联行 */
        .bb-diff {
          margin-top: 0.5rem;
          padding: 0.3rem 0.6rem;
          font-size: 0.78rem;
          color: var(--ink-soft);
          background: var(--paper-warm);
          border-left: 2px solid var(--seal);
          border-radius: 3px;
        }

        /* 思考过程折叠 */
        :global(.bb-analysis) {
          margin-top: 0.4rem;
          padding: 0.4rem 0.7rem;
          background: transparent;
          border-left: 2px solid var(--line);
          font-size: 0.82rem;
          color: var(--ink-soft);
        }
        :global(.bb-analysis summary) {
          cursor: pointer; user-select: none;
          color: var(--ink-mute);
          list-style: none;
        }
        :global(.bb-analysis summary::before) {
          content: "▸"; display: inline-block; margin-right: 0.4em;
          transition: transform 120ms ease;
        }
        :global(.bb-analysis[open] summary::before) { transform: rotate(90deg); }
        :global(.bb-analysis-body) {
          margin-top: 0.5rem;
          line-height: 1.55;
          white-space: pre-wrap;
          color: var(--ink-soft);
        }

        /* 产物 chip 行 */
        :global(.bb-artifacts) {
          margin-top: 0.5rem;
          display: flex; flex-wrap: wrap; gap: 0.35rem;
        }
      `}</style>
    </article>
  );
}

// 单个 artifact chip — 根据 kind 决定 icon + 默认行为(open / download / probe-then-act)
function ArtifactChip({ a }: { a: Artifact }) {
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    if (!a.probe) return;
    fetch(a.url, { method: "HEAD" }).then(r => { if (!r.ok) setMissing(true); }).catch(() => setMissing(true));
  }, [a.url, a.probe]);
  if (missing) return null;

  const inlineKinds = new Set(["json", "txt", "md", "html", "mp4", "mp3", "srt", "jpg", "jpeg", "png", "webp", "gif", "pdf"]);
  const canOpen = inlineKinds.has(a.kind);
  const icon = (
    a.kind === "html" ? "▶"
    : a.kind === "mp4" ? "▶"
    : a.kind === "mp3" ? "♪"
    : a.kind === "srt" ? "字"
    : a.kind === "json" ? "{}"
    : a.kind === "md" ? "✎"
    : a.kind === "txt" ? "T"
    : a.kind === "jpg" || a.kind === "jpeg" || a.kind === "png" || a.kind === "webp" || a.kind === "gif" ? "🖼"
    : a.kind === "pdf" ? "PDF"
    : "⬇"
  );
  return (
    <a
      href={a.url}
      target={canOpen || a.open_in_new ? "_blank" : undefined}
      rel="noopener noreferrer"
      download={canOpen || a.open_in_new ? undefined : ""}
      className={"chip " + (a.open_in_new ? "chip-feature" : "")}
    >
      <span className="chip-icon">{icon}</span>
      <span className="chip-label">{a.label}</span>
      <style jsx>{`
        .chip {
          display: inline-flex; align-items: center; gap: 0.4rem;
          padding: 0.22rem 0.55rem;
          border: 1px solid var(--line);
          border-radius: 999px;
          background: var(--paper);
          color: var(--ink);
          text-decoration: none;
          font-size: 0.75rem;
          transition: all var(--t-fast) var(--ease);
        }
        .chip:hover { border-color: var(--ink); transform: translateY(-1px); }
        .chip-feature {
          border-color: var(--seal); background: var(--seal-soft); color: var(--seal);
          font-weight: 500;
        }
        .chip-feature:hover { background: rgba(20,184,166,0.18); border-color: var(--seal); }
        .chip-icon {
          font-family: var(--font-mono);
          font-size: 0.7rem;
          color: var(--ink-mute);
        }
        .chip-feature .chip-icon { color: var(--seal); }
      `}</style>
    </a>
  );
}

function TypingDots() {
  return (
    <span className="td">
      <span /><span /><span />
      <style jsx>{`
        .td { display: inline-flex; gap: 5px; align-items: center; }
        .td > span {
          width: 5px; height: 5px; border-radius: 50%;
          background: var(--ink-mute);
          animation: tdot 1.2s infinite var(--ease);
        }
        .td > span:nth-child(2) { animation-delay: 0.15s; }
        .td > span:nth-child(3) { animation-delay: 0.30s; }
        @keyframes tdot {
          0%, 80%, 100% { opacity: 0.25; transform: scale(0.85); }
          40% { opacity: 1; transform: scale(1); }
        }
      `}</style>
    </span>
  );
}

function fmtTs(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
}
function idShort(agent: string): string {
  return ({
    coordinator: "AGT-01", material: "AGT-02", "point-extractor": "AGT-03",
    structure: "AGT-04", "upward-opt": "AGT-05", copywriter: "AGT-06",
    "html-designer": "AGT-07", "video-producer": "AGT-08", reviewer: "AGT-09",
  } as Record<string, string>)[agent] || agent.toUpperCase().slice(0, 8);
}

function reportTypeLabel(t: string): string {
  return ({
    daily: "日常工作汇报", project_progress: "项目进度汇报",
    review: "基层管理述职", introduction: "工作介绍",
  } as Record<string, string>)[t] || t;
}

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

function iconForMime(mime: string, filename: string): string {
  const ext = (filename.split(".").pop() || "").toLowerCase();
  if (mime.startsWith("image/"))  return "🖼";
  if (mime.startsWith("audio/"))  return "🎵";
  if (mime.startsWith("video/"))  return "🎬";
  if (mime.startsWith("text/"))   return "📄";
  if (["pdf"].includes(ext))                                         return "📕";
  if (["doc", "docx"].includes(ext))                                 return "📘";
  if (["xls", "xlsx", "csv"].includes(ext))                          return "📗";
  if (["ppt", "pptx"].includes(ext))                                 return "📙";
  if (["zip", "rar", "7z", "tar", "gz", "bz2"].includes(ext))        return "🗜";
  if (["json", "yaml", "yml", "xml", "html", "md", "log"].includes(ext)) return "📄";
  return "📎";
}

function Attach({ a }: { a: Attachment }) {
  const icon = iconForMime(a.mime, a.filename);
  const isImg = a.mime.startsWith("image/");
  return (
    <a href={a.url} target="_blank" rel="noreferrer" className="att" title={`${a.filename} · ${bytes(a.size)}`}>
      {isImg ? (
        <span className="att-thumb">
          <img src={a.url} alt={a.filename} />
        </span>
      ) : (
        <span className="att-icon">{icon}</span>
      )}
      <span className="att-body">
        <span className="att-name">{a.filename}</span>
        <span className="serial att-size">{bytes(a.size)}</span>
      </span>
      <style jsx>{`
        .att {
          display: flex; align-items: center; gap: 0.55rem;
          padding: 0.45rem 0.55rem;
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          text-decoration: none;
          color: var(--ink);
          transition: border-color var(--t-base) var(--ease);
          min-width: 0;
        }
        .att:hover { border-color: var(--ink); }
        .att-icon, .att-thumb {
          flex-shrink: 0;
          width: 32px; height: 32px;
          display: inline-flex; align-items: center; justify-content: center;
          background: var(--paper-warm);
          border: 1px solid var(--line-soft);
          border-radius: var(--r-2);
          font-size: 1.05rem;
          overflow: hidden;
        }
        .att-thumb :global(img) {
          width: 100%; height: 100%; object-fit: cover;
        }
        .att-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
        .att-name {
          font-size: var(--t-sm);
          color: var(--ink);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        :global(.att-size) { color: var(--ink-mute); }
      `}</style>
    </a>
  );
}
