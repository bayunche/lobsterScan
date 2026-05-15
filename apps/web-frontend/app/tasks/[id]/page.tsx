"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import SideRail from "@/components/SideRail";

type StepStatus = "pending" | "running" | "success" | "failed" | "skipped";

type Step = {
  step: string;
  label: string;
  agent: string;
  status: StepStatus;
  tokens?: number;
  duration_ms?: number | null;
};
type Artifact = { url: string; size: number };
type Task = {
  task_id: string;
  title: string;
  status: string;
  steps: Step[];
  artifacts: Record<string, Artifact>;
};
type Attachment = {
  file_id: string;
  filename: string;
  size: number;
  mime: string;
  url: string;
};

type ChatMsg = {
  id: string;
  agent: string;
  display_name: string;
  avatar?: string;
  seal?: string;
  ts: number;
  kind: "intro" | "result" | "error" | "user" | "system";
  text: string;
  attachments?: Attachment[];
  report_meta?: { title: string; audience: string; duration: string; style: string; report_type: string; preview?: string };
  task_id?: string;
};

const SEAL_CHAR: Record<string, string> = {
  coordinator: "调", material: "料", "point-extractor": "析",
  structure: "纲", "upward-opt": "译", copywriter: "文",
  "html-designer": "设", "video-producer": "影", reviewer: "校",
  user: "阅",
};

const STATUS_LABEL: Record<StepStatus, string> = {
  pending: "待执行", running: "进行中", success: "已完成", failed: "失败", skipped: "已跳过",
};

const ARTIFACT_LABEL: Record<string, string> = {
  "material_parsing.json":    "素材池",
  "point_extraction.json":    "工作重点",
  "structure_building.json":  "大纲",
  "upward_optimization.json": "优化稿",
  "copywriting.json":         "讲稿（结构）",
  "script.md":                "汇报讲稿",
  "html_design.json":         "HTML 工程",
  "video_production.json":    "视频元数据",
  "review.json":              "审校建议",
  "task.json":                "整体快照",
};

