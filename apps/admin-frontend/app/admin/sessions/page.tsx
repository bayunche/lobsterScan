import PageHeader from "@/components/PageHeader";
import Empty from "@/components/Empty";
import { adminFetchSafe } from "@/lib/api";

type Item = { id: string; agent_id: string; updated_at: string; size_bytes: number };

export default async function SessionsPage() {
  const data = await adminFetchSafe<{ items: Item[] }>("/admin/api/sessions?limit=100", { items: [] });

  return (
    <div>
      <PageHeader title="会话" desc="读自 ~/.openclaw/agents/<id>/sessions/*.jsonl" />
      {data.items.length === 0 ? (
        <Empty title="暂无会话" hint="Gateway 启动并产生消息后会出现" />
      ) : (
        <div className="overflow-hidden rounded-2xl border border-neutral-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
              <tr>
                <th className="px-4 py-2">Agent</th>
                <th className="px-4 py-2">Session</th>
                <th className="px-4 py-2">更新时间</th>
                <th className="px-4 py-2">大小</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((it) => (
                <tr key={`${it.agent_id}-${it.id}`} className="border-t border-neutral-100">
                  <td className="px-4 py-2 font-medium">{it.agent_id}</td>
                  <td className="px-4 py-2 font-mono text-xs text-neutral-600">{it.id}</td>
                  <td className="px-4 py-2 text-neutral-500">{it.updated_at}</td>
                  <td className="px-4 py-2 text-neutral-600">{Math.round(it.size_bytes / 1024)} KB</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
