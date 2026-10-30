"""Integration tests: actual Qt event loop + SQLite, mocked network only."""
from __future__ import annotations
import os
os.environ["QT_QPA_PLATFORM"] = "windows"
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import QThread, QTimer, Qt
from PyQt5.QtGui import QInputMethodEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox, QSystemTrayIcon
import Gemeow
import gemeow_api

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
APP.setStyle("Fusion")
APP.setStyleSheet(Gemeow.STYLE)

def response(text=None, finish=None):
    return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=finish)] if finish else [])

def normal_stream(text="안녕이다냥!"):
    yield response(text)
    yield response(finish="STOP")


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ui-test-", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.client = Mock()
        self.chat_api = self.client.chats.create.return_value
        self.chat_api.send_message_stream.side_effect = lambda _: normal_stream()
        self.patch_client = patch.object(gemeow_api.genai, "Client", return_value=self.client)
        self.patch_client.start()
        self.addCleanup(self.patch_client.stop)
        self.patch_tray = patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False)
        self.patch_tray.start()
        self.addCleanup(self.patch_tray.stop)
        self.patch_delay = patch.object(gemeow_api, "RETRY_DELAY_SECONDS", 0)
        self.patch_delay.start()
        self.addCleanup(self.patch_delay.stop)
        self.c = Gemeow.AppController(Path(self.temp.name))
        self.c.api_key = "dummy-never-network"
        self.addCleanup(self.cleanup_controller)

    def cleanup_controller(self):
        if self.c.active:
            self.c.active["cancel_event"].set()
            self.wait_until(lambda: not self.c.busy)
        self.c.quitting = False
        if self.c.thread.isRunning():
            self.c.close_worker.emit()
            self.wait_until(lambda: not self.c.thread.isRunning())
        self.c.tray.hide()
        if self.c.settings_dialog:
            self.c.settings_dialog.hide()
            self.c.settings_dialog.deleteLater()
        self.c.chat.hide()
        self.c.pet.hide()
        self.c.chat.deleteLater()
        self.c.pet.deleteLater()
        APP.processEvents()

    def wait_until(self, condition, seconds=4):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(condition(), "Qt work did not complete in time")

    def send(self, question="안녕"):
        self.c.chat.input.setPlainText(question)
        self.c.send_input()
        self.wait_until(lambda: not self.c.busy)

    def test_network_runs_off_gui_thread_and_gui_keeps_ticking(self):
        ticks = []
        worker_threads = []
        def delayed(_):
            worker_threads.append(QThread.currentThread())
            time.sleep(.22)
            yield response("기다려 줘서 고맙다냥.")
            yield response(finish="STOP")
        self.chat_api.send_message_stream.side_effect = delayed
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        self.send()
        timer.stop()
        self.assertGreaterEqual(len(ticks), 8)
        self.assertIsNot(worker_threads[0], APP.thread())
        self.assertEqual(len(self.c.store.get_turns(self.c.current_conversation)), 1)
        self.assertTrue(self.c.chat.send_button.isEnabled())

    def test_complete_pair_persists_and_next_request_restores_it(self):
        self.send("내 이름은 나비야")
        self.send("내 이름은 뭐야?")
        self.assertEqual(self.client.chats.create.call_count, 1)
        turns = self.c.store.get_turns(self.c.current_conversation)
        self.assertEqual([t["question"] for t in turns], ["내 이름은 나비야", "내 이름은 뭐야?"])
        restored = Gemeow.Store(Path(self.temp.name))
        self.assertEqual(len(restored.get_turns(self.c.current_conversation)), 2)

    def test_drafts_are_separate_across_sessions(self):
        first = self.c.current_conversation
        self.c.chat.input.setPlainText("첫 대화의 초안")
        self.c.new_conversation()
        second = self.c.current_conversation
        self.assertEqual(self.c.chat.input.toPlainText(), "")
        self.c.chat.input.setPlainText("둘째 초안")
        self.c.select_conversation(first)
        self.assertEqual(self.c.chat.input.toPlainText(), "첫 대화의 초안")
        self.c.select_conversation(second)
        self.assertEqual(self.c.chat.input.toPlainText(), "둘째 초안")

    def test_duplicate_send_is_blocked(self):
        def delayed(_):
            time.sleep(.15)
            yield from normal_stream()
        self.chat_api.send_message_stream.side_effect = delayed
        self.c.send_question("한 번")
        self.c.send_question("두 번")
        self.wait_until(lambda: not self.c.busy)
        self.assertEqual(self.chat_api.send_message_stream.call_count, 1)

    def test_failed_question_is_restored_without_saving(self):
        error = RuntimeError("raw private provider text")
        error.code = 403
        self.chat_api.send_message_stream.side_effect = error
        self.send("다시 물어볼 질문")
        self.assertEqual(self.c.chat.input.toPlainText(), "다시 물어볼 질문")
        self.assertEqual(self.c.store.get_turns(self.c.current_conversation), [])
        self.assertNotIn("raw private", self.c.chat.status.text())
        self.assertEqual(self.c.last_failed, "다시 물어볼 질문")

    def test_cancelled_partial_response_is_not_history(self):
        def delayed(_):
            yield response("부분 답변")
            time.sleep(.15)
            yield response("나머지")
            yield response(finish="STOP")
        self.chat_api.send_message_stream.side_effect = delayed
        self.c.send_question("취소할 질문")
        self.wait_until(lambda: bool(self.c.stream_text))
        self.c.cancel_request()
        self.wait_until(lambda: not self.c.busy)
        self.assertEqual(self.c.store.get_turns(self.c.current_conversation), [])
        self.assertEqual(self.c.last_failed, "취소할 질문")

    def test_retry_preserves_new_draft(self):
        self.c.last_failed = "이전 실패 질문"
        self.c.chat.input.setPlainText("새로 쓰던 초안")
        self.c.retry_question()
        self.wait_until(lambda: not self.c.busy)
        self.assertEqual(self.c.chat.input.toPlainText(), "새로 쓰던 초안")
        self.assertEqual(self.c.current_turns()[0]["question"], "이전 실패 질문")

    def test_html_is_displayed_as_plain_text(self):
        self.send("<b>내 질문</b>")
        self.assertIn("<b>내 질문</b>", self.c.chat.transcript.toPlainText())

    def test_enter_shift_enter_and_composition(self):
        widget = Gemeow.MessageInput()
        sent = []
        widget.send_requested.connect(lambda: sent.append(True))
        QTest.keyClick(widget, Qt.Key_Return, Qt.ShiftModifier)
        self.assertIn("\n", widget.toPlainText())
        QTest.keyClick(widget, Qt.Key_Return)
        self.assertEqual(len(sent), 1)
        widget.inputMethodEvent(QInputMethodEvent("한", []))
        QTest.keyClick(widget, Qt.Key_Return)
        self.assertEqual(len(sent), 1)

    def test_settings_save_key_in_memory_only_when_not_remembered(self):
        dialog = Gemeow.SettingsDialog(self.c)
        dialog.key.setText("dummy-private-value")
        dialog.remember.setChecked(False)
        dialog.context.setValue(7)
        self.c.apply_settings(dialog)
        self.assertEqual(self.c.api_key, "dummy-private-value")
        self.assertEqual(self.c.store.get_settings()["context_turns"], 7)
        for file in Path(self.temp.name).glob("*"):
            if file.is_file():
                self.assertNotIn(b"dummy-private-value", file.read_bytes())

    def test_db_write_failure_keeps_answer_and_recovery_export_allows_exit(self):
        cid = self.c.current_conversation
        with patch.object(self.c.store, "append_turn", side_effect=OSError("disk error")):
            self.send("저장 실패 질문")
        self.assertEqual(self.c.unsaved[cid][0]["answer"], "안녕이다냥!")
        destination = Path(self.temp.name) / "recovery.txt"
        with patch.object(self.c.store, "export_conversation", side_effect=OSError("db unreadable")):
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
                self.c.export_history()
        self.assertIn("저장 실패 질문", destination.read_text(encoding="utf-8-sig"))
        self.assertEqual(self.c.exported_unsaved[cid], self.c.unsaved[cid])
        with patch.object(self.c, "_stop_thread") as stop:
            self.c.quit()
            stop.assert_called_once()
        self.c.quitting = False

    def test_unreadable_new_history_is_not_sent(self):
        other = self.c.store.create_conversation("아직 열지 않은 대화")
        previous = self.c.current_conversation
        self.assertNotIn(other, self.c.turn_cache)
        with patch.object(self.c.store, "get_turns", side_effect=OSError("unreadable")):
            self.c.select_conversation(other)
            self.assertEqual(self.c.current_conversation, previous)
            self.c.current_conversation = other
            self.c.send_question("이전 기록이 필요해")
            self.assertFalse(self.c.busy)
            self.client.chats.create.assert_not_called()
            self.c.chat.render()
        self.assertIn("기록을 읽지 못", self.c.chat.status.text())

    def test_session_list_read_failure_keeps_combo_usable(self):
        before = self.c.chat.conversations.count()
        with patch.object(self.c.store, "list_conversations", side_effect=OSError("unreadable")):
            self.c.chat.refresh_sessions()
        self.assertEqual(self.c.chat.conversations.count(), before)
        self.assertFalse(self.c.chat.conversations.signalsBlocked())

    def test_character_follows_input_thinking_happy_and_idle(self):
        self.assertEqual(self.c.pet.animator.state, "idle")
        def delayed(_):
            time.sleep(.12)
            yield from normal_stream()
        self.chat_api.send_message_stream.side_effect = delayed
        with patch.object(self.c.chat, "isVisible", return_value=True), patch.object(self.c.chat.input, "hasFocus", return_value=True):
            self.c.chat.input.setPlainText("상태를 바꿔 봐")
            QTest.qWait(20)
            self.assertEqual(self.c.pet.animator.state, "listening")
            self.c.send_input()
            self.assertEqual(self.c.pet.animator.state, "thinking")
            self.wait_until(lambda: not self.c.busy)
            self.assertEqual(self.c.pet.animator.state, "happy")
            self.c.happy_timer.stop()
            self.c.update_character_state()
            self.assertEqual(self.c.pet.animator.state, "idle")

    def test_character_does_not_smile_on_failed_response(self):
        error = RuntimeError("provider details")
        error.code = 403
        self.chat_api.send_message_stream.side_effect = error
        self.send("실패할 질문")
        self.assertFalse(self.c.happy_timer.isActive())
        self.assertNotEqual(self.c.pet.animator.state, "happy")

    def test_listening_mirrors_toward_question_window(self):
        from PyQt5.QtCore import QRect
        with patch.object(self.c.chat, "isVisible", return_value=True), patch.object(self.c.chat.input, "hasFocus", return_value=True), patch.object(self.c.pet, "frameGeometry", return_value=QRect(500, 500, 220, 220)):
            self.c.chat.input.setPlainText("여기를 봐")
            with patch.object(self.c.chat, "frameGeometry", return_value=QRect(0, 0, 400, 400)):
                self.c.update_character_state()
                self.assertEqual(self.c.pet.animator.direction, "left")
            with patch.object(self.c.chat, "frameGeometry", return_value=QRect(800, 0, 400, 400)):
                self.c.update_character_state()
                self.assertEqual(self.c.pet.animator.direction, "right")

    def test_four_motions_can_be_previewed_without_network(self):
        with patch.object(self.c, "show_pet"):
            for state in ("idle", "listening", "thinking", "happy"):
                self.c.preview_character(state)
                self.assertEqual(self.c.pet.animator.state, state)
                self.assertTrue(self.c.preview_timer.isActive())
        self.client.chats.create.assert_not_called()
        self.c.preview_timer.stop()

    def test_theme_and_motion_changes_apply_immediately(self):
        dialog = Gemeow.SettingsDialog(self.c)
        dialog.theme.setCurrentIndex(dialog.theme.findData("night"))
        dialog.animation.setChecked(False)
        dialog.size.setValue(260)
        self.c.apply_settings(dialog)
        self.assertEqual(self.c.settings["theme"], "night")
        self.assertFalse(self.c.pet.animator.motion_enabled)
        self.assertEqual(self.c.pet.width(), 260)
        self.assertIn(Gemeow.palette("night")["background"], APP.styleSheet())

    def test_invalid_custom_image_keeps_existing_settings(self):
        dialog = Gemeow.SettingsDialog(self.c)
        dialog.asset_fields["idle"].setText(str(Path(self.temp.name) / "missing.png"))
        dialog.theme.setCurrentIndex(dialog.theme.findData("night"))
        self.c.apply_settings(dialog)
        self.assertEqual(self.c.store.get_settings()["theme"], "cream")
        self.assertEqual(self.c.store.get_settings()["character_assets"], {})
        self.assertIn("PNG", dialog.result_label.text())

    def test_real_sprite_asset_is_transparent_and_four_states_differ(self):
        animator = self.c.pet.animator
        samples = []
        for state in ("idle", "listening", "thinking", "happy"):
            animator.set_state(state)
            image = animator.current_pixmap().toImage()
            self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
            self.assertGreater(image.pixelColor(image.width()//2, image.height()//2).alpha(), 200)
            samples.append(image)
        self.assertTrue(all(samples[i] != samples[j] for i in range(4) for j in range(i+1, 4)))

    def test_show_chat_restores_minimized_window(self):
        with patch.object(self.c.chat, "isMinimized", return_value=True), patch.object(self.c.chat, "showNormal") as restore:
            self.c.show_chat()
            restore.assert_called_once()
        self.assertTrue(self.c.chat.isVisible())
        QTest.qWait(100)

    def test_show_chat_repositions_existing_offscreen_window(self):
        from PyQt5.QtCore import QPoint
        self.c.chat.move(-30000, -30000)
        with patch.object(self.c.chat, "isVisible", return_value=True):
            self.c.show_chat()
        screens = APP.screens()
        center = self.c.chat.frameGeometry().center()
        self.assertTrue(any(screen.availableGeometry().contains(center) for screen in screens))
        QTest.qWait(100)

    def test_pet_menu_action_uses_same_window_recovery(self):
        self.c.chat.hide()
        menu = self.c.pet_menu()
        next(action for action in menu.actions() if action.text() == "대화하기").trigger()
        self.assertTrue(self.c.chat.isVisible())
        QTest.qWait(100)

if __name__ == "__main__":
    unittest.main(verbosity=2)
