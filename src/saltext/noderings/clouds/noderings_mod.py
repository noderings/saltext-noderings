"""
Salt Cloud driver for NodeRings.

Orders servers from the NodeRings marketplace and bootstraps a Salt minion on
them over SSH.

Provider configuration, for example ``/etc/salt/cloud.providers.d/noderings.conf``:

.. code-block:: yaml

    my-noderings:
      driver: noderings
      token: <service account token>   # or the NODERINGS_TOKEN environment variable
      # organization_id: <id>          # optional
      # minion:
      #   master: salt.example.com

Profile configuration, for example ``/etc/salt/cloud.profiles.d/noderings.conf``:

.. code-block:: yaml

    noderings-ashburn-small:
      provider: my-noderings
      size: SC-GC-1C-1G-20            # plan code, see salt-cloud --list-sizes my-noderings
      location: United States/Ashburn # country/city, see salt-cloud --list-locations my-noderings
      image: ubuntu_24_04             # distro, see salt-cloud --list-images my-noderings
      billing_type: usage             # hourly, from prepaid credit
      ssh_keys: [laptop]              # names of SSH keys stored in NodeRings
      private_key: /root/.ssh/id_ed25519   # matching private key, for the bootstrap
      firewall_ruleset: web           # optional
      tags: [web]                     # optional

Then:

.. code-block:: bash

    salt-cloud -p noderings-ashburn-small web-1
    salt-cloud -d web-1
"""

import logging
import time

from salt import config
from salt.exceptions import SaltCloudException
from salt.exceptions import SaltCloudNotFound
from salt.exceptions import SaltCloudSystemExit

from saltext.noderings import __version__
from saltext.noderings.utils import compute
from saltext.noderings.utils.client import NodeRingsError
from saltext.noderings.utils.client import TimeoutError_
from saltext.noderings.utils.client import client_from_config
from saltext.noderings.utils.client import snake
from saltext.noderings.utils.client import trim_enum

log = logging.getLogger(__name__)

__virtualname__ = "noderings"

USER_AGENT = (
    f"saltext-noderings-cloud/{__version__} (+https://github.com/noderings/saltext-noderings)"
)


def __virtual__():
    if get_configured_provider() is False:
        return False
    return __virtualname__


def _get_active_provider_name():
    try:
        return __active_provider_name__.value()
    except AttributeError:
        return __active_provider_name__


def get_configured_provider():
    """
    Return the first configured NodeRings provider. A token may come from the
    provider block or the ``NODERINGS_TOKEN`` environment variable, so no key
    is required in the block.
    """
    return config.is_provider_configured(
        __opts__, _get_active_provider_name() or __virtualname__, ()
    )


def get_dependencies():
    """
    The driver only needs the standard library.
    """
    return True


def _provider_value(key, default=None):
    return config.get_cloud_config_value(
        key, get_configured_provider(), __opts__, search_global=False, default=default
    )


def _client():
    conf = {
        key: _provider_value(key)
        for key in ("token", "api_url", "organization_id", "timeout", "retries")
    }
    try:
        return client_from_config(conf, user_agent=USER_AGENT)
    except ValueError as exc:
        raise SaltCloudSystemExit(str(exc)) from exc


def _guard(call, allowed, func):
    if call not in allowed:
        raise SaltCloudSystemExit(
            f"The {func} {'action' if call == 'action' else 'function'} must be called with "
            f"{' or '.join('-' + ('a' if a == 'action' else 'f') for a in allowed)}."
        )


def _node(vm):
    view = compute.vm_view(vm)
    return {
        "id": view.get("id"),
        "image": view.get("distro"),
        "size": view.get("plan_code") or view.get("plan_id"),
        "state": view.get("power_state"),
        "private_ips": [],
        "public_ips": [ip for ip in (view.get("ipv4"), view.get("ipv6")) if ip],
    }


def _vms_by_name(client):
    out = {}
    for vm in client.list_virtual_machines():
        out[vm.get("hostname")] = vm
    return out


# -- listing ----------------------------------------------------------------------


def avail_locations(call=None):
    """
    List the cities with servers for sale, as ``Country/City``.

    .. code-block:: bash

        salt-cloud --list-locations my-noderings
    """
    _guard(call, (None, "function"), "avail_locations")
    out = {}
    for region in _client().list_marketplace_regions("ASSET_TYPE_VM"):
        country = region.get("country") or ""
        city = region.get("city") or ""
        out[f"{country}/{city}"] = snake(region)
    return out


