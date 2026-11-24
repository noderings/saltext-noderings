"""
State module tests against the live NodeRings API: apply, re-apply (no
changes), test mode, and remove. Needs NODERINGS_TOKEN.
"""

import pytest

from tests.live import billed
from tests.live import live
from tests.live import plan
from tests.live import test_domain
from tests.live import unique

pytestmark = [pytest.mark.live, live]


def test_dns_states_are_idempotent(states, modules):
    zone = f"{unique('saltext')}.{test_domain()}"
    try:
        ret = states.noderings.dns_zone_present(name=zone)
        assert ret.result is True and ret.changes

        ret = states.noderings.dns_record_present(
            name="www", zone=zone, rtype="A", values=["192.0.2.10"], ttl=300
        )
        assert ret.result is True and ret.changes
        ret = states.noderings.dns_record_present(
            name="www", zone=zone, rtype="A", values=["192.0.2.10"], ttl=300
        )
        assert ret.result is True and not ret.changes

        ret = states.noderings.dns_record_present(
            name="www", zone=zone, rtype="A", values=["192.0.2.20"], ttl=300, test=True
        )
        assert ret.result is None and ret.changes
        assert modules.noderings.get_record(zone, "www", "A")["values"] == ["192.0.2.10"]

        ret = states.noderings.dns_record_absent(name="www", zone=zone, rtype="A")
        assert ret.result is True and ret.changes
        ret = states.noderings.dns_record_absent(name="www", zone=zone, rtype="A")
        assert ret.result is True and not ret.changes
    finally:
        ret = states.noderings.dns_zone_absent(name=zone, force=True)
        assert ret.result is True
    assert modules.noderings.get_zone(zone) is None


def test_firewall_state_is_idempotent(states):
    name = unique("saltext")
    rules = [
        {
            "direction": "ingress",
            "action": "accept",
            "destination_ports": "22",
            "source": {"cidrs": ["0.0.0.0/0"]},
        },
        {
            "direction": "ingress",
            "action": "drop",
            "protocol": "any",
            "source": {"cidrs": ["0.0.0.0/0"]},
        },
    ]
    try:
        ret = states.noderings.firewall_ruleset_present(name=name, rules=rules)
        assert ret.result is True and ret.changes
        ret = states.noderings.firewall_ruleset_present(name=name, rules=rules)
        assert ret.result is True and not ret.changes
    finally:
        assert states.noderings.firewall_ruleset_absent(name=name, force=True).result is True


@billed
def test_vm_states(states, modules, tmp_path):
    """Orders an hourly server, re-applies (no second order), updates it in place, deletes it."""
    import subprocess  # pylint: disable=import-outside-toplevel

    name = unique("saltext")
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "k")], check=True
    )
    pub = (tmp_path / "k.pub").read_text().strip()
    args = {"name": name, "ssh_keys": [name], "tags": ["saltext-test"], **plan()}
    try:
        assert states.noderings.ssh_key_present(name=name, public_key=pub).result is True

        ret = states.noderings.vm_present(test=True, **args)
        assert ret.result is None and modules.noderings.get_vm(name) is None

        ret = states.noderings.vm_present(**args)
        assert ret.result is True and ret.changes["new"]["ipv4"]

        ret = states.noderings.vm_present(**args)
        assert ret.result is True and not ret.changes

        ret = states.noderings.vm_present(
            **dict(args, tags=["saltext-test", "web"], power_state="stopped")
        )
        assert ret.result is True and set(ret.changes) == {"tags", "power_state"}
        vm = modules.noderings.get_vm(name)
        assert vm["power_state"] == "stopped" and vm["tags"] == ["saltext-test", "web"]

        ret = states.noderings.vm_present(**dict(args, distro="debian_13"))
        assert ret.result is False and "allow_reinstall" in ret.comment
    finally:
        assert states.noderings.vm_absent(name=name).result is True
        states.noderings.ssh_key_absent(name=name)
    assert modules.noderings.get_vm(name) is None
