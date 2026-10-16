import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import archoclp


class DetectTests(unittest.TestCase):
    def test_detects_macbookpro92_from_sysfs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sys_vendor").write_text("Apple Inc.\n")
            (root / "product_name").write_text("MacBookPro9,2\n")
            (root / "product_version").write_text("1.0\n")
            (root / "board_name").write_text("Mac-6F01561E16C75D06\n")
            result = archoclp.detect_hardware(root, allow_dmidecode=False)
            self.assertEqual(result["model_identifier"], "MacBookPro9,2")
            self.assertEqual(result["detection_status"], "mac-detected")

    def test_non_mac_is_not_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sys_vendor").write_text("ASUSTeK COMPUTER INC.\n")
            (root / "product_name").write_text("Notebook\n")
            result = archoclp.detect_hardware(root, allow_dmidecode=False)
            self.assertIsNone(result["model_identifier"])
            self.assertEqual(result["detection_status"], "not-detected-as-mac")

    def test_model_id_can_be_extracted_from_product_version(self):
        self.assertEqual(archoclp._normalise_model("Unknown", "MacBookPro9,2"), "MacBookPro9,2")


class BuildWorkspaceTests(unittest.TestCase):
    def test_build_creates_model_specific_report_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            target = archoclp.build_workspace("MacBookPro9,2", output)
            self.assertTrue((target / "REPORT.md").exists())
            data = json.loads((target / "manifest.json").read_text())
            self.assertEqual(data["target_model"], "MacBookPro9,2")
            self.assertFalse(data["build_plan"]["efi_generated"])
            self.assertIn("Ivy Bridge", data["model_profile"]["generation"])

    def test_unmapped_models_are_not_guessed(self):
        profile = archoclp.model_profile("MacBookPro99,9", {})
        self.assertEqual(profile["profile_status"], "unmapped-model")
        self.assertIn("Must be probed", profile["gpu"])


if __name__ == "__main__":
    unittest.main()
