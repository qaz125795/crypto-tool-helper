#!/usr/bin/env bash
# 本機除錯用：載入上一層專案的 .env 後跑一次指令
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${ROOT}"

if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ROOT}/.env"
  set +a
  echo "[load-env-run] 已載入 ${ROOT}/.env"
else
  echo "[load-env-run] 無 .env，僅依目前 shell 環境變數" >&2
fi

PY="${ROOT}/.venv/bin/python"
if [[ ! -x "${PY}" ]]; then
  PY="python3"
fi

"${PY}" "${ROOT}/jackbot.py" "${1:?用法: load-env-run.sh position_change | hyperliquid | crit_radar ...}"
