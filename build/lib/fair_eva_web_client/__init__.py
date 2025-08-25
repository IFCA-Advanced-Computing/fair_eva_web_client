"""Initialize the fair_eva_web_client package.

This package exposes the Flask application factory via the :func:`create_app`
function.  It also exposes a :func:`main` entry point used by the console
script to run the development server.
"""

from .app import create_app, main  # noqa: F401

__all__ = ["create_app", "main"]