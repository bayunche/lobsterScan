"use client";

export default function Empty({
  title,
  hint,
}: {
  title: string;
  hint?: string;
}) {
  return (
    <div className="empty-sheet">
      <div className="empty-title">{title}</div>
      {hint && <div className="empty-hint">{hint}</div>}
      <style jsx>{`
        .empty-sheet {
          border: 1px dashed var(--line);
          border-radius: var(--r-2);
          background: var(--paper);
          padding: 4rem 1rem;
          text-align: center;
        }
        .empty-title {
          font-family: var(--font-serif);
          font-size: var(--t-lg);
          color: var(--ink-soft);
        }
        .empty-hint {
          margin-top: 0.5rem;
          color: var(--ink-mute);
          font-size: var(--t-sm);
        }
      `}</style>
    </div>
  );
}
