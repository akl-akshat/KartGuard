"""Multi-tenant policy endpoints: companies upload their own support policies.

A company (Zomato, Swiggy, …) registers, uploads its refund/replacement/guideline documents,
and from then on chat sessions bound to that company are answered **from that company's
policy**: each query is embedded, semantically searched against the company's chunks, and the
top paragraphs ground the agent's replies and escalation context (RAG per tenant).
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from service import doc_extract, policy_store
from service.routes.auth import require_role

router = APIRouter()


class CreateCompany(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)


class UploadPolicy(BaseModel):
    doc_name: str = Field(..., min_length=1, max_length=120)
    text: str = Field(..., min_length=40, max_length=400_000)


@router.get("/api/companies")
def list_companies() -> list[dict]:
    """All client brands, with their credibility-weighted public service rating."""
    from service import rating_store

    ratings = rating_store.all_company_ratings()
    return [{**c, **(ratings.get(c["id"]) or {"rating": None, "count": 0})}
            for c in policy_store.list_companies()]


@router.post("/api/companies")
def create_company(body: CreateCompany) -> dict:
    return policy_store.create_company(body.name)


@router.get("/api/companies/{company_id}/policies")
def list_policies(company_id: str) -> list[dict]:
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    return policy_store.list_documents(company_id)


@router.post("/api/companies/{company_id}/policies")
def upload_policy(company_id: str, body: UploadPolicy) -> dict:
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    out = policy_store.upload_policy(company_id, body.doc_name, body.text)
    if out["chunks"] == 0:
        raise HTTPException(status_code=422, detail="document produced no usable paragraphs")
    return out


class UploadPolicyFile(BaseModel):
    doc_name: str = Field(..., min_length=1, max_length=120)
    content_b64: str = Field(..., min_length=8)   # the raw file, base64-encoded (PDF/DOCX/MD/TXT)


@router.post("/api/companies/{company_id}/policies/upload")
def upload_policy_file(company_id: str, body: UploadPolicyFile) -> dict:
    """Upload a policy as a FILE — PDF, DOCX, Markdown or plain text. The text is extracted,
    chunked, embedded and immediately live for that client's conversations."""
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    try:
        data = base64.b64decode(body.content_b64, validate=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="content_b64 is not valid base64") from None
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file too large (15 MB max)")
    try:
        text = doc_extract.extract_text(body.doc_name, data)
    except doc_extract.ExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    out = policy_store.upload_policy(company_id, body.doc_name, text)
    if out["chunks"] == 0:
        raise HTTPException(status_code=422, detail="document produced no usable paragraphs")
    return {**out, "extracted_chars": len(text)}


@router.delete("/api/companies/{company_id}/policies/{doc_name}")
def delete_policy(company_id: str, doc_name: str,
                  rg_session: str | None = Cookie(default=None)) -> dict:
    """Danger zone: remove a policy document (client or admin only). Takes effect immediately —
    conversations fall back to the remaining documents / platform defaults."""
    require_role(rg_session, "client", "admin")
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    if not policy_store.delete_document(company_id, doc_name):
        raise HTTPException(status_code=404, detail="document not found")
    return {"ok": True}


@router.get("/api/companies/{company_id}/search")
def search_policy(company_id: str, q: str) -> list[dict]:
    """Debug/ops endpoint: see exactly which paragraphs a query retrieves."""
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    return policy_store.search(company_id, q)
