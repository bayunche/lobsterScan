"use client";

import { useEffect, useState } from "react";
import PageHeader from "@/components/PageHeader";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

type Options = {
  llm_providers: string[];
  tts_providers: string[];
  video_providers: string[];
  model_presets: Record<string, string[]>;
  tts_model_presets: Record<string, string[]>;
};

type Config = {
  providers: Record<string, any>;
  tts: { provider?: string; model?: string };
  video: { provider?: string };
};

export default function ConfigPage() {
  const [opt, setOpt] = useState<Options | null>(null);
  const [cfg, setCfg] = useState<Config | null>(null);
  const [llmProvider, setLlmProvider] = useState("anthropic");
  const [llmModel, setLlmModel] = useState("claude-sonnet-4-6");
  const [ttsProvider, setTtsProvider] = useState("minimax");
  const [ttsModel, setTtsModel] = useState("speech-02-hd");
  const [videoProvider, setVideoProvider] = useState("heygen");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  async function load() {
    const [o, c] = await Promise.all([
      fetch(`${BASE}/admin/api/config/options`).then((r) => r.json()),
      fetch(`${BASE}/admin/api/config`).then((r) => r.json()),
    ]);
    setOpt(o);
    setCfg(c);
    const def = c.providers?.default || "anthropic";
    setLlmProvider(def);
    setLlmModel(c.providers?.[def]?.model || (o.model_presets[def]?.[0] ?? ""));
    setTtsProvider(c.tts?.provider || "minimax");
    setTtsModel(c.tts?.model || (o.tts_model_presets["minimax"]?.[0] ?? ""));
    setVideoProvider(c.video?.provider || "heygen");
  }
  useEffect(() => { load(); }, []);

  function flash(s: string) { setToast(s); setTimeout(() => setToast(null), 2500); }

  async function save() {
    setBusy(true);
    try {
      await fetch(`${BASE}/admin/api/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          llm_provider: llmProvider, llm_model: llmModel,
          tts_provider: ttsProvider, tts_model: ttsModel,
          video_provider: videoProvider,
        }),
      });
      flash("✓ 已保存");
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function broadcastLLM() {
    setBusy(true);
    try {
      const r = await fetch(`${BASE}/admin/api/broadcast/provider`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_agents: [], provider: llmProvider, model: llmModel }),
      });
      const j = await r.json();
      flash(`✓ LLM 已下发到 ${j.updated?.length || 0} 个 Agent`);
    } finally {
      setBusy(false);
    }
  }

  async function broadcastTTS() {
    setBusy(true);
    try {
      const r = await fetch(`${BASE}/admin/api/broadcast/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_agents: [], tts_provider: ttsProvider, tts_model: ttsModel }),
      });
      const j = await r.json();
      flash(`✓ TTS 已下发到 ${j.updated?.length || 0} 个 Agent`);
    } finally {
      setBusy(false);
    }
  }

  if (!opt) return <div className="text-sm text-neutral-400">加载配置中…</div>;

  return (
    <div>
      <PageHeader
        title="Provider / TTS / 视频"
        desc="LLM、TTS、视频通道总开关。保存后可一键下发到所有相关 Agent。"
        right={
          <button onClick={save} disabled={busy} className="rounded-lg bg-sky-600 px-4 py-2 text-sm text-white disabled:opacity-50">
            保存
          </button>
        }
      />

      <Section
        title="LLM Provider"
        desc="所有 Agent 默认使用的对话模型。MiniMax / Anthropic 都已就绪，密钥在 Secrets 页填入。"
        right={
          <button onClick={broadcastLLM} disabled={busy} className="rounded-lg border border-sky-600 px-3 py-1.5 text-sm text-sky-700 disabled:opacity-50">
            保存并下发到所有 Agent
          </button>
        }
      >
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Provider">
            <select
              value={llmProvider}
              onChange={(e) => {
                setLlmProvider(e.target.value);
                const first = opt.model_presets[e.target.value]?.[0];
                if (first) setLlmModel(first);
              }}
              className="select"
            >
              {opt.llm_providers.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </Field>
          <Field label="Model">
            <input
              list={`llm-models-${llmProvider}`}
              value={llmModel}
              onChange={(e) => setLlmModel(e.target.value)}
              className="input"
            />
            <datalist id={`llm-models-${llmProvider}`}>
              {(opt.model_presets[llmProvider] || []).map((m) => <option key={m} value={m} />)}
            </datalist>
          </Field>
        </div>
        {opt.model_presets[llmProvider] && (
          <Presets options={opt.model_presets[llmProvider]} value={llmModel} onPick={setLlmModel} />
        )}
      </Section>

      <Section
        title="TTS Provider"
        desc="数字人配音引擎。MiniMax 是默认；自托管模式可切 Qwen3-TTS。"
        right={
          <button onClick={broadcastTTS} disabled={busy} className="rounded-lg border border-sky-600 px-3 py-1.5 text-sm text-sky-700 disabled:opacity-50">
            下发到 video-producer / copywriter
          </button>
        }
      >
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Provider">
            <select
              value={ttsProvider}
              onChange={(e) => {
                setTtsProvider(e.target.value);
                const first = opt.tts_model_presets[e.target.value]?.[0];
                if (first) setTtsModel(first);
              }}
              className="select"
            >
              {opt.tts_providers.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </Field>
          <Field label="TTS Model / Voice">
            <input
              list={`tts-models-${ttsProvider}`}
              value={ttsModel}
              onChange={(e) => setTtsModel(e.target.value)}
              className="input"
            />
            <datalist id={`tts-models-${ttsProvider}`}>
              {(opt.tts_model_presets[ttsProvider] || []).map((m) => <option key={m} value={m} />)}
            </datalist>
          </Field>
        </div>
        {opt.tts_model_presets[ttsProvider] && (
          <Presets options={opt.tts_model_presets[ttsProvider]} value={ttsModel} onPick={setTtsModel} />
        )}
      </Section>

      <Section
        title="视频通道"
        desc="HeyGen 托管最快出片；self-hosted 走 SadTalker + 选定 TTS 自渲染。"
      >
        <Field label="Provider">
          <select value={videoProvider} onChange={(e) => setVideoProvider(e.target.value)} className="select max-w-sm">
            {opt.video_providers.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </Field>
      </Section>

      <section className="mt-10">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-widest text-neutral-500">openclaw.json · 当前快照</h2>
        <pre className="overflow-x-auto rounded-2xl border border-neutral-200 bg-white p-5 text-xs font-mono">
{JSON.stringify({ providers: cfg?.providers, tts: cfg?.tts, video: cfg?.video }, null, 2)}
        </pre>
      </section>

      {toast && (
        <div className="fixed bottom-6 right-6 rounded-lg bg-neutral-900 px-4 py-2 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}

      <style>{`.input,.select{width:100%;border:1px solid #e5e5e5;border-radius:0.5rem;padding:0.5rem 0.75rem;font-size:0.875rem;background:white}`}</style>
    </div>
  );
}

function Section({
  title, desc, right, children,
}: { title: string; desc?: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="mb-8 rounded-2xl border border-neutral-200 bg-white p-6">
      <header className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-medium">{title}</h2>
          {desc && <p className="mt-1 text-xs text-neutral-500">{desc}</p>}
        </div>
        {right}
      </header>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="mb-1 text-xs text-neutral-500">{label}</div>
      {children}
    </label>
  );
}

function Presets({ options, value, onPick }: { options: string[]; value: string; onPick: (s: string) => void }) {
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {options.map((m) => (
        <button
          key={m}
          onClick={() => onPick(m)}
          className={
            "rounded-full border px-2.5 py-0.5 text-[11px] " +
            (m === value
              ? "border-sky-500 bg-sky-50 text-sky-700"
              : "border-neutral-200 text-neutral-600 hover:border-neutral-300")
          }
        >
          {m}
        </button>
      ))}
    </div>
  );
}
