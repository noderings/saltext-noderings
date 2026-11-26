from saltext.noderings.utils import firewall


def test_firewall_validation():
    ok = {
        "direction": "ingress",
        "action": "accept",
        "protocol": "tcp",
        "destination_ports": "443",
        "source": {"cidrs": ["0.0.0.0/0"]},
    }
    assert firewall.validate_rule(ok, 0) is None
    assert "no ports" in firewall.validate_rule(dict(ok, protocol="icmp"), 0)
    assert "one port" in firewall.validate_rule(dict(ok, destination_ports="80,443"), 0)
    assert "must be the only entry" in firewall.validate_rule(
        dict(ok, source={"cidrs": ["0.0.0.0/0", "10.0.0.0/8"]}), 0
    )
    assert "source or destination" in firewall.validate_rule(dict(ok, source=None), 0)
    assert "unknown protocol" in firewall.validate_rule(dict(ok, protocol="bogus"), 0)


class FakeRules:
    """Minimal ruleset store implementing the client calls reconcile() uses."""

    def __init__(self, bodies):
        self.rules = [{"id": f"r{i}", "rule": b} for i, b in enumerate(bodies)]
        self.n = len(bodies)
        self.ops = []

    def list_firewall_rules(self, rs):  # pylint: disable=unused-argument
        return [dict(r, position=i) for i, r in enumerate(self.rules)]

    def update_firewall_rule(self, rid, body):
        self.ops.append(("update", rid))
        next(r for r in self.rules if r["id"] == rid)["rule"] = body

    def delete_firewall_rule(self, rid):
        self.ops.append(("delete", rid))
        self.rules = [r for r in self.rules if r["id"] != rid]

    def create_firewall_rule(self, rs, body):  # pylint: disable=unused-argument
        rid = f"r{self.n}"
        self.n += 1
        self.ops.append(("create", rid))
        self.rules.insert(0, {"id": rid, "rule": body})
        return {"id": rid}

    def move_firewall_rule(  # pylint: disable=unused-argument
        self, rid, target, position="FIREWALL_RULE_POSITION_BEFORE"
    ):
        self.ops.append(("move", rid))
        r = next(x for x in self.rules if x["id"] == rid)
        self.rules.remove(r)
        self.rules.insert(next(i for i, x in enumerate(self.rules) if x["id"] == target), r)


def body(port):
    return firewall.rule_to_api(
        {
            "direction": "ingress",
            "action": "accept",
            "protocol": "tcp",
            "destination_ports": str(port),
            "source": {"cidrs": ["0.0.0.0/0"]},
        },
        {},
    )


def test_reconcile_keeps_ids_and_order():
    store = FakeRules([body(22), body(80), body(443)])
    final = firewall.reconcile(store, "rs", [body(443), body(22), body(8080)])
    assert [r["rule"]["destinationPorts"] for r in final] == ["443", "22", "8080"]
    ids = [r["id"] for r in final]
    assert ids[0] == "r2" and ids[1] == "r0"
    assert ("update", "r1") in store.ops  # 80 became 8080 in place
    assert not any(op == "create" for op, rid in store.ops)


def test_reconcile_creates_and_deletes():
    store = FakeRules([body(22), body(80)])
    final = firewall.reconcile(store, "rs", [body(1), body(2), body(3)])
    assert [r["rule"]["destinationPorts"] for r in final] == ["1", "2", "3"]
    final = firewall.reconcile(store, "rs", [])
    assert final == []
    assert not firewall.plan_rules([], [])
    assert firewall.plan_rules(store.list_firewall_rules("rs"), [body(1)])
