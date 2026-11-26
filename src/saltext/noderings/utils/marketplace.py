"""
Marketplace offer lookup: turn a plan code and a city into a plan and region ID.
"""

import re

from saltext.noderings.utils.client import NodeRingsError
from saltext.noderings.utils.client import is_not_found
from saltext.noderings.utils.client import is_uuid
from saltext.noderings.utils.client import trim_enum

BILLING_TYPES = {
    "usage": "BILLING_TYPE_USAGE",
    "subscription": "BILLING_TYPE_SUBSCRIPTION",
    "free": "BILLING_TYPE_FREE",
}
ASSET_TYPES = {"virtual_machine": "ASSET_TYPE_VM", "game_server": "ASSET_TYPE_GAME_SERVER"}


class NoOfferError(Exception):
    pass


_SLUG_RE = re.compile(r"^[a-z0-9-]{1,80}$")


def resolve_provider(client, provider):
    """Turn a provider ID, storefront slug or provider name into ``(provider_id, name)``.

    An ID or a slug that names a public storefront gives the provider ID. Anything
    else is a provider name, matched without regard to case against the offers.
    """
    value = (provider or "").strip()
    if not value:
        return None, None
    if is_uuid(value):
        return value, None
    if _SLUG_RE.match(value):
        try:
            sf = client.get_public_storefront(value)
        except NodeRingsError as exc:
            if not is_not_found(exc):
                raise
            sf = None
        if sf and sf.get("providerId"):
            return sf["providerId"], None
    return None, value


def _amount(p):
    try:
        return float(p.get("unitAmount"))
    except (TypeError, ValueError):
        return None


def main_price(prices):
    """The price that best represents what the plan costs, for sorting.

    Usage plans carry several meters (compute-hours, storage GB-hours, traffic,
    IPs); comparing the smallest of them would rank by the cheapest meter, not
    the cheapest server. Prefer the compute meter, then a recurring flat price,
    then fall back to the smallest amount.
    """
    prices = [p for p in prices or [] if _amount(p) is not None]
    if not prices:
        return float("inf")
    compute = [p for p in prices if "compute" in (p.get("unitName") or "").lower()]
    if compute:
        return min(_amount(p) for p in compute)
    flat = [
        p
        for p in prices
        if (p.get("pricingModel") or "").endswith(("FLAT", "FLAT_RATE", "FIXED", "PER_UNIT"))
        or (p.get("chargeTiming") or "").endswith("IN_ADVANCE")
    ]
    if flat:
        return min(_amount(p) for p in flat)
    return min(_amount(p) for p in prices)


def price_view(p):
    return {
        "unit_amount": p.get("unitAmount") or "",
        "currency": p.get("currency") or "",
        "unit_name": p.get("unitName") or "",
        "billing_period": trim_enum(p.get("billingPeriod"), "BILLING_PERIOD_"),
        "pricing_model": trim_enum(p.get("pricingModel"), "PRICING_MODEL_"),
        "charge_timing": trim_enum(p.get("chargeTiming"), "CHARGE_TIMING_"),
        "minimum_charge": p.get("minimumCharge") or "",
    }


