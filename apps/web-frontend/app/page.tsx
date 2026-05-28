"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import ChatSidePanel from "@/components/ChatSidePanel";

const WEB_API_BASE = process.env.NEXT_PUBLIC_WEB_API_BASE || "http://localhost:8000";

type Attachment = {
  file_id: string;
  filename: string;
  size: number;
  mime: string;
  url: string;
};

type ReportMeta = {
  title: string;
  audience: string;
  duration: string;
  style: string;
  report_type: string;
  preview?: string;
};

type Artifact = {
  label: string;
  url: string;             // /api/tasks/{id}/exports/...
  kind: string;            // json / txt / md / mp4 / mp3 / srt / html / ...
  open_in_new?: boolean;   // 投屏 HTML 这种新 tab 打开
  probe?: boolean;         // 真存在性需要 list_exports 验证(video/mp4)
};

type ChatMsg = {
  id: string;
  agent: string;
  display_name: string;
  seal: string;
  ts: number;
  kind: "user" | "intro" | "result" | "system" | "error";
  text: string;
  tokens?: number;
  attachments?: Attachment[];
  artifacts?: Artifact[];   // agent 产出的文件
  analysis?: string;        // agent 的思考过程(LLM 文本去掉 JSON 块后的解释)
  task_id?: string;
  report_meta?: ReportMeta;
};

const MEMBERS = [
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
  const [refining, setRefining] = useState(false);          // refine chip 点击中,防双发
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

  // PRD §9.4 5 个快捷动作 → POST /tasks/{id}/refine
  // 后端 REFINE_ACTION_MAP 决定重跑哪个 step + 下游级联,前端只发 action 名
  async function refine(action: string) {
    if (!taskId || refining) return;
    setRefining(true);
    try {
      await fetch(`/api/tasks/${taskId}/refine`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      // 后端会异步开 refine_with_action,把 ack 通过 SSE 推回 chat,UI 不用做 optimistic 更新
    } finally {
      setRefining(false);
    }
  }

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
          {/* PRD §9.4 5 个快捷调整动作 — 仅 task 已完成态(done/partial)显示 */}
          {taskId && (taskStatus === "done" || taskStatus === "partial") && (
            <div className="refine-chips" aria-label="快捷调整">
              <span className="chip-hint">快捷调整 →</span>
              <button className="chip" disabled={refining} onClick={() => refine("shorter")} title="把讲稿砍 30-40%">再短一点</button>
              <button className="chip" disabled={refining} onClick={() => refine("more_problem")} title="拔高风险与影响">更突出问题</button>
              <button className="chip" disabled={refining} onClick={() => refine("more_formal")} title="升级到更书面正式语气">更正式一点</button>
              <button className="chip" disabled={refining} onClick={() => refine("more_result")} title="强化已落地成果的量化数据">更突出成果</button>
              <button className="chip" disabled={refining} onClick={() => refine("regenerate_segment")} title="重新生成全部讲稿">重新生成</button>
              {refining && <span className="chip-busy">正在重做…</span>}
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

function Bubble({ msg, prev }: { msg: ChatMsg; prev?: ChatMsg }) {
  const isUser = msg.agent === "user";
  const isSystem = msg.agent === "system";
  const sameAgent = prev && prev.agent === msg.agent && msg.kind !== "system" && !isUser;

  if (isSystem) {
    const hasMeta = msg.report_meta || (msg.attachments && msg.attachments.length);

    // 富系统消息：含 report_meta / attachments 时用卡片样式
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
          {msg.text === "…" ? <TypingDots /> : msg.text}
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
  // probe 模式(video/intro.mp4 这种可能没真生成):用 HEAD 探测
  useEffect(() => {
    if (!a.probe) return;
    fetch(a.url, { method: "HEAD" }).then(r => { if (!r.ok) setMissing(true); }).catch(() => setMissing(true));
  }, [a.url, a.probe]);
  if (missing) return null;

  // 浏览器能直接 inline 显示的 kind → open in tab;否则 download
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
