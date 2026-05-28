"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import NavIcon, { type IconKey } from "@/components/NavIcon";

const NAV_GROUPS: { label: string; items: { href: string; label: string; icon: IconKey }[] }[] = [
  {
    label: "概览",
    items: [
      { href: "/admin/dashboard", label: "仪表盘",        icon: "dashboard" },
      { href: "/admin/health",    label: "集群健康",      icon: "health" },
    ],
  },
  {
    label: "集群",
    items: [
      { href: "/admin/agents",    label: "Agent",         icon: "agent" },
      { href: "/admin/skills",    label: "Skill 市场",    icon: "skill" },
      { href: "/admin/bindings",  label: "Channel 绑定",  icon: "binding" },
      { href: "/admin/sessions",  label: "会话",          icon: "session" },
    ],
  },
  {
    label: "运行",
    items: [
      { href: "/admin/pipelines", label: "流水线",        icon: "pipeline" },
      { href: "/admin/tokens",    label: "Token 计费",    icon: "tokens" },
      { href: "/admin/storage",   label: "存储",          icon: "storage" },
    ],
  },
  {
    label: "配置",
    items: [
      { href: "/admin/config",    label: "Provider/TTS",  icon: "config" },
      { href: "/admin/secrets",   label: "Secrets",       icon: "secret" },
      { href: "/admin/avatars",   label: "数字人形象",    icon: "avatar" },
      { href: "/admin/templates", label: "模板素材",      icon: "template" },
      { href: "/admin/audit",     label: "审计日志",      icon: "audit" },
    ],
  },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);

  // 路由切换时自动关抽屉
  useEffect(() => { setOpen(false); }, [path]);

  return (
    <div className={"admin-shell " + (open ? "admin-shell-open" : "")}>
      <button
        className="hamb"
        aria-label="菜单"
        onClick={() => setOpen(v => !v)}
      >
        <span /><span /><span />
      </button>
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <aside className="admin-side">
        <Link href="/admin/agents" className="brand">
          <span className="brand-mark seal" data-agent="coordinator">龙</span>
          <div>
            <div className="brand-name">OpenClaw</div>
            <div className="brand-sub serial">CONSOLE · v0.1</div>
          </div>
        </Link>

        <nav className="nav">
          {NAV_GROUPS.map((g) => (
            <div key={g.label} className="nav-grp">
              <div className="serial nav-grp-label">§ {g.label}</div>
              <ul className="nav-list">
                {g.items.map((it) => {
                  const active = path?.startsWith(it.href);
                  return (
                    <li key={it.href}>
                      <Link
                        href={it.href}
                        className={"nav-item " + (active ? "nav-item-on" : "")}
                      >
                        <span className="nav-icon">
                          <NavIcon name={it.icon} />
                        </span>
                        <span className="nav-label">{it.label}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="admin-foot serial">
          <span className="admin-foot-l">
            <span className="regmark" />
            <span>v0.1</span>
          </span>
          <span>INTERNAL</span>
        </div>
      </aside>

      <main className="admin-main">{children}</main>

      <style jsx>{`
        .admin-shell {
          display: flex;
          min-height: 100vh;
          min-width: 0;
        }
        .admin-side {
          flex: 0 0 248px;
          width: 248px;
          min-width: 248px;
          box-sizing: border-box;
          border-right: 1px solid var(--line);
          background: var(--glass-surface-2);
          backdrop-filter: blur(var(--glass-blur-2));
          -webkit-backdrop-filter: blur(var(--glass-blur-2));
          display: flex;
          flex-direction: column;
          overflow: hidden;
        }

        /* ─── 顶部品牌区 ─── */
        :global(.brand) {
          display: flex; align-items: center; gap: 0.7rem;
          padding: 1.1rem 1.25rem;
          color: var(--ink); text-decoration: none;
          white-space: nowrap;
          min-width: 0;
          position: relative;
          border-bottom: 1px solid var(--line-soft);
        }
        :global(.brand::after) {
          content: "";
          position: absolute;
          left: 1.25rem; right: 1.25rem;
          bottom: -1px;
          height: 1px;
          background: var(--seal);
          opacity: 0.18;
        }
        :global(.brand > div) { min-width: 0; flex: 1; }
        :global(.brand .seal) {
          font-size: 1.35em;
          transform: rotate(-4deg);
          width: 2em; height: 2em;
        }
        :global(.brand-name) {
          font-family: var(--font-serif);
          font-size: var(--t-lg);
          font-weight: 700;
          line-height: 1.15;
          letter-spacing: -0.005em;
          background: linear-gradient(135deg, var(--color-primary-500), var(--color-accent-500));
          -webkit-background-clip: text;
          background-clip: text;
          -webkit-text-fill-color: transparent;
          color: transparent;
        }
        :global(.brand-sub) {
          color: var(--ink-mute);
          margin-top: 3px;
          font-size: 0.6rem;
          letter-spacing: 0.18em;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        /* ─── 导航 ─── */
        .nav {
          flex: 1;
          overflow-y: auto;
          padding: 1rem 0.5rem 1rem;
        }
        .nav::-webkit-scrollbar { width: 4px; }
        .nav::-webkit-scrollbar-thumb { background: var(--line); border-radius: var(--r-pill); }

        .nav-grp + .nav-grp { margin-top: 0.85rem; }
        .nav-grp-label {
          display: flex; align-items: center; gap: 0.4rem;
          padding: 0 0.65rem;
          color: var(--ink-mute);
          margin-bottom: 0.3rem;
          font-size: 0.62rem;
        }
        .nav-grp-label::after {
          content: "";
          flex: 1;
          height: 1px;
          background: var(--line-soft);
        }

        .nav-list { list-style: none; margin: 0; padding: 0; }
        /* 注意：nav-item 是 next/link <a>，className 不会被 styled-jsx 加 jsx-hash
           所以这些规则必须用 :global() 才能匹配上 */
        :global(.nav-item) {
          display: flex; align-items: center; gap: 0.7rem;
          padding: 0.5rem 0.7rem 0.5rem 0.85rem;
          border-radius: var(--r-2);
          font-size: 0.88rem;
          color: var(--ink-soft);
          text-decoration: none;
          transition: background var(--t-fast) var(--ease),
                      color var(--t-fast) var(--ease);
          white-space: nowrap;
          position: relative;
        }
        :global(.nav-item:hover) {
          background: var(--glass-surface-1);
          color: var(--ink);
        }
        :global(.nav-item-on) {
          background: var(--glass-surface-1);
          color: var(--ink);
          font-weight: 600;
          box-shadow:
            inset 3px 0 0 0 var(--seal),
            0 1px 2px rgba(0, 0, 0, 0.04);
        }
        :global(.nav-item-on .nav-icon) { color: var(--seal); }

        :global(.nav-icon) {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 22px; height: 22px;
          color: var(--ink-mute);
          flex-shrink: 0;
          transition: color var(--t-fast) var(--ease);
        }
        :global(.nav-item:hover .nav-icon) { color: var(--ink); }
        :global(.nav-label) { flex: 1; }

        /* ─── 底部 ─── */
        .admin-foot {
          padding: 0.85rem 1.25rem;
          border-top: 1px solid var(--line-soft);
          color: var(--ink-mute);
          display: flex; align-items: center; justify-content: space-between;
          font-size: 0.62rem;
          letter-spacing: 0.14em;
        }
        .admin-foot-l { display: inline-flex; align-items: center; gap: 0.45rem; }

        .admin-main {
          flex: 1;
          padding: 2rem 2.5rem;
          min-width: 0;
        }

        /* ───── 汉堡 + 抽屉（仅小屏可见） ───── */
        .hamb {
          display: none;
          position: fixed; top: 0.85rem; left: 0.85rem;
          z-index: 60;
          width: 38px; height: 38px;
          background: var(--glass-surface-1);
          backdrop-filter: blur(var(--glass-blur-2));
          -webkit-backdrop-filter: blur(var(--glass-blur-2));
          border: 1px solid var(--line);
          border-radius: var(--r-2);
          padding: 0;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 4px;
          cursor: pointer;
          box-shadow: var(--shadow-card);
        }
        .hamb > span {
          display: block;
          width: 18px; height: 2px;
          background: var(--ink);
          border-radius: 2px;
        }
        .scrim {
          display: none;
          position: fixed; inset: 0;
          background: oklch(0.20 0.02 250 / 0.3);
          z-index: 40;
          backdrop-filter: blur(2px);
        }

        /* ───── 响应式 ───── */
        @media (max-width: 880px) {
          .hamb { display: flex; }
          .admin-side {
            position: fixed;
            top: 0; bottom: 0;
            left: -100%;             /* 默认完全移到屏外 */
            z-index: 50;
            flex: none;
            width: 280px;
            min-width: 280px;
            transform: none;
            transition: left 220ms var(--ease);
            box-shadow: 0 24px 48px -16px oklch(0.20 0.02 250 / 0.3);
            border-right: 1px solid var(--line);
          }
          .admin-shell-open .admin-side { left: 0; }
          .admin-shell-open .scrim { display: block; }
          .admin-main {
            padding: 4rem 1.25rem 1.5rem;
            width: 100%;
            min-width: 0;
          }
        }
        @media (max-width: 480px) {
          .admin-main { padding: 4rem 1rem 1.25rem; }
        }
      `}</style>
    </div>
  );
}
