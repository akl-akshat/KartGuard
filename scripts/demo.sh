#!/usr/bin/env bash
# Acceptance-criteria demonstration (AC-4 / AC-5 / AC-6 + metrics). See scripts/demo.py.
set -euo pipefail
cd "$(dirname "$0")/.."
AS_OF_DATE="${AS_OF_DATE:-2026-06-22}" python scripts/demo.py
