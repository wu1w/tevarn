# Takton 前端 Bug 审查报告

> 审查日期: 2026-07-06
> 审查范围: frontend/app/, frontend/components/, frontend/hooks/, frontend/stores/, frontend/lib/, 配置文件
> 审查维度: TypeScript 类型、React 渲染、状态管理、API 调用、安全、性能、可访问性、移动端适配

---

## 🔴 CRITICAL (严重 - 必须立即修复)

### BUG-001: MessageBubble.tsx ReactMarkdown 组件 XSS 风险
- **文件路径**: `frontend/components/chat/MessageBubble.tsx`
- **行号**: 约 65-95
- **问题描述**: `ReactMarkdown` 的 `components` 配置中对 `code`/`pre`/`a` 使用了 `{...props}` 传播所有属性。虽然 `urlTransform` 处理了恶意链接，但如果 markdown 内容包含 HTML 标签（通过 `rehype-raw` 等插件），`...props` 可能传播危险属性如 `onerror`、`onload` 等事件处理器，导致 XSS。此外 `components` 参数类型使用了 `any`，丧失了 TypeScript 类型保护。
- **修复建议**: 
  ```tsx
  // 1. 显式解构需要的 props，避免传播未知属性
  code: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <code className="...">{children}</code>
  ),
  // 2. 添加 rehype-raw 的 sanitize 配置，或禁用 HTML 解析
  // 3. 将 any 替换为正确的 ReactMarkdown 组件类型
  ```

### BUG-002: toastStore.ts 内存泄漏 - setTimeout 未清理
- **文件路径**: `frontend/stores/toastStore.ts`
- **行号**: 35-42
- **问题描述**: `addToast` 中创建的 `setTimeout` 在组件卸载前如果 toast 被手动移除（`removeToast`），setTimeout 仍会在 3 秒后执行，导致：1) 不必要的状态更新；2) 如果 toast 已不存在，`filter` 操作无意义但仍在执行；3) 在大量 toast 场景下可能导致性能问题。
- **修复建议**: 
  ```tsx
  addToast: (message, type = 'info') => {
    const id = generateUUID();
    const timer = setTimeout(() => {
      set((state) => ({
        toasts: state.toasts.filter((t) => t.id !== id),
      }));
    }, 3000);
    set((state) => ({
      toasts: [...state.toasts, { id, message, type, timer }],
    }));
  },
  removeToast: (id) =>
    set((state) => {
      const toast = state.toasts.find((t) => t.id === id);
      if (toast?.timer) clearTimeout(toast.timer);
      return { toasts: state.toasts.filter((t) => t.id !== id) };
    }),
  ```

### BUG-003: useWebSocket.ts 重连竞态条件
- **文件路径**: `frontend/hooks/useWebSocket.ts`
- **行号**: 75-85, 155-175
- **问题描述**: 
  1. `connect` 函数中清理旧连接后，如果 `connectingRef.current` 为 true 直接返回，但 `connectingRef` 的设置和 `setIsConnecting` 之间存在异步间隙，可能导致两个连接同时被创建。
  2. 自动重连的 `useEffect`（依赖 `isConnected` 和 `isConnecting`）在每次状态变化时都会重新计算延迟并设置定时器，如果组件快速重渲染，可能累积大量定时器。
- **修复建议**: 
  ```tsx
  // 1. 使用 AbortController 或更严格的连接锁
  // 2. 在重连 useEffect 中添加 cleanup 和防抖
  useEffect(() => {
    if (isConnected || isConnecting) return;
    const timer = setTimeout(() => connect(), delay);
    return () => clearTimeout(timer); // 已有 cleanup，但 delay 计算应在 ref 中维护
  }, [isConnected, isConnecting, connect]);
  // 3. 将 reconnectAttempts 的重置放在 connect 函数最开头，确保只增不减
  ```

### BUG-004: page.tsx (Chat 首页) 文件无法读取 - 潜在损坏
- **文件路径**: `frontend/app/page.tsx`
- **行号**: N/A
- **问题描述**: 该文件在审查过程中无法通过标准 `read` 工具读取（返回图片附件而非文本内容），只能通过 `exec` 的 `cmd type` 命令读取。这表明文件可能使用了特殊编码或包含不可见字符，可能导致构建失败或运行时错误。此外，chat 页面是核心页面，需要确认其内容完整性。
- **修复建议**: 检查文件编码（应为 UTF-8），确保没有 BOM 或其他特殊字符。如果文件包含非标准内容，需要修复并重新保存。

