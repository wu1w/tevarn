import type { NextConfig } from "next";

// 打包时设置 NEXT_EXPORT=1 走纯静态导出（Electron 内置静态服务器托管，不依赖 next start）
const isExport = process.env.NEXT_EXPORT === "1";

const nextConfig: NextConfig = {
  // This app has its own lockfile. Pinning the root prevents Turbopack from
  // accidentally reusing caches or resolving dependencies from the repo root.
  turbopack: {
    root: process.cwd(),
  },
  allowedDevOrigins: process.env.ALLOWED_DEV_ORIGINS?.split(',') || ["localhost", "127.0.0.1"],
  // dev/普通 build 用 .next；仅静态导出写 dist。
  // 二者共用 dist 时，Electron NEXT_EXPORT 产物会污染 next dev，导致 /usage 等路由偶发 404。
  distDir: isExport ? "dist" : ".next",
  // 静态导出模式：前后端通过 IPC 直连（lib/api.ts 在 Electron 环境返回 http://127.0.0.1:8095/api）
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
