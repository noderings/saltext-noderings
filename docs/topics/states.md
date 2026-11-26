# States

The `noderings` state module declares NodeRings resources. Every state supports
`test=True`.

```yaml+jinja
laptop-key:
  noderings.ssh_key_present:
    - name: laptop
    - public_key: ssh-ed25519 AAAA... me@laptop

web-firewall:
  noderings.firewall_ruleset_present:
    - name: web
    - rules:
      - {direction: ingress, action: accept, protocol: tcp, destination_ports: "22", source: {cidrs: [198.51.100.0/24]}}
      - {direction: ingress, action: accept, protocol: tcp, destination_ports: "443", source: {cidrs: [0.0.0.0/0]}}
      - {direction: ingress, action: drop, protocol: any, source: {cidrs: [0.0.0.0/0]}}

web-1:
  noderings.vm_present:
    - plan_code: SC-GC-1C-1G-20
    - country: United States
    - city: Ashburn
    - distro: ubuntu_24_04
    - ssh_keys: [laptop]
    - firewall_ruleset: web
    - tags: [web]
    - require:
      - noderings: laptop-key
      - noderings: web-firewall

example.com:
  noderings.dns_zone_present:
    - dnssec: True

# Salt renders this file before any state runs, so on the first run web-1
# does not exist yet: the record is skipped, and the next run adds it.
{% set web = salt['noderings.get_vm']('web-1') %}
{% if web %}
www:
  noderings.dns_record_present:
    - zone: example.com
    - rtype: A
    - ttl: 300
    - values:
      - {{ web['ipv4'] }}
{% endif %}
```

## What changes in place

| `vm_present` argument | Effect on an existing server |
|---|---|
| `tags`, `firewall_ruleset`, `power_state` | Updated in place |
| `distro` | Reinstalls the server only with `allow_reinstall: True`, because it erases the disk; otherwise the state fails and says so |
| `plan_code`, `plan_id`, `region_id`, `hosting_provider` | Only used to order; an existing server is never moved |

Firewall rules run top to bottom, and traffic that matches no rule is accepted,
so end a ruleset with a drop rule. Each rule takes one port or one range such
as `8000-8100`.
