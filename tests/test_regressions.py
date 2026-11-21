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
        # Layout (per the real fixture): magic[8] @0x0, version u32 @0x8,
        # 20 reserved bytes (iv[16] lives @0x10 inside them), blob @0x20.
        klicensee = b"K" * 16
        blob = AES.new(PFS_SK_ENCKEY, AES.MODE_CBC, b"\x00" * 16).encrypt(klicensee)
        sealed = (b"pfsSKKey" + struct.pack("<I", 0x10000) + b"\x00" * 20
                  + blob + b"\x00" * 16)
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


# --------------------------------------------------------------------------- #
# R5 — save members named like PFS metadata must not resolve to sce_pfs/ files
#      (bug: files.db entry "blocks.db" fell through to sce_pfs/blocks.db,
#       aborting the whole decrypt with "Could not resolve icv_salt")
# --------------------------------------------------------------------------- #
def r5_metadata_name_collision() -> None:
    from Crypto.Cipher import AES
    from app.pfs_native import decrypt_pfs_set, derive_keys, PFS_SK_ENCKEY, SECTOR_SIZE

    set_dir = Path(tempfile.mkdtemp(prefix="vrd_r5_set_"))
    out_dir = Path(tempfile.mkdtemp(prefix="vrd_r5_out_"))
    try:
        klicensee = b"K" * 16
        blob = AES.new(PFS_SK_ENCKEY, AES.MODE_CBC, b"\x00" * 16).encrypt(klicensee)
        # Real layout (per the fixture): magic[8] @0x0, version u32 @0x8,
        # 20 reserved bytes (iv[16] lives @0x10 inside them), blob @0x20.
        sealed = (b"pfsSKKey" + struct.pack("<I", 0x10000) + b"\x00" * 20
                  + blob + b"\x00" * 16)
        (set_dir / "sce_sys").mkdir(parents=True)
        (set_dir / "sce_sys" / "sealedkey").write_bytes(sealed)
        icv_dir = set_dir / "sce_pfs" / "icv.db"
        icv_dir.mkdir(parents=True)

        # Real save member with a metadata-like NAME, encrypted, salt 0x12345678.
        plain = (b"SAVE-MEMBER-CONTENT" * 16)[:192]   # 16-byte aligned
        salt = 0x12345678
        dec_key, twk = derive_keys(klicensee, salt)

        # XTS-ENCRYPT with the decryptor's EXACT tweak chain: the decryptor
        # computes P = D_k1(C ^ t) ^ t, so the inverse is C = E_k1(P ^ t) ^ t.
        from Crypto.Cipher import AES as _AES

        def _gf_x(tw):
            from app.pfs_native import _gf_mult_x_words
            return _gf_mult_x_words(tw)

        seed = struct.pack("<Q", 0) + b"\x00" * 8
        tw = list(struct.unpack("<4I", _AES.new(twk, _AES.MODE_ECB).encrypt(seed)))
        e1 = _AES.new(dec_key, _AES.MODE_ECB)
        ct = bytearray(len(plain))
        for i in range(0, len(plain), 16):
            t = struct.pack("<4I", *tw)
            xored = bytes(x ^ y for x, y in zip(plain[i:i + 16], t))
            enc = e1.encrypt(xored)
            ct[i:i + 16] = bytes(x ^ y for x, y in zip(enc, t))
            tw = _gf_x(tw)
        cipher = bytes(ct)
        # Real PFS members are stored sector-aligned (0x8000) on disk; the ICV
        # covers the padded sector, so write the member that way.
        cipher_file = cipher + b"\x00" * (0x8000 - len(cipher))
        (set_dir / "blocks.db").write_bytes(cipher_file)

        # Store the true ICV root for salt 0x12345678. The decryptor verifies
        # the ICV over the SECTOR-PADDED ciphertext (data trimmed/padded to a
        # whole 0x8000 sector), so the record must carry that root.
        from app.pfs_native import compute_file_icv, SECTOR_SIZE
        padded = cipher_file   # ICV covers the full stored sector
        root = compute_file_icv(padded, klicensee, salt)
        rec = bytearray(0x400)
        rec[0:8] = b"SCEICVDB"
        struct.pack_into("<I", rec, 0x28, 1)        # nSectors
        rec[0x2C:0x2C + 20] = root                  # stored ICV root
        (icv_dir / f"{salt:08x}.icv").write_bytes(bytes(rec))

        # sce_pfs/ metadata as in a real extracted set (the decoys).
        meta = {
            "files.db": b"\x00" * 0x400,   # placeholder; overwritten below
            "blocks.db": b"B" * 512,
            "blocks_grid.db": b"G" * 512,
            "free_blocks.db": b"F" * 512,
        }
        page = bytearray(0x400)
        page[8:12] = b"PGHD"
        fo = 16
        struct.pack_into("<I", page, fo, 0)
        page[fo + 4:fo + 4 + 9] = b"blocks.db"
        io = 16 + 9 * 72
        struct.pack_into("<IHHII", page, io, 0, 0x0000, 0, len(plain), 0)
        meta["files.db"] = bytes(page)
        for n, b in meta.items():
            (set_dir / "sce_pfs" / n).write_bytes(b)

        try:
            produced = decrypt_pfs_set(set_dir, out_dir)
        except Exception as e:
            check("R5 member named blocks.db decrypts (not sce_pfs decoy)",
                  False, f"raised {e!r}")
            return
        got = out_dir / "blocks.db"
        ok = got.is_file() and got.read_bytes()[:len(plain)] == plain
        check("R5 member named blocks.db decrypts (not sce_pfs decoy)", ok,
              f"output={[p.name for p in produced]}")
        # the decrypted member must be the SAVE file, never the metadata bytes
        if got.is_file():
            check("R5 output is save content, not sce_pfs metadata bytes",
                  got.read_bytes() != b"B" * 512)
    finally:
        shutil.rmtree(set_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# R6 — truncated files.db page must raise a clear PipelineError-family error,
#      never a raw struct.error from unpack past the buffer end
# --------------------------------------------------------------------------- #
def r6_truncated_files_db() -> None:
    from app.pfs_native import decrypt_pfs_set

    set_dir = Path(tempfile.mkdtemp(prefix="vrd_r6_set_"))
    out_dir = Path(tempfile.mkdtemp(prefix="vrd_r6_out_"))
    try:
        klicensee = b"K" * 16
        from Crypto.Cipher import AES
        from app.pfs_native import PFS_SK_ENCKEY
        blob = AES.new(PFS_SK_ENCKEY, AES.MODE_CBC, b"\x00" * 16).encrypt(klicensee)
        sealed = (b"pfsSKKey" + struct.pack("<I", 0x10000) + b"\x00" * 16
                  + blob + b"\x00" * 16)
        (set_dir / "sce_sys").mkdir(parents=True)
        (set_dir / "sce_sys" / "sealedkey").write_bytes(sealed)
        (set_dir / "sce_pfs" / "icv.db").mkdir(parents=True)
        (set_dir / "first.dat").write_bytes(b"hi")

        # Page 0 complete (first.dat). Page 1 truncated at 120 bytes: the
        # block-header marker AND the entry name are present, but the info
        # array (offset 664) is beyond the buffer end. Pre-fix this raised a
        # raw struct.error out of unpack_from (crash path with no user-facing
        # message) — must become a clear PfsNativeError.
        def page_with(name: str) -> bytearray:
            p = bytearray(0x400)
            p[8:12] = b"PGHD"
            fo = 16
            struct.pack_into("<I", p, fo, 0)
            p[fo + 4:fo + 4 + len(name)] = name.encode()
            io = 16 + 9 * 72
            struct.pack_into("<IHHII", p, io, 0, 0x4000, 0, 2, 0)
            return p

        full = page_with("first.dat")
        trunc = page_with("second.dat")[:120]   # name ends @90; info array @664
        (set_dir / "sce_pfs" / "files.db").write_bytes(bytes(full) + bytes(trunc))

        try:
            decrypt_pfs_set(set_dir, out_dir)
            check("R6 truncated files.db -> clear PfsNativeError", False,
                  "no error raised (entry silently dropped)")
        except Exception as e:
            from app.pfs_native import PfsNativeError
            check("R6 truncated files.db -> clear PfsNativeError",
                  isinstance(e, PfsNativeError),
                  f"got {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(set_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)


def main() -> int:
    r1_empty_files_preserved()
    r2_cli_routing()
    r3_gui_input_not_locked()
    r4_e2e_pipeline()
    r5_metadata_name_collision()
    r6_truncated_files_db()
    print("\n=== REGRESSION SUMMARY ===")
    print(f"OVERALL: {'PASS' if not failures else 'FAIL'}"
          + (f"  ({len(failures)} failing: {failures})" if failures else ""))
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
