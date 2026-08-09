"""Point existing tevarn-relay nginx default_server to SSH tunnel port 7080."""
from __future__ import annotations

import os
import sys
import time

import paramiko

HOST = os.environ.get("TEVARN_VPS_HOST", "150.158.109.231")
USER = os.environ.get("TEVARN_VPS_USER", "ubuntu")
PASSWORD = os.environ.get("TEVARN_VPS_PASSWORD", "")

CONF = """
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # Tevarn DEV: SSH reverse tunnel PC:8090 -> VPS:7080
    location / {
        proxy_pass http://127.0.0.1:7080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
        client_max_body_size 64m;
    }
}
"""


def main() -> None:
    if not PASSWORD:
        print("Set TEVARN_VPS_PASSWORD", file=sys.stderr)
        sys.exit(1)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        HOST,
        username=USER,
        password=PASSWORD,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    sftp = c.open_sftp()
    with sftp.file("/tmp/tevarn-relay.conf", "w") as f:
        f.write(CONF)
    sftp.close()

    cmds = [
        "sudo cp /etc/nginx/sites-available/tevarn-relay /etc/nginx/sites-available/tevarn-relay.bak.dev || true",
        "sudo mv /tmp/tevarn-relay.conf /etc/nginx/sites-available/tevarn-relay",
        "sudo nginx -t && sudo systemctl reload nginx",
        "curl -sS -m 8 http://127.0.0.1/api/health",
        "curl -sS -m 8 http://127.0.0.1:7080/api/health",
    ]
    for cmd in cmds:
        print("$", cmd)
        _, o, e = c.exec_command(cmd)
        print(o.read().decode("utf-8", errors="replace"))
        err = e.read().decode("utf-8", errors="replace")
        if err.strip():
            print(err)
        time.sleep(0.2)
    c.close()
    print(f"PUBLIC_HEALTH=http://{HOST}/api/health")


if __name__ == "__main__":
    main()
