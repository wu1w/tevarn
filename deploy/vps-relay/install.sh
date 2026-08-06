#!/usr/bin/env bash
# Takton VPS Relay — one-click install (Ubuntu 22.04+)
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/deploy/vps-relay/install.sh | sudo bash
# Or from a cloned repo:
#   sudo bash deploy/vps-relay/install.sh
set -euo pipefail

INSTALL_DIR="${TAKTON_RELAY_DIR:-/opt/takton-vps-relay}"
PUBLIC_PORT="${RELAY_PUBLIC_PORT:-80}"
REPO_RAW="${TAKTON_RELAY_RAW:-}"

RED='\033[0;31m'; GRN='\033[0;32m'; CYN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYN}[takton-relay]${NC} $*"; }
ok()   { echo -e "${GRN}[ok]${NC} $*"; }
err()  { echo -e "${RED}[err]${NC} $*" >&2; }

if [[ "${EUID}" -ne 0 ]]; then
  err "请用 root 运行：sudo bash install.sh"
  exit 1
fi

info "安装目录: ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"

# ── Docker ──────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  info "安装 Docker…"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      chmod a+r /etc/apt/keyrings/docker.gpg
    fi
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin || \
      apt-get install -y docker.io docker-compose-v2 || true
  else
    err "请先手动安装 Docker，然后重试"
    exit 1
  fi
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  err "未找到 docker compose"
  exit 1
fi
ok "Docker 就绪"

# ── Copy / fetch package files ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/docker-compose.yml" && -d "${SCRIPT_DIR}/relay" ]]; then
  info "从本地包复制文件…"
  cp -a "${SCRIPT_DIR}/docker-compose.yml" "${INSTALL_DIR}/"
  cp -a "${SCRIPT_DIR}/relay" "${INSTALL_DIR}/"
  [[ -f "${SCRIPT_DIR}/.env.example" ]] && cp -a "${SCRIPT_DIR}/.env.example" "${INSTALL_DIR}/"
else
  err "未找到本地 relay 包。请在仓库 deploy/vps-relay 目录执行，或先 git clone。"
  err "示例: git clone <repo> && sudo bash deploy/vps-relay/install.sh"
  exit 1
fi

# ── Token ───────────────────────────────────────────────────────────────────
ENV_FILE="${INSTALL_DIR}/.env"
if [[ -f "${ENV_FILE}" ]] && grep -q '^RELAY_TOKEN=.\+' "${ENV_FILE}"; then
  # shellcheck disable=SC1090
  set -a; source "${ENV_FILE}"; set +a
  TOKEN="${RELAY_TOKEN}"
  info "沿用已有 RELAY_TOKEN"
else
  TOKEN="tr_live_$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | xxd -p)"
  cat > "${ENV_FILE}" <<EOF
RELAY_TOKEN=${TOKEN}
RELAY_PUBLIC_PORT=${PUBLIC_PORT}
LOG_LEVEL=INFO
EOF
  chmod 600 "${ENV_FILE}"
  ok "已生成新 Token"
fi

# ── Firewall hint ───────────────────────────────────────────────────────────
if command -v ufw >/dev/null 2>&1; then
  ufw allow "${PUBLIC_PORT}/tcp" >/dev/null 2>&1 || true
fi

# ── Start ───────────────────────────────────────────────────────────────────
cd "${INSTALL_DIR}"
info "构建并启动 takton-relay…"
${COMPOSE} up -d --build

# wait health
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PUBLIC_PORT}/relay/v1/health" >/dev/null 2>&1 \
    || curl -fsS "http://127.0.0.1:8080/relay/v1/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

PUBLIC_IP="$(curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null \
  || curl -fsS --max-time 3 https://ifconfig.me 2>/dev/null \
  || hostname -I 2>/dev/null | awk '{print $1}' \
  || echo '<VPS公网IP>')"

echo ""
echo "============================================================"
ok "Takton VPS 中继已安装"
echo "============================================================"
echo ""
echo "  VPS 地址 (Host):  ${PUBLIC_IP}"
echo "  端口 (Port):      ${PUBLIC_PORT}"
echo "  访问令牌 (Token): ${TOKEN}"
echo ""
echo "  健康检查:  http://${PUBLIC_IP}:${PUBLIC_PORT}/relay/v1/health"
echo ""
echo "请到 PC Takton → 设置 → 远程连接 → 自有 VPS 中继："
echo "  1) 粘贴上面的 地址 + 令牌"
echo "  2) 点「检测连通」→「启用中继」"
echo "  3) 再点「匹配手机 · 生成二维码」扫码即可"
echo ""
echo "文件位置: ${INSTALL_DIR}"
echo "查看日志: cd ${INSTALL_DIR} && ${COMPOSE} logs -f"
echo "============================================================"
