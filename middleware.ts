import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

// 公开路由（不需要认证）
const publicRoutes = ['/login'];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // 公开路由直接放行
  if (publicRoutes.some((route) => pathname === route || pathname.startsWith(`${route}/`))) {
    return NextResponse.next();
  }

  // 静态资源文件直接放行
  if (/\.[a-zA-Z0-9]+$/.test(pathname)) {
    return NextResponse.next();
  }

  // Next.js 内部路径放行
  if (pathname.startsWith('/_next')) {
    return NextResponse.next();
  }

  // API 路由放行（后端有自己的认证）
  if (pathname.startsWith('/api/')) {
    return NextResponse.next();
  }

  // 检查认证 cookie（authStore 持久化为 'takton-auth'）
  const authCookie = request.cookies.get('takton-auth');
  const hasAuth = !!authCookie;

  // 未登录且访问受保护路由 → 重定向到登录页
  if (!hasAuth) {
    const loginUrl = new URL('/login', request.url);
    loginUrl.searchParams.set('redirect', pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * 匹配所有路径，除了：
     * - api 路由（API 有自己的认证中间件）
     * - _next/static（静态文件）
     * - _next/image（图片优化）
     * - favicon.ico
     */
    '/((?!api|_next/static|_next/image|favicon.ico).*)',
  ],
};
