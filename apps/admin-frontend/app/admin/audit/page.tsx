import PageHeader from "@/components/PageHeader";
import Empty from "@/components/Empty";
import { adminFetchSafe } from "@/lib/api";

type Row = { ts: string; actor: string; action: string; target: string | null; detail: any };

export default async function AuditPage() {
  const rows = await adminFetchSafe<Row[]>("/admin/api/broadcast/audit", []);

  return (
    <div>
      <PageHeader title="审计日志" desc="平台所有写操作（secret / skill / config / avatar 等）" />

      {rows.length === 0 ? (
        <Empty title="暂无写操作记录" />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-neutral-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
              <tr>
                <th className="px-4 py-2">时间</th>
                <th className="px-4 py-2">动作</th>
                <th className="px-4 py-2">目标</th>
                <th className="px-4 py-2">详情</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t border-neutral-100">
                  <td className="px-4 py-2 font-mono text-xs text-neutral-500">{r.ts}</td>
                  <td className="px-4 py-2 font-medium">{r.action}</td>
                  <td className="px-4 py-2 text-neutral-600">{r.target ?? "-"}</td>
                  <td className="px-4 py-2 font-mono text-xs text-neutral-500">
                    {Object.keys(r.detail || {}).length ? JSON.stringify(r.detail) : "-"}
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
