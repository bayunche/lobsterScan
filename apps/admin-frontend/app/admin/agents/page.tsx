"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import StatusDot from "@/components/StatusDot";
import Empty from "@/components/Empty";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

type Agent = {
  id: string;
  display_name: string;
  status: string;
  skill_count: number;
  skills: string[];
};

const SEAL_CHAR: Record<string, string> = {
  coordinator: "调", material: "料", "point-extractor": "析",
  structure: "纲", "upward-opt": "译", copywriter: "文",
  "html-designer": "设", "video-producer": "影", reviewer: "校",
};

const ROLE: Record<string, string> = {
  coordinator: "调度 / 总控",
  material: "素材整理",
  "point-extractor": "要点提炼",
  structure: "结构选型",
  "upward-opt": "向上视角",
  copywriter: "文案撰写",
  "html-designer": "页面设计",
  "video-producer": "数字人 / 配音",
  reviewer: "质量检查",
};

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetch(`${BASE}/admin/api/agents`, { cache: "no-store" })
      .then(r => r.json()).then(d => { setAgents(d); setLoaded(true); })
      .catch(() => setLoaded(true));
  }, []);

  if (!loaded) return <div className="serial ink-mute">LOADING ··</div>;

  if (agents.length === 0) {
    return (
      <div>
        <PageHeader title="九位常驻成员" serial="§ AGENT ROSTER · 9"
                    desc="跨 9 个独立 OpenClaw 实例的成员名单。" />
        <Empty title="集群尚未初始化" hint="bash scripts/bootstrap-openclaw.sh && bash scripts/setup-profiles.sh" />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="九位常驻成员"
        serial={`§ AGENT ROSTER · ${agents.length}`}
        desc="跨 9 个独立 OpenClaw 实例的成员名单。点击成员卡片进入查看其 SOUL / AGENTS / USER 与备份。"
      />

      <ul className="roster-grid">
        {agents.map((a, i) => (
          <li key={a.id}>
            <Link href={`/admin/agents/${a.id}`} className="roster-card">
              <div className="rc-no serial">AGT-{String(i + 1).padStart(2, "0")}</div>
              <div className="rc-row">
                <span className="seal" data-agent={a.id}>{SEAL_CHAR[a.id] || "·"}</span>
                <div className="rc-name-block">
                  <div className="rc-name">{a.display_name}</div>
                  <div className="serial rc-id">{a.id}</div>
                </div>
                <StatusDot status={a.status} />
              </div>
              <div className="rc-role">{ROLE[a.id]}</div>
              <hr className="hairline rc-rule" />
              <div className="rc-foot">
                <div>
                  <span className="serial">SKL</span>
                  <span className="rc-num">{a.skill_count}</span>
                </div>
                <div className="rc-skills">
                  {a.skills.length === 0 ? (
                    <span className="serial ink-dim">未挂载</span>
                  ) : (
                    a.skills.slice(0, 3).map((s) => (
                      <span key={s} className="tag">{s}</span>
                    ))
                  )}
                  {a.skills.length > 3 && (
                    <span className="serial ink-mute">+{a.skills.length - 3}</span>
                  )}
                </div>
              </div>
            </Link>
          </li>
        ))}
      </ul>

      <style jsx>{`
        .roster-grid {
          list-style: none;
          padding: 0; margin: 0;
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
          gap: 0.875rem;
        }
        :global(.roster-card) {
          display: block;
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          padding: 1rem 1.1rem;
          text-decoration: none;
          color: inherit;
          transition: border-color var(--t-base) var(--ease), transform var(--t-base) var(--ease);
          position: relative;
        }
        :global(.roster-card:hover) {
          border-color: var(--ink);
          transform: translateY(-1px);
        }
        :global(.roster-card .rc-no) {
          position: absolute;
          top: 0.65rem; right: 0.85rem;
          color: var(--ink-dim);
        }
        :global(.rc-row) {
          display: flex; align-items: center; gap: 0.75rem;
        }
        :global(.rc-name-block) { flex: 1; min-width: 0; }
        :global(.rc-name) {
          font-family: var(--font-serif);
          font-size: var(--t-lg);
          color: var(--ink);
        }
        :global(.rc-id) {
          color: var(--ink-mute);
          margin-top: 1px;
        }
        :global(.rc-role) {
          margin-top: 0.5rem;
          font-size: var(--t-sm);
          color: var(--ink-soft);
        }
        :global(.rc-rule) { margin: 0.75rem 0; }
        :global(.rc-foot) {
          display: flex; align-items: center; justify-content: space-between;
          font-size: var(--t-sm);
        }
        :global(.rc-num) {
          font-family: var(--font-mono);
          margin-left: 0.4rem;
          color: var(--ink);
          font-weight: 600;
        }
        :global(.rc-skills) {
          display: flex; flex-wrap: wrap; gap: 4px;
          justify-content: flex-end;
        }
      `}</style>
    </div>
  );
}
