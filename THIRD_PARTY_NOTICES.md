# Third-Party Notices

Vita Save Decryptor (native) decrypts PS Vita CMA save backups in two stages:

* **Stage 1** — unpack the CMA archive (`savedata.psvimg`) using a bundled copy of
  [psvimgtools](https://github.com/yifanlu/psvimgtools).
* **Stage 2** — decrypt the PFS savedata set in **pure Python**
  ([`app/pfs_native.py`](app/pfs_native.py)). All key material is derived locally
  from the set's own `sce_sys/sealedkey`, so stage 2 makes **no network calls** and
  needs no online key service.

This file documents every third-party component or source used in building it, its
provenance, license status, and redistribution basis. Full license texts live in
[`licenses/`](licenses/).

| Component | Upstream | License | How used here | Redistribution status |
|---|---|---|---|---|
| psvimgtools (stage 1) | [yifanlu/psvimgtools](https://github.com/yifanlu/psvimgtools) — Yifan Lu | MIT (Copyright 2017 Yifan Lu) | Bundled `psvimg-extract.exe` + Cygwin DLLs unpack the CMA archive | ✅ Permitted; full text in [`licenses/psvimgtools-MIT.txt`](licenses/psvimgtools-MIT.txt) |
| PySide6 (GUI toolkit) | [qtproject/pyside-pyside-setup](https://github.com/qtproject/pyside-pyside-setup) — The Qt Company Ltd. & contributors | LGPL v3.0 or Qt Commercial | GUI framework; dynamically linked, unmodified | ✅ Permitted under LGPLv3; note in [`licenses/pyside6-LGPL-note.md`](licenses/pyside6-LGPL-note.md) |
| pycryptodome (runtime dependency) | [Legrandin/pycryptodome](https://github.com/Legrandin/pycryptodome) — Helder Eijs | BSD-3-Clause | AES / SHA1 primitives via `Crypto.Cipher` and `hashlib` | ✅ Permitted; declared in `requirements.txt` |
| PCSG90096 regression fixture + ground truth | [TheOfficialFloW/h-encore](https://github.com/TheOfficialFloW/h-encore) — TheFloW | MIT (Copyright 2018 TheFloW) | Public development/regression test data only | ✅ Permitted; full text in [`licenses/h-encore-MIT.txt`](licenses/h-encore-MIT.txt). **TEST DATA — not user credentials.** |
| psvpfstools source (behavioral reference only) | [motoharu-gosuto/psvpfstools](https://github.com/motoharu-gosuto/psvpfstools) — motoharu-gosuto | ⚠️ No license file published upstream | **Read-only reference** for the PFS / XTS-AES / ICV-Merkle algorithm. No source is copied, vendored, or compiled into this project. | ✅ Not redistributed (nothing taken); see note below |

## psvimgtools (Yifan Lu) — stage 1

`tools/psvimgtools/` contains `psvimg-extract.exe`, `psvimg-create.exe`, and the
required Cygwin runtime DLLs, taken unmodified from the official release archive
`psvimgtools-0.1-win64.zip` of <https://github.com/yifanlu/psvimgtools> (MIT,
Copyright 2017 Yifan Lu; full text in [`licenses/psvimgtools-MIT.txt`](licenses/psvimgtools-MIT.txt)).
It is used only to unpack the CMA `.psvimg` archive into a working directory.

## PySide6 (The Qt Company Ltd.) — GUI

This application uses **PySide6** (the official Qt for Python bindings), copyright
The Qt Company Ltd. and contributors, under the GNU Lesser General Public License
v3.0 (**LGPLv3**) or the Qt Commercial License. The app dynamically links against
unmodified PySide6/Qt runtime libraries and does not modify their source; end users
retain the right to replace or relink the Qt libraries with a different version of
their choice. See [`licenses/pyside6-LGPL-note.md`](licenses/pyside6-LGPL-note.md)
and <https://www.qt.io/licensing> for details.

## pycryptodome (Helder Eijs) — stage 2 runtime dependency

The only Python runtime dependency. Provides AES-128 (ECB/CBC) and SHA1/HMAC-SHA1
primitives used by the XTS-AES engine and ICV Merkle-tree verification. BSD-3
licensed; declared in `requirements.txt` (`pycryptodome>=3.20`).

## h-encore regression fixture (TheFloW)

`tests/fixtures/hencore_savedata/` contains the PCSG90096 ("H-ENCORE") savedata set
from the official **h-encore v2.0** release archive of
<https://github.com/TheOfficialFloW/h-encore> (MIT, Copyright 2018 TheFloW; full text
in [`licenses/h-encore-MIT.txt`](licenses/h-encore-MIT.txt)).

- Purpose: public development/regression fixture for the decryption pipeline.
- Expected Title ID: **PCSG90096**.
- The set's `sce_sys/sealedkey` is key material that h-encore ships with this savedata
  specifically so the PFS can be decrypted; it is **not** an account credential and
  contains no personal data.
- All test keys used against this fixture are synthetic values generated for testing:
  **TEST DATA — NOT USER CREDENTIALS.**

`tests/fixtures/ground_truth/` holds the reference output produced by the upstream
`psvpfsparser.exe` on that same fixture; it is used only as a byte-exact comparison
target in `tests/test_pfs_native.py`.

## psvpfstools (motoharu-gosuto) — behavioral reference, NOT redistributed

The PFS container layout, XTS-AES-128 tweak perturbation, SHA1-based key derivation,
and ICV Merkle-tree construction were reverse-engineered by reading the C++ source of
<https://github.com/motoharu-gosuto/psvpfstools>. That project **publishes no license
file**, so to stay safely within its (absent) terms this project:

- copies **no** psvpfstools source code, headers, or binaries;
- ships **no** `psvpfsparser.exe` / `libcurl.dll`;
- re-implements the observed behavior independently in Python.

The reference is cited here purely for attribution and provenance. A Vita3K fork of
psvpfstools also exists (<https://github.com/Vita3K/psvpfstools>) and likewise
publishes no license file; neither was redistributed.

## Vita3K project and contributors

This work builds on PS Vita preservation research from the broader community, including
the [Vita3K](https://github.com/Vita3K/vita3k) emulator project and its PFS tooling
lineage. No Vita3K source code is compiled into this application; the credit reflects
research, compatibility work, and community guidance.
