"use client";

import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";

type Item = { key: string; is_set: boolean; masked: string; updated_at: string | null };

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

export default function SecretsPage() {
  const [items, setItems] = useState<Item[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const r = await fetch(`${BASE}/admin/api/secrets`, { cache: "no-store" });
      setItems(await r.json());
    } catch {
      setItems([]);
    }
  }
  useEffect(() => { load(); }, []);

  async function save(key: string) {
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/secrets/${key}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: draft }),
      });
      setEditing(null);
      setDraft("");
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function clear(key: string) {
    if (!confirm(`确认清除 ${key}?`)) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/secrets/${key}`, { method: "DELETE" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Secrets"
        desc="API Key 集中管理。本地用 Fernet 对称加密落盘（ADMIN_SECRET_KEY 环境变量为种子）"
      />

      <div className="space-y-3">
        {items.map((it) => (
          <div key={it.key} className="rounded-2xl border border-neutral-200 bg-white p-5">
            <div className="flex items-center justify-between">
              <div>
                <div className="font-mono text-sm font-medium">{it.key}</div>
                <div className="mt-1 text-xs text-neutral-500">
                  {it.is_set ? `已设置 · ${it.masked}` : "未设置"}
                  {it.updated_at && <span className="ml-2 text-neutral-400">· {it.updated_at}</span>}
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => { setEditing(it.key); setDraft(""); }}
                  className="rounded-lg border border-neutral-200 px-3 py-1 text-sm hover:border-sky-400"
                >
                  {it.is_set ? "更新" : "填入"}
                </button>
                {it.is_set && (
                  <button
                    onClick={() => clear(it.key)}
                    disabled={busy}
                    className="rounded-lg border border-neutral-200 px-3 py-1 text-sm text-rose-600 hover:border-rose-400"
                  >
                    清除
                  </button>
                )}
              </div>
            </div>

            {editing === it.key && (
              <div className="mt-3 flex gap-2">
                <input
                  type="password"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="粘贴 API Key（不会展示明文）"
                  className="flex-1 rounded-lg border border-neutral-200 px-3 py-2 text-sm focus:border-sky-400 focus:outline-none"
                />
                <button
                  onClick={() => save(it.key)}
                  disabled={busy || !draft}
                  className="rounded-lg bg-sky-600 px-4 py-2 text-sm text-white disabled:opacity-50"
                >
                  保存
                </button>
                <button
                  onClick={() => { setEditing(null); setDraft(""); }}
                  className="rounded-lg border border-neutral-200 px-3 py-2 text-sm"
                >
                  取消
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
