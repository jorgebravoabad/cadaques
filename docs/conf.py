"""Sphinx configuration for the CADAQUES documentation."""

import cadaques

project = "CADAQUES"
author = "Jorge Bravo-Abad"
copyright = "2026, Jorge Bravo-Abad"
version = cadaques.__version__
release = cadaques.__version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autosummary_generate = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

html_theme = "furo"
html_title = "CADAQUES"
html_theme_options = {
    "source_repository": "https://github.com/jorgebravoabad/cadaques",
    "source_branch": "main",
    "source_directory": "docs/",
}

templates_path = ["_templates"]
exclude_patterns = ["_build"]
