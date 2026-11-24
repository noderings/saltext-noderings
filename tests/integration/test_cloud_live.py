"""
Salt Cloud end to end against the live NodeRings API: the real ``salt-cloud``
command with a provider and profile, as a user runs it.

It orders an hourly server for a few minutes, so it needs NODERINGS_TOKEN and
NODERINGS_TEST_BILLED=1. Bootstrapping a minion needs a reachable master, so
the profile sets ``deploy: False``; the test checks SSH instead.
"""

import json
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

from saltext.noderings.utils.client import client_from_config
from tests.live import billed
from tests.live import plan
from tests.live import unique

pytestmark = [pytest.mark.live, pytest.mark.billed, billed]

SALT_CLOUD = str(pathlib.Path(sys.executable).parent / "salt-cloud")


@pytest.fixture
def ssh_key(tmp_path):
    """A throwaway key pair stored in NodeRings; the API needs a key or a password."""
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "id")], check=True
    )
    client = client_from_config({})
    key = client.create_ssh_key(unique("saltext"), (tmp_path / "id.pub").read_text().strip())
    try:
        yield {"name": key["name"], "private": str(tmp_path / "id")}
    finally:
        client.delete_ssh_key(key["id"])


@pytest.fixture
def cloud_dir(tmp_path, ssh_key):
    conf = tmp_path / "conf"
    (conf / "cloud.providers.d").mkdir(parents=True)
    (conf / "cloud.profiles.d").mkdir()
    for sub in ("cache", "pki", "run", "log"):
        (tmp_path / sub).mkdir()
    (conf / "master").write_text(
        f"root_dir: {tmp_path}\ncachedir: {tmp_path}/cache\npki_dir: {tmp_path}/pki\n"
        f"sock_dir: {tmp_path}/run\nlog_file: {tmp_path}/log/master\n"
    )
    (conf / "cloud").write_text(f"log_file: {tmp_path}/log/cloud\n")
    (conf / "cloud.providers.d" / "noderings.conf").write_text("nr-test:\n  driver: noderings\n")
    p = plan()
    (conf / "cloud.profiles.d" / "noderings.conf").write_text(
        "nr-small:\n"
        "  provider: nr-test\n"
        f"  size: {p['plan_code']}\n"
        f"  location: {p['country']}/{p['city']}\n"
        "  image: ubuntu_24_04\n"
        "  billing_type: usage\n"
        "  tags: [saltext-test]\n"
        f"  ssh_keys: [{ssh_key['name']}]\n"
        f"  private_key: {ssh_key['private']}\n"
        "  deploy: False\n"
    )
    return conf


def _cloud(conf, *args, timeout=1200):
    env = dict(os.environ)
    proc = subprocess.run(
        [SALT_CLOUD, "-c", str(conf), "--out=json", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except ValueError as exc:
        raise AssertionError(f"salt-cloud printed no JSON:\n{proc.stdout}\n{proc.stderr}") from exc


def _wait_for_port(host, port=22, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5):
                return True
        except OSError:
            time.sleep(5)
    return False


def test_salt_cloud_lifecycle(cloud_dir):
    name = unique("saltext")
    sizes = _cloud(cloud_dir, "--list-sizes", "nr-test")
    assert plan()["plan_code"] in json.dumps(sizes)
    try:
        created = _cloud(cloud_dir, "-p", "nr-small", name)
        node = created[name]
        assert node["hostname"] == name and node["ipv4"]
        assert _wait_for_port(node["ipv4"]), "SSH never answered"

        listed = _cloud(cloud_dir, "-Q")
        flat = json.dumps(listed)
        assert name in flat and node["ipv4"] in flat

        stopped = _cloud(cloud_dir, "-a", "stop", name)
        assert '"stopped"' in json.dumps(stopped)
        started = _cloud(cloud_dir, "-a", "start", name)
        assert '"running"' in json.dumps(started)
    finally:
        destroyed = _cloud(cloud_dir, "-d", name)
        assert name in json.dumps(destroyed)
