"""Blu-ray image subtitles (PGS, the content of a .sup file): the pictures and their times.

A PGS stream holds no text. Every subtitle is a picture with a palette, and a
display set says when it appears and when the screen is cleared again. This
module reads the stream into events, each with the pictures it shows, so that
a reader that can see (`retimer.ocr`) can turn them into text.

Everything here runs on a file a stranger sent: sizes are checked before
anything is allocated, and a stream that breaks the format is refused.
"""

from __future__ import annotations

import hashlib
import re
import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np

MAGIC = b"PG"
_HEADER = 13
_PALETTE, _OBJECT, _COMPOSITION, _WINDOW, _END = 0x14, 0x15, 0x16, 0x17, 0x80
_EPOCH_START = 0x80
_FORCED = 0x40
_CROPPED = 0x80
_FIRST_IN_SEQUENCE = 0x80

# Blu-ray video is at most 1920x1080. A little more is taken, for streams
# made by tools, and nothing near what a 16-bit size field could claim.
MAX_SIDE = 4096
MAX_EVENTS = 20_000
# Pixels of one object. A full frame of 4096x2304 fits.
_MAX_OBJECT_PIXELS = 4096 * 2304
# What the pictures are laid on. Subtitles are light text with a dark outline,
# or the reverse, and both read well on a middle grey.
BACKGROUND = (96, 96, 96)
_PADDING = 8
# Pixels more transparent than this do not count as part of a picture.
_VISIBLE_ALPHA = 24

# One step of the run-length code: a run of single pixels, or a zero byte
# followed by what it stands for.
_TOKEN = re.compile(rb"[^\x00]+|\x00(?:[\x00-\x3f]|[\x40-\xbf][\s\S]|[\xc0-\xff][\s\S]{2})")


class PgsError(ValueError):
    """The data is not a PGS stream this service can read."""


@dataclass(frozen=True)
class Picture:
    """One object of a subtitle as a viewer sees it, cut to what is visible.

    `x` and `y` are where the cut sits on the screen. `png` is the picture over
    a plain background. `key` names the content: two pictures with the same key
    look the same.
    """

    x: int
    y: int
    width: int
    height: int
    png: bytes = field(repr=False)
    key: str


@dataclass(frozen=True)
class ImageEvent:
    """What is on screen from `start_ms` to `end_ms`, pictures from top to bottom."""

    start_ms: int
    end_ms: int
    pictures: tuple[Picture, ...]
    screen: tuple[int, int]
    forced: bool = False


@dataclass
class _Object:
    width: int
    height: int
    data: bytearray
    expected: int


@dataclass(frozen=True)
class _Placed:
    object_id: int
    x: int
    y: int
    forced: bool
    crop: tuple[int, int, int, int] | None


def looks_like_pgs(data) -> bool:
    return len(data) >= _HEADER and data[:2] == MAGIC


def _segments(data) -> Iterator[tuple[int, int, bytes]]:
    """Every segment as (time in ms, type, payload).

    `data` is bytes or a memory map. The payloads are copies, so nothing
    keeps a map open once the stream is read.
    """
    position = 0
    size_of_data = len(data)
    while position < size_of_data:
        if position + _HEADER > size_of_data:
            # A stream that was cut off in the middle of a header ends here.
            return
        if data[position : position + 2] != MAGIC:
            raise PgsError(f"no segment starts at byte {position}")
        pts, _dts, kind, size = struct.unpack_from(">IIBH", data, position + 2)
        start = position + _HEADER
        if start + size > size_of_data:
            return
        yield pts // 90, kind, data[start : start + size]
        position = start + size


