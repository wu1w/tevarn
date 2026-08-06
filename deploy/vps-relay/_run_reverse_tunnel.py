"""PC-side reverse tunnel: VPS:7080 -> local 127.0.0.1:8090 via SSH -R."""
from __future__ import annotations

import os
import select
import socket
import sys
import threading
import time

import paramiko

HOST = os.environ.get("TAKTON_VPS_HOST", "150.158.109.231")
USER = os.environ.get("TAKTON_VPS_USER", "ubuntu")
PASSWORD = os.environ.get("TAKTON_VPS_PASSWORD", "")
REMOTE_PORT = int(os.environ.get("TAKTON_RELAY_PUBLIC_PORT", "7080"))
LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = int(os.environ.get("TAKTON_APP_PORT", "8090"))

if not PASSWORD:
    print("Set TAKTON_VPS_PASSWORD", file=sys.stderr)
    sys.exit(1)


def _handler(chan: paramiko.Channel, host: str, port: int) -> None:
    sock = socket.socket()
    try:
        sock.connect((host, port))
    except Exception as e:
        print(f"local connect failed {host}:{port}: {e}")
        chan.close()
        return
    while True:
        r, _, _ = select.select([sock, chan], [], [], 60)
        if not r:
            continue
        if sock in r:
            data = sock.recv(65536)
            if not data:
                break
            chan.sendall(data)
        if chan in r:
            data = chan.recv(65536)
            if not data:
                break
            sock.sendall(data)
    chan.close()
    sock.close()


def main() -> None:
    while True:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            print(f"connecting {USER}@{HOST} ...")
            client.connect(
                HOST,
                username=USER,
                password=PASSWORD,
                timeout=30,
                allow_agent=False,
                look_for_keys=False,
            )
            transport = client.get_transport()
            assert transport is not None
            # request remote forward: 0.0.0.0:REMOTE_PORT -> forwarded to us
            transport.request_port_forward("0.0.0.0", REMOTE_PORT)
            print(
                f"READY reverse tunnel: http://{HOST}:{REMOTE_PORT} -> {LOCAL_HOST}:{LOCAL_PORT}"
            )
            while True:
                chan = transport.accept(timeout=60)
                if chan is None:
                    # keep alive
                    if not transport.is_active():
                        raise RuntimeError("transport dead")
                    continue
                t = threading.Thread(
                    target=_handler, args=(chan, LOCAL_HOST, LOCAL_PORT), daemon=True
                )
                t.start()
        except Exception as e:
            print(f"tunnel error: {e}; retry in 5s")
            try:
                client.close()
            except Exception:
                pass
            time.sleep(5)


if __name__ == "__main__":
    main()
