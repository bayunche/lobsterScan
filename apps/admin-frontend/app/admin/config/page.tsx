"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import PageHeader from "@/components/PageHeader";

const BASE = process.env.NEXT_PUBLIC_ADMIN_API_BASE || "http://localhost:8100";

type SecretStatus = { key: string; is_set: boolean };

type VideoProviderDetail = {
  id: string;
  display_name: string;
  implemented: boolean;
  required_env: string[];
  notes?: string;
  default_video_model?: string;
  available_video_models?: string[];
  tokenplan_video_models?: string[];   // 给 UI 标 plan 覆盖,决定走哪把 key
};

// 通用 provider 描述 — image / music 共用(VideoProviderDetail 是历史遗留命名)
type ProviderDetail = {
  id: string;
  display_name: string;
  implemented: boolean;
  required_env: string[];
  notes?: string;
  supports_base_url?: boolean;
  default_model?: string;
  available_models?: string[];
};

type Options = {
  llm_providers: string[];
  tts_providers: string[];
  video_providers: string[];
  video_provider_details?: VideoProviderDetail[];
  image_provider_details?: ProviderDetail[];
  music_provider_details?: ProviderDetail[];
  model_presets: Record<string, string[]>;
  tts_model_presets: Record<string, string[]>;
};

type Config = {
  providers: Record<string, any>;
  tts: { provider?: string; model?: string };
  video: { provider?: string; model?: string };
  image: { provider?: string; model?: string; base_url?: string };
  music: { provider?: string; model?: string };
};

