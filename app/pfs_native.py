"""Vita Save Decryptor — native PFS savedata decryption (pure Python).

Stage-2 replacement for ``psvpfsparser.exe``: decrypts a PSVita savedata PFS
set entirely in-process, with **no bundled native tool and no F00D network
service**. All key material is derived locally from the set's own
``sce_sys/sealedkey``.

Algorithm (reverse-engineered; behavioral reference = psvpfstools source,
NOT copied — see THIRD_PARTY_NOTICES.md):

  klicensee   = AES-128-CBC-decrypt(sealedkey.blob[0:16],
                                     key=pfsSKKey__EncKey, iv=zeros)
  base        = SHA1(klicensee)
  dec_key     = SHA1(base || SHA1([icv_salt, 1]_LE8))[0:16]
  tweak_enc   = SHA1(base || SHA1([icv_salt, 2]_LE8))[0:16]

  Per 0x8000-byte sector (sector_base=0 for standalone savedata files):
      seed    = AES-ECB(tweak_enc, u64_LE(block_size * sector_index))
      XTS-AES-128 decrypt with dec_key; IEEE-1619 tweak perturbation
      (multiply-by-2 in GF(2^128), reduction 0x87) continues across all
      sub-blocks of the sector.

Per-file ``icv_salt`` is the hex name of its ``sce_pfs/icv.db/<salt>.icv``
record; ``nSectors`` comes from that record's SCEICVDB header (offset 0x28).
A file whose type has bit 0x4000 set is stored unencrypted and copied
verbatim.

Verified byte-exact against the PCSG90096 regression fixture on all four
encrypted files (system.dat, sdslot.dat, safemem.dat, keystone); see
``tests/test_pfs_native.py``.
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import AES

# --------------------------------------------------------------------------- #
# Constants (public PSVita keys — psdevwiki "PFS Sealed Key Keys")
# --------------------------------------------------------------------------- #
#: pfsSKKey__EncKey — CBC key that seals the klicensee inside sealedkey.
PFS_SK_ENCKEY = bytes.fromhex("00298CDF4428E72C8785DAE0923C60BD")

SECTOR_SIZE = 0x8000          # fileSectorSize for savedata PFS sets
FILES_DB_PAGE = 0x400         # one files.db block per page
NENC_BIT = 0x4000             # type bit: unencrypted (copy verbatim)

SEALKEY_MAGIC = b"pfsSKKey"
ICVDB_MAGIC = b"SCEICVDB"
INULL_MAGIC = b"SCEINULL"


class PfsNativeError(Exception):
    """Fatal native-PFS failure with a user-facing message."""


# --------------------------------------------------------------------------- #
# Key derivation
# --------------------------------------------------------------------------- #
def extract_klicensee(sealedkey: bytes) -> bytes:
    """Recover the 16-byte klicensee from a ``pfsSKKey`` sealed key file.

    Layout: magic[8] @0x0, version u32 @0x08, iv[16] @0x10 (zeros), then the
    AES-128-CBC ciphertext blob starting at 0x20. The first block decrypts to
    the klicensee.
    """
    if len(sealedkey) < 0x30 or sealedkey[:8] != SEALKEY_MAGIC:
        raise PfsNativeError("sealedkey has an invalid pfsSKKey header")
    iv = sealedkey[0x10:0x20]
    blob = sealedkey[0x20:]
    if len(blob) < 16:
        raise PfsNativeError("sealedkey ciphertext is too short")
    return AES.new(PFS_SK_ENCKEY, AES.MODE_CBC, iv).decrypt(blob[:16])


def derive_keys(klicensee: bytes, icv_salt: int) -> tuple[bytes, bytes]:
    """Return ``(dec_key, tweak_enc_key)`` for one file's salt."""
    base = hashlib.sha1(klicensee).digest()

    def _k(ctr: int) -> bytes:
        saltin = struct.pack("<II", icv_salt, ctr)  # int[2] little-endian
        return hashlib.sha1(base + hashlib.sha1(saltin).digest()).digest()[:16]

    return _k(1), _k(2)


