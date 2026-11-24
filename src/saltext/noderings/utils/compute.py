"""
Virtual machine helpers shared by the execution module, the state module and
the Salt Cloud driver.
"""

import time

from saltext.noderings.utils.client import NodeRingsError
from saltext.noderings.utils.client import TimeoutError_
from saltext.noderings.utils.client import Waiter
from saltext.noderings.utils.client import is_ambiguous
from saltext.noderings.utils.client import is_not_found
from saltext.noderings.utils.client import is_uuid
from saltext.noderings.utils.client import new_uuid
from saltext.noderings.utils.client import snake
from saltext.noderings.utils.client import trim_enum
from saltext.noderings.utils.marketplace import find_offers
from saltext.noderings.utils.marketplace import no_offer_message
from saltext.noderings.utils.marketplace import offer_view

NONCE_TAG_PREFIX = "salt-create-"
DEFAULT_WAIT_TIMEOUT = 900


class NodeRingsUsageError(Exception):
    """The caller asked for something that cannot work (bad name, ambiguous match)."""


def power_state(vm):
    op = (vm or {}).get("operationStatus") or ""
    if op in ("OPERATION_STATUS_STARTED", "OPERATION_STATUS_RESTARTED"):
        return "running"
    if op == "OPERATION_STATUS_STOPPED":
        return "stopped"
    return trim_enum(op, "OPERATION_STATUS_") or "unknown"


def vm_view(vm):
    """The VM as Salt returns it: snake_case keys plus a few friendly fields."""
    if not vm:
        return {}
    out = snake(vm)
    out["power_state"] = power_state(vm)
    out["distro"] = trim_enum(vm.get("distro"), "DISTRO_")
    out["billing_type"] = trim_enum(vm.get("planBillingType"), "BILLING_TYPE_")
    out["provisioning_status"] = trim_enum(vm.get("crStatus"), "CR_STATUS_")
    out["tags"] = sorted(t for t in vm.get("tags") or [] if not t.startswith(NONCE_TAG_PREFIX))
    return out


def find_vm(client, name_or_id):
    """Return the VM with this ID or unique hostname, or None."""
    if is_uuid(name_or_id):
        try:
            return client.get_virtual_machine(name_or_id)
        except NodeRingsError as exc:
            if is_not_found(exc):
                return None
            raise
    matches = [v for v in client.list_virtual_machines() if v.get("hostname") == name_or_id]
    if len(matches) > 1:
        raise NodeRingsUsageError(
            f"{len(matches)} virtual machines are named {name_or_id}; use the ID of the one you "
            f"mean: {', '.join(v.get('id') for v in matches)}"
        )
    if not matches:
        return None
    # List entries omit details such as add-on subscriptions; read the full VM.
    return client.get_virtual_machine(matches[0]["id"])


def translate_create_error(err):
    msg = str(err)
    low = msg.lower()
    if "payment method" in low:
        msg += (
            ". Add a payment method to the organization in the NodeRings console. Paid plans, "
            "including usage-billed ones, need one."
        )
    elif "runway" in low or "insufficient" in low:
        msg += ". Top up the organization's credit balance, or choose a cheaper plan."
    elif "no vm template" in low:
        msg += (
            ". This provider has no image for the chosen distro in this region. Pick another "
            "distro, or another offer."
        )
    elif "capacity" in low or "marketplace-ready node" in low:
        msg += ". The region is out of capacity for this plan. Try another provider or city."
    if isinstance(err, NodeRingsError) and err.is_quota():
        msg += (
            ". The organization has reached its virtual machine quota. Delete unused VMs or ask "
            "NodeRings support to raise the limit."
        )
    return msg


def choose_offer(
    client,
    plan_code,
    country,
    city,
    billing_type="usage",
    provider_id=None,
    require_capabilities=None,
    require_ipv6=False,
    sort="price",
):
    """Return ``(plan_id, region_id, offer)`` for the best matching offer."""
    offers, located = find_offers(
        client,
        plan_code,
        country,
        city,
        billing_type=billing_type,
        provider_id=provider_id,
        require_capabilities=require_capabilities or [],
        require_ipv6=require_ipv6,
        sort=sort,
    )
    if not offers:
        raise NodeRingsUsageError(no_offer_message(plan_code, country, city, billing_type, located))
    best = offers[0]
    return best["plan_id"], best["region_id"], offer_view(best)


