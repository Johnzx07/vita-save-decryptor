#!/usr/bin/env python3
"""Regression tests for v0.2.1 bug fixes (run BEFORE and AFTER patches).

Each check prints PASS/FAIL; the process exit code equals the number of
failures (same convention as test_pfs_native.py).

  R1  pfs_native must keep size-0 save members (data loss: empty files
      listed in files.db were silently dropped from the decrypted output).
  R2  main() must route CLI forms like ``--decrypt=PATH`` to the headless
      mode (exact-match routing launched the GUI instead when run from a
      shortcut / another program).
  R3  The GUI must re-enable the key field after a run finishes (success
      or failure): a failed decrypt left key_edit / show_key disabled with
      no way to enter a corrected key without restarting the app.
  R4  Full pipeline end-to-end: psvimg-create (stage 1 pack) ->
      app.core.decrypt_backup -> byte-exact vs vendored ground truth.
      Guards stage 1 + inner-set detection + output move; also the recipe
      used to smoke-test the packaged EXE.

R1/R3 need no network; R3 uses Qt's offscreen platform; R4 uses the bundled
psvimg-create.exe. No user files are touched (temp dirs only).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

FIXTURE = REPO / "tests" / "fixtures" / "hencore_savedata"
GROUND_TRUTH = REPO / "tests" / "fixtures" / "ground_truth"
TOOLS = REPO / "tools" / "psvimgtools"

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


# --------------------------------------------------------------------------- #
# R1 — size-0 files.db entries must appear in the output
# --------------------------------------------------------------------------- #
def r1_empty_files_preserved() -> None:
    from Crypto.Cipher import AES
    from app.pfs_native import decrypt_pfs_set, PFS_SK_ENCKEY

    set_dir = Path(tempfile.mkdtemp(prefix="vrd_r1_set_"))
    out_dir = Path(tempfile.mkdtemp(prefix="vrd_r1_out_"))
    try:
        # Valid pfsSKKey: klicensee = CBC-decrypt(blob) with the public SK key.
        klicensee = b"K" * 16
        blob = AES.new(PFS_SK_ENCKEY, AES.MODE_CBC, b"\x00" * 16).encrypt(klicensee)
        sealed = b"pfsSKKey" + struct.pack("<I", 0x10000) + b"\x00" * 16 + blob + b"\x00" * 16
        (set_dir / "sce_sys").mkdir(parents=True)
        (set_dir / "sce_sys" / "sealedkey").write_bytes(sealed)
        (set_dir / "sce_pfs" / "icv.db").mkdir(parents=True)

        # One files.db page: entry 0 = empty.dat (NENC, size 0), entry 1 =
        # hello.dat (NENC, size 5). Block header @+8 must be non-zero.
        page = bytearray(0x400)
        page[8:12] = b"PGHD"

        def put(i: int, name: str, ftype: int, size: int) -> None:
            fo = 16 + i * 72
            struct.pack_into("<I", page, fo, i)              # idx != 0xFFFFFFFF
            page[fo + 4:fo + 4 + len(name)] = name.encode()  # name, NUL-padded
            io = 16 + 9 * 72 + i * 16
            struct.pack_into("<IHHII", page, io, i, ftype, 0, size, 0)

        put(0, "empty.dat", 0x4000, 0)
        put(1, "hello.dat", 0x4000, 5)
        (set_dir / "sce_pfs" / "files.db").write_bytes(bytes(page))
        (set_dir / "empty.dat").write_bytes(b"")
        (set_dir / "hello.dat").write_bytes(b"hello")

        try:
            produced = decrypt_pfs_set(set_dir, out_dir)
        except Exception as e:  # must not crash on empty members
            check("R1 empty-file entry preserved", False, f"raised {e!r}")
            return
        names = {p.name for p in produced}
        check("R1 empty-file entry preserved", "empty.dat" in names,
              f"output={sorted(names)}")
        check("R1 empty file written with 0 bytes",
              (out_dir / "empty.dat").is_file()
              and (out_dir / "empty.dat").stat().st_size == 0)
        check("R1 non-empty member still copied verbatim",
              (out_dir / "hello.dat").is_file()
              and (out_dir / "hello.dat").read_bytes() == b"hello")
    finally:
        shutil.rmtree(set_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# R2 — CLI routing accepts --decrypt=PATH / --version forms
# --------------------------------------------------------------------------- #
def r2_cli_routing() -> None:
    try:
        from app.main import wants_cli
    except ImportError as e:
        check("R2 CLI routing helper exists", False, str(e))
        return
    check("R2 --decrypt PATH routes to CLI",
          wants_cli(["--decrypt", "C:/backup"]) is True)
    check("R2 --decrypt=PATH routes to CLI",
          wants_cli(["--decrypt=C:/backup", "--key=abcd"]) is True)
    check("R2 --version routes to CLI", wants_cli(["--version"]) is True)
    check("R2 no args stays GUI", wants_cli([]) is False)


# --------------------------------------------------------------------------- #
# R3 — GUI re-enables key field after a failed run
# --------------------------------------------------------------------------- #
def r3_gui_input_not_locked() -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtTest import QTest
    import app.main as m

    app = QApplication.instance() or QApplication(sys.argv[:1])

    tmp = Path(tempfile.mkdtemp(prefix="vrd_r3_"))
    try:
        (tmp / "savedata.psvimg").write_bytes(b"NOT-A-REAL-PSVIMG" * 4)

        # Never block on modal dialogs in this test.
        QMessageBox.critical = staticmethod(lambda *a, **k: 0)
        QMessageBox.warning = staticmethod(lambda *a, **k: 0)

        win = m.MainWindow()
        win._handle_path(tmp / "savedata.psvimg")
        check("R3 precondition: save detected", win.detected is not None)
        win.key_edit.setText("0123456789abcdef" * 4)
        win._start_decrypt()
        check("R3 precondition: inputs disabled while running",
              not win.key_edit.isEnabled())

        # Drive events until the worker finishes (junk psvimg fails fast).
        assert win.worker is not None
        import time
        deadline = time.monotonic() + 60
        while win.worker.isRunning() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        for _ in range(50):          # flush queued sig_err delivery
            app.processEvents()
            time.sleep(0.01)
        check("R3 worker finished", win.worker.isFinished(),
              "worker still running after 60 s")
        check("R3 key field re-enabled after failure",
              win.key_edit.isEnabled())
        check("R3 Show-key checkbox re-enabled after failure",
              win.show_key.isEnabled())
        check("R3 buttons re-enabled after failure",
              win.decrypt_btn.isEnabled() and win.browse_btn.isEnabled()
              and win.out_browse.isEnabled())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# R4 — full pipeline: psvimg-create -> decrypt_backup, byte-exact
# --------------------------------------------------------------------------- #
def r4_e2e_pipeline() -> None:
    from app.core import decrypt_backup

    if not FIXTURE.is_dir() or not GROUND_TRUTH.is_dir():
        check("R4 end-to-end pipeline byte-exact", True, "skipped: fixtures absent")
        return
    create = TOOLS / "psvimg-create.exe"
    if not create.is_file():
        check("R4 end-to-end pipeline byte-exact", True, "skipped: psvimg-create absent")
        return

    key = "0123456789abcdef" * 4
    tmp = Path(tempfile.mkdtemp(prefix="vrd_r4_"))
    try:
        src = tmp / "src" / "savedata"
        src.parent.mkdir(parents=True)
        shutil.copytree(FIXTURE, src)

        pack = tmp / "pack"
        p = subprocess.run([str(create), "-n", "savedata", "-K", key,
                            str(tmp / "src"), str(pack)],
                           capture_output=True, text=True, cwd=str(TOOLS),
                           timeout=300)
        if p.returncode != 0 or not (pack / "savedata.psvimg").is_file():
            check("R4 end-to-end pipeline byte-exact", False,
                  f"psvimg-create failed rc={p.returncode}: {(p.stderr or p.stdout)[:160]}")
            return

        res = decrypt_backup(pack / "savedata.psvimg", key, tmp / "out")
        gt = {q.relative_to(GROUND_TRUTH).as_posix(): q
              for q in GROUND_TRUTH.rglob("*") if q.is_file()}
        ou = {q.relative_to(res.decrypted_dir).as_posix(): q
              for q in res.decrypted_dir.rglob("*") if q.is_file()}
        bad = [n for n, f in gt.items()
               if n not in ou or hashlib.sha256(f.read_bytes()).hexdigest()
               != hashlib.sha256(ou[n].read_bytes()).hexdigest()]
        check("R4 stage-1 pack + full pipeline byte-exact", not bad,
              f"mismatched: {bad}" if bad else f"{len(ou)} files match")
    except Exception as e:
        check("R4 end-to-end pipeline byte-exact", False, f"raised {e!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    r1_empty_files_preserved()
    r2_cli_routing()
    r3_gui_input_not_locked()
    r4_e2e_pipeline()
    print("\n=== REGRESSION SUMMARY ===")
    print(f"OVERALL: {'PASS' if not failures else 'FAIL'}"
          + (f"  ({len(failures)} failing: {failures})" if failures else ""))
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
