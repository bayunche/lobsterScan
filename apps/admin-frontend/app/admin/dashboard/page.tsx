"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import StatusDot from "@/components/StatusDot";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

type Agent = {
  id: string; display_name: string; workspace_ready: boolean;
  skill_count: number; agent_dir_exists: boolean;
};
type ReadinessItem = { key: string; label: string; ok: boolean; hint?: string };
type Run = {
  task_id: string; title: string; status: string;
  step_done: number; step_total: number;
  steps_status: string[];
};
type Overview = {
  gateway: { online: boolean; port: number };
  cli_installed: boolean;
  agents: Agent[];
  config: {
    llm_provider: string | null; llm_model: string | null;
    tts_provider: string | null; tts_model: string | null;
    video_provider: string | null;
  };
  secrets: { llm_ready: boolean; video_ready: boolean; set: string[]; llm_options_set: string[]; video_options_set: string[] };
  readiness: { items: ReadinessItem[]; done: number; total: number; score: number };
  tokens: { tokens: number; cost_usd: number; daily: { date: string; tokens: number }[] };
  pipelines_recent: Run[];
  audit_recent: { ts: string; action: string; target: string | null; detail: any }[];
  storage: { outputs_bytes: number; uploads_bytes: number; task_count: number };
};

const SEAL_CHAR: Record<string, string> = {
  coordinator: "调", material: "料", "point-extractor": "析",
  structure: "纲", "upward-opt": "译", copywriter: "文",
  "html-designer": "设", "video-producer": "影", reviewer: "校",
};

