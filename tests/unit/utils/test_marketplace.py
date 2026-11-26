import pytest

from saltext.noderings.utils import marketplace
from saltext.noderings.utils.client import NodeRingsError


def test_main_price_prefers_compute_meter():
    prices = [
        {"unitAmount": "0.000041", "unitName": "per GB-hour"},
        {"unitAmount": "0.007", "unitName": "per compute-hour"},
        {"unitAmount": "3", "unitName": "per GB"},
    ]
    assert marketplace.main_price(prices) == 0.007
    assert (
        marketplace.main_price(
            [
                {"unitAmount": "5", "pricingModel": "PRICING_MODEL_FIXED"},
                {"unitAmount": "0.1", "unitName": "per GB"},
            ]
        )
        == 5
    )
    assert marketplace.main_price([]) == float("inf")


class FakeMarket:
    def __init__(self, providers):
        self.providers = providers

    def list_providers(self, code, country, city, billing, ipv6):  # pylint: disable=unused-argument
        return self.providers


def provider(name, price, caps, rating=0.0):
    return {
        "organizationId": name,
        "organizationName": name,
        "planId": "plan-" + name,
        "capabilities": caps,
        "rating": rating,
        "planRegions": [{"regionId": "region-" + name, "country": "Germany", "city": "Frankfurt"}],
        "planPrices": [
            {"unitAmount": price, "unitName": "per compute-hour"},
            {"unitAmount": "0.00001", "unitName": "per GB-hour"},
        ],
    }


def test_find_offers_filters_and_sorts():
    m = FakeMarket(
        [
            provider("a", "0.02", ["guest_firewall"], 4.5),
            provider("b", "0.01", [], 3.0),
            provider("c", "0.015", ["guest_firewall"], 4.9),
        ]
    )
    offers, located = marketplace.find_offers(
        m, "X", "Germany", "frankfurt", require_capabilities=["guest_firewall"]
    )
    assert located == 3
    assert [o["provider_name"] for o in offers] == ["c", "a"]
    offers, located = marketplace.find_offers(m, "X", "Germany", "Frankfurt", sort="rating")
    assert [o["provider_name"] for o in offers] == ["c", "a", "b"]
    assert offers[0]["region_id"] == "region-c"


class Storefronts:
    def __init__(self, error=None):
        self.error = error

    def get_public_storefront(self, slug):
        if self.error:
            raise self.error
        return {"slug": slug, "providerId": "prov-" + slug}


def test_resolve_provider():
    uuid = "33333333-3333-3333-3333-333333333333"
    assert marketplace.resolve_provider(Storefronts(), "") == (None, None)
    assert marketplace.resolve_provider(Storefronts(), uuid) == (uuid, None)
    assert marketplace.resolve_provider(Storefronts(), "acme") == ("prov-acme", None)
    assert marketplace.resolve_provider(Storefronts(), " Acme Hosting ") == (None, "Acme Hosting")
    not_found = NodeRingsError(404, 5, "storefront not found")
    assert marketplace.resolve_provider(Storefronts(not_found), "acme") == (None, "acme")
    with pytest.raises(NodeRingsError):
        marketplace.resolve_provider(Storefronts(NodeRingsError(503, 14, "unavailable")), "acme")
