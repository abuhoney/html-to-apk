#!/usr/bin/env python3
"""Generate launcher icons for the html_to_apk Android app."""
import struct, hashlib, zlib, os
from pathlib import Path

def generate_icon_png(seed: str, size: int) -> bytes:
    h = hashlib.md5(seed.encode()).digest()
    r, g, b = min(255, h[0] + 60), min(255, h[1] + 60), min(255, h[2] + 60)

    def _chunk(t: bytes, d: bytes) -> bytes:
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    raw = bytearray()
    cx = cy = size // 2
    radius = int(size * 0.36)
    for y in range(size):
        raw.append(0)
        for x in range(size):
            if ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 < radius:
                # white circle
                raw.extend([255, 255, 255])
            else:
                raw.extend([r, g, b])
    idat = _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend

base = Path("/home/z/my-project/html_to_apk/android/app/src/main/res")
densities = {
    "mipmap-mdpi":    48,
    "mipmap-hdpi":   72,
    "mipmap-xhdpi":  96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}
for folder, size in densities.items():
    d = base / folder
    d.mkdir(parents=True, exist_ok=True)
    icon = generate_icon_png("html_to_apk_main_app", size)
    (d / "ic_launcher.png").write_bytes(icon)
    print(f"  ✓ {folder}/ic_launcher.png ({size}x{size}, {len(icon)} bytes)")
print("Done.")
