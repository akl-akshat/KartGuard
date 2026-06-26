"""Adversarial QA — secret hygiene & synthetic-only data (NFR-SEC-1/3)."""

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety

ROOT = Path(__file__).resolve().parents[2]

# High-signal secret patterns (avoid matching placeholders / variable names).
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),          # OpenAI-style
    re.compile(r"sk-ant-[A-Za-z0-9-]{20,}"),     # Anthropic
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),   # GitHub tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),             # AWS access key id
    re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
]


def _tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return [ROOT / line for line in out.stdout.splitlines() if line.strip()]


def test_no_secrets_committed():
    offenders = []
    for f in _tracked_files():
        if f.suffix in (".pdf", ".docx", ".png", ".ico"):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                offenders.append((str(f.relative_to(ROOT)), pat.pattern))
    assert not offenders, f"possible secrets committed: {offenders}"


def test_env_example_has_no_real_values():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    # secret keys must be blank placeholders, not populated
    for line in env.splitlines():
        if line.startswith(("LLM_API_KEY", "LANGFUSE_SECRET_KEY", "LANGSMITH_API_KEY")):
            assert line.split("=", 1)[1].strip() == "", f"populated secret in .env.example: {line}"


def test_dataset_is_synthetic():
    from db.dataset import build_dataset
    ds = build_dataset()
    # synthetic naming, no real-looking PII
    assert all(c.name.startswith(("Customer", "Low", "Serial", "Brand", "VIP")) for c in ds.customers[:60])
