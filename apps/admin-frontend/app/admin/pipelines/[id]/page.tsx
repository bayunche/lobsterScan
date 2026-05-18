"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import PageHeader from "@/components/PageHeader";
import StatusDot from "@/components/StatusDot";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";
const WEB_BASE = process.env.NEXT_PUBLIC_WEB_API_BASE || "http://localhost:8000";

type ArtifactItem = { name: string; size: number; url: string };

type Step = {
  step: string;
  label: string;
  agent: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
};
type Run = {
  task_id: string;
  title?: string;
  report_type?: string;
  status: string;
  steps: Step[];
};

export default function PipelineDetail() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [presentationHref, setPresentationHref] = useState<string | null>(null);

  async function load() {
    const r = await fetch(`${BASE}/admin/api/pipelines/${id}`, { cache: "no-store" });
    setRun(await r.json());
  }

  // 嗅探 web-presentation/index.html 是否已经由 html_builder 产出
  async function probePresentation() {
    try {
      const r = await fetch(`${WEB_BASE}/api/tasks/${id}/exports`, { cache: "no-store" });
      if (!r.ok) return setPresentationHref(null);
      const d: { items?: ArtifactItem[] } = await r.json();
      const hit = (d.items || []).find((it) => it.name === "web-presentation/index.html");
      setPresentationHref(hit ? `${WEB_BASE}${hit.url}` : null);
    } catch {
      setPresentationHref(null);
    }
  }

  useEffect(() => {
    load();
    probePresentation();
    const t = setInterval(() => { load(); probePresentation(); }, 2000);
    return () => clearInterval(t);
  }, [id]);

  if (!run) {
    return (
      <div>
        <PageHeader title={String(id)} />
        <div className="rounded-2xl border border-dashed border-neutral-300 bg-white py-16 text-center text-sm text-neutral-500">
          加载中…
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title={run.title ?? run.task_id}
        desc={`${run.report_type ?? "—"} · ${run.status}`}
        right={
          presentationHref ? (
            <a
              href={presentationHref}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-sky-600 px-3 py-1.5 text-sm text-sky-700 hover:bg-sky-50"
            >
              ▶ 打开投屏汇报页
            </a>
          ) : (
            <span className="text-xs text-neutral-400">
              {run.status === "done" || run.status === "partial"
                ? "投屏页未生成"
                : "投屏页待 html_design 步骤完成"}
            </span>
          )
        }
      />

      <section className="overflow-hidden rounded-2xl border border-neutral-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
            <tr>
              <th className="px-4 py-2">步骤</th>
              <th className="px-4 py-2">Agent</th>
              <th className="px-4 py-2">状态</th>
              <th className="px-4 py-2">开始</th>
              <th className="px-4 py-2">结束</th>
              <th className="px-4 py-2">耗时</th>
            </tr>
          </thead>
          <tbody>
            {run.steps.map((s) => (
              <tr key={s.step} className="border-t border-neutral-100">
                <td className="px-4 py-2 font-medium">{s.label}</td>
                <td className="px-4 py-2 text-neutral-600">{s.agent}</td>
                <td className="px-4 py-2">
                  <StatusDot status={s.status} />{" "}
                  <span className="text-neutral-600">{s.status}</span>
                </td>
                <td className="px-4 py-2 font-mono text-xs text-neutral-500">
                  {s.started_at ? s.started_at.slice(11, 19) : "-"}
                </td>
                <td className="px-4 py-2 font-mono text-xs text-neutral-500">
                  {s.ended_at ? s.ended_at.slice(11, 19) : "-"}
                </td>
                <td className="px-4 py-2 text-neutral-600">
                  {s.duration_ms != null ? `${Math.round(s.duration_ms / 1000)}s` : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
