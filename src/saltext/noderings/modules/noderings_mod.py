"""
Manage NodeRings resources: virtual machines, DNS, firewalls and SSH keys.

`NodeRings <https://noderings.com>`_ is a cloud marketplace where independent
hosting providers sell virtual machines through one account and one API.

:depends: none beyond Salt
:configuration: The module needs a NodeRings service account token. Put it in
    the minion configuration or in pillar:

    .. code-block:: yaml

        noderings:
          token: <service account token>
          # optional
          organization_id: <organization id>
          api_url: https://api.noderings.com

    Without ``noderings:token``, the ``NODERINGS_TOKEN`` environment variable is
    used. Every function also accepts ``profile`` to read the settings from
    another key, for example ``profile=noderings_staging``.
"""

import logging

from salt.exceptions import CommandExecutionError
from salt.exceptions import SaltInvocationError

from saltext.noderings import __version__
from saltext.noderings.utils import compute
from saltext.noderings.utils import dns as dnsutil
from saltext.noderings.utils import firewall as fwutil
from saltext.noderings.utils.client import NodeRingsError
from saltext.noderings.utils.client import TimeoutError_
from saltext.noderings.utils.client import client_from_config
from saltext.noderings.utils.client import find_by_name
from saltext.noderings.utils.client import is_not_found
from saltext.noderings.utils.client import snake
from saltext.noderings.utils.client import trim_enum
from saltext.noderings.utils.marketplace import find_offers
from saltext.noderings.utils.marketplace import offer_view

log = logging.getLogger(__name__)

__virtualname__ = "noderings"

USER_AGENT = f"saltext-noderings/{__version__} (+https://github.com/noderings/saltext-noderings)"


def __virtual__():
    return __virtualname__


# -- plumbing -------------------------------------------------------------------


def _client(profile="noderings"):
    conf = __salt__["config.get"](profile, {}) or {}
    try:
        return client_from_config(conf, user_agent=USER_AGENT)
    except ValueError as exc:
        raise CommandExecutionError(str(exc)) from exc


def _call(what, func, *args, **kwargs):
    """Run an API call and turn its errors into Salt exceptions."""
    try:
        return func(*args, **kwargs)
    except compute.NodeRingsUsageError as exc:
        raise SaltInvocationError(str(exc)) from exc
    except (NodeRingsError, RuntimeError, TimeoutError_, OSError) as exc:
        raise CommandExecutionError(f"{what}: {exc}") from exc


def _zone(client, zone):
    """A zone by name or ID."""
    name = dnsutil.normalize_zone_name(zone)
    for z in _call("listing DNS zones", client.list_dns_zones):
        if zone == z.get("id") or dnsutil.normalize_zone_name(z.get("name")) == name:
            return z
    return None


def _require_zone(client, zone):
    z = _zone(client, zone)
    if not z:
        raise SaltInvocationError(
            f"DNS zone {zone} does not exist in this organization; create it with "
            "noderings.create_zone or the noderings.dns_zone_present state"
        )
    return z


def _zone_view(zone):
    out = snake(zone or {})
    out["state"] = trim_enum((zone or {}).get("state"), "DNS_ZONE_STATE_")
    out["dnssec_mode"] = trim_enum((zone or {}).get("dnssecMode"), "DNSSEC_MODE_")
    # The nameservers to set at the registrar.
    out["nameservers"] = list((zone or {}).get("assignedNameservers") or [])
    return out


def _record_view(zone_id, rel, rrtype, rec):
    if not rec:
        return {}
    return {
        "zone_id": zone_id,
        "name": rel,
        "type": rrtype,
        "ttl": int(rec.get("ttl") or 0),
        "values": [dnsutil.from_api(rrtype, v) for v in rec.get("rdata") or []],
    }


def _find_record(client, zone_id, rel, rrtype):
    """The RRset with exactly this name and type (the API's name filter matches substrings)."""
    recs = _call(
        "listing DNS records",
        client.list_dns_records,
        zone_id,
        "" if rel == "@" else rel,
        rrtype,
    )
    for rec in recs:
        if (
            dnsutil.relative_name(rec.get("name"), "") == rel
            and (rec.get("type") or "").upper() == rrtype
            and not rec.get("system")
        ):
            return rec
    return None


