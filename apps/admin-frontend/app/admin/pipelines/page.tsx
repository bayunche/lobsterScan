"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import StatusDot from "@/components/StatusDot";
import Empty from "@/components/Empty";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

type Step = { step: string; label: string; agent: string; status: string };
type Run = {
  task_id: string;
  title: string;
  report_type: string;
  status: string;
  created_at: string;
  steps: Step[];
};

export default function PipelinesPage() {
  const [items, setItems] = useState<Run[]>([]);
  const [busy, setBusy] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  async function load() {
    const r = await fetch(`${BASE}/admin/api/pipelines?limit=20`, { cache: "no-store" });
    const j = await r.json();
    setItems(j.items || []);
  }

  useEffect(() => {
    load();
    if (!autoRefresh) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [autoRefresh]);

  async function createMock() {
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/pipelines/mock`, { method: "POST" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function del(taskId: string) {
    if (!confirm("删除该 Pipeline 记录?")) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/pipelines/${taskId}`, { method: "DELETE" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="流水线"
        desc="接入业务后端后实时刷新；当前可用 Mock 任务演示。点击行查看每步耗时。"
        right={
          <div className="flex items-center gap-3">
            <label className="inline-flex items-center gap-2 text-xs text-neutral-500">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
              />
              自动刷新
            </label>
            <button
              onClick={createMock}
              disabled={busy}
              className="rounded-lg bg-sky-600 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              创建 Mock 任务
            </button>
          </div>
        }
      />

      {items.length === 0 ? (
        <Empty title="暂无任务" hint="点右上角『创建 Mock 任务』看 Gantt 推进" />
      ) : (
        <div className="space-y-3">
          {items.map((run) => {
            const done = run.steps.filter((s) => s.status === "success").length;
            return (
              <div
                key={run.task_id}
                className="rounded-2xl border border-neutral-200 bg-white p-5 transition hover:border-sky-400"
              >
                <div className="flex items-center justify-between">
                  <Link href={`/admin/pipelines/${run.task_id}`} className="flex items-center gap-2">
                    <StatusDot status={run.status === "done" ? "success" : run.status} />
                    <span className="font-medium">{run.title}</span>
                    <span className="text-xs text-neutral-400">{run.task_id}</span>
                  </Link>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-neutral-500">{run.report_type}</span>
                    <button
                      onClick={() => del(run.task_id)}
                      disabled={busy}
                      className="text-xs text-rose-500 hover:underline disabled:opacity-40"
                    >
                      删除
                    </button>
                  </div>
                </div>

                <div className="mt-4 grid grid-cols-8 gap-1">
                  {run.steps.map((s) => (
                    <div
                      key={s.step}
                      title={`${s.label} · ${s.status}`}
                      className={
                        "h-2 rounded-full transition-colors " +
                        (s.status === "success"
                          ? "bg-emerald-500"
                          : s.status === "running"
                          ? "bg-sky-500 animate-pulse"
                          : s.status === "failed"
                          ? "bg-rose-500"
                          : "bg-neutral-200")
                      }
                    />
                  ))}
                </div>
                <div className="mt-2 text-xs text-neutral-500">
                  {done} / {run.steps.length} 已完成 · {run.status}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
