"""Centralized resource manager for Vita Save Decryptor.

Single place that resolves every bundled asset in BOTH modes:

  * development mode   — files live in the project tree (tools/, app/resources/)
  * PyInstaller onefile — everything is extracted to ``sys._MEIPASS`` at startup

The native tool paths are delegated to :func:`app.core.tool_paths` so there is
exactly one implementation of the verified tool-resolution rules. UI assets
(logo / icon) live in ``app/resources/`` next to the code in dev mode and in
``<bundle>/resources/`` when frozen (see VitaSaveDecryptor.spec datas).

The user is NEVER asked for paths to psvimg-extract.exe or psvpfsparser.exe —
they are internal application components.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .core import PipelineError, resource_root, tool_paths


def ui_resource_dir() -> Path:
    """Directory holding UI assets (logo.png, VitaSaveDecryptor.ico)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "resources"
    return Path(__file__).resolve().parent / "resources"


def logo_path() -> Path:
    p = ui_resource_dir() / "logo.png"
    if not p.is_file():
        raise PipelineError(f"UI resource missing (packaged app is incomplete): {p}")
    return p


def icon_path() -> Path:
    """Multi-size .ico used for the EXE, window, taskbar and About dialog."""
    p = ui_resource_dir() / "VitaSaveDecryptor.ico"
    if not p.is_file():
        raise PipelineError(f"UI resource missing (packaged app is incomplete): {p}")
    return p


def bundled_tools() -> dict[str, Path]:
    """Verified native tools. Raises PipelineError with a clear message if the
    application was packaged without them."""
    return tool_paths()


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