def decode_rle(data: bytes, width: int, height: int) -> np.ndarray:
    """The palette index of every pixel of an object, as rows of a (height, width) array."""
    if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE) or (
        width * height > _MAX_OBJECT_PIXELS
    ):
        raise PgsError(f"an object of {width}x{height} pixels is refused")
    rows: list[bytes] = []
    row: list[bytes] = []
    filled = 0
    for match in _TOKEN.finditer(data):
        token = match.group()
        if token[0]:
            if filled < width:
                row.append(token)
                filled += len(token)
            continue
        code = token[1]
        if code == 0:
            line = b"".join(row)
            rows.append(line[:width].ljust(width, b"\x00"))
            row, filled = [], 0
            if len(rows) == height:
                break
            continue
        kind = code & 0xC0
        if kind == 0x00:
            length, color = code & 0x3F, 0
        elif kind == 0x40:
            length, color = ((code & 0x3F) << 8) | token[2], 0
        elif kind == 0x80:
            length, color = code & 0x3F, token[2]
        else:
            length, color = ((code & 0x3F) << 8) | token[2], token[3]
        if filled < width:
            length = min(length, width - filled)
            row.append(bytes((color,)) * length)
            filled += length
    if row and len(rows) < height:
        rows.append(b"".join(row)[:width].ljust(width, b"\x00"))
    if len(rows) < height:
        rows.extend([b"\x00" * width] * (height - len(rows)))
    return np.frombuffer(b"".join(rows), dtype=np.uint8).reshape(height, width)


def _rgba(entries: np.ndarray, high_definition: bool) -> np.ndarray:
    """Palette entries (Y, Cr, Cb, alpha) as RGBA."""
    luma = entries[:, 0].astype(np.float32) - 16.0
    red_diff = entries[:, 1].astype(np.float32) - 128.0
    blue_diff = entries[:, 2].astype(np.float32) - 128.0
    if high_definition:  # BT.709
        red = 1.164 * luma + 1.793 * red_diff
        green = 1.164 * luma - 0.213 * blue_diff - 0.533 * red_diff
        blue = 1.164 * luma + 2.112 * blue_diff
    else:  # BT.601
        red = 1.164 * luma + 1.596 * red_diff
        green = 1.164 * luma - 0.392 * blue_diff - 0.813 * red_diff
        blue = 1.164 * luma + 2.017 * blue_diff
    colors = np.stack([red, green, blue], axis=1).round().clip(0, 255).astype(np.uint8)
    return np.concatenate([colors, entries[:, 3:4]], axis=1)


