"use client";

import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import Empty from "@/components/Empty";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

type Avatar = {
  id: string;
  source: string;
  name: string;
  preview_url: string | null;
  voice_id: string | null;
};

type FormState = {
  id: string | null;     // null 表示新建
  name: string;
  source: string;
  preview_url: string;
  voice_id: string;
};

const EMPTY: FormState = { id: null, name: "", source: "heygen", preview_url: "", voice_id: "" };

export default function AvatarsPage() {
  const [items, setItems] = useState<Avatar[]>([]);
  const [editing, setEditing] = useState<FormState | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const r = await fetch(`${BASE}/admin/api/avatars`, { cache: "no-store" });
      setItems(await r.json());
    } catch {
      setItems([]);
    }
  }
  useEffect(() => { load(); }, []);

  async function save() {
    if (!editing) return;
    setBusy(true);
    try {
      const payload = {
        name: editing.name,
        source: editing.source,
        preview_url: editing.preview_url || null,
        voice_id: editing.voice_id || null,
        meta: {},
      };
      const url = editing.id ? `${BASE}/admin/api/avatars/${editing.id}` : `${BASE}/admin/api/avatars`;
      const method = editing.id ? "PUT" : "POST";
      await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setEditing(null);
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function del(id: string) {
    if (!confirm("删除该形象?")) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/avatars/${id}`, { method: "DELETE" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="数字人形象"
        desc="HeyGen AVATAR-*.md / 自托管头像 元数据。后续接入时与 video-producer Agent 联动。"
        right={
          <button
            onClick={() => setEditing({ ...EMPTY })}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm text-white"
          >
            新增形象
          </button>
        }
      />

      {items.length === 0 ? (
        <Empty title="尚未注册数字人形象" />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((a) => (
            <div key={a.id} className="rounded-2xl border border-neutral-200 bg-white p-5">
              <div className="flex items-center justify-between">
                <div className="font-medium">{a.name}</div>
                <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] text-neutral-600">{a.source}</span>
              </div>
              <div className="mt-1 font-mono text-xs text-neutral-400">{a.id}</div>
              {a.voice_id && <div className="mt-2 text-xs text-neutral-500">voice: {a.voice_id}</div>}
              {a.preview_url && (
                <a href={a.preview_url} className="mt-2 block truncate text-xs text-sky-600" target="_blank" rel="noreferrer">
                  {a.preview_url}
                </a>
              )}
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => setEditing({
                    id: a.id, name: a.name, source: a.source,
                    preview_url: a.preview_url || "", voice_id: a.voice_id || "",
                  })}
                  className="rounded-lg border border-neutral-200 px-3 py-1 text-xs hover:border-sky-400"
                >
                  编辑
                </button>
                <button
                  onClick={() => del(a.id)}
                  className="rounded-lg border border-neutral-200 px-3 py-1 text-xs text-rose-600 hover:border-rose-400"
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl">
            <h3 className="mb-1 text-lg font-semibold">{editing.id ? "编辑形象" : "新增形象"}</h3>
            <p className="mb-4 text-xs text-neutral-500">基础元数据；接入 HeyGen API 后此处会自动同步 voice 列表</p>
            <div className="space-y-3">
              <Field label="名称">
                <input
                  value={editing.name}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  className="input"
                />
              </Field>
              <Field label="来源">
                <select
                  value={editing.source}
                  onChange={(e) => setEditing({ ...editing, source: e.target.value })}
                  className="input"
                >
                  <option value="heygen">heygen</option>
                  <option value="self-hosted">self-hosted (SadTalker)</option>
                </select>
              </Field>
              <Field label="Voice ID（可空）">
                <input
                  value={editing.voice_id}
                  onChange={(e) => setEditing({ ...editing, voice_id: e.target.value })}
                  className="input font-mono"
                />
              </Field>
              <Field label="Preview URL（可空）">
                <input
                  value={editing.preview_url}
                  onChange={(e) => setEditing({ ...editing, preview_url: e.target.value })}
                  className="input"
                />
              </Field>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setEditing(null)} className="rounded-lg border border-neutral-200 px-4 py-2 text-sm">取消</button>
              <button
                onClick={save}
                disabled={busy || !editing.name}
                className="rounded-lg bg-sky-600 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {editing.id ? "保存" : "创建"}
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`.input{width:100%;border:1px solid var(--line);border-radius:var(--r-2);padding:0.5rem 0.75rem;font-size:0.875rem;background:var(--glass-surface-1);-webkit-backdrop-filter:blur(var(--glass-blur-1));backdrop-filter:blur(var(--glass-blur-1));color:var(--ink)}.input:focus{outline:none;border-color:var(--color-primary-400);box-shadow:var(--shadow-focus)}`}</style>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="mb-1 text-xs text-neutral-500">{label}</div>
      {children}
    </label>
  );
}