function bytes(b: number) {
  if (b < 1024) return `${b}B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)}KB`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)}MB`;
  return `${(b / 1024 ** 3).toFixed(1)}GB`;
}

export default function Dashboard() {
  const [d, setD] = useState<Overview | null>(null);

  async function load() {
    try {
      const r = await fetch(`${BASE}/admin/api/dashboard`, { cache: "no-store" });
      setD(await r.json());
    } catch {}
  }
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  if (!d) {
    return (
      <div>
        <PageHeader title="集群仪表盘" serial="§ DASHBOARD" />
        <span className="serial ink-mute">LOADING ··</span>
      </div>
    );
  }

  const sparkMax = Math.max(1, ...d.tokens.daily.map(x => x.tokens));

  return (
    <div>
      <PageHeader
        title="集群仪表盘"
        serial="§ CLUSTER DASHBOARD · 5s REFRESH"
        desc="一眼看清 Gateway、9 位成员状态、Token 消耗与最近任务。"
      />

      {/* 四个顶部数字 */}
      <section className="stats-grid">
        <Stat
          label="GATEWAY"
          big={<><StatusDot status={d.gateway.online ? "online" : "offline"} /> {d.gateway.online ? "在线" : "离线"}</>}
          sub={`ws://127.0.0.1:${d.gateway.port}`}
        />
        <Stat
          label="集群准备度"
          big={<><span className="font-serif">{d.readiness.score}</span>%</>}
          sub={`${d.readiness.done} / ${d.readiness.total} 项就绪`}
          bar={d.readiness.score}
        />
        <Stat
          label="近 7 天 TOKEN"
          big={<span className="font-mono">{d.tokens.tokens.toLocaleString()}</span>}
          sub={`$${d.tokens.cost_usd.toFixed(4)} USD`}
          spark={d.tokens.daily.map(x => ({ v: x.tokens, label: x.date }))}
          sparkMax={sparkMax}
        />
        <Stat
          label="产物存储"
          big={<><span className="font-serif">{d.storage.task_count}</span><span className="text-sm ink-mute"> 件</span></>}
          sub={`${bytes(d.storage.outputs_bytes)} · 输入 ${bytes(d.storage.uploads_bytes)}`}
        />
      </section>

      {/* 准备度 + 9 印章 + 配置 */}
      <section className="grid-3">
        <Sheet serial="§ 集群准备度" pad>
          <ul className="ready-list">
            {d.readiness.items.map((it, i) => (
              <li key={it.key} className={"ready-row " + (it.ok ? "ok" : "no")}>
                <span className="serial ready-no">{String(i + 1).padStart(2, "0")}</span>
                <span className="ready-mark">{it.ok ? "✓" : "·"}</span>
                <div className="flex-1">
                  <div className="ready-label">{it.label}</div>
                  {!it.ok && it.hint && (
                    <div className="font-mono serial ready-hint">{it.hint}</div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Sheet>

        <Sheet serial="§ 当前配置" pad>
          <Row label="LLM"  v={d.config.llm_provider} sub={d.config.llm_model || undefined} />
          <Row label="TTS"  v={d.config.tts_provider} sub={d.config.tts_model || undefined} />
          <Row label="视频" v={d.config.video_provider} />
          <Link href="/admin/config" className="ink-link mt-3">→ 切换 / 下发</Link>
        </Sheet>

        <Sheet serial="§ 九印章" pad>
          <div className="seal-grid">
            {d.agents.map((a) => (
              <Link key={a.id} href={`/admin/agents/${a.id}`} className="seal-cell" title={a.display_name}>
                <span className="seal" data-agent={a.id}>{SEAL_CHAR[a.id] || "·"}</span>
                <span className="seal-cell-name">{a.display_name}</span>
                <span className="serial seal-cell-skill">{a.skill_count} skill</span>
              </Link>
            ))}
          </div>
        </Sheet>
      </section>

      {/* 最近任务 + 最近写操作 */}
      <section className="grid-2">
        <Sheet serial="§ 最近任务" pad right={<Link href="/admin/pipelines" className="ink-link">全部 →</Link>}>
          {d.pipelines_recent.length === 0 ? (
            <div className="ink-mute text-sm py-6">暂无任务</div>
          ) : (
            <ul className="task-list">
              {d.pipelines_recent.map((r) => (
                <li key={r.task_id}>
                  <Link href={`/admin/pipelines/${r.task_id}`} className="task-item">
                    <div className="task-head">
                      <span className="flex items-center gap-2">
                        <StatusDot status={r.status === "done" ? "success" : r.status} />
                        <span className="task-title">{r.title}</span>
                      </span>
                      <span className="serial">{r.step_done}/{r.step_total}</span>
                    </div>
                    <div className="task-bars">
                      {r.steps_status.map((s, i) => (
                        <span key={i} className={"tb tb-" + s} />
                      ))}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Sheet>

        <Sheet serial="§ 最近写操作" pad right={<Link href="/admin/audit" className="ink-link">审计 →</Link>}>
          {d.audit_recent.length === 0 ? (
            <div className="ink-mute text-sm py-6">暂无写操作</div>
          ) : (
            <ul className="audit-list">
              {d.audit_recent.map((r, i) => (
                <li key={i}>
                  <span className="serial audit-ts">{r.ts.slice(11, 19)}</span>
                  <span className="audit-action">{r.action}</span>
                  <span className="audit-target ink-mute">{r.target || "—"}</span>
                </li>
              ))}
            </ul>
          )}
        </Sheet>
      </section>

      <style jsx>{`
        .stats-grid {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 1px;
          background: var(--line);
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          overflow: hidden;
          margin-bottom: 1.5rem;
        }
        @media (max-width: 900px) {
          .stats-grid { grid-template-columns: repeat(2, 1fr); }
        }
        .grid-3 {
          display: grid;
          grid-template-columns: 1.2fr 0.8fr 1fr;
          gap: 1rem;
          margin-bottom: 1.5rem;
        }
        @media (max-width: 1100px) {
          .grid-3 { grid-template-columns: 1fr; }
        }
        .grid-2 {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 1rem;
        }
        @media (max-width: 1100px) {
          .grid-2 { grid-template-columns: 1fr; }
        }

        /* 准备度 */
        :global(.ready-list) { list-style: none; padding: 0; margin: 0; }
        :global(.ready-row) {
          display: flex; align-items: flex-start; gap: 0.6rem;
          padding: 0.4rem 0;
          font-size: var(--t-sm);
        }
        :global(.ready-no) { color: var(--ink-dim); width: 24px; }
        :global(.ready-mark) {
          display: inline-flex; align-items: center; justify-content: center;
          width: 16px; height: 16px;
          font-family: var(--font-mono);
          font-size: 0.7rem;
          color: var(--ink-mute);
        }
        :global(.ready-row.ok .ready-mark) { color: var(--ok); }
        :global(.ready-row.ok .ready-label) { color: var(--ink); }
        :global(.ready-row.no .ready-label) { color: var(--ink-soft); }
        :global(.ready-hint) { margin-top: 2px; color: var(--ink-mute); }

        /* 配置行 */
        :global(.config-row) {
          display: flex; align-items: baseline; justify-content: space-between;
          padding: 0.35rem 0;
          font-size: var(--t-sm);
          border-bottom: 1px solid var(--line-soft);
        }
        :global(.config-row:last-child) { border-bottom: 0; }
        :global(.config-row .lbl) {
          font-family: var(--font-mono);
          font-size: var(--t-mono-xs);
          letter-spacing: var(--tracking-wide);
          color: var(--ink-mute);
        }
        :global(.config-row .v) {
          font-family: var(--font-serif);
          color: var(--ink);
        }
        :global(.config-row .sub) {
          font-family: var(--font-mono);
          font-size: var(--t-mono-xs);
          color: var(--ink-mute);
          margin-left: 0.5rem;
        }
        :global(.ink-link) {
          color: var(--ink);
          text-decoration: none;
          border-bottom: 1px solid var(--ink);
          font-size: var(--t-sm);
          padding-bottom: 1px;
          display: inline-block;
        }
        :global(.ink-link:hover) { border-color: var(--seal); color: var(--seal); }

        /* 九印章 grid */
        :global(.seal-grid) {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 0.5rem;
        }
        :global(.seal-cell) {
          display: flex; flex-direction: column;
          align-items: center; gap: 0.3rem;
          padding: 0.65rem 0.4rem;
          border: 1px solid var(--line-soft);
          border-radius: var(--r-2);
          background: var(--paper);
          text-decoration: none; color: inherit;
          transition: border-color var(--t-base) var(--ease);
        }
        :global(.seal-cell:hover) { border-color: var(--ink); }
        :global(.seal-cell-name) {
          font-size: var(--t-sm);
          font-family: var(--font-serif);
          color: var(--ink);
        }
        :global(.seal-cell-skill) {
          color: var(--ink-mute);
        }
        :global(.seal-cell .seal) { font-size: 1.2em; }

        /* 任务列表 */
        :global(.task-list) { list-style: none; padding: 0; margin: 0; }
        :global(.task-list li + li) { margin-top: 0.5rem; }
        :global(.task-item) {
          display: block;
          padding: 0.65rem 0.8rem;
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          text-decoration: none; color: inherit;
          transition: border-color var(--t-base) var(--ease);
        }
        :global(.task-item:hover) { border-color: var(--ink); }
        :global(.task-head) {
          display: flex; justify-content: space-between; align-items: center;
          font-size: var(--t-sm);
        }
        :global(.task-title) { font-weight: 500; color: var(--ink); }
        :global(.task-bars) {
          display: grid;
          grid-template-columns: repeat(8, 1fr);
          gap: 2px;
          margin-top: 0.5rem;
        }
        :global(.tb) { height: 3px; background: var(--line-soft); }
        :global(.tb-success) { background: var(--ink); }
        :global(.tb-running) { background: var(--seal); animation: bar-pulse 1.4s infinite var(--ease); }
        :global(.tb-failed)  { background: var(--fail); }
        :global(.tb-skipped) { background: var(--warn); }
        @keyframes bar-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }

        /* 审计 */
        :global(.audit-list) { list-style: none; padding: 0; margin: 0; font-size: var(--t-sm); }
        :global(.audit-list li) {
          display: grid;
          grid-template-columns: 90px 1fr auto;
          gap: 0.75rem;
          padding: 0.4rem 0;
          border-bottom: 1px solid var(--line-soft);
        }
        :global(.audit-list li:last-child) { border-bottom: 0; }
        :global(.audit-ts) { color: var(--ink-mute); }
        :global(.audit-action) {
          font-family: var(--font-mono);
          color: var(--ink);
          font-size: var(--t-mono-sm);
        }
        :global(.audit-target) {
          font-family: var(--font-mono);
          font-size: var(--t-mono-xs);
          text-align: right;
          color: var(--ink-mute);
        }
      `}</style>
    </div>
  );
}

function Stat({
  label, big, sub, bar, spark, sparkMax,
}: {
  label: string;
  big: React.ReactNode;
  sub?: string;
  bar?: number;
  spark?: { v: number; label: string }[];
  sparkMax?: number;
}) {
  return (
    <div className="stat">
      <div className="serial stat-label">{label}</div>
      <div className="stat-big">{big}</div>
      {sub && <div className="stat-sub">{sub}</div>}
      {bar !== undefined && (
        <div className="stat-bar">
          <div className="stat-bar-fill" style={{ width: `${bar}%` }} />
        </div>
      )}
      {spark && spark.length > 0 && sparkMax && (
        <div className="stat-spark">
          {spark.map((d, i) => (
            <span key={i} title={`${d.label}: ${d.v}`}
                  style={{ height: `${Math.max(6, (d.v / sparkMax) * 100)}%` }} />
          ))}
        </div>
      )}
      <style jsx>{`
        .stat {
          background: var(--paper);
          padding: 1.1rem 1.2rem;
          display: flex; flex-direction: column;
          gap: 0.4rem;
          min-height: 110px;
        }
        .stat-label { color: var(--ink-mute); }
        .stat-big {
          font-size: var(--t-2xl);
          color: var(--ink);
          line-height: 1.1;
          display: flex; align-items: baseline; gap: 0.5rem;
        }
        .stat-sub { font-family: var(--font-mono); font-size: var(--t-mono-xs); color: var(--ink-mute); }
        .stat-bar {
          height: 3px;
          background: var(--line-soft);
          margin-top: 0.4rem;
        }
        .stat-bar-fill {
          height: 100%;
          background: var(--ink);
          transition: width var(--t-slow) var(--ease);
        }
        .stat-spark {
          display: flex; align-items: flex-end; gap: 2px;
          height: 26px;
          margin-top: 0.4rem;
        }
        .stat-spark > span {
          flex: 1;
          background: var(--seal);
          min-height: 2px;
        }
        :global(.stat-big .font-serif) { font-family: var(--font-serif); font-weight: 600; }
        :global(.stat-big .font-mono) { font-family: var(--font-mono); }
      `}</style>
    </div>
  );
}

function Sheet({
  serial, pad, right, children,
}: { serial: string; pad?: boolean; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className={"sheet" + (pad ? " sheet-pad" : "")}>
      <div className="sheet-head">
        <span className="serial">{serial}</span>
        {right}
      </div>
      {children}
      <style jsx>{`
        .sheet-head {
          display: flex; justify-content: space-between; align-items: center;
          margin-bottom: 0.9rem;
        }
      `}</style>
    </section>
  );
}

function Row({ label, v, sub }: { label: string; v: string | null | undefined; sub?: string }) {
  return (
    <div className="config-row">
      <span className="lbl">{label}</span>
      <span>
        <span className="v">{v ?? "—"}</span>
        {sub && <span className="sub">{sub}</span>}
      </span>
    </div>
  );
}
