# 大陆访问官网（无需 ICP 备案）

## 为什么手机开着流量/家宽打不开 tevarn.com？

`tevarn.com` 目前托管在 **Cloudflare**（橙云代理）。  
大陆部分运营商对 Cloudflare 任播 IP / SNI 不稳定，**无 VPN 时常见超时或打不开**。  
这和 DNS 有没有记录无关——解析得到的是 CF 节点，链路被干扰。

未备案域名**不能**合法接入国内 CDN/服务器 80/443；所以不能把 `tevarn.com` 直接指到国内机房。

---

## 方案 A — 国内镜像站（立刻可用，推荐先用这个）

静态页走 **jsDelivr 国内镜像**（已备案的公共 CDN 域名，无需你备案）：

### 官网（大陆）

```text
https://cdn.jsdmirror.com/gh/wu1w/tevarn@main/website/
```

请用**带末尾斜杠**的地址（返回 `text/html`，可直接浏览）。

备用：

```text
https://fastly.jsdelivr.net/gh/wu1w/tevarn@main/website/
```

### 页面行为

打开上述镜像后，站点会自动识别为国内镜像：

| 资源 | 国内镜像页行为 |
|------|----------------|
| Windows / Android 安装包 | 走 `ghfast.top` 加速前缀 |
| VPS 一键脚本 | `cdn.jsdmirror.com/.../install-vps-relay.sh` |

### VPS 一键（大陆机器上执行）

```bash
curl -fsSL https://cdn.jsdmirror.com/gh/wu1w/tevarn@main/website/install-vps-relay.sh | sudo bash
```

---

## 方案 B — 更稳：阿里云 OSS 静态网站（免费额度，默认域名）

适合想长期固定一个「自己的」国内 URL、少依赖公共镜像的情况。

1. 开通阿里云 OSS，建桶（如 `tevarn-web`），区域选 **华东/华南**
2. 开启 **静态网站**，默认首页 `index.html`
3. 读写权限：公共读
4. 本机上传：

```powershell
# 需安装 ossutil 并配置 AK
ossutil cp -r website/ oss://tevarn-web/ --update
```

或用仓库脚本（配置环境变量后）：

```powershell
$env:OSS_BUCKET = "tevarn-web"
$env:OSS_ENDPOINT = "oss-cn-hangzhou.aliyuncs.com"
$env:ALIYUN_ACCESS_KEY_ID = "****"
$env:ALIYUN_ACCESS_KEY_SECRET = "****"
.\scripts\deploy-china-oss.ps1
```

访问形如：

```text
https://tevarn-web.oss-cn-hangzhou.aliyuncs.com/index.html
```

> 自定义域名 `cn.tevarn.com` 指到国内 OSS **需要备案**。默认 `*.aliyuncs.com` **不需要**。

---

## 方案 C — 最稳国际域名：VPS + Cloudflare「仅 DNS」

若你有香港 / 日本 / 新加坡 VPS：

1. 用 nginx 托管 `website/` + 安装包  
2. Cloudflare 控制台把 `tevarn.com` 记录改为 **DNS only（灰云）**，A 记录指向 VPS  
3. 在 VPS 上申请 Let's Encrypt  

流量**不再进 Cloudflare 代理**，大陆可达性通常明显好于橙云。  
中继 VPS 也可以顺带挂静态站（见 `deploy/site-mirror/`）。

---

## 产品里怎么写入口

| 场景 | 建议写的链接 |
|------|----------------|
| README / 微信 / 应用内 | 国内镜像 `https://cdn.jsdmirror.com/gh/wu1w/tevarn@main/website/` |
| 海外 / 已有代理 | `https://tevarn.com` |

不要只写 tevarn.com——大陆用户会以为产品挂了。

---

## 发版注意

- 改 `website/` 后 push `main`，jsdmirror 可能有数分钟缓存；紧急可用 commit SHA：  
  `https://cdn.jsdmirror.com/gh/wu1w/tevarn@<sha>/website/`
- 安装包仍在 GitHub Release；国内页通过加速前缀拉取。若加速源失效，换 `docs` 里备用前缀或改走 OSS。
