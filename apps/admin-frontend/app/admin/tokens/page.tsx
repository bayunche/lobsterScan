"use client";

import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import Empty from "@/components/Empty";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

type Summary = {
  days: number;
  total: { prompt_tokens: number; completion_tokens: number; cost_usd: number };
  by_agent: { agent_id: string; tokens: number; cost_usd: number }[];
};

export default function TokensPage() {
  const [sum, setSum] = useState<Summary>({
    days: 30, total: { prompt_tokens: 0, completion_tokens: 0, cost_usd: 0 }, by_agent: [],
  });
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const r = await fetch(`${BASE}/admin/api/tokens/summary?days=30`, { cache: "no-store" });
      setSum(await r.json());
    } catch {}
  }
  useEffect(() => { load(); }, []);

  async function genMock() {
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/tokens/mock?count=24`, { method: "POST" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function clearAll() {
    if (!confirm("确认清空所有 token 使用记录？")) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/tokens/all`, { method: "DELETE" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  const maxTokens = Math.max(1, ...sum.by_agent.map((a) => a.tokens));

  return (
    <div>
      <PageHeader
        title="Token 计费"
        desc="近 30 天各 Agent 的 LLM token 用量与折算成本"
        right={
          <div className="flex gap-2">
            <button onClick={genMock} disabled={busy} className="rounded-lg border border-neutral-200 px-3 py-1.5 text-sm hover:border-sky-400 disabled:opacity-50">生成 Mock 数据</button>
            <button onClick={clearAll} disabled={busy} className="rounded-lg border border-neutral-200 px-3 py-1.5 text-sm text-rose-600 hover:border-rose-400 disabled:opacity-50">清空</button>
          </div>
        }
      />

      <section className="grid gap-4 md:grid-cols-3">
        <Card label="Prompt Tokens" value={sum.total.prompt_tokens.toLocaleString()} />
        <Card label="Completion Tokens" value={sum.total.completion_tokens.toLocaleString()} />
        <Card label="Cost" value={`$${sum.total.cost_usd.toFixed(4)}`} sub="USD" />
      </section>

      <section className="mt-8">
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-neutral-500">按 Agent 拆分</h2>
        {sum.by_agent.length === 0 ? (
          <Empty title="尚无用量数据" hint="点右上『生成 Mock 数据』先看 UI 效果" />
        ) : (
          <div className="space-y-2 rounded-2xl border border-neutral-200 bg-white p-5">
            {sum.by_agent.map((r) => (
              <div key={r.agent_id} className="grid grid-cols-[140px_1fr_120px_100px] items-center gap-3 text-sm">
                <div className="font-medium">{r.agent_id}</div>
                <div className="h-2 overflow-hidden rounded-full bg-neutral-100">
                  <div
                    className="h-full bg-sky-500"
                    style={{ width: `${(r.tokens / maxTokens) * 100}%` }}
                  />
                </div>
                <div className="text-right font-mono text-xs text-neutral-600">{r.tokens.toLocaleString()}</div>
                <div className="text-right font-mono text-xs text-neutral-500">${r.cost_usd.toFixed(4)}</div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl border border-neutral-200 bg-white p-5">
      <div className="text-xs uppercase tracking-widest text-neutral-400">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
      {sub && <div className="text-xs text-neutral-500">{sub}</div>}
    </div>
  );
}
