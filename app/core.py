"""Vita Save Decryptor — production backend (Phase 3).

Wraps the regression-verified two-stage pipeline:

    Stage 1  psvimg-extract   CMA .psvimg -> PFS set tree
    Stage 2  native Python    encrypted PFS set -> decrypted files
                             (keys derived locally from sealedkey; no network)

Stage 2 is a pure-Python implementation (:mod:`app.pfs_native`) that derives the
per-file keys from the sealed key and resolves salts via the ICV Merkle oracle,
so it needs neither ``psvpfsparser.exe`` nor the F00D online service. Only stage
1 still uses a bundled native tool (``psvimg-extract.exe``) to unpack the CMA
archive. See docs/DEVELOPMENT_STATUS.md for the locked reference state.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.pfs_native import decrypt_pfs_set, PfsNativeError

KEY_RE = re.compile(r"[0-9a-fA-F]{64}")  # parse_key() requires exactly 64 hex chars


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class PipelineError(Exception):
    """Fatal pipeline failure with a user-facing message."""


# --------------------------------------------------------------------------- #
# Tool location (dev tree vs PyInstaller _MEIPASS)
# --------------------------------------------------------------------------- #
def resource_root() -> Path:
    """Directory that contains ``tools/`` and ``resources/``.

    Dev: the project root. Frozen one-file EXE: sys._MEIPASS (PyInstaller
    extracts bundled datas there at startup).
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def tool_paths() -> dict[str, Path]:
    """Locate the bundled native tools (stage 1 only).

    Stage 2 is pure Python (:mod:`app.pfs_native`), so only ``psvimg-extract.exe``
    must be present. Missing tool -> PipelineError with a clear message.
    """
    root = resource_root() / "tools"
    psvimg_extract = root / "psvimgtools" / "psvimg-extract.exe"
    missing = [str(p) for p in (psvimg_extract,) if not p.is_file()]
    if missing:
        raise PipelineError(
            "Bundled native tool(s) missing:\n  " + "\n  ".join(missing)
            + "\nThe application was packaged without its tools."
        )
    return {"psvimg_extract": psvimg_extract}


# --------------------------------------------------------------------------- #
# Backup / save detection
# --------------------------------------------------------------------------- #
@dataclass
class DetectedSave:
    backup_root: Path          # folder the user pointed at (or file's parent chain)
    psvimg: Path               # the .psvimg to decrypt
    title_id: str | None       # e.g. PCSG90096 (from path structure, best effort)
    app_id: str | None         # 10-digit AID if present in path
    set_name: str | None = None


def detect_save(user_path: Path) -> DetectedSave:
    """Locate a .psvimg inside the user-supplied folder or file.

    Accepts (any of):
      * a CMA backup root containing APP/<AID>/<TITLE>/savedata/*.psvimg
      * any folder that contains a *.psvimg somewhere below it
      * a direct path to a .psvimg file
    """
    user_path = Path(user_path)
    if not user_path.exists():
        raise PipelineError(f"Path does not exist:\n{user_path}")

    if user_path.is_file():
        if user_path.suffix.lower() != ".psvimg":
            raise PipelineError(
                f"Not a PSVIMG file (expected .psvimg):\n{user_path.name}"
            )
        psvimg = user_path
    else:
        candidates = sorted(user_path.rglob("*.psvimg"))
        if not candidates:
            raise PipelineError(
                "No savedata.psvimg / *.psvimg found under:\n"
                f"{user_path}\n\nPoint the app at your CMA backup folder."
            )
        # Prefer a file literally named savedata.psvimg, else the first.
        psvimg = next((c for c in candidates if c.name.lower() == "savedata.psvimg"),
                      candidates[0])

    title_id = app_id = None
    parts = [p.upper() for p in psvimg.parts]
    # APP/<AID>/<TITLE>/... pattern anywhere in the path
    for i, part in enumerate(parts):
        if part == "APP" and i + 2 < len(parts):
            aid, title = parts[i + 1], parts[i + 2]
            if re.fullmatch(r"\d{10}", aid):
                app_id = aid
            if re.fullmatch(r"[A-Z]{4}\d{5}", title):
                title_id = title
    # Fallback: set-name pattern ux0_temp_game_<TITLE>_savedata_...
    m = re.search(r"ux0_temp_game_([A-Z0-9]+)_savedata", psvimg.parent.name, re.I)
    if not title_id and m:
        title_id = m.group(1).upper()

    return DetectedSave(backup_root=user_path if user_path.is_dir() else psvimg.parent,
                        psvimg=psvimg, title_id=title_id, app_id=app_id)


