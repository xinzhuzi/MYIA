"""spike 图标生成:纯 stdlib 写一张 1024x1024 PNG(zlib/struct,无 PIL 依赖)。"""

from __future__ import annotations

import struct
import zlib

SIZE = 1024


def chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def main() -> None:
    rows: list[bytes] = []
    for y in range(SIZE):
        row = bytearray([0])  # filter type 0
        for x in range(SIZE):
            # 深蓝底 + 居中白色方块"芯片"图案(spike 占位图标)
            inside = 256 <= x < 768 and 256 <= y < 768
            pin = (x % 128 < 32 or y % 128 < 32) and 128 <= x < 896 and 128 <= y < 896
            if inside:
                row += b"\xf5\xf7\xfa" if pin else (b"\x16\x2b\x4d" if (x // 64 + y // 64) % 2 else b"\x1d\x3a\x66")
            else:
                row += b"\x0d\x1f\x3a"
        rows.append(bytes(row))
    raw = b"".join(rows)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open("app-icon.png", "wb") as fh:
        fh.write(png)
    print("app-icon.png written")


if __name__ == "__main__":
    main()
