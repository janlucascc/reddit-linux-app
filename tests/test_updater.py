#!/usr/bin/env python3
"""
Testes unitários para o sistema de verificação e atualização multi-formato Linux (.rpm, .deb, .appimage).
"""

import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest import TestCase, main, mock
from PySide6.QtWidgets import QApplication

from updater import (
    APP_VERSION,
    FORMAT_METADATA,
    UpdateAsset,
    UpdateDialog,
    UpdateReleaseInfo,
    detect_system_format,
    parse_semantic_version,
)
from ui_theme import ThemeManager


class TestUpdaterSystem(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)
        ThemeManager.instance().set_theme("dark")

    def test_version_parsing(self) -> None:
        self.assertEqual(parse_semantic_version("v1.0.0"), (1, 0, 0))
        self.assertEqual(parse_semantic_version("1.2.3"), (1, 2, 3))
        self.assertEqual(parse_semantic_version("V2.10.1-beta"), (2, 10, 1))
        self.assertGreater(parse_semantic_version("v1.1.0"), parse_semantic_version("v1.0.0"))
        self.assertGreater(parse_semantic_version("v2.0.0"), parse_semantic_version("v1.9.9"))

    def test_system_format_detection(self) -> None:
        fmt = detect_system_format()
        self.assertIn(fmt, ("appimage", "deb", "rpm", "arch", "generic"))
        self.assertIn(fmt, FORMAT_METADATA)

    def test_asset_mapping_multi_format(self) -> None:
        assets = [
            UpdateAsset(
                name="reddit-linux-1.1.0.x86_64.AppImage",
                download_url="https://github.com/test/download/appimage",
                size_bytes=52428800,
                format_type="appimage",
            ),
            UpdateAsset(
                name="reddit-linux_1.1.0_amd64.deb",
                download_url="https://github.com/test/download/deb",
                size_bytes=41943040,
                format_type="deb",
            ),
            UpdateAsset(
                name="reddit-linux-1.1.0.x86_64.rpm",
                download_url="https://github.com/test/download/rpm",
                size_bytes=42991616,
                format_type="rpm",
            ),
        ]

        info = UpdateReleaseInfo(
            tag_name="v1.1.0",
            version="1.1.0",
            title="Reddit Linux v1.1.0",
            body="Novos recursos e correções de interface.",
            html_url="https://github.com/test/release",
            published_at="2026-11-19",
            assets=assets,
            detected_format="rpm",
            is_newer=True,
        )

        rpm_asset = info.get_asset_for_format("rpm")
        self.assertIsNotNone(rpm_asset)
        self.assertEqual(rpm_asset.name, "reddit-linux-1.1.0.x86_64.rpm")
        self.assertIn("41.0 MB", rpm_asset.size_formatted)

        deb_asset = info.get_asset_for_format("deb")
        self.assertIsNotNone(deb_asset)
        self.assertEqual(deb_asset.name, "reddit-linux_1.1.0_amd64.deb")

        appimage_asset = info.get_asset_for_format("appimage")
        self.assertIsNotNone(appimage_asset)
        self.assertEqual(appimage_asset.name, "reddit-linux-1.1.0.x86_64.AppImage")

    def test_update_dialog_renders_offscreen(self) -> None:
        info = UpdateReleaseInfo(
            tag_name="v1.1.0",
            version="1.1.0",
            title="Reddit Linux v1.1.0",
            body="### Novidades:\n* Notificação de atualização\n* Suporte a .rpm, .deb e .appimage",
            html_url="https://github.com/test/release",
            published_at="2026-11-19",
            assets=[
                UpdateAsset(
                    name="reddit-linux-1.1.0.x86_64.AppImage",
                    download_url="https://github.com/test/appimage",
                    size_bytes=50000000,
                    format_type="appimage",
                )
            ],
            detected_format="deb",
            is_newer=True,
        )

        dlg = UpdateDialog(info)
        dlg.show()
        self.assertIn("Notificação de atualização", dlg.notes_browser.toPlainText())
        self.assertIn("Suporte a .rpm", dlg.notes_browser.toPlainText())
        dlg.close()

    def test_flatpak_detection(self) -> None:
        with mock.patch.dict(os.environ, {"FLATPAK_ID": "io.github.janlucascc.reddit-linux-app"}):
            fmt = detect_system_format()
            self.assertEqual(fmt, "flatpak")
            meta = FORMAT_METADATA.get(fmt)
            self.assertIsNotNone(meta)
            self.assertIn("Flatpak", meta["label"])


if __name__ == "__main__":
    main()