def _ruleset(client, ruleset):
    for rs in _call("listing firewall rulesets", client.list_firewall_rulesets):
        if ruleset in (rs.get("id"), rs.get("name")):
            return rs
    return None


def _vm(client, name):
    return _call(f"looking up virtual machine {name}", compute.find_vm, client, name)


def _require_vm(client, name):
    vm = _vm(client, name)
    if not vm:
        raise SaltInvocationError(f"no virtual machine is named {name}")
    return vm


# -- account --------------------------------------------------------------------


def get_organization(profile="noderings"):
    """
    Return the organization the token belongs to.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.get_organization
    """
    client = _client(profile)
    return snake(_call("reading the organization", client.get_organization) or {})


def get_credit_balance(profile="noderings"):
    """
    Return the organization's prepaid credit balance. Hourly (usage-billed)
    servers draw from it.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.get_credit_balance
    """
    client = _client(profile)
    return snake(_call("reading the credit balance", client.get_credit_balance))


# -- catalog --------------------------------------------------------------------


def list_distros(profile="noderings"):
    """
    List the operating systems a VM can be installed with, as the names
    ``create_vm`` takes (for example ``ubuntu_24_04``).

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.list_distros
    """
    client = _client(profile)
    out = []
    for d in _call("listing distros", client.list_distros):
        item = snake(d)
        item["name"] = trim_enum(d.get("distro"), "DISTRO_")
        out.append(item)
    return out


def list_regions(profile="noderings"):
    """
    List the cities with servers for sale.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.list_regions
    """
    client = _client(profile)
    return snake(_call("listing regions", client.list_marketplace_regions, "ASSET_TYPE_VM"))


def list_plans(country=None, city=None, billing_type=None, profile="noderings"):
    """
    List plan codes for sale, optionally in one city or of one billing type
    (``usage``, ``subscription`` or ``free``).

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.list_plans country="United States" city=Ashburn billing_type=usage
    """
    client = _client(profile)
    billing = {
        "usage": "BILLING_TYPE_USAGE",
        "subscription": "BILLING_TYPE_SUBSCRIPTION",
        "free": "BILLING_TYPE_FREE",
    }
    return snake(
        _call(
            "listing plans",
            client.list_available_plans,
            country,
            city,
            billing.get(billing_type or "", None),
            "ASSET_TYPE_VM",
        )
    )


def list_offers(
    plan_code,
    country,
    city,
    billing_type="usage",
    sort="price",
    require_capabilities=None,
    profile="noderings",
):
    """
    List the offers for a plan code in a city, best first. An offer is one
    provider's plan, with its prices. ``sort`` is ``price`` or ``rating``.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.list_offers SC-GC-1C-1G-20 "United States" Ashburn
    """
    client = _client(profile)
    offers, _ = _call(
        "listing offers",
        find_offers,
        client,
        plan_code,
        country,
        city,
        billing_type=billing_type,
        require_capabilities=require_capabilities or [],
        sort=sort,
    )
    return [offer_view(o) for o in offers]


# -- SSH keys -------------------------------------------------------------------


def list_ssh_keys(profile="noderings"):
    """
    List the organization's SSH keys.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.list_ssh_keys
    """
    client = _client(profile)
    return snake(_call("listing SSH keys", client.list_ssh_keys))


def get_ssh_key(name, profile="noderings"):
    """
    Return the SSH key with this name or ID, or ``None``.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.get_ssh_key laptop
    """
    client = _client(profile)
    for key in _call("listing SSH keys", client.list_ssh_keys):
        if name in (key.get("id"), key.get("name")):
            return snake(key)
    return None


def create_ssh_key(name, public_key, profile="noderings"):
    """
    Upload a public key. VMs get it as root's authorized key.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.create_ssh_key laptop "ssh-ed25519 AAAA... me@laptop"
    """
    client = _client(profile)
    return snake(_call(f"creating SSH key {name}", client.create_ssh_key, name, public_key.strip()))


def delete_ssh_key(name, profile="noderings"):
    """
    Delete an SSH key by name or ID. Returns ``False`` when it does not exist.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.delete_ssh_key laptop
    """
    client = _client(profile)
    key = get_ssh_key(name, profile)
    if not key:
        return False
    _call(f"deleting SSH key {name}", client.delete_ssh_key, key["id"])
    return True


