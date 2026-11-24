"""Sphinx configuration for saltext-noderings."""

import datetime
import os
import sys

sys.path.insert(0, os.path.abspath("../src"))

try:
    from saltext.noderings import __version__ as release
except ImportError:  # pragma: no cover
    release = "0.0.0"

project = "saltext-noderings"
author = "NodeRings"
copyright = f"2026-{datetime.date.today().year}, {author}"  # pylint: disable=redefined-builtin
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinx_inline_tabs",
    "myst_parser",
]
myst_enable_extensions = ["colon_fence", "deflist"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
master_doc = "index"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_title = "NodeRings Salt Extension"
html_static_path = ["_static"]
html_show_sourcelink = True

autodoc_default_options = {"members": True, "show-inheritance": False}
autodoc_mock_imports = []
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "salt": ("https://docs.saltproject.io/en/latest", None),
}
