import os
import sys
import json
from datetime import datetime
import PyQt5

# 윈도우 바탕화면 창 에러 방지용 (반드시 맨 위에 위치)
pyqt_dir = os.path.dirname(PyQt5.__file__)
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(pyqt_dir, 'Qt5', 'plugins', 'platforms')

from PyQt5.QtWidgets import (
    QApplication, QLabel, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QFrame, QMenu, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QPixmap, QColor, QFont, QTextOption
from google import genai

# --------------------------------------------------
# API 키는 코드에 적지 않고 실행 환경에서 읽습니다.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# 키가 없어도 고양이 창은 열고, 질문을 보낼 때 설정을 안내합니다.
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
# --------------------------------------------------

# 현재 파이썬 파일이 있는 폴더의 절대 경로 구하기
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_PATH = os.path.join(BASE_DIR, 'cat.png')
HISTORY_FILE = os.path.join(BASE_DIR, 'chat_history.json')


# ==================================================
# 📁 대화 기록 로드 & 저장 함수
# ==================================================
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_history(history_data):
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("대화 기록 저장 실패:", e)


# ==================================================
# ✍️ 키 입력 감지 및 자동 줄바꿈 커스텀 입력창
# ==================================================
class CustomTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ControlModifier:
                self.insertPlainText("\n")
            else:
                dialog = self.window()
                if isinstance(dialog, CustomInputDialog):
                    dialog.accept_input()
        else:
            super().keyPressEvent(event)


# ==================================================
# 📜 대화 기록 보기 대화창
# ==================================================
class HistoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()

    def initUI(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(420, 380)

        frame = QFrame(self)
        frame.setGeometry(10, 10, 400, 360)
        frame.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border-radius: 16px;
                border: 2px solid #6366F1;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 4)
        frame.setGraphicsEffect(shadow)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        # 타이틀
        title = QLabel("📜 제미냥이와의 대화 기록", frame)
        title.setFont(QFont("Malgun Gothic", 11, QFont.Bold))
        title.setStyleSheet("color: #4F46E5; border: none;")
        layout.addWidget(title)

        # 기록 표시 영역
        content = QTextEdit(frame)
        content.setReadOnly(True)
        content.setFont(QFont("Malgun Gothic", 9))
        content.setLineWrapMode(QTextEdit.WidgetWidth)
        content.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        content.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content.setStyleSheet("""
            QTextEdit {
                background-color: #F9FAFB;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
                padding: 10px;
                color: #1F2937;
            }
        """)

        # 저장된 기록 불러와서 텍스트 생성
        history = load_history()
        if not history:
            content.setPlainText("아직 제미냥이와 나눈 대화가 없다냥!")
        else:
            formatted_text = []
            for item in history:
                time_str = item.get("time", "")
                q = item.get("question", "")
                a = item.get("answer", "")
                block = f"⏱ [{time_str}]\n❓ 나: {q}\n🐾 제미냥이: {a}\n" + "─"*28
                formatted_text.append(block)
            content.setPlainText("\n\n".join(formatted_text))

        layout.addWidget(content)

        # 닫기 버튼
        close_btn = QPushButton("닫기", frame)
        close_btn.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366F1;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 18px;
            }
            QPushButton:hover { background-color: #4F46E5; }
        """)
        close_btn.clicked.connect(self.accept)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)


# ==================================================
# 🎨 세련된 맞춤형 질문 입력 대화창
# ==================================================
class CustomInputDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.user_text = ""
        self.initUI()

    def initUI(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(380, 220)

        # 메인 카드 프레임
        frame = QFrame(self)
        frame.setGeometry(10, 10, 360, 200)
        frame.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border-radius: 16px;
                border: 2px solid #E5E7EB;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 4)
        frame.setGraphicsEffect(shadow)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        # 타이틀
        title = QLabel("🐾 제미냥이에게 물어보기", frame)
        title.setFont(QFont("Malgun Gothic", 11, QFont.Bold))
        title.setStyleSheet("color: #374151; border: none;")
        layout.addWidget(title)

        # 커스텀 입력 박스
        self.text_edit = CustomTextEdit(frame)
        self.text_edit.setPlaceholderText("궁금한 것을 물어보라냥...\n(Enter: 질문하기 / Ctrl+Enter: 줄바꿈)")
        self.text_edit.setFont(QFont("Malgun Gothic", 10))
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #F9FAFB;
                border: 1px solid #D1D5DB;
                border-radius: 10px;
                padding: 8px;
                color: #1F2937;
            }
            QTextEdit:focus {
                border: 1.5px solid #6366F1;
                background-color: #FFFFFF;
            }
        """)
        layout.addWidget(self.text_edit)

        # 버튼 영역
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        # 대화 기록 버튼
        history_btn = QPushButton("대화 기록", frame)
        history_btn.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
        history_btn.setCursor(Qt.PointingHandCursor)
        history_btn.setStyleSheet("""
            QPushButton {
                background-color: #EEF2FF;
                color: #4F46E5;
                border: 1px solid #C7D2FE;
                border-radius: 8px;
                padding: 6px 12px;
            }
            QPushButton:hover { background-color: #E0E7FF; }
        """)
        history_btn.clicked.connect(self.show_history)

        cancel_btn = QPushButton("취소", frame)
        cancel_btn.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #F3F4F6;
                color: #4B5563;
                border: none;
                border-radius: 8px;
                padding: 6px 14px;
            }
            QPushButton:hover { background-color: #E5E7EB; }
        """)
        cancel_btn.clicked.connect(self.reject)

        send_btn = QPushButton("질문하기", frame)
        send_btn.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
        send_btn.setCursor(Qt.PointingHandCursor)
        send_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366F1;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 16px;
            }
            QPushButton:hover { background-color: #4F46E5; }
        """)
        send_btn.clicked.connect(self.accept_input)

        btn_layout.addStretch()
        btn_layout.addWidget(history_btn)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(send_btn)
        layout.addLayout(btn_layout)

    def show_history(self):
        h_dialog = HistoryDialog(self)
        h_dialog.move(self.x() - 20, self.y() - 80)
        h_dialog.exec_()

    def accept_input(self):
        self.user_text = self.text_edit.toPlainText().strip()
        if self.user_text:
            self.accept()

    def get_text(self):
        return self.user_text