def avail_images(call=None):
    """
    List the operating systems a server can be ordered with.

    .. code-block:: bash

        salt-cloud --list-images my-noderings
    """
    _guard(call, (None, "function"), "avail_images")
    out = {}
    for distro in _client().list_distros():
        name = trim_enum(distro.get("distro"), "DISTRO_")
        out[name] = snake(distro)
    return out


def avail_sizes(call=None):
    """
    List the plan codes for sale, with their size.

    .. code-block:: bash

        salt-cloud --list-sizes my-noderings
    """
    _guard(call, (None, "function"), "avail_sizes")
    out = {}
    for plan in _client().list_available_plans(asset_type="ASSET_TYPE_VM"):
        code = plan.get("planCode") or plan.get("code")
        if code and code not in out:
            out[code] = snake(plan)
    return out


def list_nodes(call=None):
    """
    List servers with their basic details.

    .. code-block:: bash

        salt-cloud -Q
    """
    _guard(call, (None, "function"), "list_nodes")
    return {name: _node(vm) for name, vm in _vms_by_name(_client()).items()}


def list_nodes_full(call=None):
    """
    List servers with every field.

    .. code-block:: bash

        salt-cloud -F
    """
    _guard(call, (None, "function"), "list_nodes_full")
    return {
        name: {**compute.vm_view(vm), **_node(vm)} for name, vm in _vms_by_name(_client()).items()
    }


def list_nodes_select(call=None):
    """
    List servers with the fields in ``query.selection``.

    .. code-block:: bash

        salt-cloud -S
    """
    return __utils__["cloud.list_nodes_select"](
        list_nodes_full("function"), __opts__["query.selection"], call
    )


def show_instance(name, call=None):
    """
    Show one server.

    .. code-block:: bash

        salt-cloud -a show_instance web-1
    """
    _guard(call, ("action",), "show_instance")
    try:
        vm = compute.find_vm(_client(), name)
    except (NodeRingsError, compute.NodeRingsUsageError) as exc:
        raise SaltCloudSystemExit(str(exc)) from exc
    if not vm:
        raise SaltCloudNotFound(f"The specified instance {name} could not be found.")
    view = compute.vm_view(vm)
    __utils__["cloud.cache_node"](view, _get_active_provider_name(), __opts__)
    return view


# -- create and destroy -----------------------------------------------------------


def _profile_value(vm_, key, default=None):
    return config.get_cloud_config_value(key, vm_, __opts__, default=default, search_global=False)


