"""Dependency-free persistence and conversation helpers for 제미곰.

Only complete question/answer pairs are saved. API credentials belong in the
operating-system credential store, never in this database's settings.
"""
from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

LEGACY_CAT_PERSONA = (
    "너는 바탕화면에 앉아 있는 귀여운 회색 고양이 '제미냥이'야. "
    "한국어로 짧고 친근하게, 보통 2~3문장으로 답해 줘. "
    "자연스러울 때 말끝에 '~냥'을 붙여 줘. "
    "앞선 대화를 참고하고, 모르는 내용은 모른다고 말해 줘."
)
CHARACTER_STATES = frozenset({"idle", "listening", "thinking", "happy"})

DEFAULT_SETTINGS: dict[str, Any] = {
    "model": "gemini-3.8-flash",
    "persona": (
        "너는 바탕화면에서 이야기를 듣는 귀여운 곰 친구 '제미곰'이야. "
        "한국어로 짧고 다정하게, 보통 2~3문장으로 답해 줘. "
        "앞선 대화를 자연스럽게 참고하고, 모르는 내용은 모른다고 말해 줘."
    ),
    "context_turns": 20,
    "pet_size": 220,
    "always_on_top": True,
    "remember_key": False,
    "pet_x": None,
    "pet_y": None,
    "theme": "cream",
    "animation_enabled": True,
    "character_assets": {},
}

class AlreadyImportedError(ValueError):
    """The same validated legacy contents were imported previously."""

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

def _is_secret_key(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    if normalized == "rememberkey":
        return False
    return normalized in {"key", "token", "authorization"} or any(
        marker in normalized for marker in (
            "apikey", "accesstoken", "refreshtoken", "password", "secret", "credential"
        )
    )

def _without_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_secrets(item) for key, item in value.items()
                if isinstance(key, str) and not _is_secret_key(key)}
    if isinstance(value, (list, tuple)):
        return [_without_secrets(item) for item in value]
    return value

def _check_setting(name: str, value: Any) -> None:
    if name in {"model", "persona"}:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} 설정에는 비어 있지 않은 문자열이 필요합니다.")
    elif name in {"always_on_top", "remember_key", "animation_enabled"}:
        if not isinstance(value, bool):
            raise ValueError(f"{name} 설정에는 참/거짓 값이 필요합니다.")
    elif name == "context_turns":
        if type(value) is not int or value < 0:
            raise ValueError("대화 기억 수는 0 이상의 정수여야 합니다.")
    elif name == "pet_size":
        if type(value) is not int or value <= 0:
            raise ValueError("캐릭터 크기는 양의 정수여야 합니다.")
    elif name in {"pet_x", "pet_y"}:
        if value is not None and type(value) is not int:
            raise ValueError("캐릭터 위치는 정수 또는 비어 있는 값이어야 합니다.")
    elif name == "theme":
        if value not in ("cream", "night"):
            raise ValueError("테마는 cream 또는 night여야 합니다.")
    elif name == "character_assets":
        if not isinstance(value, dict):
            raise ValueError("캐릭터 이미지는 상태별 경로 사전이어야 합니다.")
        if not all(isinstance(state, str) and state in CHARACTER_STATES for state in value):
            raise ValueError("캐릭터 이미지 상태는 idle/listening/thinking/happy만 사용할 수 있습니다.")
        if not all(isinstance(path, str) and path.strip() for path in value.values()):
            raise ValueError("캐릭터 이미지 경로에는 비어 있지 않은 문자열이 필요합니다.")

def _normalize_setting(name: str, value: Any) -> Any:
    # Blank file-picker rows mean use the bundled image for that state.
    if name == "character_assets" and isinstance(value, dict):
        return {state: path.strip() if isinstance(path, str) else path
                for state, path in value.items()
                if not (isinstance(path, str) and not path.strip())}
    return value

