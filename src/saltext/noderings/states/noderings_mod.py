"""
Declare NodeRings resources in Salt states: SSH keys, DNS zones and records,
firewall rulesets and virtual machines.

Every state supports ``test=True``. Credentials come from ``noderings:token``
in the minion configuration or pillar, or the ``NODERINGS_TOKEN`` environment
variable; see :py:mod:`the execution module <saltext.noderings.modules.noderings_mod>`.

.. code-block:: yaml

    laptop-key:
      noderings.ssh_key_present:
        - name: laptop
        - public_key: ssh-ed25519 AAAA... me@laptop

    example.com:
      noderings.dns_zone_present

    web-1:
      noderings.vm_present:
        - plan_code: SC-GC-1C-1G-20
        - country: United States
        - city: Ashburn
        - distro: ubuntu_24_04
        - ssh_keys: [laptop]
        - firewall_ruleset: web
        - require:
          - noderings: laptop-key
          - noderings: web-firewall

    www:
      noderings.dns_record_present:
        - zone: example.com
        - rtype: A
        - values:
          - {{ salt['noderings.get_vm']('web-1')['ipv4'] }}
"""

import logging

from salt.exceptions import CommandExecutionError
from salt.exceptions import SaltInvocationError

from saltext.noderings.utils import dns as dnsutil
from saltext.noderings.utils import firewall as fwutil
from saltext.noderings.utils.client import client_from_config
from saltext.noderings.utils.client import find_by_name

log = logging.getLogger(__name__)

__virtualname__ = "noderings"


def __virtual__():
    if "noderings.get_vm" not in __salt__:
        return False, "The noderings execution module is not available"
    return __virtualname__


def _ret(name):
    return {"name": name, "result": True, "changes": {}, "comment": ""}


def _fail(ret, exc):
    ret["result"] = False
    ret["comment"] = str(exc)
    return ret


def _test():
    return bool(__opts__.get("test"))


# -- SSH keys -------------------------------------------------------------------


def ssh_key_present(name, public_key, profile="noderings"):
    """
    Ensure an SSH key with this name holds ``public_key``. A key with the same
    name but another public key is replaced.

    name
        Key name.

    public_key
        The public key, for example ``ssh-ed25519 AAAA... me@laptop``.
    """
    ret = _ret(name)
    want = " ".join(public_key.split()[:2])
    try:
        have = __salt__["noderings.get_ssh_key"](name, profile=profile)
        if have and " ".join((have.get("public_key") or "").split()[:2]) == want:
            ret["comment"] = f"SSH key {name} is up to date"
            return ret
        if _test():
            ret["result"] = None
            ret["comment"] = f"SSH key {name} would be {'replaced' if have else 'created'}"
            ret["changes"] = {"old": have, "new": {"name": name, "public_key": public_key}}
            return ret
        if have:
            __salt__["noderings.delete_ssh_key"](have["id"], profile=profile)
        new = __salt__["noderings.create_ssh_key"](name, public_key, profile=profile)
    except (CommandExecutionError, SaltInvocationError) as exc:
        return _fail(ret, exc)
    ret["changes"] = {"old": have, "new": new}
    ret["comment"] = f"SSH key {name} {'replaced' if have else 'created'}"
    return ret


def ssh_key_absent(name, profile="noderings"):
    """
    Ensure no SSH key has this name.
    """
    ret = _ret(name)
    try:
        have = __salt__["noderings.get_ssh_key"](name, profile=profile)
        if not have:
            ret["comment"] = f"SSH key {name} is already absent"
            return ret
        if _test():
            ret["result"] = None
            ret["comment"] = f"SSH key {name} would be deleted"
            ret["changes"] = {"old": have, "new": None}
            return ret
        __salt__["noderings.delete_ssh_key"](have["id"], profile=profile)
    except (CommandExecutionError, SaltInvocationError) as exc:
        return _fail(ret, exc)
    ret["changes"] = {"old": have, "new": None}
    ret["comment"] = f"SSH key {name} deleted"
    return ret


# -- DNS ------------------------------------------------------------------------


