"""제미곰 3 — 바탕화면 캐릭터와 이어 나누는 대화."""
from __future__ import annotations

import argparse
import html
import os
from pathlib import Path
import sys
import threading
import uuid
import traceback
import json
from datetime import datetime

if sys.platform == "win32":
    # This desktop application requires real Windows top-level windows.
    os.environ["QT_QPA_PLATFORM"] = "windows"

import PyQt5
from PyQt5 import sip
_qt_plugins = Path(PyQt5.__file__).parent / "Qt5" / "plugins" / "platforms"
if _qt_plugins.is_dir():
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(_qt_plugins)

from PyQt5.QtCore import QObject, QPoint, QLockFile, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QTextCursor, QPalette
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QShortcut, QSpinBox, QSystemTrayIcon, QTextBrowser,
    QVBoxLayout, QWidget, QTabWidget,
)
from gemeow_api import GeminiWorker
from gemeow_core import Store
from gemeow_secrets import SecretStore
from gemeow_character import CharacterAnimator
from gemeow_theme import palette, stylesheet

BASE_DIR = Path(__file__).resolve().parent
STYLE = stylesheet("cream")


def gui_diagnostic(event, **details):
    """Local GUI diagnostics; never store messages, API keys, or exception values."""
    try:
        folder = BASE_DIR / "data"
        folder.mkdir(exist_ok=True)
        with (folder / "gui_diagnostics.log").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"),
                                     "pid": os.getpid(), "event": event, **details}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def gui_exception_hook(exc_type, value, tb):
    gui_diagnostic("exception", error_type=exc_type.__name__,
                   frames=[{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                           for frame in traceback.extract_tb(tb)])


def visible_screen(point):
    return QApplication.screenAt(point) or QApplication.primaryScreen()


def place_within_screen(widget, point):
    rect = visible_screen(point).availableGeometry().adjusted(8, 8, -8, -8)
    widget.move(max(rect.left(), min(point.x(), rect.right() - widget.width() + 1)),
                max(rect.top(), min(point.y(), rect.bottom() - widget.height() + 1)))


def icon_for_pet():
    sheet = QPixmap(str(BASE_DIR / "assets" / "bear_sprites.png"))
    if not sheet.isNull():
        return QIcon(sheet.copy(0, 0, round(sheet.width()/4), round(sheet.height()/4)))
    pix = QPixmap()
    if pix.isNull():
        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#665BCC"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, 64, 64)
        painter.setPen(Qt.white)
        painter.setFont(QFont("Malgun Gothic", 24, QFont.Bold))
        painter.drawText(pix.rect(), Qt.AlignCenter, "곰")
        painter.end()
    return QIcon(pix)


class MessageInput(QPlainTextEdit):
    send_requested = pyqtSignal()
    activity_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.composing = False
        self.setPlaceholderText("오늘은 어떤 이야기를 나눌까요?\nEnter 전송 · Shift/Ctrl+Enter 줄바꿈")
        self.setMinimumHeight(85)
        self.setMaximumHeight(120)

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)
        self.activity_changed.emit()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.activity_changed.emit()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.activity_changed.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not self.composing:
            if not event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
                self.send_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class ChatWindow(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint |
                            Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setObjectName("chat")
        self.setWindowTitle("제미곰 · 대화")
        self.setWindowIcon(controller.icon)
        self.resize(600, 700)
        self.setMinimumSize(430, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        header = QHBoxLayout()
        avatar = QLabel()
        avatar.setPixmap(controller.icon.pixmap(56, 56))
        avatar.setFixedSize(62, 62)
        header.addWidget(avatar)
        titles = QVBoxLayout()
        title = QLabel("제미곰")
        title.setObjectName("heading")
        titles.addWidget(title)
        self.subtitle = QLabel("네 이야기에 귀 기울이고 있어요")
        self.subtitle.setObjectName("subtitle")
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        settings_button = QPushButton("설정")
        settings_button.clicked.connect(controller.open_settings)
        header.addWidget(settings_button)
        layout.addLayout(header)
        self.state_label = QLabel("편안하게 쉬는 중")
        self.state_label.setObjectName("stateBadge")
        self.state_label.setContentsMargins(10, 5, 10, 5)
        layout.addWidget(self.state_label)

        sessions = QHBoxLayout()
        self.conversations = QComboBox()
        self.conversations.setMinimumContentsLength(12)
        self.conversations.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.conversations.currentIndexChanged.connect(self._select_conversation)
        sessions.addWidget(self.conversations, 1)
        self.new_button = QPushButton("새 대화")
        self.new_button.clicked.connect(controller.new_conversation)
        sessions.addWidget(self.new_button)
        self.more_button = QPushButton("기록 ▾")
        more = QMenu(self)
        more.addAction("기존 JSON 기록 가져오기…", controller.import_history)
        more.addAction("현재 대화 내보내기…", controller.export_history)
        more.addSeparator()
        more.addAction("현재 대화 삭제…", controller.delete_conversation)
        self.more_button.setMenu(more)
        sessions.addWidget(self.more_button)
        layout.addLayout(sessions)

        find_row = QHBoxLayout()
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("현재 대화에서 찾기 (Enter: 다음 결과)")
        self.find_edit.returnPressed.connect(self.find_next)
        find_row.addWidget(self.find_edit)
        copy_button = QPushButton("답변 복사")
        copy_button.clicked.connect(controller.copy_answer)
        find_row.addWidget(copy_button)
        layout.addLayout(find_row)
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        layout.addWidget(self.transcript, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.input = MessageInput()
        self.input.send_requested.connect(controller.send_input)
        self.input.textChanged.connect(controller.on_input_activity)
        self.input.activity_changed.connect(controller.on_input_activity)
        layout.addWidget(self.input)
        footer = QHBoxLayout()
        hint = QLabel("Esc 창 접기 · 캐릭터 더블클릭으로 다시 열기")
        hint.setObjectName("subtitle")
        footer.addWidget(hint, 1)
        self.retry_button = QPushButton("다시 보내기")
        self.retry_button.clicked.connect(controller.retry_question)
        self.retry_button.hide()
        footer.addWidget(self.retry_button)
        self.cancel_button = QPushButton("응답 취소")
        self.cancel_button.clicked.connect(controller.cancel_request)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        self.send_button = QPushButton("보내기")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(controller.send_input)
        footer.addWidget(self.send_button)
        layout.addLayout(footer)
        QShortcut(QKeySequence("Escape"), self, activated=self.hide)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.find_edit.setFocus)

    def _select_conversation(self, index):
        value = self.conversations.itemData(index)
        if value:
            self.controller.select_conversation(value)

    def refresh_sessions(self):
        self.conversations.blockSignals(True)
        self.conversations.clear()
        current_index = 0
        for item in self.controller.get_sessions():
            self.conversations.addItem(item["title"], item["id"])
            if item["id"] == self.controller.current_conversation:
                current_index = self.conversations.count() - 1
        self.conversations.setCurrentIndex(current_index)
        self.conversations.blockSignals(False)

    def set_status(self, text, error=False):
        self.status_error = error
        self.status.setText(text)
        colors = palette(self.controller.settings["theme"])
        self.status.setStyleSheet("color: " + colors["error" if error else "muted"] + ";")

    def set_busy(self, busy):
        for widget in (self.send_button, self.new_button, self.conversations, self.more_button):
            widget.setEnabled(not busy)
        self.retry_button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)

    def render(self):
        scrollbar = self.transcript.verticalScrollBar()
        position = scrollbar.value()
        follow = scrollbar.maximum() - position < 30
        try:
            turns = self.controller.current_turns()
        except Exception:
            self.set_status("대화 기록을 읽지 못했어요. data 폴더를 확인해 주세요.", True)
            return
        colors = palette(self.controller.settings["theme"])
        pieces = [f"<html><body style='font-family:Malgun Gothic; color:{colors['text']};'>"]
        if len(turns) > 300:
            pieces.append(f"<p style='color:{colors['muted']}'>최근 300개 문답을 표시합니다. 전체 내용은 내보내기로 저장할 수 있어요.</p>")
        for turn in turns[-300:]:
            pieces.append(self._message("나", turn["question"], colors["user_bubble"]))
            pieces.append(self._message("제미곰", turn["answer"], colors["assistant_bubble"]))
        active = self.controller.active
        if active and active["kind"] == "chat" and not active.get("completed"):
            pieces.append(self._message("나", active["question"], colors["user_bubble"]))
            pieces.append(self._message("제미곰", self.controller.stream_text or "생각 중이에요…", colors["assistant_bubble"]))
        elif not turns:
            pieces.append(f"<p style='color:{colors['muted']}; margin:20px'>안녕! 나는 제미곰이야.<br><br>오늘 있었던 일이나 궁금한 것을 들려줘.<br>이 대화는 다음에 켜도 이어갈 수 있어.</p>")
        pieces.append("</body></html>")
        self.transcript.setHtml("".join(pieces))
        scrollbar.setValue(scrollbar.maximum() if follow else position)

    def _message(self, who, text, color):
        safe = html.escape(text).replace("\n", "<br>")
        muted = palette(self.controller.settings["theme"])["muted"]
        return (f"<p style='margin-top:12px; margin-bottom:4px; color:{muted}'><b>{who}</b></p>"
                f"<table width='100%' cellspacing='0' cellpadding='10' bgcolor='{color}'><tr><td>{safe}</td></tr></table>")

    def find_next(self):
        text = self.find_edit.text().strip()
        if text and not self.transcript.find(text):
            self.transcript.moveCursor(QTextCursor.Start)
            if not self.transcript.find(text):
                self.set_status("찾는 내용이 현재 표시된 대화에 없어요.")

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.controller.update_character_state)

    def hideEvent(self, event):
        super().hideEvent(event)
        QTimer.singleShot(0, self.controller.update_character_state)

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self.controller, "pet"):
            self.controller.update_character_state()

    def closeEvent(self, event):
        self.hide()
        event.ignore()


