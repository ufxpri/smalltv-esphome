#!/usr/bin/env python3
"""Entry point for the SmallTV tray/menu-bar widget.

Run from source:   python smalltv_widget.py
Settings window:   python smalltv_widget.py --settings   (used internally)

This file lives next to the ``smalltv`` and ``widget`` packages so both import
cleanly, both from source and when frozen by PyInstaller.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from widget.app import main

if __name__ == "__main__":
    main()