# --------------------------------------------------------------------------- #
# XTS-AES-128 (IEEE P1619 tweak perturbation, little-endian u32 words)
# --------------------------------------------------------------------------- #
def _gf_mult_x_words(tw: list[int]) -> list[int]:
    """Multiply a 128-bit value (4 little-endian u32 words) by x in GF(2^128).

    Standard IEEE P1619 / XTS perturbation: left-shift the full 128-bit value;
    if the top bit was set, XOR reduction constant 0x87 into word 0.
    """
    v = tw[0] | (tw[1] << 32) | (tw[2] << 64) | (tw[3] << 96)
    top = (v >> 127) & 1
    v = ((v << 1) & ((1 << 128) - 1))
    if top:
        v ^= 0x87
    return [
        v & 0xFFFFFFFF,
        (v >> 32) & 0xFFFFFFFF,
        (v >> 64) & 0xFFFFFFFF,
        (v >> 96) & 0xFFFFFFFF,
    ]


def _xts_decrypt_sector(plain: bytes, dec_key: bytes, tweak_enc_key: bytes,
                        sector_index: int) -> bytes:
    """Decrypt one 0x8000-byte XTS-AES sector (sector_base = 0)."""
    seed_val = SECTOR_SIZE * sector_index
    seed = struct.pack("<Q", seed_val) + b"\x00" * 8
    tweak = AES.new(tweak_enc_key, AES.MODE_ECB).encrypt(seed)

    dec_ecb = AES.new(dec_key, AES.MODE_ECB)
    out = bytearray(len(plain))
    tw = list(struct.unpack("<4I", tweak))
    for i in range(0, len(plain), 16):
        blk = plain[i:i + 16]
        t = struct.pack("<4I", *tw)
        xored = bytes(x ^ y for x, y in zip(blk, t))
        dec = dec_ecb.decrypt(xored)
        out[i:i + 16] = bytes(x ^ y for x, y in zip(dec, t))
        tw = _gf_mult_x_words(tw)
    return bytes(out)


def xts_decrypt_file(data: bytes, n_sectors: int, dec_key: bytes,
                     tweak_enc_key: bytes) -> bytes:
    """XTS-AES decrypt ``n_sectors`` full sectors of ``data``."""
    out = bytearray()
    for s in range(n_sectors):
        chunk = data[s * SECTOR_SIZE:(s + 1) * SECTOR_SIZE]
        if len(chunk) < SECTOR_SIZE:
            # Tail shorter than a sector: pad, decrypt, trim.
            padded = chunk + b"\x00" * (SECTOR_SIZE - len(chunk))
            dec = _xts_decrypt_sector(padded, dec_key, tweak_enc_key, s)
            out += dec[:len(chunk)]
        else:
            out += _xts_decrypt_sector(chunk, dec_key, tweak_enc_key, s)
    return bytes(out)


# --------------------------------------------------------------------------- #
# files.db / icv.db parsing
# --------------------------------------------------------------------------- #
@dataclass
class PfsFileEntry:
    name: str          # e.g. "SYSTEM.DAT" (upper-case in the db)
    ftype: int         # sce_ng_pfs_file_types value
    size: int          # logical file size in bytes
    salt: int | None   # icv_salt (hex of <salt>.icv), None if unencrypted/absent


def _parse_files_db(fdb: bytes) -> list[tuple[str, int, int]]:
    """Yield ``(name, ftype, size)`` for every file entry in files.db."""
    entries: list[tuple[str, int, int]] = []
    n_pages = len(fdb) // FILES_DB_PAGE
    for pg in range(n_pages):
        off = pg * FILES_DB_PAGE
        # Skip fully-zero pages (no block header).
        if fdb[off + 8:off + 12] == b"\x00\x00\x00\x00":
            continue
        for i in range(9):
            fo = off + 16 + i * 72
            idx = struct.unpack_from("<I", fdb, fo)[0]
            if idx == 0xFFFFFFFF:
                continue
            name = fdb[fo + 4:fo + 4 + 68].split(b"\x00")[0].decode("utf-8", "replace")
            info_off = off + 16 + 9 * 72 + i * 16
            fidx, ftype, _p0, size, _p1 = struct.unpack_from("<IHHII", fdb, info_off)
            if name:
                entries.append((name, ftype, size))
    return entries


