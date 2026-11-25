# NodeRings Salt Extension

[![CI](https://github.com/noderings/saltext-noderings/actions/workflows/ci.yml/badge.svg)](https://github.com/noderings/saltext-noderings/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/saltext.noderings)](https://pypi.org/project/saltext.noderings/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

`saltext-noderings` brings [NodeRings](https://noderings.com) to [SaltStack](https://saltproject.io). NodeRings is a cloud marketplace where independent hosting providers sell virtual machines through one account and one API.

The extension has three parts:

- **Salt Cloud driver.** `salt-cloud -p <profile> web-1` orders a server from the marketplace and bootstraps a minion on it.
- **Execution module.** `salt '*' noderings.<function>` manages servers, DNS, firewalls and SSH keys.
- **State module.** `noderings.vm_present`, `noderings.dns_record_present` and the other states keep them in code. Every state supports `test=True`.

It needs Salt 3006 or newer and nothing else.

## Install

```bash
salt-pip install saltext-noderings   # onedir Salt packages
pip install saltext-noderings        # Salt in a virtualenv
```

## Configure

Create a service account token in the NodeRings console (Organization, Access Control, Service accounts), then put it in pillar or the minion configuration:

```yaml
noderings:
  token: <service account token>
```

Or set `NODERINGS_TOKEN` in the environment of the minion or master.

## Order a server with Salt Cloud

`/etc/salt/cloud.providers.d/noderings.conf`:

```yaml
my-noderings:
  driver: noderings
  token: <service account token>
  minion:
    master: salt.example.com
```

`/etc/salt/cloud.profiles.d/noderings.conf`:

```yaml
noderings-ashburn-small:
  provider: my-noderings
  size: SC-GC-1C-1G-20             # 1 vCPU, 1 GB RAM, 20 GB disk
  location: United States/Ashburn
  image: ubuntu_24_04
  billing_type: usage              # hourly, from prepaid credit
  ssh_keys: [laptop]               # stored in NodeRings
  private_key: /root/.ssh/id_ed25519
```

```bash
salt-cloud -p noderings-ashburn-small web-1
salt-cloud -Q
salt-cloud -d web-1
```

`salt-cloud --list-sizes|--list-locations|--list-images my-noderings` list what you can order.

## Manage resources with the execution module

```bash
salt-call noderings.list_offers SC-GC-1C-1G-20 "United States" Ashburn
salt-call noderings.create_zone example.com
salt-call noderings.set_record example.com www A '["192.0.2.10"]' ttl=300
salt-call noderings.get_vm web-1
salt-call noderings.stop_vm web-1
```

## Declare them in states

```yaml
web-1:
  noderings.vm_present:
    - plan_code: SC-GC-1C-1G-20
    - country: United States
    - city: Ashburn
    - ssh_keys: [laptop]
    - tags: [web]

example.com:
  noderings.dns_zone_present

# Salt renders this file before any state runs, so on the first run web-1
# does not exist yet: the record is skipped, and the next run adds it.
{% set web = salt['noderings.get_vm']('web-1') %}
{% if web %}
www:
  noderings.dns_record_present:
    - zone: example.com
    - rtype: A
    - values:
      - {{ web['ipv4'] }}
{% endif %}
```

`vm_present` updates tags, the firewall and the power state in place. It reinstalls a server for a new `distro` only with `allow_reinstall: True`, because that erases the disk.

## What is covered

| Area | Execution module | States | Salt Cloud |
|---|---|---|---|
| Virtual machines | order, list, get, start, stop, restart, reinstall, tags, delete | `vm_present`, `vm_absent` | create, destroy, list, start, stop, reboot |
| DNS | zones, records of every type, DNSSEC | `dns_zone_*`, `dns_record_*` | |
| Firewall | rulesets, ordered rules, attach and detach | `firewall_ruleset_*` | `firewall_ruleset` in profiles |
| SSH keys | list, create, delete | `ssh_key_*` | `ssh_keys` in profiles |
| Marketplace | regions, plans, offers, distros | | `--list-sizes`, `--list-locations`, `--list-images` |

## Documentation

Full documentation, including every function and state, is at <https://noderings.github.io/saltext-noderings/>.

## Development

```bash
git clone https://github.com/noderings/saltext-noderings.git && cd saltext-noderings
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[tests,lint]"
pytest tests/unit                       # no account needed
NODERINGS_TOKEN=... pytest tests/functional                                   # live, free
NODERINGS_TOKEN=... NODERINGS_TEST_BILLED=1 pytest tests/functional tests/integration   # also orders servers
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).
