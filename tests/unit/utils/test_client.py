import io
import json
from urllib.error import HTTPError

import pytest

from saltext.noderings.utils.client import NodeRingsClient
from saltext.noderings.utils.client import NodeRingsError
from saltext.noderings.utils.client import TimeoutError_
from saltext.noderings.utils.client import Waiter
from saltext.noderings.utils.client import client_from_config
from saltext.noderings.utils.client import decode_error
from saltext.noderings.utils.client import is_ambiguous
from saltext.noderings.utils.client import is_busy
from saltext.noderings.utils.client import is_uuid
from saltext.noderings.utils.client import snake
from saltext.noderings.utils.client import trim_enum


class Resp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return json.dumps(self.body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def http_error(status, body):
    return HTTPError("https://api", status, "x", {}, io.BytesIO(json.dumps(body).encode()))


def make_client(responses, **kwargs):
    calls = []

    def opener(url, method, data, headers, timeout):  # pylint: disable=unused-argument
        calls.append((method, url, headers, data))
        resp = responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return Resp(resp)

    client = NodeRingsClient(token="t", opener=opener, sleep=lambda s: None, **kwargs)
    return client, calls


def test_requires_token():
    with pytest.raises(ValueError):
        NodeRingsClient(token="")


def test_token_from_environment(monkeypatch):
    monkeypatch.setenv("NODERINGS_TOKEN", "from-env")
    assert client_from_config({}).token == "from-env"
    assert client_from_config({"token": "from-config"}).token == "from-config"


def test_headers_query_and_user_agent():
    client, calls = make_client([{"zones": []}], organization_id="org-1", user_agent="ua/1")
    client.list_dns_zones()
    method, url, headers, _ = calls[0]
    assert method == "GET"
    assert url == "https://api.noderings.com/v1/dns/zones?page=0&pageSize=-1"
    assert headers["Authorization"] == "Bearer t"
    assert headers["X-Organization-ID"] == "org-1"
    assert headers["User-Agent"] == "ua/1"


def test_default_user_agent_names_the_extension():
    client, calls = make_client([{"zones": []}])
    client.list_dns_zones()
    assert calls[0][2]["User-Agent"].startswith("saltext-noderings/")


def test_retries_rate_limit_then_succeeds():
    client, calls = make_client(
        [http_error(429, {"code": 8, "message": "slow down"}), {"sshKeys": [{"id": "1"}]}]
    )
    assert client.list_ssh_keys() == [{"id": "1"}]
    assert len(calls) == 2


def test_quota_errors_are_not_retried():
    quota = {
        "code": 8,
        "message": "limit",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "VM_LIMIT_REACHED",
                "metadata": {"limit": "2", "current": "2", "limit_key": "vms"},
            }
        ],
    }
    client, calls = make_client([http_error(429, quota)])
    with pytest.raises(NodeRingsError) as exc:
        client.list_ssh_keys()
    assert len(calls) == 1
    assert "quota vms: 2 of 2 used" in str(exc.value)


def test_writes_do_not_retry_503_but_reads_do():
    client, calls = make_client([http_error(503, {"message": "bad record"})])
    with pytest.raises(NodeRingsError):
        client.upsert_dns_record("z", {"name": "www"})
    assert len(calls) == 1
    client, calls = make_client([http_error(503, {}), {"zones": []}])
    assert client.list_dns_zones() == []
    assert len(calls) == 2


def test_orders_are_never_retried():
    client, calls = make_client([http_error(502, {})])
    with pytest.raises(NodeRingsError):
        client.create_virtual_machine({"hostname": "x"})
    assert len(calls) == 1


def test_decode_error_and_predicates():
    err = decode_error(404, b"not json")
    assert err.code == 5 and err.message == "not json"
    busy = decode_error(400, json.dumps({"code": 9, "message": "Operation in progress"}).encode())
    assert is_busy(busy)
    assert is_ambiguous(decode_error(504, b"{}"))
    assert not is_ambiguous(decode_error(400, b"{}"))


def test_snake_keeps_free_form_maps():
    out = snake(
        {
            "planCode": "x",
            "metadata": {"someKey": 1},
            "continents": {"EU": "a"},
            "addonSubscriptions": [{"addonId": "1"}],
        }
    )
    assert out == {
        "plan_code": "x",
        "metadata": {"someKey": 1},
        "continents": {"EU": "a"},
        "addon_subscriptions": [{"addon_id": "1"}],
    }
    assert trim_enum("BILLING_TYPE_USAGE", "BILLING_TYPE_") == "usage"
    assert trim_enum("BILLING_TYPE_UNSPECIFIED", "BILLING_TYPE_") == ""


def test_is_uuid():
    assert is_uuid("4d9926e5-9dfe-4ade-9e2c-d5bbb3eed0f7")
    assert not is_uuid("web-1")


def test_waiter_times_out():
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    waiter = Waiter(10, interval=2, sleep=sleep, clock=lambda: clock[0])
    with pytest.raises(TimeoutError_):
        waiter.wait("never", lambda: (False, None))
    waiter = Waiter(10, sleep=sleep, clock=lambda: clock[0])
    seen = []
    assert waiter.wait("soon", lambda: (seen.append(1) or len(seen) == 3, "done")) == "done"