export default function ConfigPage() {
  const [opt, setOpt] = useState<Options | null>(null);
  const [cfg, setCfg] = useState<Config | null>(null);
  const [secretSet, setSecretSet] = useState<Record<string, boolean>>({});
  const [llmProvider, setLlmProvider] = useState("anthropic");
  const [llmModel, setLlmModel] = useState("claude-sonnet-4-6");
  const [ttsProvider, setTtsProvider] = useState("minimax");
  const [ttsModel, setTtsModel] = useState("speech-02-hd");
  const [videoProvider, setVideoProvider] = useState("minimax");
  const [videoModel, setVideoModel] = useState("MiniMax-Hailuo-2.3");
  const [llmBaseUrl, setLlmBaseUrl] = useState("");
  // 各 LLM provider 独立的 base_url(用户在 LLM section 表格里编辑,跟选哪个 default 解耦)
  const [providerBaseUrls, setProviderBaseUrls] = useState<Record<string, string>>({});
  const [imageProvider, setImageProvider] = useState("openai-gpt-image-2");
  const [imageModel, setImageModel] = useState("gpt-image-2");
  const [imageBaseUrl, setImageBaseUrl] = useState("");
  const [musicProvider, setMusicProvider] = useState("minimax-music");
  const [musicModel, setMusicModel] = useState("music-2.6");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  async function load() {
    const [o, c, secrets] = await Promise.all([
      fetch(`${BASE}/admin/api/config/options`).then((r) => r.json()),
      fetch(`${BASE}/admin/api/config`).then((r) => r.json()),
      fetch(`${BASE}/admin/api/secrets`).then((r) => r.ok ? r.json() as Promise<SecretStatus[]> : []),
    ]);
    setOpt(o);
    setCfg(c);
    setSecretSet(Object.fromEntries((secrets as SecretStatus[]).map((s) => [s.key, s.is_set])));
    const def = c.providers?.default || "anthropic";
    setLlmProvider(def);
    setLlmModel(c.providers?.[def]?.model || (o.model_presets[def]?.[0] ?? ""));
    setLlmBaseUrl(c.providers?.[def]?.base_url || "");
    // 从 cfg.providers 拉每个 provider 当前 base_url(可能没设)
    const pbu: Record<string, string> = {};
    for (const id of o.llm_providers) {
      pbu[id] = c.providers?.[id]?.base_url || "";
    }
    setProviderBaseUrls(pbu);
    setTtsProvider(c.tts?.provider || "minimax");
    setTtsModel(c.tts?.model || (o.tts_model_presets["minimax"]?.[0] ?? ""));
    const vp = c.video?.provider || "minimax";
    setVideoProvider(vp);
    const vDet = ((o.video_provider_details || []) as VideoProviderDetail[]).find((d) => d.id === vp);
    setVideoModel(c.video?.model || vDet?.default_video_model || "");
    // image
    const ip = c.image?.provider || "openai-gpt-image-2";
    setImageProvider(ip);
    const iDet = ((o.image_provider_details || []) as ProviderDetail[]).find((d) => d.id === ip);
    setImageModel(c.image?.model || iDet?.default_model || "");
    setImageBaseUrl(c.image?.base_url || "");
    // music
    const mp = c.music?.provider || "minimax-music";
    setMusicProvider(mp);
    const mDet = ((o.music_provider_details || []) as ProviderDetail[]).find((d) => d.id === mp);
    setMusicModel(c.music?.model || mDet?.default_model || "");
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
          llm_provider: llmProvider, llm_model: llmModel, llm_base_url: llmBaseUrl || null,
          providers_base_urls: providerBaseUrls,
          tts_provider: ttsProvider, tts_model: ttsModel,
          video_provider: videoProvider, video_model: videoModel,
          image_provider: imageProvider, image_model: imageModel, image_base_url: imageBaseUrl || null,
          music_provider: musicProvider, music_model: musicModel,
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
        <div className="mt-5 rounded-xl border border-neutral-200 bg-neutral-50/60 p-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div>
              <div className="text-xs font-medium text-neutral-700">各 LLM Provider Base URL</div>
              <div className="mt-0.5 text-[11px] text-neutral-500">
                每个 provider 独立 endpoint,不必切到该 provider 才能改。留空 = 走官方端点。
              </div>
            </div>
          </div>
          <div className="space-y-2">
            {opt.llm_providers.map((pid) => (
              <div key={pid} className="grid items-center gap-2" style={{gridTemplateColumns: "120px 1fr"}}>
                <div className="font-mono text-xs text-neutral-700">{pid}</div>
                <input
                  value={providerBaseUrls[pid] ?? ""}
                  onChange={(e) =>
                    setProviderBaseUrls((cur) => ({ ...cur, [pid]: e.target.value }))
                  }
                  placeholder={
                    pid === "anthropic" ? "https://api.anthropic.com/v1(默认)"
                    : pid === "openai"    ? "https://api.openai.com/v1(默认)"
                    : pid === "minimax"   ? "https://api.minimaxi.com/v1(默认)"
                    : "留空走官方端点"
                  }
                  className="w-full rounded-lg border border-neutral-200 bg-white px-3 py-1.5 font-mono text-xs"
                />
              </div>
            ))}
          </div>
          <div className="mt-2 text-[11px] text-neutral-400">
            注:LLM 推理实际走 OpenClaw subprocess,这里写到 <code className="font-mono">openclaw.json providers.&lt;id&gt;.base_url</code>,
            供 OpenClaw plugin / 业务后端读取使用。要让 OpenClaw 真切端点还需在 ~/.openclaw auth-profiles 对齐。
          </div>
        </div>
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
        desc="数字人源 4 选 1。占位项还没接好就选,后端 pipeline 会自动走降级 JSON,不阻塞演示。"
      >
        <VideoProviderPicker
          details={opt.video_provider_details}
          fallbackIds={opt.video_providers}
          value={videoProvider}
          onChange={(id) => {
            setVideoProvider(id);
            const det = (opt.video_provider_details || []).find((d) => d.id === id);
            setVideoModel(det?.default_video_model || "");
          }}
          secretSet={secretSet}
          videoModel={videoModel}
          onVideoModelChange={setVideoModel}
        />
      </Section>

      <Section
        title="图像通道"
        desc="html-designer 用它出 slide 封面图 / 配图。OpenAI 类 provider 支持切 base_url(走代理 / Azure / 自建)。"
      >
        <ProviderModelPicker
          details={opt.image_provider_details || []}
          providerValue={imageProvider}
          onProviderChange={(id) => {
            setImageProvider(id);
            const det = (opt.image_provider_details || []).find((d) => d.id === id);
            setImageModel(det?.default_model || "");
            // 切走 OpenAI 类后清掉 base_url(避免误把别人的 url 用到 minimax 上)
            if (!(det?.supports_base_url)) setImageBaseUrl("");
          }}
          modelValue={imageModel}
          onModelChange={setImageModel}
          baseUrlValue={imageBaseUrl}
          onBaseUrlChange={setImageBaseUrl}
          secretSet={secretSet}
        />
      </Section>

      <Section
        title="音乐通道"
        desc="video-producer 用它给数字人视频配 BGM / 主题曲。music-2.6-free 需要 Max plan,默认 music-2.6。"
      >
        <ProviderModelPicker
          details={opt.music_provider_details || []}
          providerValue={musicProvider}
          onProviderChange={(id) => {
            setMusicProvider(id);
            const det = (opt.music_provider_details || []).find((d) => d.id === id);
            setMusicModel(det?.default_model || "");
          }}
          modelValue={musicModel}
          onModelChange={setMusicModel}
          baseUrlValue={null}                         // music 没切 url 需求
          onBaseUrlChange={() => {}}
          secretSet={secretSet}
        />
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

      <style>{`.input,.select{width:100%;border:1px solid var(--line);border-radius:var(--r-2);padding:0.5rem 0.75rem;font-size:0.875rem;background:var(--glass-surface-1);-webkit-backdrop-filter:blur(var(--glass-blur-1));backdrop-filter:blur(var(--glass-blur-1));color:var(--ink)}.input:focus,.select:focus{outline:none;border-color:var(--color-primary-400);box-shadow:var(--shadow-focus)}`}</style>
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

function VideoProviderPicker({
  details, fallbackIds, value, onChange, secretSet,
  videoModel, onVideoModelChange,
}: {
  details?: VideoProviderDetail[];
  fallbackIds: string[];
  value: string;
  onChange: (id: string) => void;
  secretSet: Record<string, boolean>;
  videoModel: string;
  onVideoModelChange: (m: string) => void;
}) {
  const list: VideoProviderDetail[] = details && details.length > 0
    ? details
    : fallbackIds.map((id) => ({ id, display_name: id, implemented: true, required_env: [] }));
  const selected = list.find((p) => p.id === value);
  const showWarning = selected && !selected.implemented;
  const missingEnvForSelected = selected
    ? selected.required_env.filter((e) => !secretSet[e])
    : [];
  return (
    <div>
      <div className="grid gap-3 md:grid-cols-2">
        {list.map((p) => {
          const active = p.id === value;
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => onChange(p.id)}
              className={
                "group flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all " +
                (active
                  ? "border-sky-500 bg-sky-50/60 shadow-sm"
                  : "border-neutral-200 bg-white hover:border-neutral-300")
              }
            >
              <div className="flex w-full items-center justify-between gap-2">
                <div className="text-sm font-medium text-neutral-900">{p.display_name}</div>
                <span
                  className={
                    "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium tracking-wide " +
                    (p.implemented
                      ? "bg-emerald-50 text-emerald-700"
                      : "bg-amber-50 text-amber-700")
                  }
                >
                  {p.implemented ? "已接入" : "占位"}
                </span>
              </div>
              <div className="font-mono text-[11px] text-neutral-400">{p.id}</div>
              {p.notes && (
                <div className="text-xs leading-relaxed text-neutral-500">{p.notes}</div>
              )}
              {p.required_env.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {p.required_env.map((e) => {
                    const ok = !!secretSet[e];
                    return (
                      <span
                        key={e}
                        title={ok ? "Secret 已配置" : "未在 Secrets 页填入"}
                        className={
                          "rounded px-1.5 py-0.5 font-mono text-[10px] " +
                          (ok ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700")
                        }
                      >
                        {ok ? "✓" : "✗"} {e}
                      </span>
                    );
                  })}
                </div>
              )}
            </button>
          );
        })}
      </div>
      {showWarning && (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          ⚠ <strong>{selected!.display_name}</strong> 还未真接入,pipeline 跑到 video_production 时会写一份降级 JSON 直接通过,不会阻塞其他步骤。
        </div>
      )}
      {!showWarning && missingEnvForSelected.length > 0 && (
        <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
          🔑 <strong>{selected!.display_name}</strong> 已接入但还缺 secret:
          {" "}
          {missingEnvForSelected.map((e, i) => (
            <span key={e}>
              <code className="rounded bg-white px-1 py-0.5 font-mono">{e}</code>
              {i < missingEnvForSelected.length - 1 ? ", " : ""}
            </span>
          ))}
          。请到 <Link href="/admin/secrets" className="underline">Secrets 页</Link> 填入,否则 video_production 步骤会失败。
        </div>
      )}

      {selected?.available_video_models && selected.available_video_models.length > 0 && (
        <div className="mt-4 rounded-xl border border-neutral-200 bg-neutral-50/60 p-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-neutral-700">
            <span>视频模型(TokenPlan 周配额按 model 分别计费)</span>
            {/* 标当前选中的 model 走哪条 key */}
            {videoModel && (
              <span
                className={
                  "rounded-full px-2 py-0.5 text-[10px] font-medium " +
                  ((selected.tokenplan_video_models || []).includes(videoModel)
                    ? "bg-violet-50 text-violet-700"
                    : "bg-emerald-50 text-emerald-700")
                }
              >
                走 {(selected.tokenplan_video_models || []).includes(videoModel) ? "TokenPlan" : "PAYG"} 渠道 key
              </span>
            )}
          </div>
          <input
            list="video-model-options"
            value={videoModel}
            onChange={(e) => onVideoModelChange(e.target.value)}
            placeholder={selected.default_video_model}
            className="mb-2 w-full rounded-lg border border-neutral-200 bg-white px-3 py-2 font-mono text-xs"
          />
          <datalist id="video-model-options">
            {selected.available_video_models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <div className="flex flex-wrap gap-1.5">
            {selected.available_video_models.map((m) => {
              const isPlan = (selected.tokenplan_video_models || []).includes(m);
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => onVideoModelChange(m)}
                  title={isPlan ? "TokenPlan 覆盖,走 _TOKENPLAN key" : "走 _PAYG key"}
                  className={
                    "rounded-full border px-2.5 py-0.5 font-mono text-[10px] " +
                    (m === videoModel
                      ? "border-sky-500 bg-sky-50 text-sky-700"
                      : "border-neutral-200 text-neutral-600 hover:border-neutral-300")
                  }
                >
                  {isPlan ? "★ " : ""}{m}
                </button>
              );
            })}
          </div>
          <div className="mt-2 text-[11px] text-neutral-500">
            ★ TokenPlan 覆盖型号(走 <code className="font-mono">MINIMAX_API_KEY_TOKENPLAN</code>);
            未标星走 PAYG(<code className="font-mono">MINIMAX_API_KEY_PAYG</code>),无对应 key 时回退到通用
            <code className="font-mono">MINIMAX_API_KEY</code>。
          </div>
        </div>
      )}
    </div>
  );
}

