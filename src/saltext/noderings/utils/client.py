"""
A small JSON client for the NodeRings public API.

It is shared by the execution module, the state module and the Salt Cloud
driver. It retries rate-limited and transient failures, never retries orders
blindly, and keeps secrets out of error messages. It uses only the standard
library.
"""

import json
import os
import random
import re
import time
import uuid
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen

from saltext.noderings import __version__

DEFAULT_API_URL = "https://api.noderings.com"
TOKEN_ENV_VARS = ("NODERINGS_TOKEN", "NODERINGS_API_TOKEN", "NR_API_TOKEN")

# gRPC status codes carried in the API's error bodies.
CODE_INVALID_ARGUMENT = 3
CODE_NOT_FOUND = 5
CODE_ALREADY_EXISTS = 6
CODE_PERMISSION_DENIED = 7
CODE_FAILED_PRECONDITION = 9
CODE_UNAUTHENTICATED = 16

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class NodeRingsError(Exception):
    """An error returned by the NodeRings API."""

    def __init__(self, status, code=0, message="", details=None, method="", path=""):
        self.status = status
        self.code = code
        self.message = (message or "").strip()
        self.details = details or []
        self.method = method
        self.path = path
        super().__init__(str(self))

    def quota_info(self):
        for detail in self.details:
            if not isinstance(detail, dict):
                continue
            if str(detail.get("@type", "")).endswith("google.rpc.ErrorInfo") and str(
                detail.get("reason", "")
            ).endswith("LIMIT_REACHED"):
                return detail
        return None

    def is_quota(self):
        return self.quota_info() is not None

    def __str__(self):
        msg = self.message or f"HTTP {self.status}"
        quota = self.quota_info()
        if quota:
            meta = quota.get("metadata") or {}
            if "limit" in meta:
                return (
                    f"{msg} (quota {meta.get('limit_key')}: "
                    f"{meta.get('current')} of {meta.get('limit')} used)"
                )
        where = f" on {self.method} {self.path}" if self.method else ""
        return f"{msg} (HTTP {self.status}{where})"


class TimeoutError_(Exception):  # pylint: disable=invalid-name
    """A wait ran out of time (named to avoid shadowing the builtin)."""


def is_not_found(err):
    return isinstance(err, NodeRingsError) and err.code == CODE_NOT_FOUND


def is_busy(err):
    """The API refuses changes while a VM operation is running."""
    return (
        isinstance(err, NodeRingsError)
        and err.code == CODE_FAILED_PRECONDITION
        and "in progress" in err.message.lower()
    )


def is_ambiguous(err):
    """True when a request may or may not have been applied (lost response)."""
    if isinstance(err, NodeRingsError):
        if err.status in (502, 503, 504):
            return True
        if err.status == 429:
            return not err.is_quota()
        return False
    return isinstance(err, (URLError, OSError))


def decode_error(status, body, method="", path=""):
    code, message, details = 0, "", []
    try:
        data = json.loads((body or b"{}").decode("utf-8"))
        if isinstance(data, dict):
            code = int(data.get("code") or 0)
            message = data.get("message") or ""
            details = data.get("details") or []
        else:
            message = str(data)[:300]
    except (ValueError, TypeError, UnicodeDecodeError):
        message = (body or b"").decode("utf-8", "replace").strip()[:300]
    if not code:
        code = {
            404: CODE_NOT_FOUND,
            409: CODE_ALREADY_EXISTS,
            403: CODE_PERMISSION_DENIED,
            401: CODE_UNAUTHENTICATED,
        }.get(status, 0)
    return NodeRingsError(status, code, message, details, method, path)


def new_uuid():
    return str(uuid.uuid4())


def is_uuid(value):
    return bool(_UUID_RE.match(str(value or "")))


def path_escape(value):
    return quote(str(value), safe="")


def trim_enum(value, prefix):
    """BILLING_TYPE_USAGE -> usage."""
    if not value:
        return ""
    value = str(value)
    value = value.removeprefix(prefix)
    if value == "UNSPECIFIED":
        return ""
    return value.lower()


# Values under these keys are free-form maps (user data); their keys are kept as-is.
OPAQUE_KEYS = frozenset(
    [
        "metadata",
        "continents",
        "countries",
        "regions",
        "environment",
        "instantAppVariables",
        "labels",
    ]
)


def _snake_key(key):
    if not isinstance(key, str):
        return key
    out = []
    for i, ch in enumerate(key):
        if ch.isupper():
            if i and (not key[i - 1].isupper() or (i + 1 < len(key) and key[i + 1].islower())):
                out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out).lstrip("@")


