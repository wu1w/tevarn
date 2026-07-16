import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/layout/AppShell";
import { QueryProvider } from "@/components/QueryProvider";
import Toasts from "@/components/Toasts";
import { ThemeProvider } from "@/components/ThemeProvider";

/** 拉丁 UI：Inter — 现代产品默认高级感 */
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

/** 代码 / 模型 ID */
const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "Takton - 个人专属 Agent 终端",
  description: "自托管异步 Agent 控制台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="zh-CN"
      suppressHydrationWarning
      className={`${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `
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
                document.documentElement.style.colorScheme = resolved;
              } catch(e) {}
            })();
          `,
          }}
        />
      </head>
      <body className="font-sans h-full overflow-hidden flex flex-col bg-page-bg text-foreground text-ui">
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