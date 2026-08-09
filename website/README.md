# Tevarn 官网落地页

**冷白机身 · Light Console** 风格的产品官网单页。

## 品牌色

- 电光紫 `#6D5DF6` / `#8B7CFF`
- 霓虹青 `#00A8C0` / `#22D8EE`

## 线上地址

| 线路 | URL |
|------|-----|
| 国际 | https://tevarn.com |
| 大陆镜像 | https://cdn.jsdmirror.com/gh/wu1w/tevarn@main/website/index.html |

大陆访问说明：[`docs/CHINA_ACCESS.md`](../docs/CHINA_ACCESS.md)

## 本地预览

```bash
npx --yes serve website -p 5173
# 浏览器打开 http://127.0.0.1:5173
```
## 文件

| 路径 | 说明 |
|------|------|
| `index.html` | 完整单页（内联样式与交互） |
| `assets/logo.png` | 品牌 Logo |

## 下载区

页面 `#download`：

| 项 | 地址 |
|----|------|
| Windows Setup | `https://dl.tevarn.com/vX.Y.Z/Tevarn-Setup-…exe` |
| Android APK | `https://dl.tevarn.com/vX.Y.Z/Tevarn-Mobile-….apk` |
| VPS 一键脚本 | `https://tevarn.com/install-vps-relay.sh` |

```bash
curl -fsSL https://tevarn.com/install-vps-relay.sh | sudo bash
```

安装包经 Cloudflare 边缘代理；脚本随 `website/` 静态部署。发版说明见 [`docs/WEBSITE_DOWNLOADS.md`](../docs/WEBSITE_DOWNLOADS.md)。

## 部署建议

1. Cloudflare Pages 绑定仓库，Root = `website`
2. 自定义域 `tevarn.com`
3. R2 桶 `tevarn-releases` + 自定义域 `dl.tevarn.com`
4. 发版后跑上传脚本