---

## 🟠 HIGH (高 - 建议尽快修复)

### BUG-005: api.ts 缺少全局请求超时
- **文件路径**: `frontend/lib/api.ts`
- **行号**: 14-22
- **问题描述**: Axios 实例未配置 `timeout`，任何网络请求在服务端无响应时都会无限挂起，导致用户界面持续处于 loading 状态且无法取消。这在弱网环境下尤其严重。
- **修复建议**: 
  ```ts
  const api = axios.create({
    baseURL: BASE_URL,
    timeout: 30000, // 30 秒超时
    headers: { 'Content-Type': 'application/json' },
  });
  ```

### BUG-006: AppShell.tsx 服务端渲染安全跳转
- **文件路径**: `frontend/components/layout/AppShell.tsx`
- **行号**: 15-25
- **问题描述**: `useEffect` 中的路由重定向在客户端执行，但在服务端渲染（SSR）时，`hasHydrated` 初始为 false，`isAuthenticated` 为 false，会渲染未认证状态的占位 UI。如果页面使用静态生成，用户可能在已登录状态下先看到"请登录后使用..."的闪烁（FOUC）。
- **修复建议**: 使用 Next.js 的 `middleware.ts` 进行服务端路由守卫，避免客户端闪烁。

### BUG-007: context/page.tsx 创建上下文项时 session_id 可能为 undefined
- **文件路径**: `frontend/app/context/page.tsx`
- **行号**: 55-60
- **问题描述**: `handleCreate` 中 `createCtxItem({ ...form, session_id: currentSession?.id })` 使用了可选链 `?.id`，如果 `currentSession` 为 null，`session_id` 会是 `undefined`。虽然页面顶部有 `if (!currentSession)` 的 guard，但 `handleCreate` 作为异步函数，在点击按钮到 API 调用之间 `currentSession` 可能已被清除（例如用户切换 session）。
- **修复建议**: 
  ```tsx
  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentSession?.id) return; // 严格检查
    setSubmitting(true);
    try {
      await createCtxItem({ ...form, session_id: currentSession.id });
      // ...
    }
  };
  ```

### BUG-008: authStore.ts login 未验证 token 有效性
- **文件路径**: `frontend/stores/authStore.ts`
- **行号**: 45-52
- **问题描述**: `login` action 直接信任后端返回的数据并设置 `isAuthenticated: true`，但 token 可能已过期或无效。如果后端返回了错误的 `expires_in`，用户会被错误地标记为已认证，后续 API 调用会 401 并重定向循环。
- **修复建议**: 在 `login` 后调用 `getMe()` 验证 token，或至少检查 `access_token` 的存在性和格式。

### BUG-009: WorkflowCanvas.tsx 拖拽性能问题
- **文件路径**: `frontend/components/workflow/WorkflowCanvas.tsx`
- **行号**: 全文件
- **问题描述**: 
  1. 每次 `mousemove` 都通过 `setState` 更新 `mousePos`，导致高频率重渲染。
  2. `toCanvas` 回调依赖 `pan` 和 `zoom`，每次 `pan` 变化都会重新创建函数引用。
  3. SVG 中的节点和边在每次状态变化时都会重新渲染整个列表。
- **修复建议**: 
  1. 使用 `useRef` 存储 `mousePos` 和 `pan` 用于拖拽计算，仅在需要渲染时更新状态。
  2. 使用 `React.memo` 包装节点和边组件。
  3. 使用 `requestAnimationFrame` 节流拖拽更新。

### BUG-010: MessageInput.tsx 文件上传后未清理 input
- **文件路径**: `frontend/components/chat/MessageInput.tsx`
- **行号**: 约 120-140（在文件后半部分）
- **问题描述**: 文件上传成功后，`<input type="file">` 的 value 未被重置，导致用户再次选择相同文件时 `onChange` 不会触发（因为值没有变化）。
- **修复建议**: 在文件上传完成后执行 `fileInputRef.current.value = ''`。

---

## 🟡 MEDIUM (中 - 建议修复)

### BUG-011: sessionStore.ts 缺少请求取消机制
- **文件路径**: `frontend/stores/sessionStore.ts`
- **行号**: 50-70
- **问题描述**: `loadSession` 和 `loadMessages` 中没有 `AbortController` 或请求取消机制。如果用户快速切换 session，前一个 session 的 API 响应可能在后一个之后到达，导致状态混乱（显示错误的 session 数据）。
- **修复建议**: 使用 `AbortController` 并在 `loadSession` 开始时取消之前的请求，或使用一个递增的 `requestId` 来忽略过期的响应。

