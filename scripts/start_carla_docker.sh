#!/usr/bin/env bash
# scripts/start_carla_docker.sh
# ─────────────────────────────────────────────────────────────────────────────
# Start a CARLA server in Docker. Works on macOS and Linux.
#
# Environment variables:
#   CARLA_DOCKER_IMAGE   Override the Docker image (default: carlasim/carla:0.9.15)
#   CARLA_PORT           CARLA server port exposed to the host (default: 2000)
#   DOCKER_EXTRA_ARGS    Additional docker run flags (e.g. "--gpus all")
#
# Usage:
#   bash scripts/start_carla_docker.sh
#   CARLA_DOCKER_IMAGE=carlasim/carla:0.9.14 bash scripts/start_carla_docker.sh
#   CARLA_PORT=3000 bash scripts/start_carla_docker.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────
BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
DIM="\033[2m"
RESET="\033[0m"

info()  { echo -e "  ${BOLD}→${RESET} $*"; }
ok()    { echo -e "  ${GREEN}[ OK ]${RESET} $*"; }
warn()  { echo -e "  ${YELLOW}[WARN]${RESET} $*"; }
fail()  { echo -e "  ${RED}[FAIL]${RESET} $*" >&2; }

echo ""
echo -e "${BOLD}────────────────────────────────────────────────────────────────${RESET}"
echo -e "${BOLD}  CARLA Foundation Driving Demo — Docker Launcher${RESET}"
echo -e "${BOLD}────────────────────────────────────────────────────────────────${RESET}"
echo ""

# ── Resolve configuration ──────────────────────────────────────────────────────
IMAGE="${CARLA_DOCKER_IMAGE:-carlasim/carla:0.9.15}"
PORT="${CARLA_PORT:-2000}"
EXTRA="${DOCKER_EXTRA_ARGS:-}"

# Calculate port range: CARLA uses port, port+1, port+2
PORT_END=$((PORT + 2))
PORT_RANGE="${PORT}-${PORT_END}:${PORT}-${PORT_END}"

info "Image : ${IMAGE}"
info "Ports : ${PORT_RANGE} (CARLA, streaming, secondary)"

# ── Check Docker CLI ──────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    fail "Docker is not installed or not in PATH."
    echo ""
    echo "  Install Docker Desktop: https://docs.docker.com/get-docker/"
    exit 1
fi
ok "Docker CLI found: $(docker --version | head -1)"

# ── Check Docker daemon ────────────────────────────────────────────────────────
if ! docker info &>/dev/null 2>&1; then
    fail "Docker daemon is not running."
    echo ""
    echo "  • macOS/Windows: Start Docker Desktop"
    echo "  • Linux:         sudo systemctl start docker"
    exit 1
fi
ok "Docker daemon is running"

# ── Apple Silicon warning ──────────────────────────────────────────────────────
ARCH=$(uname -m)
OS=$(uname -s)

if [[ "$OS" == "Darwin" && "$ARCH" == "arm64" ]]; then
    echo ""
    warn "Apple Silicon detected (arm64 / M-series Mac)"
    echo ""
    echo -e "  ${DIM}The carlasim/carla image is built for linux/amd64 only.${RESET}"
    echo -e "  ${DIM}On Apple Silicon it runs under Rosetta 2 emulation.${RESET}"
    echo ""
    echo "  Known limitations in emulation mode:"
    echo "    • No OpenGL / GPU passthrough (software rendering only)"
    echo "    • Reduced performance — 20 Hz ticks may not be achievable"
    echo "    • Occasional crash under memory pressure"
    echo ""
    echo "  This is suitable for:"
    echo "    ✓  Connectivity testing and Phase 1 smoke test"
    echo "    ✗  Data collection (use remote Linux GPU server instead)"
    echo ""
    echo "  To connect to a remote CARLA server instead:"
    echo "    CARLA_HOST=<server-ip> make smoke"
    echo ""

    # Add --platform flag for explicit emulation mode
    EXTRA="--platform linux/amd64 ${EXTRA}"
fi

# ── Build docker run command ──────────────────────────────────────────────────
CMD=(
    docker run
    --rm
    --detach
    --name "carla-server"
    -p "${PORT_RANGE}"
)

# Add extra args if provided
if [[ -n "${EXTRA// }" ]]; then
    # shellcheck disable=SC2206
    CMD+=($EXTRA)
fi

CMD+=(
    "${IMAGE}"
    /bin/bash -c
    "/home/carla/CarlaUE4.sh -RenderOffScreen -nosound -carla-port=${PORT} 2>&1"
)

# ── Print command for transparency ───────────────────────────────────────────
echo ""
info "Running command:"
echo ""
echo -e "  ${DIM}${CMD[*]}${RESET}"
echo ""

# ── Check if a container named carla-server is already running ───────────────
if docker ps --format '{{.Names}}' | grep -q '^carla-server$'; then
    warn "A container named 'carla-server' is already running."
    warn "Stop it first: docker stop carla-server"
    exit 1
fi

# ── Launch ────────────────────────────────────────────────────────────────────
CONTAINER_ID=$("${CMD[@]}")

echo ""
ok "CARLA container started: ${CONTAINER_ID:0:12}"
echo ""
echo "  Allow 15–30s for CARLA to initialise, then:"
echo ""
echo -e "  ${BOLD}CARLA_HOST=127.0.0.1 CARLA_PORT=${PORT} make smoke${RESET}"
echo ""
echo "  To stop CARLA:"
echo "    docker stop carla-server"
echo ""
echo "  To view logs:"
echo "    docker logs -f carla-server"
echo ""
