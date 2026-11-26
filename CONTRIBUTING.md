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
nox -e tests-3.12 lint docs
pre-commit run --all-files
```

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/). release-please reads them to pick the next version and write `CHANGELOG.md`:

| Prefix | Changelog section | Version bump (before 1.0) |
|---|---|---|
| `feat:` | Features | minor |
| `fix:` | Bug Fixes | patch |
| `perf:` | Performance | patch |
| `docs:` | Documentation | patch |
| `deps:` | Dependencies | patch |
| `ci:`, `build:`, `chore:`, `test:`, `refactor:` | hidden | none |
| `feat!:` or a `BREAKING CHANGE:` footer | Breaking changes | minor before 1.0, major after |

The changelog is generated, so there are no towncrier fragments to add (the `changelog/` directory and its towncrier settings come from the copier template and are not used).

## CI

`.github/workflows/ci.yml` runs pre-commit, the unit tests on Salt 3006 to 3008, the docs build and a package check. A single **CI result** check is required on `main`. CI runs on GitHub-hosted runners until a NodeRings self-hosted runner exists and the variable `NODERINGS_RUNNER` is `true`. See [.github/self-hosted-runner.md](.github/self-hosted-runner.md).

## Releases

1. Merge pull requests into `main`. release-please (`.github/workflows/release-please.yml`, using the noderings-release-bot GitHub App) keeps a pull request named `chore: release X.Y.Z` up to date.
2. Merge that pull request when you want to release. release-please pushes the tag `vX.Y.Z` and creates a draft GitHub release.
3. The tag starts `.github/workflows/release.yml`, which:
   - builds the sdist and wheel (setuptools_scm reads the version from the tag) and attests their build provenance
   - publishes them to PyPI with trusted publishing from the `pypi` environment
   - uploads them to the GitHub release and publishes it
4. The docs on GitHub Pages update on every push to `main`.

Do not create tags or releases by hand.

### One-time setup

| Where | What |
|---|---|
| GitHub (done) | Variable `RELEASE_BOT_APP_ID`, secret `RELEASE_BOT_PRIVATE_KEY`, environment `pypi` limited to `v*` tags |
| pypi.org, account `noderings` → *Your account* → *Publishing* → *Add a new pending publisher* (GitHub) | PyPI project name `saltext.noderings`, owner `noderings`, repository `saltext-noderings`, workflow name `release.yml`, environment name `pypi` |
| GitHub → Settings → Secrets and variables → Actions → Variables | `PYPI_TRUSTED_PUBLISHING` = `true` (until then the PyPI step is skipped with a warning) |

A pending publisher turns into a normal trusted publisher with the first upload, which also creates the project on PyPI. If a release ran before PyPI was ready, open Actions → Release → *Run workflow*, pick the tag under *Use workflow from*, and run it again. Files already on PyPI are skipped.

## Code of conduct

See [CODE-OF-CONDUCT.md](CODE-OF-CONDUCT.md).