def _render(indexes: np.ndarray, palette: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The object over the background, and which pixels of it are visible."""
    rgba = palette[indexes]
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    background = np.array(BACKGROUND, dtype=np.float32)
    rgb = rgba[..., :3].astype(np.float32) * alpha + background * (1.0 - alpha)
    return rgb.round().astype(np.uint8), rgba[..., 3] > _VISIBLE_ALPHA


def _cut(rgb: np.ndarray, visible: np.ndarray) -> tuple[np.ndarray, int, int] | None:
    """Cut a rendered object to what is visible, with a margin around it. None when nothing is."""
    rows = np.flatnonzero(visible.any(axis=1))
    columns = np.flatnonzero(visible.any(axis=0))
    if len(rows) == 0:
        return None
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(columns[0]), int(columns[-1]) + 1
    framed = np.empty((bottom - top + 2 * _PADDING, right - left + 2 * _PADDING, 3), dtype=np.uint8)
    framed[:] = BACKGROUND
    framed[_PADDING:-_PADDING, _PADDING:-_PADDING] = rgb[top:bottom, left:right]
    return framed, left - _PADDING, top - _PADDING


@dataclass
class _Staged:
    """An object on screen under one palette, not rendered yet."""

    x: int
    y: int
    indexes: np.ndarray
    palette: np.ndarray
    # How much of it is lit: opaque pixels count in full, faint ones in part.
    lit: float


class _Reader:
    """Follows a stream from display set to display set and collects the events."""

    def __init__(self) -> None:
        self.events: list[ImageEvent] = []
        self.screen = (1920, 1080)
        self.palettes: dict[int, np.ndarray] = {}
        self.objects: dict[int, _Object] = {}
        # The composition of the display set that is being read.
        self.time_ms = 0
        self.palette_id = 0
        self.placed: list[_Placed] = []
        self.composed = False
        # What is on screen: since when, its content, and its best-lit rendering.
        self.open_since: int | None = None
        self.open_content: tuple | None = None
        self.open_staged: list[_Staged] = []
        self.open_lit = -1.0
        self.open_forced = False
        # The pixels of the objects on screen, and how often each palette
        # entry occurs in them. A fade shows the same objects under many
        # palettes, and they are decoded once.
        self._decoded: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def composition(self, time_ms: int, payload: bytes) -> None:
        if len(payload) < 11:
            raise PgsError("a composition segment is cut short")
        width, height, _rate, _number, state, _update, palette_id, count = struct.unpack_from(
            ">HHBHBBBB", payload, 0
        )
        if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
            raise PgsError(f"a screen of {width}x{height} pixels is refused")
        if state & _EPOCH_START:
            self.palettes.clear()
            self.objects.clear()
        self.screen = (width, height)
        self.time_ms = time_ms
        self.palette_id = palette_id
        self.placed = []
        self.composed = True
        position = 11
        for _ in range(count):
            if position + 8 > len(payload):
                raise PgsError("a composition segment is cut short")
            object_id, _window, flags, x, y = struct.unpack_from(">HBBHH", payload, position)
            position += 8
            crop = None
            if flags & _CROPPED:
                if position + 8 > len(payload):
                    raise PgsError("a composition segment is cut short")
                crop = struct.unpack_from(">HHHH", payload, position)
                position += 8
            self.placed.append(_Placed(object_id, x, y, bool(flags & _FORCED), crop))

    def palette(self, payload: bytes) -> None:
        if len(payload) < 2:
            return
        palette_id = payload[0]
        table = self.palettes.get(palette_id)
        if table is None:
            table = np.zeros((256, 4), dtype=np.uint8)
            table[:, 0] = 16
            table[:, 1:3] = 128
        else:
            table = table.copy()
        body = np.frombuffer(payload[2:], dtype=np.uint8)
        body = body[: len(body) // 5 * 5].reshape(-1, 5)
        table[body[:, 0]] = body[:, 1:5]
        self.palettes[palette_id] = table

    def object(self, payload: bytes) -> None:
        if len(payload) < 4:
            return
        object_id = int.from_bytes(payload[0:2], "big")
        if payload[3] & _FIRST_IN_SEQUENCE:
            if len(payload) < 11:
                raise PgsError("an object segment is cut short")
            length = int.from_bytes(payload[4:7], "big")
            width, height = struct.unpack_from(">HH", payload, 7)
            self.objects[object_id] = _Object(width, height, bytearray(payload[11:]), length - 4)
        elif object_id in self.objects:
            current = self.objects[object_id]
            if len(current.data) + len(payload) - 4 <= max(current.expected, 0) + 64:
                current.data.extend(payload[4:])

    def _stage(self, placed: _Placed, palette: np.ndarray) -> tuple[tuple, _Staged | None]:
        item = self.objects.get(placed.object_id)
        if item is None:
            return (), None
        digest = hashlib.sha256(struct.pack(">HH", item.width, item.height))
        digest.update(item.data)
        name = digest.hexdigest()
        content = (name, placed.x, placed.y, placed.crop)
        key = f"{name}:{placed.crop}"
        known = self._decoded.get(key)
        if known is None:
            indexes = decode_rle(bytes(item.data), item.width, item.height)
            if placed.crop is not None:
                left, top, width, height = placed.crop
                indexes = indexes[top : top + height, left : left + width]
            known = (indexes, np.bincount(indexes.ravel(), minlength=256))
            self._decoded[key] = known
        indexes, counts = known
        lit = float((counts * palette[:, 3].astype(np.int64)).sum())
        if lit <= 0:
            return content, None
        return content, _Staged(placed.x, placed.y, indexes, palette, lit)

    def _close(self, time_ms: int) -> None:
        if self.open_since is None:
            return
        pictures = []
        if time_ms > self.open_since:
            for staged in self.open_staged:
                rgb, visible = _render(staged.indexes, _rgba(staged.palette, self.screen[1] >= 600))
                cut = _cut(rgb, visible)
                if cut is None:
                    continue
                png = encode_png(cut[0])
                pictures.append(
                    Picture(
                        x=staged.x + cut[1],
                        y=staged.y + cut[2],
                        width=int(cut[0].shape[1]),
                        height=int(cut[0].shape[0]),
                        png=png,
                        key=hashlib.sha256(png).hexdigest(),
                    )
                )
        if pictures:
            if len(self.events) >= MAX_EVENTS:
                raise PgsError(f"the stream has more than {MAX_EVENTS} subtitles")
            self.events.append(
                ImageEvent(
                    start_ms=self.open_since,
                    end_ms=time_ms,
                    pictures=tuple(sorted(pictures, key=lambda p: (p.y, p.x))),
                    screen=self.screen,
                    forced=self.open_forced,
                )
            )
        self.open_since = None
        self.open_content = None
        self.open_staged = []
        self.open_lit = -1.0
        self.open_forced = False
        self._decoded.clear()

    def end(self) -> None:
        """A display set is complete: what it shows replaces what was on screen."""
        if not self.composed:
            return
        self.composed = False
        palette = self.palettes.get(self.palette_id)
        shown: list[tuple[tuple, _Staged | None]] = []
        if palette is not None:
            shown = [self._stage(placed, palette) for placed in self.placed]
            shown = [(content, staged) for content, staged in shown if content]
        if not shown:
            self._close(self.time_ms)
            return
        content = tuple(item for item, _ in shown)
        if content != self.open_content:
            self._close(self.time_ms)
            self.open_since = self.time_ms
            self.open_content = content
        # A subtitle that fades in is the same objects under a palette that
        # grows less transparent. The frame in which most of it is lit is the
        # one a reader should get.
        staged = [item for _, item in shown if item is not None]
        lit = sum(item.lit for item in staged)
        if lit > self.open_lit:
            self.open_lit = lit
            self.open_staged = staged
        self.open_forced = self.open_forced or any(placed.forced for placed in self.placed)


def read_pgs(data) -> list[ImageEvent]:
    """The subtitles of a PGS stream, in the order they are shown.

    `data` is the stream as bytes, or a memory map of the file that holds it.
    """
    if not looks_like_pgs(data):
        raise PgsError("the data does not start like a PGS stream")
    reader = _Reader()
    last_ms = 0
    for time_ms, kind, payload in _segments(data):
        last_ms = max(last_ms, time_ms)
        if kind == _COMPOSITION:
            # A composition that follows one without an end segment closes it.
            reader.end()
            reader.composition(time_ms, payload)
        elif kind == _PALETTE:
            reader.palette(payload)
        elif kind == _OBJECT:
            reader.object(payload)
        elif kind == _END:
            reader.end()
        elif kind != _WINDOW:
            raise PgsError(f"unknown segment type 0x{kind:02x}")
    reader.end()
    # A stream that ends with a subtitle on screen shows it for a moment longer.
    reader._close(max(last_ms, (reader.open_since or 0) + 2_000))
    return reader.events


def encode_png(rgb: np.ndarray) -> bytes:
    """An (height, width, 3) array of bytes as a PNG file."""
    height, width = rgb.shape[:2]
    rows = np.zeros((height, 1 + width * 3), dtype=np.uint8)
    rows[:, 1:] = rgb.reshape(height, width * 3)

    def chunk(name: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + name
            + body
            + struct.pack(">I", zlib.crc32(name + body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows.tobytes(), 6))
        + chunk(b"IEND", b"")
    )
