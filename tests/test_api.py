"""Provider-free regression tests for the Gemini Qt worker."""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gemeow_api as api


class ProviderError(Exception):
    def __init__(self, code: int):
        self.code = code
        super().__init__("secret-api-key-sentinel and provider internals")


def stream(*pieces):
    responses = [SimpleNamespace(text=piece) for piece in pieces]
    responses.append(SimpleNamespace(
        text=None, candidates=[SimpleNamespace(finish_reason="STOP")]
    ))
    return iter(responses)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.chat = self.client.chats.create.return_value
        self.chat.send_message_stream.return_value = stream("안녕", "이다냥!")
        self.factory_patch = patch.object(api.genai, "Client", return_value=self.client)
        self.factory = self.factory_patch.start()
        self.delay_patch = patch.object(api, "RETRY_DELAY_SECONDS", 0)
        self.delay_patch.start()
        self.worker = api.GeminiWorker()
        self.events = {name: [] for name in ("chunk", "succeeded", "failed", "cancelled", "finished", "status", "closed")}
        for name, captured in self.events.items():
            getattr(self.worker, name).connect(lambda *args, out=captured: out.append(args))
        self.addCleanup(self.factory_patch.stop)
        self.addCleanup(self.delay_patch.stop)
        self.addCleanup(self.worker.close)

    def job(self, **updates):
        job = dict(id="r1", api_key="secret-api-key-sentinel", model="gemini-test",
                   persona="고양이처럼 말해 줘", context_turns=2, turns=[],
                   question="안녕", cancel_event=threading.Event(), kind="chat",
                   conversation_id="c1")
        job.update(updates)
        return job

    def test_streams_and_reuses_then_bounds_history(self):
        prior = [{"question": "이름은?", "answer": "제미냥이야"}]
        self.worker.request(self.job(turns=prior))
        self.assertEqual(self.events["chunk"], [("r1", "안녕"), ("r1", "이다냥!")])
        self.assertEqual(self.events["succeeded"], [("r1", "안녕이다냥!")])
        self.assertEqual(self.events["finished"], [("r1",)])
        initial_history = self.client.chats.create.call_args.kwargs["history"]
        self.assertEqual(initial_history[0]["parts"][0]["text"], "이름은?")
        turns = prior + [{"question": "안녕", "answer": "안녕이다냥!"}]
        self.chat.send_message_stream.return_value = stream("두 번째")
        self.worker.request(self.job(id="r2", turns=turns, question="계속"))
        self.assertEqual(self.client.chats.create.call_count, 1)
        turns.append({"question": "계속", "answer": "두 번째"})
        self.chat.send_message_stream.return_value = stream("세 번째")
        self.worker.request(self.job(id="r3", turns=turns, question="다음"))
        self.assertEqual(self.client.chats.create.call_count, 2)
        bounded_history = self.client.chats.create.call_args.kwargs["history"]
        self.assertEqual(len(bounded_history), 4)
        self.assertEqual(bounded_history[0]["parts"][0]["text"], "안녕")

    def test_conversation_switch_rebuilds_identical_history(self):
        self.worker.request(self.job())
        self.chat.send_message_stream.return_value = stream("새 대화")
        self.worker.request(self.job(conversation_id="c2"))
        self.assertEqual(self.client.chats.create.call_count, 2)

    def test_cancellation_before_network_finishes_once(self):
        event = threading.Event()
        event.set()
        self.worker.request(self.job(cancel_event=event))
        self.factory.assert_not_called()
        self.assertEqual(self.events["cancelled"], [("r1",)])
        self.assertEqual(self.events["finished"], [("r1",)])
        self.assertFalse(self.events["failed"])

    def test_cancellation_after_chunk_discards_chat(self):
        event = threading.Event()
        self.worker.chunk.connect(lambda *_: event.set())
        self.worker.request(self.job(cancel_event=event))
        self.assertEqual(len(self.events["chunk"]), 1)
        self.assertEqual(self.events["cancelled"], [("r1",)])
        self.assertFalse(self.events["succeeded"])
        self.assertIsNone(self.worker._chat)
        self.assertEqual(self.chat.send_message_stream.call_count, 1)

    def test_transport_error_retries_once_before_output(self):
        self.chat.send_message_stream.side_effect = [ConnectionError("secret-api-key-sentinel"), stream("복구")]
        self.worker.request(self.job())
        self.assertEqual(self.chat.send_message_stream.call_count, 2)
        self.assertEqual(self.client.chats.create.call_count, 2)
        self.assertEqual(self.events["succeeded"], [("r1", "복구")])
        self.assertFalse(self.events["failed"])

    def test_partial_output_is_never_retried(self):
        def broken_stream():
            yield SimpleNamespace(text="일부 답변")
            raise ProviderError(503)
        self.chat.send_message_stream.return_value = broken_stream()
        self.worker.request(self.job())
        self.assertEqual(self.chat.send_message_stream.call_count, 1)
        self.assertEqual(len(self.events["failed"]), 1)
        self.assertFalse(self.events["succeeded"])
        self.assertIsNone(self.worker._chat)

    def test_retries_at_most_once_and_never_displays_secret(self):
        self.chat.send_message_stream.side_effect = ConnectionError("secret-api-key-sentinel")
        self.worker.request(self.job())
        self.assertEqual(self.chat.send_message_stream.call_count, 2)
        self.assertEqual(len(self.events["failed"]), 1)
        self.assertEqual(self.events["finished"], [("r1",)])
        self.assertNotIn("secret-api-key-sentinel", repr(self.events))

    def test_can_cancel_retry_backoff(self):
        event = threading.Event()
        self.chat.send_message_stream.side_effect = ConnectionError("private")
        self.worker.status.connect(lambda _, message: event.set() if "한 번 더" in message else None)
        self.worker.request(self.job(cancel_event=event))
        self.assertEqual(self.chat.send_message_stream.call_count, 1)
        self.assertEqual(self.events["cancelled"], [("r1",)])
        self.assertFalse(self.events["failed"])

    def test_probe_checks_model_without_generation(self):
        self.worker.request(self.job(kind="probe", question=""))
        self.client.models.get.assert_called_once_with(model="gemini-test")
        self.client.chats.create.assert_not_called()
        self.assertEqual(len(self.events["succeeded"]), 1)
        self.assertFalse(self.events["chunk"])

    def test_errors_are_friendly_and_auth_or_quota_is_not_retried(self):
        for code, expected in [(401, "인증"), (403, "권한"), (400, "요청"),
                               (404, "모델"), (429, "한도"), (503, "서버")]:
            with self.subTest(code=code):
                self.events["failed"].clear()
                self.chat.send_message_stream.reset_mock()
                self.chat.send_message_stream.side_effect = ProviderError(code)
                self.worker.request(self.job(id=str(code)))
                self.assertEqual(len(self.events["failed"]), 1)
                message = self.events["failed"][0][1]
                self.assertIn(expected, message)
                self.assertNotIn("secret-api-key-sentinel", message)
                self.assertEqual(self.chat.send_message_stream.call_count, 2 if code == 503 else 1)
                self.assertIsNone(self.worker._chat)

    def test_empty_response_is_not_saved_as_success(self):
        self.chat.send_message_stream.return_value = stream(None, " ")
        self.worker.request(self.job())
        self.assertFalse(self.events["succeeded"])
        self.assertIn("표시할 답변", self.events["failed"][0][1])
        self.assertIsNone(self.worker._chat)

    def test_new_key_closes_old_client_and_configures_timeout(self):
        self.worker.request(self.job())
        options = self.factory.call_args.kwargs["http_options"]
        self.assertEqual(options.timeout, 30000)
        self.assertEqual(options.retry_options.attempts, 1)
        another_client = Mock()
        another_client.chats.create.return_value.send_message_stream.return_value = stream("새 키")
        self.factory.return_value = another_client
        self.worker.request(self.job(api_key="different-key"))
        self.client.close.assert_called_once()
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.events["succeeded"][-1], ("r1", "새 키"))
        self.worker.close()
        another_client.close.assert_called_once()

    def test_shutdown_signals_after_client_is_closed(self):
        self.worker.request(self.job())
        self.assertFalse(self.events["closed"])
        order = []
        self.client.close.side_effect = lambda: order.append("client")
        self.worker.closed.connect(lambda: order.append("signal"))
        self.worker.close()
        self.assertEqual(order, ["client", "signal"])
        self.assertEqual(self.events["closed"], [()])

    def test_shutdown_signals_even_if_client_close_raises(self):
        self.worker.request(self.job())
        self.client.close.side_effect = RuntimeError("secret-api-key-sentinel")
        self.worker.close()
        self.assertEqual(self.events["closed"], [()])
        self.assertIsNone(self.worker._client)
        self.assertNotIn("secret-api-key-sentinel", repr(self.events))

    def test_real_sdk_stop_response_is_accepted(self):
        response = api.types.GenerateContentResponse(candidates=[api.types.Candidate(
            content=api.types.Content(parts=[api.types.Part(text="정상 답변")]),
            finish_reason=api.types.FinishReason.STOP,
        )])
        self.chat.send_message_stream.return_value = iter([response])
        self.worker.request(self.job())
        self.assertEqual(self.events["succeeded"], [("r1", "정상 답변")])

    def test_real_sdk_max_tokens_is_not_recorded_or_retried(self):
        responses = [
            api.types.GenerateContentResponse(candidates=[api.types.Candidate(
                content=api.types.Content(parts=[api.types.Part(text="부분 답변")]),
            )]),
            api.types.GenerateContentResponse(candidates=[api.types.Candidate(
                finish_reason=api.types.FinishReason.MAX_TOKENS,
            )]),
        ]
        self.chat.send_message_stream.return_value = iter(responses)
        self.worker.request(self.job())
        self.assertEqual(self.events["chunk"], [("r1", "부분 답변")])
        self.assertFalse(self.events["succeeded"])
        self.assertIn("끝까지 생성", self.events["failed"][0][1])
        self.assertEqual(self.chat.send_message_stream.call_count, 1)
        self.assertIsNone(self.worker._chat)

    def test_real_sdk_prompt_block_is_not_recorded(self):
        response = api.types.GenerateContentResponse(
            prompt_feedback=api.types.GenerateContentResponsePromptFeedback(
                block_reason=api.types.BlockedReason.SAFETY
            )
        )
        self.chat.send_message_stream.return_value = iter([response])
        self.worker.request(self.job())
        self.assertFalse(self.events["succeeded"])
        self.assertFalse(self.events["chunk"])
        self.assertIn("콘텐츠", self.events["failed"][0][1])
        self.assertEqual(self.chat.send_message_stream.call_count, 1)
        self.assertIsNone(self.worker._chat)

    def test_real_sdk_safety_reason_hides_blocked_chunk(self):
        response = api.types.GenerateContentResponse(candidates=[api.types.Candidate(
            content=api.types.Content(parts=[api.types.Part(text="차단된 내용")]),
            finish_reason=api.types.FinishReason.SAFETY,
        )])
        self.chat.send_message_stream.return_value = iter([response])
        self.worker.request(self.job())
        self.assertFalse(self.events["succeeded"])
        self.assertFalse(self.events["chunk"])
        self.assertIn("콘텐츠", self.events["failed"][0][1])
        self.assertIsNone(self.worker._chat)

    def test_real_sdk_blocked_rating_is_not_recorded(self):
        response = api.types.GenerateContentResponse(candidates=[api.types.Candidate(
            content=api.types.Content(parts=[api.types.Part(text="차단된 내용")]),
            finish_reason=api.types.FinishReason.STOP,
            safety_ratings=[api.types.SafetyRating(blocked=True)],
        )])
        self.chat.send_message_stream.return_value = iter([response])
        self.worker.request(self.job())
        self.assertFalse(self.events["succeeded"])
        self.assertFalse(self.events["chunk"])
        self.assertIn("콘텐츠", self.events["failed"][0][1])
        self.assertIsNone(self.worker._chat)

    def test_stream_without_stop_is_not_recorded(self):
        self.chat.send_message_stream.return_value = iter([SimpleNamespace(text="끊긴 답변")])
        self.worker.request(self.job())
        self.assertFalse(self.events["succeeded"])
        self.assertIn("연결이 끊겼", self.events["failed"][0][1])
        self.assertEqual(self.chat.send_message_stream.call_count, 1)
        self.assertIsNone(self.worker._chat)

    def test_missing_key_does_not_contact_provider(self):
        self.worker.request(self.job(api_key=""))
        self.factory.assert_not_called()
        self.assertIn("API 키", self.events["failed"][0][1])
        self.assertEqual(self.events["finished"], [("r1",)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
