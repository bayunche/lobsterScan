"use client";

import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";
import Empty from "@/components/Empty";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

const KINDS = [
  { key: "report-structures", label: "报告大纲", ext: ".yaml" },
  { key: "html-themes",       label: "HTML 主题", ext: ".json" },
  { key: "avatars",           label: "数字人 AVATAR.md", ext: ".md" },
] as const;

type KindKey = (typeof KINDS)[number]["key"];

export default function TemplatesPage() {
  const [kind, setKind] = useState<KindKey>("report-structures");
  const [items, setItems] = useState<string[]>([]);
  const [root, setRoot] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");

  async function loadList() {
    const r = await fetch(`${BASE}/admin/api/templates/${kind}`, { cache: "no-store" });
    const j = await r.json();
    setItems(j.items || []);
    setRoot(j.root || "");
  }

  async function loadContent(name: string) {
    const r = await fetch(`${BASE}/admin/api/templates/${kind}/${name}`);
    if (!r.ok) {
      setContent("");
      return;
    }
    const j = await r.json();
    setContent(j.content || "");
    setSelected(j.name);
  }

  useEffect(() => {
    setSelected(null);
    setContent("");
    loadList();
  }, [kind]);

  async function save() {
    if (!selected) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/templates/${kind}/${selected}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      await loadList();
    } finally {
      setBusy(false);
    }
  }

  async function del() {
    if (!selected || !confirm(`删除 ${selected}?`)) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/templates/${kind}/${selected}`, { method: "DELETE" });
      setSelected(null);
      setContent("");
      await loadList();
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    if (!newName) return;
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/templates/${kind}/${newName}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: "" }),
      });
      setNewName("");
      await loadList();
      const ext = KINDS.find((k) => k.key === kind)!.ext;
      const fn = newName.endsWith(ext) ? newName : `${newName}${ext}`;
      await loadContent(fn);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="模板与素材"
        desc="在线编辑大纲 / 主题 / Avatar 文件。直接写到 skills/custom/ 或 workspaces/ 下，OpenClaw 加载下次任务时生效。"
      />

      <div className="mb-5 flex gap-2 border-b border-neutral-200">
        {KINDS.map((k) => (
          <button
            key={k.key}
            onClick={() => setKind(k.key)}
            className={
              "px-4 py-2 text-sm " +
              (kind === k.key ? "border-b-2 border-sky-600 font-medium" : "text-neutral-500 hover:text-neutral-700")
            }
          >
            {k.label}
          </button>
        ))}
      </div>

      {root && <div className="mb-3 font-mono text-[11px] text-neutral-400">{root}</div>}

      <div className="grid gap-5 lg:grid-cols-[260px_1fr]">
        <aside className="rounded-2xl border border-neutral-200 bg-white p-4">
          {items.length === 0 ? (
            <Empty title="暂无文件" />
          ) : (
            <ul className="space-y-1">
              {items.map((it) => (
                <li key={it}>
                  <button
                    onClick={() => loadContent(it)}
                    className={
                      "block w-full truncate rounded px-2 py-1.5 text-left text-sm " +
                      (selected === it ? "bg-sky-50 text-sky-700" : "hover:bg-neutral-100")
                    }
                  >
                    {it}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-3 flex gap-1 border-t border-neutral-100 pt-3">
            <input
              placeholder="新文件名"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className="flex-1 rounded border border-neutral-200 px-2 py-1 text-xs"
            />
            <button
              onClick={create}
              disabled={!newName || busy}
              className="rounded bg-sky-600 px-2 py-1 text-xs text-white disabled:opacity-50"
            >
              新建
            </button>
          </div>
        </aside>

        <section>
          {selected ? (
            <>
              <div className="mb-3 flex items-center justify-between">
                <div className="font-mono text-sm">{selected}</div>
                <div className="flex gap-2">
                  <button onClick={save} disabled={busy} className="rounded-lg bg-sky-600 px-4 py-1.5 text-sm text-white disabled:opacity-50">保存</button>
                  <button onClick={del} disabled={busy} className="rounded-lg border border-neutral-200 px-3 py-1.5 text-sm text-rose-600 hover:border-rose-400 disabled:opacity-50">删除</button>
                </div>
              </div>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={28}
                className="w-full rounded-xl border border-neutral-200 bg-white px-4 py-3 font-mono text-sm focus:border-sky-400 focus:outline-none"
              />
            </>
          ) : (
            <Empty title="未选择文件" hint="左侧点一个文件查看内容" />
          )}
        </section>
      </div>
    </div>
  );
}
