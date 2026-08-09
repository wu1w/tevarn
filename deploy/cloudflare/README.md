# Cloudflare 部署（官网 + 下载直链）

## 组件

| 资源 | 用途 | 域名 |
|------|------|------|
| Pages `tevarn` | 静态官网 `website/` | `tevarn.com` |
| Worker `tevarn-download-proxy` | 安装包直链 | `dl.tevarn.com` |

下载 Worker 默认从 **GitHub Release 经 Cloudflare 边缘代理**（大陆用户只连 CF，不直连 GitHub）。  
若账号已开通 R2，可绑定桶 `tevarn-releases` 作为优先源（见 `docs/WEBSITE_DOWNLOADS.md`）。

## 部署

```powershell
$env:CLOUDFLARE_ACCOUNT_ID = "<account_id>"
$env:CLOUDFLARE_API_TOKEN = "<api_token>"

# 下载代理
cd deploy/cloudflare/download-proxy
npx wrangler deploy

# 官网
cd ../../..
npx wrangler pages project create tevarn --production-branch main
npx wrangler pages deploy website --project-name tevarn --branch main
```

自定义域在 Dashboard 绑定，或用 API（脚本已写在运维流程里）。
