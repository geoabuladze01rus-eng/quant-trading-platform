#!/usr/bin/env bash
set -euo pipefail

docker compose config --quiet
docker compose build
trap 'docker compose down --remove-orphans' EXIT
docker compose up -d

for attempt in {1..30}; do
  if curl --fail --silent --show-error http://127.0.0.1:8000/health \
    | python -c 'import json,sys; h=json.load(sys.stdin); assert h["status"] == "ok" and h["trading_mode"] == "paper" and h["live_trading"] == "locked"'; then
    echo "Docker smoke: localhost health is paper/live locked"
    exit 0
  fi
  sleep 2
done

echo "Docker smoke: localhost paper health did not become ready" >&2
exit 1
