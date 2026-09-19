#!/usr/bin/env python3
"""Verify zipalign of an APK by inspecting local file header offsets."""
import struct, sys, zipfile
from pathlib import Path

apk = Path(sys.argv[1] if len(sys.argv) > 1 else
           "/home/z/my-project/html_to_apk/download/html_to_apk.apk")

print(f"Checking zipalign of: {apk.name}\n")

with open(apk, "rb") as f:
    data = f.read()

# Walk the central directory
eocd_off = data.rfind(b"PK\x05\x06")
if eocd_off == -1:
    print("✗ No EOCD found"); sys.exit(1)
cd_off = struct.unpack("<I", data[eocd_off+16:eocd_off+20])[0]
cd_size = struct.unpack("<I", data[eocd_off+12:eocd_off+16])[0]
print(f"Central directory at offset {cd_off}, size {cd_size}\n")

off = cd_off
entries = []
while off < cd_off + cd_size:
    sig = struct.unpack("<I", data[off:off+4])[0]
    if sig != 0x02014b50: break
    name_len = struct.unpack("<H", data[off+28:off+30])[0]
    extra_len = struct.unpack("<H", data[off+30:off+32])[0]
    comment_len = struct.unpack("<H", data[off+32:off+34])[0]
    local_off = struct.unpack("<I", data[off+42:off+46])[0]
    compress_method = struct.unpack("<H", data[off+10:off+12])[0]
    name = data[off+46:off+46+name_len].decode("utf-8", errors="replace")
    # Compute the actual data offset
    # Local file header: 30 bytes + name + extra
    local_name_len = struct.unpack("<H", data[local_off+26:local_off+28])[0]
    local_extra_len = struct.unpack("<H", data[local_off+28:local_off+30])[0]
    data_off = local_off + 30 + local_name_len + local_extra_len
    entries.append((name, compress_method, local_off, data_off))
    off += 46 + name_len + extra_len + comment_len

print(f"{'File':<40} {'Method':<8} {'DataOff':<10} {'Aligned':<8}")
print("-" * 70)
all_aligned = True
for name, method, lhdr_off, data_off in entries:
    method_name = "STORED" if method == 0 else "DEFLATE" if method == 8 else f"0x{method:x}"
    needs_align = (method == 0)  # STORED entries need alignment
    aligned = (data_off % 4 == 0) if needs_align else True
    mark = "✓" if aligned else "✗"
    if not aligned: all_aligned = False
    print(f"{name[:40]:<40} {method_name:<8} {data_off:<10} {mark}")
print("-" * 70)
print(f"\nResult: {'✓ ALL ALIGNED' if all_aligned else '✗ MISALIGNED — Android 11+ will reject'}")
