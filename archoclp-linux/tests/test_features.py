import plistlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import archoclp_features as features


class DownloadCatalogTests(unittest.TestCase):
    def test_recognizes_direct_file_types(self):
        self.assertEqual(features._direct_file_kind("https://updates.cdn-apple.com/old/Sierra.dmg"), "DMG")
        self.assertEqual(features._direct_file_kind("https://swcdn.apple.com/InstallAssistant.pkg"), "PKG")
        self.assertEqual(features._direct_file_kind("https://archive.org/download/item/osx.iso"), "ISO")
        self.assertIsNone(features._direct_file_kind("https://example.com/article"))
        self.assertTrue(features._is_apple_download_host("updates-http.cdn-apple.com"))
        self.assertTrue(features._is_apple_download_host("swcdn.apple.com"))
        self.assertFalse(features._is_apple_download_host("fakeapple.com"))

    def test_download_rejects_non_https(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                features.download_file("http://example.com/test.dmg", Path(tmp) / "x.dmg")

    def test_download_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "existing.dmg"
            destination.write_bytes(b"keep me")
            with self.assertRaises(FileExistsError):
                features.download_file("https://example.com/test.dmg", destination)
            self.assertEqual(destination.read_bytes(), b"keep me")

    def test_archive_anchor_parser_preserves_row_context(self):
        parser = features._AnchorParser().parser
        parser.feed('<table><tr><td>macOS 12.7.6</td><td><a href="https://swcdn.apple.com/InstallAssistant.pkg">InstallAssistant.pkg</a></td></tr></table>')
        self.assertEqual(len(parser.links), 1)
        self.assertIn("12.7.6", parser.links[0][2])

    def test_apple_silicon_refused_by_intel_efi_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "Apple Silicon"):
                features.build_efi("MacBookPro18,3", Path(tmp))


class ConfigBuilderTests(unittest.TestCase):
    def test_build_config_creates_enabled_kext_entry_and_data_rom_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "Sample.plist"
            sample.write_bytes(plistlib.dumps({
                "Kernel": {"Add": []},
                "UEFI": {"Drivers": [{"Path": "OpenRuntime.efi", "Enabled": False}]},
                "Misc": {"Boot": {}, "Security": {}},
                "NVRAM": {"Add": {}},
                "PlatformInfo": {"Generic": {}},
            }))
            oc_dir = root / "EFI" / "OC"
            kext = oc_dir / "Kexts" / "Lilu.kext" / "Contents"
            (kext / "MacOS").mkdir(parents=True)
            (kext / "MacOS" / "Lilu").write_bytes(b"test")
            (kext / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleExecutable": "Lilu", "CFBundleIdentifier": "as.vit9696.Lilu", "CFBundleVersion": "1.0"
            }))
            config_path, warnings = features._build_config(sample, oc_dir, "MacBookPro9,2")
            config = plistlib.loads(config_path.read_bytes())
            self.assertEqual(config["Kernel"]["Add"][0]["BundlePath"], "Lilu.kext")
            self.assertTrue(config["Kernel"]["Add"][0]["Enabled"])
            self.assertEqual(config["PlatformInfo"]["Generic"]["ROM"], b"\x00" * 6)
            self.assertEqual(config["PlatformInfo"]["Generic"]["SystemProductName"], "MacBookPro9,2")
            self.assertTrue(warnings)

    def test_zip_extractor_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "bad")
            with self.assertRaises(ValueError):
                features._safe_extract(archive, root / "out")


if __name__ == "__main__":
    unittest.main()
