"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

const TABS = [
  { key: "soul",   label: "SOUL.md" },
  { key: "agents", label: "AGENTS.md" },
  { key: "user",   label: "USER.md" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

const DISPLAY: Record<string, string> = {
  coordinator: "汇报总控", material: "资料员", "point-extractor": "分析师",
  structure: "结构师", "upward-opt": "表达教练", copywriter: "文书",
  "html-designer": "设计师", "video-producer": "视频制作", reviewer: "质量检查员",
};

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

type Backup = { filename: string; which: string; saved_at: string; size_bytes: number };

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [tab, setTab] = useState<TabKey>("soul");
  const [content, setContent] = useState("");
  const [etag, setEtag] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [backups, setBackups] = useState<Backup[]>([]);
  const [toast, setToast] = useState<string | null>(null);

  async function loadFile() {
    const r = await fetch(`${BASE}/admin/api/agents/${id}/files/${tab}`);
    const j = await r.json();
    setContent(j.content || "");
    setEtag(j.etag || null);
    setSavedAt(j.updated_at || null);
  }

  async function loadBackups() {
    const r = await fetch(`${BASE}/admin/api/agents/${id}/backups`, { cache: "no-store" });
    setBackups(await r.json());
  }

  useEffect(() => { loadFile(); }, [id, tab]);
  useEffect(() => { loadBackups(); }, [id]);

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 2500);
  }

  async function save(alsoReload = false) {
    setBusy(true);
    try {
      const r = await fetch(`${BASE}/admin/api/agents/${id}/files/${tab}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, etag }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        flash(j?.error?.biz_message || "保存失败");
        return;
      }
      flash("✓ 已保存");
      await Promise.all([loadFile(), loadBackups()]);
      if (alsoReload) await reload();
    } finally {
      setBusy(false);
    }
  }

  async function reload() {
    const r = await fetch(`${BASE}/admin/api/agents/${id}/actions/reload`, { method: "POST" });
    const j = await r.json();
    flash(j.note || "已触发重载");
  }

  async function restore(filename: string) {
    if (!confirm(`确认用 ${filename} 覆盖当前文件？当前版本会先备份。`)) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/agents/${id}/backups/${filename}/restore`, { method: "POST" });
      flash("✓ 已恢复");
      await Promise.all([loadFile(), loadBackups()]);
    } finally {
      setBusy(false);
    }
  }

  const tabBackups = backups.filter((b) => b.which === tab);

  return (
    <div>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{DISPLAY[id as string] || id}</h1>
          <p className="mt-1 text-xs text-neutral-500">
            <span className="font-mono">{id}</span> · OpenClaw profile <span className="font-mono">lobster-{id}</span> ·
            最近保存：{savedAt ? new Date(savedAt).toLocaleString("zh-CN") : "—"}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => save(false)}
            disabled={busy}
            className="rounded-lg border border-neutral-200 px-4 py-2 text-sm disabled:opacity-50 hover:border-sky-400"
          >
            保存
          </button>
          <button
            onClick={() => save(true)}
            disabled={busy}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            保存并重载
          </button>
        </div>
      </div>

      <div className="flex gap-2 border-b border-neutral-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={
              "px-4 py-2 text-sm " +
              (tab === t.key ? "border-b-2 border-sky-600 font-medium" : "text-neutral-500 hover:text-neutral-700")
            }
          >
            {t.label}
            {backups.filter((b) => b.which === t.key).length > 0 && (
              <span className="ml-1 rounded bg-neutral-100 px-1 text-[10px] text-neutral-600">
                {backups.filter((b) => b.which === t.key).length}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_280px]">
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={28}
          className="w-full rounded-xl border border-neutral-200 bg-white px-4 py-3 font-mono text-sm leading-relaxed focus:border-sky-400 focus:outline-none"
        />

        <aside className="rounded-2xl border border-neutral-200 bg-white p-4">
          <div className="mb-2 text-xs font-medium uppercase tracking-widest text-neutral-500">
            备份 · {tab.toUpperCase()}.md
          </div>
          {tabBackups.length === 0 ? (
            <div className="text-xs text-neutral-400">暂无备份，保存后自动生成</div>
          ) : (
            <ul className="space-y-1.5">
              {tabBackups.map((b) => (
                <li key={b.filename}>
                  <button
                    onClick={() => restore(b.filename)}
                    disabled={busy}
                    className="w-full rounded-lg border border-neutral-100 px-3 py-2 text-left text-xs hover:border-sky-400 disabled:opacity-50"
                  >
                    <div className="font-mono text-neutral-700">
                      {new Date(b.saved_at).toLocaleString("zh-CN", { hour12: false })}
                    </div>
                    <div className="text-[10px] text-neutral-400">
                      {Math.round(b.size_bytes / 100) / 10} KB · 点击恢复
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>

      {toast && (
        <div className="fixed bottom-6 right-6 rounded-lg bg-neutral-900 px-4 py-2 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
