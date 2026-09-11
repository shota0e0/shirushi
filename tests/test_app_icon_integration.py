from __future__ import annotations

from pathlib import Path
import struct
import unittest

from PIL import Image


PROJECT = Path(__file__).resolve().parents[1]
PNG_PATH = PROJECT / "assets/app_icon.png"
ICO_PATH = PROJECT / "assets/app_icon.ico"
MASTER_PATH = PROJECT / "assets/branding/shirushi-app-icon-master.png"
SPEC_PATH = PROJECT / "packaging/Shirushi.spec"


class AppIconIntegrationTests(unittest.TestCase):
    def test_master_and_runtime_assets_are_valid(self) -> None:
        self.assertTrue(MASTER_PATH.is_file())
        with Image.open(PNG_PATH) as image:
            self.assertEqual("PNG", image.format)
            self.assertEqual("RGBA", image.mode)
            self.assertEqual((512, 512), image.size)
        with Image.open(ICO_PATH) as image:
            self.assertEqual("ICO", image.format)
            self.assertEqual(
                {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)},
                image.ico.sizes(),
            )

    def test_spec_packages_and_embeds_the_official_icon(self) -> None:
        source = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIn('(str(PROJECT_ROOT / "assets" / "app_icon.png"), "gui")', source)
        self.assertIn('(str(PROJECT_ROOT / "assets" / "app_icon.ico"), "gui")', source)
        self.assertIn('icon=str(PROJECT_ROOT / "assets" / "app_icon.ico")', source)

    def test_window_icon_is_reapplied_after_native_window_mapping(self) -> None:
        source = (PROJECT / "src/gui/app.py").read_text(encoding="utf-8")
        self.assertIn("self.root.after_idle(self._set_mapped_window_icon)", source)
        self.assertIn("self.root.iconphoto(False, self._icon_image)", source)
        self.assertIn("self.root.iconbitmap(str(APP_ICON_ICO_PATH))", source)
        self.assertNotIn("except tk.TclError:\n            return", source)

    def test_ico_directory_contains_exact_requested_sizes(self) -> None:
        data = ICO_PATH.read_bytes()
        reserved, image_type, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual((0, 1, 7), (reserved, image_type, count))
        sizes = set()
        for index in range(count):
            width, height = struct.unpack_from("<BB", data, 6 + index * 16)
            sizes.add((width or 256, height or 256))
        self.assertEqual(
            {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)},
            sizes,
        )


if __name__ == "__main__":
    unittest.main()
