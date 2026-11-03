"""Offline tests for input integrity and Android disk/APEX extraction."""
import hashlib
import importlib.util
import io
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("prepare", Path(__file__).resolve().parents[1] / "scripts/prepare_dependencies.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def image_fixture():
    data = bytearray(131072)
    data[512:520] = b"EFI PART"
    struct.pack_into("<QII", data, 512 + 72, 2, 1, 128)
    struct.pack_into("<QQ", data, 1024 + 32, 64, 255)
    data[1024 + 56:1024 + 66] = "super".encode("utf-16-le")
    base = 64 * 512
    header = base + 12288
    struct.pack_into("<IHHI", data, header, 0x414C5030, 10, 0, 128)
    struct.pack_into("<I", data, header + 44, 100)
    struct.pack_into("<III", data, header + 80, 0, 1, 52)
    struct.pack_into("<III", data, header + 92, 52, 2, 24)
    table = header + 128
    data[table:table + 6] = b"system"
    struct.pack_into("<II", data, table + 40, 0, 2)
    struct.pack_into("<QIQI", data, table + 52, 1, 0, 64, 0)
    struct.pack_into("<QIQI", data, table + 76, 1, 0, 68, 0)
    data[base + 64 * 512:base + 65 * 512] = b"a" * 512
    data[base + 68 * 512:base + 69 * 512] = b"b" * 512
    return data, table


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def extract(self, data):
        raw, out = self.root / "disk.raw", self.root / "system.img"
        raw.write_bytes(data)
        prepare.extract_system(raw, out)
        return out.read_bytes()

    def test_multiple_extents_are_concatenated(self):
        data, _ = image_fixture()
        self.assertEqual(self.extract(data), b"a" * 512 + b"b" * 512)

    def test_system_a_is_supported(self):
        data, table = image_fixture()
        data[table:table + 8] = b"system_a"
        self.assertEqual(len(self.extract(data)), 1024)

    def test_missing_system_is_rejected(self):
        data, table = image_fixture()
        data[table:table + 6] = b"vendor"
        with self.assertRaisesRegex(ValueError, "No system"):
            self.extract(data)

    def test_unknown_extent_type_is_rejected(self):
        data, table = image_fixture()
        struct.pack_into("<I", data, table + 52 + 8, 1)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.extract(data)

    def test_out_of_bounds_extent_is_rejected(self):
        data, table = image_fixture()
        struct.pack_into("<Q", data, table + 52 + 12, 10000)
        with self.assertRaisesRegex(ValueError, "out-of-bounds"):
            self.extract(data)

    def test_truncated_image_is_rejected(self):
        data, _ = image_fixture()
        with self.assertRaises(ValueError):
            self.extract(data[:100])

    def test_invalid_descriptor_is_rejected(self):
        data, table = image_fixture()
        struct.pack_into("<I", data, table - 128 + 92 + 4, 10000)
        with self.assertRaisesRegex(ValueError, "descriptors"):
            self.extract(data)

    def test_apex_and_compressed_apex(self):
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as z:
            z.writestr("apex_payload.img", b"filesystem")
        archive, out = self.root / "module.apex", self.root / "payload.img"
        archive.write_bytes(nested.getvalue())
        prepare.extract_apex(archive, out)
        self.assertEqual(out.read_bytes(), b"filesystem")
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("original_apex", nested.getvalue())
        prepare.extract_apex(archive, out)
        self.assertEqual(out.read_bytes(), b"filesystem")

    def test_download_hash_and_cache(self):
        source = self.root / "source"
        source.write_bytes(b"download")
        spec = {"url": source.as_uri(), "sha256": hashlib.sha256(b"download").hexdigest()}
        target = prepare.download(spec, self.root / "cache")
        with patch.object(prepare, "run", side_effect=AssertionError("unexpected download")):
            self.assertEqual(prepare.download(spec, self.root / "cache"), target)
        target.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            prepare.download(spec, self.root / "cache")

    def test_bad_download_is_not_published(self):
        source = self.root / "source"
        source.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            prepare.download({"url": source.as_uri(), "sha256": "0" * 64}, self.root / "cache")
        self.assertEqual(list((self.root / "cache").iterdir()), [])

    def test_debugfs_zero_exit_without_output_is_failure(self):
        with patch.object(prepare, "run"):
            with self.assertRaisesRegex(ValueError, "did not produce"):
                prepare.debugfs("debugfs", "image", "dump /missing", self.root / "missing")

    def test_incomplete_android_cache_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing Android"):
            prepare.validate_android(self.root)

    def test_checkout_preserves_local_modifications(self):
        origin = self.root / "origin"
        prepare.run("git", "init", "--quiet", origin)
        (origin / "file").write_text("original")
        prepare.run("git", "-C", origin, "add", "file")
        prepare.run("git", "-C", origin, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture")
        spec = {"url": str(origin), "commit": prepare.capture("git", "-C", origin, "rev-parse", "HEAD")}
        checkout = self.root / "checkout"
        prepare.checkout(spec, checkout)
        prepare.checkout(spec, checkout)
        (checkout / "file").write_text("user edit")
        with self.assertRaises(subprocess.CalledProcessError):
            prepare.checkout(spec, checkout)
        self.assertEqual((checkout / "file").read_text(), "user edit")


if __name__ == "__main__":
    unittest.main()
