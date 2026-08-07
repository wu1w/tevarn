#!/usr/bin/env bash
# Takton VPS Relay — Ubuntu one-line install
#
#   curl -fsSL https://raw.githubusercontent.com/wu1w/takton/feature/agent-kernel/deploy/vps-relay/install.sh | sudo bash
#
# Optional env:
#   RELAY_PUBLIC_PORT=80
#   TAKTON_RELAY_DIR=/opt/takton-vps-relay
#   TAKTON_GITHUB_REPO=wu1w/takton
#   TAKTON_RELAY_REF=feature/agent-kernel   # git branch/tag for raw/archive fallback
#   TAKTON_RELAY_VERSION=v0.5.7-alpha      # GitHub Release tag (preferred package source)
#   TAKTON_RELAY_ZIP_URL=https://...       # override package zip URL
#   RELAY_TOKEN=tr_live_...                # reuse a fixed token (otherwise auto-generated)
#
set -euo pipefail

INSTALL_DIR="${TAKTON_RELAY_DIR:-/opt/takton-vps-relay}"
PUBLIC_PORT="${RELAY_PUBLIC_PORT:-80}"
GITHUB_REPO="${TAKTON_GITHUB_REPO:-wu1w/takton}"
RELAY_REF="${TAKTON_RELAY_REF:-feature/agent-kernel}"
RELAY_VERSION="${TAKTON_RELAY_VERSION:-v0.5.7-alpha}"
INFO_FILE="${INSTALL_DIR}/INSTALL_INFO.txt"

RED='\033[0;31m'; GRN='\033[0;32m'; CYN='\033[0;36m'; YLW='\033[1;33m'; BLD='\033[1m'; NC='\033[0m'
info() { echo -e "${CYN}[takton-relay]${NC} $*"; }
ok()   { echo -e "${GRN}[ok]${NC} $*"; }
warn() { echo -e "${YLW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[err]${NC} $*" >&2; }

need_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    err "请用 root 运行，例如："
    err "  curl -fsSL https://raw.githubusercontent.com/wu1w/takton/${RELAY_REF}/deploy/vps-relay/install.sh | sudo bash"
    exit 1
  fi
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

download() {
  # download <url> <dest>
  local url="$1" dest="$2"
  if have_cmd curl; then
    curl -fsSL --connect-timeout 15 --max-time 180 -o "${dest}" "${url}"
  elif have_cmd wget; then
    wget -q -O "${dest}" "${url}"
  else
    return 1
  fi
}

fetch_text() {
  local url="$1"
  if have_cmd curl; then
    curl -fsSL --connect-timeout 10 --max-time 30 "${url}"
  elif have_cmd wget; then
    wget -q -O - "${url}"
  else
    return 1
  fi
}

install_docker() {
  if have_cmd docker; then
    ok "Docker 已安装: $(docker --version 2>/dev/null | head -n1)"
    return 0
  fi
  info "安装 Docker…"
  if ! have_cmd apt-get; then
    err "未检测到 apt-get。请先手动安装 Docker 后重试。"
    exit 1
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg openssl unzip tar
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  if ! apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin; then
    warn "官方 Docker 源安装失败，尝试 docker.io …"
    apt-get install -y docker.io docker-compose-v2 || apt-get install -y docker.io docker-compose || true
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  if ! have_cmd docker; then
    err "Docker 安装失败"
    exit 1
  fi
  ok "Docker 已安装"
}

resolve_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
  elif have_cmd docker-compose; then
    COMPOSE="docker-compose"
  else
    err "未找到 docker compose 插件。请安装 docker-compose-plugin 后重试。"
    exit 1
  fi
  ok "Compose: ${COMPOSE}"
}

package_ok() {
  local root="$1"
  [[ -f "${root}/docker-compose.yml" && -f "${root}/relay/server.py" && -f "${root}/relay/Dockerfile" ]]
}

