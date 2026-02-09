#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="$(cd -- "${BACKEND_DIR}/.." && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"

PYTHON_BIN="${PYTHON_BIN:-python3.11}"

log() {
  printf "\n==> %s\n" "$1"
}

die() {
  printf "\nERROR: %s\n" "$1" >&2
  exit 1
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

cleanup_tracked_artifacts() {
  local did_something=0

  if git -C "${BACKEND_DIR}" ls-files venv | head -n 1 | grep -q .; then
    log "Removing tracked backend/venv from Git index (one-time cleanup)"
    git -C "${BACKEND_DIR}" rm -r --cached -f venv >/dev/null
    did_something=1
  fi

  if git -C "${BACKEND_DIR}" ls-files __pycache__ | head -n 1 | grep -q .; then
    log "Removing tracked backend/__pycache__ from Git index (one-time cleanup)"
    git -C "${BACKEND_DIR}" rm -r --cached -f __pycache__ >/dev/null
    did_something=1
  fi

  for f in output.log server.log; do
    if git -C "${BACKEND_DIR}" ls-files --error-unmatch "${f}" >/dev/null 2>&1; then
      log "Removing tracked backend/${f} from Git index (one-time cleanup)"
      git -C "${BACKEND_DIR}" rm --cached -f "${f}" >/dev/null
      did_something=1
    fi
  done

  if git -C "${BACKEND_DIR}" ls-files --error-unmatch .DS_Store >/dev/null 2>&1; then
    log "Removing tracked backend/.DS_Store from Git index (one-time cleanup)"
    git -C "${BACKEND_DIR}" rm --cached -f .DS_Store >/dev/null
    did_something=1
  fi

  if [[ "${did_something}" -eq 1 ]]; then
    log "Git cleanup staged"
    echo "Commit and push these changes from Cursor (backend repo):"
    echo "  - Remove venv/__pycache__/logs from Git"
    echo
  fi
}

setup_backend() {
  log "Backend: Creating .env (if missing)"
  if [[ ! -f "${BACKEND_DIR}/.env" ]]; then
    if [[ -f "${BACKEND_DIR}/.env.example" ]]; then
      cp "${BACKEND_DIR}/.env.example" "${BACKEND_DIR}/.env"
      echo "Created backend/.env from backend/.env.example. Fill in your secrets."
    else
      echo "No backend/.env.example found; skipping."
    fi
  fi

  have_cmd "${PYTHON_BIN}" || die "Missing ${PYTHON_BIN}. Install Python 3.11 or set PYTHON_BIN=python3."

  log "Backend: Rebuilding venv (this will wipe backend/venv)"
  "${PYTHON_BIN}" -m venv --clear "${BACKEND_DIR}/venv"

  log "Backend: Installing dependencies"
  "${BACKEND_DIR}/venv/bin/python" -m pip install -U pip setuptools wheel
  "${BACKEND_DIR}/venv/bin/python" -m pip install -r "${BACKEND_DIR}/requirements.txt"

  log "Backend: Quick sanity checks"
  "${BACKEND_DIR}/venv/bin/python" -m py_compile "${BACKEND_DIR}"/*.py
  "${BACKEND_DIR}/venv/bin/python" - <<'PY'
import importlib
mods = [
  "fastapi",
  "uvicorn",
  "pydantic",
  "dotenv",
  "httpx",
  "pytz",
  "timezonefinder",
  "swisseph",
  "sqlalchemy",
  "docx",
  "jose",
]
for m in mods:
  importlib.import_module(m)
print("Backend imports: OK")
PY
}

setup_frontend() {
  [[ -d "${FRONTEND_DIR}" ]] || return 0

  have_cmd npm || die "Missing npm. Install Node.js, then re-run."

  log "Frontend: Installing dependencies (npm ci)"
  (cd "${FRONTEND_DIR}" && npm ci)
}

main() {
  log "AstroMind: Local bootstrap"
  echo "Workspace: ${ROOT_DIR}"

  cleanup_tracked_artifacts
  setup_backend
  setup_frontend

  log "Done"
  echo "If Cursor still shows old Python errors: Cmd+Shift+P -> 'Developer: Reload Window'."
}

main "$@"
