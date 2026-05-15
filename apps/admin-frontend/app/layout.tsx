import "./globals.css";

export const metadata = {
  title: "OpenClaw 龙虾集群 · 管理平台",
  description: "Agent / Skill / Channel / Pipeline 统一控制面",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
