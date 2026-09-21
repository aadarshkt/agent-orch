#!/usr/bin/env bash
# =============================================================================
# dev.sh — Local development startup script for agent-orch
#
# What this does:
#   1. Ensures PostgreSQL is running (via Docker on port 5433)
#   2. Waits for Postgres to be healthy
#   3. Installs Python dependencies (venv)
#   4. Starts the FastAPI backend (uvicorn) — DB init + seeding happens on startup
#   5. Installs Node dependencies and starts the Next.js frontend
#
# Usage:
#   chmod +x dev.sh
#   ./dev.sh
#
# Requirements:
#   - Docker (for Postgres)            OR  a local Postgres on port 5433
#   - Python 3.10+
#   - Node.js 18+
# =============================================================================

set -euo pipefail

# ─── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()    { echo -e "${CYAN}${BOLD}[dev]${NC} $*"; }
ok()     { echo -e "${GREEN}${BOLD}[✓]${NC} $*"; }
warn()   { echo -e "${YELLOW}${BOLD}[!]${NC} $*"; }
err()    { echo -e "${RED}${BOLD}[✗]${NC} $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

# ─── Config (mirrors backend/.env) ────────────────────────────────────────────
PG_CONTAINER="agent-orch-postgres"
PG_PORT=5433
PG_USER="postgres"
PG_PASSWORD="postgres"
PG_DB="postgres"
BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_PORT="${FRONTEND_PORT:-3001}"


# ─── Cleanup on exit ──────────────────────────────────────────────────────────
BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo ""
  log "Shutting down services…"
  [[ -n "$BACKEND_PID" ]]  && kill "$BACKEND_PID"  2>/dev/null && ok "Backend stopped"
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null && ok "Frontend stopped"
  exit 0
}

trap cleanup SIGINT SIGTERM

# =============================================================================
# 1. PostgreSQL via Docker
# =============================================================================
start_postgres() {
  log "Checking PostgreSQL…"

  if ! command -v docker &>/dev/null; then
    warn "Docker not found — assuming a local Postgres is already running on port $PG_PORT."
    return
  fi

  # If container already running, leave it alone
  if docker ps --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$"; then
    ok "Postgres container '${PG_CONTAINER}' is already running."
    return
  fi

  # If container exists but is stopped, start it
  if docker ps -a --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$"; then
    log "Starting existing Postgres container '${PG_CONTAINER}'…"
    docker start "$PG_CONTAINER" >/dev/null
  else
    log "Creating and starting Postgres container '${PG_CONTAINER}'…"
    docker run -d \
      --name "$PG_CONTAINER" \
      -e POSTGRES_USER="$PG_USER" \
      -e POSTGRES_PASSWORD="$PG_PASSWORD" \
      -e POSTGRES_DB="$PG_DB" \
      -p "${PG_PORT}:5432" \
      postgres:16-alpine \
      >/dev/null
  fi

  ok "Postgres container started."
}

wait_for_postgres() {
  log "Waiting for Postgres to accept connections on port ${PG_PORT}…"
  local retries=30
  local i=0

  # Try pg_isready locally first, then fall back to running it inside the container
  until pg_isready -h localhost -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" &>/dev/null 2>&1 \
     || docker exec "$PG_CONTAINER" pg_isready -U "$PG_USER" &>/dev/null 2>&1; do
    i=$((i + 1))
    if [[ $i -ge $retries ]]; then
      err "Postgres did not become ready in time. Exiting."
      exit 1
    fi
    echo -n "."
    sleep 1
  done

  echo ""
  ok "Postgres is ready."
}

# =============================================================================
# 2. Python venv + dependencies
# =============================================================================
setup_python() {
  log "Setting up Python environment…"

  cd "$BACKEND_DIR"

  if [[ ! -d "venv" ]]; then
    log "Creating virtual environment…"
    python3 -m venv venv
  fi

  # shellcheck source=/dev/null
  source venv/bin/activate

  log "Installing Python dependencies…"
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt

  ok "Python dependencies installed."
  deactivate || true
}

# =============================================================================
# 3. FastAPI backend  (DB init + seed happen inside the app lifespan)
# =============================================================================
start_backend() {
  log "Starting FastAPI backend on http://localhost:${BACKEND_PORT} …"

  cd "$BACKEND_DIR"

  # Ensure .env exists
  if [[ ! -f ".env" ]]; then
    cp .env.example .env
    warn "Created .env from .env.example — edit it if needed."
  fi

  source venv/bin/activate

  # PYTHONPATH so 'src.*' imports resolve from backend/
  PYTHONPATH="$BACKEND_DIR" uvicorn src.api.main:app \
    --host 0.0.0.0 \
    --port "$BACKEND_PORT" \
    --reload \
    --reload-dir "$BACKEND_DIR/src" &
  BACKEND_PID=$!

  deactivate || true
  ok "Backend started (PID $BACKEND_PID)."
}

# =============================================================================
# 4. Next.js frontend
# =============================================================================
setup_node() {
  log "Checking Node.js environment…"

  if ! command -v node &>/dev/null; then
    err "Node.js is not installed! Please install Node.js (version 18+ recommended)."
    exit 1
  fi

  local node_version
  node_version=$(node -v)
  local major_version
  major_version=$(echo "$node_version" | sed -E 's/^v([0-9]+).*/\1/')

  if [[ "$major_version" -lt 18 ]]; then
    warn "Detected Node.js $node_version. Next.js 15+ works best with Node.js 18.18+ or 20+."
  else
    ok "Node.js version $node_version detected."
  fi

  cd "$FRONTEND_DIR"

  if [[ ! -d "node_modules" ]]; then
    log "node_modules not found. Running npm install…"
    npm install
    ok "Node dependencies installed."
  else
    # Check if native bindings (e.g., lightningcss or turbopack) match current platform
    if ! node -e "try { require('lightningcss'); } catch(e) { process.exit(1); }" &>/dev/null; then
      warn "Native bindings mismatch detected (common when moving project across OS/architectures)."
      log "Running npm install to download platform-specific binaries…"
      npm install
    else
      ok "node_modules present and verified."
    fi
  fi
}

start_frontend() {
  log "Starting Next.js frontend on http://localhost:${FRONTEND_PORT} …"
  cd "$FRONTEND_DIR"

  PORT="$FRONTEND_PORT" NEXT_PUBLIC_API_BASE="http://localhost:${BACKEND_PORT}" npm run dev -- -p "$FRONTEND_PORT" &
  FRONTEND_PID=$!

  ok "Frontend started (PID $FRONTEND_PID)."
}

# =============================================================================
# Main
# =============================================================================
main() {
  echo ""
  echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
  echo -e "${BOLD}║       agent-orch  dev  startup       ║${NC}"
  echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"
  echo ""

  start_postgres
  wait_for_postgres
  setup_python
  start_backend
  setup_node
  start_frontend

  echo ""
  echo -e "${GREEN}${BOLD}All services are up!${NC}"
  echo -e "  ${CYAN}API${NC}      → http://localhost:${BACKEND_PORT}"
  echo -e "  ${CYAN}API Docs${NC} → http://localhost:${BACKEND_PORT}/docs"
  echo -e "  ${CYAN}Frontend${NC} → http://localhost:${FRONTEND_PORT}"
  echo ""
  echo -e "Press ${BOLD}Ctrl+C${NC} to stop everything."
  echo ""

  # Keep script alive until Ctrl-C
  wait
}

main
