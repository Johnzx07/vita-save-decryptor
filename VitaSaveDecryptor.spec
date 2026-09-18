# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build specification — Vita Save Decryptor (native).

Build from the PROJECT ROOT with the venv PyInstaller:

    .venv/Scripts/pyinstaller.exe VitaSaveDecryptor.spec --noconfirm

Output: dist/VitaSaveDecryptor.exe — ONE self-contained Windows executable.

Packaging contract
------------------
* Entry point is the top-level shim ``vita_main.py`` (NOT app/main.py):
  PyInstaller treats a script inside a package as top-level, which breaks
  the relative imports in app/ at frozen runtime. The shim keeps all logic
  in app.main while giving PyInstaller a proper parent package.
* onefile + windowed: no console window; everything (Python runtime,
  PySide6, app code, native tools, UI assets) is extracted to sys._MEIPASS
  at startup and resolved by the centralized resource manager
  (app/resources.py + app/core.tool_paths).
* Stage 2 PFS decryption is pure Python (app/pfs_native.py), so it needs no
  bundled parser exe or network access — only pycryptodome (AES/XTS) which
  PyInstaller bundles automatically. The F00D online service is NOT required.
* datas layout inside _MEIPASS:
      tools/psvimgtools/*   psvimg-extract.exe + Cygwin DLLs (stage 1 only)
      resources/logo.png    window / About dialog logo
      resources/VitaSaveDecryptor.ico  (also the EXE icon)
"""
import os

ROOT = os.path.abspath(SPECPATH)

a = Analysis(
    [os.path.join(ROOT, 'vita_main.py')],
    pathex=[ROOT],                      # so `app` resolves as a package
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'tools', 'psvimgtools'), 'tools/psvimgtools'),
        (os.path.join(ROOT, 'app', 'resources', 'logo.png'), 'resources'),
        (os.path.join(ROOT, 'app', 'resources', 'VitaSaveDecryptor.ico'), 'resources'),
    ],
    hiddenimports=[
        # pycryptodome modules used by app/pfs_native.py (stage 2). PyInstaller's
        # hook usually catches these; listed explicitly so the frozen exe never
        # misses a lazily-imported submodule.
        'Crypto',
        'Crypto.Cipher',
        'Crypto.Cipher.AES',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],               # not used; keeps the bundle lean
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,                            # onefile: binaries + datas inside EXE
    [],
    name='VitaSaveDecryptor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                          # no UPX (not installed; Cygwin DLLs are sensitive)
    console=False,                      # release build: NO console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, 'app', 'resources', 'VitaSaveDecryptor.ico'),
)
