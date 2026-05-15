"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type Task = {
  task_id: string;
  title: string;
  status: string;
  steps: { status: string }[];
};

const STATUS_LABEL: Record<string, string> = {
  running: "推进中", done: "已完成", partial: "部分完成", failed: "失败",
};

export default function ChatSidePanel() {
  const [tasks, setTasks] = useState<Task[]>([]);

  async function load() {
    try {
      const r = await fetch("/api/tasks?limit=6", { cache: "no-store" });
      if (r.ok) {
        const d = await r.json();
        setTasks(d.items || []);
      }
    } catch {}
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <aside className="side">
      {/* 主要功能入口 */}
      <Link href="/feat/report" className="cta">
        <span className="cta-icon">📂</span>
        <span className="cta-body">
          <span className="cta-title">上传材料 · 创建汇报</span>
          <span className="serial cta-sub">把素材交给集群，自动生成全套</span>
        </span>
        <span className="cta-arrow">→</span>
      </Link>

      <section className="block">
        <header className="block-head">
          <span className="serial">§ 活跃任务</span>
          <span className="serial">{tasks.length}</span>
        </header>
        {tasks.length === 0 ? (
          <div className="empty">
            <div className="serial">— 还没有任务 —</div>
            <Link href="/feat/report" className="empty-link">从材料开始 →</Link>
          </div>
        ) : (
          <ul className="task-list">
            {tasks.map((t) => {
              const done = t.steps.filter(s => s.status === "success").length;
              const total = t.steps.length || 8;
              return (
                <li key={t.task_id}>
                  <Link href={`/tasks/${t.task_id}`} className="task-row">
                    <div className="task-row-top">
                      <span className={"task-dot dot " + (
                        t.status === "done" ? "dot-on" :
                        t.status === "failed" ? "dot-fail" :
                        t.status === "partial" ? "dot-warn" : "dot-run"
                      )} />
                      <span className="task-title">{t.title}</span>
                    </div>
                    <div className="task-row-bottom">
                      <span className="task-bars">
                        {Array.from({ length: total }).map((_, i) => (
                          <span key={i} className={"tb " + (i < done ? "tb-on" : "")} />
                        ))}
                      </span>
                      <span className="serial">{done}/{total} · {STATUS_LABEL[t.status] || t.status}</span>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="block tip">
        <div className="serial tip-label">§ 用法提示</div>
        <ul className="tip-list">
          <li>顶部点 <span className="kbd">头像</span> 自动 @ 那位成员单聊</li>
          <li>消息里写 <span className="kbd">@分析师 @质量检查员</span> 可同时 @ 多位</li>
          <li>需要做汇报材料 → 上方的<span className="kbd">📂 上传</span></li>
        </ul>
      </section>

      <a
        href={(process.env.NEXT_PUBLIC_ADMIN_URL || "http://localhost:3100") + "/admin/agents"}
        target="_blank" rel="noreferrer"
        className="admin-entry"
      >
        <span className="ae-icon" aria-hidden="true">
          <svg viewBox="0 0 16 16" fill="none" width="16" height="16">
            <path d="M8 4.8a3.2 3.2 0 100 6.4 3.2 3.2 0 000-6.4z M9.6 1.5l.3 1.7c.5.2 1 .4 1.4.7l1.6-.7 1.6 2.7-1.4 1c.1.3.1.6.1.9s0 .6-.1.9l1.4 1-1.6 2.7-1.6-.7c-.4.3-.9.6-1.4.7L9.6 14h-3.2l-.3-1.7c-.5-.2-1-.4-1.4-.7l-1.6.7-1.6-2.7 1.4-1A4.5 4.5 0 011.4 8c0-.3 0-.6.1-.9l-1.4-1L1.7 3.4l1.6.7c.4-.3.9-.6 1.4-.7L5 1.5h4.6z"
              stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
          </svg>
        </span>
        <span className="ae-body">
          <span className="ae-title">管理控制台</span>
          <span className="serial ae-sub">Agent · Skill · 配置 · 监控</span>
        </span>
        <span className="ae-arrow">↗</span>
      </a>

      <style jsx>{`
        .side {
          width: 100%;
          border-left: 1px solid var(--line);
          background: var(--paper);
          padding: 1.25rem 1.15rem 1.25rem;
          display: flex;
          flex-direction: column;
          gap: 1.1rem;
          overflow-y: auto;
          overflow-x: hidden;
          min-width: 0;
        }

        /* === 主 CTA === */
        :global(.cta) {
          display: flex; align-items: center; gap: 0.75rem;
          padding: 0.85rem 0.95rem;
          background: var(--ink);
          color: var(--paper);
          border-radius: var(--r-4);
          text-decoration: none;
          box-shadow: 0 2px 4px oklch(0.20 0.02 250 / 0.08),
                      0 12px 24px -16px oklch(0.20 0.02 250 / 0.4);
          transition: all var(--t-base) var(--ease);
          position: relative;
          overflow: hidden;
        }
        :global(.cta)::before {
          content: "";
          position: absolute; inset: 0;
          background: linear-gradient(135deg, oklch(0.55 0.18 30 / 0.12) 0%, transparent 60%);
          opacity: 0;
          transition: opacity var(--t-base) var(--ease);
        }
        :global(.cta:hover) {
          transform: translateY(-1px);
          box-shadow: 0 4px 8px oklch(0.20 0.02 250 / 0.12),
                      0 16px 32px -16px oklch(0.20 0.02 250 / 0.5);
        }
        :global(.cta:hover::before) { opacity: 1; }
        :global(.cta-icon) {
          width: 38px; height: 38px;
          background: oklch(0.30 0.025 245);
          border-radius: var(--r-2);
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 1.1rem;
          flex-shrink: 0;
          position: relative; z-index: 1;
        }
        :global(.cta-body) {
          flex: 1; min-width: 0;
          display: flex; flex-direction: column; gap: 2px;
          position: relative; z-index: 1;
        }
        :global(.cta-title) {
          font-family: var(--font-serif);
          font-size: 0.95rem;
          font-weight: 600;
        }
        :global(.cta-sub) { color: var(--paper); opacity: 0.6; }
        :global(.cta-arrow) {
          font-family: var(--font-mono);
          font-size: 1.1rem;
          opacity: 0.7;
          transition: transform var(--t-base) var(--ease);
          position: relative; z-index: 1;
        }
        :global(.cta:hover .cta-arrow) { transform: translateX(3px); opacity: 1; }

        /* === 区块 === */
        .block { display: flex; flex-direction: column; gap: 0.55rem; }
        .block-head {
          display: flex; justify-content: space-between;
          color: var(--ink-mute);
        }

        .empty {
          padding: 1.25rem 0.65rem;
          text-align: center;
          border: 1px dashed var(--line);
          border-radius: var(--r-2);
          display: flex; flex-direction: column; gap: 0.45rem;
          align-items: center;
        }
        :global(.empty .serial) { color: var(--ink-mute); }
        :global(.empty-link) {
          color: var(--ink);
          font-size: var(--t-sm);
          border-bottom: 1px solid var(--ink);
          text-decoration: none;
        }
        :global(.empty-link:hover) { color: var(--seal); border-color: var(--seal); }

        /* === 任务行 === */
        :global(.task-list) {
          list-style: none; padding: 0; margin: 0;
          display: flex; flex-direction: column; gap: 0.45rem;
        }
        :global(.task-row) {
          display: flex; flex-direction: column; gap: 0.4rem;
          padding: 0.6rem 0.7rem;
          background: var(--paper-warm);
          border: 1px solid var(--line-soft);
          border-radius: var(--r-2);
          text-decoration: none;
          color: inherit;
          transition: border-color var(--t-base) var(--ease);
        }
        :global(.task-row:hover) { border-color: var(--ink-soft); }
        :global(.task-row-top) {
          display: flex; align-items: center; gap: 0.4rem;
          min-width: 0;
        }
        :global(.task-title) {
          font-size: var(--t-sm);
          color: var(--ink);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        :global(.task-row-bottom) {
          display: flex; align-items: center; gap: 0.5rem;
          font-size: 0.62rem;
        }
        :global(.task-bars) {
          display: inline-flex; gap: 2px;
          flex: 1;
        }
        :global(.task-bars .tb) {
          flex: 1; height: 3px; border-radius: 1px;
          background: var(--line-soft);
        }
        :global(.task-bars .tb-on) { background: var(--ink); }
        :global(.task-row .serial) { color: var(--ink-mute); }

        /* === 提示 === */
        .tip-label { color: var(--ink-mute); margin-bottom: 0.4rem; }
        .tip-list {
          list-style: none;
          padding: 0; margin: 0;
          display: flex; flex-direction: column; gap: 0.5rem;
          font-size: 0.78rem;
          color: var(--ink-soft);
          line-height: 1.55;
        }
        .tip-list li {
          padding-left: 0.6rem;
          border-left: 2px solid var(--line);
        }
        .kbd {
          font-family: var(--font-mono);
          font-size: 0.72rem;
          padding: 0.05rem 0.35rem;
          background: var(--paper-fade);
          border: 1px solid var(--line);
          border-radius: 2px;
          color: var(--ink);
        }

        /* ─── 管理控制台入口 ─── */
        :global(.admin-entry) {
          margin-top: auto;          /* 顶部 panel 块自然填充，自己沉底 */
          display: flex;
          align-items: center;
          gap: 0.7rem;
          padding: 0.7rem 0.85rem;
          background: var(--paper-warm);
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          color: var(--ink);
          text-decoration: none;
          transition: all var(--t-base) var(--ease);
        }
        :global(.admin-entry:hover) {
          border-color: var(--ink);
          background: var(--paper);
        }
        :global(.ae-icon) {
          width: 32px; height: 32px;
          display: inline-flex; align-items: center; justify-content: center;
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          color: var(--ink-soft);
          flex-shrink: 0;
          transition: color var(--t-base) var(--ease);
        }
        :global(.admin-entry:hover .ae-icon) { color: var(--seal); }
        :global(.ae-body) {
          flex: 1; min-width: 0;
          display: flex; flex-direction: column; gap: 1px;
        }
        :global(.ae-title) {
          font-size: var(--t-sm);
          color: var(--ink);
        }
        :global(.ae-sub) { color: var(--ink-mute); font-size: 0.62rem; }
        :global(.ae-arrow) {
          font-family: var(--font-mono);
          color: var(--ink-mute);
          font-size: 0.9rem;
          transition: transform var(--t-base) var(--ease);
        }
        :global(.admin-entry:hover .ae-arrow) {
          transform: translate(2px, -2px);
          color: var(--seal);
        }
      `}</style>
    </aside>
  );
}
