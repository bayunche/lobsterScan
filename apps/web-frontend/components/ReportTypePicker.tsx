"use client";

const TYPES = [
  { value: "daily",            label: "日常工作汇报", hint: "周报 / 日报 / 例会" },
  { value: "project_progress", label: "项目进度汇报", hint: "项目周会 / 阶段汇报" },
  { value: "review",           label: "基层管理述职", hint: "转正 / 季度 / 年度" },
  { value: "introduction",     label: "工作介绍",     hint: "岗位 / 流程 / 项目" },
];

export default function ReportTypePicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <fieldset>
      <legend className="mb-3 text-sm font-medium text-neutral-700">汇报类型</legend>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {TYPES.map((t) => {
          const active = value === t.value;
          return (
            <button
              key={t.value}
              type="button"
              onClick={() => onChange(t.value)}
              className={
                "rounded-xl border px-4 py-3 text-left transition " +
                (active
                  ? "border-[var(--color-accent)] bg-white shadow-sm"
                  : "border-neutral-200 bg-white hover:border-neutral-300")
              }
            >
              <div className="font-medium">{t.label}</div>
              <div className="mt-1 text-xs text-neutral-500">{t.hint}</div>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
