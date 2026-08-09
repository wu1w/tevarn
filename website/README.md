# Tevarn 官网落地页

**冷白机身 · Light Console** 风格的产品官网单页。

## 品牌色

- 电光紫 `#6D5DF6` / `#8B7CFF`
- 霓虹青 `#00A8C0` / `#22D8EE`

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

页面 `#download` 提供两个安装包：

| 包 | 官网直链（主） | 备用 |
|----|----------------|------|
| Windows Setup | `https://dl.tevarn.com/vX.Y.Z/Tevarn-Setup-…exe` | GitHub Release |
| Android APK | `https://dl.tevarn.com/vX.Y.Z/Tevarn-Mobile-….apk` | GitHub Release |

直链托管在 **Cloudflare R2**（与域名同账号，**无需 ICP 备案**），避免大陆用户依赖 GitHub。

发版与上传步骤见：[`docs/WEBSITE_DOWNLOADS.md`](../docs/WEBSITE_DOWNLOADS.md)

上传脚本：[`scripts/publish-downloads-r2.ps1`](../scripts/publish-downloads-r2.ps1)

改版本时只需编辑 `index.html` 底部 `RELEASE` 配置对象。

## 部署建议

1. Cloudflare Pages 绑定仓库，Root = `website`
2. 自定义域 `tevarn.com`
3. R2 桶 `tevarn-releases` + 自定义域 `dl.tevarn.com`
4. 发版后跑上传脚本