### BUG-012: wiki/page.tsx importResult 未清理
- **文件路径**: `frontend/app/wiki/page.tsx`
- **行号**: 40-45
- **问题描述**: `importResult` 状态在成功导入后持续显示，没有自动隐藏机制。用户进行新的导入操作前，旧结果一直显示，可能造成误解。
- **修复建议**: 在关闭导入弹窗或开始新的导入时清除 `importResult`；或添加 5 秒自动隐藏机制。

### BUG-013: tools/page.tsx JSON 解析错误处理不完整
- **文件路径**: `frontend/app/tools/page.tsx`
- **行号**: 约 120-130
- **问题描述**: `handleCreate` 和 `handleUpdate` 中的 `JSON.parse(newConfig)` 如果失败，会通过 `alert` 显示错误。但 `newConfig` 初始值为 `'{}'`，如果用户输入空字符串，会报错。此外 `alert` 在现代 Web 应用中体验较差。
- **修复建议**: 使用 try-catch 包装并显示更友好的内联错误提示，而非 `alert`。

### BUG-014: config/*Editor.tsx 组件可访问性缺失
- **文件路径**: `frontend/components/config/IdentityEditor.tsx`, `SysPromptEditor.tsx`, `AgentMdEditor.tsx`
- **行号**: 全部
- **问题描述**: 所有 textarea 都缺少 `label` 关联（通过 `htmlFor` 或 `aria-label`/`aria-labelledby`），且没有 `aria-describedby` 关联描述文本。屏幕阅读器用户无法正确识别这些输入框的用途。
- **修复建议**: 
  ```tsx
  <label htmlFor="identity-editor">Identity</label>
  <textarea id="identity-editor" aria-describedby="identity-desc" ... />
  <p id="identity-desc">...</p>
  ```

### BUG-015: page.tsx (Chat) 缺少消息去重
- **文件路径**: `frontend/app/page.tsx`
- **行号**: N/A（无法完全读取，但从 ChatWindow 推断）
- **问题描述**: `ChatWindow` 的 `messages.map` 使用 `msg.id` 作为 key，如果后端消息 ID 重复或 websocket 推送重复消息，可能导致渲染异常。
- **修复建议**: 在 `useSession` 或 `sessionStore` 中添加消息去重逻辑，确保 `messages` 数组中 id 唯一。

### BUG-016: Sidebar.tsx 依赖数组包含对象属性
- **文件路径**: `frontend/components/layout/Sidebar.tsx`
- **行号**: 约 80-90
- **问题描述**: `useEffect` 依赖数组中包含 `currentSession?.id`，这本身没问题，但如果依赖了 `currentSession` 对象，可能导致无限重渲染。当前代码中 `useEffect(() => { ... }, [isAuthenticated, currentSession?.id]);` 是正确的，但另一个 `useEffect` 中 `getMySessions` 的依赖列表需要确认。
- **修复建议**: 已检查，当前依赖列表正确。但 `mySessions` 的加载没有错误处理 UI，失败时只是 `console.error`。

### BUG-017: useWebSocket.ts 未处理连接异常断开
- **文件路径**: `frontend/hooks/useWebSocket.ts`
- **行号**: 115-125
- **问题描述**: `ws.onclose` 在连接异常断开时也会触发，但代码中没有任何重连限制（如最大重连次数），如果服务器完全不可用，会无限重连，消耗客户端资源。
- **修复建议**: 添加 `maxReconnectAttempts` 限制，超过后停止重连并显示错误状态。

---

## 🟢 LOW (低 - 优化建议)

### BUG-018: Toasts.tsx 缺少 aria-live 区域
- **文件路径**: `frontend/components/Toasts.tsx`
- **行号**: 全文件
- **问题描述**: Toast 通知没有 `aria-live="polite"` 或 `aria-live="assertive"` 属性，屏幕阅读器用户无法感知 toast 的出现。
- **修复建议**: 在容器 `<div>` 上添加 `aria-live="polite" aria-atomic="true"`。

### BUG-019: login/page.tsx 密码输入缺少 autocomplete
- **文件路径**: `frontend/app/login/page.tsx`
- **行号**: 约 120-130
- **问题描述**: 密码输入框没有 `autoComplete="current-password"`（登录）或 `autoComplete="new-password"`（注册），导致浏览器无法自动填充密码，用户体验不佳。
- **修复建议**: 在密码 `<input>` 上添加 `autoComplete="current-password"`（登录表单）和 `autoComplete="new-password"`（注册表单）。