def dns_zone_present(name, dnssec=False, profile="noderings"):
    """
    Ensure a DNS zone exists, and optionally that it is signed with DNSSEC.
    Set the zone's nameservers (in the ``nameservers`` change) at the registrar.

    name
        Domain, for example ``example.com``.

    dnssec
        Sign the zone. Add the DS record from ``noderings.get_dnssec`` at the
        registrar afterwards. Turning signing off is left to
        ``noderings.delete_zone`` or the console, because doing it while the
        registrar has the DS record breaks the domain.
    """
    ret = _ret(name)
    try:
        zone = __salt__["noderings.get_zone"](name, profile=profile)
        changes = {}
        if not zone:
            if _test():
                ret["result"] = None
                ret["comment"] = f"DNS zone {name} would be created"
                ret["changes"] = {"old": None, "new": {"name": name, "dnssec": dnssec}}
                return ret
            zone = __salt__["noderings.create_zone"](name, profile=profile)
            changes["created"] = {"id": zone.get("id"), "nameservers": zone.get("nameservers")}
        if dnssec and zone.get("dnssec_mode") in ("", "unsigned", None):
            if _test():
                ret["result"] = None
                ret["comment"] = f"DNSSEC would be enabled on {name}"
                ret["changes"] = {"dnssec": {"old": "unsigned", "new": "signed"}}
                return ret
            sec = __salt__["noderings.enable_dnssec"](name, profile=profile)
            changes["dnssec"] = {"old": "unsigned", "new": sec.get("ds") or sec}
    except (CommandExecutionError, SaltInvocationError) as exc:
        return _fail(ret, exc)
    ret["changes"] = changes
    ret["comment"] = f"DNS zone {name} {'updated' if changes else 'is up to date'}"
    return ret


def dns_zone_absent(name, force=False, profile="noderings"):
    """
    Ensure a DNS zone does not exist. Deleting it deletes all its records.
    """
    ret = _ret(name)
    try:
        zone = __salt__["noderings.get_zone"](name, profile=profile)
        if not zone:
            ret["comment"] = f"DNS zone {name} is already absent"
            return ret
        if _test():
            ret["result"] = None
            ret["comment"] = f"DNS zone {name} would be deleted"
            ret["changes"] = {"old": zone.get("name"), "new": None}
            return ret
        __salt__["noderings.delete_zone"](name, force=force, profile=profile)
    except (CommandExecutionError, SaltInvocationError) as exc:
        return _fail(ret, exc)
    ret["changes"] = {"old": zone.get("name"), "new": None}
    ret["comment"] = f"DNS zone {name} deleted"
    return ret


def dns_record_present(name, zone, rtype, values, ttl=3600, profile="noderings"):
    """
    Ensure the records of one name and type hold exactly ``values``.

    name
        Record name relative to the zone (``www``, ``@`` for the apex), or
        fully qualified.

    zone
        Zone name or ID.

    rtype
        Record type, for example ``A``, ``AAAA``, ``CNAME``, ``MX`` or ``TXT``.

    values
        List of values. Hostnames should end with a dot. TXT values are plain
        text without quotes.

    ttl
        Time to live in seconds, 30 to 86400.
    """
    ret = _ret(f"{name} {rtype.upper()}")
    rrtype = rtype.upper()
    if isinstance(values, str):
        values = [values]
    try:
        have = __salt__["noderings.get_record"](zone, name, rrtype, profile=profile)
        if (
            have
            and have.get("ttl") == int(ttl)
            and dnsutil.same_values(rrtype, have.get("values"), values)
        ):
            ret["comment"] = f"DNS record {name} {rrtype} is up to date"
            return ret
        new = {"ttl": int(ttl), "values": list(values)}
        old = {"ttl": have.get("ttl"), "values": have.get("values")} if have else None
        if _test():
            ret["result"] = None
            ret["comment"] = (
                f"DNS record {name} {rrtype} would be {'updated' if have else 'created'}"
            )
            ret["changes"] = {"old": old, "new": new}
            return ret
        __salt__["noderings.set_record"](zone, name, rrtype, values, ttl=ttl, profile=profile)
    except (CommandExecutionError, SaltInvocationError) as exc:
        return _fail(ret, exc)
    ret["changes"] = {"old": old, "new": new}
    ret["comment"] = f"DNS record {name} {rrtype} {'updated' if have else 'created'}"
    return ret