# -- DNS ------------------------------------------------------------------------


def list_zones(profile="noderings"):
    """
    List DNS zones.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.list_zones
    """
    client = _client(profile)
    return [_zone_view(z) for z in _call("listing DNS zones", client.list_dns_zones)]


def get_zone(zone, profile="noderings"):
    """
    Return a DNS zone by name or ID, with its nameservers, or ``None``.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.get_zone example.com
    """
    client = _client(profile)
    z = _zone(client, zone)
    if not z:
        return None
    return _zone_view(_call(f"reading zone {zone}", client.get_dns_zone, z["id"]) or z)


def create_zone(zone, profile="noderings"):
    """
    Host a domain on NodeRings DNS. Set the returned nameservers at the
    registrar to make it live.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.create_zone example.com
    """
    client = _client(profile)
    name = dnsutil.normalize_zone_name(zone)
    created = _call(f"creating zone {name}", client.create_dns_zone, name) or {}
    # Read the zone back so the result always carries its nameservers.
    if created.get("id"):
        created = _call(f"reading zone {name}", client.get_dns_zone, created["id"]) or created
    return _zone_view(created)


def delete_zone(zone, force=False, profile="noderings"):
    """
    Delete a DNS zone and all its records. ``force=True`` also deletes a zone
    whose DS record is still at the registrar, which breaks resolution for
    validating resolvers. Returns ``False`` when it does not exist.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.delete_zone example.com
    """
    client = _client(profile)
    z = _zone(client, zone)
    if not z:
        return False
    _call(f"deleting zone {zone}", client.delete_dns_zone, z["id"], force)
    return True


def list_records(zone, profile="noderings"):
    """
    List the records of a zone, one entry per name and type.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.list_records example.com
    """
    client = _client(profile)
    z = _require_zone(client, zone)
    out = []
    for rec in _call("listing DNS records", client.list_dns_records, z["id"]):
        rrtype = (rec.get("type") or "").upper()
        item = _record_view(
            z["id"], dnsutil.relative_name(rec.get("name"), z.get("name")), rrtype, rec
        )
        item["system"] = bool(rec.get("system"))
        out.append(item)
    return out


def get_record(zone, name, rtype, profile="noderings"):
    """
    Return the records of one name and type, or ``None``. ``name`` is relative
    to the zone (``www``, ``@`` for the apex) or fully qualified.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.get_record example.com www A
    """
    client = _client(profile)
    z = _require_zone(client, zone)
    rrtype = rtype.upper()
    rel = dnsutil.relative_name(name, z.get("name"))
    return _record_view(z["id"], rel, rrtype, _find_record(client, z["id"], rel, rrtype)) or None


def set_record(zone, name, rtype, values, ttl=3600, profile="noderings"):
    """
    Create or replace the records of one name and type. ``values`` is a list;
    hostnames should end with a dot, and TXT values are plain text without
    quotes.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.set_record example.com www A '["192.0.2.10"]' ttl=300
        salt '*' noderings.set_record example.com @ MX '["10 mx1.example.net."]'
    """
    client = _client(profile)
    z = _require_zone(client, zone)
    rrtype = rtype.upper()
    if rrtype not in dnsutil.RECORD_TYPES:
        raise SaltInvocationError(
            f"unsupported record type {rtype}; use one of {', '.join(dnsutil.RECORD_TYPES)}"
        )
    if isinstance(values, str):
        values = [values]
    values = [str(v).strip() for v in values or []]
    err = dnsutil.validate_values(rrtype, values)
    if err:
        raise SaltInvocationError(f"{name} {rrtype}: {err}")
    ttl = int(ttl)
    if not 30 <= ttl <= 86400:
        raise SaltInvocationError("ttl must be between 30 and 86400 seconds")
    rel = dnsutil.relative_name(name, z.get("name"))
    _call(
        f"writing DNS record {rel} {rrtype}",
        client.upsert_dns_record,
        z["id"],
        {
            "name": rel,
            "type": rrtype,
            "ttl": ttl,
            "rdata": [dnsutil.to_api(rrtype, v) for v in values],
        },
    )
    return _record_view(z["id"], rel, rrtype, _find_record(client, z["id"], rel, rrtype))


