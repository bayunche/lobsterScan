"use client";

import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import Empty from "@/components/Empty";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

type Channel = { name: string; type: string; listen?: string };
type Binding = { agentId: string; match: Record<string, any> };
type Resp = { channels: Channel[]; bindings: Binding[]; agent_ids: string[] };

export default function BindingsPage() {
  const [data, setData] = useState<Resp>({ channels: [], bindings: [], agent_ids: [] });
  const [busy, setBusy] = useState(false);
  const [newCh, setNewCh] = useState<Channel>({ name: "", type: "ws-custom", listen: "" });
  const [newBd, setNewBd] = useState<{ agentId: string; matchChannel: string }>({
    agentId: "coordinator", matchChannel: "",
  });

  async function load() {
    const r = await fetch(`${BASE}/admin/api/bindings`, { cache: "no-store" });
    setData(await r.json());
  }
  useEffect(() => { load(); }, []);

  async function addChannel() {
    if (!newCh.name) return;
    setBusy(true);
    try {
      const r = await fetch(`${BASE}/admin/api/bindings/channels`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newCh),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        alert(j?.detail?.error?.biz_message || "添加失败");
      } else {
        setNewCh({ name: "", type: "ws-custom", listen: "" });
        await load();
      }
    } finally {
      setBusy(false);
    }
  }

  async function delChannel(name: string) {
    if (!confirm(`删除 channel ${name}? 相关 binding 也会一并删除`)) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/bindings/channels/${name}`, { method: "DELETE" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function addBinding() {
    if (!newBd.matchChannel) return alert("请选择 channel");
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/bindings/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agentId: newBd.agentId, match: { channel: newBd.matchChannel } }),
      });
      setNewBd({ ...newBd, matchChannel: "" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function delBinding(idx: number) {
    if (!confirm("删除该 binding?")) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/bindings/items/${idx}`, { method: "DELETE" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Channel 绑定" desc="Channel → Agent 路由（写入 openclaw/openclaw.json）" />

      <section className="mb-10">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-neutral-500">Channels</h2>
        <div className="overflow-hidden rounded-2xl border border-neutral-200 bg-white">
          {data.channels.length === 0 ? (
            <Empty title="未配置 Channel" />
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr><th className="px-4 py-2">名称</th><th className="px-4 py-2">类型</th><th className="px-4 py-2">监听</th><th className="px-4 py-2 w-20"></th></tr>
              </thead>
              <tbody>
                {data.channels.map((c) => (
                  <tr key={c.name} className="border-t border-neutral-100">
                    <td className="px-4 py-2 font-medium">{c.name}</td>
                    <td className="px-4 py-2 text-neutral-600">{c.type}</td>
                    <td className="px-4 py-2 font-mono text-xs">{c.listen || "-"}</td>
                    <td className="px-4 py-2">
                      <button
                        onClick={() => delChannel(c.name)}
                        disabled={busy}
                        className="text-xs text-rose-600 hover:underline disabled:opacity-50"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {/* 新增行 */}
          <div className="flex gap-2 border-t border-neutral-100 bg-neutral-50/50 px-4 py-3">
            <input
              placeholder="name"
              value={newCh.name}
              onChange={(e) => setNewCh({ ...newCh, name: e.target.value })}
              className="w-32 rounded border border-neutral-200 px-2 py-1 text-sm"
            />
            <input
              placeholder="type"
              value={newCh.type}
              onChange={(e) => setNewCh({ ...newCh, type: e.target.value })}
              className="w-32 rounded border border-neutral-200 px-2 py-1 text-sm"
            />
            <input
              placeholder="host:port"
              value={newCh.listen || ""}
              onChange={(e) => setNewCh({ ...newCh, listen: e.target.value })}
              className="flex-1 rounded border border-neutral-200 px-2 py-1 text-sm font-mono"
            />
            <button
              onClick={addChannel}
              disabled={busy || !newCh.name}
              className="rounded bg-sky-600 px-3 py-1 text-xs text-white disabled:opacity-50"
            >
              添加
            </button>
          </div>
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-neutral-500">Bindings</h2>
        <div className="overflow-hidden rounded-2xl border border-neutral-200 bg-white">
          {data.bindings.length === 0 ? (
            <Empty title="未配置 Binding" />
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr><th className="px-4 py-2">→ Agent</th><th className="px-4 py-2">匹配条件</th><th className="px-4 py-2 w-20"></th></tr>
              </thead>
              <tbody>
                {data.bindings.map((b, i) => (
                  <tr key={i} className="border-t border-neutral-100">
                    <td className="px-4 py-2 font-medium">{b.agentId}</td>
                    <td className="px-4 py-2 font-mono text-xs text-neutral-600">{JSON.stringify(b.match)}</td>
                    <td className="px-4 py-2">
                      <button
                        onClick={() => delBinding(i)}
                        disabled={busy}
                        className="text-xs text-rose-600 hover:underline disabled:opacity-50"
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {/* 新增 binding 行 */}
          <div className="flex gap-2 border-t border-neutral-100 bg-neutral-50/50 px-4 py-3">
            <select
              value={newBd.agentId}
              onChange={(e) => setNewBd({ ...newBd, agentId: e.target.value })}
              className="w-44 rounded border border-neutral-200 px-2 py-1 text-sm"
            >
              {data.agent_ids.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
            <select
              value={newBd.matchChannel}
              onChange={(e) => setNewBd({ ...newBd, matchChannel: e.target.value })}
              className="flex-1 rounded border border-neutral-200 px-2 py-1 text-sm"
            >
              <option value="">选 channel…</option>
              {data.channels.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
            </select>
            <button
              onClick={addBinding}
              disabled={busy || !newBd.matchChannel}
              className="rounded bg-sky-600 px-3 py-1 text-xs text-white disabled:opacity-50"
            >
              添加
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