// 通用 provider + model + 可选 base_url 选择器 — image / music 共用,VideoProviderPicker
// 保留 TokenPlan 渠道徽章那种特殊逻辑,所以不合并。
function ProviderModelPicker({
  details, providerValue, onProviderChange,
  modelValue, onModelChange,
  baseUrlValue, onBaseUrlChange,
  secretSet,
}: {
  details: ProviderDetail[];
  providerValue: string;
  onProviderChange: (id: string) => void;
  modelValue: string;
  onModelChange: (m: string) => void;
  baseUrlValue: string | null;
  onBaseUrlChange: (u: string) => void;
  secretSet: Record<string, boolean>;
}) {
  const selected = details.find((p) => p.id === providerValue);
  const missingEnv = selected ? selected.required_env.filter((e) => !secretSet[e]) : [];
  return (
    <div>
      <div className="grid gap-3 md:grid-cols-2">
        {details.map((p) => {
          const active = p.id === providerValue;
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => onProviderChange(p.id)}
              className={
                "group flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all " +
                (active
                  ? "border-sky-500 bg-sky-50/60 shadow-sm"
                  : "border-neutral-200 bg-white hover:border-neutral-300")
              }
            >
              <div className="flex w-full items-center justify-between gap-2">
                <div className="text-sm font-medium text-neutral-900">{p.display_name}</div>
                <span
                  className={
                    "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium tracking-wide " +
                    (p.implemented
                      ? "bg-emerald-50 text-emerald-700"
                      : "bg-amber-50 text-amber-700")
                  }
                >
                  {p.implemented ? "已接入" : "占位"}
                </span>
              </div>
              <div className="font-mono text-[11px] text-neutral-400">{p.id}</div>
              {p.notes && (
                <div className="text-xs leading-relaxed text-neutral-500">{p.notes}</div>
              )}
              {p.required_env.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {p.required_env.map((e) => {
                    const ok = !!secretSet[e];
                    return (
                      <span
                        key={e}
                        className={
                          "rounded px-1.5 py-0.5 font-mono text-[10px] " +
                          (ok ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700")
                        }
                      >
                        {ok ? "✓" : "✗"} {e}
                      </span>
                    );
                  })}
                </div>
              )}
            </button>
          );
        })}
      </div>

      {missingEnv.length > 0 && (
        <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
          🔑 缺 secret:
          {" "}
          {missingEnv.map((e, i) => (
            <span key={e}>
              <code className="rounded bg-white px-1 py-0.5 font-mono">{e}</code>
              {i < missingEnv.length - 1 ? ", " : ""}
            </span>
          ))}
          。请到 <Link href="/admin/secrets" className="underline">Secrets 页</Link> 填入。
        </div>
      )}

      {selected && selected.available_models && selected.available_models.length > 0 && (
        <div className="mt-4 rounded-xl border border-neutral-200 bg-neutral-50/60 p-3">
          <div className="mb-2 text-xs font-medium text-neutral-700">模型</div>
          <input
            list={`model-options-${selected.id}`}
            value={modelValue}
            onChange={(e) => onModelChange(e.target.value)}
            placeholder={selected.default_model}
            className="mb-2 w-full rounded-lg border border-neutral-200 bg-white px-3 py-2 font-mono text-xs"
          />
          <datalist id={`model-options-${selected.id}`}>
            {selected.available_models.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          <div className="flex flex-wrap gap-1.5">
            {selected.available_models.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => onModelChange(m)}
                className={
                  "rounded-full border px-2.5 py-0.5 font-mono text-[10px] " +
                  (m === modelValue
                    ? "border-sky-500 bg-sky-50 text-sky-700"
                    : "border-neutral-200 text-neutral-600 hover:border-neutral-300")
                }
              >
                {m}
              </button>
            ))}
          </div>
        </div>
      )}

      {selected?.supports_base_url && baseUrlValue !== null && (
        <div className="mt-3">
          <Field label="Base URL(可选 · 走代理 / Azure / 自建 endpoint)">
            <input
              value={baseUrlValue}
              onChange={(e) => onBaseUrlChange(e.target.value)}
              placeholder="留空走官方端点;例如 https://your-gateway.com/v1"
              className="input"
            />
          </Field>
        </div>
      )}
    </div>
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
