"use client";

export type Settings = {
  audience: "直属领导" | "团队内部" | "跨部门" | "客户";
  duration: "1分钟" | "3分钟" | "5分钟";
  style:    "简洁正式" | "成果突出" | "问题导向" | "述职风";
};

const AUDIENCE: Settings["audience"][] = ["直属领导", "团队内部", "跨部门", "客户"];
const DURATION: Settings["duration"][] = ["1分钟", "3分钟", "5分钟"];
const STYLE:    Settings["style"][]    = ["简洁正式", "成果突出", "问题导向", "述职风"];

export default function ReportSettings({
  value,
  onChange,
}: {
  value: Settings;
  onChange: (v: Settings) => void;
}) {
  function pick<K extends keyof Settings>(key: K, options: readonly Settings[K][]) {
    return (
      <div>
        <div className="mb-2 text-xs text-neutral-500">{label(key)}</div>
        <div className="flex flex-wrap gap-2">
          {options.map((o) => {
            const active = value[key] === o;
            return (
              <button
                key={o as string}
                type="button"
                onClick={() => onChange({ ...value, [key]: o })}
                className={
                  "rounded-full border px-3 py-1 text-sm transition " +
                  (active
                    ? "border-[var(--color-accent)] bg-[var(--color-accent)] text-white"
                    : "border-neutral-200 bg-white hover:border-neutral-300")
                }
              >
                {o}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="grid gap-5 md:grid-cols-3">
      {pick("audience", AUDIENCE)}
      {pick("duration", DURATION)}
      {pick("style", STYLE)}
    </div>
  );
}

function label(k: keyof Settings): string {
  return { audience: "汇报对象", duration: "汇报时长", style: "表达风格" }[k];
}