class SettingsDialog(QDialog):
    STATE_NAMES = {"idle": "평소", "listening": "질문 입력", "thinking": "생각 중", "happy": "답변 완료"}

    def __init__(self, controller):
        super().__init__(controller.chat)
        self.controller = controller
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowSystemMenuHint | Qt.WindowCloseButtonHint)
        self.setWindowTitle("제미곰 설정")
        self.resize(580, 670)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        title = QLabel("내 곁의 제미곰")
        title.setObjectName("heading")
        layout.addWidget(title)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        conversation_tab, appearance_tab = QWidget(), QWidget()
        conversation_tab.setObjectName("settingsPage")
        appearance_tab.setObjectName("settingsPage")
        tabs.addTab(conversation_tab, "대화와 API")
        tabs.addTab(appearance_tab, "배경과 캐릭터")
        conversation_layout = QVBoxLayout(conversation_tab)
        form = QFormLayout()
        self.key = QLineEdit(controller.api_key)
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Gemini API 키를 여기에 입력하세요")
        show_key = QCheckBox("표시")
        show_key.toggled.connect(lambda checked: self.key.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(show_key)
        form.addRow("API 키", key_row)
        self.remember = QCheckBox("이 Windows 계정에 암호화하여 저장")
        self.remember.setChecked(bool(controller.settings["remember_key"]))
        form.addRow("", self.remember)
        self.model = QLineEdit(controller.settings["model"])
        form.addRow("모델", self.model)
        self.persona = QPlainTextEdit(controller.settings["persona"])
        self.persona.setMinimumHeight(130)
        form.addRow("말투와 성격", self.persona)
        self.context = QSpinBox()
        self.context.setRange(1, 100)
        self.context.setValue(controller.settings["context_turns"])
        self.context.setSuffix(" 회의 문답")
        form.addRow("기억할 최근 대화", self.context)
        conversation_layout.addLayout(form)
        self.probe_button = QPushButton("연결 확인")
        self.probe_button.clicked.connect(lambda: controller.start_probe(self))
        conversation_layout.addWidget(self.probe_button)
        note = QLabel("키는 이 창에만 입력해 주세요. 대화 기록은 data 폴더에 저장돼요.\n최근 대화는 답변을 위해 Gemini API에 함께 전달됩니다.")
        note.setWordWrap(True)
        note.setObjectName("subtitle")
        conversation_layout.addWidget(note)
        conversation_layout.addStretch()

        appearance_layout = QVBoxLayout(appearance_tab)
        appearance = QFormLayout()
        self.theme = QComboBox()
        self.theme.addItem("따뜻한 크림", "cream")
        self.theme.addItem("차분한 밤", "night")
        self.theme.setCurrentIndex(max(0, self.theme.findData(controller.settings["theme"])))
        appearance.addRow("대화창 배경", self.theme)
        self.size = QSpinBox()
        self.size.setRange(100, 360)
        self.size.setValue(controller.settings["pet_size"])
        self.size.setSuffix(" px")
        appearance.addRow("캐릭터 크기", self.size)
        self.animation = QCheckBox("상태별 모션 재생")
        self.animation.setChecked(controller.settings["animation_enabled"])
        appearance.addRow("", self.animation)
        self.on_top = QCheckBox("캐릭터와 대화창을 항상 위에 표시")
        self.on_top.setChecked(controller.settings["always_on_top"])
        appearance.addRow("", self.on_top)
        appearance_layout.addLayout(appearance)
        preview_row = QHBoxLayout()
        for state, label in self.STATE_NAMES.items():
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, chosen=state: controller.preview_character(chosen))
            preview_row.addWidget(button)
        appearance_layout.addWidget(QLabel("모션 미리보기 · 버튼을 누르면 5초간 보여요"))
        appearance_layout.addLayout(preview_row)
        appearance_layout.addWidget(QLabel("내 이미지로 바꾸기 (선택 사항)"))
        self.asset_fields, self.asset_buttons = {}, []
        custom = QFormLayout()
        for state, label in self.STATE_NAMES.items():
            row = QHBoxLayout()
            field = QLineEdit(controller.settings["character_assets"].get(state, ""))
            field.setReadOnly(True)
            field.setPlaceholderText("기본 캐릭터 사용")
            self.asset_fields[state] = field
            choose = QPushButton("선택")
            choose.clicked.connect(lambda checked=False, chosen=state: self.choose_asset(chosen))
            clear = QPushButton("기본")
            clear.clicked.connect(field.clear)
            self.asset_buttons.extend([choose, clear])
            row.addWidget(field, 1)
            row.addWidget(choose)
            row.addWidget(clear)
            custom.addRow(label, row)
        appearance_layout.addLayout(custom)
        detail = QLabel("기본 캐릭터는 배경이 투명해요. PNG는 정지 그림, GIF는 움직이는 그림으로 표시합니다.\n사용자 이미지 파일을 옮기면 설정에서 다시 선택해 주세요.")
        detail.setObjectName("subtitle")
        detail.setWordWrap(True)
        appearance_layout.addWidget(detail)
        appearance_layout.addStretch()
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        buttons = QHBoxLayout()
        close_button = QPushButton("닫기")
        close_button.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(close_button)
        self.save_button = QPushButton("저장")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(lambda: controller.apply_settings(self))
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.set_busy(controller.busy)

    def choose_asset(self, state):
        filename, _ = QFileDialog.getOpenFileName(self, self.STATE_NAMES[state] + " 이미지 선택", "", "캐릭터 이미지 (*.png *.gif)")
        if filename:
            self.asset_fields[state].setText(filename)

    def values(self):
        return {
            "model": self.model.text().strip(), "persona": self.persona.toPlainText().strip(),
            "context_turns": self.context.value(), "pet_size": self.size.value(),
            "always_on_top": self.on_top.isChecked(), "remember_key": self.remember.isChecked(),
            "theme": self.theme.currentData(), "animation_enabled": self.animation.isChecked(),
            "character_assets": {state: field.text() for state, field in self.asset_fields.items() if field.text()},
        }

    def set_busy(self, busy):
        for widget in (self.probe_button, self.save_button, self.key, self.model, self.persona, self.context,
                       self.size, self.on_top, self.theme, self.animation, *self.asset_buttons):
            widget.setEnabled(not busy)
        self.remember.setEnabled(not busy and self.controller.secrets.available)


