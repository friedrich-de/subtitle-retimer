"""Small PGS streams, written segment by segment, for the tests of image subtitles."""

import hashlib
import json
import struct
import zlib

import numpy as np

from retimer.providers import Reading

WHITE = (235, 128, 128, 255)
BLACK = (16, 128, 128, 255)
PALETTE = {1: WHITE, 2: BLACK}


def encode_rle(bitmap: np.ndarray) -> bytes:
    out = bytearray()
    for row in bitmap:
        position = 0
        while position < len(row):
            color = int(row[position])
            length = 1
            while position + length < len(row) and row[position + length] == color:
                length += 1
            position += length
            if color and length < 3:
                out += bytes([color]) * length
            elif color == 0 and length < 64:
                out += bytes([0, length])
            elif color == 0:
                out += bytes([0, 0x40 | (length >> 8), length & 0xFF])
            elif length < 64:
                out += bytes([0, 0x80 | length, color])
            else:
                out += bytes([0, 0xC0 | (length >> 8), length & 0xFF, color])
        out += b"\x00\x00"
    return bytes(out)


def segment(time_ms: int, kind: int, payload: bytes = b"") -> bytes:
    return b"PG" + struct.pack(">IIBH", time_ms * 90, 0, kind, len(payload)) + payload


def block(width: int = 24, height: int = 6, color: int = 1) -> np.ndarray:
    """A rectangle with a transparent frame of one pixel around it."""
    bitmap = np.zeros((height + 2, width + 2), dtype=np.uint8)
    bitmap[1:-1, 1:-1] = color
    return bitmap


def display_set(
    time_ms: int,
    objects: list[tuple[int, int, int, np.ndarray | None]],
    *,
    palette: dict[int, tuple[int, int, int, int]] | None = PALETTE,
    state: int = 0x80,
    screen: tuple[int, int] = (1920, 1080),
    forced: bool = False,
    end: bool = True,
) -> bytes:
    """A display set that shows `objects`: (id, x, y, bitmap).

    A bitmap of None is an object that is not sent again.
    """
    out = bytearray()
    composition = struct.pack(">HHBHBBBB", screen[0], screen[1], 0x10, 0, state, 0, 0, len(objects))
    for object_id, x, y, _ in objects:
        composition += struct.pack(">HBBHH", object_id, 0, 0x40 if forced else 0, x, y)
    out += segment(time_ms, 0x16, composition)
    out += segment(time_ms, 0x17, b"\x00")
    if palette is not None:
        entries = b"".join(bytes([index, *entry]) for index, entry in palette.items())
        out += segment(time_ms, 0x14, bytes([0, 0]) + entries)
    for object_id, _, _, bitmap in objects:
        if bitmap is None:
            continue
        data = encode_rle(bitmap)
        header = struct.pack(">HBB", object_id, 0, 0xC0)
        header += (len(data) + 4).to_bytes(3, "big")
        header += struct.pack(">HH", bitmap.shape[1], bitmap.shape[0])
        out += segment(time_ms, 0x15, header + data)
    if end:
        out += segment(time_ms, 0x80)
    return bytes(out)


def clear_set(time_ms: int) -> bytes:
    return display_set(time_ms, [], palette=None, state=0)


def simple_stream(count: int = 3, *, every_ms: int = 4_000, shown_ms: int = 2_500) -> bytes:
    """`count` subtitles, each a block of its own width, shown one after the other."""
    out = b""
    for index in range(count):
        start = 10_000 + index * every_ms
        out += display_set(start, [(index, 600, 900, block(width=20 + 4 * index))])
        out += clear_set(start + shown_ms)
    return out


def png_size(png: bytes) -> tuple[int, int]:
    return struct.unpack(">II", png[16:24])


def png_pixels(png: bytes) -> np.ndarray:
    width, height = png_size(png)
    length = struct.unpack(">I", png[33:37])[0]
    raw = np.frombuffer(zlib.decompress(png[41 : 41 + length]), dtype=np.uint8)
    return raw.reshape(height, 1 + width * 3)[:, 1:].reshape(height, width, 3)


class FakeReader:
    """Stands in for the model that reads pictures: it knows them by their width.

    `texts` maps the width of a picture to what it "says". A width that is
    not listed reads as `default`.
    """

    def __init__(self, texts: dict[int, str] | None = None, default: str = "line") -> None:
        self.texts = texts or {}
        self.default = default
        self.requests: list[dict] = []
        self.spoil: list[str] = []
        self.cost_usd = 0.002

    def read_pictures(self, instructions, pictures, *, model, **kwargs) -> Reading:
        self.requests.append(
            {
                "instructions": instructions,
                "labels": [label for label, _ in pictures],
                "keys": [hashlib.sha256(png).hexdigest() for _, png in pictures],
                "model": model,
                **kwargs,
            }
        )
        if self.spoil:
            return Reading(text=self.spoil.pop(0), cost_usd=self.cost_usd)
        answer = {}
        for number, (_, png) in enumerate(pictures, 1):
            width = png_size(png)[0]
            answer[str(number)] = self.texts.get(width, f"{self.default} {width}")
        return Reading(
            text=json.dumps(answer, ensure_ascii=False),
            cost_usd=self.cost_usd,
            input_tokens=100 * len(pictures),
            output_tokens=10 * len(pictures),
        )

    def embed(self, *args, **kwargs):
        raise AssertionError("reading pictures needs no embeddings")
