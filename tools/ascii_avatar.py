#!/usr/bin/env python3
"""Render an 8-bit RGB/RGBA PNG as deterministic, terminal-sized ASCII.

No third-party libraries, external commands, network, or image conversion are used.
The checked-in portrait was fetched from
https://avatars.githubusercontent.com/u/18630029?v=4 with curl after Python's
local HTTPS certificate verification failed. GitHub returned JPEG bytes; macOS
`sips -s format png img/avatar.png --out img/avatar.png` converted them ONCE
when preparing the asset. The checked-in input is a real, non-interlaced PNG.

Reproduce tools/avatar.txt from the repository root:
    python3 tools/ascii_avatar.py --background portrait-wall --output tools/avatar.txt

The actual avatar is a photograph of a smiling man with glasses, a blue suit,
a dark brown wall, and a white/orange badge. The optional portrait-wall mask keeps
blue clothing and well-lit pixels and replaces the dark wall with white before
resampling. It is a color heuristic, not person segmentation: it may remove dark
hair/details and retain badge details. NEVER enable it blindly for other images.
Default background handling preserves all pixels, including non-photo artwork.

Examples:
    python3 tools/ascii_avatar.py                    # unmasked art on stdout
    python3 tools/ascii_avatar.py other.png --width 40 --height 20
    python3 tools/ascii_avatar.py --background portrait-wall --invert

Use --help for options. Indexed/grayscale, 16-bit, and interlaced PNGs must first
be exported as non-interlaced 8-bit RGB/RGBA PNGs by an image editor. There is no
runtime dependency on macOS or sips. Gamma and the darkest-to-lightest ramp are
applied after area-averaged resampling; white/transparent pixels become spaces.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import struct
import sys
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_CHARS = "@%#*+=-:. "
DEFAULT_INPUT = Path(__file__).resolve().parents[1] / "img" / "avatar.png"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_DIMENSION = 8192


class AvatarError(ValueError):
    """An actionable input/format error safe to display without a traceback."""


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    dl, da, du = (abs(estimate - value) for value in (left, above, upper_left))
    if dl <= da and dl <= du:
        return left
    return above if da <= du else upper_left


def decode_png(path: Path) -> tuple[int, int, list[tuple[int, int, int]]]:
    """Validate PNG chunks/CRCs and undo filters 0–4; composite alpha on white."""
    with path.open("rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise AvatarError("PNG exceeds the 64 MiB input limit")
    if not data.startswith(PNG_SIGNATURE):
        raise AvatarError("input is not a PNG; export it as 8-bit RGB/RGBA PNG first")

    pos = len(PNG_SIGNATURE)
    width = height = channels = 0
    compressed = bytearray()
    saw_header = saw_data = ended_data = saw_end = False
    while pos < len(data):
        if len(data) - pos < 12:
            raise AvatarError("truncated PNG chunk header")
        length = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4:pos + 8]
        if length > len(data) - pos - 12:
            raise AvatarError("truncated PNG chunk payload")
        payload = data[pos + 8:pos + 8 + length]
        expected_crc = struct.unpack_from(">I", data, pos + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise AvatarError(f"PNG {kind!r} chunk has a CRC mismatch")
        pos += length + 12
        if not saw_header and kind != b"IHDR":
            raise AvatarError("PNG must begin with IHDR")
        if kind == b"IHDR":
            if saw_header or length != 13:
                raise AvatarError("PNG contains a duplicate or malformed IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION):
                raise AvatarError("PNG dimensions must be between 1 and 8192 pixels")
            if width * height > MAX_PIXELS:
                raise AvatarError("PNG exceeds the 16 million pixel limit")
            if depth != 8 or color not in (2, 6):
                raise AvatarError("only 8-bit RGB/RGBA PNGs are supported; re-export the image")
            if compression != 0 or filtering != 0 or interlace != 0:
                raise AvatarError("only standard, non-interlaced PNGs are supported")
            channels = 3 if color == 2 else 4
            saw_header = True
        elif kind == b"IDAT":
            if ended_data:
                raise AvatarError("PNG IDAT chunks must be consecutive")
            compressed.extend(payload)
            saw_data = True
        elif kind == b"IEND":
            if length != 0 or not saw_data:
                raise AvatarError("PNG has malformed IEND or missing image data")
            if pos != len(data):
                raise AvatarError("unexpected trailing bytes after PNG IEND")
            saw_end = True
            break
        else:
            if saw_data:
                ended_data = True
            if kind == b"tRNS":
                raise AvatarError("RGB color-key transparency is unsupported; export as RGBA")
            if kind == b"PLTE":
                if saw_data or not length or length % 3 or length > 768:
                    raise AvatarError("PNG contains a malformed palette")
            elif not kind[0] & 0x20:
                raise AvatarError(f"unsupported critical PNG chunk {kind!r}")
    if not saw_end:
        raise AvatarError("PNG is missing IEND")

    stride = width * channels
    expected_size = (stride + 1) * height
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(bytes(compressed), expected_size + 1)
    except zlib.error as exc:
        raise AvatarError(f"invalid PNG compressed data: {exc}") from exc
    if len(raw) != expected_size or not decoder.eof or decoder.unused_data:
        raise AvatarError("PNG decompressed size/stream does not match its dimensions")

    pixels: list[tuple[int, int, int]] = []
    previous = bytearray(stride)
    for y in range(height):
        offset = y * (stride + 1)
        filter_type = raw[offset]
        row = bytearray(raw[offset + 1:offset + 1 + stride])
        if filter_type > 4:
            raise AvatarError(f"unsupported PNG scanline filter {filter_type}")
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
            above = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                predictor = 0
            row[i] = (row[i] + predictor) & 255
        for i in range(0, stride, channels):
            r, g, b = row[i:i + 3]
            if channels == 4:
                alpha = row[i + 3]
                r, g, b = ((value * alpha + 255 * (255 - alpha) + 127) // 255
                           for value in (r, g, b))
            pixels.append((r, g, b))
        previous = row
    return width, height, pixels


def render_ascii(
    width: int,
    height: int,
    pixels: list[tuple[int, int, int]],
    columns: int = 45,
    rows: int = 23,
    gamma: float = 0.45,
    chars: str = DEFAULT_CHARS,
    background: str = "keep",
    invert: bool = False,
) -> str:
    """Fit to the requested box (1:2 character cells), without stretching/cropping."""
    if not (1 <= columns <= 45 and 1 <= rows <= 23):
        raise AvatarError("output bounds must be 1–45 columns and 1–23 rows")
    if not math.isfinite(gamma) or gamma <= 0:
        raise AvatarError("gamma must be a finite positive number")
    if len(chars) < 2 or not chars.isascii() or any(ord(c) < 32 or ord(c) > 126 for c in chars):
        raise AvatarError("character ramp must contain at least two printable ASCII characters")
    if background not in ("keep", "blue-lit", "portrait-wall"):
        raise AvatarError("background must be keep, blue-lit, or portrait-wall")
    if width < 1 or height < 1 or len(pixels) != width * height:
        raise AvatarError("pixel count does not match positive image dimensions")

    # The requested SVG uses 7.9px character width and 15px line height.
    aspect = 15 / 7.9
    scale = min(columns / width, rows * aspect / height)
    out_width = min(columns, max(1, int(width * scale + 0.5)))
    out_height = min(rows, max(1, int(height * scale / aspect + 0.5)))
    luminances = []
    for r, g, b in pixels:
        luminance = (299 * r + 587 * g + 114 * b) / 255000
        if background == "blue-lit":
            # Reference algorithm: remove bright blue sky and overexposed white.
            sky = (b - r) > 12 and luminance * 255 > 90
            bright = luminance * 255 > 235
            if sky or bright:
                luminance = 1.0
        elif background == "portrait-wall":
            # This specific portrait has a brown wall, NOT a blue sky.
            blue = b >= 35 and b > r * 1.15 and b > g * 1.02
            lit = luminance >= 0.36
            if not (blue or lit):
                luminance = 1.0
        luminances.append(luminance)

    lines = []
    for y in range(out_height):
        top, bottom = y * height / out_height, (y + 1) * height / out_height
        line = []
        for x in range(out_width):
            left, right = x * width / out_width, (x + 1) * width / out_width
            weighted = total = 0.0
            for sy in range(int(top), math.ceil(bottom)):
                wy = min(bottom, sy + 1) - max(top, sy)
                for sx in range(int(left), math.ceil(right)):
                    weight = wy * (min(right, sx + 1) - max(left, sx))
                    weighted += luminances[sy * width + sx] * weight
                    total += weight
            light = min(1.0, max(0.0, weighted / total)) ** gamma
            if invert:
                light = 1.0 - light
            index = min(len(chars) - 1, int(light * (len(chars) - 1) + 0.5))
            line.append(chars[index])
        lines.append("".join(line).rstrip())
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT,
                        help="8-bit RGB/RGBA PNG (default: repo img/avatar.png)")
    parser.add_argument("-o", "--output", type=Path, help="write ASCII to this path instead of stdout")
    parser.add_argument("--width", type=int, default=45, help="maximum columns, 1–45 (default: 45)")
    parser.add_argument("--height", type=int, default=23, help="maximum rows, 1–23 (default: 23)")
    parser.add_argument("--gamma", type=float, default=0.45, help="positive luminance exponent (default: .45)")
    parser.add_argument("--chars", default=DEFAULT_CHARS, help="dark-to-light ASCII ramp (default: '@%%#*+=-:. ')")
    parser.add_argument("--background", choices=("keep", "blue-lit", "portrait-wall"), default="keep",
                        help="keep pixels; remove bright blue sky per reference; or mask this portrait's brown wall")
    parser.add_argument("--invert", action="store_true", help="reverse brightness for a light terminal theme")
    args = parser.parse_args(argv)
    try:
        if args.output and args.input.resolve() == args.output.resolve():
            raise AvatarError("input and output paths must differ; refusing to overwrite the PNG")
        width, height, pixels = decode_png(args.input)
        art = render_ascii(width, height, pixels, args.width, args.height, args.gamma,
                           args.chars, args.background, args.invert)
        if args.output:
            with args.output.open("w", encoding="ascii", newline="\n") as destination:
                destination.write(art)
        else:
            sys.stdout.write(art)
    except (AvatarError, OSError, UnicodeError) as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
