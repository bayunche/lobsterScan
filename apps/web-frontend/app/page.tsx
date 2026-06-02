"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import ChatSidePanel from "@/components/ChatSidePanel";
// P7(spec 007-chat-ux):Bubble 及类型/成员表抽到独立组件(Next page 不允许 named export)。
import { Bubble, MEMBERS, PROMPT_TEMPLATES, type ChatMsg } from "@/components/Bubble";

const WEB_API_BASE = process.env.NEXT_PUBLIC_WEB_API_BASE || "http://localhost:8000";

const SESSION_KEY = "lobster-chat-session";

function initialMessages(): ChatMsg[] {
  const now = Math.floor(Date.now() / 1000);
  return [
    { id: "sys-welcome", agent: "system", display_name: "系统", seal: "·",
      ts: now, kind: "system",
      text: "九位常驻成员已加入会议室。直接说话，或 @ 某位单聊。" },
    { id: "intro-coordinator", agent: "coordinator", display_name: "汇报总控", seal: "调",
      ts: now, kind: "intro",
      text: "我是总控。要做汇报材料就说一声，或者随便聊聊也行。" },
    { id: "intro-material", agent: "material", display_name: "资料员", seal: "料",
      ts: now + 1, kind: "intro",
      text: "扔给我任何工作记录 / 表格 / 截图，我能拆成结构化素材池。" },
    { id: "intro-point-extractor", agent: "point-extractor", display_name: "分析师", seal: "析",
      ts: now + 2, kind: "intro",
      text: "要「讲到点子上」就找我。我挑领导真正关心的几条重点。" },
    { id: "intro-reviewer", agent: "reviewer", display_name: "质量检查员", seal: "校",
      ts: now + 3, kind: "intro",
      text: "成稿后我做最后一轮检查，给可执行的改进建议。" },
  ];
}

export default function HomePage() {
  // useSearchParams 需要 Suspense 边界（Next.js CSR bailout），否则 next build 预渲染会报错
  return (
    <Suspense fallback={<div className="page-bg" />}>
      <HomePageInner />
    </Suspense>
  );
}

function HomePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const taskId = searchParams.get("task");  // 来自 /tasks 列表点击 → /?task=tsk_xxx
  const [sid, setSid] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [taskTitle, setTaskTitle] = useState<string>("");   // task 模式下顶部显示
  const [taskStatus, setTaskStatus] = useState<string>(""); // running/done/partial/failed — 决定 refine chip 是否显示
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let s = sessionStorage.getItem(SESSION_KEY);
    if (!s) {
      s = "ses_" + Math.random().toString(36).slice(2, 14);
      sessionStorage.setItem(SESSION_KEY, s);
    }
    setSid(s);
  }, []);

  // 两种会话源:有 ?task 走 task chat,否则走 cluster session
  useEffect(() => {
    if (taskId) {
      // task 会话模式:fetch 该 task 的 chat,SSE 直连 web-backend(避免 Next rewrites SSE 缓冲)
      const refreshDetail = () => fetch(`/api/tasks/${taskId}`).then(r => r.ok ? r.json() : null)
        .then(d => { setTaskTitle(d?.title || ""); setTaskStatus(d?.status || ""); });
      refreshDetail();
      fetch(`/api/tasks/${taskId}/chat`).then(r => r.ok ? r.json() : { messages: [] })
        .then(d => setMessages(d.messages || []));
      const es = new EventSource(`${WEB_API_BASE}/api/tasks/${taskId}/events`);
      es.addEventListener("chat.message", (e) => {
        const m = JSON.parse((e as MessageEvent).data);
        setMessages(cur => cur.find(x => x.id === m.id) ? cur : [...cur, m]);
      });
      es.addEventListener("chat.message.update", (e) => {
        const m = JSON.parse((e as MessageEvent).data);
        setMessages(cur => cur.map(x => x.id === m.id ? m : x));
      });
      // task.done 事件 → 刷新 status,触发 refine chip 行显示/隐藏
      es.addEventListener("task.done", () => { refreshDetail(); });
      return () => es.close();
    }
    // 集群会话模式(默认)
    if (!sid) return;
    setTaskTitle("");
    setTaskStatus("");
    fetch(`/api/cluster/chat/${sid}/messages`).then(r => r.json()).then(d => {
      const fromServer: ChatMsg[] = d.messages || [];
      setMessages(fromServer.length > 0 ? fromServer : initialMessages());
    }).catch(() => setMessages(initialMessages()));

    const es = new EventSource(`/api/cluster/chat/${sid}/events`);
    es.addEventListener("chat.message", (e) => {
      const m = JSON.parse((e as MessageEvent).data);
      setMessages(cur => cur.find(x => x.id === m.id) ? cur : [...cur, m]);
    });
    es.addEventListener("chat.message.update", (e) => {
      const m = JSON.parse((e as MessageEvent).data);
      setMessages(cur => cur.map(x => x.id === m.id ? m : x));
    });
    return () => es.close();
  }, [sid, taskId, router]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);

  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  }, [draft]);

  // P7(spec 007 · US4):快捷调整改为 prompt 模板(点击填入输入框,见 PROMPT_TEMPLATES),
  // 用户编辑后走 send → /tasks/{id}/chat(后端触发 refine 流程)。原 refine() 直发已移除。

  async function send(text?: string) {
    const t = (text ?? draft).trim();
    if (!t || sending) return;
    if (!taskId && !sid) return;
    setSending(true);
    setDraft("");
    try {
      if (taskId) {
        // task 模式:发到 task chat(后端会触发 refine 流程)
        await fetch(`/api/tasks/${taskId}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: t }),
        });
      } else {
        // 集群模式
        await fetch("/api/cluster/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: t, session_id: sid }),
        });
      }
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="page-bg">
      <div className="hall">
        {taskId && (
          <div className="task-banner serial" title={taskId}>
            <span className="regmark" />
            <span>正在查看任务对话</span>
            {taskTitle ? <> · <span>{taskTitle}</span></> : <> · <span>未命名任务</span></>}
            <span className="grow" />
            <Link href="/" className="back-link">⟵ 返回集群对话</Link>
            <Link href={`/tasks/${taskId}`} className="back-link ml-3">看任务详情 →</Link>
          </div>
        )}
        <header className="hall-head">
          <div className="head-top">
            <div className="brand-row">
              <span className="brand-mark">🦞</span>
              <div>
                <div className="brand-name">龙虾集群</div>
                <div className="brand-sub serial">OPENCLAW · 9 AGENTS · LIVE</div>
              </div>
            </div>
          </div>
          <div className="strip-wrap">
            <div className="member-strip">
              {MEMBERS.map((m) => (
                <button
                  key={m.id}
                  className="mbr"
                  title={`@${m.name}`}
                  onClick={() => setDraft((d) => (d ? d + " " : "") + `@${m.name} `)}
                >
                  <span className="seal seal-sm" data-agent={m.id}>{m.seal}</span>
                  <span className="mbr-name">{m.name}</span>
                  <span className="dot dot-on mbr-dot" />
                </button>
              ))}
            </div>
          </div>
        </header>

        <div className="middle">
          <section className="conv" ref={scrollRef}>
            <div className="conv-inner">
              {messages.map((m, i) => (
                <Bubble key={m.id} msg={m} prev={messages[i - 1]} />
              ))}
              {messages.length === 0 && (
                <div className="conv-load serial">·· LOADING ··</div>
              )}
            </div>
          </section>
          <ChatSidePanel />
        </div>

        <footer className="composer">
          {/* P7(spec 007 · US4):快捷调整模板 — 点击填入输入框可编辑后发送(FR-009/010)。
              仅 task 已完成态(done/partial)显示。*/}
          {taskId && (taskStatus === "done" || taskStatus === "partial") && (
            <div className="refine-chips" aria-label="快捷调整模板">
              <span className="chip-hint">常用调整 →</span>
              {PROMPT_TEMPLATES.map((tpl) => (
                <button
                  key={tpl.label}
                  className="chip"
                  onClick={() => { setDraft(tpl.text); taRef.current?.focus(); }}
                  title="填入输入框,可补充后发送"
                >
                  {tpl.label}
                </button>
              ))}
            </div>
          )}
          <div className="composer-pad">
            <button
              className="cp-icon"
              onClick={() => router.push("/feat/report")}
              title="上传材料，触发汇报生成"
            >
              📂
            </button>
            <textarea
              ref={taRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                // 桌面: Cmd/Ctrl+Enter 发送；手机/触屏: 没有快捷键，靠按钮
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={1}
              placeholder="跟集群说点啥 — 闲聊 / 工作记录 / 提问"
              className="cp-input"
            />
            <button
              onClick={() => send()}
              disabled={!draft.trim() || sending}
              className="cp-send"
            >
              <span>{sending ? "发送中" : "发送"}</span>
              <span className="serial cp-hint">⌘⏎</span>
            </button>
          </div>
          <div className="cp-meta serial">
            <span>SESSION · {(sid || "—").slice(0, 14)}</span>
            <span className="cp-meta-r">
              <span className="dot dot-on" /> 9 / 9 在线
            </span>
          </div>
        </footer>
      </div>

      <style jsx>{`
        .task-banner {
          display: flex; align-items: center; gap: 0.5rem;
          padding: 0.55rem 1.25rem;
          border-bottom: 1px solid var(--line);
          background: var(--paper-warm);
          color: var(--ink-soft);
          font-size: 0.78rem;
        }
        .task-banner .grow { flex: 1; }
        .back-link {
          color: var(--ink);
          text-decoration: none;
          font-size: 0.78rem;
        }
        .back-link:hover { color: var(--seal); }

        .page-bg {
          min-height: 100vh;
          display: flex;
          justify-content: center;
        }
        .hall {
          width: 100%;
          max-width: 1280px;
          display: grid;
          grid-template-rows: auto 1fr auto;
          height: 100vh;
          min-width: 0;
        }
        .middle {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 304px;
          min-height: 0;
          overflow: hidden;
        }
        @media (max-width: 1024px) {
          .middle { grid-template-columns: minmax(0, 1fr) 272px; }
        }
        @media (max-width: 880px) {
          .middle { grid-template-columns: minmax(0, 1fr); }
          :global(.side) { display: none; }
        }
        .hall-head {
          display: flex;
          flex-direction: column;
          gap: 0.9rem;
          padding: 1.1rem 1.75rem 0.85rem;
          border-bottom: 1px solid var(--line);
          background: var(--glass-surface-2);
          backdrop-filter: blur(var(--glass-blur-2));
          -webkit-backdrop-filter: blur(var(--glass-blur-2));
          min-width: 0;
        }
        .head-top {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 1.5rem;
          min-width: 0;
        }
        .brand-row {
          display: flex; align-items: center; gap: 0.75rem;
          min-width: 0;
        }
        .brand-mark { font-size: 1.65rem; filter: saturate(0.7) brightness(0.95); }
        .brand-name {
          font-family: var(--font-serif);
          font-size: var(--t-xl);
          font-weight: 700;
          line-height: 1;
          background: linear-gradient(135deg, var(--color-primary-500), var(--color-accent-500));
          -webkit-background-clip: text;
          background-clip: text;
          -webkit-text-fill-color: transparent;
          color: transparent;
        }
        .brand-sub {
          color: var(--ink-mute);
          font-size: 0.62rem;
          margin-top: 4px;
          letter-spacing: 0.16em;
        }
        .strip-wrap {
          position: relative;
          margin: 0 -1.75rem;             /* 撑到 header 边缘 */
        }
        .strip-wrap::before,
        .strip-wrap::after {
          content: "";
          position: absolute; top: 0; bottom: 4px; width: 28px;
          pointer-events: none;
          z-index: 1;
        }
        .strip-wrap::before {
          left: 0;
          background: linear-gradient(90deg, var(--paper) 0%, transparent 100%);
        }
        .strip-wrap::after {
          right: 0;
          background: linear-gradient(270deg, var(--paper) 0%, transparent 100%);
        }
        .member-strip {
          display: flex; gap: 0.55rem;
          overflow-x: auto;
          padding: 0 1.75rem 6px;
          scrollbar-width: thin;
          scroll-snap-type: x proximity;
        }
        .member-strip::-webkit-scrollbar { height: 4px; }
        .member-strip::-webkit-scrollbar-thumb {
          background: var(--line);
          border-radius: var(--r-pill);
        }
        .member-strip::-webkit-scrollbar-thumb:hover { background: var(--ink-mute); }
        :global(.mbr) {
          display: flex; align-items: center; gap: 0.45rem;
          padding: 0.3rem 0.7rem 0.3rem 0.35rem;
          background: var(--glass-surface-1);
          backdrop-filter: blur(var(--glass-blur-1));
          -webkit-backdrop-filter: blur(var(--glass-blur-1));
          border: 1px solid var(--line-soft);
          border-radius: var(--r-pill);
          flex-shrink: 0;
          cursor: pointer;
          transition: all var(--t-fast) var(--ease);
          position: relative;
          scroll-snap-align: start;
          white-space: nowrap;
        }
        :global(.mbr:hover) {
          border-color: var(--ink-soft);
          background: var(--paper);
          transform: translateY(-1px);
          box-shadow: 0 2px 8px oklch(0.20 0.02 250 / 0.07);
        }
        :global(.mbr-name) {
          font-size: 0.78rem;
          color: var(--ink-soft);
        }
        :global(.mbr-dot) {
          position: absolute;
          top: 4px; right: 5px;
        }


        .conv {
          overflow-y: auto;
          padding: 1.5rem 1.75rem 1rem;
          min-width: 0;
        }
        .conv-inner {
          max-width: 760px;
          margin: 0 auto;
          display: flex;
          flex-direction: column;
          gap: 0.85rem;
        }
        .conv-load { color: var(--ink-mute); text-align: center; padding: 3rem 0; }

        .composer {
          padding: 0.85rem 1.75rem 1.1rem;
          border-top: 1px solid var(--line);
          background: var(--paper);
        }
        .refine-chips {
          max-width: 760px; margin: 0 auto 0.55rem;
          display: flex; align-items: center; flex-wrap: wrap; gap: 0.4rem;
          font-size: 0.78rem;
        }
        .chip-hint {
          color: var(--ink-mute);
          letter-spacing: 0.04em;
          margin-right: 0.2rem;
        }
        :global(.chip) {
          padding: 0.3rem 0.7rem;
          border: 1px solid var(--line);
          border-radius: 999px;
          background: var(--paper-warm);
          color: var(--ink);
          font-size: 0.78rem;
          cursor: pointer;
          transition: border-color var(--t-base) var(--ease), background var(--t-base) var(--ease);
        }
        :global(.chip:hover:not(:disabled)) {
          border-color: var(--seal);
          background: var(--paper);
        }
        :global(.chip:disabled) { opacity: 0.45; cursor: wait; }
        .chip-busy {
          color: var(--ink-mute);
          font-style: italic;
          margin-left: 0.3rem;
        }
        .composer-pad {
          max-width: 760px; margin: 0 auto;
          display: flex; align-items: flex-end; gap: 0.6rem;
          background: var(--glass-surface-1);
          backdrop-filter: blur(var(--glass-blur-1));
          -webkit-backdrop-filter: blur(var(--glass-blur-1));
          border: 1px solid var(--line);
          border-radius: var(--r-lg);
          padding: 0.5rem 0.5rem 0.5rem 0.65rem;
          box-shadow: 0 1px 0 oklch(0.20 0.02 250 / 0.03),
                      0 4px 16px -12px oklch(0.20 0.02 250 / 0.14);
          transition: border-color var(--t-base) var(--ease), background var(--t-base) var(--ease);
        }
        .composer-pad:focus-within {
          border-color: var(--ink-soft);
          background: var(--paper);
        }
        :global(.cp-icon) {
          flex-shrink: 0;
          width: 36px; height: 36px;
          display: inline-flex; align-items: center; justify-content: center;
          background: transparent; border: 1px solid var(--line);
          border-radius: var(--r-2);
          font-size: 1rem;
          cursor: pointer;
          transition: all var(--t-fast) var(--ease);
        }
        :global(.cp-icon:hover) { border-color: var(--ink); background: var(--paper); }
        :global(.cp-input) {
          flex: 1;
          background: transparent;
          border: 0;
          outline: none;
          padding: 0.55rem 0.5rem;
          font-family: var(--font-sans);
          font-size: 0.95rem;
          color: var(--ink);
          resize: none;
          min-height: 40px;
          max-height: 220px;
          line-height: 1.5;
        }
        :global(.cp-input::placeholder) { color: var(--ink-mute); }
        :global(.cp-send) {
          flex-shrink: 0;
          background: var(--seal);
          color: #fff;
          border: 0;
          padding: 0.6rem 1.1rem;
          border-radius: var(--r-pill);
          font-family: var(--font-sans);
          font-size: var(--t-sm);
          cursor: pointer;
          display: inline-flex; align-items: center; gap: 0.6rem;
          box-shadow: 0 2px 8px rgba(13, 148, 136, 0.25);
          transition: all var(--t-base) var(--ease);
        }
        :global(.cp-send:hover:not(:disabled)) {
          background: var(--seal-deep);
          box-shadow: 0 4px 16px rgba(13, 148, 136, 0.35);
        }
        :global(.cp-send:disabled) { opacity: 0.35; cursor: not-allowed; box-shadow: none; }
        :global(.cp-hint) { color: #fff; opacity: 0.6; }

        .cp-meta {
          max-width: 760px; margin: 0.5rem auto 0;
          display: flex; justify-content: space-between;
          color: var(--ink-mute);
        }
        .cp-meta-r { display: inline-flex; align-items: center; gap: 4px; }

        /* ───── 响应式 ───── */
        @media (max-width: 880px) {
          .hall-head { padding: 0.9rem 1rem 0.7rem; }
          .strip-wrap { margin: 0 -1rem; }
          :global(.member-strip) { padding: 0 1rem 6px; }
          .conv { padding: 1.1rem 1rem 0.8rem; }
          .composer { padding: 0.7rem 1rem 0.9rem; }
        }
        @media (max-width: 640px) {
          .brand-name { font-size: var(--t-lg); }
          .brand-sub { display: none; }
          .brand-mark { font-size: 1.35rem; }
          .hall-head { gap: 0.6rem; }
          .conv-inner { gap: 0.7rem; }
          :global(.cp-input) { font-size: 0.9rem; min-height: 36px; }
          :global(.cp-send) { padding: 0.5rem 0.9rem; gap: 0; }
          :global(.cp-hint) { display: none; }                    /* 隐 ⌘⏎ 提示 */
          :global(.cp-icon) { width: 32px; height: 32px; font-size: 0.9rem; }
          .cp-meta { font-size: 0.62rem; }
        }
      `}</style>
    </div>
  );
}