copy_package_from() {
  local src="$1"
  mkdir -p "${INSTALL_DIR}/relay"
  cp -a "${src}/docker-compose.yml" "${INSTALL_DIR}/"
  cp -a "${src}/relay/." "${INSTALL_DIR}/relay/"
  [[ -f "${src}/.env.example" ]] && cp -a "${src}/.env.example" "${INSTALL_DIR}/" || true
  [[ -f "${src}/README.md" ]] && cp -a "${src}/README.md" "${INSTALL_DIR}/" || true
}

fetch_package_remote() {
  local tmp zip tarball extracted
  tmp="$(mktemp -d)"
  trap 'rm -rf "'"${tmp}"'"' RETURN

  # 1) Explicit zip URL
  if [[ -n "${TAKTON_RELAY_ZIP_URL:-}" ]]; then
    info "下载包: ${TAKTON_RELAY_ZIP_URL}"
    zip="${tmp}/relay.zip"
    if download "${TAKTON_RELAY_ZIP_URL}" "${zip}"; then
      mkdir -p "${tmp}/unz"
      if unzip -q "${zip}" -d "${tmp}/unz" 2>/dev/null || (have_cmd busybox && busybox unzip -q "${zip}" -d "${tmp}/unz"); then
        extracted="$(find "${tmp}/unz" -type f -name docker-compose.yml | head -n1 | xargs -r dirname)"
        if [[ -n "${extracted}" ]] && package_ok "${extracted}"; then
          copy_package_from "${extracted}"
          ok "已从 TAKTON_RELAY_ZIP_URL 部署包文件"
          return 0
        fi
      fi
    fi
    warn "自定义 ZIP 拉取失败，尝试其他源…"
  fi

  # 2) GitHub Release asset
  local ver_plain="${RELAY_VERSION#v}"
  local candidates=(
    "https://github.com/${GITHUB_REPO}/releases/download/${RELAY_VERSION}/takton-vps-relay-${ver_plain}.zip"
    "https://github.com/${GITHUB_REPO}/releases/download/${RELAY_VERSION}/takton-vps-relay-${RELAY_VERSION}.zip"
    "https://github.com/${GITHUB_REPO}/releases/download/${RELAY_VERSION}/takton-vps-relay-0.5.7-alpha.zip"
  )
  for url in "${candidates[@]}"; do
    info "尝试 Release 包: ${url}"
    zip="${tmp}/relay-release.zip"
    if download "${url}" "${zip}"; then
      mkdir -p "${tmp}/rel"
      rm -rf "${tmp}/rel"/* 2>/dev/null || true
      mkdir -p "${tmp}/rel"
      if unzip -q "${zip}" -d "${tmp}/rel" 2>/dev/null; then
        extracted="$(find "${tmp}/rel" -type f -name docker-compose.yml 2>/dev/null | head -n1 | xargs -r dirname)"
        if [[ -n "${extracted}" ]] && package_ok "${extracted}"; then
          copy_package_from "${extracted}"
          ok "已从 GitHub Release 部署包文件"
          return 0
        fi
      fi
    fi
  done
  warn "Release zip 不可用，改从源码树拉取…"

  # 3) GitHub branch/tag archive (codeload)
  local archive_urls=(
    "https://codeload.github.com/${GITHUB_REPO}/tar.gz/refs/heads/${RELAY_REF}"
    "https://codeload.github.com/${GITHUB_REPO}/tar.gz/refs/tags/${RELAY_VERSION}"
    "https://github.com/${GITHUB_REPO}/archive/refs/heads/${RELAY_REF}.tar.gz"
    "https://github.com/${GITHUB_REPO}/archive/refs/tags/${RELAY_VERSION}.tar.gz"
  )
  for url in "${archive_urls[@]}"; do
    info "尝试源码归档: ${url}"
    tarball="${tmp}/src.tgz"
    if download "${url}" "${tarball}"; then
      mkdir -p "${tmp}/src"
      if tar -xzf "${tarball}" -C "${tmp}/src" 2>/dev/null; then
        extracted="$(find "${tmp}/src" -type f -path '*/deploy/vps-relay/docker-compose.yml' 2>/dev/null | head -n1 | xargs -r dirname)"
        if [[ -n "${extracted}" ]] && package_ok "${extracted}"; then
          copy_package_from "${extracted}"
          ok "已从源码归档部署包文件"
          return 0
        fi
      fi
    fi
  done

  # 4) Raw file-by-file (minimal set)
  info "逐文件从 raw.githubusercontent.com 拉取…"
  local base="https://raw.githubusercontent.com/${GITHUB_REPO}/${RELAY_REF}/deploy/vps-relay"
  mkdir -p "${INSTALL_DIR}/relay"
  local f
  for f in docker-compose.yml .env.example relay/Dockerfile relay/requirements.txt relay/server.py; do
    info "  get ${f}"
    if ! download "${base}/${f}" "${INSTALL_DIR}/${f}"; then
      err "无法下载 ${base}/${f}"
      err "请检查网络 / 仓库可见性，或设置 TAKTON_RELAY_ZIP_URL"
      exit 1
    fi
  done
  if package_ok "${INSTALL_DIR}"; then
    ok "已从 raw 文件部署包"
    return 0
  fi
  err "远程包不完整"
  exit 1
}

