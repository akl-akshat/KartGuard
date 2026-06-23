# ReturnGuard

**Autonomous Returns-Deflection & Resolution Agent** — an agentic, constrained-autonomy
system for e-commerce post-order resolution.

ReturnGuard intercepts return / cancellation / refund requests and resolves them by selecting
the **lowest-cost action that satisfies platform policy, protects seller margin, and preserves
customer satisfaction** — escalating to a human only when warranted. It is a stateful agent
graph (LangGraph) behind a FastAPI service, driven both synchronously (chat/API) and
asynchronously (a Kafka pipeline), with **deterministic financial guardrails**,
**human-in-the-loop** escalation, full **observability**, and an **evaluation harness** with
hard safety gates.

[![CI](https://github.com/akl-akshat/ReturnGuard/actions/workflows/ci.yml/badge.svg)](https://github.com/akl-akshat/ReturnGuard/actions/workflows/ci.yml)

> Spec: [`docs/ReturnGuard_SRS.pdf`](docs/ReturnGuard_SRS.pdf) (source of truth) ·
> Runbook: [`docs/ReturnGuard_Build_Plan.docx`](docs/ReturnGuard_Build_Plan.docx)

---

## Why this exists

In Indian e-commerce, fashion return rates run 25–35% (≈40% in festive periods); COD
return-to-origin runs 20–40% of COD orders; sellers lose an estimated 8–15% of monthly revenue
to unrecovered return losses. ReturnGuard replaces the implicit human decision step with an
**auditable, constrained, autonomous agent** that defers to humans on the consequential minority.

## Architecture

```
Customer / API  ──▶  FastAPI Service  ◀──▶  Operator Dashboard (/dashboard)
                          │
   Kafka ingest  ───────▶ Agent Orchestrator (LangGraph) ◀──── review queue / resume / metrics
   (returns.requests.v1)   │            │
        reads (MCP)  ──────┘            └────  actions (guardrailed, simulated, audited tools)
              │                                      │
              ▼                                      ▼
        MCP data servers                       Action tools
   (order / customer / fraud)        (refund / exchange / coupon / credit / …)
              └──────────────┬───────────────────────┘
                             ▼
        PostgreSQL (+ pgvector): orders, customers, policies, resolutions,
        audit_log, escalations, eval_cases, vector index, LangGraph checkpointer
                             │
                             ▼
        Kafka emit: resolutions / escalations / audit / outcomes   ·   Tracing: Langfuse / LangSmith
```

The agent graph (`agent/graph.py`) is a single `StateGraph` of specialised nodes with exactly
**two branch points**: the clarification loop at intake and the **guardrail → HITL fork** before
execution. The money decision is a deterministic, bounded constrained-optimisation (§9.4), not
model discretion. See SRS §3 (Design Rationale) and §9 (Decision Logic).

## Quickstart (offline — no Docker, no API key)

ReturnGuard runs fully offline using a deterministic **stub LLM** and an in-memory data layer,
so the graph and the evaluation harness are reproducible out of the box.

```bash
python -m venv .venv && source .venv/bin/activate     # (Windows: .venv\Scripts\activate)
pip install -e ".[dev]"

make test          # full test suite (unit + safety + integration)
make eval          # evaluation harness with hard gates
bash scripts/demo.sh   # AC-4 / AC-5 / AC-6 acceptance demonstrations
```

Run the API and open the docs / dashboard:

```bash
make api                       # uvicorn service.app:app
# http://localhost:8000/docs        Swagger UI (UI-1)
# http://localhost:8000/dashboard   Operator console (UI-2)
```

Resolve a request (sync):

```bash
curl -s localhost:8000/resolve -H 'content-type: application/json' -d '{
  "request_id":"demo-1","issue_text":"The kurti is too tight, I want to return it",
  "order_id":"ORD-FIT-PREPAID","customer_id":"CUST-LOW1"}' | jq
# -> exchange_with_size_guide, with the refund right surfaced
```

Other paths: `POST /resolve/stream` (SSE, node-by-node); `GET /escalations` +
`POST /escalations/{id}/decision` (HITL); `GET /resolutions`, `GET /metrics/summary`.

## Full stack (Postgres + Kafka + real Claude)

```bash
cp .env.example .env          # set LLM_PROVIDER=anthropic and LLM_API_KEY for real Claude
docker compose up -d          # Postgres(+pgvector) + Kafka
make schema && make seed      # apply schema + load synthetic data
make embed                    # chunk + embed the policy corpus into pgvector
make api                      # service (uses the Postgres checkpointer -> durable HITL)
make worker                   # Kafka consumer (idempotent, with DLQ)
```

The MCP read servers run standalone: `python -m mcp_servers.order_service` (and customer / fraud).

## Configuration

Everything is config-driven (`config/settings.py`, SRS Appendix B). Key knobs:
`MAX_COUPON_PCT=0.20`, `MAX_COUPON_ABS=300`, `MAX_AUTO_REFUND_ABS=2000`,
`RISK_ESCALATION_THRESHOLD=0.70`, `MAX_GOODWILL_CREDIT=150`, `AUTO_REFUND_RATE_LIMIT=3/30d`,
`RAG_TOP_K=4`, `MAX_ITERATIONS=12`, `LLM_TIMEOUT_S=30`, `LLM_MAX_RETRIES=3`. Secrets come only
from the environment (NFR-SEC-1).

## Project layout

| Path | What |
|---|---|
| `config/` | pydantic-settings (all thresholds) |
| `db/` | schema, deterministic synthetic dataset, repository (in-memory + Postgres) |
| `policies/` | RAG corpus, embedder, metadata-filtered retrieval |
| `mcp_servers/` | read-only order/customer/fraud MCP servers |
| `tools/` | simulated, audited action tools + data-access seam |
| `agent/` | typed state, graph, nodes, decision core (cost/eligibility/select/guardrails) |
| `service/` | FastAPI app, routes, schemas, metrics, dashboard |
| `events/` | Kafka schemas, producer, idempotent consumer + DLQ |
| `observability/` | structured logging + tracing seam |
| `eval/` | 44 labelled cases, runner, metrics, hard gates |
| `tests/` | unit · integration · safety |

## Safety & evaluation

- **Guardrails are deterministic and non-bypassable**: they read only the proposed action,
  order facts, and config — never customer free text — so prompt injection cannot move money
  (FR-GRD-4). Verified in `tests/safety/`.
- **Eval hard gates** (CI-enforced): guardrail-violation rate **0%**, satisfaction-floor
  adherence **100%**. The deterministic baseline also meets every soft target (root-cause
  accuracy, escalation precision/recall, action appropriateness, p95 ≤ 8s). Run `make eval`.

## License

[MIT](LICENSE) © 2026 Akshat Lakhera
