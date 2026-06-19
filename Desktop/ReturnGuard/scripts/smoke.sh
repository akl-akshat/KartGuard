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

say "offline unit + safety suite (no infra)"
python -m pytest -q -m "unit or safety" -p no:cacheprovider

# Graph vertical slice (requires langgraph; skipped if not installed).
if python -c "import langgraph" 2>/dev/null; then
  say "graph vertical slice (stub invoke on a seeded order)"
  python -c "
from langgraph.checkpoint.memory import MemorySaver
from agent.graph import build_graph
from agent.state import initial_state
g = build_graph(checkpointer=MemorySaver())
out = g.invoke(initial_state('smoke-1','return please',order_id='ORD-FIT-PREPAID',customer_id='CUST-LOW1'),
               {'configurable':{'thread_id':'smoke-1'}})
assert out['status'] == 'resolved', out
print('graph invoke OK ->', out['status'], '| msg:', out['customer_message'][:40])
"
fi

if [[ "${RG_SMOKE_INFRA:-0}" == "1" ]]; then
  say "infra health (docker compose)"
  docker compose ps
fi

say "smoke OK"