def _title_for(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= 36 else title[:35] + "…"

class Store:
    """Transactional SQLite store using a separate connection per operation.

    The source legacy JSON is only read; failed validation or migration never
    changes it. Complete turns and their conversation title commit together.
    """
    def __init__(self, data_dir: Path, legacy_path: Path | None = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "gemeow.sqlite3"
        self.startup_warnings: list[str] = []
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS settings ("
                               "key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS conversations ("
                               "id TEXT PRIMARY KEY, title TEXT NOT NULL, "
                               "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS turns ("
                               "id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL "
                               "REFERENCES conversations(id) ON DELETE CASCADE, "
                               "question TEXT NOT NULL CHECK(length(trim(question)) > 0), "
                               "answer TEXT NOT NULL CHECK(length(trim(answer)) > 0), "
                               "created_at TEXT NOT NULL)")
            connection.execute("CREATE INDEX IF NOT EXISTS turns_conversation_idx "
                               "ON turns(conversation_id)")
            connection.execute("CREATE TABLE IF NOT EXISTS migrations ("
                               "content_hash TEXT PRIMARY KEY, source_path TEXT NOT NULL, "
                               "imported_at TEXT NOT NULL, conversation_id TEXT "
                               "REFERENCES conversations(id) ON DELETE SET NULL)")
            connection.execute("PRAGMA user_version = 1")
        if legacy_path is not None:
            try:
                source = Path(legacy_path)
                if source.is_file():
                    self.import_legacy(source)
            except AlreadyImportedError:
                pass
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.startup_warnings.append(
                    "이전 대화 기록을 가져오지 못했습니다. 원본 파일은 보존했습니다. "
                    + (str(exc) if isinstance(exc, ValueError)
                       else "파일 접근 또는 저장 상태를 확인해 주세요."))

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        """Connections close after each operation; this supports UI cleanup."""

    def get_settings(self) -> dict[str, Any]:
        result = deepcopy(DEFAULT_SETTINGS)
        with self._connection() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            if _is_secret_key(row["key"]):
                continue
            try:
                value = _normalize_setting(row["key"], _without_secrets(json.loads(row["value"])))
                _check_setting(row["key"], value)
            except (ValueError, TypeError):
                continue
            result[row["key"]] = value
        # Only replace the unmodified old default; preserve a user's own persona.
        if result["persona"] == LEGACY_CAT_PERSONA:
            result["persona"] = DEFAULT_SETTINGS["persona"]
        return result

    def save_settings(self, settings: dict[str, Any]) -> None:
        """Merge non-secret settings without persisting API keys or tokens."""
        if not isinstance(settings, dict):
            raise ValueError("설정은 사전 형식이어야 합니다.")
        cleaned = _without_secrets(settings)
        serialized: list[tuple[str, str]] = []
        for name, value in cleaned.items():
            value = _normalize_setting(name, value)
            _check_setting(name, value)
            try:
                encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} 설정을 저장할 수 없습니다.") from exc
            serialized.append((name, encoded))
        with self._connection() as connection:
            existing = connection.execute("SELECT key FROM settings").fetchall()
            connection.executemany("DELETE FROM settings WHERE key = ?",
                                   [(row["key"],) for row in existing
                                    if _is_secret_key(row["key"])])
            connection.executemany(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", serialized)

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT conversations.*, (SELECT COUNT(*) FROM turns "
                "WHERE conversation_id = conversations.id) AS turn_count "
                "FROM conversations ORDER BY updated_at DESC, rowid DESC").fetchall()
        return [dict(row) for row in rows]

    def create_conversation(self, title: str = "새 대화") -> str:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("대화 제목이 비어 있습니다.")
        conversation_id, timestamp = uuid4().hex, _now()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, title.strip(), timestamp, timestamp))
        return conversation_id

    def get_turns(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, question, answer, created_at FROM turns "
                "WHERE conversation_id = ? ORDER BY rowid", (conversation_id,)).fetchall()
        return [dict(row) for row in rows]

    def append_turn(self, conversation_id: str, question: str, answer: str) -> str:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("질문이 비어 있어 저장하지 않았습니다.")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("완성된 답변이 없어 저장하지 않았습니다.")
        question, answer = question.strip(), answer.strip()
        turn_id, timestamp = uuid4().hex, _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT id FROM conversations WHERE id = ?",
                                  (conversation_id,)).fetchone() is None:
                raise KeyError("대화를 찾을 수 없습니다.")
            is_first = connection.execute(
                "SELECT 1 FROM turns WHERE conversation_id = ? LIMIT 1",
                (conversation_id,)).fetchone() is None
            connection.execute(
                "INSERT INTO turns(id, conversation_id, question, answer, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (turn_id, conversation_id, question, answer, timestamp))
            if is_first:
                connection.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                    (_title_for(question), timestamp, conversation_id))
            else:
                connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?",
                                   (timestamp, conversation_id))
        return turn_id

    def delete_conversation(self, conversation_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def export_conversation(self, conversation_id: str) -> str:
        with self._connection() as connection:
            conversation = connection.execute("SELECT * FROM conversations WHERE id = ?",
                                               (conversation_id,)).fetchone()
            if conversation is None:
                raise KeyError("대화를 찾을 수 없습니다.")
            turns = connection.execute(
                "SELECT question, answer, created_at FROM turns "
                "WHERE conversation_id = ? ORDER BY rowid", (conversation_id,)).fetchall()
        lines = ["제미곰 대화 기록", f"제목: {conversation['title']}",
                 f"시작: {conversation['created_at']}", ""]
        for turn in turns:
            lines.extend([f"[{turn['created_at']}]", f"나: {turn['question']}",
                          f"제미곰: {turn['answer']}", "", "─" * 36, ""])
        if not turns:
            lines.append("아직 나눈 대화가 없습니다.")
        return "\n".join(lines).rstrip() + "\n"

    def import_legacy(self, path: Path) -> int:
        """Import a validated legacy list once, keeping its source unchanged.

        Returns the complete turn count. Identical normalized contents raise
        AlreadyImportedError even when the JSON indentation differs.
        """
        source = Path(path)
        try:
            contents = json.loads(source.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ValueError("대화 기록 파일이 올바른 UTF-8 JSON 형식이 아닙니다.") from exc
        if not isinstance(contents, list):
            raise ValueError("이전 대화 기록은 목록 형식이어야 합니다.")
        validated: list[dict[str, str]] = []
        for index, item in enumerate(contents, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"{index}번째 기록이 올바른 항목 형식이 아닙니다.")
            question = item.get("question")
            answer = item.get("answer")
            timestamp = item.get("time", "")
            if not isinstance(question, str) or not question.strip():
                raise ValueError(f"{index}번째 기록에 완성된 질문이 없습니다.")
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError(f"{index}번째 기록에 완성된 답변이 없습니다.")
            if not isinstance(timestamp, str):
                raise ValueError(f"{index}번째 기록의 시간 형식이 올바르지 않습니다.")
            validated.append({"question": question.strip(), "answer": answer.strip(),
                              "time": timestamp.strip()})
        canonical = json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        imported_at = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM migrations WHERE content_hash = ?",
                                  (fingerprint,)).fetchone():
                raise AlreadyImportedError("이미 가져온 것과 동일한 대화 기록입니다.")
            conversation_id = uuid4().hex if validated else None
            if validated:
                connection.execute(
                    "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (conversation_id, "이전 대화 · " + _title_for(validated[0]["question"]),
                     imported_at, imported_at))
                connection.executemany(
                    "INSERT INTO turns(id, conversation_id, question, answer, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(uuid4().hex, conversation_id, item["question"], item["answer"],
                      item["time"] or imported_at) for item in validated])
            connection.execute(
                "INSERT INTO migrations(content_hash, source_path, imported_at, conversation_id) "
                "VALUES (?, ?, ?, ?)",
                (fingerprint, str(source.resolve()), imported_at, conversation_id))
        return len(validated)


