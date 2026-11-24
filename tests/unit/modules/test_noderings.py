import pytest
from salt.exceptions import SaltInvocationError

import saltext.noderings.modules.noderings_mod as noderings_module
from tests.unit.fakeapi import PLAN_ID
from tests.unit.fakeapi import REGION_ID


@pytest.fixture
def configure_loader_modules():
    return {noderings_module: {"__salt__": {"config.get": lambda key, default=None: default}}}


def test_get_organization(api):  # pylint: disable=unused-argument
    assert noderings_module.get_organization()["name"] == "Test Org"


def test_missing_token(monkeypatch):
    for var in ("NODERINGS_TOKEN", "NODERINGS_API_TOKEN", "NR_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(Exception, match="token is required"):
        noderings_module.list_zones()


def test_zone_and_record_lifecycle(api):
    zone = noderings_module.create_zone("Example.COM.")
    assert zone["name"] == "example.com"
    assert noderings_module.get_zone("example.com")["nameservers"] == [
        "ans1.nrings.io",
        "ans2.nrings.io",
    ]

    rec = noderings_module.set_record(
        "example.com", "www.example.com.", "a", ["192.0.2.10"], ttl=300
    )
    assert rec == {
        "zone_id": zone["id"],
        "name": "www",
        "type": "A",
        "ttl": 300,
        "values": ["192.0.2.10"],
    }

    txt = noderings_module.set_record("example.com", "@", "TXT", "v=spf1 -all")
    assert txt["values"] == ["v=spf1 -all"]
    assert api.records[zone["id"]][("@", "TXT")]["rdata"] == ['"v=spf1 -all"']

    assert noderings_module.delete_record("example.com", "www", "A") is True
    assert noderings_module.delete_record("example.com", "www", "A") is False
    assert noderings_module.delete_zone("example.com") is True
    assert noderings_module.delete_zone("example.com") is False


def test_record_validation(api):  # pylint: disable=unused-argument
    noderings_module.create_zone("example.com")
    with pytest.raises(SaltInvocationError, match="not an IPv4 address"):
        noderings_module.set_record("example.com", "www", "A", ["not-an-ip"])
    with pytest.raises(SaltInvocationError, match="ttl"):
        noderings_module.set_record("example.com", "www", "A", ["192.0.2.1"], ttl=5)
    with pytest.raises(SaltInvocationError, match="does not exist"):
        noderings_module.set_record("missing.com", "www", "A", ["192.0.2.1"])


def test_ssh_keys(api):  # pylint: disable=unused-argument
    key = noderings_module.create_ssh_key("laptop", "ssh-ed25519 AAAA me@laptop\n")
    assert noderings_module.get_ssh_key("laptop")["id"] == key["id"]
    assert noderings_module.delete_ssh_key("laptop") is True
    assert noderings_module.get_ssh_key("laptop") is None


def test_set_rules_keeps_order(api):  # pylint: disable=unused-argument
    noderings_module.create_ruleset("web")
    rules = [
        {
            "direction": "ingress",
            "action": "accept",
            "destination_ports": "22",
            "source": {"cidrs": ["198.51.100.0/24"]},
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
    out = noderings_module.set_rules("web", rules)
    assert [r["destination_ports"] for r in out] == ["22", "443", None]
    assert [r["action"] for r in out] == ["accept", "accept", "drop"]


def test_set_rules_rejects_port_lists(api):  # pylint: disable=unused-argument
    noderings_module.create_ruleset("web")
    bad = [
        {
            "direction": "ingress",
            "action": "accept",
            "destination_ports": "80,443",
            "source": {"cidrs": ["0.0.0.0/0"]},
        }
    ]
    with pytest.raises(SaltInvocationError, match="one port"):
        noderings_module.set_rules("web", bad)


def test_create_vm_picks_offer_and_keys(api):
    noderings_module.create_ssh_key("laptop", "ssh-ed25519 AAAA me@laptop")
    vm = noderings_module.create_vm(
        "web-1",
        plan_code="SC-GC-1C-1G-20",
        country="United States",
        city="Ashburn",
        ssh_keys=["laptop"],
        tags=["web"],
    )
    assert vm["hostname"] == "web-1"
    assert vm["power_state"] == "running"
    assert vm["tags"] == ["web"]
    body = api.created_vm_bodies[0]
    assert body["planId"] == PLAN_ID and body["regionId"] == REGION_ID
    assert body["distro"] == "DISTRO_UBUNTU_24_04"
    assert len(body["sshKeyIds"]) == 1 and body["idempotencyKey"]


def test_create_vm_errors(api):  # pylint: disable=unused-argument
    with pytest.raises(SaltInvocationError, match="No offer"):
        noderings_module.create_vm(
            "web-1", plan_code="SC-GC-1C-1G-20", country="United States", city="Nowhere"
        )
    with pytest.raises(SaltInvocationError, match="no SSH key"):
        noderings_module.create_vm(
            "web-1",
            plan_code="SC-GC-1C-1G-20",
            country="United States",
            city="Ashburn",
            ssh_keys=["missing"],
        )
    noderings_module.create_vm("web-1", plan_id=PLAN_ID, region_id=REGION_ID)
    with pytest.raises(SaltInvocationError, match="already exists"):
        noderings_module.create_vm("web-1", plan_id=PLAN_ID, region_id=REGION_ID)


def test_vm_power_tags_firewall_and_delete(api):  # pylint: disable=unused-argument
    noderings_module.create_vm("web-1", plan_id=PLAN_ID, region_id=REGION_ID, tags=["a"])
    assert noderings_module.stop_vm("web-1")["power_state"] == "stopped"
    assert noderings_module.start_vm("web-1")["power_state"] == "running"
    assert noderings_module.set_tags("web-1", ["b", "c"])["tags"] == ["b", "c"]
    rs = noderings_module.create_ruleset("web")
    assert noderings_module.attach_ruleset("web-1", "web")["firewall_ruleset_id"] == rs["id"]
    assert not noderings_module.detach_ruleset("web-1").get("firewall_ruleset_id")
    assert noderings_module.delete_vm("web-1") is True
    assert noderings_module.get_vm("web-1") is None
