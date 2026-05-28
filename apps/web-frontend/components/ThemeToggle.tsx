"use client";

import { useEffect, useState } from "react";

// 亮/暗主题切换 — 悬浮在右下角。
// 主题在 layout 的 bootstrap 脚本里已于首帧前写入 <html data-theme>，
// 这里只负责读取当前值、切换、并持久化到 localStorage。
export default function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const cur = (document.documentElement.getAttribute("data-theme") as "light" | "dark") || "light";
    setTheme(cur);
    setMounted(true);
  }, []);

  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("mg-theme", next); } catch {}
  }

  const isDark = theme === "dark";

  return (
    <button
      className="theme-toggle"
      onClick={toggle}
      aria-label={isDark ? "切换到亮色" : "切换到暗色"}
      title={isDark ? "亮色模式" : "暗色模式"}
      style={{ opacity: mounted ? 1 : 0 }}
    >
      {isDark ? (
        // 月亮
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
      ) : (
        // 太阳
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="4.2" />
          <path d="M12 2v2.4M12 19.6V22M4.2 4.2l1.7 1.7M18.1 18.1l1.7 1.7M2 12h2.4M19.6 12H22M4.2 19.8l1.7-1.7M18.1 5.9l1.7-1.7" />
        </svg>
      )}
      <style jsx>{`
        .theme-toggle {
          position: fixed;
          bottom: 1.25rem;
          right: 1.25rem;
          z-index: 300;
          width: 44px;
          height: 44px;
          border-radius: 50%;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          background: var(--glass-surface-1);
          backdrop-filter: blur(var(--glass-blur-2));
          -webkit-backdrop-filter: blur(var(--glass-blur-2));
          border: 1px solid var(--glass-border);
          color: var(--ink-soft);
          cursor: pointer;
          box-shadow: var(--shadow-card);
          transition: transform var(--t-fast) var(--ease-out),
                      box-shadow var(--t-fast) var(--ease-out),
                      color var(--t-fast) var(--ease-out),
                      opacity var(--t-base) var(--ease-out);
        }
        .theme-toggle:hover {
          transform: scale(1.06);
          box-shadow: var(--shadow-elev);
          color: var(--seal);
        }
        .theme-toggle:active { transform: scale(0.96); }
        @media (max-width: 640px) {
          .theme-toggle { bottom: 0.85rem; right: 0.85rem; width: 40px; height: 40px; }
        }
      `}</style>
    </button>
  );
}
