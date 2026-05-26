"use client";

// /tasks · 任务列表
// 展示所有历史 task,点击 → 跳首页 `/?task=tsk_xxx`,首页把对话切到该 task 的 chat。

import { useEffect, useState } from "react";
import Link from "next/link";
import SideRail from "@/components/SideRail";

type TaskItem = {
  task_id: string;
  title: string;
  status: string;
  report_type?: string;
  audience?: string;
  duration?: string;
  created_at?: string;
  steps?: { step: string; status: string }[];
};

const STATUS_TAG: Record<string, { label: string; cls: string }> = {
  done:    { label: "已完成",   cls: "tag-ok" },
  partial: { label: "部分完成", cls: "tag-warn" },
  failed:  { label: "失败",     cls: "tag-fail" },
  running: { label: "推进中",   cls: "tag-run" },
};

function fmtTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const m = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${m(d.getMonth() + 1)}-${m(d.getDate())} ${m(d.getHours())}:${m(d.getMinutes())}`;
}

export default function TasksPage() {
  const [items, setItems] = useState<TaskItem[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const r = await fetch(`/api/tasks?limit=50`, { cache: "no-store" });
      const d = await r.json();
      setItems(d.items || []);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="app-grid">
      <SideRail />
      <main className="task-list-shell">
        <header className="head">
          <div>
            <div className="serial"><span className="regmark" /> 任务列表</div>
            <h1 className="display display-md">历史会话</h1>
            <p className="ink-soft mt-2 text-sm">
              点击进入对应任务的对话(切换首页会话)。也可以
              <Link href="/feat/report" className="underline ml-1">新建一份</Link>。
            </p>
          </div>
          <div className="head-meta serial">{items.length} 项</div>
        </header>

        {loading ? (
          <div className="empty serial">·· LOADING ··</div>
        ) : items.length === 0 ? (
          <div className="empty">
            <div className="serial">还没有任务</div>
            <Link href="/feat/report" className="btn-ink mt-3 inline-block">新建汇报</Link>
          </div>
        ) : (
          <ul className="list">
            {items.map((it) => {
              const tag = STATUS_TAG[it.status] || { label: it.status, cls: "tag-run" };
              const done = (it.steps || []).filter((s) => s.status === "success").length;
              const total = (it.steps || []).length;
              return (
                <li key={it.task_id}>
                  <Link
                    href={{ pathname: "/", query: { task: it.task_id } }}
                    className="card"
                    title={it.task_id}
                  >
                    <div className="card-row1">
                      <span className={"tag " + tag.cls}>{tag.label}</span>
                      <span className="title">{it.title || "(未命名)"}</span>
                    </div>
                    <div className="card-row2 serial">
                      <span>{it.report_type || "—"}</span>
                      <span>·</span>
                      <span>{it.audience || "—"}</span>
                      <span>·</span>
                      <span>{it.duration || "—"}</span>
                      <span>·</span>
                      <span>进度 {done}/{total}</span>
                      <span className="grow" />
                      <span>{fmtTime(it.created_at)}</span>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}

        <style jsx>{`
          .task-list-shell { max-width: 980px; padding: 2rem; }
          .head {
            display: flex; align-items: flex-start; justify-content: space-between;
            margin-bottom: 1.5rem; padding-bottom: 1rem;
            border-bottom: 1px solid var(--line);
          }
          .head-meta { color: var(--ink-mute); }
          .empty {
            padding: 5rem 0; text-align: center; color: var(--ink-mute);
          }
          .list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.6rem; }
          .card {
            display: block; padding: 0.85rem 1rem;
            border: 1px solid var(--line); border-radius: var(--r-2);
            background: var(--paper); color: var(--ink); text-decoration: none;
            transition: all var(--t-fast) var(--ease);
          }
          .card:hover { border-color: var(--ink); transform: translateY(-1px); }
          .card-row1 {
            display: flex; align-items: center; gap: 0.6rem;
            margin-bottom: 0.35rem;
          }
          .title { font-size: var(--t-base); font-weight: 500; flex: 1; }
          .id { color: var(--ink-mute); font-family: var(--font-mono); font-size: 0.75rem; }
          .card-row2 {
            display: flex; align-items: center; gap: 0.55rem;
            color: var(--ink-mute); font-size: 0.78rem;
          }
          .grow { flex: 1; }
          .tag {
            font-size: 0.7rem; padding: 0.15rem 0.55rem; border-radius: 999px;
            font-family: var(--font-mono); flex-shrink: 0;
          }
          .tag-ok   { background: #e8f1e3; color: #3d5b32; }
          .tag-warn { background: #fdf1d6; color: #876b1f; }
          .tag-fail { background: #f7dbd1; color: #8a3422; }
          .tag-run  { background: var(--ink); color: var(--paper); }
        `}</style>
      </main>
    </div>
  );
}
