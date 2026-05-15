"use client";

export default function MaterialInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="mb-3 block text-sm font-medium text-neutral-700">
        粘贴本周工作记录、任务清单、问题反馈…
      </label>
      <textarea
        rows={8}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="例：本周完成客户回访 18 个，活动页面设计初稿完成，测试发现 3 个问题……"
        className="w-full resize-y rounded-xl border border-neutral-200 bg-white px-4 py-3 text-sm leading-relaxed focus:border-[var(--color-accent)] focus:outline-none"
      />
      <div className="mt-2 text-xs text-neutral-500">
        支持 .txt / .md / .docx / .xlsx / .pdf 文件上传（MVP 暂未启用）
      </div>
    </div>
  );
}
