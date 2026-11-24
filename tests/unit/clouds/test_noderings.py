from unittest.mock import MagicMock

import pytest
from salt.exceptions import SaltCloudNotFound
from salt.exceptions import SaltCloudSystemExit

import saltext.noderings.clouds.noderings_mod as noderings_cloud

PROVIDER = "my-noderings"


@pytest.fixture
def configure_loader_modules():
    return {
        noderings_cloud: {
            "__opts__": {
                "providers": {
                    PROVIDER: {"noderings": {"driver": "noderings", "token": "test-token"}}
                },
                "profiles": {},
                "sock_dir": "/tmp",
                "transport": "zeromq",
                "query.selection": ["id", "state"],
            },
            "__utils__": {
                "cloud.filter_event": MagicMock(return_value={}),
                "cloud.fire_event": MagicMock(),
                "cloud.bootstrap": MagicMock(return_value={"deployed": False}),
                "cloud.wait_for_port": MagicMock(return_value=True),
                "cloud.cache_node": MagicMock(),
                "cloud.list_nodes_select": MagicMock(
                    side_effect=lambda nodes, sel, call: {
                        n: {k: v for k, v in d.items() if k in sel} for n, d in nodes.items()
                    }
                ),
            },
            "__active_provider_name__": f"{PROVIDER}:noderings",
        }
    }


def _vm_(**extra):
    vm_ = {
        "name": "web-1",
        "profile": None,
        "driver": f"{PROVIDER}:noderings",
        "provider": f"{PROVIDER}:noderings",
        "size": "SC-GC-1C-1G-20",
        "location": "United States/Ashburn",
        "image": "ubuntu_24_04",
        "deploy": False,
    }
    vm_.update(extra)
    return vm_


def test_provider_is_configured():
    assert noderings_cloud.get_configured_provider()
    assert noderings_cloud.__virtual__() == "noderings"


def test_catalog(api):  # pylint: disable=unused-argument
    assert "United States/Ashburn" in noderings_cloud.avail_locations()
    assert "ubuntu_24_04" in noderings_cloud.avail_images()
    assert "SC-GC-1C-1G-20" in noderings_cloud.avail_sizes()


def test_create_list_show_destroy(api):
    ret = noderings_cloud.create(_vm_(tags=["web"]))
    assert ret["hostname"] == "web-1" and ret["ipv4"] == "192.0.2.50"
    bootstrap = noderings_cloud.__utils__["cloud.bootstrap"]  # pylint: disable=no-member
    assert bootstrap.call_args[0][0]["ssh_host"] == "192.0.2.50"
    assert bootstrap.call_args[0][0]["ssh_username"] == "root"

    nodes = noderings_cloud.list_nodes()
    assert nodes["web-1"]["public_ips"] == ["192.0.2.50"]
    assert nodes["web-1"]["state"] == "running"
    assert noderings_cloud.list_nodes_select()["web-1"] == {
        "id": nodes["web-1"]["id"],
        "state": "running",
    }
    assert noderings_cloud.show_instance("web-1", call="action")["plan_code"] == "SC-GC-1C-1G-20"

    assert noderings_cloud.stop("web-1", call="action")["power_state"] == "stopped"
    assert noderings_cloud.start("web-1", call="action")["power_state"] == "running"
    assert noderings_cloud.destroy("web-1") == {"destroyed": "web-1"}
    assert api.vms == {}
    with pytest.raises(SaltCloudNotFound):
        noderings_cloud.destroy("web-1")


def test_create_rejects_duplicates_and_bad_profiles(api):  # pylint: disable=unused-argument
    noderings_cloud.create(_vm_())
    with pytest.raises(SaltCloudSystemExit, match="already exists"):
        noderings_cloud.create(_vm_())
    with pytest.raises(SaltCloudSystemExit, match="needs size"):
        noderings_cloud.create(_vm_(name="web-2", location="Ashburn"))
    with pytest.raises(SaltCloudSystemExit, match="No offer"):
        noderings_cloud.create(_vm_(name="web-3", location="United States/Nowhere"))


def test_call_guards(api):  # pylint: disable=unused-argument
    with pytest.raises(SaltCloudSystemExit):
        noderings_cloud.list_nodes(call="action")
    with pytest.raises(SaltCloudSystemExit):
        noderings_cloud.show_instance("web-1", call="function")
    with pytest.raises(SaltCloudSystemExit):
        noderings_cloud.destroy("web-1", call="function")