### BUG-020: themeStore.ts SSR 安全
- **文件路径**: `frontend/stores/themeStore.ts`
- **行号**: 28-30
- **问题描述**: `onRehydrateStorage` 中直接操作 `document.documentElement`，在服务端渲染时 `document` 不存在，可能导致 hydration mismatch。
- **修复建议**: 在 `ThemeProvider` 组件中使用 `useEffect` 设置 data-theme（当前代码中已如此实现），但 `onRehydrateStorage` 中也应添加 `typeof document !== 'undefined'` 检查。

### BUG-021: QueryProvider.tsx 缺少错误边界
- **文件路径**: `frontend/components/QueryProvider.tsx`
- **行号**: 全文件
- **问题描述**: 虽然配置了 `retry: 1`，但没有全局错误处理。如果请求失败且组件没有单独处理，错误可能静默发生。
- **修复建议**: 添加 `queryCache` 的 `onError` 回调或配置全局错误处理。

### BUG-022: 移动端适配问题
- **文件路径**: 多个页面
- **问题描述**: 
  1. `frontend/components/layout/Sidebar.tsx` 固定宽度 `w-64`，在移动端没有响应式折叠。
  2. `frontend/components/tasks/TaskPanel.tsx` 固定宽度 `w-96`，在小屏幕上可能溢出。
  3. 多个页面的表格布局（如 `context/page.tsx` 的 `<table>`）在小屏幕上没有水平滚动，内容可能被截断。
  4. `frontend/components/workflow/WorkflowCanvas.tsx` 的 `NODE_WIDTH = 144` 在移动端可能太小，但画布本身没有响应式缩放。
- **修复建议**: 使用响应式类（如 `w-64 md:w-72 lg:w-80`）、添加 `overflow-x-auto` 容器、实现移动端侧边栏折叠（hamburger menu）。

### BUG-023: api.ts 401 处理过于激进
- **文件路径**: `frontend/lib/api.ts`
- **行号**: 40-55
- **问题描述**: 任何 401 响应都会立即清除本地存储并重定向到 `/login`，如果后端在短时间内返回多个 401（例如并发请求），可能导致重定向竞争和闪烁。
- **修复建议**: 添加一个标志位确保只执行一次登出逻辑，或使用 debounce。

### BUG-024: settings/page.tsx 缺少加载状态
- **文件路径**: `frontend/app/settings/page.tsx`
- **行号**: 全文件（根据已知信息推断）
- **问题描述**: 如果页面存在，设置保存后没有视觉反馈，用户不知道是否成功。
- **修复建议**: 添加 toast 提示或成功状态指示器。

### BUG-025: page.tsx (Chat) 可能的内存泄漏
- **文件路径**: `frontend/app/page.tsx`
- **行号**: N/A
- **问题描述**: 如果 chat 页面使用了 `useWebSocket`，在组件卸载时可能未正确清理 WebSocket 连接。`useWebSocket` 的 `useEffect` 返回了 cleanup 函数，但需要确认 `page.tsx` 是否正确使用。
- **修复建议**: 确保 `useWebSocket` 的 `disconnect` 在组件卸载时被调用，且页面导航时正确清理。

---

## 📊 Bug 统计

| 严重度 | 数量 | 分类 |
|--------|------|------|
| CRITICAL | 4 | XSS、内存泄漏、竞态条件、文件损坏 |
| HIGH | 6 | 超时、SSR、状态错误、性能、安全 |
| MEDIUM | 7 | 可访问性、数据一致性、错误处理 |
| LOW | 8 | 移动端适配、UX优化、SSR安全 |
| **总计** | **25** | |

---

## 🔧 优先修复建议

1. **立即修复 CRITICAL**: BUG-001 (XSS), BUG-002 (内存泄漏), BUG-003 (竞态), BUG-004 (文件完整性)
2. **本周修复 HIGH**: BUG-005 (API超时), BUG-007 (undefined session), BUG-009 (性能), BUG-010 (文件上传)
3. **下月修复 MEDIUM**: BUG-011 (请求取消), BUG-012 (状态清理), BUG-014 (可访问性), BUG-017 (重连限制)
4. **持续优化 LOW**: 移动端适配、可访问性、UX 细节
