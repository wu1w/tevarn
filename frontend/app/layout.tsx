import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
// Pixel Console 主题层：覆盖令牌 + tk-* 结构类；删除本行即回退原主题
import "./pixel-console.css";
import { AppShell } from "@/components/layout/AppShell";
import { QueryProvider } from "@/components/QueryProvider";
import Toasts from "@/components/Toasts";
import { ThemeProvider } from "@/components/ThemeProvider";

/**
 * 字体：优先苹果系统字体（SF Pro / PingFang），跨平台回退
 * 不依赖 Google Fonts 在线拉取（离线/代理构建会失败）。
 */
const fontStyle = {
  ["--font-inter" as string]:
    "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'SF Pro Display', 'Helvetica Neue', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei UI', system-ui, sans-serif",
  ["--font-jetbrains" as string]:
    "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Monaco, 'Cascadia Code', Consolas, 'Liberation Mono', monospace",
} as React.CSSProperties;

export const metadata: Metadata = {
  title: "Takton - Personal Agent Console",
  description: "Self-hosted async Agent console",
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
