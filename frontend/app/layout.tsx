import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
// Pixel Console theme layer
import "./pixel-console.css";
import { AppShell } from "@/components/layout/AppShell";
import { QueryProvider } from "@/components/QueryProvider";
import Toasts from "@/components/Toasts";
import { ThemeProvider } from "@/components/ThemeProvider";

/**
 * 瀛椾綋锛氫紭鍏堣嫻鏋滅郴缁熷瓧浣擄紙SF Pro / PingFang锛夛紝璺ㄥ钩鍙板洖閫€
 * 涓嶄緷璧?Google Fonts 鍦ㄧ嚎鎷夊彇锛堢绾?浠ｇ悊鏋勫缓浼氬け璐ワ級銆? */
const fontStyle = {
  ["--font-inter" as string]:
    "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'SF Pro Display', 'Helvetica Neue', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei UI', system-ui, sans-serif",
  ["--font-jetbrains" as string]:
    "ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Monaco, 'Cascadia Code', Consolas, 'Liberation Mono', monospace",
} as React.CSSProperties;

export const metadata: Metadata = {
  title: "Takton - 涓汉涓撳睘 Agent 缁堢",
  description: "鑷墭绠″紓姝?Agent 鎺у埗鍙?,
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
        {/* beforeInteractive锛氶伩鍏嶅湪 React 瀛愭爲閲屽 <script> 瑙﹀彂 client 璀﹀憡 */}
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
