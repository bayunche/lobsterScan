/** 侧栏 nav 用的极简 line icon — 16×16 / stroke="currentColor" / width=1.5 */

const C = {
  width: 18, height: 18, viewBox: "0 0 18 18",
  fill: "none", stroke: "currentColor", strokeWidth: 1.4,
  strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
};

export type IconKey =
  | "dashboard" | "health"
  | "agent" | "skill" | "binding" | "session"
  | "pipeline" | "tokens" | "storage"
  | "config" | "secret" | "avatar" | "template" | "audit";

export default function NavIcon({ name }: { name: IconKey }) {
  switch (name) {
    case "dashboard":
      return (
        <svg {...C} aria-hidden>
          <path d="M3 10a6 6 0 0112 0" />
          <path d="M9 10l3-3.5" />
          <circle cx="9" cy="10" r="1" fill="currentColor" stroke="none" />
        </svg>
      );
    case "health":
      return (
        <svg {...C} aria-hidden>
          <path d="M2 9h3l2-4 2 8 2-4h5" />
        </svg>
      );
    case "agent":
      return (
        <svg {...C} aria-hidden>
          <circle cx="9" cy="6.5" r="2.6" />
          <path d="M3 15c0-2.7 2.5-4.5 6-4.5s6 1.8 6 4.5" />
        </svg>
      );
    case "skill":
      return (
        <svg {...C} aria-hidden>
          <path d="M10 2L4 10h4l-1 6 6-8h-4l1-6z" />
        </svg>
      );
    case "binding":
      return (
        <svg {...C} aria-hidden>
          <path d="M7 6L5 8a3 3 0 004.2 4.2L11 10" />
          <path d="M11 12l2-2a3 3 0 00-4.2-4.2L7 8" />
        </svg>
      );
    case "session":
      return (
        <svg {...C} aria-hidden>
          <path d="M3 5a2 2 0 012-2h8a2 2 0 012 2v5a2 2 0 01-2 2H7l-4 3V5z" />
        </svg>
      );
    case "pipeline":
      return (
        <svg {...C} aria-hidden>
          <circle cx="4" cy="4" r="1.6" />
          <circle cx="14" cy="9" r="1.6" />
          <circle cx="4" cy="14" r="1.6" />
          <path d="M5.5 4.5h4a3 3 0 013 3v.5" />
          <path d="M5.5 13.5h4a3 3 0 003-3v-.5" />
        </svg>
      );
    case "tokens":
      return (
        <svg {...C} aria-hidden>
          <ellipse cx="9" cy="5" rx="6" ry="2" />
          <path d="M3 5v5c0 1.1 2.7 2 6 2s6-.9 6-2V5" />
          <path d="M3 10v3c0 1.1 2.7 2 6 2s6-.9 6-2v-3" />
        </svg>
      );
    case "storage":
      return (
        <svg {...C} aria-hidden>
          <rect x="3" y="3" width="12" height="3.5" rx="1" />
          <rect x="3" y="7.5" width="12" height="3.5" rx="1" />
          <rect x="3" y="12" width="12" height="3.5" rx="1" />
          <circle cx="6" cy="4.75" r="0.6" fill="currentColor" stroke="none" />
          <circle cx="6" cy="9.25" r="0.6" fill="currentColor" stroke="none" />
          <circle cx="6" cy="13.75" r="0.6" fill="currentColor" stroke="none" />
        </svg>
      );
    case "config":
      return (
        <svg {...C} aria-hidden>
          <path d="M3 5h7" /><circle cx="12" cy="5" r="1.6" />
          <path d="M3 9h2" /><circle cx="7" cy="9" r="1.6" /><path d="M9 9h6" />
          <path d="M3 13h10" /><circle cx="14" cy="13" r="1.6" />
        </svg>
      );
    case "secret":
      return (
        <svg {...C} aria-hidden>
          <circle cx="6" cy="9" r="3" />
          <path d="M9 9h7" />
          <path d="M13 9v2.5" />
          <path d="M15 9v2.5" />
        </svg>
      );
    case "avatar":
      return (
        <svg {...C} aria-hidden>
          <circle cx="9" cy="9" r="6.2" />
          <circle cx="9" cy="7.5" r="2" />
          <path d="M4.5 14.5c1.2-2 2.6-3 4.5-3s3.3 1 4.5 3" />
        </svg>
      );
    case "template":
      return (
        <svg {...C} aria-hidden>
          <rect x="3" y="3" width="5" height="5" rx="0.8" />
          <rect x="10" y="3" width="5" height="5" rx="0.8" />
          <rect x="3" y="10" width="5" height="5" rx="0.8" />
          <rect x="10" y="10" width="5" height="5" rx="0.8" />
        </svg>
      );
    case "audit":
      return (
        <svg {...C} aria-hidden>
          <path d="M4 3h7l3 3v9a1 1 0 01-1 1H4a1 1 0 01-1-1V4a1 1 0 011-1z" />
          <path d="M11 3v3h3" />
          <path d="M6 9h6" /><path d="M6 12h6" />
        </svg>
      );
  }
}