def resolve_ssh_keys(client, keys):
    """SSH key names or IDs -> IDs."""
    if not keys:
        return []
    existing = client.list_ssh_keys()
    out = []
    for key in keys:
        match = next((k for k in existing if k.get("id") == key or k.get("name") == key), None)
        if not match:
            raise NodeRingsUsageError(
                f"no SSH key is named {key}; create it with noderings.create_ssh_key or the "
                f"noderings.ssh_key_present state"
            )
        out.append(match["id"])
    return out


def resolve_ruleset(client, ruleset):
    """A firewall ruleset name or ID -> ID."""
    if not ruleset:
        return ""
    for rs in client.list_firewall_rulesets():
        if ruleset in (rs.get("id"), rs.get("name")):
            return rs["id"]
    raise NodeRingsUsageError(f"no firewall ruleset is named {ruleset}")


def wait_provisioned(client, vm_id, timeout=DEFAULT_WAIT_TIMEOUT, sleep=time.sleep):
    def check():
        vm = client.get_virtual_machine(vm_id)
        status = vm.get("crStatus")
        if status == "CR_STATUS_APPLIED":
            return True, vm
        if status == "CR_STATUS_FAILED":
            raise RuntimeError(
                f"provisioning of virtual machine {vm_id} failed. The API does not report why; "
                "delete it and try another offer, or contact the provider"
            )
        return False, None

    return Waiter(timeout, sleep=sleep).wait(f"virtual machine {vm_id} to be provisioned", check)


def wait_idle(client, vm_id, check_vm, what, timeout=DEFAULT_WAIT_TIMEOUT, sleep=time.sleep):
    def check():
        vm = client.get_virtual_machine(vm_id)
        return (check_vm(vm) and vm.get("crStatus") != "CR_STATUS_PROCESSING"), vm

    return Waiter(timeout, sleep=sleep).wait(what, check)


def wait_settled(client, vm_id, timeout=DEFAULT_WAIT_TIMEOUT, sleep=time.sleep):
    """Wait while the VM is in a transitional power state (created, reinstalling)."""
    return wait_idle(
        client,
        vm_id,
        lambda vm: power_state(vm) in ("running", "stopped"),
        "the virtual machine to finish booting",
        timeout,
        sleep,
    )


def set_power(client, vm_id, want, wait=True, timeout=DEFAULT_WAIT_TIMEOUT, sleep=time.sleep):
    """Start (``want="running"``), stop (``"stopped"``) or restart (``"restarted"``) a VM."""
    action = {"running": "start", "stopped": "stop", "restarted": "restart"}[want]
    Waiter(timeout, sleep=sleep).retry_busy(lambda: client.vm_action(vm_id, action))
    if not wait:
        return client.get_virtual_machine(vm_id)
    target = "running" if want == "restarted" else want
    return wait_idle(
        client,
        vm_id,
        lambda vm: power_state(vm) == target,
        f"the virtual machine to be {target}",
        timeout,
        sleep,
    )


def attach_firewall(
    client, vm_id, ruleset_id, wait=True, timeout=DEFAULT_WAIT_TIMEOUT, sleep=time.sleep
):
    Waiter(timeout, sleep=sleep).retry_busy(
        lambda: client.vm_action(vm_id, "attach-firewall", {"rulesetId": ruleset_id})
    )
    if wait:
        return wait_idle(
            client,
            vm_id,
            lambda vm: vm.get("firewallRulesetId") == ruleset_id,
            "the firewall to be applied",
            timeout,
            sleep,
        )
    return client.get_virtual_machine(vm_id)


def detach_firewall(
    client, vm_id, ruleset_id, wait=True, timeout=DEFAULT_WAIT_TIMEOUT, sleep=time.sleep
):
    Waiter(timeout, sleep=sleep).retry_busy(
        lambda: client.vm_action(vm_id, "detach-firewall", {"rulesetId": ruleset_id})
    )
    if wait:
        return wait_idle(
            client,
            vm_id,
            lambda vm: not vm.get("firewallRulesetId"),
            "the firewall to be removed",
            timeout,
            sleep,
        )
    return client.get_virtual_machine(vm_id)