def dns_record_absent(name, zone, rtype, profile="noderings"):
    """
    Ensure there are no records of this name and type. A missing zone counts
    as absent.
    """
    ret = _ret(f"{name} {rtype.upper()}")
    rrtype = rtype.upper()
    try:
        if not __salt__["noderings.get_zone"](zone, profile=profile):
            ret["comment"] = f"DNS zone {zone} does not exist, so neither does the record"
            return ret
        have = __salt__["noderings.get_record"](zone, name, rrtype, profile=profile)
        if not have:
            ret["comment"] = f"DNS record {name} {rrtype} is already absent"
            return ret
        if _test():
            ret["result"] = None
            ret["comment"] = f"DNS record {name} {rrtype} would be deleted"
            ret["changes"] = {"old": have.get("values"), "new": None}
            return ret
        __salt__["noderings.delete_record"](zone, name, rrtype, profile=profile)
    except (CommandExecutionError, SaltInvocationError) as exc:
        return _fail(ret, exc)
    ret["changes"] = {"old": have.get("values"), "new": None}
    ret["comment"] = f"DNS record {name} {rrtype} deleted"
    return ret


# -- firewall -------------------------------------------------------------------


def _desired_rules(client, rules):
    names = set()
    for i, rule in enumerate(rules):
        rule = dict(rule)
        rule.setdefault("protocol", "tcp")
        err = fwutil.validate_rule(rule, i)
        if err:
            raise SaltInvocationError(err)
        for side in ("source", "destination"):
            names.update((rule.get(side) or {}).get("address_groups") or [])
    groups = {}
    if names:
        existing = client.list_firewall_address_groups()
        for gname in names:
            match = find_by_name(existing, gname)
            if not match:
                raise SaltInvocationError(f"no firewall address group is named {gname}")
            groups[gname] = match["id"]
    return [fwutil.rule_to_api(dict(r, protocol=r.get("protocol") or "tcp"), groups) for r in rules]


def firewall_ruleset_present(name, rules=None, profile="noderings"):
    """
    Ensure a firewall ruleset exists and, when ``rules`` is given, holds
    exactly these rules in this order.

    Rules run top to bottom, and traffic that matches no rule is accepted, so
    end the list with a drop rule. Each rule is a mapping with ``direction``
    (``ingress`` or ``egress``), ``action`` (``accept`` or ``drop``),
    ``protocol`` (default ``tcp``), ``source`` and ``destination`` (each with
    ``cidrs``, ``address_groups``, ``address_group_ids`` or ``vm_tags``), and
    ``destination_ports`` / ``source_ports`` (one port such as ``443`` or one
    range such as ``8000-8100``).

    .. code-block:: yaml

        web-firewall:
          noderings.firewall_ruleset_present:
            - name: web
            - rules:
              - {direction: ingress, action: accept, protocol: tcp, destination_ports: "22", source: {cidrs: [198.51.100.0/24]}}
              - {direction: ingress, action: accept, protocol: tcp, destination_ports: "443", source: {cidrs: [0.0.0.0/0]}}
              - {direction: ingress, action: drop, protocol: any, source: {cidrs: [0.0.0.0/0]}}
    """
    ret = _ret(name)
    try:
        client = client_from_config(__salt__["config.get"](profile, {}) or {})
        desired = _desired_rules(client, rules) if rules is not None else None
        ruleset = find_by_name(client.list_firewall_rulesets(), name)
        current = client.list_firewall_rules(ruleset["id"]) if ruleset else []
        create = not ruleset
        rewrite = desired is not None and fwutil.plan_rules(current, desired)
        if not create and not rewrite:
            ret["comment"] = f"Firewall ruleset {name} is up to date"
            return ret
        old = [fwutil.rule_view(r) for r in current] if ruleset else None
        new = [fwutil.rule_view(d) for d in desired] if desired is not None else []
        if _test():
            ret["result"] = None
            ret["comment"] = (
                f"Firewall ruleset {name} would be {'created' if create else 'updated'}"
            )
            ret["changes"] = {"old": old, "new": new}
            return ret
        if create:
            ruleset = client.create_firewall_ruleset(name)
        if desired is not None:
            final = fwutil.reconcile(client, ruleset["id"], desired)
            new = [fwutil.rule_view(r) for r in final]
    except (ValueError, SaltInvocationError) as exc:
        return _fail(ret, exc)
    except Exception as exc:  # pylint: disable=broad-except
        return _fail(ret, f"writing firewall ruleset {name}: {exc}")
    ret["changes"] = {"old": old, "new": new}
    ret["comment"] = f"Firewall ruleset {name} {'created' if create else 'updated'}"
    return ret


