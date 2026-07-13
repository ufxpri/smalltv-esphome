"""Helpers to build a command that re-launches this same program.

Works both when running from source (``python smalltv_widget.py``) and when
frozen by PyInstaller into a single executable / .app bundle.
"""
import os
import sys


def entry_script() -> str:
    """Absolute path to smalltv_widget.py (only meaningful when unfrozen)."""
    here = os.path.dirname(os.path.abspath(__file__))          # .../client/widget
    return os.path.abspath(os.path.join(here, os.pardir, "smalltv_widget.py"))


def self_command(*extra: str) -> list:
    """Command (argv list) that starts this program again with ``extra`` args."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *extra]
    return [sys.executable, entry_script(), *extra]