def create(vm_):
    """
    Order a server and bootstrap a Salt minion on it.

    .. code-block:: bash

        salt-cloud -p noderings-ashburn-small web-1
    """
    name = vm_["name"]
    try:
        if (
            vm_["profile"]
            and config.is_profile_configured(
                __opts__, _get_active_provider_name() or __virtualname__, vm_["profile"], vm_=vm_
            )
            is False
        ):
            return False
    except AttributeError:
        pass

    __utils__["cloud.fire_event"](
        "event",
        "starting create",
        f"salt/cloud/{name}/creating",
        args=__utils__["cloud.filter_event"](
            "creating", vm_, ["name", "profile", "provider", "driver"]
        ),
        sock_dir=__opts__["sock_dir"],
        transport=__opts__["transport"],
    )

    client = _client()
    size = _profile_value(vm_, "size")
    location = _profile_value(vm_, "location") or ""
    plan_id = _profile_value(vm_, "plan_id")
    region_id = _profile_value(vm_, "region_id")
    if not plan_id:
        if not size or "/" not in location:
            raise SaltCloudSystemExit(
                "The profile needs size (a plan code) and location (Country/City), or plan_id and region_id."
            )
        country, city = location.split("/", 1)
    timeout = int(_profile_value(vm_, "wait_timeout", 900))
    try:
        if compute.find_vm(client, name):
            raise SaltCloudSystemExit(f"A server named {name} already exists.")
        ruleset_id = compute.resolve_ruleset(client, _profile_value(vm_, "firewall_ruleset"))
        if not plan_id:
            plan_id, region_id, offer = compute.choose_offer(
                client,
                size,
                country,
                city,
                billing_type=_profile_value(vm_, "billing_type", "usage"),
                provider_id=_profile_value(vm_, "provider_id"),
                require_capabilities=["guest_firewall"] if ruleset_id else [],
                sort=_profile_value(vm_, "sort", "price"),
                # ``provider`` names the Salt Cloud provider block, so the hosting
                # provider has its own key.
                provider=_profile_value(vm_, "hosting_provider"),
            )
            log.info("Ordering %s from %s", name, offer.get("provider_name"))
        key_ids = compute.resolve_ssh_keys(client, _profile_value(vm_, "ssh_keys") or [])
        vm = compute.order_vm(
            client,
            name,
            plan_id,
            region_id,
            distro=_profile_value(vm_, "image", "ubuntu_24_04"),
            ssh_key_ids=key_ids,
            password=_profile_value(vm_, "password"),
            tags=_profile_value(vm_, "tags") or [],
            ruleset_id=ruleset_id,
            wait=True,
            timeout=timeout,
        )
    except (NodeRingsError, compute.NodeRingsUsageError, RuntimeError, TimeoutError_) as exc:
        raise SaltCloudSystemExit(f"Error creating {name} on NodeRings: {exc}") from exc

    data = compute.vm_view(vm)
    ip = data.get("ipv4") or data.get("ipv6")
    if not ip:
        raise SaltCloudSystemExit(
            f"{name} was created but has no IP address yet; destroy it and try again."
        )

    vm_["ssh_host"] = ip
    vm_["ssh_username"] = _profile_value(vm_, "ssh_username", "root")
    vm_["key_filename"] = _profile_value(vm_, "private_key")
    if _profile_value(vm_, "password"):
        vm_["password"] = _profile_value(vm_, "password")

    if _profile_value(vm_, "deploy", True) is not False:
        __utils__["cloud.wait_for_port"](
            ip, timeout=int(_profile_value(vm_, "ssh_connect_timeout", 300))
        )
        # The SSH port opens before cloud-init has written the authorized key.
        time.sleep(int(_profile_value(vm_, "ssh_ready_delay", 20)))

    ret = __utils__["cloud.bootstrap"](vm_, __opts__)
    ret.update(data)

    __utils__["cloud.fire_event"](
        "event",
        "created instance",
        f"salt/cloud/{name}/created",
        args=__utils__["cloud.filter_event"](
            "created", vm_, ["name", "profile", "provider", "driver"]
        ),
        sock_dir=__opts__["sock_dir"],
        transport=__opts__["transport"],
    )
    return ret


def destroy(name, call=None):
    """
    Delete a server. An hourly server stops billing; a monthly subscription is
    cancelled without a refund.

    .. code-block:: bash

        salt-cloud -d web-1
    """
    _guard(call, (None, "action"), "destroy")
    __utils__["cloud.fire_event"](
        "event",
        "destroying instance",
        f"salt/cloud/{name}/destroying",
        args={"name": name},
        sock_dir=__opts__["sock_dir"],
        transport=__opts__["transport"],
    )
    client = _client()
    try:
        vm = compute.find_vm(client, name)
        if not vm:
            raise SaltCloudNotFound(f"The specified instance {name} could not be found.")
        client.delete_virtual_machine(vm["id"])
    except (NodeRingsError, compute.NodeRingsUsageError) as exc:
        raise SaltCloudException(f"Error destroying {name}: {exc}") from exc

    __utils__["cloud.fire_event"](
        "event",
        "destroyed instance",
        f"salt/cloud/{name}/destroyed",
        args={"name": name},
        sock_dir=__opts__["sock_dir"],
        transport=__opts__["transport"],
    )
    if __opts__.get("update_cachedir", False) is True:
        __utils__["cloud.delete_minion_cachedir"](
            name, _get_active_provider_name().split(":")[0], __opts__
        )
    return {"destroyed": name}


def _power(name, call, want):
    _guard(call, ("action",), want)
    client = _client()
    try:
        vm = compute.find_vm(client, name)
        if not vm:
            raise SaltCloudNotFound(f"The specified instance {name} could not be found.")
        return compute.vm_view(compute.set_power(client, vm["id"], want))
    except (NodeRingsError, compute.NodeRingsUsageError, TimeoutError_) as exc:
        raise SaltCloudSystemExit(f"Error changing the power of {name}: {exc}") from exc


def start(name, call=None):
    """
    Start a stopped server.

    .. code-block:: bash

        salt-cloud -a start web-1
    """
    return _power(name, call, "running")


def stop(name, call=None):
    """
    Stop a server. Its disk and IP addresses are kept.

    .. code-block:: bash

        salt-cloud -a stop web-1
    """
    return _power(name, call, "stopped")


def reboot(name, call=None):
    """
    Restart a server.

    .. code-block:: bash

        salt-cloud -a reboot web-1
    """
    return _power(name, call, "restarted")
