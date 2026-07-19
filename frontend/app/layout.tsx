import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import { AppShell } from "@/components/layout/AppShell";
import { QueryProvider } from "@/components/QueryProvider";
import Toasts from "@/components/Toasts";
import { ThemeProvider } from "@/components/ThemeProvider";

/**
 * 不依赖 Google Fonts 在线拉取（离线/代理构建会失败）。
 */
const fontStyle = {
  ["--font-inter" as string]:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif",
  ["--font-jetbrains" as string]:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
} as React.CSSProperties;

export const metadata: Metadata = {
  title: "Takton - 个人专属 Agent 终端",
  description: "自托管异步 Agent 控制台",
};

const themeBootScript = `
(function() {
  try {
    var pref = 'system';
    var raw = localStorage.getItem('takton-theme');
    if (raw) {
      try {
        var parsed = JSON.parse(raw);
        if (parsed && parsed.state && parsed.state.theme) {
          pref = parsed.state.theme;
        } else if (raw === 'light' || raw === 'dark' || raw === 'system') {
          pref = raw;
        }
      } catch (e1) {
        if (raw === 'light' || raw === 'dark' || raw === 'system') pref = raw;
      }
    }
    var resolved = pref;
    if (pref === 'system' || !pref) {
      resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-theme', resolved);
    if (resolved === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
    document.documentElement.style.colorScheme = resolved;
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      suppressHydrationWarning
      className="h-full antialiased"
      style={fontStyle}
    >
      <body className="font-sans h-full overflow-hidden flex flex-col bg-page-bg text-foreground text-ui">
        {/* beforeInteractive：避免在 React 子树里塞 <script> 触发 client 警告 */}
        <Script id="takton-theme-boot" strategy="beforeInteractive">
          {themeBootScript}
        </Script>
        <ThemeProvider>
          <QueryProvider>
            <AppShell>{children}</AppShell>
            <Toasts />
          </QueryProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
