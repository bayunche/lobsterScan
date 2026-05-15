import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "龙虾集群 · 会汇报",
  description:
    "OpenClaw 多实例集群协作。把零散工作内容讲到重点上。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