def _parse_icv_db(icv_dir: Path) -> dict[int, tuple[int, bytes]]:
    """Map ``salt -> (nSectors, stored_icv_root)`` from every SCEICVDB record.

    The 20-byte ICV root is at offset 0x2C in each record; it is the Merkle
    tree root over HMAC-SHA1 hashes of the **ciphertext** sectors and serves
    as a deterministic oracle for salt resolution (see ``resolve_salt_by_icv``).
    """
    meta: dict[int, tuple[int, bytes]] = {}
    if not icv_dir.is_dir():
        return meta
    for p in sorted(icv_dir.iterdir()):
        if not p.name.endswith(".icv"):
            continue
        data = p.read_bytes()
        if data[:8] == INULL_MAGIC:
            continue  # placeholder (file absent from the set)
        if data[:8] != ICVDB_MAGIC:
            continue
        nsec = struct.unpack_from("<I", data, 0x28)[0]
        stored_icv = data[0x2C:0x40]
        salt = int(p.name.split(".")[0], 16)
        meta[salt] = (nsec, stored_icv)
    return meta


# --------------------------------------------------------------------------- #
# ICV Merkle tree (deterministic salt resolution + integrity check)
# --------------------------------------------------------------------------- #
def icv_secret(klicensee: bytes, icv_salt: int) -> bytes:
    """Derive the 20-byte HMAC-SHA1 secret for one file's ICV.

    ``secret = SHA1( SHA1(klicensee) || SHA1([0xA, icv_salt]_LE8) )``
    (psvpfstools ``generate_secret``, savedata variant).
    """
    base0 = hashlib.sha1(klicensee).digest()
    saltin = struct.pack("<II", 0xA, icv_salt)
    base1 = hashlib.sha1(saltin).digest()
    return hashlib.sha1(base0 + base1).digest()


def _merkle_leaf_indices(n_leaves: int) -> tuple[list[int], int]:
    """Sector index assigned to each leaf (BFS left-right order), per the
    psvpfstools ``tree_indexer``.

    Heap layout is 1-based: root at position 1, children of i at 2i / 2i+1;
    leaves occupy positions n_nodes-n_leaves+1 .. n_nodes where
    n_nodes = 2*n_leaves - 1 (full binary tree). The left child inherits its
    parent's sector index; the right child takes the next sequence number.
    """
    n_nodes = 2 * n_leaves - 1
    idx_of = [0] * (n_nodes + 1)   # 1-based heap positions
    nxt = 1
    q = deque([1])
    while q:
        i = q.popleft()
        if 2 * i > n_nodes:
            continue               # leaf
        idx_of[2 * i] = idx_of[i]  # left inherits parent index
        idx_of[2 * i + 1] = nxt    # right gets next sequence number
        nxt += 1
        q.append(2 * i)
        q.append(2 * i + 1)
    leaves = list(range(n_nodes - n_leaves + 1, n_nodes + 1))
    return [idx_of[l] for l in leaves], n_nodes


