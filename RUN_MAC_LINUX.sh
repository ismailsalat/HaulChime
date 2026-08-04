#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x backend/.venv/bin/python ]; then
  python3 -m venv backend/.venv
fi
backend/.venv/bin/python -m pip install -r backend/requirements.txt
(
  cd backend
  .venv/bin/python seed.py
)

cleanup() {
  kill "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(
  cd backend
  .venv/bin/python -m flask --app app run --port 5002
) &
BACKEND_PID=$!
(
  cd frontend/dist
  ../../backend/.venv/bin/python -m http.server 8080
) &
FRONTEND_PID=$!

printf '\nHaulChime is running.\nWebsite: http://localhost:8080\nAdmin: http://localhost:5002/admin\nLogin: admin / haulchime123\nPress Ctrl+C to stop both servers.\n\n'
if command -v open >/dev/null 2>&1; then open http://localhost:8080 || true
elif command -v xdg-open >/dev/null 2>&1; then xdg-open http://localhost:8080 || true
fi
wait