def delete_record(zone, name, rtype, profile="noderings"):
    """
    Delete the records of one name and type. Returns ``False`` when there are
    none.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.delete_record example.com www A
    """
    client = _client(profile)
    z = _require_zone(client, zone)
    rrtype = rtype.upper()
    rel = dnsutil.relative_name(name, z.get("name"))
    if not _find_record(client, z["id"], rel, rrtype):
        return False
    try:
        client.delete_dns_record(z["id"], rel, rrtype)
    except NodeRingsError as exc:
        if not is_not_found(exc):
            raise CommandExecutionError(f"deleting DNS record {rel} {rrtype}: {exc}") from exc
    return True


def enable_dnssec(zone, profile="noderings"):
    """
    Sign a zone with DNSSEC and return the DS record to add at the registrar.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.enable_dnssec example.com
    """
    client = _client(profile)
    z = _require_zone(client, zone)
    _call(f"enabling DNSSEC on {zone}", client.enable_dnssec, z["id"])
    return snake(_call(f"reading DNSSEC of {zone}", client.get_dnssec, z["id"]))


def get_dnssec(zone, profile="noderings"):
    """
    Return the DNSSEC state of a zone, including its DS record.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.get_dnssec example.com
    """
    client = _client(profile)
    z = _require_zone(client, zone)
    return snake(_call(f"reading DNSSEC of {zone}", client.get_dnssec, z["id"]))


# -- firewall -------------------------------------------------------------------


def _rules_to_api(client, rules):
    if not isinstance(rules, list):
        raise SaltInvocationError("rules must be a list of rule mappings")
    names = set()
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict) or not rule.get("direction") or not rule.get("action"):
            raise SaltInvocationError(f"rule {i} needs at least direction and action")
        rule.setdefault("protocol", "tcp")
        err = fwutil.validate_rule(rule, i)
        if err:
            raise SaltInvocationError(err)
        for side in ("source", "destination"):
            names.update((rule.get(side) or {}).get("address_groups") or [])
    groups = {}
    if names:
        existing = _call("listing address groups", client.list_firewall_address_groups)
        for name in names:
            match = find_by_name(existing, name)
            if not match:
                raise SaltInvocationError(f"no firewall address group is named {name}")
            groups[name] = match["id"]
    if len(rules) > fwutil.MAX_RULES:
        raise SaltInvocationError(f"a ruleset holds at most {fwutil.MAX_RULES} rules")
    return [fwutil.rule_to_api(r, groups) for r in rules]


def list_rulesets(profile="noderings"):
    """
    List firewall rulesets.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.list_rulesets
    """
    client = _client(profile)
    return snake(_call("listing firewall rulesets", client.list_firewall_rulesets))


def get_ruleset(ruleset, profile="noderings"):
    """
    Return a firewall ruleset by name or ID with its rules in order, or ``None``.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.get_ruleset web
    """
    client = _client(profile)
    rs = _ruleset(client, ruleset)
    if not rs:
        return None
    out = snake(rs)
    out["rules"] = [
        fwutil.rule_view(r) for r in _call("listing rules", client.list_firewall_rules, rs["id"])
    ]
    return out


def create_ruleset(ruleset, profile="noderings"):
    """
    Create an empty firewall ruleset.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.create_ruleset web
    """
    client = _client(profile)
    return snake(_call(f"creating ruleset {ruleset}", client.create_firewall_ruleset, ruleset))


def set_rules(ruleset, rules, profile="noderings"):
    """
    Make a ruleset hold exactly ``rules``, in this order. Rules run top to
    bottom; traffic that matches no rule is accepted, so end with a drop rule.

    Each rule is a mapping with ``direction`` (``ingress`` or ``egress``),
    ``action`` (``accept`` or ``drop``), ``protocol`` (default ``tcp``),
    ``source`` and ``destination`` (each with ``cidrs``, ``address_groups``,
    ``address_group_ids`` or ``vm_tags``), and ``destination_ports`` or
    ``source_ports`` (one port such as ``443`` or one range such as
    ``8000-8100``).

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.set_rules web '[{"direction": "ingress", "action": "accept", "protocol": "tcp", "destination_ports": "22", "source": {"cidrs": ["0.0.0.0/0"]}}]'
    """
    client = _client(profile)
    rs = _ruleset(client, ruleset)
    if not rs:
        raise SaltInvocationError(f"no firewall ruleset is named {ruleset}")
    desired = _rules_to_api(client, rules)
    final = _call(f"writing rules of {ruleset}", fwutil.reconcile, client, rs["id"], desired)
    return [fwutil.rule_view(r) for r in final]