# --------------------------------------------------------------------------- #
# Subprocess runner (same hardened rules as vita_decrypt.py)
# --------------------------------------------------------------------------- #
def _run(exe: Path, args: list[str], cwd: Path | None = None, timeout_s: int = 600):
    cmd = [str(exe)] + [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              cwd=str(cwd) if cwd else None, timeout=timeout_s)
    except FileNotFoundError as e:
        raise PipelineError(f"Could not launch {exe.name}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise PipelineError(f"{exe.name} timed out after {timeout_s}s") from e
    return proc


# --------------------------------------------------------------------------- #
# Pipeline result
# --------------------------------------------------------------------------- #
@dataclass
class DecryptResult:
    decrypted_dir: Path
    title_id: str | None
    game_title: str | None
    file_count: int
    files: list[Path] = field(default_factory=list)


STAGES = [
    "Checking backup...",
    "Locating PSVIMG...",
    "Extracting CMA archive...",
    "Locating encrypted PFS save...",
    "Decrypting PFS...",
    "Validating output...",
]


def _validate_sfo(param_sfo: Path, expected_title: str | None) -> tuple[bool, str]:
    """Decrypted param.sfo must be a valid SFO and (if we know the title) contain it."""
    if not param_sfo.is_file():
        return False, "sce_sys/param.sfo missing from decrypted output"
    data = param_sfo.read_bytes()
    # SFO magic: 0x00 'P' 'S' 'F' at offset 0..3
    if len(data) < 4 or data[1:4] != b"PSF":
        return False, "param.sfo is not a valid SFO file (bad magic)"
    if expected_title:
        # Title ID appears as an ASCII field value inside the SFO.
        if expected_title.encode("ascii") in data:
            return True, f"SFO valid; contains title {expected_title}"
        return False, f"SFO valid but does not contain expected title {expected_title}"
    return True, "SFO valid"


def _find_inner_set(extract_dir: Path) -> Path | None:
    """The PFS set root = dir containing VITA_PATH.TXT (and sce_sys/)."""
    for vp in extract_dir.rglob("VITA_PATH.TXT"):
        if (vp.parent / "sce_sys").is_dir():
            return vp.parent
    # Fallback: sealedkey under sce_sys
    for sk in extract_dir.rglob("sealedkey"):
        if sk.parent.name == "sce_sys":
            return sk.parent.parent
    return None


def _extract_game_title(param_sfo: Path) -> str | None:
    """Best-effort game title from decrypted param.sfo (TITLE field)."""
    try:
        data = param_sfo.read_bytes()
    except OSError:
        return None
    # SFO fields are NUL-terminated ASCII; grab printable runs and look for a
    # plausible title near the 'TITLE' key. Simple heuristic, display-only.
    m = re.search(rb"TITLE\x00", data)
    if not m:
        return None
    tail = data[m.end():m.end() + 256]
    run = re.match(rb"[ -~]{3,80}?", tail)
    if run and run.group(0).strip():
        return run.group(0).decode("ascii", "replace").strip() or None
    return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def decrypt_backup(user_path: Path | str, key: str, out_dir: Path | str,
                   progress_cb=None) -> DecryptResult:
    """Run the full verified pipeline. progress_cb(stage_index, stage_text)."""
    def prog(i):
        if progress_cb:
            progress_cb(i, STAGES[i])

    tools = tool_paths()  # raises PipelineError if bundled tools missing
    key = (key or "").strip().strip('"')
    if not KEY_RE.fullmatch(key):
        raise PipelineError(
            "Invalid CMA key.\n\nThe key must be exactly 64 hexadecimal characters —\n"
            "the one shown by cma.henkaku.xyz when you created the backup."
        )

    prog(0)
    # The bundled Cygwin tools require native (absolute) Windows paths; a relative
    # user path makes psvimg-extract fail with "open: No such file or directory".
    det = detect_save(Path(user_path).expanduser().resolve())

    prog(1)
    psvimg = det.psvimg
    if not psvimg.is_file():
        raise PipelineError(f"PSVIMG disappeared: {psvimg}")

    out_dir = Path(out_dir).expanduser().resolve()
    work = out_dir / "work"
    shutil.rmtree(work, ignore_errors=True)
    extract_dir = work / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    prog(2)
    p = _run(tools["psvimg_extract"], ["-K", key, psvimg, extract_dir],
             cwd=tools["psvimg_extract"].parent)
    if p.returncode != 0:
        raise PipelineError(
            "CMA extraction failed (stage 1).\n\n"
            f"{(p.stderr or p.stdout).strip()[:500]}\n\n"
            "Most common cause: wrong CMA key."
        )

    prog(3)
    inner_set = _find_inner_set(extract_dir)
    if inner_set is None:
        raise PipelineError("No PFS save set found inside the extracted archive.")
    sealedkey = inner_set / "sce_sys" / "sealedkey"
    if not sealedkey.is_file():
        raise PipelineError(
            f"Expected sealed key at:\n{sealedkey}\n\nThe archive does not look like a Vita savedata set."
        )

    prog(4)
    pfs_out = work / "decrypted"
    pfs_out.mkdir(parents=True, exist_ok=True)
    try:
        produced = decrypt_pfs_set(inner_set, pfs_out)
    except PfsNativeError as e:
        raise PipelineError(f"PFS decryption failed (stage 2):\n{e}") from e

    if not produced:
        raise PipelineError(
            "PFS decryption failed (stage 2) — no files were produced.\n\n"
            f"The archive does not look like a Vita savedata set, or the sealed key at:\n{sealedkey}\n"
            "did not match this save."
        )

    prog(5)
    param_sfo = pfs_out / "sce_sys" / "param.sfo"
    ok, msg = _validate_sfo(param_sfo, det.title_id)
    if not ok:
        raise PipelineError(f"Output validation failed:\n{msg}")

    # Move decrypted tree to the final output location (originals untouched).
    final_dir = Path(out_dir) / "decrypted_save"
    shutil.rmtree(final_dir, ignore_errors=True)
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pfs_out), str(final_dir))

    files = sorted(f for f in final_dir.rglob("*") if f.is_file())
    return DecryptResult(
        decrypted_dir=final_dir,
        title_id=det.title_id,
        game_title=_extract_game_title(final_dir / "sce_sys" / "param.sfo"),
        file_count=len(files),
        files=files,
    )
