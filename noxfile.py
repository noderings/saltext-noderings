"""
Development sessions, as in the salt-extension copier template.

    nox -e tests -- tests/unit        # unit tests, no account needed
    nox -e tests -- tests/functional  # live tests, need NODERINGS_TOKEN
    nox -e lint lint-tests docs
"""

import os

import nox

nox.options.default_venv_backend = "uv|virtualenv"
nox.options.reuse_existing_virtualenvs = True
nox.options.error_on_missing_interpreters = False
nox.options.sessions = ["tests", "lint", "lint-tests"]

SALT_REQUIREMENT = os.environ.get("SALT_REQUIREMENT", "salt>=3006")
PYTHON_VERSIONS = ["3.11", "3.12", "3.13"]


@nox.session(python=PYTHON_VERSIONS)
def tests(session):
    """Run the tests. Pass a path to pick a suite; live tests skip without NODERINGS_TOKEN."""
    session.install(SALT_REQUIREMENT, "-e", ".[tests]")
    args = session.posargs or ["tests/unit"]
    session.run("pytest", "-ra", *args, env={"PYTHONPATH": "."})


@nox.session(python="3.12")
def lint(session):
    """Lint the extension with the template's pylint configuration."""
    session.install(SALT_REQUIREMENT, "-e", ".[lint,tests]")
    session.run("pylint", "--rcfile=.pylintrc", "src/saltext/noderings")


@nox.session(name="lint-tests", python="3.12")
def lint_tests(session):
    """Lint the tests."""
    session.install(SALT_REQUIREMENT, "-e", ".[lint,tests]")
    session.run(
        "pylint",
        "--rcfile=.pylintrc",
        "--disable=redefined-outer-name,unused-argument,import-outside-toplevel",
        "tests",
    )


@nox.session(python="3.12")
def docs(session):
    """Build the HTML documentation into docs/_build/html."""
    session.install(SALT_REQUIREMENT, "-e", ".[docs]")
    session.run("sphinx-build", "-W", "-b", "html", "docs", "docs/_build/html")