def reinstall(
    client,
    vm_id,
    hostname,
    distro,
    ssh_key_ids=None,
    password=None,
    wait=True,
    timeout=DEFAULT_WAIT_TIMEOUT,
    sleep=time.sleep,
):
    """Reinstall the operating system. This erases the disk; the IP addresses stay."""
    body = {"hostname": hostname or "", "distro": f"DISTRO_{distro.upper()}"}
    if ssh_key_ids:
        body["sshKeyIds"] = ssh_key_ids
    if password:
        body["password"] = password
    Waiter(timeout, sleep=sleep).retry_busy(lambda: client.vm_action(vm_id, "reinstall", body))
    if not wait:
        return client.get_virtual_machine(vm_id)

    def check():
        vm = client.get_virtual_machine(vm_id)
        status = vm.get("reinstallStatus")
        if status == "REINSTALL_STATUS_FAILED":
            raise RuntimeError(
                f"the reinstall of virtual machine {vm_id} failed; the API does not report why"
            )
        return (
            status == "REINSTALL_STATUS_COMPLETED" and vm.get("crStatus") != "CR_STATUS_PROCESSING",
            vm,
        )

    # The status may still read COMPLETED from an earlier reinstall for a moment.
    sleep(3)
    Waiter(timeout, sleep=sleep).wait("the reinstall to finish", check)
    return wait_settled(client, vm_id, timeout, sleep)


def order_vm(
    client,
    hostname,
    plan_id,
    region_id,
    distro=None,
    ssh_key_ids=None,
    password=None,
    tags=None,
    ruleset_id=None,
    wait=True,
    timeout=DEFAULT_WAIT_TIMEOUT,
    sleep=time.sleep,
):
    """
    Order a VM and return it. Safe against lost responses: an ambiguous failure
    is retried with the same idempotency key, and free plans (which ignore the
    key) are tagged with a nonce so the VM is found instead of ordered twice.
    """
    body = {"hostname": hostname, "planId": plan_id, "regionId": region_id}
    body["idempotencyKey"] = new_uuid()
    if distro:
        body["distro"] = f"DISTRO_{distro.upper()}"
    if ssh_key_ids:
        body["sshKeyIds"] = ssh_key_ids
    if password:
        body["password"] = password
    tags = sorted(set(tags or []))

    nonce = ""
    try:
        free = client.get_marketplace_plan(plan_id).get("billingType") == "BILLING_TYPE_FREE"
    except NodeRingsError:
        free = True
    if free:
        nonce = NONCE_TAG_PREFIX + new_uuid()
        tags = tags + [nonce]
    if tags:
        body["tags"] = tags

    def find_by_nonce():
        for vm in client.list_virtual_machines():
            if nonce in (vm.get("tags") or []):
                return vm
        return None

    vm, last_err, new_key = None, None, False
    for attempt in range(6):
        try:
            vm = client.create_virtual_machine(body)
            break
        except NodeRingsError as exc:
            last_err = exc
            if exc.code == 9 and "new idempotency key" in exc.message.lower() and not new_key:
                new_key = True
                body["idempotencyKey"] = new_uuid()
                continue
            if not is_ambiguous(exc):
                raise NodeRingsUsageError(
                    f"ordering virtual machine {hostname}: {translate_create_error(exc)}"
                ) from exc
        except OSError as exc:
            last_err = exc
        if nonce:
            found = find_by_nonce()
            if found:
                vm = found
                break
        sleep(min(2 * (attempt + 1), 10))
    else:
        raise NodeRingsUsageError(
            f"ordering virtual machine {hostname}: {last_err}. The order may or may not have "
            "been placed; check the console before trying again"
        )
    if (not vm or not vm.get("id")) and nonce:
        vm = find_by_nonce()
    if not vm or not vm.get("id"):
        raise NodeRingsUsageError(
            f"the API accepted the order for {hostname} but did not return the new virtual "
            "machine; check the console before trying again"
        )
    vm_id = vm["id"]
    if wait or ruleset_id or nonce:
        wait_provisioned(client, vm_id, timeout, sleep)
        # The VM reads CREATED until the platform has booted it, then STARTED.
        wait_settled(client, vm_id, timeout, sleep)
    if nonce:
        try:
            client.update_virtual_machine_tags(vm_id, remove=[nonce])
        except NodeRingsError:
            pass
    if ruleset_id:
        attach_firewall(client, vm_id, ruleset_id, wait, timeout, sleep)
    return client.get_virtual_machine(vm_id)


__all__ = [
    "NodeRingsUsageError",
    "TimeoutError_",
    "attach_firewall",
    "choose_offer",
    "detach_firewall",
    "find_vm",
    "order_vm",
    "power_state",
    "reinstall",
    "resolve_ruleset",
    "resolve_ssh_keys",
    "set_power",
    "vm_view",
    "wait_settled",
]
