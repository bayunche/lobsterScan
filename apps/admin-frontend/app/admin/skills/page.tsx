"use client";

import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import Empty from "@/components/Empty";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

type Skill = {
  name: string;
  category: string;
  source_available: boolean;
  installed_in: string[];
};

const AGENT_IDS = [
  "coordinator", "material", "point-extractor", "structure",
  "upward-opt", "copywriter", "html-designer", "video-producer", "reviewer",
];

const AGENT_DISPLAY: Record<string, string> = {
  coordinator: "汇报总控", material: "资料员", "point-extractor": "分析师",
  structure: "结构师", "upward-opt": "表达教练", copywriter: "文书",
  "html-designer": "设计师", "video-producer": "视频制作", reviewer: "质量检查员",
};

const SEAL: Record<string, string> = {
  coordinator: "调", material: "料", "point-extractor": "析",
  structure: "纲", "upward-opt": "译", copywriter: "文",
  "html-designer": "设", "video-producer": "影", reviewer: "校",
};

export default function SkillsPage() {
  const [items, setItems] = useState<Skill[]>([]);
  const [picking, setPicking] = useState<string | null>(null);
  const [pickedAgents, setPickedAgents] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  async function load() {
    const r = await fetch(`${BASE}/admin/api/skills/catalog`, { cache: "no-store" });
    setItems(await r.json());
  }
  useEffect(() => { load(); }, []);

  const byCat = items.reduce<Record<string, Skill[]>>((acc, s) => {
    (acc[s.category] ||= []).push(s);
    return acc;
  }, {});

  async function install() {
    if (!picking || pickedAgents.length === 0) return;
    setBusy(true);
    try {
      const r = await fetch(`${BASE}/admin/api/skills/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: picking, target_agents: pickedAgents }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        alert(j?.detail?.error?.biz_message || "挂载失败");
      }
      setPicking(null); setPickedAgents([]);
      await load();
    } finally { setBusy(false); }
  }

  async function uninstall(name: string, agent: string) {
    if (!confirm(`从 ${AGENT_DISPLAY[agent] || agent} 卸载 ${name}?`)) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/skills/${name}?target_agent=${agent}`, { method: "DELETE" });
      await load();
    } finally { setBusy(false); }
  }

  return (
    <div>
      <PageHeader
        title="Skill 市场"
        serial="§ SKILL MARKETPLACE"
        desc="挂载到任一 agent 的 .agents/skills/，让该位成员即刻具备这项能力。"
      />

      {items.length === 0 ? (
        <Empty title="尚无可用 Skill" hint="确认 admin-backend 在线" />
      ) : (
        Object.entries(byCat).map(([cat, list]) => (
          <section key={cat} className="cat-block">
            <div className="cat-head">
              <span className="serial">§ {cat}</span>
              <span className="hairline cat-rule" />
              <span className="serial cat-count">{list.length}</span>
            </div>
            <div className="skill-grid">
              {list.map((s) => (
                <article key={s.name} className="skill-card">
                  <header className="sk-head">
                    <h3 className="sk-name">{s.name}</h3>
                    <span className={"tag " + (s.source_available ? "tag-ok" : "")}>
                      {s.source_available ? "源已就绪" : "源缺失"}
                    </span>
                  </header>

                  <div className="sk-mount">
                    <div className="serial sk-mount-label">已挂载</div>
                    {s.installed_in.length === 0 ? (
                      <div className="serial ink-dim">— 暂无 —</div>
                    ) : (
                      <div className="sk-mount-list">
                        {s.installed_in.map((a) => (
                          <span key={a} className="mount-chip">
                            <span className="mount-chip-name">{AGENT_DISPLAY[a] || a}</span>
                            <button
                              onClick={() => uninstall(s.name, a)}
                              disabled={busy}
                              className="mount-chip-x"
                              aria-label={`卸载 ${a}`}
                            >×</button>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <button
                    onClick={() => { setPicking(s.name); setPickedAgents([]); }}
                    disabled={!s.source_available || busy}
                    className="btn-line sk-btn"
                  >
                    挂载到 ··
                  </button>
                </article>
              ))}
            </div>
          </section>
        ))
      )}

      {picking && (
        <div className="modal-bg" onClick={() => setPicking(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="mo-head">
              <span className="serial">§ 挂载 SKILL</span>
            </header>
            <h3 className="mo-title">{picking}</h3>
            <p className="mo-desc">选择要挂载到的成员，可多选。挂载后该成员立即可调用此 Skill。</p>

            <div className="mo-grid">
              {AGENT_IDS.map((a) => {
                const on = pickedAgents.includes(a);
                return (
                  <button
                    key={a}
                    onClick={() => setPickedAgents(on ? pickedAgents.filter(x => x !== a) : [...pickedAgents, a])}
                    className={"mo-cell " + (on ? "mo-cell-on" : "")}
                  >
                    <span className="seal seal-sm" data-agent={a}>{SEAL[a] || "·"}</span>
                    <span className="mo-cell-name">{AGENT_DISPLAY[a]}</span>
                  </button>
                );
              })}
            </div>

            <div className="mo-foot">
              <button onClick={() => setPicking(null)} className="btn-line">取消</button>
              <button onClick={install} disabled={busy || !pickedAgents.length} className="btn-ink">
                挂载到 {pickedAgents.length} 位
              </button>
            </div>
          </div>
        </div>
      )}

      <style jsx>{`
        .cat-block { margin-bottom: 2.5rem; }
        .cat-head {
          display: flex; align-items: center; gap: 0.6rem;
          margin-bottom: 0.85rem;
          color: var(--ink-mute);
        }
        :global(.cat-rule) { flex: 1; }
        .cat-count { font-family: var(--font-mono); color: var(--ink-mute); }

        .skill-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
          gap: 0.85rem;
        }
        .skill-card {
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          padding: 1rem 1.15rem;
          display: flex;
          flex-direction: column;
          gap: 0.8rem;
          transition: border-color var(--t-base) var(--ease);
        }
        .skill-card:hover { border-color: var(--ink-soft); }
        .sk-head { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
        .sk-name {
          font-family: var(--font-serif);
          font-size: var(--t-lg);
          color: var(--ink);
          margin: 0;
        }
        .sk-mount { flex: 1; font-size: var(--t-sm); }
        .sk-mount-label { color: var(--ink-mute); margin-bottom: 0.3rem; }
        .sk-mount-list { display: flex; flex-wrap: wrap; gap: 4px; }
        :global(.mount-chip) {
          display: inline-flex; align-items: center; gap: 4px;
          padding: 2px 4px 2px 8px;
          background: var(--paper-warm);
          border: 1px solid var(--line);
          border-radius: var(--r-pill);
          font-size: 0.78rem;
        }
        :global(.mount-chip-name) { color: var(--ink); }
        :global(.mount-chip-x) {
          background: transparent; border: 0;
          color: var(--ink-mute); cursor: pointer;
          width: 16px; height: 16px; border-radius: 50%;
          display: inline-flex; align-items: center; justify-content: center;
          font-size: 0.9rem;
          transition: all var(--t-fast) var(--ease);
        }
        :global(.mount-chip-x:hover) {
          background: var(--seal); color: var(--paper);
        }
        :global(.sk-btn) { width: 100%; }

        .modal-bg {
          position: fixed; inset: 0;
          background: oklch(0.20 0.02 250 / 0.32);
          display: flex; align-items: center; justify-content: center;
          z-index: 50;
          backdrop-filter: blur(2px);
        }
        .modal {
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: 16px;
          padding: 1.5rem 1.6rem;
          width: 100%; max-width: 520px;
          box-shadow: var(--shadow-elev);
        }
        .mo-head { color: var(--ink-mute); margin-bottom: 0.4rem; }
        .mo-title {
          font-family: var(--font-serif);
          font-size: var(--t-2xl);
          font-weight: 600;
          margin: 0;
        }
        .mo-desc { margin-top: 0.4rem; font-size: var(--t-sm); color: var(--ink-soft); }
        .mo-grid {
          margin-top: 1.2rem;
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 0.5rem;
        }
        :global(.mo-cell) {
          display: flex; align-items: center; gap: 0.45rem;
          padding: 0.55rem 0.7rem;
          background: var(--paper-warm);
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          cursor: pointer;
          transition: all var(--t-fast) var(--ease);
          font-family: var(--font-sans);
        }
        :global(.mo-cell:hover) { border-color: var(--ink-soft); }
        :global(.mo-cell-on) { border-color: var(--ink); background: var(--paper); }
        :global(.mo-cell .seal) { font-size: 0.55em; }
        :global(.mo-cell-name) { font-size: var(--t-sm); color: var(--ink); }
        .mo-foot { margin-top: 1.5rem; display: flex; justify-content: flex-end; gap: 0.6rem; }
      `}</style>
    </div>
  );
}
