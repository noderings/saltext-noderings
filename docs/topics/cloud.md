# Salt Cloud

The `noderings` Salt Cloud driver orders servers from the NodeRings marketplace
and bootstraps a Salt minion on them over SSH.

## Provider

`/etc/salt/cloud.providers.d/noderings.conf`:

```yaml
my-noderings:
  driver: noderings
  token: <service account token>   # or NODERINGS_TOKEN in the environment
  minion:
    master: salt.example.com
```

## Profiles

`/etc/salt/cloud.profiles.d/noderings.conf`:

```yaml
noderings-ashburn-small:
  provider: my-noderings
  size: SC-GC-1C-1G-20             # plan code
  location: United States/Ashburn  # Country/City
  image: ubuntu_24_04              # distro
  billing_type: usage              # hourly, from prepaid credit
  ssh_keys: [laptop]               # SSH keys stored in NodeRings (a key or a password is required)
  private_key: /root/.ssh/id_ed25519
  firewall_ruleset: web            # optional
  tags: [web]                      # optional
```

| Key | Meaning |
|---|---|
| `size` | Plan code. `salt-cloud --list-sizes my-noderings` lists them. |
| `location` | `Country/City`. `salt-cloud --list-locations my-noderings` lists them. |
| `image` | Distro. `salt-cloud --list-images my-noderings` lists them. |
| `billing_type` | `usage` (hourly, default), `subscription` or `free`. |
| `plan_id`, `region_id` | Order one exact offer instead of the cheapest for `size` and `location`. |
| `provider_id` | Only consider offers from this hosting provider. |
| `sort` | `price` (default) or `rating`. |
| `ssh_keys` | Names of SSH keys stored in NodeRings. The API needs a key or a `password`. |
| `private_key` | Private key matching one of `ssh_keys`, used for the bootstrap. |
| `firewall_ruleset` | Name of a ruleset to attach. |
| `tags` | Tags for the server. |
| `deploy` | `False` to skip the minion bootstrap. |
| `wait_timeout` | Seconds to wait for the server to be running (default 900). |

## Use

```bash
salt-cloud -p noderings-ashburn-small web-1   # order and bootstrap
salt-cloud -Q                                 # list servers
salt-cloud -a stop web-1                      # stop, start, reboot
salt-cloud -a show_instance web-1
salt-cloud -d web-1                           # delete
```

An hourly server stops billing when it is deleted. A monthly subscription is
cancelled at once, without a refund for the rest of the month.
