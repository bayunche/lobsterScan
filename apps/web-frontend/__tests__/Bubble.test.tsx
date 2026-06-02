// P7(spec 007-chat-ux)· 前端组件测试 · US1/US2/US3
// T007/T010/T014 · 测 Bubble 三种新渲染 + renderWithMentions

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Bubble, renderWithMentions, PROMPT_TEMPLATES } from "../components/Bubble";

function msg(over: Partial<any> = {}): any {
  return {
    id: "m1", agent: "point-extractor", display_name: "分析师", seal: "析",
    ts: 1_700_000_000, kind: "result", text: "", ...over,
  };
}

// ────────────────────────── US1 @高亮 ──────────────────────────

describe("renderWithMentions (US1 @高亮)", () => {
  it("纯字符串无 @ → 原样返回", () => {
    expect(renderWithMentions("没有提及")).toBe("没有提及");
  });
  it("含 @成员名 → 返回数组(拆出 mention 节点)", () => {
    const out = renderWithMentions("交给 @分析师 看");
    expect(Array.isArray(out)).toBe(true);
  });
});

describe("Bubble @高亮渲染 (US1)", () => {
  it("@分析师 → 渲染出 mention chip(US1-AC1)", () => {
    const { container } = render(<Bubble msg={msg({ text: "请 @分析师 接着看" })} />);
    const chips = container.querySelectorAll(".mention");
    expect(chips.length).toBe(1);
    expect(chips[0].textContent).toBe("@分析师");
  });
  it("多个 @ → 各自高亮(US1-AC2)", () => {
    const { container } = render(<Bubble msg={msg({ text: "@设计师 @视频制作 一起上" })} />);
    const chips = container.querySelectorAll(".mention");
    expect(chips.length).toBe(2);
  });
  it("@非成员名 → 不高亮(US1-AC3)", () => {
    const { container } = render(<Bubble msg={msg({ text: "@隔壁老王 在吗" })} />);
    expect(container.querySelectorAll(".mention").length).toBe(0);
  });
});

// ────────────────────────── US2 silent 灰显 ──────────────────────────

describe("Bubble silent 气泡 (US2)", () => {
  it("kind=silent + reason → 含「掠过」+ 成员名 + 理由(US2-AC1)", () => {
    render(<Bubble msg={msg({ kind: "silent", silent_reason: "等大纲就绪" })} />);
    expect(screen.getByText(/掠过/)).toBeInTheDocument();
    expect(screen.getByText(/分析师/)).toBeInTheDocument();
    expect(screen.getByText(/等大纲就绪/)).toBeInTheDocument();
  });
  it("silent 无 reason → 仅「{名} 掠过」不报错(edge)", () => {
    render(<Bubble msg={msg({ kind: "silent" })} />);
    expect(screen.getByText(/分析师 掠过/)).toBeInTheDocument();
  });
});

// ────────────────────────── US4 prompt 模板 ──────────────────────────

describe("PROMPT_TEMPLATES (US4)", () => {
  it("有一组模板,每个含 label + 非空 text(填入输入框用)", () => {
    expect(PROMPT_TEMPLATES.length).toBeGreaterThanOrEqual(5);
    for (const t of PROMPT_TEMPLATES) {
      expect(t.label).toBeTruthy();
      expect(t.text.length).toBeGreaterThan(0);
    }
  });
  it("覆盖原 5 个 refine 动作语义", () => {
    const labels = PROMPT_TEMPLATES.map((t) => t.label);
    expect(labels).toContain("再短一点");
    expect(labels).toContain("更突出问题");
  });
});

// ────────────────────────── US3 artifact diff ──────────────────────────

describe("Bubble artifact diff (US3)", () => {
  it("artifact_delta version=2 → diff 行(US3-AC1)", () => {
    render(<Bubble msg={msg({
      text: "改好了",
      artifact_delta: { id: "大纲", version: 2, summary: "补充风险章节" },
    })} />);
    expect(screen.getByText(/改了 大纲 第 2 版/)).toBeInTheDocument();
    expect(screen.getByText(/补充风险章节/)).toBeInTheDocument();
  });
  it("无 artifact_delta → 无 diff 行(US3-AC2)", () => {
    const { container } = render(<Bubble msg={msg({ text: "首次产出" })} />);
    expect(container.querySelector(".bb-diff")).toBeNull();
  });
});
