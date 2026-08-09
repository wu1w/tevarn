# 官网镜像（VPS 托管，配合 Cloudflare 灰云）

大陆访问差时，把静态站放到香港/日本 VPS，Cloudflare 只做 DNS（灰云），不经过 CF 代理。

## 快速挂载（nginx）

```bash
sudo mkdir -p /var/www/tevarn
# 将仓库 website/ 同步到 /var/www/tevarn
sudo rsync -a ./website/ /var/www/tevarn/
```

nginx server：

```nginx
server {
  listen 80;
  server_name tevarn.com www.tevarn.com;
  root /var/www/tevarn;
  index index.html;
  location / {
    try_files $uri $uri/ /index.html;
  }
  location ~* \.(exe|apk|sh)$ {
    add_header Content-Disposition "attachment";
  }
}
```

然后 Cloudflare DNS：

- 类型 A，名称 `@`，内容 `VPS_IP`，代理状态 **仅 DNS（灰云）**
- `www` CNAME 到 `@` 或同样 A 记录，灰云

HTTPS：在 VPS 上 `certbot --nginx -d tevarn.com -d www.tevarn.com`。
