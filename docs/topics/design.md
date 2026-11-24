# Design and testing

How the extension is built, why, and how it is tested. Read this before changing it.

## Layout

The project follows the [salt-extension copier template](https://github.com/salt-extensions/salt-extension-copier) used by the [salt-extensions](https://github.com/salt-extensions) organization, so it can be adopted there later without restructuring.

| Loader | File | Salt name |
|---|---|---|
| Execution module | `src/saltext/noderings/modules/noderings_mod.py` | `noderings.*` |
| State module | `src/saltext/noderings/states/noderings_mod.py` | `noderings.*` |
| Salt Cloud driver | `src/saltext/noderings/clouds/noderings_mod.py` | driver `noderings` |

Salt finds the loaders through one entry point in `pyproject.toml`:

```toml
[project.entry-points."salt.loader"]
"saltext.noderings" = "saltext.noderings"
```

The shared code in `src/saltext/noderings/utils/` is plain Python without Salt's dunders, so the three loaders and the tests import it directly:

| Module | Responsibility |
|---|---|
| `client.py` | JSON client for the NodeRings API on the standard library: authentication, organization header, User-Agent, retries with backoff, error decoding |
| `compute.py` | Choosing an offer, ordering a VM safely, waiting for provisioning and boot, power, reinstall, firewall attach and detach |
| `marketplace.py` | Turning a plan code and a city into the cheapest (or best rated) offer |
| `dns.py` | Record-value validation, TXT quoting, relative names, comparing record sets |
| `firewall.py` | Rule validation, conversion to the API format, and reconciling a ruleset to an exact ordered list of rules |

The logic is a port of the NodeRings Ansible collection, which is tested against the same API.

## Behaviour that matters

**Orders are never duplicated.** An order is sent once with an idempotency key. If the response is lost (HTTP 502, 503, 504, or a connection error), it is retried with the same key. Free plans ignore the key, so their orders also carry a temporary `salt-create-<uuid>` tag, and after a lost response the extension looks for a VM with that tag instead of ordering again. The tag is removed once the VM is found.

**Writes and reads retry differently.** Reads retry on 429, 502, 503 and 504. Writes do not retry on 503, because some write endpoints report validation errors with that status. Quota errors (429 with a `LIMIT_REACHED` reason) are never retried.

**Waiting.** A new VM is ready when its provisioning status is `APPLIED` and its power state has left `CREATED`. Power, firewall and reinstall operations retry while the API reports another operation in progress.

**States never erase a disk by accident.** `vm_present` reinstalls only with `allow_reinstall: True`. It never moves an existing server to another plan or region, even when a cheaper offer appears.

**Firewall rules keep their IDs.** `firewall_ruleset_present` keeps matching rules, updates changed ones in place, creates and deletes the rest, then moves them into order. It reports no change when the rules already match, whatever the list order of CIDRs inside a rule.

**Secrets stay out of output.** Tokens are only sent in the `Authorization` header. Error messages carry the API message, the method and the path, never request bodies.

## Tests

| Suite | What it covers | Needs | Runs in CI |
|---|---|---|---|
| `tests/unit/utils` | Client retries, quota handling, order never retried, error decoding, DNS and firewall helpers, offer sorting | nothing | every push, Salt 3006 and 3008 |
| `tests/unit/modules`, `states`, `clouds` | Every function, state and cloud action against `tests/unit/fakeapi.py`, a stateful in-memory NodeRings plugged in at the HTTP layer; `test=True` makes no writes; states are idempotent | nothing | every push |
| `tests/functional` | The execution and state modules loaded by Salt's loader, against the real API: catalog, DNS records of several types and DNSSEC, SSH keys, firewall ordering, idempotent states | `NODERINGS_TOKEN` | `main`, when the secret is set |
| `tests/functional` (billed) | Ordering a VM, tags, firewall attach and detach, stop and start, `vm_present` idempotency and update in place, refusal to reinstall silently | plus `NODERINGS_TEST_BILLED=1` | manually |
| `tests/integration` | The real `salt-cloud` command with a provider and a profile: list sizes, create, SSH reachable, list, stop, start, destroy | plus `NODERINGS_TEST_BILLED=1` | manually |

Live tests use random names and delete what they create in `finally` blocks, so a failure does not leave resources behind.

Not covered by automated tests: bootstrapping a minion with `deploy: True`, because the new server has to reach a Salt master. The bootstrap uses Salt Cloud's standard `cloud.bootstrap` with the server's IP, `root` and the profile's `private_key`.
