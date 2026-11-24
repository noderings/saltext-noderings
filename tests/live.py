"""Helpers for tests that call the real NodeRings API."""

import os
import uuid

import pytest

HAS_TOKEN = any(
    os.environ.get(v) for v in ("NODERINGS_TOKEN", "NODERINGS_API_TOKEN", "NR_API_TOKEN")
)
BILLED = os.environ.get("NODERINGS_TEST_BILLED") == "1"

live = pytest.mark.skipif(
    not HAS_TOKEN, reason="needs NODERINGS_TOKEN (calls the real NodeRings API)"
)
billed = pytest.mark.skipif(
    not (HAS_TOKEN and BILLED), reason="needs NODERINGS_TEST_BILLED=1 (orders a billed server)"
)


def unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_domain():
    return os.environ.get("NODERINGS_TEST_DOMAIN", "example.com")


def plan():
    """The offer billed tests order: an hourly 1 vCPU, 1 GB server."""
    return {
        "plan_code": os.environ.get("NODERINGS_TEST_PLAN_CODE", "SC-GC-1C-1G-20"),
        "country": os.environ.get("NODERINGS_TEST_COUNTRY", "United States"),
        "city": os.environ.get("NODERINGS_TEST_CITY", "Ashburn"),
    }
