import type { Metadata } from "next";
import AppShell from "@/components/AppShell";
import "./globals.css";

export const metadata: Metadata = {
  title: "创作工作室",
  description: "从一句灵感到一部短片，把脑海里的故事变成画面。",
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
