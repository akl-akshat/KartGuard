# ReturnGuard

**Autonomous, fraud-resistant returns resolution — as a multi-tenant platform.**

Any company uploads its own refund/replacement policy document; ReturnGuard's support agent
then resolves that company's return, refund and replacement requests **according to that
document** — verifying claims with evidence before money moves, scoring customer credibility
over time, and deferring the consequential minority to a human reviewer with full context.

It is a stateful agent graph (LangGraph) behind a FastAPI service, driven synchronously (a
persistent multi-turn support console + REST API) and asynchronously (a Kafka pipeline), with
**deterministic financial guardrails**, **evidence-gated remedies**, a **persistent customer
credibility ledger**, **per-tenant policy RAG**, **human-in-the-loop** escalation, full
observability, and an evaluation harness with hard safety gates.

[![CI](https://github.com/akl-akshat/ReturnGuard/actions/workflows/ci.yml/badge.svg)](https://github.com/akl-akshat/ReturnGuard/actions/workflows/ci.yml)

**Live demo:** [returnguard-99qu.onrender.com/chat](https://returnguard-99qu.onrender.com/chat) (customer console) ·
[/admin](https://returnguard-99qu.onrender.com/admin) (ops console) — free tier, so the first
request after idle takes ~30–50s to wake. A sample tenant ("Zomato (demo)") is pre-seeded:
bind a new conversation to it and ask *"what is the policy if my food arrives with an insect?"*

> Spec: [`docs/ReturnGuard_SRS.pdf`](docs/ReturnGuard_SRS.pdf) (source of truth) ·
> Runbook: [`docs/ReturnGuard_Build_Plan.docx`](docs/ReturnGuard_Build_Plan.docx)

---

## Why this exists

In Indian e-commerce, fashion return rates run 25–35% (≈40% in festive periods); COD
return-to-origin runs 20–40% of COD orders; sellers lose an estimated 8–15% of monthly revenue
to unrecovered return losses — a meaningful share of it to **refund abuse**: false damage
claims, serial returners, "no-no-no-until-full-refund" pressure on support agents. ReturnGuard
replaces the implicit human decision step with an **auditable, constrained, autonomous agent**
that is deliberately hard to defraud and defers to humans on the consequential minority.

## The money-path guarantees

The support conversation enforces six invariants, each regression-tested and adversarially
red-teamed (a 5-agent attack pass found 11 exploits — evidence reuse across claims,
self-certified evidence, a substring false-confirm, credibility penalties on procedural
escalations — all fixed):

1. **Refund is never the default.** Deflection (exchange / like-for-like replacement) is the
   first remedy; a refund is one conditional outcome, reached only when it is the eligible
   remedy AND the claim is verified AND the customer is credible AND it is within caps.
2. **Rejecting a remedy never buys a better one.** Declining re-affirms the policy-correct
   remedy once, then routes to a human. There is no generosity ladder to climb.
3. **One resolution per session.** After any execution or escalation the session is locked —
   a second payout in the same conversation is structurally impossible.
4. **Claims must make sense and be proven.** An issue must be valid for the product category
   (no "wrong size" on a television — redirected, never actioned), and money-moving claims
   (damage, defect, spoiled food, wrong item, fit) require evidence assessed **server-side**
   (vision-model seam; the customer cannot self-certify). Weak or contradictory evidence goes
   to a human, not to a payout.
5. **Credibility is learned and it gates.** A persistent per-customer score drops **only**
   when a human disproves a claim (never for merely asking), and tightens the evidence bar:
   trusted 0.80 → normal 0.85 → watch 0.92 → high-risk always human. Internal only — never
   shown to the customer.
6. **Deterministic guardrails are non-bypassable.** Payout caps, per-customer rate limits,
   order-value ceilings and risk gates read only structured facts and config — never free
   text — so prompt injection cannot move money.

## Multi-tenant policy RAG

The ops console has a **Tenant policies** panel: register a company (e.g. Zomato, Swiggy),
upload its refund/guideline document. The document is chunked into paragraphs and embedded;
every customer query on a session bound to that company is embedded, semantically searched
against *that company's* chunks, and the **top-5 paragraphs** ground the agent's answers (with
citations) and the escalation context a human reviewer sees. Tenant isolation is tested: the
same question on a Zomato-bound vs Swiggy-bound session quotes each company's own policy —
and the same verified spoiled-food claim that the default policy denies resolves as a full
refund under Zomato's uploaded rules.

## Architecture

```
Customer chat (/chat) ──▶  FastAPI Service  ◀── Ops console (/admin): reviews · tenant policies · metrics
        │                        │
        │      per-tenant RAG: query → embed → cosine top-5 over the company's uploaded policy
        │                        │
        ▼                        ▼
  Conversation engine ──▶ validity gate → evidence gate (vision seam) → credibility gate
  (persistent sessions,          │
   confirmation-gated)           ▼
   Kafka ingest ───────▶ Agent Orchestrator (LangGraph) ◀──── review queue / resume / decisions
   (returns.requests.v1)   │            │
        reads (MCP)  ──────┘            └────  actions (guardrailed, idempotent, audited tools)
              │                                      │
              ▼                                      ▼
        MCP data servers                       Action tools
   (order / customer / fraud)        (refund / exchange / coupon / credit / …)
              └──────────────┬───────────────────────┘
                             ▼
        PostgreSQL (+ pgvector) in production · SQLite chat/tenant/credibility store offline:
        orders, customers, policies, resolutions, audit_log, escalations, sessions, ledger
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

Run the API and open the product:

```bash
python scripts/_preview_serve.py     # pins the demo clock; or: make api
# http://localhost:8000/chat     Customer support console (persistent multi-turn sessions)
# http://localhost:8000/admin    Ops console: review queue, tenant policy upload, metrics
# http://localhost:8000/docs     Swagger UI
```

Try the fraud gates yourself in `/chat`: claim a damaged item (it demands a photo before
offering anything — "Attach a clear photo" auto-resolves, "unclear" routes to a human), say
"no" repeatedly to a proposal (goes to a specialist, never a bigger refund), or ask for a
second refund in a resolved chat (locked). In `/admin`, upload a company's policy document and
watch a bound session answer from it with citations.

Resolve a request (sync):

```bash
curl -s localhost:8000/resolve -H 'content-type: application/json' -d '{
  "request_id":"demo-1","issue_text":"The kurti is too tight, I want to return it",
  "order_id":"ORD-FIT-PREPAID","customer_id":"CUST-LOW1"}' | jq
# -> exchange_with_size_guide, with the refund right surfaced
```

Other paths: `POST /resolve/stream` (SSE, node-by-node); `GET /escalations` +
`POST /escalations/{id}/decision` (HITL); `GET /resolutions`, `GET /metrics/summary`.

## Deploy (free, no API keys)

The demo deploys as-is to [Render](https://render.com) via the checked-in
[`render.yaml`](render.yaml) blueprint: **New → Blueprint → select this repo → Apply**. It
runs the deterministic stub providers (no keys), pins the demo clock so seeded return windows
are open, and seeds a sample tenant ("Zomato (demo)") on boot so the per-tenant RAG flow is
always demonstrable. Free-tier disk is ephemeral: sessions/uploads reset on redeploy (the demo
tenant re-seeds automatically).

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
| `agent/conversation.py` | turn-based support dialogue: confirmation gate, one-resolution lock |
| `agent/validity.py` · `evidence.py` · `credibility.py` | issue×category validity · server-side evidence assessor (vision seam) · persistent credibility ledger logic |
| `service/` | FastAPI app, routes, schemas, metrics |
| `service/chat_store.py` · `policy_store.py` | durable SQLite: sessions, messages, credibility · per-tenant policy chunks + embeddings + semantic search |
| `service/routes/chat.py` · `tenants.py` · `portal.py` | support console API + operator review · company/policy upload + search · UI pages |
| `events/` | Kafka schemas, producer, idempotent consumer + DLQ |
| `observability/` | structured logging + tracing seam |
| `eval/` | 44 labelled cases, runner, metrics, hard gates |
| `tests/` | unit · integration · safety — **228 automated tests** |

## Safety & evaluation

- **Guardrails are deterministic and non-bypassable**: they read only the proposed action,
  order facts, and config — never customer free text — so prompt injection cannot move money
  (FR-GRD-4). Verified in `tests/safety/`.
- **Adversarially red-teamed**: a five-agent attack pass targeted each money-path invariant
  (the generosity ladder, double resolution, evidence reuse/forgery, credibility integrity,
  intent false-positives) and produced 11 concrete exploits — every one reproduced, fixed,
  and pinned by a regression test (`tests/integration/test_chat.py`,
  `tests/unit/test_fraud_gates.py`).
- **Eval hard gates** (CI-enforced): guardrail-violation rate **0%**, satisfaction-floor
  adherence **100%**. The deterministic baseline also meets every soft target (root-cause
  accuracy, escalation precision/recall, action appropriateness, p95 ≤ 8s). Run `make eval`.

## Honest scope notes

The photo assessor and LLM run as **deterministic offline stubs behind real provider seams**
(`LLM_PROVIDER`, `EMBEDDING_PROVIDER`, `agent/evidence.py`): swap in a hosted vision/LLM/
embedding model with zero architecture change. The gating architecture — not the model — is
what protects the money, and it is fully real and tested. In-development; no production users
are claimed.

## License

[MIT](LICENSE) © 2026 Akshat Lakhera
