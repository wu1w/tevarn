# 官网安装包直链（Cloudflare R2，无需 ICP 备案）

目标：大陆用户从 **tevarn.com 官网** 下载 Windows Setup / Android APK，**不经过 GitHub**。

不把二进制提交进 Git（体积大）。安装包放在 **Cloudflare R2**，用子域名 `dl.tevarn.com` 对外。

> R2 在 Cloudflare 全球节点分发，**不是国内机房**，因此 **不需要 ICP 备案**。  
> 体验通常优于 GitHub；若个别地区 CF 仍慢，可保留 GitHub 备用按钮。

---

## 架构

```text
tevarn.com          →  Cloudflare Pages  （静态官网 website/）
dl.tevarn.com       →  Cloudflare R2     （安装包）
github.com/.../releases  →  备用源
```

对象键约定：

```text
v0.4.0/Tevarn-Setup-0.4.0-x64.exe
v0.4.0/Tevarn-Mobile-0.4.0.apk
```

对应 URL：

```text
https://dl.tevarn.com/v0.4.0/Tevarn-Setup-0.4.0-x64.exe
https://dl.tevarn.com/v0.4.0/Tevarn-Mobile-0.4.0.apk
```

官网 `website/index.html` 里的 `RELEASE.cdnBase` 指向 `https://dl.tevarn.com`。

---

## 一次性配置（Cloudflare Dashboard，约 10 分钟）

### 1. 创建 R2 桶

1. Cloudflare Dashboard → **R2 Object Storage** → **Create bucket**
2. 名称建议：`tevarn-releases`
3. 区域默认即可

### 2. 允许公开读

**推荐：自定义域名（生产）**

1. 打开桶 → **Settings** → **Custom Domains** → **Connect Domain**
2. 填入 `dl.tevarn.com`
3. 确认域名在同一 Cloudflare 账号下（tevarn.com 已在 CF 即可自动加 DNS）
4. 等状态变为 **Active**

**可选：r2.dev 临时公网**

1. 桶 → **Settings** → **Public access** → **Allow Access**
2. 得到 `https://pub-xxxxx.r2.dev`
3. 把官网 `RELEASE.cdnBase` 临时改成该地址（上线自定义域后再改回 `https://dl.tevarn.com`）

### 3. CORS（可选）

仅当浏览器 JS 要读文件头时需要；**直接 `<a href>` 下载一般不用 CORS**。

### 4. 部署官网到 Pages

1. **Workers & Pages** → **Create** → **Pages** → 连接 `wu1w/tevarn`
2. Root directory: `website`
3. Build command: 空
4. Output: `/` 或 `.`
5. Custom domain: `tevarn.com` / `www.tevarn.com`

---

## 上传安装包

### 方式 A：脚本（推荐）

本机已装 Node.js，在仓库根目录：

```powershell
# 首次：登录 Cloudflare
npx wrangler login

# 上传 v0.4.0 的两个包（路径可按本机实际修改）
.\scripts\publish-downloads-r2.ps1 `
  -Version 0.4.0 `
  -Bucket tevarn-releases `
  -WinSetup "frontend\release\Tevarn-Setup-0.4.0-x64.exe" `
  -Apk "mobile\dist\Tevarn-Mobile-0.4.0.apk"
```

脚本会上传到：

- `v0.4.0/Tevarn-Setup-0.4.0-x64.exe`
- `v0.4.0/Tevarn-Mobile-0.4.0.apk`

并打印最终 URL。

### 方式 B：Dashboard 网页上传

R2 桶 → **Upload** → 先建前缀文件夹 `v0.4.0/` → 拖入两个文件。

### 方式 C：API Token（CI）

创建 Token 权限：`Account.R2 Storage Edit`，在 GitHub Secrets 写入：

- `CF_ACCOUNT_ID`
- `CF_API_TOKEN`

（可选后续加 Actions 在 release 时自动同步。）

---

## 发新版本时 checklist

1. 打好 `Tevarn-Setup-x.y.z-x64.exe` 与 `Tevarn-Mobile-x.y.z.apk`
2. 上传到 R2：`vX.Y.Z/...`
3. 改 `website/index.html` 中 `RELEASE` 对象的 `version` / 文件名 / `ghTag` / `ghBase`
4. 同步上传 GitHub Release（海外备用）
5. 推送 main，Pages 自动刷新官网

---

## 验证

```powershell
# 应返回 200，且 Content-Length 接近本地文件
curl.exe -I "https://dl.tevarn.com/v0.4.0/Tevarn-Setup-0.4.0-x64.exe"
curl.exe -I "https://dl.tevarn.com/v0.4.0/Tevarn-Mobile-0.4.0.apk"
```

浏览器打开 `https://tevarn.com/#download`，点 **官网直链下载**。

---

## 费用与限制（大致）

- R2 免费额：存储 10 GB / 月，A 类操作有免费配额；**经 CF 出网不计经典流量费**（相对 S3 友好）
- 单文件 120MB 级完全可接受
- **不要**把安装包放进 Git / Cloudflare Pages 构建产物（Pages 单文件约 25MB 上限）

---

## 为什么不备案也能给大陆用

| 方式 | 是否要 ICP | 说明 |
|------|------------|------|
| Cloudflare R2 + `dl.tevarn.com` | 否 | 源站不在中国大陆 |
| 阿里云 OSS + 国内 CDN + 自有域名 | 通常要 | 国内加速必须备案 |
| 只挂 GitHub Release | 否 | 大陆常连不上 |

本方案选第一行：不备案、比 GitHub 稳、和官网同品牌域名。
