#!/usr/bin/env python3
"""
Pure-Python zipalign that works at the BINARY level, not through zipfile.

The problem with Python's zipfile module: it controls local file header
offsets internally and any `extra` field we set can be overwritten when
the entry is committed. So we re-write the whole ZIP manually.

This implementation:
  1. Reads the source ZIP's central directory
  2. Re-orders/pads STORED entries so their data offsets are 4-byte aligned
  3. Writes a new ZIP with proper local headers, central directory, and EOCD
"""
import struct, sys, zlib, os
from pathlib import Path

LOCAL_FILE_HEADER_SIG = 0x04034b50
CENTRAL_DIR_SIG = 0x02014b50
EOCD_SIG = 0x06054b50


def read_central_directory(data: bytes):
    """Return list of (filename, compress_method, lhdr_off, crc32, comp_size, uncomp_size, extra, comment, external_attr, date_time)."""
    # Find EOCD
    eocd_off = data.rfind(b"PK\x05\x06")
    if eocd_off == -1:
        raise ValueError("No EOCD found")
    cd_off = struct.unpack("<I", data[eocd_off+16:eocd_off+20])[0]
    cd_size = struct.unpack("<I", data[eocd_off+12:eocd_off+16])[0]

    entries = []
    off = cd_off
    end = cd_off + cd_size
    while off < end:
        sig = struct.unpack("<I", data[off:off+4])[0]
        if sig != CENTRAL_DIR_SIG:
            break
        crc = struct.unpack("<I", data[off+16:off+20])[0]
        comp_size = struct.unpack("<I", data[off+20:off+24])[0]
        uncomp_size = struct.unpack("<I", data[off+24:off+28])[0]
        name_len = struct.unpack("<H", data[off+28:off+30])[0]
        extra_len = struct.unpack("<H", data[off+30:off+32])[0]
        comment_len = struct.unpack("<H", data[off+32:off+34])[0]
        compress_method = struct.unpack("<H", data[off+10:off+12])[0]
        mod_time = struct.unpack("<H", data[off+12:off+14])[0]
        mod_date = struct.unpack("<H", data[off+14:off+16])[0]
        lhdr_off = struct.unpack("<I", data[off+42:off+46])[0]
        int_attr = struct.unpack("<H", data[off+36:off+38])[0]
        ext_attr = struct.unpack("<I", data[off+38:off+42])[0]
        name = data[off+46:off+46+name_len]
        extra = data[off+46+name_len:off+46+name_len+extra_len]
        comment = data[off+46+name_len+extra_len:off+46+name_len+extra_len+comment_len]
        entries.append({
            "name": name, "method": compress_method,
            "crc": crc, "comp_size": comp_size, "uncomp_size": uncomp_size,
            "mod_time": mod_time, "mod_date": mod_date,
            "int_attr": int_attr, "ext_attr": ext_attr,
            "extra": extra, "comment": comment,
            "lhdr_off": lhdr_off,
        })
        off += 46 + name_len + extra_len + comment_len
    return entries, eocd_off


def read_local_header_data(data: bytes, lhdr_off: int):
    """Read the data bytes of an entry from the source ZIP."""
    name_len = struct.unpack("<H", data[lhdr_off+26:lhdr_off+28])[0]
    extra_len = struct.unpack("<H", data[lhdr_off+28:lhdr_off+30])[0]
    data_off = lhdr_off + 30 + name_len + extra_len
    comp_size = struct.unpack("<I", data[lhdr_off+18:lhdr_off+22])[0]
    return data[data_off:data_off + comp_size]


def zipalign(in_path: Path, out_path: Path, alignment: int = 4) -> None:
    """Re-write `in_path` as a new zip at `out_path` with STORED entries aligned."""
    data = in_path.read_bytes()
    entries, eocd_off = read_central_directory(data)

    out = bytearray()
    central_dir = bytearray()

    for entry in entries:
        comp_data = read_local_header_data(data, entry["lhdr_off"])
        name = entry["name"]
        method = entry["method"]

        # Compute extra padding for STORED entries so data is aligned
        # data_off = local_header_offset + 30 + name_len + extra_len
        local_header_offset = len(out)
        base_extra_len = 0  # we drop original extra fields; they're not needed
        if method == 0:  # STORED
            needed = (alignment - ((local_header_offset + 30 + len(name) + base_extra_len) % alignment)) % alignment
            extra = b"\x00" * needed
        else:  # DEFLATE — alignment not required
            extra = b""

        data_off = local_header_offset + 30 + len(name) + len(extra)

        # Local file header
        lhdr = struct.pack("<IHHHHHIIIHH",
            LOCAL_FILE_HEADER_SIG,
            20,  # version needed to extract (2.0)
            0,   # general purpose bit flag
            method,
            entry["mod_time"],
            entry["mod_date"],
            entry["crc"],
            len(comp_data) if method == 0 else entry["comp_size"],  # compressed size
            entry["uncomp_size"],  # uncompressed size
            len(name),
            len(extra),
        )
        out.extend(lhdr)
        out.extend(name)
        out.extend(extra)
        out.extend(comp_data)

        # Central directory entry
        cd_entry = struct.pack("<IHHHHHHIIIHHHHHII",
            CENTRAL_DIR_SIG,
            20,  # version made by
            20,  # version needed
            0,   # flag
            method,
            entry["mod_time"],
            entry["mod_date"],
            entry["crc"],
            entry["comp_size"],
            entry["uncomp_size"],
            len(name),
            len(entry["extra"]) if False else 0,  # central extra (drop)
            len(entry["comment"]),
            0,   # disk number
            entry["int_attr"],
            entry["ext_attr"],
            local_header_offset,
        )
        central_dir.extend(cd_entry)
        central_dir.extend(name)

    # EOCD
    cd_off = len(out)
    eocd = struct.pack("<IHHHHIIH",
        EOCD_SIG,
        0, 0,
        len(entries), len(entries),
        len(central_dir),
        cd_off,
        0,
    )
    out.extend(central_dir)
    out.extend(eocd)

    out_path.write_bytes(out)


if __name__ == "__main__":
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else in_path.with_suffix(".aligned.apk")
    zipalign(in_path, out_path)
    print(f"Aligned {in_path.name} → {out_path.name}")
