#!/usr/bin/env python3
"""Native PFS savedata decryption test (pure Python — no exe, no network).

Fixture: TheOfficialFloW/h-encore v2.0 release, Title ID PCSG90096, savedata set
(vendored at tests/fixtures/hencore_savedata/). Ground truth: the reference
psvpfsparser.exe output captured in a prior run (tests/work/backend_regression/
final/decrypted_save/), which this test compares against byte-for-byte.

What this proves:
  * app/pfs_native.py derives all key material locally from sce_sys/sealedkey
    (no F00D network service, no bundled native tool)
  * XTS-AES decryption is byte-exact on every encrypted file
  * unencrypted files (type & 0x4000) are copied verbatim
  * salt resolution via the ICV Merkle oracle is deterministic

Checks (each PASS/FAIL, exit code = number of failures):
  1. klicensee extraction matches the known fixture value
  2. every encrypted file decrypts byte-exact vs ground truth
  3. unencrypted files are copied verbatim
  4. determinism: a second run produces an identical SHA-256 manifest

No network access required. No native tools required.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "hencore_savedata"


def _find_ground_truth() -> Path | None:
    """Locate the reference psvpfsparser.exe output (byte-exact ground truth).

    Release layout vendors it at tests/fixtures/ground_truth; the dev repo has
    it under tests/work/backend_regression/final/decrypted_save.
    """
    candidates = [
        ROOT / "tests" / "fixtures" / "ground_truth",
        ROOT / "tests" / "work" / "backend_regression" / "final" / "decrypted_save",
    ]
    for c in candidates:
        if c.is_dir() and any(p.is_file() for p in c.rglob("*")):
            return c
    return None


GROUND_TRUTH = _find_ground_truth()

# Known fixture klicensee (identity mapping of pfsSKKey__EncKey for this set).
EXPECTED_KLICENSEE = bytes.fromhex("00298CDF4428E72C8785DAE0923C60BD")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    from app.pfs_native import decrypt_pfs_set, extract_klicensee

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}"
              + (f" — {detail}" if detail and not ok else ""))

    # --- precondition -------------------------------------------------------
    check("fixture present", FIXTURE.is_dir(), str(FIXTURE))
    sealedkey = FIXTURE / "sce_sys" / "sealedkey"
    check("fixture sealedkey present", sealedkey.is_file())
    if not (FIXTURE.is_dir() and sealedkey.is_file()):
        _report(results)
        return len([r for r in results if not r[1]])

    # --- 1. klicensee extraction -------------------------------------------
    kl = extract_klicensee(sealedkey.read_bytes())
    check("klicensee matches known fixture value",
          kl == EXPECTED_KLICENSEE, f"got {kl.hex()}")

    # --- run native decryption (twice, for determinism) ---------------------
    out1 = Path(tempfile.mkdtemp(prefix="pfs_native_"))
    out2 = Path(tempfile.mkdtemp(prefix="pfs_native_"))
    try:
        files1 = decrypt_pfs_set(FIXTURE, out1)
        files2 = decrypt_pfs_set(FIXTURE, out2)

        # --- 2. byte-exact vs ground truth ----------------------------------
        if GROUND_TRUTH is not None:
            truth_files = {p.relative_to(GROUND_TRUTH): p
                           for p in GROUND_TRUTH.rglob("*") if p.is_file()}
            check("ground truth present", len(truth_files) > 0, str(GROUND_TRUTH))

            n_ok = 0
            for rel, tp in sorted(truth_files.items()):
                op = out1 / rel
                ok = (op.is_file() and sha256(op) == sha256(tp))
                if ok:
                    n_ok += 1
                else:
                    check(f"byte-exact {rel}", False,
                          "missing or hash mismatch")
            check("all ground-truth files byte-exact",
                  n_ok == len(truth_files),
                  f"{n_ok}/{len(truth_files)} matched")

            # --- 3. unencrypted files copied verbatim -----------------------
            for rel in (Path("sce_sys/param.sfo"), Path("sce_sys/sealedkey")):
                op = out1 / rel
                src = FIXTURE / rel
                ok = (op.is_file() and sha256(op) == sha256(src))
                check(f"verbatim copy {rel}", ok, "missing or differs")

            # --- 4. determinism ---------------------------------------------
            def manifest(out: Path) -> dict[str, str]:
                return {str(p.relative_to(out)): sha256(p)
                        for p in out.rglob("*") if p.is_file()}
            check("deterministic across runs",
                  manifest(out1) == manifest(out2), "manifests differ")

            # --- output structure sanity ------------------------------------
            check("system.dat present and non-empty",
                  (out1 / "system.dat").is_file()
                  and (out1 / "system.dat").stat().st_size > 0)
        else:
            print("[SKIP] ground truth not found — byte-exact checks skipped")

    finally:
        shutil.rmtree(out1, ignore_errors=True)
        shutil.rmtree(out2, ignore_errors=True)

    _report(results)
    return len([r for r in results if not r[1]])


def _report(results: list[tuple[str, bool, str]]) -> None:
    print("\n=== NATIVE PFS DECRYPTION SUMMARY ===")
    for name, ok, detail in results:
        line = f"{'PASS' if ok else 'FAIL'}  {name}"
        if not ok and detail:
            line += f" — {detail}"
        print(line)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print("OVERALL:", "PASS" if n_fail == 0 else f"FAIL ({n_fail})")


if __name__ == "__main__":
    sys.exit(main())
