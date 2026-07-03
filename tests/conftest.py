"""Shared test setup.

Deterministic 'today'
---------------------
The seeded dataset computes every order's return window relative to
``db.dataset.REFERENCE_DATE`` (2026-06-22). Window math (``policies.retrieve``)
uses ``settings.as_of_date``, which falls back to the real wall-clock date when
``AS_OF_DATE`` is empty. So the suite is only correct if 'today' is pinned to the
reference date — otherwise, once real time moves past the seeded windows, every
seeded order silently becomes out-of-window and eligibility/decision assertions
flip (e.g. ``resolved`` -> ``denied``).

Many tests already pin ``settings.AS_OF_DATE = "2026-06-22"`` and then reset it to
``""`` (real today) on teardown, which leaks a wall-clock-dependent value into any
later test that doesn't pin the date itself. This autouse fixture re-establishes
the pinned reference before every test, so the whole suite is deterministic
regardless of when it runs or in what order the tests execute.
"""

from __future__ import annotations

import pytest

from config.settings import settings

# Keep in sync with db.dataset.REFERENCE_DATE.
REFERENCE_DATE_ISO = "2026-06-22"


@pytest.fixture(autouse=True)
def _pin_as_of_date():
    """Pin window math to the seed reference date at the start of every test."""
    settings.AS_OF_DATE = REFERENCE_DATE_ISO
    yield
