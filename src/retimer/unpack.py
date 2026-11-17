"""Image subtitles as they are passed around: bare, or packed as gzip, zip or 7z.

A PGS stream is large, a hundred megabytes for an episode, and packs to a few.
Sites that share them serve archives, so the service takes those as they come
and gets the one stream out. An archive is a stranger's file: nothing in it
names a file here, what it claims about sizes is not believed, and what comes
out is counted while it is written.
"""

from __future__ import annotations

import gzip
import shutil
import zipfile
from pathlib import Path
from typing import BinaryIO

from retimer.pgs import MAGIC

_GZIP = b"\x1f\x8b"
_ZIP = b"PK\x03\x04"
_SEVEN_ZIP = b"7z\xbc\xaf\x27\x1c"
_EXTENSION = ".sup"
_CHUNK = 1024 * 1024


class UnpackError(ValueError):
    """The upload is not an image subtitle, or not an archive that holds one."""


class TooLarge(UnpackError):
    """What the archive holds is larger than the service takes."""


def _copy(source: BinaryIO, path: Path, limit: int) -> int:
    written = 0
    with path.open("wb") as handle:
        while True:
            chunk = source.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                raise TooLarge("the image subtitle is larger than allowed once it is unpacked")
            handle.write(chunk)
    return written


def _pick(names_and_sizes: list[tuple[str, int]]) -> str:
    """The member of an archive that is the image subtitle: the one .sup, or the largest."""
    candidates = [item for item in names_and_sizes if item[0].lower().endswith(_EXTENSION)]
    if not candidates:
        raise UnpackError("the archive holds no .sup file")
    return max(candidates, key=lambda item: item[1])[0]


def _from_zip(source: Path, dest: Path, limit: int) -> None:
    try:
        with zipfile.ZipFile(source) as archive:
            members = [(info.filename, info.file_size) for info in archive.infolist()]
            name = _pick(members)
            with archive.open(name) as member:
                _copy(member, dest, limit)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as error:
        raise UnpackError("the zip archive could not be read") from error


def _from_gzip(source: Path, dest: Path, limit: int) -> None:
    try:
        with gzip.open(source, "rb") as member:
            _copy(member, dest, limit)
    except (gzip.BadGzipFile, EOFError, OSError) as error:
        raise UnpackError("the gzip file could not be read") from error


def _from_seven_zip(source: Path, dest: Path, limit: int) -> None:
    try:
        import py7zr
        from py7zr.io import NullIO, Py7zIO, WriterFactory
    except ImportError as error:  # The service runs without it and says so.
        raise UnpackError("this server cannot read 7z archives") from error

    class _ToFile(Py7zIO):
        """Writes one member to `dest` and counts it."""

        def __init__(self) -> None:
            self.handle = dest.open("wb")
            self.written = 0

        def write(self, data) -> int:
            self.written += len(data)
            if self.written > limit:
                raise TooLarge("the image subtitle is larger than allowed once it is unpacked")
            return self.handle.write(data)

        def read(self, size: int | None = None) -> bytes:
            return b""

        def seek(self, offset: int, whence: int = 0) -> int:
            return offset

        def flush(self) -> None:
            self.handle.flush()

        def size(self) -> int:
            return self.written

    class _Factory(WriterFactory):
        def __init__(self, wanted: str) -> None:
            self.wanted = wanted
            self.writer: _ToFile | None = None

        def create(self, filename: str) -> Py7zIO:
            if filename == self.wanted and self.writer is None:
                self.writer = _ToFile()
                return self.writer
            return NullIO()

    factory: _Factory | None = None
    try:
        with py7zr.SevenZipFile(source, max_extract_size=limit) as archive:
            if archive.needs_password():
                raise UnpackError("the 7z archive is protected by a password")
            members = [
                (info.filename, info.uncompressed)
                for info in archive.list()
                if not info.is_directory
            ]
            name = _pick(members)
            if dict(members)[name] > limit:
                raise TooLarge("the image subtitle is larger than allowed once it is unpacked")
            factory = _Factory(name)
            archive.extract(targets=[name], factory=factory)
    except UnpackError:
        raise
    except py7zr.exceptions.DecompressionBombError as error:
        raise TooLarge("the image subtitle is larger than allowed once it is unpacked") from error
    except Exception as error:
        # The library raises many kinds of error on a damaged archive.
        raise UnpackError("the 7z archive could not be read") from error
    finally:
        if factory is not None and factory.writer is not None:
            factory.writer.handle.close()
    if factory.writer is None:
        raise UnpackError("the 7z archive could not be read")


def unpack_image_subtitle(source: Path, dest: Path, limit: int) -> str:
    """Write the PGS stream that `source` is, or holds, to `dest`.

    Returns how it came: "sup", "gzip", "zip" or "7z". `limit` is the most the
    stream may measure in bytes. On an error nothing is left at `dest`.
    """
    with source.open("rb") as handle:
        head = handle.read(8)
    try:
        if head.startswith(MAGIC):
            if source.stat().st_size > limit:
                raise TooLarge("the image subtitle is larger than allowed")
            shutil.copyfile(source, dest)
            packing = "sup"
        elif head.startswith(_SEVEN_ZIP):
            _from_seven_zip(source, dest, limit)
            packing = "7z"
        elif head.startswith(_ZIP):
            _from_zip(source, dest, limit)
            packing = "zip"
        elif head.startswith(_GZIP):
            _from_gzip(source, dest, limit)
            packing = "gzip"
        else:
            raise UnpackError(
                "the file is neither a PGS stream (.sup) nor a gzip, zip or 7z archive of one"
            )
        with dest.open("rb") as handle:
            if not handle.read(2) == MAGIC:
                raise UnpackError("what the archive holds is not a PGS stream (.sup)")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return packing