def delete_ruleset(ruleset, force=False, profile="noderings"):
    """
    Delete a firewall ruleset. ``force=True`` also deletes one that is still
    attached to VMs. Returns ``False`` when it does not exist.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.delete_ruleset web
    """
    client = _client(profile)
    rs = _ruleset(client, ruleset)
    if not rs:
        return False
    _call(f"deleting ruleset {ruleset}", client.delete_firewall_ruleset, rs["id"], force)
    return True


# -- virtual machines -----------------------------------------------------------


def list_vms(tags=None, profile="noderings"):
    """
    List virtual machines, optionally only those with all of ``tags``.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.list_vms
        salt '*' noderings.list_vms tags='["web"]'
    """
    client = _client(profile)
    want = set(tags or [])
    return [
        compute.vm_view(v)
        for v in _call("listing virtual machines", client.list_virtual_machines)
        if want.issubset(set(v.get("tags") or []))
    ]


def get_vm(name, profile="noderings"):
    """
    Return a virtual machine by hostname or ID, or ``None``.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.get_vm web-1
    """
    client = _client(profile)
    return compute.vm_view(_vm(client, name)) or None


def create_vm(
    name,
    plan_code=None,
    country=None,
    city=None,
    distro="ubuntu_24_04",
    billing_type="usage",
    ssh_keys=None,
    tags=None,
    firewall_ruleset=None,
    password=None,
    plan_id=None,
    region_id=None,
    provider_id=None,
    hosting_provider=None,
    sort="price",
    wait=True,
    wait_timeout=900,
    profile="noderings",
):
    """
    Order a virtual machine and return it once it is running.

    Give either ``plan_code``, ``country`` and ``city`` (the cheapest matching
    offer is ordered, or the best rated with ``sort=rating``), or ``plan_id``
    and ``region_id`` from ``noderings.list_offers``. ``billing_type=usage``
    bills by the hour from prepaid credit and stops when the VM is deleted.

    ``hosting_provider`` orders only from one hosting provider: its provider ID,
    its storefront slug (such as ``acme-hosting``) or its name (``Acme Hosting``,
    any case). A name that more than one provider uses is an error; give the ID
    or slug then. ``provider_id`` takes precedence.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.create_vm web-1 plan_code=SC-GC-1C-1G-20 country="United States" city=Ashburn ssh_keys='["laptop"]'
        salt '*' noderings.create_vm web-1 plan_code=SC-GC-1C-1G-20 country="United States" city=Ashburn hosting_provider=acme-hosting ssh_keys='["laptop"]'
    """
    client = _client(profile)
    if _vm(client, name):
        raise SaltInvocationError(f"a virtual machine named {name} already exists")
    ruleset_id = _call(
        "resolving firewall ruleset", compute.resolve_ruleset, client, firewall_ruleset
    )
    if not plan_id:
        if not (plan_code and country and city):
            raise SaltInvocationError("give plan_code, country and city, or plan_id and region_id")
        caps = ["guest_firewall"] if ruleset_id else []
        plan_id, region_id, _ = _call(
            "choosing an offer",
            compute.choose_offer,
            client,
            plan_code,
            country,
            city,
            billing_type=billing_type,
            provider_id=provider_id,
            require_capabilities=caps,
            sort=sort,
            provider=hosting_provider,
        )
    elif not region_id:
        raise SaltInvocationError("plan_id needs region_id")
    key_ids = _call("resolving SSH keys", compute.resolve_ssh_keys, client, ssh_keys)
    vm = _call(
        f"ordering virtual machine {name}",
        compute.order_vm,
        client,
        name,
        plan_id,
        region_id,
        distro=distro,
        ssh_key_ids=key_ids,
        password=password,
        tags=tags,
        ruleset_id=ruleset_id,
        wait=wait,
        timeout=int(wait_timeout),
    )
    return compute.vm_view(vm)


