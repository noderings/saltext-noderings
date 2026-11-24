from saltext.noderings.utils import marketplace


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

    def list_providers(self, code, country, city, billing, ipv6):
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
