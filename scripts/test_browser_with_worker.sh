#!/usr/bin/env bash
set -euo pipefail

# CI owns this worker/beat process group. Existing desktop workers are never stopped.
worker_dir=$(mktemp -d)
worker_node="forge-browser-$$@localhost"
worker_pid=""
cleanup() {
  if [[ -n "$worker_pid" ]]; then
    kill -TERM -- "-$worker_pid" 2>/dev/null || true
    wait "$worker_pid" 2>/dev/null || true
  fi
  rm -rf -- "$worker_dir"
}
trap cleanup EXIT INT TERM
setsid uv run --project apps/api celery -A forge_erp.workers.celery_app worker \
  --beat --pool=solo --concurrency=1 --hostname="$worker_node" \
  --schedule="$worker_dir/beat" --loglevel=WARNING >"$worker_dir/worker.log" 2>&1 &
worker_pid=$!
ready=false
for _ in {1..20}; do
  if timeout 8s uv run --project apps/api celery -A forge_erp.workers.celery_app \
    inspect ping --destination="$worker_node" --timeout=1 >/dev/null 2>&1; then
    ready=true
    break
  fi
  if ! kill -0 "$worker_pid" 2>/dev/null; then break; fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  echo "The owned browser-test worker did not become ready." >&2
  exit 1
fi
pnpm test:e2e
