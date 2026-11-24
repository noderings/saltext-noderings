"""
Execution module tests against the live NodeRings API.

Free tests need NODERINGS_TOKEN. The VM test orders an hourly server for a few
minutes and also needs NODERINGS_TEST_BILLED=1.
"""

import pytest

from tests.live import billed
from tests.live import live
from tests.live import plan
from tests.live import test_domain
from tests.live import unique

pytestmark = [pytest.mark.live, live]


@pytest.fixture
def noderings(modules):
    return modules.noderings


def test_catalog(noderings):
    assert noderings.get_organization()["id"]
    names = [d["name"] for d in noderings.list_distros()]
    assert "ubuntu_24_04" in names
    offers = noderings.list_offers(plan()["plan_code"], plan()["country"], plan()["city"])
    assert offers and float(offers[0]["prices"][0]["unit_amount"]) >= 0


def test_dns(noderings):
    zone = f"{unique('saltext')}.{test_domain()}"
    try:
        created = noderings.create_zone(zone)
        assert created["name"] == zone and created["nameservers"]
        rec = noderings.set_record(zone, "www", "A", ["192.0.2.10"], ttl=300)
        assert rec["values"] == ["192.0.2.10"] and rec["ttl"] == 300
        noderings.set_record(
            zone, "@", "MX", ["10 mx1.example.net.", "20 mx2.example.net."], ttl=3600
        )
        noderings.set_record(zone, "@", "TXT", ["v=spf1 -all"])
        assert noderings.get_record(zone, "@", "TXT")["values"] == ["v=spf1 -all"]
        assert sorted(noderings.get_record(zone, "@", "MX")["values"]) == [
            "10 mx1.example.net.",
            "20 mx2.example.net.",
        ]
        sec = noderings.enable_dnssec(zone)
        assert sec["ds"]
        assert noderings.delete_record(zone, "www", "A") is True
        assert noderings.get_record(zone, "www", "A") is None
    finally:
        noderings.delete_zone(zone, force=True)
    assert noderings.get_zone(zone) is None


def test_ssh_key_and_firewall(noderings, tmp_path):
    key_name = unique("saltext")
    ruleset = unique("saltext")
    # A fresh key every run: the API refuses two keys with the same fingerprint.
    import subprocess  # pylint: disable=import-outside-toplevel

    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "k")], check=True
    )
    pub = (tmp_path / "k.pub").read_text()
    try:
        assert noderings.create_ssh_key(key_name, pub)["name"] == key_name
        assert noderings.get_ssh_key(key_name)
        noderings.create_ruleset(ruleset)
        rules = [
            {
                "direction": "ingress",
                "action": "accept",
                "destination_ports": "22",
                "source": {"cidrs": ["0.0.0.0/0"]},
            },
            {
                "direction": "ingress",
                "action": "accept",
                "destination_ports": "443",
                "source": {"cidrs": ["0.0.0.0/0"]},
            },
            {
                "direction": "ingress",
                "action": "drop",
                "protocol": "any",
                "source": {"cidrs": ["0.0.0.0/0"]},
            },
        ]
        out = noderings.set_rules(ruleset, rules)
        assert [r["destination_ports"] for r in out] == ["22", "443", None]
        out = noderings.set_rules(ruleset, [rules[2], rules[0]])
        assert [r["action"] for r in out] == ["drop", "accept"]
    finally:
        noderings.delete_ruleset(ruleset, force=True)
        noderings.delete_ssh_key(key_name)
    assert noderings.get_ruleset(ruleset) is None
    assert noderings.get_ssh_key(key_name) is None


@billed
def test_vm_lifecycle(noderings, tmp_path):
    name = unique("saltext")
    ruleset = unique("saltext")
    import subprocess  # pylint: disable=import-outside-toplevel

    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "k")], check=True
    )
    try:
        noderings.create_ssh_key(name, (tmp_path / "k.pub").read_text())
        noderings.create_ruleset(ruleset)
        vm = noderings.create_vm(name, tags=["saltext-test"], ssh_keys=[name], **plan())
        assert vm["power_state"] == "running" and vm["ipv4"]
        assert noderings.get_vm(name)["id"] == vm["id"]
        assert noderings.set_tags(name, ["saltext-test", "web"])["tags"] == ["saltext-test", "web"]
        assert noderings.attach_ruleset(name, ruleset)["firewall_ruleset_id"]
        assert not noderings.detach_ruleset(name).get("firewall_ruleset_id")
        assert noderings.stop_vm(name)["power_state"] == "stopped"
        assert noderings.start_vm(name)["power_state"] == "running"
    finally:
        noderings.delete_vm(name)
        noderings.delete_ruleset(ruleset, force=True)
        noderings.delete_ssh_key(name)
    assert noderings.get_vm(name) is None
