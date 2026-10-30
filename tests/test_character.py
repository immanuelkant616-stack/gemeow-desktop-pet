"""Character animation tests using synthetic, code-drawn test images."""
from __future__ import annotations
import base64
import os
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import PyQt5
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(Path(PyQt5.__file__).parent / "Qt5" / "plugins" / "platforms")
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QImage, QMovie, QPainter
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from gemeow_character import CharacterAnimator, STATES

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def synthetic_sheet(path: Path):
    image = QImage(128, 128, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    for row in range(4):
        for col in range(4):
            x, y = col * 32, row * 32
            painter.fillRect(x + 2, y + 2, 14, 28, QColor(60 + row * 40, 20 + col * 50, 80))
            painter.fillRect(x + 16, y + 2, 14, 28, QColor(20 + col * 50, 80, 60 + row * 40))
    painter.end()
    assert image.save(str(path))


class CharacterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="character-test-", dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        synthetic_sheet(self.root / "bear_sprites.png")
        self.animator = CharacterAnimator(self.root)
        self.animator.set_size(32)
        self.addCleanup(self.animator.stop)
        self.addCleanup(lambda: self.animator.set_custom_assets({}))

    def pixel(self, x=5, y=10):
        return self.animator.current_pixmap().toImage().pixelColor(x, y)

    def test_default_state_and_transparency(self):
        self.assertEqual(self.animator.state, "idle")
        self.assertEqual(self.animator.size, 32)
        self.assertFalse(self.animator.current_pixmap().isNull())
        self.assertEqual(self.pixel(0, 0).alpha(), 0)
        self.assertEqual(self.animator.warnings, [])

    def test_state_change_is_immediate_and_validated(self):
        emitted = []
        self.animator.frame_changed.connect(lambda pix: emitted.append(pix))
        initial = self.pixel()
        self.animator.set_state("thinking")
        self.assertEqual(self.animator.state, "thinking")
        self.assertNotEqual(initial, self.pixel())
        self.assertEqual(len(emitted), 1)
        with self.assertRaises(ValueError):
            self.animator.set_state("unknown")
        self.assertEqual(self.animator.state, "thinking")

    def test_size_changes_square_output_and_validates_values(self):
        self.animator.set_size(125)
        pix = self.animator.current_pixmap()
        self.assertEqual((pix.width(), pix.height()), (125, 125))
        for bad in (0, -1, 20, True, "180", 2049):
            with self.assertRaises(ValueError):
                self.animator.set_size(bad)

    def test_direction_mirrors_listening_only(self):
        self.animator.set_state("listening")
        original_left, original_right = self.pixel(5), self.pixel(26)
        self.animator.set_direction("right")
        self.assertEqual(self.pixel(5), original_right)
        self.assertEqual(self.pixel(26), original_left)
        self.animator.set_state("idle")
        idle = self.pixel(5)
        self.animator.set_direction("left")
        self.assertEqual(self.pixel(5), idle)
        with self.assertRaises(ValueError):
            self.animator.set_direction("up")

    def test_timer_advances_frames_and_stop_pauses(self):
        self.animator.set_state("happy")
        first = self.pixel()
        self.animator.start()
        QTest.qWait(170)
        self.assertNotEqual(first, self.pixel())
        self.animator.stop()
        paused = self.pixel()
        QTest.qWait(170)
        self.assertEqual(paused, self.pixel())
        self.assertFalse(self.animator.timer.isActive())
        self.assertIs(self.animator.timer.parent(), self.animator)

    def test_motion_disabled_still_changes_state(self):
        self.animator.start()
        self.animator.set_motion_enabled(False)
        self.assertFalse(self.animator.timer.isActive())
        self.animator.set_state("thinking")
        initial = self.pixel()
        self.animator._advance()
        self.assertEqual(initial, self.pixel())
        self.animator.set_state("happy")
        self.assertNotEqual(initial, self.pixel())
        self.animator.set_motion_enabled(True)
        self.assertTrue(self.animator.timer.isActive())

    def test_default_frames_remain_cached_after_source_removed(self):
        (self.root / "bear_sprites.png").unlink()
        self.animator.start()
        self.animator._advance()
        self.animator.set_state("listening")
        self.animator.set_size(64)
        self.assertFalse(self.animator.current_pixmap().isNull())
        self.assertEqual(self.animator.current_pixmap().width(), 64)

    def test_missing_or_bad_grid_has_fallback_and_warning(self):
        absent = CharacterAnimator(self.root / "missing")
        self.addCleanup(absent.stop)
        self.assertTrue(absent.warnings)
        self.assertFalse(absent.current_pixmap().isNull())
        for state in STATES:
            absent.set_state(state)
            self.assertFalse(absent.current_pixmap().isNull())
        broken = QImage(2, 3, QImage.Format_ARGB32)
        broken.fill(Qt.transparent)
        broken.save(str(self.root / "bear_sprites.png"))
        bad = CharacterAnimator(self.root)
        self.addCleanup(bad.stop)
        self.assertTrue(bad.warnings)

    def test_png_override_is_static_cached_and_resettable(self):
        path = self.root / "custom.png"
        image = QImage(20, 40, QImage.Format_ARGB32)
        image.fill(QColor("#00FF00"))
        image.save(str(path))
        self.animator.set_custom_assets({"happy": path})
        self.animator.set_state("happy")
        self.assertEqual(self.pixel(16, 16), QColor("#00FF00"))
        path.unlink()
        self.animator.start()
        self.assertFalse(self.animator.timer.isActive())
        self.animator.set_size(64)
        self.assertFalse(self.animator.current_pixmap().isNull())
        self.animator.set_custom_assets({})
        self.assertTrue(self.animator.timer.isActive())

    def test_failed_custom_validation_keeps_previous_overrides(self):
        custom = self.root / "custom.png"
        image = QImage(32, 32, QImage.Format_ARGB32)
        image.fill(QColor("#FF00FF"))
        image.save(str(custom))
        self.animator.set_custom_assets({"idle": custom})
        before = self.pixel()
        with self.assertRaises(ValueError):
            self.animator.set_custom_assets({"idle": custom, "happy": self.root / "not-there.png"})
        self.assertEqual(before, self.pixel())
        self.assertEqual(self.animator._custom_paths["idle"], str(custom.resolve()))
        for bad in ({"x": custom}, {"idle": 123}, {"idle": self.root / "file.txt"}, []):
            with self.assertRaises(ValueError):
                self.animator.set_custom_assets(bad)
        self.assertEqual(before, self.pixel())

    def test_gif_uses_qmovie_and_stop_pauses_without_reading_again(self):
        path = self.root / "custom.gif"
        path.write_bytes(base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"))
        self.animator.set_custom_assets({"thinking": path})
        self.animator.set_state("thinking")
        self.assertIsInstance(self.animator._movies["thinking"], QMovie)
        self.assertFalse(self.animator.current_pixmap().isNull())
        self.animator.start()
        self.animator.stop()
        self.assertNotEqual(self.animator._movies["thinking"].state(), QMovie.Running)
        self.animator.set_state("idle")
        self.assertEqual(self.animator.state, "idle")

    def test_fractional_grid_dimensions_are_supported(self):
        image = QImage(1254, 1254, QImage.Format_ARGB32)
        image.fill(QColor("#D6B082"))
        image.save(str(self.root / "bear_sprites.png"))
        animator = CharacterAnimator(self.root)
        self.addCleanup(animator.stop)
        self.assertEqual(animator.warnings, [])
        self.assertEqual(len(animator._defaults["idle"]), 4)
        self.assertEqual(animator._defaults["idle"][0].width(), 314)
        self.assertEqual(animator._defaults["idle"][1].width(), 313)
        self.assertEqual(sum(frame.width() for frame in animator._defaults["idle"]), 1254)

    def test_empty_override_values_restore_default(self):
        self.animator.set_custom_assets({"idle": "", "thinking": None})
        self.assertEqual(self.animator._custom_paths, {})
        self.assertFalse(self.animator.current_pixmap().isNull())


if __name__ == "__main__":
    unittest.main(verbosity=2)