def find_offers(
    client,
    plan_code,
    country,
    city,
    billing_type=None,
    asset_type="virtual_machine",
    provider_id=None,
    storefront_slug=None,
    require_capabilities=None,
    require_ipv6=False,
    sort="price",
    provider=None,
):
    """Return (offers, located) where offers are sorted best first and located
    counts the offers that matched the location before capability/IPv6 filters.

    ``provider`` is a provider ID, storefront slug or provider name; see
    :func:`resolve_provider`."""
    provider_name = None
    if provider and not (provider_id or storefront_slug):
        provider_id, provider_name = resolve_provider(client, provider)
    billing = BILLING_TYPES.get(billing_type or "", "")
    asset = ASSET_TYPES.get(asset_type or "virtual_machine", "ASSET_TYPE_VM")
    if storefront_slug:
        sf = client.get_public_storefront(storefront_slug)
        if not sf:
            raise NoOfferError(f"no storefront is named {storefront_slug}")
        provider_id = sf.get("providerId")
    found = []
    if provider_id:
        p = client.get_provider(provider_id) or {}
        info = p.get("provider") or {}
        for pl in p.get("availablePlans") or []:
            if pl.get("planCode") != plan_code or (billing and pl.get("billingType") != billing):
                continue
            if pl.get("assetType") and pl.get("assetType") != asset:
                continue
            for r in pl.get("regions") or []:
                if (r.get("country") or "").lower() == country.lower() and (
                    r.get("city") or ""
                ).lower() == city.lower():
                    found.append(
                        {
                            "provider_id": info.get("organizationId"),
                            "provider_name": info.get("organizationName"),
                            "plan_id": pl.get("planId"),
                            "region_id": r.get("regionId"),
                            "billing_type": pl.get("billingType"),
                            "platform_driver": info.get("platformDriver"),
                            "capabilities": info.get("capabilities") or [],
                            "ipv6": bool(p.get("hasIpv6")),
                            "rating": float(info.get("rating") or 0),
                            "total_reviews": int(info.get("totalReviews") or 0),
                            "prices": pl.get("planPrices") or [],
                        }
                    )
    else:
        for p in client.list_providers(plan_code, country, city, billing or None, require_ipv6):
            region_id = ""
            for r in p.get("planRegions") or []:
                if (r.get("city") or "").lower() == city.lower() and (
                    r.get("country") or ""
                ).lower() == country.lower():
                    region_id = r.get("regionId")
            if not region_id and p.get("planRegions"):
                region_id = p["planRegions"][0].get("regionId")
            found.append(
                {
                    "provider_id": p.get("organizationId"),
                    "provider_name": p.get("organizationName"),
                    "plan_id": p.get("planId"),
                    "region_id": region_id,
                    "billing_type": billing,
                    "platform_driver": p.get("platformDriver"),
                    "capabilities": p.get("capabilities") or [],
                    "ipv6": bool(p.get("ipv6AvailableInFilteredRegion")),
                    "rating": float(p.get("rating") or 0),
                    "total_reviews": int(p.get("totalReviews") or 0),
                    "prices": p.get("planPrices") or [],
                }
            )
    if provider_name:
        want = provider_name.lower()
        found = [o for o in found if (o["provider_name"] or "").strip().lower() == want]
        ids = {o["provider_id"] for o in found}
        if len(ids) > 1:
            raise NoOfferError(
                f"more than one provider is named {provider_name}; give its provider ID or "
                "storefront slug instead"
            )
    located = len(found)
    need = set(require_capabilities or [])
    out = [
        o
        for o in found
        if need.issubset(set(o["capabilities"])) and (not require_ipv6 or o["ipv6"])
    ]

    def key(o):
        price = main_price(o["prices"])
        if sort == "rating":
            return (-o["rating"], price, o["provider_name"] or "", o["plan_id"] or "")
        return (price, -o["rating"], o["provider_name"] or "", o["plan_id"] or "")

    out.sort(key=key)
    return out, located


def offer_view(o):
    return {
        "provider_id": o["provider_id"],
        "provider_name": o["provider_name"],
        "plan_id": o["plan_id"],
        "region_id": o["region_id"],
        "billing_type": trim_enum(o.get("billing_type"), "BILLING_TYPE_"),
        "platform_driver": trim_enum(o.get("platform_driver"), "PLATFORM_DRIVER_"),
        "capabilities": o["capabilities"],
        "ipv6": o["ipv6"],
        "rating": o["rating"],
        "total_reviews": o["total_reviews"],
        "prices": [price_view(p) for p in o["prices"]],
    }


def no_offer_message(plan_code, country, city, billing_type, located, provider=None):
    msg = f"No offer for plan code {plan_code} in {city}, {country}"
    if provider:
        msg += f" from provider {provider}"
    if billing_type:
        msg += f" with billing type {billing_type}"
    msg += "."
    if located:
        msg += (
            f" {located} offer(s) matched the location but not the required capabilities or IPv6."
        )
    msg += " List what is sold with noderings.list_plans and noderings.list_regions."
    return msg