class DesktopPet(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("제미곰")
        self.setWindowIcon(controller.icon)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.label = QLabel(self)
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.label.setAlignment(Qt.AlignCenter)
        self.setToolTip("더블클릭해서 대화 · 드래그로 이동 · 오른쪽 클릭 메뉴")
        self.animator = CharacterAnimator(BASE_DIR / "assets", self)
        self.animator.frame_changed.connect(self.on_frame)
        self.old_pos = None
        self.apply_settings()

    def on_frame(self, pixmap):
        self.label.setPixmap(pixmap)

    def apply_settings(self):
        was_visible = self.isVisible()
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.controller.settings["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        size = self.controller.settings["pet_size"]
        self.resize(size, size)
        self.label.resize(size, size)
        self.animator.set_size(size)
        self.animator.set_motion_enabled(self.controller.settings["animation_enabled"])
        try:
            self.animator.set_custom_assets(self.controller.settings["character_assets"])
        except ValueError:
            self.animator.set_custom_assets({})
            message = "이전에 선택한 캐릭터 이미지를 열지 못해 기본 캐릭터를 표시합니다. 설정에서 이미지를 다시 선택해 주세요."
            if message not in self.controller.startup_messages:
                self.controller.startup_messages.append(message)
        self.on_frame(self.animator.current_pixmap())
        place_within_screen(self, self.pos())
        if was_visible:
            self.show()

    def showEvent(self, event):
        super().showEvent(event)
        self.animator.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.animator.stop()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.old_pos = event.globalPos()

    def mouseMoveEvent(self, event):
        if self.old_pos is not None and event.buttons() & Qt.LeftButton:
            delta = event.globalPos() - self.old_pos
            place_within_screen(self, self.pos() + delta)
            self.old_pos = event.globalPos()
            self.controller.update_character_state()

    def mouseReleaseEvent(self, event):
        self.old_pos = None
        self.controller.remember_position()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.controller.show_chat()

    def contextMenuEvent(self, event):
        self.controller.pet_menu().exec_(event.globalPos())

    def closeEvent(self, event):
        event.ignore()
        self.controller.quit()


class AppController(QObject):
    job_requested = pyqtSignal(object)
    close_worker = pyqtSignal()

    def __init__(self, data_dir):
        super().__init__()
        self.data_dir = data_dir
        self.store = Store(data_dir, legacy_path=BASE_DIR / "chat_history.json")
        self.settings = self.store.get_settings()
        self.secrets = SecretStore(data_dir)
        self.api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.startup_messages = list(self.store.startup_warnings)
        if self.settings["remember_key"]:
            try:
                self.api_key = self.secrets.load() or self.api_key
            except RuntimeError as exc:
                self.startup_messages.append(str(exc))
        self.icon = icon_for_pet()
        self.active = None
        self.stream_text = ""
        self.last_failed = None
        self.unsaved = {}
        self.exported_unsaved = {}
        self.drafts = {}
        self.turn_cache = {}
        self.quitting = False
        self.settings_dialog = None
        self.happy_timer = QTimer(self)
        self.happy_timer.setSingleShot(True)
        self.happy_timer.setInterval(3500)
        self.happy_timer.timeout.connect(self.update_character_state)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(5000)
        self.preview_timer.timeout.connect(self.update_character_state)
        self.preview_state = None
        sessions = self.store.list_conversations()
        self.session_cache = sessions
        previous = self.settings.get("last_conversation_id")
        self.current_conversation = next((s["id"] for s in sessions if s["id"] == previous), None)
        if not self.current_conversation:
            self.current_conversation = sessions[0]["id"] if sessions else self.store.create_conversation()
        self.chat = ChatWindow(self)
        self.pet = DesktopPet(self)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(65)
        self.render_timer.timeout.connect(self.chat.render)
        self.thread = QThread(self)
        self.worker = GeminiWorker()
        self.worker.moveToThread(self.thread)
        self.job_requested.connect(self.worker.request, Qt.QueuedConnection)
        self.close_worker.connect(self.worker.close, Qt.QueuedConnection)
        self.worker.closed.connect(self.thread.quit)
        self.worker.chunk.connect(self.on_chunk)
        self.worker.succeeded.connect(self.on_success)
        self.worker.failed.connect(self.on_failure)
        self.worker.cancelled.connect(self.on_cancelled)
        self.worker.status.connect(self.on_status)
        self.worker.finished.connect(self.on_finished)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._exit_when_ready)
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("제미곰 · 더블클릭해서 대화")
        self.tray.setContextMenu(self.pet_menu())
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.apply_window_settings()
        self.restore_position()
        self.chat.refresh_sessions()
        self.chat.render()
        self.update_character_state()
        self.thread.start()

    @property
    def busy(self):
        return self.active is not None

    def start(self):
        self.pet.show()
        if not self.api_key or self.startup_messages:
            self.show_chat()
        if self.startup_messages:
            self.chat.set_status("\n".join(self.startup_messages), True)
        if not self.api_key:
            QTimer.singleShot(100, self.open_settings)

    def get_sessions(self):
        try:
            self.session_cache = self.store.list_conversations()
        except Exception:
            self.chat.set_status("대화 목록을 읽지 못했어요. 마지막으로 읽은 목록을 표시합니다.", True)
        return self.session_cache

    def current_turns(self):
        if self.current_conversation not in self.turn_cache:
            self.turn_cache[self.current_conversation] = self.store.get_turns(self.current_conversation)
        return self.turn_cache[self.current_conversation] + self.unsaved.get(self.current_conversation, [])

    def apply_window_settings(self):
        QApplication.instance().setStyleSheet(stylesheet(self.settings["theme"]))
        colors = palette(self.settings["theme"])
        app_palette = QApplication.instance().palette()
        app_palette.setColor(QPalette.PlaceholderText, QColor(colors["muted"]))
        QApplication.instance().setPalette(app_palette)
        was_visible = self.chat.isVisible()
        self.chat.setWindowFlag(Qt.WindowStaysOnTopHint, bool(self.settings["always_on_top"]))
        if was_visible:
            self.chat.show()
        self.pet.apply_settings()
        self.chat.subtitle.setText(f"최근 {self.settings['context_turns']}회 문답을 기억하며 듣고 있어요")
        self.chat.render()
        self.chat.set_status(self.chat.status.text(), getattr(self.chat, "status_error", False))
        self.update_character_state()

    def on_input_activity(self):
        if not self._ui_alive():
            return
        if self.chat.input.hasFocus() and (self.chat.input.toPlainText().strip() or self.chat.input.composing):
            self.happy_timer.stop()
            self.preview_timer.stop()
        QTimer.singleShot(0, self.update_character_state)

    def update_character_state(self):
        if self.quitting or not self._ui_alive():
            return
        if self.active and self.active["kind"] == "chat":
            state = "thinking"
        elif self.preview_timer.isActive() and self.preview_state:
            state = self.preview_state
        elif self.chat.isVisible() and self.chat.input.hasFocus() and (
                self.chat.input.toPlainText().strip() or self.chat.input.composing):
            state = "listening"
        elif self.happy_timer.isActive():
            state = "happy"
        else:
            state = "idle"
        direction = "left" if self.chat.frameGeometry().center().x() <= self.pet.frameGeometry().center().x() else "right"
        self.pet.animator.set_direction(direction)
        self.pet.animator.set_state(state)
        self.chat.state_label.setText({"idle": "편안하게 쉬는 중", "listening": "질문창을 바라보고 있어요",
                                       "thinking": "턱을 괴고 생각하는 중…", "happy": "답변을 마치고 활짝 웃는 중"}[state])

    def _ui_alive(self):
        return (hasattr(self, "chat") and hasattr(self, "pet") and
                not sip.isdeleted(self.chat) and not sip.isdeleted(self.pet))

    def preview_character(self, state):
        if self.busy:
            return
        self.preview_state = state
        self.preview_timer.start()
        self.show_pet()
        self.update_character_state()

    def restore_position(self):
        x, y = self.settings.get("pet_x"), self.settings.get("pet_y")
        if isinstance(x, int) and isinstance(y, int):
            target = QPoint(x, y)
        else:
            rect = QApplication.primaryScreen().availableGeometry()
            target = QPoint(rect.right() - self.pet.width() - 40, rect.bottom() - self.pet.height() - 20)
        place_within_screen(self.pet, target)

    def remember_position(self):
        try:
            self.store.save_settings({"pet_x": self.pet.x(), "pet_y": self.pet.y(),
                                      "last_conversation_id": self.current_conversation})
        except Exception:
            self.chat.set_status("위치를 저장하지 못했어요. data 폴더의 쓰기 권한과 여유 공간을 확인해 주세요.", True)

    def show_chat(self):
        self._log_chat_state("show_chat_requested")
        # show() alone leaves a minimized QWidget minimized. Restore it before
        # positioning, including when Windows still reports it as visible.
        if self.chat.isMinimized():
            self.chat.showNormal()
        point = QPoint(self.pet.x() + (self.pet.width() - self.chat.width()) // 2,
                       self.pet.y() - self.chat.height() - 10)
        place_within_screen(self.chat, point)
        self._activate_chat()
        # A QMenu may still own the foreground when its action runs. Repeat
        # after the menu closes, using Qt rather than forcing OS foreground.
        QTimer.singleShot(80, self._activate_chat)

    def _activate_chat(self):
        if self.quitting or not self._ui_alive():
            return
        self.chat.setAttribute(Qt.WA_DontShowOnScreen, False)
        self.chat.setWindowOpacity(1.0)
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()
        handle = self.chat.windowHandle()
        if handle is not None:
            handle.requestActivate()
        self.chat.input.setFocus()
        self._log_chat_state("show_chat_done")

    def _log_chat_state(self, event):
        if not self._ui_alive():
            return
        rect = self.chat.frameGeometry()
        gui_diagnostic(event, visible=self.chat.isVisible(), hidden=self.chat.isHidden(),
                       minimized=self.chat.isMinimized(), active=self.chat.isActiveWindow(),
                       geometry=[rect.x(), rect.y(), rect.width(), rect.height()],
                       opacity=self.chat.windowOpacity(), flags=int(self.chat.windowFlags()),
                       backend=QApplication.platformName(),
                       exposed=bool(self.chat.windowHandle() and self.chat.windowHandle().isExposed()),
                       dont_show=self.chat.testAttribute(Qt.WA_DontShowOnScreen))


    def show_pet(self):
        self.restore_position()
        self.pet.show()
        self.pet.raise_()

    def pet_menu(self):
        menu = QMenu()
        menu.addAction("대화하기", self.show_chat)
        menu.addAction("설정", self.open_settings)
        menu.addSeparator()
        if QSystemTrayIcon.isSystemTrayAvailable():
            menu.addAction("캐릭터 숨기기", self.pet.hide)
        menu.addAction("캐릭터 불러오기", self.show_pet)
        menu.addSeparator()
        menu.addAction("제미곰 재우기 (종료)", self.quit)
        return menu

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_pet()
            self.show_chat()

    def select_conversation(self, conversation_id):
        if self.busy or conversation_id == self.current_conversation:
            return
        if conversation_id not in self.turn_cache:
            try:
                self.turn_cache[conversation_id] = self.store.get_turns(conversation_id)
            except Exception:
                self.chat.set_status("선택한 대화 기록을 읽지 못했어요. 현재 대화를 유지합니다.", True)
                self.chat.refresh_sessions()
                return
        self.drafts[self.current_conversation] = self.chat.input.toPlainText()
        self.current_conversation = conversation_id
        self.chat.input.setPlainText(self.drafts.get(conversation_id, ""))
        self.last_failed = None
        self.chat.retry_button.hide()
        self.chat.set_status("")
        self.chat.find_edit.clear()
        self.chat.render()
        self.remember_position()

    def new_conversation(self):
        if self.busy:
            return
        self.drafts[self.current_conversation] = self.chat.input.toPlainText()
        try:
            self.current_conversation = self.store.create_conversation()
            self.chat.input.clear()
        except Exception:
            self.chat.set_status("새 대화를 만들지 못했어요. data 폴더를 확인해 주세요.", True)
            return
        self.chat.refresh_sessions()
        self.last_failed = None
        self.chat.retry_button.hide()
        self.chat.find_edit.clear()
        self.chat.set_status("새로운 이야기로 시작해요.")
        self.chat.render()
        self.remember_position()

    def open_settings(self):
        self.show_chat()
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self)
        elif not self.settings_dialog.isVisible():
            self.settings_dialog.deleteLater()
            self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def apply_settings(self, dialog):
        if self.busy:
            return
        values = dialog.values()
        key = dialog.key.text().strip()
        if not values["model"] or not values["persona"]:
            dialog.result_label.setText("모델 이름과 말투를 입력해 주세요.")
            return
        validator = CharacterAnimator(BASE_DIR / "assets", dialog)
        try:
            validator.set_custom_assets(values["character_assets"])
        except ValueError as exc:
            dialog.result_label.setText(str(exc))
            return
        finally:
            validator.stop()
            validator.deleteLater()
        try:
            if values["remember_key"]:
                if not key:
                    dialog.result_label.setText("저장할 API 키를 입력해 주세요.")
                    return
                self.secrets.save(key)
            else:
                self.secrets.clear()
            self.store.save_settings(values)
            self.settings = self.store.get_settings()
            self.api_key = key
        except Exception:
            dialog.result_label.setText("설정을 저장하지 못했어요. data 폴더의 쓰기 권한과 Windows 암호화 기능을 확인해 주세요.")
            return
        self.apply_window_settings()
        dialog.accept()
        self.chat.set_status("설정을 저장했어요. 바로 적용됩니다.")
        self.chat.input.setFocus()

    def send_input(self):
        question = self.chat.input.toPlainText().strip()
        if question:
            self.send_question(question, clear_input=True)

    def send_question(self, question, clear_input=False):
        if self.busy or self.quitting:
            return
        if not self.api_key:
            self.chat.set_status("설정에서 Gemini API 키를 먼저 입력해 주세요.", True)
            self.open_settings()
            return
        if len(question) > 20000:
            self.chat.set_status("한 번에 20,000자까지 보낼 수 있어요. 질문을 나눠 주세요.", True)
            return
        try:
            turns = self.current_turns()
        except Exception:
            self.chat.set_status("이전 대화를 읽지 못해 전송하지 않았어요. data 폴더를 확인해 주세요.", True)
            return
        job = {"id": uuid.uuid4().hex, "kind": "chat", "question": question,
               "conversation_id": self.current_conversation, "api_key": self.api_key,
               "turns": turns, "cancel_event": threading.Event(),
               **{k: self.settings[k] for k in ("model", "persona", "context_turns")}}
        self._begin_job(job)
        if clear_input:
            self.chat.input.clear()
        self.last_failed = None
        self.chat.retry_button.hide()
        self.chat.set_status("생각 중이에요…")
        self.chat.render()
        self.job_requested.emit(job)

    def start_probe(self, dialog):
        if self.busy:
            return
        key = dialog.key.text().strip()
        model = dialog.model.text().strip()
        if not key or not model:
            dialog.result_label.setText("API 키와 모델을 입력해 주세요.")
            return
        job = {"id": uuid.uuid4().hex, "kind": "probe", "api_key": key,
               "model": model, "cancel_event": threading.Event()}
        self._begin_job(job)
        dialog.result_label.setText("연결을 확인하고 있어요…")
        self.chat.set_status("API 연결 확인 중…")
        self.job_requested.emit(job)

    def _begin_job(self, job):
        self.happy_timer.stop()
        self.preview_timer.stop()
        self.active = job
        self.stream_text = ""
        self.chat.set_busy(True)
        if self.settings_dialog:
            self.settings_dialog.set_busy(True)
        self.update_character_state()

    def on_chunk(self, request_id, text):
        if self.active and self.active["id"] == request_id and not self.active["cancel_event"].is_set():
            self.stream_text += text
            if not self.render_timer.isActive():
                self.render_timer.start()

    def on_status(self, request_id, text):
        if self.active and self.active["id"] == request_id and not self.active["cancel_event"].is_set():
            self.chat.set_status(text)

    def _record_answer(self, question, answer):
        pending = self.unsaved.setdefault(self.current_conversation, [])
        pending.append({"question": question, "answer": answer, "created_at": ""})
        while pending:
            turn = pending[0]
            try:
                turn_id = self.store.append_turn(self.current_conversation, turn["question"], turn["answer"])
                self.turn_cache.setdefault(self.current_conversation, []).append({**turn, "id": turn_id})
            except Exception:
                return False
            pending.pop(0)
        return True

    def on_success(self, request_id, text):
        if not self.active or self.active["id"] != request_id:
            return
        if self.active["cancel_event"].is_set():
            self.on_cancelled(request_id)
            return
        if self.active["kind"] == "probe":
            if self.settings_dialog:
                self.settings_dialog.result_label.setText(text)
            self.chat.set_status(text)
            return
        stored = self._record_answer(self.active["question"], text)
        self.active["completed"] = True
        self.happy_timer.start()
        self.chat.refresh_sessions()
        self.chat.set_status("이어서 이야기해 주세요." if stored else
                             "답변은 받았지만 기록 저장에 실패했어요. 종료 전에 내보내기로 보관해 주세요.", not stored)
        if not self.chat.isVisible() and self.tray.isVisible():
            self.tray.showMessage("제미곰", "답변이 도착했어요!", QSystemTrayIcon.Information, 3500)

    def _keep_failed_question(self):
        if self.active and self.active["kind"] == "chat":
            self.last_failed = self.active["question"]
            if not self.chat.input.toPlainText().strip():
                self.chat.input.setPlainText(self.last_failed)
            self.chat.retry_button.show()

    def on_failure(self, request_id, message):
        if not self.active or self.active["id"] != request_id:
            return
        self._keep_failed_question()
        self.happy_timer.stop()
        self.chat.set_status(message, True)
        if self.active["kind"] == "probe" and self.settings_dialog:
            self.settings_dialog.result_label.setText(message)

    def on_cancelled(self, request_id):
        if self.active and self.active["id"] == request_id:
            self.happy_timer.stop()
            self._keep_failed_question()
            self.chat.set_status("응답을 취소했어요. 질문은 다시 보낼 수 있어요.")

    def on_finished(self, request_id):
        if self.active and self.active["id"] == request_id:
            self.active = None
            self.stream_text = ""
            self.render_timer.stop()
            self.chat.set_busy(False)
            if self.settings_dialog:
                self.settings_dialog.set_busy(False)
            self.chat.render()
            self.update_character_state()
        if self.quitting:
            self._stop_thread()

    def cancel_request(self):
        if self.active:
            self.active["cancel_event"].set()
            self.chat.cancel_button.setEnabled(False)
            self.chat.set_status("취소 중이에요. 네트워크 응답 대기가 끝나면 정리됩니다 (통신 대기 시간제한 30초).")

    def retry_question(self):
        if self.last_failed and not self.busy:
            question = self.last_failed
            self.send_question(question, clear_input=self.chat.input.toPlainText().strip() == question)

    def copy_answer(self):
        try:
            turns = self.current_turns()
        except Exception:
            self.chat.set_status("대화 기록을 읽지 못해 복사하지 못했어요.", True)
            return
        if turns:
            QApplication.clipboard().setText(turns[-1]["answer"])
            self.chat.set_status("마지막 답변을 복사했어요.")



    def import_history(self):
        if self.busy:
            return
        filename, _ = QFileDialog.getOpenFileName(self.chat, "기존 제미곰 기록 가져오기", "", "JSON (*.json)")
        if not filename:
            return
        try:
            count = self.store.import_legacy(Path(filename))
        except ValueError as exc:
            QMessageBox.information(self.chat, "기록 가져오기", str(exc))
            return
        except Exception:
            QMessageBox.warning(self.chat, "기록 가져오기", "기록을 가져오지 못했어요. 파일과 data 폴더의 권한을 확인해 주세요.")
            return
        self.drafts[self.current_conversation] = self.chat.input.toPlainText()
        sessions = self.get_sessions()
        if sessions:
            self.current_conversation = sessions[0]["id"]
        self.last_failed = None
        self.chat.retry_button.hide()
        self.chat.find_edit.clear()
        self.chat.input.setPlainText(self.drafts.get(self.current_conversation, ""))
        self.chat.refresh_sessions()
        self.chat.render()
        self.chat.set_status(f"{count}개의 문답을 가져왔어요. 원본 기록은 보존돼요.")
        self.remember_position()

    def export_history(self):
        if self.busy:
            return
        filename, _ = QFileDialog.getSaveFileName(self.chat, "대화 내보내기", "제미곰_대화.txt", "텍스트 (*.txt)")
        if not filename:
            return
        try:
            try:
                text = self.store.export_conversation(self.current_conversation)
            except Exception:
                if self.current_conversation not in self.turn_cache:
                    raise
                lines = ["제미곰 대화 기록 (메모리에서 복구)", ""]
                for turn in self.turn_cache[self.current_conversation]:
                    lines.extend([f"나: {turn['question']}", f"제미곰: {turn['answer']}", ""])
                text = "\n".join(lines)
            for turn in self.unsaved.get(self.current_conversation, []):
                text += f"\n\n나: {turn['question']}\n제미곰: {turn['answer']}\n"
            Path(filename).write_text(text, encoding="utf-8-sig")
            self.exported_unsaved[self.current_conversation] = list(self.unsaved.get(self.current_conversation, []))
            self.chat.set_status("대화를 파일로 저장했어요.")
        except Exception:
            QMessageBox.warning(self.chat, "내보내기", "파일을 저장하지 못했어요. 선택한 폴더의 권한과 여유 공간을 확인해 주세요.")

    def delete_conversation(self):
        if self.busy:
            return
        if QMessageBox.question(self.chat, "대화 삭제", "현재 대화를 삭제할까요? 삭제한 대화는 복원할 수 없어요.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.store.delete_conversation(self.current_conversation)
            self.unsaved.pop(self.current_conversation, None)
            self.exported_unsaved.pop(self.current_conversation, None)
            self.drafts.pop(self.current_conversation, None)
            self.turn_cache.pop(self.current_conversation, None)
            sessions = self.get_sessions()
            self.current_conversation = sessions[0]["id"] if sessions else self.store.create_conversation()
        except Exception:
            self.chat.set_status("대화를 삭제하지 못했어요. data 폴더를 확인해 주세요.", True)
            return
        self.chat.input.setPlainText(self.drafts.get(self.current_conversation, ""))
        self.last_failed = None
        self.chat.retry_button.hide()
        self.chat.find_edit.clear()
        self.chat.refresh_sessions()
        self.chat.render()
        self.chat.set_status("대화를 삭제했어요.")
        self.remember_position()

    def quit(self):
        if self.quitting:
            return
        unprotected = next((cid for cid, turns in self.unsaved.items()
                            if turns and turns != self.exported_unsaved.get(cid)), None)
        if unprotected:
            self.select_conversation(unprotected)
            self.chat.refresh_sessions()
            self.show_chat()
            QMessageBox.warning(self.chat, "저장되지 않은 대화", "저장되지 않은 답변이 있어요. 지금 표시된 대화를 기록 메뉴에서 내보낸 뒤 다시 종료해 주세요.")
            return
        self.remember_position()
        self.quitting = True
        self.happy_timer.stop()
        self.preview_timer.stop()
        if self.settings_dialog:
            self.settings_dialog.hide()
        self.pet.hide()
        self.chat.hide()
        if self.active:
            self.active["cancel_event"].set()
            if self.tray.isVisible():
                self.tray.showMessage("제미곰", "요청을 정리한 뒤 종료합니다. 네트워크 응답에 따라 잠시 걸릴 수 있어요.", QSystemTrayIcon.Information, 3000)
        else:
            self._stop_thread()

    def _stop_thread(self):
        self.close_worker.emit()

    def _exit_when_ready(self):
        if self.quitting:
            self.tray.hide()
            self.store.close()
            QApplication.quit()


def main():
    sys.excepthook = gui_exception_hook
    gui_diagnostic("startup", build="windows-visible-gui-startup")
    parser = argparse.ArgumentParser(description="제미곰 3")
    parser.add_argument("--data-dir", type=Path, default=BASE_DIR / "data", help="대화와 설정을 저장할 폴더")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Gemeow")
    app.setApplicationDisplayName("제미곰")
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Malgun Gothic", 10))
    try:
        args.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        QMessageBox.critical(None, "제미곰", "data 폴더를 만들 수 없어요. 압축을 풀고 쓰기 가능한 폴더에서 실행해 주세요.")
        return 1
    lock = QLockFile(str(args.data_dir / "gemeow.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "제미곰", "이 폴더의 제미곰가 이미 실행 중이에요. 트레이의 캐릭터 아이콘을 확인해 주세요.")
        return 0
    try:
        controller = AppController(args.data_dir)
    except Exception:
        QMessageBox.critical(None, "제미곰", "기록을 열지 못했어요. data 폴더의 권한과 저장 공간을 확인해 주세요. 기존 기록 파일은 보존됩니다.")
        return 1
    controller.start()
    result = app.exec_()
    lock.unlock()
    return result


if __name__ == "__main__":
    sys.exit(main())

