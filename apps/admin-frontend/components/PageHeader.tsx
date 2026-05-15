"use client";

export default function PageHeader({
  title,
  desc,
  serial,
  right,
}: {
  title: string;
  desc?: string;
  serial?: string;
  right?: React.ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {serial && <div className="serial mb-2">{serial}</div>}
        <h1 className="display display-md">{title}</h1>
        {desc && <p className="ink-soft mt-2 text-sm" style={{ maxWidth: 720 }}>{desc}</p>}
      </div>
      {right}
      <style jsx>{`
        .page-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 2rem;
          margin-bottom: 1.75rem;
          padding-bottom: 1.25rem;
          border-bottom: 1px solid var(--line);
        }
      `}</style>
    </header>
  );
}
