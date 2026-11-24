# Contributing

Bug reports, fixes, docs and new functions are welcome. Open an issue first for larger changes.

## Layout

The project follows the [salt-extension copier template](https://github.com/salt-extensions/salt-extension-copier).

| Path | What it is |
|---|---|
| `src/saltext/noderings/modules/noderings_mod.py` | Execution module (`noderings.*`) |
| `src/saltext/noderings/states/noderings_mod.py` | State module |
| `src/saltext/noderings/clouds/noderings_mod.py` | Salt Cloud driver |
| `src/saltext/noderings/utils/` | API client, VM order logic, DNS and firewall helpers. Plain Python, no Salt dunders |
| `tests/unit/` | Unit tests against an in-memory fake of the API (`tests/unit/fakeapi.py`) |
| `tests/functional/` | Live tests through Salt's loader against the real API |
| `tests/integration/` | Live `salt-cloud` command-line test |
| `docs/` | Sphinx documentation, published to GitHub Pages |

## Set up

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[tests,lint,docs,dev]"
pre-commit install
```

## Tests

| Command | Needs | Cost |
|---|---|---|
| `pytest tests/unit` | nothing | free |
| `pytest tests/functional` | `NODERINGS_TOKEN` | free (DNS, firewall, SSH keys) |
| `NODERINGS_TEST_BILLED=1 pytest tests/functional tests/integration` | `NODERINGS_TOKEN` | orders hourly servers for a few minutes |

Optional settings for the live tests: `NODERINGS_TEST_DOMAIN` (parent of the throwaway zones, default `example.com`), `NODERINGS_TEST_PLAN_CODE`, `NODERINGS_TEST_COUNTRY`, `NODERINGS_TEST_CITY`. Every live test uses random names and deletes what it creates, also when it fails. Use a test organization with a credit limit.

Run everything CI runs with nox:

```bash
nox -e tests lint lint-tests docs
```

## Changelog

Add a fragment for user-visible changes: `changelog/<issue number>.<type>.md`, where type is `added`, `changed`, `fixed`, `deprecated`, `removed`, `security` or `breaking`. Releases collect them with towncrier.

## Releases

Push a tag such as `v0.2.0`. The release workflow builds the package and publishes it to PyPI with trusted publishing, then publishes the docs.

## Code of conduct

See [CODE-OF-CONDUCT.md](CODE-OF-CONDUCT.md).
