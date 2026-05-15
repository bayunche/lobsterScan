import PageHeader from "@/components/PageHeader";
import Empty from "@/components/Empty";
import { adminFetchSafe } from "@/lib/api";

type Overview = { outputs_bytes: number; uploads_bytes: number; task_count: number };
type Task = { task_id: string; size_bytes: number; has_video: boolean; has_html: boolean };

function bytes(b: number) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`;
  return `${(b / 1024 ** 3).toFixed(1)} GB`;
}

export default async function StoragePage() {
  const [ov, tasks] = await Promise.all([
    adminFetchSafe<Overview>("/admin/api/storage/overview", {
      outputs_bytes: 0, uploads_bytes: 0, task_count: 0,
    }),
    adminFetchSafe<Task[]>("/admin/api/storage/tasks", []),
  ]);

  return (
    <div>
      <PageHeader title="存储" desc="data/outputs · data/uploads · 按任务清理" />

      <section className="mb-8 grid gap-4 md:grid-cols-3">
        <Card label="任务数" value={`${ov.task_count}`} />
        <Card label="产物" value={bytes(ov.outputs_bytes)} />
        <Card label="上传" value={bytes(ov.uploads_bytes)} />
      </section>

      <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-neutral-500">任务产物</h2>
      {tasks.length === 0 ? (
        <Empty title="暂无任务产物" />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-neutral-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
              <tr><th className="px-4 py-2">Task</th><th className="px-4 py-2">大小</th><th className="px-4 py-2">产物</th></tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.task_id} className="border-t border-neutral-100">
                  <td className="px-4 py-2 font-mono text-xs">{t.task_id}</td>
                  <td className="px-4 py-2">{bytes(t.size_bytes)}</td>
                  <td className="px-4 py-2">
                    {t.has_html && <span className="mr-2 rounded bg-emerald-50 px-1.5 py-0.5 text-xs text-emerald-700">HTML</span>}
                    {t.has_video && <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-xs text-emerald-700">视频</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-neutral-200 bg-white p-5">
      <div className="text-xs uppercase tracking-widest text-neutral-400">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
    </div>
  );
}
