"""
A stateful in-memory stand-in for the NodeRings API.

It is plugged in where the client opens URLs, so unit tests run the real
client, retry and order logic against it.
"""

import io
import json
import re
import uuid
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.parse import urlparse

PLAN_ID = "11111111-1111-1111-1111-111111111111"
REGION_ID = "22222222-2222-2222-2222-222222222222"
ACME_ID = "33333333-3333-3333-3333-333333333333"
ACME_PLAN_ID = "44444444-4444-4444-4444-444444444444"
ACME_REGION_ID = "55555555-5555-5555-5555-555555555555"
ACME_OFFER = {
    "organizationId": ACME_ID,
    "organizationName": "Acme Hosting",
    "planId": ACME_PLAN_ID,
    "planRegions": [{"country": "United States", "city": "Ashburn", "regionId": ACME_REGION_ID}],
    "capabilities": ["guest_firewall"],
    "rating": 4.8,
    "planPrices": [{"unitAmount": "0.012", "unitName": "per compute-hour"}],
}


class FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _err(status, code, message):
    return status, {"code": code, "message": message}


class FakeNodeRings:  # pylint: disable=too-many-public-methods
    """A tiny NodeRings: just enough behaviour for the extension's code paths."""

    def __init__(self):
        self.calls = []
        self.extra_providers = []
        self.ssh_keys = {}
        self.zones = {}
        self.records = {}  # zone_id -> {(name, type): {"ttl", "rdata"}}
        self.rulesets = {}
        self.rules = {}  # ruleset_id -> [rule dicts with id, position, rule]
        self.groups = {}
        self.vms = {}
        self.created_vm_bodies = []
        self.routes = [
            ("GET", r"/v1/organization", self.get_org),
            ("GET", r"/v1/ssh-keys", self.list_keys),
            ("POST", r"/v1/ssh-keys", self.create_key),
            ("DELETE", r"/v1/ssh-keys/([^/]+)", self.delete_key),
            ("GET", r"/v1/dns/zones", self.list_zones),
            ("POST", r"/v1/dns/zones", self.create_zone),
            ("GET", r"/v1/dns/zones/([^/]+)", self.get_zone),
            ("DELETE", r"/v1/dns/zones/([^/]+)", self.delete_zone),
            ("GET", r"/v1/dns/zones/([^/]+)/records", self.list_records),
            ("PUT", r"/v1/dns/zones/([^/]+)/records", self.put_record),
            ("DELETE", r"/v1/dns/zones/([^/]+)/records", self.delete_record),
            ("POST", r"/v1/dns/zones/([^/]+)/dnssec/enable", self.enable_dnssec),
            ("GET", r"/v1/dns/zones/([^/]+)/dnssec", self.get_dnssec),
            ("GET", r"/v1/firewall/address-groups", self.list_groups),
            ("GET", r"/v1/firewall/rulesets", self.list_rulesets),
            ("POST", r"/v1/firewall/rulesets", self.create_ruleset),
            ("DELETE", r"/v1/firewall/rulesets/([^/]+)", self.delete_ruleset),
            ("GET", r"/v1/firewall/rulesets/([^/]+)/rules", self.list_rules),
            ("POST", r"/v1/firewall/rulesets/([^/]+)/rules", self.create_rule),
            ("PATCH", r"/v1/firewall/rules/([^/]+)", self.update_rule),
            ("DELETE", r"/v1/firewall/rules/([^/]+)", self.delete_rule),
            ("POST", r"/v1/firewall/rules/([^/]+)/move", self.move_rule),
            ("GET", r"/v1/marketplace/providers", self.list_providers),
            ("GET", r"/v1/marketplace/providers/([^/]+)", self.get_provider),
            ("GET", r"/v1/public/storefronts/([^/]+)", self.get_storefront),
            ("GET", r"/v1/marketplace/plans/([^/]+)", self.get_plan),
            ("GET", r"/v1/marketplace/plans", self.list_plans),
            ("GET", r"/v1/marketplace/regions", self.list_regions),
            ("GET", r"/v1/distros", self.list_distros),
            ("GET", r"/v1/virtual-machines", self.list_vms),
            ("POST", r"/v1/virtual-machines", self.create_vm),
            ("GET", r"/v1/virtual-machines/([^/:]+)", self.get_vm),
            ("PATCH", r"/v1/virtual-machines/([^/:]+)", self.patch_vm),
            ("DELETE", r"/v1/virtual-machines/([^/:]+)", self.delete_vm),
            ("POST", r"/v1/virtual-machines/([^/:]+):([a-z-]+)", self.vm_action),
        ]

    # -- transport ------------------------------------------------------------

    def __call__(self, url, method, data, headers, timeout):  # pylint: disable=unused-argument
        parsed = urlparse(url)
        query = {k: v if len(v) > 1 else v[0] for k, v in parse_qs(parsed.query).items()}
        body = json.loads(data) if data else None
        self.calls.append((method, parsed.path, query, body))
        for meth, pattern, handler in self.routes:
            match = re.fullmatch(pattern, parsed.path)
            if meth == method and match:
                status, out = handler(query, body, *match.groups())
                if status >= 400:
                    raise HTTPError(url, status, "error", {}, io.BytesIO(json.dumps(out).encode()))
                return FakeResponse(out)
        raise HTTPError(
            url,
            404,
            "no route",
            {},
            io.BytesIO(
                json.dumps({"code": 5, "message": f"no route {method} {parsed.path}"}).encode()
            ),
        )

    def writes(self):
        return [(m, p) for m, p, _, _ in self.calls if m != "GET"]

    # -- account / catalog ------------------------------------------------------

    def get_org(self, q, b):  # pylint: disable=unused-argument
        return 200, {"organization": {"id": "org-1", "name": "Test Org"}}

    def list_distros(self, q, b):  # pylint: disable=unused-argument
        return 200, {"distros": [{"distro": "DISTRO_UBUNTU_24_04"}, {"distro": "DISTRO_DEBIAN_13"}]}

    def list_regions(self, q, b):  # pylint: disable=unused-argument
        return 200, {"regions": [{"country": "United States", "city": "Ashburn"}]}

    def list_plans(self, q, b):  # pylint: disable=unused-argument
        return 200, {"plans": [{"planCode": "SC-GC-1C-1G-20", "billingType": "BILLING_TYPE_USAGE"}]}

    def get_plan(self, q, b, plan_id):  # pylint: disable=unused-argument
        return 200, {"id": plan_id, "billingType": "BILLING_TYPE_USAGE"}

    def list_providers(self, q, b):  # pylint: disable=unused-argument
        if q.get("filterCity") != "Ashburn":
            return 200, {"providers": []}
        return 200, {
            "providers": [
                {
                    "organizationId": "prov-1",
                    "organizationName": "Cheap Host",
                    "planId": PLAN_ID,
                    "planRegions": [
                        {"country": "United States", "city": "Ashburn", "regionId": REGION_ID}
                    ],
                    "capabilities": ["guest_firewall"],
                    "planPrices": [{"unitAmount": "0.007", "unitName": "per compute-hour"}],
                },
                ACME_OFFER,
            ]
            + self.extra_providers
        }

    def get_provider(self, q, b, provider_id):  # pylint: disable=unused-argument
        if provider_id != ACME_ID:
            return _err(404, 5, "provider not found")
        return 200, {
            "provider": {
                "provider": {
                    "organizationId": ACME_ID,
                    "organizationName": "Acme Hosting",
                    "rating": 4.8,
                },
                "availablePlans": [
                    {
                        "planId": ACME_PLAN_ID,
                        "planCode": "SC-GC-1C-1G-20",
                        "billingType": "BILLING_TYPE_USAGE",
                        "assetType": "ASSET_TYPE_VM",
                        "regions": [
                            {
                                "country": "United States",
                                "city": "Ashburn",
                                "regionId": ACME_REGION_ID,
                            }
                        ],
                        "planPrices": ACME_OFFER["planPrices"],
                    }
                ],
            }
        }

    def get_storefront(self, q, b, slug):  # pylint: disable=unused-argument
        if slug != "acme-hosting":
            return _err(404, 5, "storefront not found")
        return 200, {"storefront": {"slug": slug, "providerId": ACME_ID}}

    # -- SSH keys ----------------------------------------------------------------

    def list_keys(self, q, b):  # pylint: disable=unused-argument
        return 200, {"sshKeys": list(self.ssh_keys.values())}

    def create_key(self, q, b):  # pylint: disable=unused-argument
        for key in self.ssh_keys.values():
            if key["publicKey"] == b["publicKey"]:
                return _err(409, 6, "SSH key with same fingerprint already exists.")
        key = {"id": str(uuid.uuid4()), "name": b["name"], "publicKey": b["publicKey"]}
        self.ssh_keys[key["id"]] = key
        return 200, {"sshKey": key}

    def delete_key(self, q, b, key_id):  # pylint: disable=unused-argument
        if self.ssh_keys.pop(key_id, None) is None:
            return _err(404, 5, "not found")
        return 200, {}

    # -- DNS -----------------------------------------------------------------------

    def list_zones(self, q, b):  # pylint: disable=unused-argument
        return 200, {"zones": list(self.zones.values())}

    def create_zone(self, q, b):  # pylint: disable=unused-argument
        zone = {
            "id": str(uuid.uuid4()),
            "name": b["name"],
            "state": "DNS_ZONE_STATE_PENDING_NS",
            "assignedNameservers": ["ans1.nrings.io", "ans2.nrings.io"],
            "dnssecMode": "DNSSEC_MODE_UNSIGNED",
        }
        self.zones[zone["id"]] = zone
        self.records[zone["id"]] = {}
        return 200, {"zone": zone}

    def get_zone(self, q, b, zone_id):  # pylint: disable=unused-argument
        if zone_id not in self.zones:
            return _err(404, 5, "zone not found")
        return 200, {"zone": self.zones[zone_id]}

    def delete_zone(self, q, b, zone_id):  # pylint: disable=unused-argument
        self.zones.pop(zone_id, None)
        self.records.pop(zone_id, None)
        return 200, {}

    def list_records(self, q, b, zone_id):  # pylint: disable=unused-argument
        out = []
        for (name, rtype), rec in self.records.get(zone_id, {}).items():
            if q.get("name") and q["name"] not in name:
                continue
            if q.get("type") and q["type"] != rtype:
                continue
            out.append({"name": name, "type": rtype, "ttl": rec["ttl"], "rdata": rec["rdata"]})
        return 200, {"records": out, "totalCount": len(out)}

    def put_record(self, q, b, zone_id):  # pylint: disable=unused-argument
        rec = b["record"]
        self.records[zone_id][(rec["name"], rec["type"])] = {
            "ttl": rec["ttl"],
            "rdata": rec["rdata"],
        }
        return 200, {}

    def delete_record(self, q, b, zone_id):  # pylint: disable=unused-argument
        if self.records[zone_id].pop((q["name"], q["type"]), None) is None:
            return _err(404, 5, "record not found")
        return 200, {}

    def enable_dnssec(self, q, b, zone_id):  # pylint: disable=unused-argument
        self.zones[zone_id]["dnssecMode"] = "DNSSEC_MODE_SIGNED_DS_PENDING"
        return 200, {}

    def get_dnssec(self, q, b, zone_id):  # pylint: disable=unused-argument
        return 200, {"mode": self.zones[zone_id]["dnssecMode"], "ds": "12345 13 2 abcdef"}

    # -- firewall ------------------------------------------------------------------

    def list_groups(self, q, b):  # pylint: disable=unused-argument
        return 200, {"groups": list(self.groups.values())}

    def list_rulesets(self, q, b):  # pylint: disable=unused-argument
        items = [r for r in self.rulesets.values() if not q.get("name") or r["name"] == q["name"]]
        return 200, {"rulesets": items}

    def create_ruleset(self, q, b):  # pylint: disable=unused-argument
        rs = {"id": str(uuid.uuid4()), "name": b["ruleset"]["name"]}
        self.rulesets[rs["id"]] = rs
        self.rules[rs["id"]] = []
        return 200, {"ruleset": rs}

    def delete_ruleset(self, q, b, rs_id):  # pylint: disable=unused-argument
        self.rulesets.pop(rs_id, None)
        self.rules.pop(rs_id, None)
        return 200, {}

    def _renumber(self, rs_id):
        for i, rule in enumerate(self.rules[rs_id]):
            rule["position"] = i

    def _find_rule(self, rule_id):
        for rs_id, rules in self.rules.items():
            for rule in rules:
                if rule["id"] == rule_id:
                    return rs_id, rule
        return None, None

    def list_rules(self, q, b, rs_id):  # pylint: disable=unused-argument
        return 200, {"rules": self.rules.get(rs_id, [])}

    def create_rule(self, q, b, rs_id):  # pylint: disable=unused-argument
        rule = {"id": str(uuid.uuid4()), "position": 0, "rule": b["rule"]}
        self.rules[rs_id].insert(0, rule)
        self._renumber(rs_id)
        return 200, {"rule": rule}

    def update_rule(self, q, b, rule_id):  # pylint: disable=unused-argument
        _, rule = self._find_rule(rule_id)
        rule["rule"] = b["rule"]
        return 200, {}

    def delete_rule(self, q, b, rule_id):  # pylint: disable=unused-argument
        rs_id, rule = self._find_rule(rule_id)
        self.rules[rs_id].remove(rule)
        self._renumber(rs_id)
        return 200, {}

    def move_rule(self, q, b, rule_id):  # pylint: disable=unused-argument
        rs_id, rule = self._find_rule(rule_id)
        rules = self.rules[rs_id]
        rules.remove(rule)
        target = next(i for i, r in enumerate(rules) if r["id"] == b["targetRuleId"])
        rules.insert(target, rule)
        self._renumber(rs_id)
        return 200, {}

    # -- virtual machines ------------------------------------------------------------

    def list_vms(self, q, b):  # pylint: disable=unused-argument
        return 200, {"virtualMachines": list(self.vms.values())}

    def create_vm(self, q, b):  # pylint: disable=unused-argument
        self.created_vm_bodies.append(b)
        vm = {
            "id": str(uuid.uuid4()),
            "hostname": b["hostname"],
            "planId": b["planId"],
            "planCode": "SC-GC-1C-1G-20",
            "regionId": b["regionId"],
            "distro": b.get("distro", "DISTRO_UBUNTU_24_04"),
            "tags": list(b.get("tags") or []),
            "ipv4": "192.0.2.50",
            "crStatus": "CR_STATUS_APPLIED",
            "operationStatus": "OPERATION_STATUS_STARTED",
            "planBillingType": "BILLING_TYPE_USAGE",
        }
        self.vms[vm["id"]] = vm
        return 200, {"virtualMachine": vm}

    def get_vm(self, q, b, vm_id):  # pylint: disable=unused-argument
        if vm_id not in self.vms:
            return _err(404, 5, "not found")
        return 200, self.vms[vm_id]

    def patch_vm(self, q, b, vm_id):  # pylint: disable=unused-argument
        vm = self.vms[vm_id]
        tags = [t for t in vm["tags"] if t not in (b.get("removeTags") or [])]
        tags += [t for t in b.get("addTags") or [] if t not in tags]
        vm["tags"] = tags
        return 200, {}

    def delete_vm(self, q, b, vm_id):  # pylint: disable=unused-argument
        self.vms.pop(vm_id, None)
        return 200, {}

    def vm_action(self, q, b, vm_id, action):  # pylint: disable=unused-argument
        vm = self.vms[vm_id]
        if action == "stop":
            vm["operationStatus"] = "OPERATION_STATUS_STOPPED"
        elif action in ("start", "restart"):
            vm["operationStatus"] = "OPERATION_STATUS_STARTED"
        elif action == "attach-firewall":
            vm["firewallRulesetId"] = b["rulesetId"]
        elif action == "detach-firewall":
            vm.pop("firewallRulesetId", None)
        elif action == "reinstall":
            vm["distro"] = b["distro"]
            vm["reinstallStatus"] = "REINSTALL_STATUS_COMPLETED"
        return 200, {}