def build_history(turns: list[dict[str, Any]], max_turns: int) -> list[dict[str, Any]]:
    """Build Gemini contents from the latest complete pairs, in order."""
    if type(max_turns) is not int or max_turns < 0:
        raise ValueError("대화 기억 수는 0 이상의 정수여야 합니다.")
    if max_turns == 0:
        return []
    complete = [turn for turn in turns if isinstance(turn, dict)
                and isinstance(turn.get("question"), str) and turn["question"].strip()
                and isinstance(turn.get("answer"), str) and turn["answer"].strip()]
    history: list[dict[str, Any]] = []
    for turn in complete[-max_turns:]:
        history.extend([
            {"role": "user", "parts": [{"text": turn["question"]}]},
            {"role": "model", "parts": [{"text": turn["answer"]}]},
        ])
    return history


def friendly_error(exc: BaseException) -> str:
    """Return a safe message without echoing exception text or credentials."""
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status in (401, 403):
        return "API 키와 Gemini API 사용 권한을 확인해 주세요."
    if status == 404:
        return "선택한 모델을 찾을 수 없습니다. 설정에서 모델 이름과 사용 가능 여부를 확인해 주세요."
    if status == 429:
        return "잠시 요청이 많거나 사용 한도에 도달했습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(exc, TimeoutError):
        return "응답을 기다리는 시간이 길어졌습니다. 연결을 확인한 뒤 다시 보내 주세요."
    if isinstance(exc, (ConnectionError, OSError)):
        return "연결 또는 파일 접근에 문제가 생겼습니다. 연결 상태와 저장 위치를 확인해 주세요."
    return "요청을 완료하지 못했습니다. 설정과 연결 상태를 확인한 뒤 다시 시도해 주세요."