ensure_package() {
  # Local mode: script lives next to docker-compose.yml (cloned repo or unzipped release)
  local script_path script_dir
  script_path="${BASH_SOURCE[0]:-}"
  if [[ -n "${script_path}" && -f "${script_path}" ]]; then
    script_dir="$(cd "$(dirname "${script_path}")" 2>/dev/null && pwd || true)"
  else
    script_dir=""
  fi

  if [[ -n "${script_dir}" ]] && package_ok "${script_dir}"; then
    info "使用本地包: ${script_dir}"
    copy_package_from "${script_dir}"
    ok "本地包已复制到 ${INSTALL_DIR}"
    return 0
  fi

  # Already installed tree?
  if package_ok "${INSTALL_DIR}"; then
    info "安装目录已有包文件，复用: ${INSTALL_DIR}"
    return 0
  fi

  info "远程安装模式（curl|bash 或目录无源码）— 自动下载中继包"
  # Ensure unzip/tar/curl for fetch
  if have_cmd apt-get; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y >/dev/null 2>&1 || true
    apt-get install -y ca-certificates curl openssl unzip tar >/dev/null 2>&1 || true
  fi
  fetch_package_remote
}

gen_token() {
  if [[ -n "${RELAY_TOKEN:-}" ]]; then
    echo "${RELAY_TOKEN}"
    return 0
  fi
  if have_cmd openssl; then
    echo "tr_live_$(openssl rand -hex 24)"
    return 0
  fi
  # fallback
  echo "tr_live_$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
}

write_env() {
  local token="$1"
  local env_file="${INSTALL_DIR}/.env"
  cat > "${env_file}" <<EOF
# Generated by install.sh — keep secret
RELAY_TOKEN=${token}
RELAY_PUBLIC_PORT=${PUBLIC_PORT}
LOG_LEVEL=INFO
RELAY_REQUIRE_EDGE_AUTH=1
EOF
  chmod 600 "${env_file}"
}

detect_public_ip() {
  local ip=""
  ip="$(fetch_text https://api.ipify.org 2>/dev/null || true)"
  if [[ -z "${ip}" ]]; then
    ip="$(fetch_text https://ifconfig.me 2>/dev/null || true)"
  fi
  if [[ -z "${ip}" ]]; then
    ip="$(fetch_text https://ipecho.net/plain 2>/dev/null || true)"
  fi
  if [[ -z "${ip}" ]]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  if [[ -z "${ip}" ]]; then
    ip="<YOUR_VPS_PUBLIC_IP>"
  fi
  # trim whitespace
  echo "${ip}" | tr -d '[:space:]'
}

