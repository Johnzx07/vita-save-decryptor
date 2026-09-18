# Vita Save Decryptor — Development Status & Locked Reference State

Updated: 2026-09-12 (goal revision 4: stale "Phase 3 locked until Phase 2c" rule CANCELLED and removed from all project state)

## Goal revision 4 — phase status
| Phase | Status |
|---|---|
| Phase 1 (tool audit + CLI verification) | **PASS** |
| Phase 2a (CMA/PSVIMG round trip) | **PASS** |
| Phase 2b (PFS decrypt on extracted set) | **PASS** |
| Phase 2c Personal CMA Compatibility Test | **OPTIONAL / PENDING** — `PRIVATE_CMA_COMPATIBILITY_TEST = PENDING_OPTIONAL`; personal backup not currently available; does NOT block development or release-candidate construction. Do not request it again during this goal. |
| Phase 3 GUI (PySide6) | **PASS** — built and verified in dev mode AND from the packaged EXE |
| Phase 4 EXE Packaging (onefile) | **PASS** — `dist\VitaSaveDecryptor.exe` built + regression-tested vs PCSG90096 fixture |

The verified PCSG90096 fixture is the official development and regression fixture.
GUI_LOCKED = FALSE · PERSONAL_SAVE_REQUIRED_FOR_GUI = FALSE · PERSONAL_SAVE_REQUIRED_FOR_PACKAGING = FALSE

## Test status (per goal doc)

| Item | Status |
|---|---|
| Public verified integration fixture (PCSG90096) | **PASS** |
| Private user CMA save | **PRIVATE_CMA_COMPATIBILITY_TEST = PENDING_OPTIONAL** (optional, non-blocking; backup not currently available — do not request again during this goal) |

## LOCKED VERIFIED PIPELINE — reference state

Do not rewrite the known-good decryption pipeline without a regression-tested reason.

### Fixture source
- Repo: `TheOfficialFloW/h-encore` (official), release **v2.0** zip (`h-encore.zip`)
- Title ID: **PCSG90096**
- Set path in release: `savedata/ux0_temp_game_PCSG90096_savedata_PCSG90096/`
- Vendored copy (official regression fixture): `tests/fixtures/hencore_savedata/` — 15 files, SHA-verified identical to the release zip contents.

### Fixture hashes (SHA-256)
Aggregate manifest hash (sorted per-file sha256 list): **c291c3243f4bfcd9be9de889cadd59e86804ab7d7a65353ee7abb8e20fac14a8**

Per file:
```
4a85b5cf6afe26c9b95ead5d53b7f439c6aeb745e0b7d6839e91017e68af202a  sce_pfs/files.db
401112b1a6e527fb35ad27e281eec25634ea7fdd8bc820e389c52d60add7ef1e  sce_pfs/icv.db/2da7772c.icv
252c6d50184e5517d795a870de0c1fc1f47f3bfe789152593cd64a8ba342c4ee  sce_pfs/icv.db/58b8e8cf.icv
f315acca6ceffc1e68855307366508bac6875e29c4e61f5586b5997a47d8f34e  sce_pfs/icv.db/59f40480.icv
b5a3ffd6ecc9b7e673e819754b2067ee888705b6d46591ac0efab54c2e0c3cc5  sce_pfs/icv.db/621b0519.icv
726cef026b67ccf21db5c1c4ba0193ce8e0fdc8c1887d58bff75d4050dc05f3f  sce_pfs/icv.db/845d922c.icv
5772cbc5f4064d2126523e2c7f685d573531d1131fa0a637fb5410366dda0bd8  sce_pfs/icv.db/aad62c72.icv
5e6bef3e6a329da262ab83012a43b78c404e3b0e7947b22fc9eb265bbd10d2a1  sce_pfs/icv.db/cbab98f3.icv
4c75fa774a5af30d63f1172b7560da21634ad1e47936b8b23c714c49081f20af  sce_sys/keystone
c6a08e9d74acbd55a10813c8912aca767f4d8ad868a1c0a362435e198c088710  sce_sys/param.sfo
18e3b3ee1282c7b16f03ca48d643a5088f9b0d274fbaa1df9427c1b13b0f1c30  sce_sys/safemem.dat
ae0ceaa9b2bbc13608b4aee8ae6d5626f8a4d9f67ac89344e6ed3a6c2ae567d5  sce_sys/sdslot.dat
dadfa87bdcd9b15aa8401961d8344fe7cc3f7914a3ba79de657af7b824922fea  sce_sys/sealedkey
e50fde86807b48ba04a2081ff78eb9c8cb46fe223ab30138b368b2da52f6b357  system.dat
0134952ff0e1389df38160abbd08177ef37f93f8e4429d77746b2f54f909c373  VITA_PATH.TXT
```

### Tool revisions (SHA-256 of release zips)
| Tool | Source | Release artifact | SHA-256 |
|---|---|---|---|
| psvimgtools (create/extract/keyfind/md-decrypt + Cygwin DLLs) | yifanlu/psvimgtools | `psvimgtools-0.1-win64.zip` | 68d589f080afba4a86bd7eacf03f312a066429904985b91b7cf0c4ccb43fd0cf |
| psvpfsparser (psvpfstools) | motoharu-gosuto/psvpfstools | `release_win64_7.zip` | 441cac58455caae9187affc390595c6c74446351de82f426fb2afc040f4618bf |

