# Vita Save Decryptor v0.3.0-native

Two real bugs, found and fixed the honest way: regression test written first,
watched it fail against the shipped code, then made it pass. If you hit either
message below in a log, this update is for you.

---

## Bug 1 — saves containing a file called `blocks.db` (or a few other names)
### Breaking score: 8/10

**What it did.** If a game's save folder happens to contain a file named after
one of PFS's own internal metadata files — `files.db`, `blocks.db`,
`free_blocks.db`, `direct_blocks.db`, `blocks_grid.db`, `hash.db`, `icv.db`,
`sse_hash.db`, `nptr.db` — the whole decrypt either died with

```
Could not resolve icv_salt for blocks.db
```

or, sneakier, wrote out a file with the right name and the *wrong bytes
inside*. The second one is the nasty kind: no error, and Vita3K just doesn't
see your progress.

**Why it happened.** To find each save file on disk, the decryptor checked the
set root, then `sce_sys/`, and when both missed, searched the entire set tree
for a matching name. `sce_pfs/` holds PFS's own bookkeeping database —
including a file literally named `blocks.db` — and it always won that search
for the wrong reason. The old fallback only skipped the `icv.db` folder.

**How it was fixed.** The whole `sce_pfs/` directory is off-limits when
looking for a save member now. Those are the filesystem's own records, not
part of your save. If a file listed in the index genuinely isn't on disk, you
get a clear per-file error instead of a misleading icv_salt abort.

**Proof.** A new regression test builds a save set containing a member
deliberately named `blocks.db` (encrypted, with a real ICV record), runs the
full pipeline, and byte-compares the output. Against the old code that test
fails with the error above; against v0.3.0 it passes.

## Bug 2 — truncated `files.db` crashed instead of telling you what to do
### Breaking score: 6/10

**What it did.** A backup whose `files.db` got cut short — an interrupted CMA
export, a partial copy — produced a raw Python stack trace ending in
`struct.error: unpack requires a buffer of 16 bytes`. Technically true,
completely useless to anyone trying to fix their backup.

**Why it happened.** The index parser only walked whole 0x400-byte pages and
unpacked records with no bounds checks. A short final page unpacked past the
end of the buffer. Two smaller issues lived next to it: a partial final page
could also *silently drop* the entries recorded on it, and the page count was
derived with integer division, so a partial page was never even looked at.

**How it was fixed.** Short final pages are parsed like any other page, with
bounds checked per record. When something genuinely doesn't fit you now get:

```
files.db is truncated: info record cut off at offset 0x698. Re-export the CMA backup.
```

**Proof.** New regression test truncates the index mid-record and asserts the
clean error — it raised the raw crash before the fix.

---

## New: support buttons
- YouTube and Ko-fi buttons in the app header, and badges in the About dialog
  and the README — they open [The New Game+](https://www.youtube.com/@TheNewGamePluss)
  and [ko-fi.com/thenewgameplus](https://ko-fi.com/thenewgameplus) in your
  browser. Pure vector SVG artwork, no bitmap scaling.
- If the artwork can't load for any reason you just get a plain text button —
  the decoration can never take the app down.
- Small tidy-ups: the output step is now numbered `3 · OUTPUT` like the other
  steps, and the README's Discord link wasn't going anywhere. Fixed.

## Testing
30 checks, all green, no network involved:
- `tests/test_pfs_native.py` — byte-exact decrypt of the vendored PCSG90096
  fixture against reference ground truth (9 checks).
- `tests/test_regressions.py` — R1–R7: one regression per bug fixed in this
  and the previous release, brand-button checks, plus a full pipeline
  end-to-end (pack a save with `psvimg-create`, decrypt with this app,
  byte-compare every file): 21 checks.

## Requirements
- Windows 10/11 (x64)
- A PS Vita CMA save backup (`savedata.psvimg`) and its 64-hex CMA key

## Download
`VitaSaveDecryptor.exe` below — one self-contained file, no install. The app
is unsigned, so SmartScreen may warn on a fresh download ("More info → Run
anyway"); the SHA-256 below lets you verify the bytes you got.

---
Made by [The New Game+](https://www.youtube.com/@TheNewGamePluss) · Support on [Ko-fi](https://ko-fi.com/thenewgameplus) · Join the [Discord](https://discord.gg/nwXq8wZzEA)

**SHA-256:** `63ccb38cdbc03def3a80169b545216762e11d80a641e16f7a609942626eb2e23`
