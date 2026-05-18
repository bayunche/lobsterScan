"use client";

import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";

type Item = { key: string; is_set: boolean; masked: string; updated_at: string | null };

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

// 给已知 key 标注用途 / 渠道。MiniMax 走 TokenPlan + PAYG 双通道。
const KEY_META: Record<string, { channel?: string; help?: string }> = {
  MINIMAX_API_KEY: { help: "通用 key — 作为渠道 key 缺失时的 fallback" },
  MINIMAX_API_KEY_TOKENPLAN: { channel: "TokenPlan", help: "走周配额账户;model 在 plan 列表时优先用这把" },
  MINIMAX_API_KEY_PAYG: { channel: "PAYG", help: "走预付费余额账户;model 不在 plan 列表时用这把" },
  MINIMAX_GROUP_ID: { help: "可选;skill 实测不需要" },
};

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
                <div className="flex items-center gap-2">
                  <div className="font-mono text-sm font-medium">{it.key}</div>
                  {KEY_META[it.key]?.channel && (
                    <span
                      className={
                        "rounded-full px-2 py-0.5 text-[10px] font-medium tracking-wide " +
                        (KEY_META[it.key].channel === "TokenPlan"
                          ? "bg-violet-50 text-violet-700"
                          : "bg-emerald-50 text-emerald-700")
                      }
                    >
                      {KEY_META[it.key].channel} 渠道
                    </span>
                  )}
                </div>
                <div className="mt-1 text-xs text-neutral-500">
                  {it.is_set ? `已设置 · ${it.masked}` : "未设置"}
                  {it.updated_at && <span className="ml-2 text-neutral-400">· {it.updated_at}</span>}
                </div>
                {KEY_META[it.key]?.help && (
                  <div className="mt-1 text-[11px] text-neutral-400">{KEY_META[it.key].help}</div>
                )}
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