wait_health() {
  local port="$1"
  local i
  for i in $(seq 1 45); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${port}/relay/v1/health" >/dev/null 2>&1; then
      return 0
    fi
    if curl -fsS --max-time 2 "http://127.0.0.1:8080/relay/v1/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

print_credentials() {
  local ip="$1" port="$2" token="$3" health_ok="$4"
  local base_url health_url
  if [[ "${port}" == "80" ]]; then
    base_url="http://${ip}"
  else
    base_url="http://${ip}:${port}"
  fi
  health_url="${base_url}/relay/v1/health"

  # Persist for later `cat INSTALL_INFO.txt`
  cat > "${INFO_FILE}" <<EOF
# Takton VPS Relay — install credentials
# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
HOST=${ip}
PORT=${port}
TOKEN=${token}
BASE_URL=${base_url}
HEALTH_URL=${health_url}
INSTALL_DIR=${INSTALL_DIR}
EOF
  chmod 600 "${INFO_FILE}"

  echo ""
  echo -e "${BLD}============================================================${NC}"
  echo -e "${GRN}${BLD}  Takton VPS 中继已就绪 — 请保存以下信息${NC}"
  echo -e "${BLD}============================================================${NC}"
  echo ""
  echo -e "  ${BLD}IP / Host${NC} :  ${YLW}${ip}${NC}"
  echo -e "  ${BLD}端口 Port${NC} :  ${YLW}${port}${NC}"
  echo -e "  ${BLD}令牌 Token${NC}:  ${YLW}${token}${NC}"
  echo ""
  echo -e "  访问地址  :  ${base_url}"
  echo -e "  健康检查  :  ${health_url}"
  if [[ "${health_ok}" == "1" ]]; then
    echo -e "  健康状态  :  ${GRN}OK${NC}"
  else
    echo -e "  健康状态  :  ${YLW}启动中/未探测到（请检查安全组与 docker logs）${NC}"
  fi
  echo ""
  echo "  云厂商安全组请放行 TCP ${port}"
  echo ""
  echo "  在 PC Takton → 设置 → 远程连接 → 自有 VPS 中继："
  echo "    1) 填入 Host=${ip}  Port=${port}  Token=上面令牌"
  echo "    2) 检测连通 → 启用中继"
  echo "    3) 匹配手机 · 生成二维码"
  echo ""
  echo "  凭据已写入: ${INFO_FILE}"
  echo "  查看日志:   cd ${INSTALL_DIR} && ${COMPOSE} logs -f"
  echo "  再次显示:   sudo cat ${INFO_FILE}"
  echo -e "${BLD}============================================================${NC}"
  echo ""
}

main() {
  need_root
  info "安装目录: ${INSTALL_DIR}"
  mkdir -p "${INSTALL_DIR}"

  install_docker
  resolve_compose
  ensure_package

  if ! package_ok "${INSTALL_DIR}"; then
    err "包文件缺失: 需要 docker-compose.yml + relay/server.py + relay/Dockerfile"
    exit 1
  fi

  # Token: keep existing unless RELAY_TOKEN env overrides
  local token=""
  if [[ -n "${RELAY_TOKEN:-}" ]]; then
    token="${RELAY_TOKEN}"
    info "使用环境变量 RELAY_TOKEN"
  elif [[ -f "${INSTALL_DIR}/.env" ]] && grep -qE '^RELAY_TOKEN=.+' "${INSTALL_DIR}/.env"; then
    # shellcheck disable=SC1090
    set -a
    # shellcheck disable=SC1091
    source "${INSTALL_DIR}/.env"
    set +a
    token="${RELAY_TOKEN}"
    info "沿用已有 .env 中的 RELAY_TOKEN"
  else
    token="$(gen_token)"
    ok "已生成新 Token"
  fi
  write_env "${token}"

  # Firewall
  if have_cmd ufw; then
    ufw allow "${PUBLIC_PORT}/tcp" >/dev/null 2>&1 || true
  fi

  # Start
  cd "${INSTALL_DIR}"
  info "构建并启动 takton-relay（首次可能需几分钟）…"
  ${COMPOSE} up -d --build

  local health_ok=0
  if wait_health "${PUBLIC_PORT}"; then
    health_ok=1
    ok "健康检查通过"
  else
    warn "健康检查超时，容器可能仍在启动。请执行: cd ${INSTALL_DIR} && ${COMPOSE} logs --tail=100"
  fi

  local public_ip
  public_ip="$(detect_public_ip)"
  print_credentials "${public_ip}" "${PUBLIC_PORT}" "${token}" "${health_ok}"
}

main "$@"