# ==================================================
# 🐾 세련된 맞춤형 대답 출력 대화창
# ==================================================
class CustomResponseDialog(QDialog):
    def __init__(self, title_text, response_text, is_error=False, parent=None):
        super().__init__(parent)
        self.title_text = title_text
        self.response_text = response_text
        self.is_error = is_error
        self.ask_again = False
        self.initUI()

    def initUI(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(400, 240)

        frame = QFrame(self)
        frame.setGeometry(10, 10, 380, 220)
        
        border_color = "#EF4444" if self.is_error else "#6366F1"
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: #FFFFFF;
                border-radius: 16px;
                border: 2px solid {border_color};
            }}
        """)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 4)
        frame.setGraphicsEffect(shadow)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)

        # 타이틀
        title = QLabel(self.title_text, frame)
        title.setFont(QFont("Malgun Gothic", 11, QFont.Bold))
        title_color = "#DC2626" if self.is_error else "#4F46E5"
        title.setStyleSheet(f"color: {title_color}; border: none;")
        layout.addWidget(title)

        # 내용 영역
        content = QTextEdit(frame)
        content.setPlainText(self.response_text)
        content.setReadOnly(True)
        content.setFont(QFont("Malgun Gothic", 10))
        content.setLineWrapMode(QTextEdit.WidgetWidth)
        content.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        content.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content.setStyleSheet("""
            QTextEdit {
                background-color: #F9FAFB;
                border: none;
                border-radius: 10px;
                padding: 10px;
                color: #1F2937;
            }
        """)
        layout.addWidget(content)

        # 버튼 영역
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        # 다시 질문하기 버튼
        reask_btn = QPushButton("다시 질문하기", frame)
        reask_btn.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
        reask_btn.setCursor(Qt.PointingHandCursor)
        reask_btn.setStyleSheet("""
            QPushButton {
                background-color: #EEF2FF;
                color: #4F46E5;
                border: 1px solid #C7D2FE;
                border-radius: 8px;
                padding: 6px 14px;
            }
            QPushButton:hover { background-color: #E0E7FF; }
        """)
        reask_btn.clicked.connect(self.on_reask)

        # 확인 버튼
        ok_btn = QPushButton("고마워!", frame)
        ok_btn.setFont(QFont("Malgun Gothic", 9, QFont.Bold))
        ok_btn.setCursor(Qt.PointingHandCursor)
        ok_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {border_color};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 18px;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
        """)
        ok_btn.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(reask_btn)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    def on_reask(self):
        self.ask_again = True
        self.accept()


