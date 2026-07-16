import type { NextConfig } from "next";

// 打包时设置 NEXT_EXPORT=1 走纯静态导出（Electron 内置静态服务器托管，不依赖 next start）
const isExport = process.env.NEXT_EXPORT === "1";

const nextConfig: NextConfig = {
  typescript: {
    ignoreBuildErrors: true,
  },
  allowedDevOrigins: process.env.ALLOWED_DEV_ORIGINS?.split(',') || ["localhost", "127.0.0.1"],
  distDir: "dist",
  // 静态导出模式：前后端通过 IPC 直连（lib/api.ts 在 Electron 环境返回 http://127.0.0.1:8000/api）
  ...(isExport ? { output: "export" as const, trailingSlash: true } : {}),
  // 开发/测试模式：用 Next.js rewrites 做 API 反向代理，方便浏览器 E2E
  ...(isExport
    ? {}
    : {
        async rewrites() {
          return [
            {
              source: "/api/:path*",
              destination: "http://127.0.0.1:8090/api/:path*",
            },
            {
              source: "/ws/:path*",
              destination: "http://127.0.0.1:8090/api/ws/:path*",
            },
          ];
        },
      }),
};

export default nextConfig;
