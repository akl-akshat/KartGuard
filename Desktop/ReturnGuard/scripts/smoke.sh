#!/usr/bin/env bash
# Cumulative smoke test (SRS build-plan §0.4): the no-regression safety net.
# It MUST always exit 0. It grows one capability per phase.
#
# Levels:
#   * offline (always run): config loads + pure unit/safety tests + offline eval smoke.
#   * infra   (RG_SMOKE_INFRA=1): docker compose health + seed + a /resolve round-trip.
#
# By design the offline level needs no Docker/LLM key, so it stays green in CI.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

say "config loads (NFR-MNT-2)"
python -c "from config.settings import settings; print('config OK:', settings.APP_NAME, settings.MAX_AUTO_REFUND_ABS, settings.MAX_ITERATIONS)"

# --- Phase 0 baseline ends here. Later phases append capabilities below. ---

if [[ "${RG_SMOKE_INFRA:-0}" == "1" ]]; then
  say "infra health (docker compose)"
  docker compose ps
fi

say "smoke OK"
