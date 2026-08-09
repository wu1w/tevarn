"""Configure VPS for SSH reverse tunnel (GatewayPorts) and open 7080."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("TEVARN_VPS_HOST", "150.158.109.231")
USER = os.environ.get("TEVARN_VPS_USER", "ubuntu")
PASSWORD = os.environ.get("TEVARN_VPS_PASSWORD", "")

if not PASSWORD:
    print("Set TEVARN_VPS_PASSWORD", file=sys.stderr)
    sys.exit(1)


def main() -> None:
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

    cmds = [
        # allow remote bind on non-loopback for -R
        "sudo sed -i 's/^#\\?GatewayPorts.*/GatewayPorts clientspecified/' /etc/ssh/sshd_config",
        "grep -q '^GatewayPorts' /etc/ssh/sshd_config || echo 'GatewayPorts clientspecified' | sudo tee -a /etc/ssh/sshd_config",
        "sudo systemctl reload sshd || sudo service ssh reload || true",
        # optional: stop frps if port conflict later
        "docker ps --filter name=tevarn-frps --format '{{.Names}} {{.Status}}' || true",
        "ss -lntp | grep -E ':7000|:7080' || true",
    ]
    for cmd in cmds:
        print("$", cmd)
        _, out, err = c.exec_command(cmd)
        print(out.read().decode("utf-8", errors="replace"))
        e = err.read().decode("utf-8", errors="replace")
        if e.strip():
            print(e)
    c.close()
    print("SSH_TUNNEL_SERVER_READY")
    print(f"On PC run reverse tunnel to expose local :8090 as http://{HOST}:7080")


if __name__ == "__main__":
    main()