def compute_icv_root(sector_hashes: list[bytes], secret: bytes) -> bytes:
    """Combine per-sector HMAC-SHA1 hashes into the ICV Merkle root.

    Internal nodes are ``HMAC-SHA1(secret, left || right)`` over the full
    binary tree (n_nodes = 2*n - 1). A single sector is its own hash.
    """
    n = len(sector_hashes)
    if n == 0:
        raise PfsNativeError("no sectors to hash")
    if n == 1:
        return sector_hashes[0]
    leaf_idx, n_nodes = _merkle_leaf_indices(n)
    base = n_nodes - n + 1          # first leaf position (1-based heap)
    data = [None] * (n_nodes + 1)
    for pos, sec in zip(range(base, n_nodes + 1), leaf_idx):
        data[pos] = sector_hashes[sec]
    for i in range(n_nodes // 2, 0, -1):
        data[i] = hmac.new(secret, data[2 * i] + data[2 * i + 1],
                           hashlib.sha1).digest()
    return data[1]


def compute_file_icv(ciphertext: bytes, klicensee: bytes, icv_salt: int) -> bytes:
    """Compute the ICV root for a file's **ciphertext** sectors."""
    secret = icv_secret(klicensee, icv_salt)
    nsec_full = len(ciphertext) // SECTOR_SIZE
    tail = len(ciphertext) % SECTOR_SIZE
    chunks = [ciphertext[i * SECTOR_SIZE:(i + 1) * SECTOR_SIZE]
              for i in range(nsec_full)]
    if tail:
        chunks.append(ciphertext[nsec_full * SECTOR_SIZE:])
    sh = [hmac.new(secret, c, hashlib.sha1).digest() for c in chunks]
    return compute_icv_root(sh, secret)


def resolve_salt_by_icv(data: bytes, klicensee: bytes,
                        icv_meta: dict[int, tuple[int, bytes]]) -> int | None:
    """Deterministically find the salt whose stored ICV matches this file.

    For each candidate salt, compute the Merkle root over HMAC-SHA1 hashes of
    the ciphertext sectors and compare to the 20-byte value stored in that
    salt's ``.icv`` record. Exactly one salt matches (verified on fixture).
    """
    for salt in sorted(icv_meta):
        _, stored_icv = icv_meta[salt]
        if compute_file_icv(data, klicensee, salt) == stored_icv:
            return salt
    return None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def decrypt_pfs_set(set_dir: Path | str, out_dir: Path | str) -> list[Path]:
    """Decrypt a savedata PFS set in pure Python.

    ``set_dir`` must contain ``sce_sys/sealedkey``, ``system.dat`` and the
    ``sce_pfs/`` metadata (files.db + icv.db). Decrypted/copied files are
    written under ``out_dir`` preserving their logical paths. Returns the list
    of output file paths.
    """
    set_dir = Path(set_dir)
    out_dir = Path(out_dir)

    sealedkey_path = set_dir / "sce_sys" / "sealedkey"
    if not sealedkey_path.is_file():
        raise PfsNativeError(f"sealed key not found at {sealedkey_path}")
    klicensee = extract_klicensee(sealedkey_path.read_bytes())

    fdb_path = set_dir / "sce_pfs" / "files.db"
    if not fdb_path.is_file():
        raise PfsNativeError(f"files.db not found at {fdb_path}")
    entries = _parse_files_db(fdb_path.read_bytes())

    icv_meta = _parse_icv_db(set_dir / "sce_pfs" / "icv.db")

    out_files: list[Path] = []
    for name, ftype, size in entries:
        # Directory entry (bit 0x8000) — no data to decrypt.
        if ftype & 0x8000 or size == 0:
            continue

        src = _find_source(set_dir, name)
        if src is None:
            continue

        # Mirror the on-disk location (e.g. sce_sys/keystone stays under sce_sys).
        dest_rel = src.relative_to(set_dir)
        if ftype & NENC_BIT:
            # Unencrypted system file — copy verbatim.
            dest = out_dir / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            out_files.append(dest)
            continue

        data = src.read_bytes()
        # Deterministic salt resolution via ICV Merkle match (no heuristics).
        salt = resolve_salt_by_icv(data, klicensee, icv_meta)
        if salt is None:
            raise PfsNativeError(f"Could not resolve icv_salt for {name}")
        nsec, _stored = icv_meta[salt]

        dec_key, twk_key = derive_keys(klicensee, salt)
        dec = xts_decrypt_file(data[:nsec * SECTOR_SIZE], nsec, dec_key, twk_key)

        dest = out_dir / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write the full decrypted sector data (sector-padded), matching the
        # reference tool's output — do NOT trim to logical size.
        dest.write_bytes(dec[:nsec * SECTOR_SIZE])
        out_files.append(dest)

    return sorted(out_files)


def _find_source(set_dir: Path, name: str) -> Path | None:
    """Find the on-disk file for a files.db entry (case-insensitive)."""
    lname = name.lower()
    # Common savedata layout: system.dat at set root; sce_sys/* under it.
    candidates = [set_dir / lname, set_dir / "sce_sys" / lname]
    for c in candidates:
        if c.is_file():
            return c
    # Fallback: search the whole set (bounded).
    for p in set_dir.rglob(lname):
        if p.is_file() and p.parent.name != "icv.db":
            return p
    return None
