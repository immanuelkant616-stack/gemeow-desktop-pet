"""Gemini calls on a dedicated Qt worker; cancellation waits for network I/O.

A pending read can take up to the 30 second transport timeout to return.
Raw provider exceptions are never displayed because they may contain secrets.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from google import genai
from google.genai import types
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from gemeow_core import build_history

TIMEOUT_MS = 30_000
RETRY_DELAY_SECONDS = 0.6


class _RequestCancelled(Exception):
    pass


class _InputError(Exception):
    """Only app-authored, safe-to-display messages belong here."""


def _status_code(error: Exception) -> int | None:
    for value in (
        getattr(error, "code", None),
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _is_network_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    return any(
        cls.__name__ in {
            "TimeoutException", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
            "PoolTimeout", "ConnectError", "ReadError", "WriteError", "CloseError",
            "NetworkError", "RemoteProtocolError", "ProxyError", "TransportError",
        }
        for cls in type(error).__mro__
    )


def _retryable(error: Exception) -> bool:
    code = _status_code(error)
    return code in (408, 500, 502, 503, 504) or (
        code is None and _is_network_error(error)
    )


def _friendly_error(error: Exception) -> str:
    if isinstance(error, _InputError):
        return str(error)
    code = _status_code(error)
    if code == 401:
        return "API 키 인증에 실패했어요. 설정에서 키를 확인해 주세요."
    if code == 403:
        return "이 API 키로 접근할 수 없어요. 키와 프로젝트의 Gemini API 사용 권한을 확인해 주세요."
    if code == 400:
        return "요청을 처리할 수 없어요. API 키, 모델 이름과 입력 내용을 확인해 주세요."
    if code == 404:
        return "설정한 모델을 찾을 수 없어요. 설정에서 사용할 수 있는 모델 이름을 확인해 주세요."
    if code == 429:
        return "요청 또는 사용량 한도에 도달했어요. 잠시 후 다시 시도하거나 API 사용량을 확인해 주세요."
    if code == 408 or _is_network_error(error):
        return "서버에 연결하지 못했거나 응답 시간이 초과됐어요. 인터넷 연결을 확인하고 다시 보내 주세요."
    if code is not None and 500 <= code <= 599:
        return "Gemini 서버가 일시적으로 응답하지 않아요. 잠시 후 다시 보내 주세요."
    return "응답을 받지 못했어요. 설정과 인터넷 연결을 확인한 뒤 다시 보내 주세요."


def _finish_reason(response: Any) -> str | None:
    """Reject blocked or incomplete output before it can become saved history."""
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = getattr(feedback, "block_reason", None)
    block_name = str(getattr(block_reason, "value", block_reason) or "")
    blocked_message = (
        "이 질문이나 답변은 Gemini의 콘텐츠 처리 기준 때문에 제공되지 않았어요. "
        "표현을 바꾸어 다시 보내 주세요."
    )
    if block_name and block_name != "BLOCKED_REASON_UNSPECIFIED":
        raise _InputError(blocked_message)
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    candidate = candidates[0]
    if any(getattr(rating, "blocked", False)
           for rating in (getattr(candidate, "safety_ratings", None) or [])):
        raise _InputError(blocked_message)
    reason = getattr(candidate, "finish_reason", None)
    reason_name = str(getattr(reason, "value", reason) or "")
    if not reason_name or reason_name == "FINISH_REASON_UNSPECIFIED":
        return None
    if reason_name == "STOP":
        return reason_name
    if reason_name == "MAX_TOKENS":
        raise _InputError(
            "답변이 길어져 끝까지 생성되지 않았어요. 질문 범위를 줄이거나 "
            "짧게 답해 달라고 요청한 뒤 다시 보내 주세요."
        )
    if reason_name in {
        "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
        "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION",
    }:
        raise _InputError(blocked_message)
    raise _InputError("답변이 끝까지 완성되지 않았어요. 질문을 바꾸거나 다시 보내 주세요.")


def _signature(history: list[dict[str, Any]]) -> str:
    return json.dumps(history, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class GeminiWorker(QObject):
    chunk = pyqtSignal(str, str)
    succeeded = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)
    cancelled = pyqtSignal(str)
    finished = pyqtSignal(str)
    status = pyqtSignal(str, str)
    closed = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client: Any = None
        self._client_key: str | None = None
        self._chat: Any = None
        self._chat_config: tuple[Any, ...] | None = None
        self._chat_history: str | None = None

    def _invalidate_chat(self) -> None:
        self._chat = None
        self._chat_config = None
        self._chat_history = None

    def _get_client(self, api_key: str) -> Any:
        if self._client is not None and self._client_key == api_key:
            return self._client
        self._close_client()
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        self._client_key = api_key
        return self._client

    @staticmethod
    def _check_cancel(event: threading.Event) -> None:
        if event.is_set():
            raise _RequestCancelled()

    @pyqtSlot(object)
    def request(self, job: dict[str, Any]) -> None:
        request_id = str(job.get("id", "")) if isinstance(job, dict) else ""
        try:
            if not isinstance(job, dict):
                raise _InputError("요청 정보를 읽지 못했어요. 다시 보내 주세요.")
            event = job.get("cancel_event") or threading.Event()
            self._check_cancel(event)
            api_key = str(job.get("api_key", "")).strip()
            model = str(job.get("model", "")).strip()
            kind = job.get("kind", "chat")
            if not api_key:
                raise _InputError("설정에서 Gemini API 키를 입력해 주세요.")
            if not model:
                raise _InputError("설정에서 사용할 Gemini 모델 이름을 입력해 주세요.")
            if kind not in ("chat", "probe"):
                raise _InputError("지원하지 않는 요청이에요. 다시 보내 주세요.")
            question = str(job.get("question", "")).strip()
            if kind == "chat" and not question:
                raise _InputError("질문을 입력해 주세요.")
            persona = str(job.get("persona", ""))
            try:
                max_turns = max(1, min(100, int(job.get("context_turns", 20))))
            except (TypeError, ValueError):
                max_turns = 20
            history = build_history(job.get("turns", []), max_turns) if kind == "chat" else []
            history_signature = _signature(history)
            config_signature = (model, persona, max_turns, job.get("conversation_id", ""))

            for attempt in range(2):
                emitted_text = False
                try:
                    self._check_cancel(event)
                    client = self._get_client(api_key)
                    if kind == "probe":
                        self.status.emit(request_id, "API 키와 모델을 확인하고 있어요…")
                        client.models.get(model=model)
                        self._check_cancel(event)
                        self.succeeded.emit(request_id, "연결 확인이 완료됐어요. 이 API 키로 설정한 모델을 사용할 수 있어요.")
                        return

                    self.status.emit(request_id, "생각 중이에요…")
                    if (self._chat is None or self._chat_config != config_signature
                            or self._chat_history != history_signature):
                        self._chat = client.chats.create(
                            model=model,
                            config=types.GenerateContentConfig(system_instruction=persona),
                            history=history,
                        )
                        self._chat_config = config_signature
                        self._chat_history = history_signature
                    self._check_cancel(event)
                    parts: list[str] = []
                    finish_reason: str | None = None
                    stream = self._chat.send_message_stream(question)
                    try:
                        for response in stream:
                            self._check_cancel(event)
                            finish_reason = _finish_reason(response) or finish_reason
                            piece = response.text
                            if piece:
                                parts.append(piece)
                                emitted_text = True
                                self.chunk.emit(request_id, piece)
                    finally:
                        close_stream = getattr(stream, "close", None)
                        if callable(close_stream):
                            try:
                                close_stream()
                            except Exception:
                                pass
                    self._check_cancel(event)
                    answer = "".join(parts)
                    if not answer.strip():
                        raise _InputError("표시할 답변을 받지 못했어요. 질문을 바꾸거나 다시 보내 주세요.")
                    if finish_reason != "STOP":
                        raise _InputError(
                            "답변 전송이 끝나기 전에 연결이 끊겼어요. 다시 보내 주세요."
                        )
                    # Compare against the entire actual chat. If the next job
                    # trims older turns, comparison forces a fresh bounded chat.
                    self._chat_history = _signature(history + [
                        {"role": "user", "parts": [{"text": question}]},
                        {"role": "model", "parts": [{"text": answer}]},
                    ])
                    self.succeeded.emit(request_id, answer)
                    return
                except _RequestCancelled:
                    raise
                except Exception as error:
                    self._invalidate_chat()
                    if attempt == 0 and not emitted_text and _retryable(error):
                        self.status.emit(request_id, "연결이 지연되어 한 번 더 시도하고 있어요…")
                        if event.wait(RETRY_DELAY_SECONDS):
                            raise _RequestCancelled()
                        continue
                    self.failed.emit(request_id, _friendly_error(error))
                    return
        except _RequestCancelled:
            self._invalidate_chat()
            self.cancelled.emit(request_id)
        except Exception as error:
            self._invalidate_chat()
            self.failed.emit(request_id, _friendly_error(error))
        finally:
            self.finished.emit(request_id)

    def _close_client(self) -> None:
        self._invalidate_chat()
        client, self._client = self._client, None
        self._client_key = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    @pyqtSlot()
    def close(self) -> None:
        try:
            self._close_client()
        finally:
            self.closed.emit()
