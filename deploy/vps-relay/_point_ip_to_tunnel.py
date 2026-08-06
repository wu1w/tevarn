"""Point VPS IP-based nginx vhosts (150.158.109.231) to tunnel :7080."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = "150.158.109.231"
PASSWORD = os.environ.get("TAKTON_VPS_PASSWORD", "")

CONF_HTTP = f"""
server {{
    listen 80;
    server_name {HOST};

    location / {{
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
    }}
}}
"""


def main() -> None:
    if not PASSWORD:
        sys.exit("need TAKTON_VPS_PASSWORD")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        HOST,
        username="ubuntu",
        password=PASSWORD,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    sftp = c.open_sftp()
    with sftp.file("/tmp/ip-http-test.conf", "w") as f:
        f.write(CONF_HTTP)
    sftp.close()

    cmds = [
        "sudo cp /etc/nginx/sites-available/ip-http-test /etc/nginx/sites-available/ip-http-test.bak.takton || true",
        "sudo mv /tmp/ip-http-test.conf /etc/nginx/sites-available/ip-http-test",
        # keep SSL vhost as-is (443); only HTTP IP path for DEV
        "sudo nginx -t && sudo systemctl reload nginx",
        f"curl -sS -m 8 -H 'Host: {HOST}' http://127.0.0.1/api/health",
    ]
    for cmd in cmds:
        print("$", cmd)
        _, o, e = c.exec_command(cmd)
        print(o.read().decode("utf-8", errors="replace"))
        err = e.read().decode("utf-8", errors="replace")
        if err.strip():
            print(err)
    c.close()
    print(f"TRY http://{HOST}/api/health")


if __name__ == "__main__":
    main()
