"""Cached, transparent desktop-character animation for Gemeow.

The default sprite sheet contains four rows (idle, listening, thinking, happy)
and four columns of animation frames. User PNG/GIF overrides are optional.
No network or image-generation work happens while this module runs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import PyQt5
from PyQt5.QtCore import QCoreApplication, QObject, QSize, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QMovie, QPainter, QPixmap, QTransform

STATES = ("idle", "listening", "thinking", "happy")
_INTERVALS = {"idle": 200, "listening": 180, "thinking": 220, "happy": 140}
_LABELS = {"idle": "곰", "listening": "듣는 중", "thinking": "생각 중", "happy": "방긋!"}


class CharacterAnimator(QObject):
    """Emit ready-to-display square pixmaps without reading files every frame.

    Construct this object after QApplication, and keep it on the GUI thread.
    stop() pauses playback while retaining the current image. State changes are
    still displayed immediately while paused or with motion disabled.
    """

    frame_changed = pyqtSignal(QPixmap)

    def __init__(self, asset_dir: Path, parent: QObject | None = None):
        super().__init__(parent)
        # Some Windows environments set the platform-plugin path explicitly;
        # add the plugin root as well so QMovie can discover qgif.dll.
        plugin_root = Path(PyQt5.__file__).parent / "Qt5" / "plugins"
        if plugin_root.is_dir():
            QCoreApplication.addLibraryPath(str(plugin_root))
        self.asset_dir = Path(asset_dir)
        self.warnings: list[str] = []
        self._state = "idle"
        self._direction = "left"
        self._size = 180
        self._motion_enabled = True
        self._started = False
        self._index = 0
        self._cache: dict[tuple, QPixmap] = {}
        self._custom_pixmaps: dict[str, QPixmap] = {}
        self._movies: dict[str, QMovie] = {}
        self._custom_paths: dict[str, str] = {}
        self._defaults = self._load_default_sheet()
        self._current = QPixmap()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        self._show_current()

    @property
    def state(self) -> str:
        return self._state

    @property
    def direction(self) -> str:
        return self._direction

    @property
    def size(self) -> int:
        return self._size

    @property
    def motion_enabled(self) -> bool:
        return self._motion_enabled

    def _load_default_sheet(self) -> dict[str, list[QPixmap]]:
        path = self.asset_dir / "bear_sprites.png"
        sheet = QPixmap(str(path))
        if not sheet.isNull() and sheet.width() >= 4 and sheet.height() >= 4:
            # Generation tools may return a size such as 1254x1254 rather than
            # an exact multiple of four. Shared rounded edges keep every pixel.
            xs = [round(column * sheet.width() / 4) for column in range(5)]
            ys = [round(row * sheet.height() / 4) for row in range(5)]
            return {
                state: [sheet.copy(xs[column], ys[row],
                                   xs[column + 1] - xs[column], ys[row + 1] - ys[row])
                        for column in range(4)]
                for row, state in enumerate(STATES)
            }
        self.warnings.append(
            "기본 캐릭터 이미지를 읽지 못해 상태 안내 그림을 표시합니다. "
            "assets/bear_sprites.png 파일을 확인해 주세요."
        )
        return {state: [self._fallback(state)] for state in STATES}

    @staticmethod
    def _fallback(state: str) -> QPixmap:
        # A native UI badge keeps the application usable if an asset is missing.
        pix = QPixmap(180, 180)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#A09078"))
        painter.setBrush(QColor("#F6EEDC"))
        painter.drawRoundedRect(12, 30, 156, 120, 30, 30)
        painter.setPen(QColor("#6D5B43"))
        painter.setFont(QFont("Malgun Gothic", 19, QFont.Bold))
        painter.drawText(pix.rect(), Qt.AlignCenter, _LABELS[state])
        painter.end()
        return pix

    def current_pixmap(self) -> QPixmap:
        return QPixmap(self._current)

    def set_state(self, state: str) -> None:
        if state not in STATES:
            raise ValueError("지원하지 않는 캐릭터 상태입니다.")
        if state == self._state:
            return
        previous = self._movies.get(self._state)
        if previous is not None:
            previous.stop()
        self._state = state
        self._index = 0
        movie = self._movies.get(state)
        if movie is not None:
            movie.jumpToFrame(0)
        self._show_current()
        self._sync_playback()

    def set_direction(self, direction: str = "left") -> None:
        if direction not in ("left", "right"):
            raise ValueError("캐릭터 방향은 left 또는 right여야 합니다.")
        if direction != self._direction:
            self._direction = direction
            self._show_current()

    def set_size(self, px: int) -> None:
        if type(px) is not int or not 32 <= px <= 2048:
            raise ValueError("캐릭터 크기는 32~2048 픽셀의 정수여야 합니다.")
        if px != self._size:
            self._size = px
            self._cache.clear()
            self._show_current()

    def set_motion_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("모션 설정은 참 또는 거짓이어야 합니다.")
        if enabled == self._motion_enabled:
            return
        self._motion_enabled = enabled
        if not enabled:
            self._index = 0
            movie = self._movies.get(self._state)
            if movie is not None:
                movie.setPaused(True)
                movie.jumpToFrame(0)
            self._show_current()
        self._sync_playback()

    def set_custom_assets(self, assets: Mapping[str, str | Path | None]) -> None:
        """Replace state overrides atomically; an empty mapping restores defaults.

        PNG is a static pose. GIF retains its original timing and loops through
        QMovie. All paths and images are validated before replacing old assets.
        A failure leaves every previous custom image and playback intact.
        """
        if not isinstance(assets, Mapping):
            raise ValueError("캐릭터 이미지는 상태별 파일 경로로 지정해 주세요.")
        new_pixmaps: dict[str, QPixmap] = {}
        new_movies: dict[str, QMovie] = {}
        new_paths: dict[str, str] = {}
        try:
            for state, value in assets.items():
                if state not in STATES:
                    raise ValueError("지원하지 않는 캐릭터 상태입니다.")
                if value is None or value == "":
                    continue
                if not isinstance(value, (str, Path)):
                    raise ValueError("캐릭터 이미지 파일 경로가 올바르지 않습니다.")
                path = Path(value)
                if not path.is_file() or path.suffix.lower() not in (".png", ".gif"):
                    raise ValueError("존재하는 PNG 또는 GIF 파일을 선택해 주세요.")
                if path.suffix.lower() == ".gif":
                    movie = QMovie(str(path), parent=self)
                    new_movies[state] = movie
                    movie.setCacheMode(QMovie.CacheAll)
                    if not movie.isValid() or not movie.jumpToFrame(0) or movie.currentPixmap().isNull():
                        raise ValueError("GIF 이미지를 읽지 못했습니다. 다른 파일을 선택해 주세요.")
                else:
                    pix = QPixmap(str(path))
                    if pix.isNull():
                        raise ValueError("PNG 이미지를 읽지 못했습니다. 다른 파일을 선택해 주세요.")
                    new_pixmaps[state] = pix
                new_paths[state] = str(path.resolve())
        except Exception as error:
            for movie in new_movies.values():
                movie.stop()
                movie.setFileName("")
                movie.deleteLater()
            if isinstance(error, ValueError):
                raise
            raise ValueError("캐릭터 이미지를 읽지 못했습니다. 파일과 접근 권한을 확인해 주세요.") from error

        self.timer.stop()
        for movie in self._movies.values():
            movie.stop()
            movie.setFileName("")
            movie.deleteLater()
        self._custom_pixmaps = new_pixmaps
        self._movies = new_movies
        self._custom_paths = new_paths
        self._index = 0
        self._cache.clear()
        for state, movie in self._movies.items():
            movie.frameChanged.connect(lambda _frame, current=state: self._on_movie_frame(current))
        self._show_current()
        self._sync_playback()

    def start(self) -> None:
        self._started = True
        self._sync_playback()

    def stop(self) -> None:
        self._started = False
        self.timer.stop()
        for movie in self._movies.values():
            if movie.state() == QMovie.Running:
                movie.setPaused(True)

    def _sync_playback(self) -> None:
        self.timer.stop()
        movie = self._movies.get(self._state)
        if not self._started or not self._motion_enabled:
            if movie is not None and movie.state() == QMovie.Running:
                movie.setPaused(True)
            return
        if movie is not None:
            if movie.state() == QMovie.Paused:
                movie.setPaused(False)
            else:
                movie.start()
        elif self._state not in self._custom_pixmaps and len(self._defaults[self._state]) > 1:
            self.timer.start(_INTERVALS[self._state])

    def _advance(self) -> None:
        if not self._started or not self._motion_enabled:
            return
        if self._state in self._movies or self._state in self._custom_pixmaps:
            return
        self._index = (self._index + 1) % len(self._defaults[self._state])
        self._show_current()

    def _on_movie_frame(self, state: str) -> None:
        if state == self._state:
            self._show_current()

    def _show_current(self) -> None:
        state = self._state
        if state in self._movies:
            movie = self._movies[state]
            source = movie.currentPixmap()
            frame_number = movie.currentFrameNumber()
            kind = "gif"
        elif state in self._custom_pixmaps:
            source = self._custom_pixmaps[state]
            frame_number = 0
            kind = "png"
        else:
            source = self._defaults[state][self._index]
            frame_number = self._index
            kind = "sheet"
        if source.isNull():
            source = self._defaults[state][0]
            kind, frame_number = "fallback", 0
        direction = self._direction if state == "listening" else "left"
        key = (state, kind, frame_number, direction, self._size)
        if key not in self._cache:
            scaled = source.scaled(QSize(self._size, self._size), Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
            if direction == "right":
                scaled = scaled.transformed(QTransform().scale(-1, 1))
            canvas = QPixmap(self._size, self._size)
            canvas.fill(Qt.transparent)
            painter = QPainter(canvas)
            painter.drawPixmap((self._size - scaled.width()) // 2,
                               (self._size - scaled.height()) // 2, scaled)
            painter.end()
            self._cache[key] = canvas
        self._current = self._cache[key]
        self.frame_changed.emit(QPixmap(self._current))
