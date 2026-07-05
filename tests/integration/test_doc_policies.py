"""Document-based policies (PDF / DOCX) and the coupon settlement desk.

* A client's policy can arrive as a real PDF or Word file: the platform extracts the text,
  chunks + embeds it, and the agent grounds replies in it — same as pasted markdown.
* The danger zone (replace / delete a document) takes effect on the very next message.
* Coupons behave like boarding passes with a settlement lifecycle: the brand can check a
  code's value and settle it exactly once — after which the platform owes the brand that
  amount and the pass reads USED.
"""

import base64
import io
import zipfile

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent.deps import reset_deps  # noqa: E402
from service import chat_store, demo_seed  # noqa: E402
from service.app import app  # noqa: E402
from service.doc_extract import extract_text  # noqa: E402

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------ file builders
def make_pdf(paragraphs: list[str]) -> bytes:
    """A minimal but valid single-page PDF with real text operators (readable by PyPDF2)."""
    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    lines = []
    y = 760
    for p in paragraphs:
        for chunk in [p[i:i + 90] for i in range(0, len(p), 90)]:
            lines.append(f"BT /F1 11 Tf 40 {y} Td ({esc(chunk)}) Tj ET")
            y -= 16
        y -= 8
    stream = "\n".join(lines).encode("latin-1", "replace")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode())
        out.write(body)
        out.write(b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def make_docx(paragraphs: list[str]) -> bytes:
    """A minimal Word document (docx = zipped OOXML) readable by python-docx."""
    body = "".join(
        f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>" for p in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


PDF_PARAS = [
    "AirVogue Fashion Returns Policy.",
    "Size and fit: for any size issue on apparel, AirVogue offers a free size exchange as the "
    "first remedy, verified by a photo of the garment.",
    "Damaged items: damaged or defective items receive a full refund after photo verification "
    "within seven days of delivery.",
    "Escalation: claims above two thousand rupees are reviewed by an AirVogue specialist.",
]


# ------------------------------------------------------------------ extraction units
def test_pdf_extraction_reads_real_text():
    text = extract_text("policy.pdf", make_pdf(PDF_PARAS))
    assert "AirVogue" in text and "free size exchange" in text and "specialist" in text


def test_docx_extraction_reads_real_text():
    text = extract_text("policy.docx", make_docx(PDF_PARAS))
    assert "AirVogue" in text and "full refund after photo verification" in text


def test_garbage_is_rejected():
    with pytest.raises(ValueError):
        extract_text("policy.pdf", b"\x00\x01\x02 not a pdf at all")


# ------------------------------------------------------------------ end-to-end
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(chat_store, "_INIT", False)
    reset_deps()
    with TestClient(app) as c:
        demo_seed.ensure_platform_demo()
        yield c
    reset_deps()


def _admin(client):
    client.post("/api/auth/login", json={"role": "admin", "id": "admin", "password": "admin123"})


def _turn(client, sid, text="", ev=None):
    body = {"text": text}
    if ev:
        body["evidence"] = ev
    return client.post(f"/api/sessions/{sid}/messages", json=body).json()


def test_pdf_policy_grounds_a_new_companys_conversations(client):
    _admin(client)
    reg = client.post("/api/admin/companies",
                      json={"name": "AirVogue", "policy_text": "placeholder policy text " * 4}).json()
    cid = reg["company"]["id"]
    up = client.post(f"/api/companies/{cid}/policies/upload",
                     json={"doc_name": "airvogue-policy.pdf",
                           "content_b64": base64.b64encode(make_pdf(PDF_PARAS)).decode()}).json()
    assert up["chunks"] >= 2 and up["extracted_chars"] > 200
    o = client.post(f"/api/companies/{cid}/orders",
                    json={"phone": "9776655443", "customer_name": "Rahul Verma",
                          "title": "Linen Shirt", "category": "apparel", "price": 1499}).json()
    rahul = next(u for u in client.get("/api/platform/users").json() if u["phone"] == "9776655443")
    s = client.post("/api/sessions", json={"customer_id": rahul["id"], "order_id": o["id"]}).json()
    r = _turn(client, s["id"], "what is your policy for size issues?")
    assert "AirVogue" in r["messages"][0]["text"]
    assert "free size exchange" in r["messages"][0]["text"]


def test_docx_upload_and_danger_zone_replace_delete(client):
    _admin(client)
    reg = client.post("/api/admin/companies",
                      json={"name": "WordBrand", "policy_text": "placeholder policy text " * 4}).json()
    cid = reg["company"]["id"]
    up = client.post(f"/api/companies/{cid}/policies/upload",
                     json={"doc_name": "policy.docx",
                           "content_b64": base64.b64encode(make_docx(PDF_PARAS)).decode()})
    assert up.status_code == 200 and up.json()["chunks"] >= 2
    # replace: same doc name, new content — chunk set replaced, not appended
    up2 = client.post(f"/api/companies/{cid}/policies/upload",
                      json={"doc_name": "policy.docx",
                            "content_b64": base64.b64encode(
                                make_docx(["All WordBrand sales are final, always — no returns are accepted."])).decode()}).json()
    docs = client.get(f"/api/companies/{cid}/policies").json()
    d = next(x for x in docs if x["doc_name"] == "policy.docx")
    assert d["chunks"] == up2["chunks"] == 1
    # delete (danger zone) — admin/client session required, disappears immediately
    r = client.delete(f"/api/companies/{cid}/policies/policy.docx")
    assert r.status_code == 200
    assert not any(x["doc_name"] == "policy.docx"
                   for x in client.get(f"/api/companies/{cid}/policies").json())
    # unauthenticated deletion is refused
    fresh = TestClient(app)
    assert fresh.delete(f"/api/companies/{cid}/policies/whatever.md").status_code == 401


# ------------------------------------------------------------------ coupon settlement
def test_coupon_check_and_settle_lifecycle(client):
    me = next(u for u in client.get("/api/platform/users").json() if u["phone"] == "9650440034")
    from service import wallet_store
    wallet_store.credit(me["id"], 500, "refund", ref="seed-balance")
    code = client.post(f"/api/wallet/{me['id']}/coupon",
                       json={"brand": "Amazon", "amount": 300}).json()["code"]
    # brand desk requires a signed-in client of THAT brand
    fresh = TestClient(app)
    assert fresh.get(f"/api/coupons/{code}").status_code == 401
    client.post("/api/auth/login", json={"role": "client", "id": "swiggy", "password": "swiggy123"})
    assert client.get(f"/api/coupons/{code}").status_code == 403     # Swiggy can't read Amazon's code
    client.post("/api/auth/login", json={"role": "client", "id": "amazon", "password": "amazon123"})
    chk = client.get(f"/api/coupons/{code}").json()
    assert chk["amount"] == 300 and chk["settled"] is False
    st = client.post(f"/api/coupons/{code}/settle").json()
    assert st["ok"] and st["amount"] == 300                          # platform owes Amazon ₹300
    assert client.post(f"/api/coupons/{code}/settle").status_code == 409   # one-shot
    # the customer's boarding pass now reads USED
    w = client.get(f"/api/wallet/{me['id']}").json()
    assert any(c["code"] == code and c["settled"] for c in w["coupons"])
