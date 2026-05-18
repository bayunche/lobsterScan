"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const FEATURES = [
  { href: "/feat/report", code: "RPT", label: "汇报材料生成", hint: "上传素材 → 集群拆解" },
  // future:
  // { href: "/feat/video",  code: "VID", label: "数字人讲解",  hint: "MP4 + 字幕" },
  // { href: "/feat/review", code: "RVW", label: "材料审校",     hint: "已有材料 → 改写" },
];

export default function SideRail() {
  const path = usePathname();
  return (
    <aside className="rail">
      <Link href="/" className="rail-brand">
        <span className="seal" data-agent="coordinator">龙</span>
        <div>
          <div className="rail-name">龙虾集群</div>
          <div className="serial rail-sub">CLUSTER / v0.1</div>
        </div>
      </Link>

      <nav>
        <div className="serial rail-section"><span className="regmark" /> 主入口</div>
        <ul className="rail-list">
          <li>
            <Link href="/" className={"rail-item " + (path === "/" ? "on" : "")}>
              <span className="rail-code">CHT</span>
              <div className="rail-text">
                <span className="rail-label">集群对话</span>
                <span className="serial rail-hint">跟集群说说看</span>
              </div>
            </Link>
          </li>
          <li>
            <Link href="/tasks" className={"rail-item " + (path?.startsWith("/tasks") ? "on" : "")}>
              <span className="rail-code">TSK</span>
              <div className="rail-text">
                <span className="rail-label">任务列表</span>
                <span className="serial rail-hint">历史会话 / 切换</span>
              </div>
            </Link>
          </li>
        </ul>

        <div className="serial rail-section mt-5"><span className="regmark" /> 集群功能</div>
        <ul className="rail-list">
          {FEATURES.map((f) => {
            const active = path?.startsWith(f.href);
            return (
              <li key={f.href}>
                <Link href={f.href} className={"rail-item " + (active ? "on" : "")}>
                  <span className="rail-code">{f.code}</span>
                  <div className="rail-text">
                    <span className="rail-label">{f.label}</span>
                    <span className="serial rail-hint">{f.hint}</span>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="rail-foot serial">
        <span className="regmark" />
        <span>会汇报 · 第 1 版</span>
      </div>

      <style jsx>{`
        .rail {
          width: 220px;
          flex-shrink: 0;
          border-right: 1px solid var(--line);
          background: var(--paper-warm);
          display: flex;
          flex-direction: column;
          padding: 0;
          min-height: 100vh;
        }
        .rail-brand {
          display: flex; align-items: center; gap: 0.7rem;
          padding: 1.25rem;
          border-bottom: 1px solid var(--line);
          color: var(--ink);
          text-decoration: none;
        }
        :global(.rail-brand .seal) { font-size: 1.5em; transform: rotate(-4deg); }
        .rail-name {
          font-family: var(--font-serif);
          font-size: var(--t-lg);
          font-weight: 600;
        }
        .rail-sub { color: var(--ink-mute); margin-top: 2px; }

        nav { flex: 1; padding: 1.25rem 0.5rem; }
        .rail-section {
          padding: 0 0.85rem;
          color: var(--ink-mute);
          margin-bottom: 0.4rem;
        }
        .rail-list { list-style: none; padding: 0; margin: 0; }
        :global(.rail-item) {
          display: flex; align-items: flex-start; gap: 0.65rem;
          padding: 0.55rem 0.85rem;
          border-radius: var(--r-2);
          color: var(--ink-soft);
          text-decoration: none;
          transition: all var(--t-fast) var(--ease);
        }
        :global(.rail-item:hover) {
          background: var(--paper);
          color: var(--ink);
        }
        :global(.rail-item.on) {
          background: var(--ink);
          color: var(--paper);
        }
        :global(.rail-item.on .rail-code),
        :global(.rail-item.on .rail-hint) { color: var(--paper); opacity: 0.5; }
        .rail-code {
          font-family: var(--font-mono);
          font-size: var(--t-mono-xs);
          letter-spacing: var(--tracking-wide);
          color: var(--ink-dim);
          width: 28px;
          padding-top: 2px;
          flex-shrink: 0;
        }
        :global(.rail-text) { flex: 1; display: flex; flex-direction: column; gap: 1px; }
        :global(.rail-label) {
          font-size: var(--t-sm);
          line-height: 1.3;
        }
        :global(.rail-hint) {
          font-size: 0.65rem;
          color: var(--ink-mute);
          line-height: 1.2;
        }

        .rail-foot {
          padding: 0.85rem 1.25rem;
          border-top: 1px solid var(--line);
          color: var(--ink-mute);
          display: flex; align-items: center; gap: 0.5rem;
        }
      `}</style>
    </aside>
  );
}
