"""Deploy frps to VPS and print connection info. Token via env TEVARN_RELAY_TOKEN."""
from __future__ import annotations

import os
import secrets
import sys
import time
from pathlib import Path

import paramiko

HOST = os.environ.get("TEVARN_VPS_HOST", "150.158.109.231")
USER = os.environ.get("TEVARN_VPS_USER", "ubuntu")
PASSWORD = os.environ.get("TEVARN_VPS_PASSWORD", "")
REMOTE_DIR = "/opt/tevarn-vps-relay"
HERE = Path(__file__).resolve().parent

if not PASSWORD:
    print("Set TEVARN_VPS_PASSWORD", file=sys.stderr)
    sys.exit(1)

token = os.environ.get("TEVARN_RELAY_TOKEN") or ("tr_" + secrets.token_urlsafe(24))


def run(client: paramiko.SSHClient, cmd: str, check: bool = True) -> str:
    print(f"$ {cmd}")
    _, stdout, stderr = client.exec_command(cmd, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if out.strip():
        print(out)
    if err.strip():
        print(err, file=sys.stderr)
    if check and code != 0:
        raise RuntimeError(f"cmd failed ({code}): {cmd}")
    return out


def main() -> None:
    frps = (HERE / "frps.toml").read_text(encoding="utf-8")
    frps = frps.replace("TEVARN_RELAY_TOKEN_PLACEHOLDER", token)
    compose = (HERE / "docker-compose.yml").read_text(encoding="utf-8")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username=USER,
        password=PASSWORD,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    sftp = client.open_sftp()
    run(client, f"sudo mkdir -p {REMOTE_DIR} && sudo chown {USER}:{USER} {REMOTE_DIR}")
    with sftp.file(f"{REMOTE_DIR}/frps.toml", "w") as f:
        f.write(frps)
    with sftp.file(f"{REMOTE_DIR}/docker-compose.yml", "w") as f:
        f.write(compose)
    sftp.close()

    # firewall if ufw active
    run(
        client,
        "sudo ufw status 2>/dev/null | head -5 || true",
        check=False,
    )
    run(
        client,
        "if sudo ufw status 2>/dev/null | grep -qi active; then "
        "sudo ufw allow 7000/tcp && sudo ufw allow 7080/tcp; fi",
        check=False,
    )

    run(
        client,
        f"cd {REMOTE_DIR} && docker compose pull && docker compose up -d",
    )
    time.sleep(2)
    run(client, "docker ps --filter name=tevarn-frps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'")
    run(client, "docker logs tevarn-frps 2>&1 | tail -20", check=False)

    # write local frpc config (not committed)
    local_frpc = HERE / "frpc.local.toml"
    local_frpc.write_text(
        f"""serverAddr = "{HOST}"
serverPort = 7000
auth.method = "token"
auth.token = "{token}"

[[proxies]]
name = "tevarn-backend"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8090
remotePort = 7080
""",
        encoding="utf-8",
    )
    # token file for local scripts
    (HERE / ".relay-token").write_text(token, encoding="utf-8")
    try:
        os.chmod(HERE / ".relay-token", 0o600)
    except OSError:
        pass

    print("\n=== RELAY READY ===")
    print(f"VPS:        {HOST}")
    print(f"Control:    {HOST}:7000")
    print(f"Public URL: http://{HOST}:7080")
    print(f"Token:      {token}")
    print(f"Local frpc: {local_frpc}")
    print("===================\n")
    client.close()


if __name__ == "__main__":
    main()