Staged copies: `tools/psvimgtools/`, `tools/psvpfstools/` (binaries + required DLLs adjacent).

### Exact successful commands
```bat
:: Stage 1a — create CMA image from fixture input dir (subdir w/ VITA_PATH.TXT)
tools\psvimgtools\psvimg-create.exe -n savedata -K <TEST_KEY_64HEX> "<input_dir>" "<out_dir>"
::   -> <out_dir>\savedata.psvimg + savedata.psvmd

:: Stage 1b — extract CMA image with same key
tools\psvimgtools\psvimg-extract.exe -K <TEST_KEY_64HEX> "<out_dir>\savedata.psvimg" "<extract_dir>"
::   -> <extract_dir>\ux0_temp_game_PCSG90096_savedata_PCSG90096\... (15 files)

:: Stage 2 — PFS decrypt (sealedkey at <set>/sce_sys/sealedkey; F00D online)
tools\psvpfstools\psvpfsparser.exe -i "<extract_dir>\ux0_temp_game_PCSG90096_savedata_PCSG90096" -o "<pfs_out>" -f http://cma.henkaku.xyz
```

Key format: **exactly 64 hex chars** (32 bytes) — enforced by `parse_key()` in psvimgtools source.

### Test results (all PASS, exit code 0)
- `tests/test_cma_roundtrip.py`: creation / extraction / file count (15=15) / SHA-256 (15/15 identical) — deterministic across runs.
- `tests/test_pfs_decrypt.py`: seal verified ("matched retail hmac"), output produced, determinism (identical manifests across 2 runs).

### F00D behavior
GET to `http://cma.henkaku.xyz` with the sealed key as query parameter → JSON containing encrypted `key` + `drv_key`. Confirmed live in 2026. No cache file needed for online path; bundled psvpfsparser build has no `-c/--f00d_cache` flag (script probes `--help` and falls back to online).

### Expected decrypted param.sfo verification
Decrypted `sce_sys/param.sfo` is a valid SFO container whose fields include the expected Title ID **PCSG90096** (TITLE_ID / PARENT_DIRECTORY `/PCSG90096`). This is the stage-2 correctness oracle.

### Known binary quirks (do not "fix" without regression evidence)
- `psvpfsparser.exe` exits 0 even on failure → verify output files + stderr text, never exit code alone.
- Cygwin-built psvimgtools binaries require native Windows paths (MSYS `/tmp/...` is misread).

## Build order progress (goal revision 4)
1. Production Python backend — **DONE** (`app/core.py`, verified)
2. Backend regression vs PCSG90096 fixture — **DONE** (PASS, exit 0)
3–18. GUI, drag/drop, detection, title display, key UI, progress, errors, bundled tools, logo/ICO, PyInstaller one-file, EXE regression, clean-env test, docs, final audit — **ALL DONE**

## Packaged EXE (Phase 4) — build & regression evidence
- Build command (project root): `.venv/Scripts/pyinstaller.exe VitaSaveDecryptor.spec --noconfirm` (~40 s).
- Spec: `VitaSaveDecryptor.spec` — onefile, entry shim `vita_main.py`, `console=False` (windowed bootloader,
  no console window), `upx=False`, EXE icon = `app/resources/VitaSaveDecryptor.ico`; datas bundle
  `tools/` (psvimg-extract + psvpfsparser + native DLLs) and `resources/` (logo.png, .ico).
- Artifact: **`dist\VitaSaveDecryptor.exe` — ONE self-contained file, 53,322,505 bytes.**
  User installs nothing else (no Python / PySide6 / psvimgtools / psvpfstools / DLLs / scripts needed).
- Packaged regression (official PCSG90096 fixture), run against the FINAL rebuilt binary:
  - `--version` → `Vita Save Decryptor 1.0.0`
  - Full decrypt: all 6 stages → **DECRYPTION SUCCESSFUL**, 6 files in `decrypted_save/`
    (bundled psvimg-extract.exe + psvpfsparser.exe executed from `_MEIPASS`)
  - Decrypted `sce_sys/param.sfo` SHA-256 =
    `c6a08e9d74acbd55a10813c8912aca767f4d8ad868a1c0a362435e198c088710` — **byte-for-byte identical** to the
    known-good fixture; PCSG90096 present (offset 1308)
  - Source fixture `savedata.psvimg` SHA-256 unchanged:
    `7b73e422d772a8e93f32cb304b2d3eb8f6eb401d047f32111b913123be84912d` (backup never modified)
  - Output tree: `tests/work/packaged_exe_regression_v2/out/`
- GUI verification (packaged EXE): window client area exactly **920×780**; official logo in titlebar,
  header, and About dialog; full header subtitle visible (no clipping); all workflow sections render
  (backup card + auto-detect panel, CMA key row with Show toggle, output row, DECRYPT SAVE DATA button,
  6-stage progress checklist, success banner area, log). Screenshot captured from the running EXE.
- Gotcha recorded: window-size measurement tooling must be DPI-aware
  (`SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`); otherwise `GetWindowRect` returns virtualized
  coordinates (~1/1.5 scale) and looks like a sizing bug when it is not.

## Checkpoints
- Git tag `checkpoint/backend-verified` = verified backend + locked fixture (see git log).
