# Vita Save Decryptor v0.2.1-native

Bug-fix release on top of v0.2.0-native. Same fully-offline pipeline (no bundled parser
exe, no online key service) — three real bugs fixed, all covered by new automated
regression tests.

## Fixed in this release
- **Empty save files are no longer silently dropped.** A file recorded in the save's
  `files.db` with size 0 (some games keep empty marker/placeholder files) vanished from
  the decrypted output. Every member of the save set now appears in the output folder.
- **CLI mode accepts `--decrypt=PATH`.** Programs and Windows shortcuts that pass the
  argument in `=` form (`--decrypt=C:\backups\savedata.psvimg`) previously launched the
  GUI instead of decrypting. Both `--decrypt PATH` and `--decrypt=PATH` now run headless,
  together with `--key <64-hex>` and optional `--out <dir>`; `--version` prints the version.
- **The GUI no longer locks up after a failed run.** After a decryption error, the key
  field (and the Show-key checkbox) stayed greyed out — the only way to retry with a
  corrected key was to restart the app. All inputs now re-enable on every outcome
  (success or failure).

## Testing
Two standalone suites, no pytest, no network, exit code = number of failures:
- `tests/test_pfs_native.py` — byte-exact decrypt of the vendored PCSG90096 fixture
  against reference ground truth (9 checks).
- `tests/test_regressions.py` — one regression test per bug fixed above plus a full
  pipeline end-to-end check (pack a save with `psvimg-create`, decrypt with this app,
  byte-compare): 11 checks, all pass.

## Requirements
- Windows 10/11 (x64)
- A PS Vita CMA save backup (`savedata.psvimg`) and its 64-hex CMA key

## Download
Grab `VitaSaveDecryptor.exe` below — a single self-contained file. Double-click to run;
no install needed. SHA-256 of the shipped file is listed on the release page.

---
Made by [The New Game+](https://www.youtube.com/@TheNewGamePluss) · Support on [Ko-fi](https://ko-fi.com/thenewgameplus) · Join the [Discord](https://discord.gg/nwXq8wZzEA)