def snake(value):
    """Convert API camelCase keys to snake_case, recursively."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in OPAQUE_KEYS and isinstance(item, dict):
                out[_snake_key(key)] = item
            else:
                out[_snake_key(key)] = snake(item)
        return out
    if isinstance(value, list):
        return [snake(v) for v in value]
    return value


def _urllib_open(url, method, data, headers, timeout):
    req = Request(url, data=data, headers=headers, method=method)
    return urlopen(req, timeout=timeout)  # nosec B310 - the URL is the configured https API


class NodeRingsClient:  # pylint: disable=too-many-public-methods
    """JSON client for the NodeRings public API."""

    def __init__(
        self,
        token,
        api_url=DEFAULT_API_URL,
        organization_id=None,
        timeout=60,
        retries=5,
        user_agent=None,
        opener=None,
        sleep=time.sleep,
    ):
        if not token or not str(token).strip():
            raise ValueError(
                "A NodeRings API token is required. Set noderings:token in the minion or master "
                "config or pillar, or the NODERINGS_TOKEN environment variable. Create a "
                "service account token in the NodeRings console under Organization, Access "
                "Control, Service accounts."
            )
        self.base = (api_url or DEFAULT_API_URL).rstrip("/")
        if not self.base.startswith(("https://", "http://")):
            raise ValueError(f"api_url must be an absolute URL such as {DEFAULT_API_URL}")
        self.token = str(token).strip()
        self.organization_id = (organization_id or "").strip()
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.user_agent = user_agent or f"saltext-noderings/{__version__}"
        self._open = opener or _urllib_open
        self._sleep = sleep

    # -- transport --------------------------------------------------------------

    @staticmethod
    def _backoff(attempt):
        delay = min(0.5 * (2 ** min(attempt, 10)), 10.0)
        return delay / 2 + random.uniform(0, delay / 2)  # nosec B311 - jitter, not security

    @staticmethod
    def _retryable(err, retry_unavailable):
        if isinstance(err, NodeRingsError):
            if err.status == 429:
                return not err.is_quota()
            if err.status in (502, 504):
                return True
            if err.status == 503:
                return retry_unavailable
            return False
        return True  # connection errors

    def request(self, method, path, query=None, body=None, retry_unavailable=None, retries=None):
        """
        Send one API request and return the decoded JSON body, or ``{}``.

        GET requests retry on 503. Writes do not, because some write endpoints
        (DNS) report validation errors as 503.
        """
        if retry_unavailable is None:
            retry_unavailable = method == "GET"
        max_retries = self.retries if retries is None else retries
        url = self.base + path
        if query:
            pairs = []
            for key in sorted(query):
                value = query[key]
                if value is None or value == "":
                    continue
                if isinstance(value, (list, tuple)):
                    pairs.extend((key, str(item)) for item in value)
                elif isinstance(value, bool):
                    pairs.append((key, "true" if value else "false"))
                else:
                    pairs.append((key, str(value)))
            if pairs:
                url += f"?{urlencode(pairs)}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.organization_id:
            headers["X-Organization-ID"] = self.organization_id

        attempt = 0
        while True:
            try:
                with self._open(url, method, data, headers, self.timeout) as resp:
                    raw = resp.read()
                if not raw or not raw.strip():
                    return {}
                return json.loads(raw.decode("utf-8"))
            except HTTPError as exc:
                try:
                    raw = exc.read()
                except Exception:  # pylint: disable=broad-except
                    raw = b""
                err = decode_error(exc.code, raw, method, path)
            except (URLError, OSError) as exc:
                err = exc
            if attempt >= max_retries or not self._retryable(err, retry_unavailable):
                raise err
            self._sleep(self._backoff(attempt))
            attempt += 1

    def get(self, path, query=None):
        return self.request("GET", path, query=query)

    def list_all(self, path, key, query=None):
        params = {"page": "0", "pageSize": "-1"}
        params.update(query or {})
        return self.get(path, params).get(key) or []

    def list_paged(self, path, key, query=None, page_size=100):
        out = []
        page = 0
        while True:
            params = {"page": str(page), "pageSize": str(page_size)}
            params.update(query or {})
            data = self.get(path, params)
            items = data.get(key) or []
            out.extend(items)
            total = int(data.get("totalCount") or 0)
            if len(items) < page_size or len(out) >= total:
                return out
            page += 1

    # -- organization and billing -------------------------------------------------

    def get_organization(self):
        return self.get("/v1/organization").get("organization")

    def get_credit_balance(self):
        return self.get("/v1/credits/balance")

    # -- SSH keys -----------------------------------------------------------------

    def list_ssh_keys(self):
        return self.list_all("/v1/ssh-keys", "sshKeys")

    def create_ssh_key(self, name, public_key):
        return self.request(
            "POST", "/v1/ssh-keys", body={"name": name, "publicKey": public_key}
        ).get("sshKey")

    def delete_ssh_key(self, key_id):
        self.request("DELETE", f"/v1/ssh-keys/{path_escape(key_id)}")

    # -- DNS ----------------------------------------------------------------------

    @staticmethod
    def zone_path(zone_id):
        return f"/v1/dns/zones/{path_escape(zone_id)}"

    def list_dns_zones(self):
        return self.list_all("/v1/dns/zones", "zones")

    def get_dns_zone(self, zone_id):
        return self.get(self.zone_path(zone_id)).get("zone")

    def create_dns_zone(self, name):
        return self.request("POST", "/v1/dns/zones", body={"name": name}).get("zone")

    def delete_dns_zone(self, zone_id, force=False):
        self.request("DELETE", self.zone_path(zone_id), query={"force": "true"} if force else None)

    def list_dns_records(self, zone_id, name=None, rrtype=None):
        return self.list_paged(
            f"{self.zone_path(zone_id)}/records", "records", {"name": name, "type": rrtype}
        )

    def upsert_dns_record(self, zone_id, record):
        return self.request("PUT", f"{self.zone_path(zone_id)}/records", body={"record": record})

    def delete_dns_record(self, zone_id, name, rrtype):
        self.request(
            "DELETE", f"{self.zone_path(zone_id)}/records", query={"name": name, "type": rrtype}
        )

    def get_dnssec(self, zone_id):
        return self.get(f"{self.zone_path(zone_id)}/dnssec")

    def enable_dnssec(self, zone_id):
        return self.request("POST", f"{self.zone_path(zone_id)}/dnssec/enable", body={})

    def disable_dnssec(self, zone_id, force=False):
        return self.request(
            "POST", f"{self.zone_path(zone_id)}/dnssec/disable", body={"force": force}
        )

    # -- firewall -----------------------------------------------------------------

    def list_firewall_address_groups(self, name=None):
        return self.list_all("/v1/firewall/address-groups", "groups", {"name": name})

    def list_firewall_rulesets(self, name=None):
        return self.list_all("/v1/firewall/rulesets", "rulesets", {"name": name})

    def get_firewall_ruleset(self, ruleset_id):
        return self.get(f"/v1/firewall/rulesets/{path_escape(ruleset_id)}")

    def create_firewall_ruleset(self, name):
        out = self.request("POST", "/v1/firewall/rulesets", body={"ruleset": {"name": name}}).get(
            "ruleset"
        )
        if out and out.get("id"):
            return out
        return find_by_name(self.list_firewall_rulesets(name), name)

    def delete_firewall_ruleset(self, ruleset_id, force=False):
        self.request(
            "DELETE",
            f"/v1/firewall/rulesets/{path_escape(ruleset_id)}",
            query={"force": "true"} if force else None,
        )

    def list_firewall_rules(self, ruleset_id):
        rules = self.list_all(f"/v1/firewall/rulesets/{path_escape(ruleset_id)}/rules", "rules")
        return sorted(rules, key=lambda r: int(r.get("position") or 0))

    def create_firewall_rule(self, ruleset_id, rule):
        return self.request(
            "POST",
            f"/v1/firewall/rulesets/{path_escape(ruleset_id)}/rules",
            body={"rule": rule},
        ).get("rule")

    def update_firewall_rule(self, rule_id, rule):
        return self.request(
            "PATCH", f"/v1/firewall/rules/{path_escape(rule_id)}", body={"rule": rule}
        )

    def move_firewall_rule(self, rule_id, target_id, position="FIREWALL_RULE_POSITION_BEFORE"):
        return self.request(
            "POST",
            f"/v1/firewall/rules/{path_escape(rule_id)}/move",
            body={"targetRuleId": target_id, "position": position},
        )

    def delete_firewall_rule(self, rule_id):
        self.request("DELETE", f"/v1/firewall/rules/{path_escape(rule_id)}")

    # -- virtual machines ---------------------------------------------------------

    @staticmethod
    def vm_path(vm_id):
        return f"/v1/virtual-machines/{path_escape(vm_id)}"

    def list_virtual_machines(self):
        return self.list_all("/v1/virtual-machines", "virtualMachines")

    def get_virtual_machine(self, vm_id):
        vm = self.get(self.vm_path(vm_id))
        if not vm or not vm.get("id"):
            raise NodeRingsError(404, CODE_NOT_FOUND, f"virtual machine {vm_id} not found")
        return vm

    def create_virtual_machine(self, body):
        # Never retried blindly: the caller handles ambiguous failures.
        return self.request("POST", "/v1/virtual-machines", body=body, retries=0).get(
            "virtualMachine"
        )

    def update_virtual_machine_tags(self, vm_id, add=None, remove=None):
        return self.request(
            "PATCH", self.vm_path(vm_id), body={"addTags": add or [], "removeTags": remove or []}
        )

    def delete_virtual_machine(self, vm_id):
        self.request("DELETE", self.vm_path(vm_id))

    def vm_action(self, vm_id, action, body=None):
        return self.request(
            "POST", f"{self.vm_path(vm_id)}:{action}", body=body if body is not None else {}
        )

    # -- marketplace and catalog --------------------------------------------------

    def list_marketplace_regions(self, asset_type=None):
        return (
            self.get("/v1/marketplace/regions", {"filterAssetType": asset_type}).get("regions")
            or []
        )

    def list_available_plans(self, country=None, city=None, billing_type=None, asset_type=None):
        return (
            self.get(
                "/v1/marketplace/plans",
                {
                    "filterCountry": country,
                    "filterCity": city,
                    "filterBillingType": billing_type,
                    "filterAssetType": asset_type,
                },
            ).get("plans")
            or []
        )

    def get_marketplace_plan(self, plan_id):
        return self.get(f"/v1/marketplace/plans/{path_escape(plan_id)}")

    def list_providers(self, plan_code, country, city, billing_type=None, ipv6_only=False):
        query = {
            "page": "0",
            "pageSize": "-1",
            "filterPlanCode": plan_code,
            "filterCountry": country,
            "filterCity": city,
            "filterBillingType": billing_type,
        }
        if ipv6_only:
            query["filterIpv6AvailableOnly"] = "true"
        return self.get("/v1/marketplace/providers", query).get("providers") or []

    def get_public_storefront(self, slug):
        return self.get(f"/v1/public/storefronts/{path_escape(slug)}").get("storefront")

    def get_provider(self, provider_id):
        return self.get(f"/v1/marketplace/providers/{path_escape(provider_id)}").get("provider")

    def list_distros(self):
        return self.get("/v1/distros").get("distros") or []


class Waiter:
    """Polls with a growing interval until a check reports done or a timeout passes."""

    def __init__(self, timeout, interval=2.0, max_interval=15.0, sleep=time.sleep, clock=time.time):
        self.deadline = clock() + timeout
        self.interval = interval
        self.max_interval = max_interval
        self._sleep = sleep
        self._clock = clock

    def wait(self, what, check):
        delay = self.interval
        while True:
            done, value = check()
            if done:
                return value
            if self._clock() + delay > self.deadline:
                raise TimeoutError_(f"timed out waiting for {what}")
            self._sleep(delay)
            delay = min(delay * 1.5, self.max_interval)

    def retry_busy(self, func):
        """Run func until the VM stops reporting 'operation in progress'."""
        last = [None]

        def check():
            try:
                return True, func()
            except NodeRingsError as exc:
                if is_busy(exc):
                    last[0] = exc
                    return False, None
                raise

        try:
            return self.wait("the virtual machine to finish its current operation", check)
        except TimeoutError_ as exc:
            raise TimeoutError_(f"{exc} (last error: {last[0]})") from exc


def find_by_name(items, name):
    for item in items or []:
        if item.get("name") == name:
            return item
    return None


def client_from_config(conf, user_agent=None):
    """
    Build a client from a config mapping (``noderings`` in the minion config,
    master config or pillar, or a Salt Cloud provider block).

    Keys: ``token`` (falls back to ``NODERINGS_TOKEN``), ``api_url``,
    ``organization_id``, ``timeout``, ``retries``.
    """
    conf = conf or {}
    token = conf.get("token")
    if not token:
        for var in TOKEN_ENV_VARS:
            if os.environ.get(var):
                token = os.environ[var]
                break
    return NodeRingsClient(
        token=token,
        api_url=conf.get("api_url") or os.environ.get("NODERINGS_API_URL") or DEFAULT_API_URL,
        organization_id=conf.get("organization_id") or os.environ.get("NODERINGS_ORGANIZATION_ID"),
        timeout=int(conf.get("timeout") or 60),
        retries=int(conf.get("retries") if conf.get("retries") is not None else 5),
        user_agent=user_agent,
    )
