"""Adversarial QA — audit integrity, RAG filtering, MCP read-only, graph durability/cap."""

import inspect
from pathlib import Path

import pytest

from agent.deps import get_deps, reset_deps
from agent.graph import build_graph
from agent.runner import run_config
from agent.state import initial_state
from config.settings import settings

pytestmark = pytest.mark.integration

MONEY = {"instant_refund", "partial_refund", "store_credit_refund", "retention_coupon", "goodwill_credit"}


@pytest.fixture(autouse=True)
def _clock():
    settings.AS_OF_DATE = "2026-06-22"
    reset_deps()
    yield
    settings.AS_OF_DATE = ""
    reset_deps()


def _mem():
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


def _run_batch():
    g = build_graph(checkpointer=_mem())
    cases = [
        ("a1", "ORD-FIT-PREPAID", "CUST-LOW1", "The kurti is too tight"),
        ("a2", "ORD-MIND-PREPAID", "CUST-VIP1", "changed my mind, return it"),
        ("a3", "ORD-DEFECT-ELEC", "CUST-LOW1", "arrived defective and broken"),
        ("a4", "EVO-GENUINE-PRE", "CUST-LOW1", "I have an issue and want a refund"),
    ]
    for rid, oid, cid, msg in cases:
        g.invoke(initial_state(rid, msg, order_id=oid, customer_id=cid), run_config(rid))
    return get_deps().repo, [c[0] for c in cases]


# ----------------------------------------------------------- audit integrity
def test_no_orphan_audit_and_every_monetary_has_audit():
    repo, rids = _run_batch()
    for rid in rids:
        res = repo.get_resolution(rid)
        assert res is not None
        ex = res.get("executed_action") or {}
        if ex.get("action_type") in MONEY:
            assert repo.get_audit(rid, ex["action_type"]), f"monetary action without audit row: {rid}"
    # no orphan audit rows (every audited request has a resolution)
    for a in repo._audit:  # noqa: SLF001 - integrity introspection
        assert repo.get_resolution(a["request_id"]) is not None, f"orphan audit: {a}"


def test_audit_amounts_are_non_negative_and_within_value():
    repo, rids = _run_batch()
    for a in repo._audit:  # noqa: SLF001
        assert (a.get("amount") or 0) >= 0, f"negative audit amount: {a}"


def test_resolution_amount_reconciles_with_audit_total():
    """AC-6 reconciliation: the recorded resolution amount should equal the money actually
    moved for that request (incl. any goodwill sweetener)."""
    repo, rids = _run_batch()
    for rid in rids:
        res = repo.get_resolution(rid)
        audit_total = round(sum(a.get("amount") or 0 for a in repo.get_audit(rid)
                                if a["action_type"] in MONEY), 2)
        recorded = round(res.get("amount") or 0, 2)
        assert recorded == audit_total, (
            f"{rid}: resolution.amount={recorded} != audit monetary total={audit_total} "
            "(goodwill sweetener under-reported in resolution.amount)"
        )


def test_no_app_level_audit_mutation_methods():
    """FR-LOG-2: audit_log is insert-only at the application layer."""
    from db.repository import InMemoryRepository, PostgresRepository
    for cls in (InMemoryRepository, PostgresRepository):
        names = {m for m in dir(cls)}
        assert not any("update_audit" in n or "delete_audit" in n for n in names)


# ------------------------------------------------------------- MCP read-only
def test_mcp_servers_expose_only_read_tools():
    base = Path(__file__).resolve().parents[2] / "mcp_servers"
    for f in ("order_service.py", "customer_service.py", "fraud_service.py"):
        src = (base / f).read_text(encoding="utf-8")
        # every @mcp.tool function name starts with get_
        import re
        tools = re.findall(r"@mcp\.tool\s+def\s+(\w+)", src)
        assert tools, f"no tools found in {f}"
        assert all(t.startswith("get_") for t in tools), f"non-read tool in {f}: {tools}"


def test_data_access_unknown_ids_no_fabrication():
    from tools.data_access import LocalDataAccess
    da = LocalDataAccess()
    assert da.get_order("NOPE") is None
    assert da.get_customer("NOPE") is None
    assert da.get_risk_signals("NOPE", "NOPE") is None


# ------------------------------------------------------------------ RAG
def test_rag_unknown_category_returns_only_cross_cutting_policy():
    """An unknown category must not invent a category-specific rule; only genuine wildcard
    ('*') cross-cutting policies (defect exception, refund mode) may apply."""
    from policies.retrieve import retrieve_policy
    snips = retrieve_policy("nonexistent_category", "PREPAID", "return_request")
    assert all(s.metadata.get("category") == "*" for s in snips), (
        f"retrieval surfaced a category-specific rule for an unknown category: "
        f"{[(s.policy_id, s.metadata.get('category')) for s in snips]}"
    )


def test_rag_payment_mode_filter_excludes_wrong_mode():
    from policies.retrieve import retrieve_policy
    assert all(s.policy_id != "POL-COD-REFUND-MODE" for s in retrieve_policy("apparel", "PREPAID", "refund_status"))


# ------------------------------------------------------- graph durability/cap
def test_checkpointer_persists_and_no_memorysaver_in_agent_pkg():
    g = build_graph(checkpointer=_mem())
    cfg = run_config("ckpt-1")
    g.invoke(initial_state("ckpt-1", "The kurti is too tight", order_id="ORD-FIT-PREPAID",
                           customer_id="CUST-LOW1"), cfg)
    assert g.get_state(cfg).values["request_id"] == "ckpt-1"
    agent_dir = Path(__file__).resolve().parents[2] / "agent"
    for py in agent_dir.rglob("*.py"):
        assert "memorysaver" not in py.read_text(encoding="utf-8").lower()
