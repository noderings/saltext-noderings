import pytest

import saltext.noderings.modules.noderings_mod as noderings_module
import saltext.noderings.states.noderings_mod as noderings_state
from tests.unit.fakeapi import ACME_PLAN_ID
from tests.unit.fakeapi import PLAN_ID
from tests.unit.fakeapi import REGION_ID

PUBKEY = "ssh-ed25519 AAAAC3Nza me@laptop"


@pytest.fixture
def configure_loader_modules():
    config_get = {"config.get": lambda key, default=None: default}
    salt_funcs = {
        f"noderings.{name}": getattr(noderings_module, name)
        for name in dir(noderings_module)
        if not name.startswith("_")
    }
    salt_funcs.update(config_get)
    return {
        noderings_module: {"__salt__": config_get},
        noderings_state: {"__salt__": salt_funcs, "__opts__": {"test": False}},
    }


@pytest.fixture
def test_mode():
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(noderings_state.__opts__, "test", True)  # pylint: disable=no-member
        yield


def test_ssh_key_present_is_idempotent(api):  # pylint: disable=unused-argument
    ret = noderings_state.ssh_key_present("laptop", PUBKEY)
    assert ret["result"] is True and ret["changes"]
    ret = noderings_state.ssh_key_present("laptop", PUBKEY + "  ")
    assert ret["result"] is True and not ret["changes"]
    ret = noderings_state.ssh_key_absent("laptop")
    assert ret["changes"] == {"old": ret["changes"]["old"], "new": None}
    assert not noderings_state.ssh_key_absent("laptop")["changes"]


def test_test_mode_changes_nothing(api, test_mode):  # pylint: disable=unused-argument
    ret = noderings_state.dns_zone_present("example.com")
    assert ret["result"] is None and ret["changes"]
    ret = noderings_state.vm_present("web-1", plan_id=PLAN_ID, region_id=REGION_ID)
    assert ret["result"] is None
    assert api.writes() == []


@pytest.mark.parametrize("provider", [None, "acme-hosting", "Acme Hosting"])
def test_vm_present_orders_from_the_hosting_provider(api, provider):
    ret = noderings_state.vm_present(
        "web-1",
        plan_code="SC-GC-1C-1G-20",
        country="United States",
        city="Ashburn",
        hosting_provider=provider,
    )
    assert ret["result"] is True and ret["changes"]["old"] is None
    assert api.created_vm_bodies[-1]["planId"] == (ACME_PLAN_ID if provider else PLAN_ID)


def test_vm_present_fails_for_an_unknown_provider(api):
    ret = noderings_state.vm_present(
        "web-1",
        plan_code="SC-GC-1C-1G-20",
        country="United States",
        city="Ashburn",
        hosting_provider="Nobody Hosting",
    )
    assert ret["result"] is False and "from provider Nobody Hosting" in ret["comment"]
    assert not api.created_vm_bodies


def test_dns_zone_and_records(api):  # pylint: disable=unused-argument
    assert noderings_state.dns_zone_present("example.com", dnssec=True)["changes"]["dnssec"]
    assert not noderings_state.dns_zone_present("example.com", dnssec=True)["changes"]
    ret = noderings_state.dns_record_present("www", "example.com", "A", ["192.0.2.10"], ttl=300)
    assert ret["result"] is True and ret["changes"]["new"] == {"ttl": 300, "values": ["192.0.2.10"]}
    ret = noderings_state.dns_record_present("www", "example.com", "A", ["192.0.2.10"], ttl=300)
    assert not ret["changes"]
    ret = noderings_state.dns_record_present("www", "example.com", "A", ["192.0.2.11"], ttl=300)
    assert ret["changes"]["old"]["values"] == ["192.0.2.10"]
    assert noderings_state.dns_record_absent("www", "example.com", "A")["changes"]
    assert not noderings_state.dns_record_absent("www", "example.com", "A")["changes"]
    assert noderings_state.dns_zone_absent("example.com")["changes"]
    assert noderings_state.dns_record_absent("www", "example.com", "A")["result"] is True


def test_firewall_ruleset(api):  # pylint: disable=unused-argument
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
    assert noderings_state.firewall_ruleset_present("web", rules)["changes"]
    assert not noderings_state.firewall_ruleset_present("web", rules)["changes"]
    ret = noderings_state.firewall_ruleset_present("web", list(reversed(rules)))
    assert ret["changes"]["new"][0]["action"] == "drop"
    bad = [
        {
            "direction": "ingress",
            "action": "accept",
            "destination_ports": "1-2-3",
            "source": {"cidrs": ["0.0.0.0/0"]},
        }
    ]
    assert noderings_state.firewall_ruleset_present("web", bad)["result"] is False
    assert noderings_state.firewall_ruleset_absent("web")["changes"]


def test_vm_present_creates_then_updates_in_place(api):
    ret = noderings_state.vm_present(
        "web-1", plan_code="SC-GC-1C-1G-20", country="United States", city="Ashburn", tags=["web"]
    )
    assert ret["result"] is True and ret["changes"]["new"]["ipv4"] == "192.0.2.50"
    ret = noderings_state.vm_present(
        "web-1", plan_code="SC-GC-1C-1G-20", country="United States", city="Ashburn", tags=["web"]
    )
    assert not ret["changes"]
    ret = noderings_state.vm_present("web-1", tags=["web", "prod"], power_state="stopped")
    assert set(ret["changes"]) == {"tags", "power_state"}
    assert len(api.created_vm_bodies) == 1


def test_vm_present_refuses_silent_reinstall(api):  # pylint: disable=unused-argument
    noderings_state.vm_present("web-1", plan_id=PLAN_ID, region_id=REGION_ID)
    ret = noderings_state.vm_present("web-1", distro="debian_13")
    assert ret["result"] is False and "allow_reinstall" in ret["comment"]
    assert noderings_state.vm_absent("web-1")["changes"]
    assert not noderings_state.vm_absent("web-1")["changes"]
