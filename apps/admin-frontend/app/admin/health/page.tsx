import PageHeader from "@/components/PageHeader";
import StatusDot from "@/components/StatusDot";
import { adminFetchSafe } from "@/lib/api";

type Health = {
  gateway: { host: string; port: number; online: boolean };
  cli: { installed: boolean; version: string | null };
  agents: { id: string; workspace_ready: boolean; skills: string[]; agent_dir_exists: boolean }[];
};

export default async function HealthPage() {
  const h = await adminFetchSafe<Health>("/admin/api/health", {
    gateway: { host: "127.0.0.1", port: 7800, online: false },
    cli: { installed: false, version: null },
    agents: [],
  });

  return (
    <div>
      <PageHeader title="集群健康" desc="5 秒一次刷新（后端缓存）" />

      <section className="grid gap-4 md:grid-cols-2 mb-8">
        <div className="rounded-2xl border border-neutral-200 bg-white p-6">
          <div className="text-xs uppercase tracking-widest text-neutral-400">Gateway</div>
          <div className="mt-3 flex items-center gap-2 text-xl font-semibold">
            <StatusDot status={h.gateway.online ? "online" : "offline"} />
            {h.gateway.online ? "在线" : "离线"}
          </div>
          <div className="mt-1 font-mono text-xs text-neutral-500">ws://{h.gateway.host}:{h.gateway.port}</div>
        </div>
        <div className="rounded-2xl border border-neutral-200 bg-white p-6">
          <div className="text-xs uppercase tracking-widest text-neutral-400">OpenClaw CLI</div>
          <div className="mt-3 flex items-center gap-2 text-xl font-semibold">
            <StatusDot status={h.cli.installed ? "online" : "offline"} />
            {h.cli.installed ? "已安装" : "未安装"}
          </div>
          <div className="mt-1 font-mono text-xs text-neutral-500">{h.cli.version ?? "请执行 npm i -g openclaw@latest"}</div>
        </div>
      </section>

      <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-neutral-500">Agents</h2>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {h.agents.map((a) => (
          <div key={a.id} className="rounded-2xl border border-neutral-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium">
                <StatusDot status={a.workspace_ready ? "ready" : "warn"} />
                {a.id}
              </div>
              <span className="text-xs text-neutral-400">{a.skills.length} skill</span>
            </div>
            <div className="mt-2 text-xs text-neutral-500">
              workspace {a.workspace_ready ? "就绪" : "待初始化"} ·{" "}
              agentDir {a.agent_dir_exists ? "已建" : "未建"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
