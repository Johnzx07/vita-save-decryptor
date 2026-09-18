"""PyInstaller entry point for Vita Save Decryptor.

Thin top-level launcher so PyInstaller bundles ``app`` as a proper package —
relative imports inside app/ require a real parent package at frozen runtime.
All application logic lives in :mod:`app.main` (GUI + CLI).
"""
import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