export default function TaskPage() {
  const { id } = useParams<{ id: string }>();
  const [task, setTask] = useState<Task | null>(null);
  const [chat, setChat] = useState<ChatMsg[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [hasIntroVideo, setHasIntroVideo] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  async function fetchTask() {
    try {
      const r = await fetch(`/api/tasks/${id}`, { cache: "no-store" });
      if (r.ok) setTask(await r.json());
    } catch {}
  }

  // 嗅探数字人开场视频是否已生成（用 list_exports，避免 HEAD 兼容问题）
  async function probeIntroVideo() {
    try {
      const r = await fetch(`/api/tasks/${id}/exports`, { cache: "no-store" });
      if (!r.ok) return;
      const d = await r.json();
      const has = (d.items || []).some((it: { name: string }) => it.name === "video/intro.mp4");
      setHasIntroVideo(has);
    } catch { /* ignore */ }
  }

  useEffect(() => {
    fetchTask();
    probeIntroVideo();
    fetch(`/api/tasks/${id}/chat`).then(r => r.ok ? r.json() : { messages: [] })
      .then(d => setChat(d.messages || []));

    const es = new EventSource(`/api/tasks/${id}/events`);
    es.addEventListener("task.step", () => fetchTask());
    es.addEventListener("task.artifact", () => { fetchTask(); probeIntroVideo(); });
    es.addEventListener("chat.message", (e) => {
      const m = JSON.parse((e as MessageEvent).data) as ChatMsg;
      setChat(cur => cur.find(x => x.id === m.id) ? cur : [...cur, m]);
    });
    es.addEventListener("chat.message.update", (e) => {
      const m = JSON.parse((e as MessageEvent).data) as ChatMsg;
      setChat(cur => cur.map(x => x.id === m.id ? m : x));
    });
    es.addEventListener("task.done", (e) => {
      fetchTask();
      // task.done.status = running 时表示 refine 进行中，不要关 SSE
      try {
        const d = JSON.parse((e as MessageEvent).data || "{}");
        if (d.status !== "running") es.close();
      } catch { es.close(); }
    });
    const t = setInterval(fetchTask, 8000);
    return () => { es.close(); clearInterval(t); };
  }, [id]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [chat.length]);

  async function send() {
    const t = draft.trim();
    if (!t || sending) return;
    setSending(true); setDraft("");
    try {
      await fetch(`/api/tasks/${id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: t }),
      });
    } finally { setSending(false); }
  }

  if (!task) {
    return <main className="task-loading"><span className="serial">LOADING ··</span></main>;
  }

  const done = task.steps.filter(s => s.status === "success").length;
  const failed = task.steps.filter(s => s.status === "failed").length;
  const statusTag = task.status === "done" ? "tag-ok"
                  : task.status === "partial" ? "tag-warn"
                  : task.status === "failed" ? "tag-fail" : "tag-seal";
  const statusText = ({
    done: "已完成", partial: "部分完成", failed: "失败", running: "推进中",
  } as Record<string, string>)[task.status] || task.status;

  return (
    <div className="app-grid">
    <SideRail />
    <main className="task-shell">
      <header className="task-head">
        <div>
          <div className="serial">
            <Link href="/" className="head-back">⟵ 龙虾集群</Link>
            <span className="tick" />
            TASK · {task.task_id}
          </div>
          <h1 className="display display-md mt-2">{task.title}</h1>
          <div className="task-meta">
            <span className={"tag " + statusTag}>● {statusText}</span>
            <span className="serial">{done} / {task.steps.length} 完成{failed > 0 && ` · ${failed} 失败`}</span>
          </div>
        </div>
        <div className="serial regbox">
          <span className="regmark" />
          <span>PG / TASK</span>
        </div>
      </header>

      {/* 8 节进度条 */}
      <div className="step-strip">
        {task.steps.map((s, i) => (
          <div key={s.step} className={"step-cell step-" + s.status} title={`${s.label} · ${STATUS_LABEL[s.status]}`}>
            <span className="serial step-no">{String(i + 1).padStart(2, "0")}</span>
            <span className="step-bar" />
            <span className="step-label">{s.label}</span>
          </div>
        ))}
      </div>

      {hasIntroVideo && (
        <section className="intro-vid mt-6">
          <div className="intro-vid-head serial">
            <span className="seal" data-agent="video-producer">影</span>
            <span>§ 数字人开场</span>
            <span className="intro-vid-rule" />
            <a
              className="intro-vid-dl"
              href={`/api/tasks/${id}/exports/video/intro.mp4`}
              download="intro.mp4"
            >下载 mp4 ⤓</a>
          </div>
          <video
            className="intro-vid-player"
            src={`/api/tasks/${id}/exports/video/intro.mp4`}
            controls
            playsInline
            preload="metadata"
          />
        </section>
      )}

      <hr className="hairline mt-6 mb-6" />

      <div className="task-grid">
        {/* ─── 群聊 ─── */}
        <section className="conv">
          <div ref={scrollRef} className="conv-scroll">
            <div className="serial conv-note">§ 9 位成员加入了这场汇报准备</div>
            <div className="conv-stream">
              {chat.map(m => <Bubble key={m.id} msg={m} />)}
              {chat.length === 0 && <div className="serial ink-mute mt-8">团队即将就位 ··</div>}
            </div>
          </div>

          <div className="composer">
            <div className="composer-meta serial">
              <span>@汇报总控 可以提出调整 / 重写 / 强调要点</span>
              <span>⌘⏎</span>
            </div>
            <div className="composer-row">
              <textarea
                value={draft}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); }
                }}
                rows={2}
                placeholder="想让团队哪里再优化？"
                className="field-textarea"
              />
              <button onClick={send} disabled={!draft.trim() || sending} className="btn-ink">
                {sending ? "发送中" : "发送"}
              </button>
            </div>
          </div>
        </section>

        {/* ─── 产物 ─── */}
        <aside className="artifacts">
          <div className="serial mb-3">§ 汇报材料</div>
          {Object.keys(task.artifacts).length === 0 ? (
            <div className="art-empty">产物生成中 ··</div>
          ) : (
            <ul className="art-list">
              {Object.entries(task.artifacts).map(([name, a]) => (
                <li key={name}>
                  <a href={a.url} className="art-item">
                    <div className="art-label">{ARTIFACT_LABEL[name] || name}</div>
                    <div className="art-foot">
                      <span className="font-mono serial">{name}</span>
                      <span className="serial">{Math.round(a.size / 100) / 10} KB</span>
                    </div>
                  </a>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>

      <style jsx>{`
        .task-shell {
          max-width: 1280px;
          margin: 0 auto;
          padding: 2rem 2rem 1.5rem;
          min-height: 100vh;
          display: flex;
          flex-direction: column;
        }
        .task-loading {
          min-height: 100vh; display: grid; place-items: center;
          color: var(--ink-mute);
        }
        .task-head {
          display: flex; justify-content: space-between; align-items: flex-start;
          gap: 2rem;
        }
        .head-back { color: var(--ink-mute); text-decoration: none; }
        .head-back:hover { color: var(--seal); }
        .task-meta { display: flex; align-items: center; gap: 0.75rem; margin-top: 0.5rem; }
        .regbox { display: flex; align-items: center; gap: 0.5rem; color: var(--ink-mute); padding-top: 0.5rem; }

        .step-strip {
          display: grid;
          grid-template-columns: repeat(8, 1fr);
          gap: 0.75rem;
          margin-top: 1.5rem;
        }
        .step-cell {
          display: flex; flex-direction: column; gap: 0.4rem;
        }
        .step-no { color: var(--ink-dim); }
        .step-bar {
          height: 3px;
          background: var(--line-soft);
          transition: background var(--t-base) var(--ease);
        }
        .step-label {
          font-size: 0.7rem;
          color: var(--ink-mute);
          line-height: 1.3;
        }
        .step-pending .step-bar  { background: var(--line-soft); }
        .step-running .step-bar  { background: var(--seal); animation: bar-pulse 1.4s infinite var(--ease); }
        .step-success .step-bar  { background: var(--ink); }
        .step-success .step-no   { color: var(--ink); }
        .step-success .step-label{ color: var(--ink-soft); }
        .step-failed  .step-bar  { background: var(--fail); }
        .step-skipped .step-bar  { background: var(--warn); }
        @keyframes bar-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }

        .intro-vid {
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          background: var(--paper);
          padding: 0.85rem 1rem 1rem;
          box-shadow: 0 1px 2px oklch(0.20 0.02 250 / 0.04);
        }
        .intro-vid-head {
          display: flex; align-items: center; gap: 0.55rem;
          color: var(--ink-mute);
          margin-bottom: 0.7rem;
          font-size: 0.72rem;
          letter-spacing: 0.12em;
        }
        :global(.intro-vid-head .seal) { font-size: 0.85em; }
        .intro-vid-rule { flex: 1; height: 1px; background: var(--line-soft); margin: 0 0.25rem; }
        .intro-vid-dl {
          color: var(--seal);
          text-decoration: none;
          font-family: var(--font-mono);
        }
        .intro-vid-dl:hover { text-decoration: underline; }
        .intro-vid-player {
          width: 100%;
          max-height: 360px;
          background: oklch(0.18 0.01 250);
          border-radius: var(--r-1);
          display: block;
        }

        .task-grid {
          display: grid;
          grid-template-columns: 1fr 280px;
          gap: 2rem;
          flex: 1;
          min-height: 0;
        }
        @media (max-width: 880px) {
          .task-grid { grid-template-columns: 1fr; }
        }

        .conv { display: flex; flex-direction: column; min-height: 0; }
        .conv-scroll { flex: 1; overflow-y: auto; padding-right: 0.5rem; }
        .conv-note { text-align: center; margin: 0.5rem 0 1.5rem; color: var(--ink-mute); }
        .conv-stream { display: flex; flex-direction: column; gap: 1.25rem; }

        .composer {
          margin-top: 1rem; padding-top: 0.75rem;
          border-top: 1px solid var(--line);
        }
        .composer-meta {
          display: flex; justify-content: space-between;
          color: var(--ink-mute); margin-bottom: 0.5rem;
        }
        .composer-row { display: flex; gap: 0.75rem; align-items: stretch; }
        :global(.composer-row textarea) { flex: 1; }
        :global(.composer-row .btn-ink) { padding-left: 1.4rem; padding-right: 1.4rem; }

        .artifacts {
          border-left: 1px solid var(--line);
          padding-left: 1.5rem;
        }
        @media (max-width: 880px) {
          .artifacts { border-left: 0; padding-left: 0; padding-top: 1.5rem; border-top: 1px solid var(--line); }
        }
        .art-empty {
          padding: 2rem 1rem;
          text-align: center;
          color: var(--ink-mute);
          font-size: var(--t-sm);
          border: 1px dashed var(--line);
          border-radius: var(--r-2);
        }
        .art-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.5rem; }
        .art-item {
          display: block; padding: 0.6rem 0.75rem;
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          background: var(--paper);
          text-decoration: none; color: inherit;
          transition: border-color var(--t-base) var(--ease);
        }
        .art-item:hover { border-color: var(--ink); }
        .art-label { font-size: var(--t-sm); color: var(--ink); font-weight: 500; }
        .art-foot {
          display: flex; justify-content: space-between; margin-top: 4px;
          color: var(--ink-mute);
        }

        /* ───── 响应式 ───── */
        @media (max-width: 880px) {
          .task-shell { padding: 1.25rem 1rem 1rem; }
          .task-head { flex-direction: column; gap: 0.65rem; }
          .step-strip {
            grid-template-columns: repeat(4, 1fr);
            gap: 0.5rem;
          }
          .step-label { font-size: 0.62rem; }
        }
        @media (max-width: 480px) {
          .step-strip { grid-template-columns: repeat(4, 1fr); gap: 0.4rem; }
          .step-label { display: none; }     /* 仅留 No + bar */
          .composer-meta { font-size: 0.62rem; flex-direction: column; align-items: flex-start; gap: 2px; }
          :global(.composer-row .btn-ink) { padding: 0.55rem 0.85rem; }
        }
      `}</style>
    </main>
    </div>
  );
}

function Bubble({ msg }: { msg: ChatMsg }) {
  const isUser = msg.agent === "user";
  const seal = SEAL_CHAR[msg.agent] || msg.avatar || "·";

  if (isUser) {
    return (
      <div className="bub bub-user animate-type">
        <div className="bub-head bub-head-r">
          <span className="serial">{fmtTs(msg.ts)}</span>
          <span className="serial">你</span>
          <span className="seal" data-agent="user">{seal}</span>
        </div>
        <div className="bub-user-body">{msg.text}</div>
        <style jsx>{`
          .bub-user { align-items: flex-end; display: flex; flex-direction: column; gap: 0.4rem; }
          .bub-head-r { justify-content: flex-end; }
          .bub-user-body {
            background: var(--ink);
            color: var(--paper);
            padding: 0.85rem 1.05rem;
            border-radius: var(--r-2);
            max-width: 80%;
            text-align: left;
            font-size: var(--t-base);
            white-space: pre-wrap;
          }
          .bub-head {
            display: flex; align-items: center; gap: 0.5rem; font-size: var(--t-sm);
          }
          :global(.bub-user .seal) { font-size: 0.85em; }
          :global(.bub-user .serial) { color: var(--ink-mute); }
        `}</style>
      </div>
    );
  }

  return (
    <article className="bub animate-type">
      <header className="bub-head">
        <span className="seal" data-agent={msg.agent}>{seal}</span>
        <span className="bub-name">{msg.display_name}</span>
        <span className="serial bub-id">{idShort(msg.agent)}</span>
        <span className="bub-rule" />
        <span className="serial">{fmtTs(msg.ts)}</span>
      </header>
      <div className={"bub-body" + (msg.kind === "intro" ? " bub-intro" : "") + (msg.kind === "error" ? " bub-error" : "")}>
        {msg.text}
      </div>
      <style jsx>{`
        .bub { display: flex; flex-direction: column; gap: 0.4rem; }
        .bub-head {
          display: flex; align-items: center; gap: 0.5rem;
          font-size: var(--t-sm);
        }
        .bub-name { font-weight: 600; color: var(--ink); }
        .bub-id { font-family: var(--font-mono); color: var(--ink-mute); }
        .bub-rule { flex: 1; height: 1px; background: var(--line); margin: 0 0.25rem; }
        .bub-body {
          padding-left: 2.1rem;
          font-size: var(--t-base);
          line-height: var(--leading-normal);
          color: var(--ink);
          white-space: pre-wrap;
        }
        .bub-intro { color: var(--ink-soft); font-style: italic; }
        .bub-error { color: var(--fail); }
        :global(.bub .seal) { font-size: 0.85em; }
        :global(.bub .serial) { color: var(--ink-mute); }
      `}</style>
    </article>
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
