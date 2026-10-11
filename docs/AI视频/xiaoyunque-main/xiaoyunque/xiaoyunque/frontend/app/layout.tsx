import '../components/stages/manual-revisions.css';
import type { Metadata } from "next";
import AppShell from "@/components/AppShell";
import "./globals.css";

export const metadata: Metadata = {
  title: "映序｜AIGC 视频创作与任务编排 Agent",
  description: "面向独立短片、短剧创作者，解决素材分散、角色与场景版本不一致、上游修改导致下游返工等问题，构建从脚本、资产、分镜到生成、审核与导出的创作流程。",
  icons: { icon: { url: '/studio-mark.svg', type: 'image/svg+xml' }, shortcut: '/studio-mark.svg' },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body
        className="antialiased"
      >
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
