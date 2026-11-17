import gzip
import zipfile

import py7zr
import pytest

from retimer.unpack import TooLarge, UnpackError, unpack_image_subtitle

from .pgs_helpers import simple_stream

STREAM = simple_stream(3)
LIMIT = 10_000_000


def seven_zip(path, members: dict[str, bytes], password: str | None = None):
    with py7zr.SevenZipFile(path, "w", password=password) as archive:
        for name, data in members.items():
            archive.writestr(data, name)
    return path


def test_a_bare_stream_is_taken_as_it_is(tmp_path):
    source = tmp_path / "upload"
    source.write_bytes(STREAM)

    packing = unpack_image_subtitle(source, tmp_path / "out", LIMIT)

    assert packing == "sup"
    assert (tmp_path / "out").read_bytes() == STREAM


def test_the_stream_is_taken_out_of_gzip_zip_and_7z(tmp_path):
    (tmp_path / "a.gz").write_bytes(gzip.compress(STREAM))
    with zipfile.ZipFile(tmp_path / "a.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("notes.txt", "from the disc")
        archive.writestr("folder/Episode 01.SUP", STREAM)
    seven_zip(tmp_path / "a.7z", {"readme.txt": b"hello", "Episode 01.sup": STREAM})

    for name, packing in (("a.gz", "gzip"), ("a.zip", "zip"), ("a.7z", "7z")):
        out = tmp_path / f"{name}.out"
        assert unpack_image_subtitle(tmp_path / name, out, LIMIT) == packing
        assert out.read_bytes() == STREAM


def test_of_several_streams_in_an_archive_the_largest_is_taken(tmp_path):
    larger = simple_stream(6)
    seven_zip(tmp_path / "a.7z", {"forced.sup": STREAM, "full.sup": larger})

    unpack_image_subtitle(tmp_path / "a.7z", tmp_path / "out", LIMIT)

    assert (tmp_path / "out").read_bytes() == larger


def test_what_unpacks_to_more_than_allowed_is_refused_and_leaves_nothing(tmp_path):
    big = STREAM + b"\x00" * 200_000
    (tmp_path / "a.sup").write_bytes(big)
    (tmp_path / "a.gz").write_bytes(gzip.compress(big))
    with zipfile.ZipFile(tmp_path / "a.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("a.sup", big)
    seven_zip(tmp_path / "a.7z", {"a.sup": big})

    for name in ("a.sup", "a.gz", "a.zip", "a.7z"):
        with pytest.raises(TooLarge):
            unpack_image_subtitle(tmp_path / name, tmp_path / "out", 100_000)
        assert not (tmp_path / "out").exists()


def test_an_archive_without_a_stream_is_refused(tmp_path):
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as archive:
        archive.writestr("subtitle.srt", "1\n00:00:01,000 --> 00:00:02,000\nhello\n")
    seven_zip(tmp_path / "a.7z", {"movie.sup": b"this is no PGS stream"})
    (tmp_path / "a.gz").write_bytes(gzip.compress(b"neither is this"))
    (tmp_path / "plain").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nhello\n")
    (tmp_path / "broken.7z").write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64)
    (tmp_path / "broken.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 64)
    (tmp_path / "broken.gz").write_bytes(b"\x1f\x8b" + b"\x00" * 64)

    for name in ("a.zip", "a.7z", "a.gz", "plain", "broken.7z", "broken.zip", "broken.gz"):
        with pytest.raises(UnpackError):
            unpack_image_subtitle(tmp_path / name, tmp_path / "out", LIMIT)
        assert not (tmp_path / "out").exists()


def test_an_archive_with_a_password_is_refused(tmp_path):
    seven_zip(tmp_path / "a.7z", {"a.sup": STREAM}, password="secret")

    with pytest.raises(UnpackError):
        unpack_image_subtitle(tmp_path / "a.7z", tmp_path / "out", LIMIT)


def test_a_name_in_an_archive_never_names_a_file_here(tmp_path):
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as archive:
        archive.writestr("../../escaped.sup", STREAM)
    out = tmp_path / "job" / "out"
    out.parent.mkdir()

    unpack_image_subtitle(tmp_path / "a.zip", out, LIMIT)

    assert out.read_bytes() == STREAM
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.zip", "job"]
