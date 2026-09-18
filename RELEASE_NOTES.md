# Vita Save Decryptor v0.2.0-native

Fully offline PS Vita CMA save backup decryption — no bundled parser exe, no online key service.

## What's new in this native release
- **Stage 2 is now pure Python** (`app/pfs_native.py`): local key derivation + XTS-AES + ICV-Merkle salt oracle. No `psvpfsparser.exe`, no F00D network call — works with zero connectivity.
- Stage 1 still uses [psvimgtools](https://github.com/yifanlu/psvimgtools) to unpack the CMA archive.
- Byte-for-byte identical output to the reference tool (verified against the h-encore PCSG90096 fixture).

## Requirements
- Windows 10/11 (x64)
- A PS Vita CMA save backup (`savedata.psvimg`) and its 64-hex CMA key

## Download
Grab `VitaSaveDecryptor.exe` below — a single self-contained file. Double-click to run; no install needed.

---
Made by [The New Game+](https://www.youtube.com/@TheNewGamePluss) · Support on [Ko-fi](https://ko-fi.com/thenewgameplus) · Join the [Discord](https://discord.gg/nwXq8wZzEA)
