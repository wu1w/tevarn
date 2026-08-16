# 官网与安装包直链（Cloudflare，无需 ICP）

## 线上现状（已部署）

| 地址 | 用途 |
|------|------|
| https://tevarn.com | 产品官网（Worker 静态资源 `tevarn-site`） |
| https://www.tevarn.com | 同上 |
| https://dl.tevarn.com | 安装包直链（Worker `tevarn-download-proxy`） |
| https://tevarn.pages.dev | Pages 备用部署 |

### 安装包 URL

```text
https://tevarn.com/downloads/Tevarn-Setup-0.4.3-x64.exe
https://dl.tevarn.com/v0.4.3/Tevarn-Setup-0.4.3-x64.exe
https://dl.tevarn.com/v0.4.0/Tevarn-Mobile-0.4.0.apk
```

大陆用户只连 Cloudflare，不直连 GitHub。Worker 从边缘拉取 GitHub Release 并 Cache API 缓存（`x-tevarn-source: github-proxy`）。

## 架构

```text
用户 ──► tevarn.com          Worker tevarn-site     (website/)
用户 ──► dl.tevarn.com       Worker download-proxy  ──► GitHub Release（边缘缓存）
                                                    └─► 可选：R2 桶（开通后优先）
```

源码：

- `deploy/cloudflare/site/` — 官网
- `deploy/cloudflare/download-proxy/` — 下载代理
- `website/index.html` — `RELEASE` 配置

## 重新部署

```powershell
$env:CLOUDFLARE_ACCOUNT_ID = "<account_id>"
$env:CLOUDFLARE_API_TOKEN = "<api_token>"

cd deploy/cloudflare/site
npx wrangler deploy

cd ../download-proxy
npx wrangler deploy
```

发新版本时：

1. 上传 GitHub Release 资产  
2. 在 `download-proxy/src/index.js` 的 `ASSETS` 增加路径映射  
3. 改 `website/index.html` 的 `RELEASE`  
4. 两边 `wrangler deploy`

## 可选：R2 真源（需 Dashboard 开通）

当前账号若未在 Dashboard 启用 R2，会报 `10042 Please enable R2 through the Cloudflare Dashboard`（通常要绑卡激活免费额度）。

开通后：

1. 建桶 `tevarn-releases`  
2. 用 `scripts/publish-downloads-r2.ps1` 上传  
3. 给 Worker 加 R2 binding `RELEASES`  
4. 代理会优先读 R2，失败再回落 GitHub  

## 安全

- **不要**把 API Token / R2 密钥提交进 Git  
- 若密钥曾出现在聊天/截图中，请到 Cloudflare 立刻 **Rotate / 删除重建**