def firewall_ruleset_absent(name, force=False, profile="noderings"):
    """
    Ensure a firewall ruleset does not exist. ``force=True`` deletes it even
    while it is attached to virtual machines.
    """
    ret = _ret(name)
    try:
        have = __salt__["noderings.get_ruleset"](name, profile=profile)
        if not have:
            ret["comment"] = f"Firewall ruleset {name} is already absent"
            return ret
        if _test():
            ret["result"] = None
            ret["comment"] = f"Firewall ruleset {name} would be deleted"
            ret["changes"] = {"old": have.get("name"), "new": None}
            return ret
        __salt__["noderings.delete_ruleset"](name, force=force, profile=profile)
    except (CommandExecutionError, SaltInvocationError) as exc:
        return _fail(ret, exc)
    ret["changes"] = {"old": have.get("name"), "new": None}
    ret["comment"] = f"Firewall ruleset {name} deleted"
    return ret


# -- virtual machines -----------------------------------------------------------


def vm_present(
    name,
    plan_code=None,
    country=None,
    city=None,
    distro="ubuntu_24_04",
    billing_type="usage",
    ssh_keys=None,
    tags=None,
    firewall_ruleset=None,
    power_state="running",
    allow_reinstall=False,
    plan_id=None,
    region_id=None,
    provider_id=None,
    password=None,
    wait_timeout=900,
    profile="noderings",
):
    """
    Ensure a virtual machine with this hostname exists, in the given state.

    A new VM is ordered from the cheapest offer for ``plan_code`` in
    ``country`` and ``city`` (or exactly ``plan_id`` and ``region_id``).
    Ordering costs money: ``billing_type=usage`` bills by the hour from prepaid
    credit. For an existing VM, ``tags``, ``firewall_ruleset`` and
    ``power_state`` are updated in place. A different ``distro`` reinstalls the
    VM only with ``allow_reinstall=True``, because reinstalling erases the
    disk; otherwise the state reports the difference and fails. The plan and
    region of an existing VM are never changed.

    name
        Hostname. Must be unique in the organization.

    power_state
        ``running`` or ``stopped``.

    firewall_ruleset
        Name or ID of a ruleset to attach. ``""`` detaches the current one;
        ``None`` (the default) leaves it alone.
    """
    ret = _ret(name)
    if power_state not in ("running", "stopped"):
        return _fail(ret, "power_state must be running or stopped")
    try:
        vm = __salt__["noderings.get_vm"](name, profile=profile)
        if not vm:
            if _test():
                ret["result"] = None
                ret["comment"] = f"Virtual machine {name} would be ordered"
                ret["changes"] = {
                    "old": None,
                    "new": {
                        "hostname": name,
                        "plan_code": plan_code,
                        "plan_id": plan_id,
                        "distro": distro,
                    },
                }
                return ret
            new = __salt__["noderings.create_vm"](
                name,
                plan_code=plan_code,
                country=country,
                city=city,
                distro=distro,
                billing_type=billing_type,
                ssh_keys=ssh_keys,
                tags=tags,
                firewall_ruleset=firewall_ruleset or None,
                password=password,
                plan_id=plan_id,
                region_id=region_id,
                provider_id=provider_id,
                wait_timeout=wait_timeout,
                profile=profile,
            )
            if power_state == "stopped":
                new = __salt__["noderings.stop_vm"](
                    name, wait_timeout=wait_timeout, profile=profile
                )
            ret["changes"] = {
                "old": None,
                "new": {"id": new.get("id"), "ipv4": new.get("ipv4"), "ipv6": new.get("ipv6")},
            }
            ret["comment"] = f"Virtual machine {name} ordered"
            return ret

        changes = {}
        todo = []
        if distro and vm.get("distro") and vm["distro"] != distro:
            if not allow_reinstall:
                return _fail(
                    ret,
                    f"Virtual machine {name} runs {vm['distro']}, not {distro}. Set "
                    "allow_reinstall: True to reinstall it, which erases the disk.",
                )
            changes["distro"] = {"old": vm["distro"], "new": distro}
            todo.append(
                lambda: __salt__["noderings.reinstall_vm"](
                    name,
                    distro,
                    ssh_keys=ssh_keys,
                    password=password,
                    wait_timeout=wait_timeout,
                    profile=profile,
                )
            )
        if tags is not None and sorted(set(tags)) != sorted(vm.get("tags") or []):
            changes["tags"] = {"old": vm.get("tags"), "new": sorted(set(tags))}
            todo.append(lambda: __salt__["noderings.set_tags"](name, tags, profile=profile))
        if firewall_ruleset is not None:
            have_rs = vm.get("firewall_ruleset_id") or ""
            want_rs = ""
            if firewall_ruleset:
                rs = __salt__["noderings.get_ruleset"](firewall_ruleset, profile=profile)
                if not rs:
                    return _fail(ret, f"no firewall ruleset is named {firewall_ruleset}")
                want_rs = rs["id"]
            if have_rs != want_rs:
                changes["firewall_ruleset_id"] = {"old": have_rs or None, "new": want_rs or None}
                if want_rs:
                    todo.append(
                        lambda: __salt__["noderings.attach_ruleset"](
                            name, want_rs, wait_timeout=wait_timeout, profile=profile
                        )
                    )
                else:
                    todo.append(
                        lambda: __salt__["noderings.detach_ruleset"](
                            name, wait_timeout=wait_timeout, profile=profile
                        )
                    )
        if vm.get("power_state") != power_state:
            changes["power_state"] = {"old": vm.get("power_state"), "new": power_state}
            func = "noderings.start_vm" if power_state == "running" else "noderings.stop_vm"
            todo.append(lambda: __salt__[func](name, wait_timeout=wait_timeout, profile=profile))
        if not changes:
            ret["comment"] = f"Virtual machine {name} is up to date"
            return ret
        if _test():
            ret["result"] = None
            ret["comment"] = f"Virtual machine {name} would be updated"
            ret["changes"] = changes
            return ret
        for step in todo:
            step()
    except (CommandExecutionError, SaltInvocationError) as exc:
        return _fail(ret, exc)
    ret["changes"] = changes
    ret["comment"] = f"Virtual machine {name} updated"
    return ret


def vm_absent(name, profile="noderings"):
    """
    Ensure no virtual machine has this hostname. An hourly VM stops billing; a
    monthly subscription is cancelled without a refund.
    """
    ret = _ret(name)
    try:
        vm = __salt__["noderings.get_vm"](name, profile=profile)
        if not vm:
            ret["comment"] = f"Virtual machine {name} is already absent"
            return ret
        if _test():
            ret["result"] = None
            ret["comment"] = f"Virtual machine {name} would be deleted"
            ret["changes"] = {"old": vm.get("id"), "new": None}
            return ret
        __salt__["noderings.delete_vm"](name, profile=profile)
    except (CommandExecutionError, SaltInvocationError) as exc:
        return _fail(ret, exc)
    ret["changes"] = {"old": vm.get("id"), "new": None}
    ret["comment"] = f"Virtual machine {name} deleted"
    return ret