def delete_vm(name, profile="noderings"):
    """
    Delete a virtual machine. An hourly VM stops billing; a monthly
    subscription is cancelled without a refund. Returns ``False`` when it does
    not exist.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.delete_vm web-1
    """
    client = _client(profile)
    vm = _vm(client, name)
    if not vm:
        return False
    _call(f"deleting virtual machine {name}", client.delete_virtual_machine, vm["id"])
    return True


def _power(name, want, wait, wait_timeout, profile):
    client = _client(profile)
    vm = _require_vm(client, name)
    return compute.vm_view(
        _call(
            f"changing power of {name}",
            compute.set_power,
            client,
            vm["id"],
            want,
            wait,
            int(wait_timeout),
        )
    )


def start_vm(name, wait=True, wait_timeout=900, profile="noderings"):
    """
    Start a stopped virtual machine.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.start_vm web-1
    """
    return _power(name, "running", wait, wait_timeout, profile)


def stop_vm(name, wait=True, wait_timeout=900, profile="noderings"):
    """
    Stop a virtual machine. Its disk and IP addresses are kept.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.stop_vm web-1
    """
    return _power(name, "stopped", wait, wait_timeout, profile)


def restart_vm(name, wait=True, wait_timeout=900, profile="noderings"):
    """
    Restart a virtual machine.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.restart_vm web-1
    """
    return _power(name, "restarted", wait, wait_timeout, profile)


def reinstall_vm(
    name, distro, ssh_keys=None, password=None, wait=True, wait_timeout=900, profile="noderings"
):
    """
    Reinstall a virtual machine with another operating system. This erases the
    disk. The VM keeps its IP addresses.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.reinstall_vm web-1 debian_13 ssh_keys='["laptop"]'
    """
    client = _client(profile)
    vm = _require_vm(client, name)
    key_ids = _call("resolving SSH keys", compute.resolve_ssh_keys, client, ssh_keys)
    return compute.vm_view(
        _call(
            f"reinstalling {name}",
            compute.reinstall,
            client,
            vm["id"],
            vm.get("hostname"),
            distro,
            ssh_key_ids=key_ids,
            password=password,
            wait=wait,
            timeout=int(wait_timeout),
        )
    )


def attach_ruleset(name, ruleset, wait=True, wait_timeout=900, profile="noderings"):
    """
    Put a firewall ruleset in front of a virtual machine, replacing any other.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.attach_ruleset web-1 web
    """
    client = _client(profile)
    vm = _require_vm(client, name)
    ruleset_id = _call("resolving firewall ruleset", compute.resolve_ruleset, client, ruleset)
    if vm.get("firewallRulesetId") == ruleset_id:
        return compute.vm_view(vm)
    if vm.get("firewallRulesetId"):
        _call(
            "detaching the current firewall",
            compute.detach_firewall,
            client,
            vm["id"],
            vm["firewallRulesetId"],
            wait,
            int(wait_timeout),
        )
    return compute.vm_view(
        _call(
            "attaching firewall",
            compute.attach_firewall,
            client,
            vm["id"],
            ruleset_id,
            wait,
            int(wait_timeout),
        )
    )


def detach_ruleset(name, wait=True, wait_timeout=900, profile="noderings"):
    """
    Remove the firewall ruleset from a virtual machine.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.detach_ruleset web-1
    """
    client = _client(profile)
    vm = _require_vm(client, name)
    if not vm.get("firewallRulesetId"):
        return compute.vm_view(vm)
    return compute.vm_view(
        _call(
            "detaching firewall",
            compute.detach_firewall,
            client,
            vm["id"],
            vm["firewallRulesetId"],
            wait,
            int(wait_timeout),
        )
    )


def set_tags(name, tags, profile="noderings"):
    """
    Make a virtual machine carry exactly ``tags``.

    CLI Example:

    .. code-block:: bash

        salt '*' noderings.set_tags web-1 '["web", "prod"]'
    """
    client = _client(profile)
    vm = _require_vm(client, name)
    have = set(compute.vm_view(vm)["tags"])
    want = set(tags or [])
    if have != want:
        _call(
            f"updating tags of {name}",
            client.update_virtual_machine_tags,
            vm["id"],
            sorted(want - have),
            sorted(have - want),
        )
    return compute.vm_view(_call(f"reading {name}", client.get_virtual_machine, vm["id"]))