# ==================================================
# 🐱 바탕화면 고양이 메인 클래스
# ==================================================
class DesktopPet(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint | 
            Qt.WindowStaysOnTopHint | 
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.label = QLabel(self)
        self.pixmap = QPixmap(IMAGE_PATH)
        
        if self.pixmap.isNull():
            print(f"\n[오류] 이미지를 찾을 수 없습니다: {IMAGE_PATH}\n")
            sys.exit()

        self.pixmap = self.pixmap.scaledToWidth(140, Qt.SmoothTransformation)
        self.label.setPixmap(self.pixmap)
        self.resize(self.pixmap.size())

        screen = QApplication.primaryScreen().geometry()
        self.screen_width = screen.width()
        self.screen_height = screen.height()

        self.move(self.screen_width // 2, self.screen_height - 220)

        self.old_pos = None
        self.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.old_pos = event.globalPos()
            self.raise_()

    def mouseMoveEvent(self, event):
        if self.old_pos is not None:
            delta = QPoint(event.globalPos() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPos()

    def mouseReleaseEvent(self, event):
        self.old_pos = None

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        quit_action = menu.addAction("제미냥이 재우기 (종료)")
        action = menu.exec_(self.mapToGlobal(event.pos()))
        if action == quit_action:
            QApplication.quit()

    def get_smart_position(self, dialog_w, dialog_h):
        pet_geo = self.geometry()
        pos_x = pet_geo.x() + (pet_geo.width() - dialog_w) // 2
        pos_y = pet_geo.y() - dialog_h - 10

        if pos_y < 10:
            pos_y = pet_geo.y() + pet_geo.height() + 10

        if pos_x < 10:
            pos_x = 10
        elif pos_x + dialog_w > self.screen_width - 10:
            pos_x = self.screen_width - dialog_w - 10

        return pos_x, pos_y

    def open_input_dialog(self):
        dialog = CustomInputDialog(self)
        x, y = self.get_smart_position(dialog.width(), dialog.height())
        dialog.move(x, y)

        if dialog.exec_() == QDialog.Accepted:
            question = dialog.get_text()
            if question:
                self.ask_gemini(question)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.open_input_dialog()

    def ask_gemini(self, question):
        try:
            if client is None:
                raise RuntimeError(
                    "GEMINI_API_KEY 환경변수를 설정한 뒤 프로그램을 다시 실행해 주세요."
                )
            chat = client.chats.create(model='gemini-3.8-flash')
            prompt = (
                f"너는 바탕화면에 가만히 앉아있는 귀여운 흰 고양이 캐릭터이고 이름은 '제미냥이'야. "
                f"짧고 친근하게(말끝에 '~냥' 붙이기) 2~3문장 이내로 답변해줘.\n"
                f"질문: {question}"
            )
            response = chat.send_message(prompt)

            # 💾 대화 기록 저장
            history = load_history()
            history.append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "question": question,
                "answer": response.text
            })
            save_history(history)

            # 대답창 출력
            res_dialog = CustomResponseDialog('🐾 제미냥이의 대답', response.text, is_error=False, parent=self)
            x, y = self.get_smart_position(res_dialog.width(), res_dialog.height())
            res_dialog.move(x, y)
            res_dialog.exec_()

            # '다시 질문하기' 클릭 시 질문창 바로 띄우기
            if res_dialog.ask_again:
                self.open_input_dialog()

        except Exception as e:
            err_dialog = CustomResponseDialog('😿 오류가 발생했다냥', str(e), is_error=True, parent=self)
            x, y = self.get_smart_position(err_dialog.width(), err_dialog.height())
            err_dialog.move(x, y)
            err_dialog.exec_()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    pet = DesktopPet()
    sys.exit(app.exec_())