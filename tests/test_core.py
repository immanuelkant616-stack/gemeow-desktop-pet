"""Run: python -m unittest discover -s tests -v (no GUI, SDK, network, or API key required)."""
from __future__ import annotations
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gemeow_core import AlreadyImportedError, DEFAULT_SETTINGS, LEGACY_CAT_PERSONA, Store, build_history, friendly_error

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="core-tests-", dir=Path(__file__).resolve().parent)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "data")

    def tearDown(self):
        self.store.close()
        self.assertEqual(self.root.resolve().parent, Path(__file__).resolve().parent)
        self.temporary.cleanup()

    def legacy(self, contents, name="chat_history.json"):
        path = self.root / name
        path.write_text(json.dumps(contents, ensure_ascii=False), encoding="utf-8")
        return path

    def test_restart_preserves_context_and_settings(self):
        conversation = self.store.create_conversation()
        self.store.append_turn(conversation, "내 이름은 이솔이야", "이솔아 반갑다냥!")
        self.store.append_turn(conversation, "내 이름이 뭐야?", "이솔이다냥!")
        self.store.save_settings({"last_conversation_id": conversation, "pet_x": -200})
        self.store.close()
        restored = Store(self.root / "data")
        self.assertEqual(restored.get_settings()["last_conversation_id"], conversation)
        self.assertEqual(restored.get_settings()["pet_x"], -200)
        history = build_history(restored.get_turns(conversation), 20)
        self.assertEqual([item["role"] for item in history], ["user", "model", "user", "model"])
        self.assertEqual(history[0]["parts"][0]["text"], "내 이름은 이솔이야")
        self.assertEqual(restored.list_conversations()[0]["title"], "내 이름은 이솔이야")

    def test_truncation_preserves_only_latest_complete_pairs(self):
        turns = [{"question": f"q{number}", "answer": f"a{number}"} for number in range(5)]
        turns.extend([{"question": "failed", "answer": ""}, {"question": "pending"}])
        history = build_history(turns, 2)
        self.assertEqual([item["parts"][0]["text"] for item in history], ["q3", "a3", "q4", "a4"])
        self.assertEqual(build_history(turns, 0), [])
        with self.assertRaises(ValueError):
            build_history(turns, -1)

    def test_malformed_import_is_all_or_nothing_and_keeps_source(self):
        conversation = self.store.create_conversation()
        self.store.append_turn(conversation, "기존 질문", "기존 답변")
        path = self.legacy([
            {"question": "유효한 질문", "answer": "유효한 답변"},
            {"question": "실패한 질문", "answer": None},
        ])
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.import_legacy(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(self.store.list_conversations()), 1)
        self.assertEqual(self.store.get_turns(conversation)[0]["answer"], "기존 답변")
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM migrations").fetchone()[0], 0)

    def test_invalid_json_autoimport_warns_and_never_overwrites(self):
        path = self.root / "broken.json"
        original = b'{"broken": '
        path.write_bytes(original)
        restored = Store(self.root / "auto", legacy_path=path)
        self.assertTrue(restored.startup_warnings)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(restored.list_conversations(), [])

    def test_duplicate_import_detects_equivalent_json_and_restart(self):
        data = [{"time": "2026-10-26 19:00", "question": "안녕", "answer": "반갑다냥"}]
        path = self.legacy(data)
        original = path.read_bytes()
        self.assertEqual(self.store.import_legacy(path), 1)
        self.assertEqual(path.read_bytes(), original)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
        with self.assertRaises(AlreadyImportedError):
            self.store.import_legacy(path)
        restored = Store(self.root / "data", legacy_path=path)
        self.assertEqual(restored.startup_warnings, [])
        self.assertEqual(len(restored.list_conversations()), 1)
        conversation = restored.list_conversations()[0]["id"]
        self.assertEqual(restored.get_turns(conversation)[0]["created_at"], "2026-10-26 19:00")

    def test_delete_cascades_turns_and_retains_duplicate_tracking(self):
        path = self.legacy([{"question": "안녕", "answer": "안녕냥"}])
        self.store.import_legacy(path)
        conversation = self.store.list_conversations()[0]["id"]
        self.store.delete_conversation(conversation)
        self.assertEqual(self.store.list_conversations(), [])
        self.assertEqual(self.store.get_turns(conversation), [])
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 0)
        with self.assertRaises(AlreadyImportedError):
            self.store.import_legacy(path)

    def test_secret_settings_never_reach_database(self):
        self.store.save_settings({
            "api_key": "test-api-secret-value", "GEMINI_API_KEY": "test-gemini-value",
            "accessToken": "test-token-value", "remember_key": True,
            "context_turns": 12, "last_conversation_id": "safe-id",
            "extra": {"safe": "kept", "password": "test-password-value",
                      "items": [{"apiKey": "nested-test-secret", "visible": 2}]},
        })
        settings = self.store.get_settings()
        self.assertTrue(settings["remember_key"])
        self.assertEqual(settings["context_turns"], 12)
        self.assertEqual(settings["model"], DEFAULT_SETTINGS["model"])
        self.assertEqual(settings["extra"], {"safe": "kept", "items": [{"visible": 2}]})
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            rows = str(connection.execute("SELECT key, value FROM settings").fetchall())
        for secret in ["test-api-secret-value", "test-gemini-value", "test-token-value",
                       "test-password-value", "nested-test-secret"]:
            self.assertNotIn(secret, rows)

    def test_invalid_settings_do_not_partially_save(self):
        self.store.save_settings({"context_turns": 20})
        with self.assertRaises(ValueError):
            self.store.save_settings({"last_conversation_id": "should-not-save", "context_turns": -1})
        self.assertNotIn("last_conversation_id", self.store.get_settings())
        self.assertEqual(self.store.get_settings()["context_turns"], 20)

    def test_incomplete_turn_and_missing_conversation_are_not_saved(self):
        conversation = self.store.create_conversation()
        for question, answer in [("", "a"), ("q", ""), ("q", None), ("q", " \n")]:
            with self.assertRaises(ValueError):
                self.store.append_turn(conversation, question, answer)
        with self.assertRaises(KeyError):
            self.store.append_turn("missing", "q", "a")
        self.assertEqual(self.store.get_turns(conversation), [])

    def test_title_and_turn_commit_or_rollback_together(self):
        conversation = self.store.create_conversation()
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            connection.execute(
                "CREATE TRIGGER fail_update BEFORE UPDATE ON conversations "
                "BEGIN SELECT RAISE(ABORT, 'simulated disk transaction error'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.append_turn(conversation, "원자성 테스트", "저장하면 안 되는 답변")
        self.assertEqual(self.store.get_turns(conversation), [])
        self.assertEqual(self.store.list_conversations()[0]["title"], "새 대화")

    def test_export_is_readable_and_preserves_plain_text(self):
        conversation = self.store.create_conversation()
        self.store.append_turn(conversation, "<b>안녕</b>\n두 번째 줄", "반갑다냥!\n함께 놀자냥.")
        text = self.store.export_conversation(conversation)
        self.assertIn("제미곰 대화 기록", text)
        self.assertIn("나: <b>안녕</b>\n두 번째 줄", text)
        self.assertIn("제미곰: 반갑다냥!", text)

    def test_appearance_settings_persist_after_restart(self):
        assets = {"idle": r"C:\\art\\평소.png", "thinking": "assets/thinking.gif"}
        self.store.save_settings({
            "theme": "night", "animation_enabled": False,
            "pet_size": 260, "character_assets": assets,
        })
        restored = Store(self.root / "data").get_settings()
        self.assertEqual(restored["theme"], "night")
        self.assertFalse(restored["animation_enabled"])
        self.assertEqual(restored["pet_size"], 260)
        self.assertEqual(restored["character_assets"], assets)

    def test_appearance_validation_is_atomic(self):
        invalid = [
            {"theme": "unknown"}, {"theme": None}, {"theme": 3},
            {"animation_enabled": "yes"}, {"animation_enabled": 1},
            {"character_assets": []}, {"character_assets": {"unknown": "x.png"}},
            {"character_assets": {"idle": None}}, {"character_assets": {"happy": 5}},
        ]
        for setting in invalid:
            with self.subTest(setting=setting):
                with self.assertRaises(ValueError):
                    self.store.save_settings({"last_conversation_id": "do-not-save", **setting})
                self.assertNotIn("last_conversation_id", self.store.get_settings())
        self.assertEqual(self.store.get_settings()["theme"], "cream")

    def test_empty_asset_rows_use_defaults_and_other_paths_preserved(self):
        self.store.save_settings({"character_assets": {"idle": "  ", "happy": " smile.png "}})
        self.assertEqual(self.store.get_settings()["character_assets"], {"happy": "smile.png"})

    def test_mutable_default_assets_are_independent(self):
        first = self.store.get_settings()
        first["character_assets"]["idle"] = "private.png"
        second = self.store.get_settings()
        self.assertEqual(second["character_assets"], {})
        self.assertEqual(DEFAULT_SETTINGS["character_assets"], {})

    def test_old_default_persona_migrates_without_rewriting_database(self):
        self.store.save_settings({"persona": LEGACY_CAT_PERSONA})
        self.assertEqual(self.store.get_settings()["persona"], DEFAULT_SETTINGS["persona"])
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            stored = json.loads(connection.execute(
                "SELECT value FROM settings WHERE key = 'persona'").fetchone()[0])
        self.assertEqual(stored, LEGACY_CAT_PERSONA)

    def test_custom_persona_is_preserved(self):
        custom = "나는 사용자만의 조용한 파란 고양이야."
        self.store.save_settings({"persona": custom})
        self.assertEqual(self.store.get_settings()["persona"], custom)

    def test_error_messages_never_echo_exception_or_key(self):
        message = friendly_error(RuntimeError("API key=test-secret-and-sensitive-url"))
        self.assertNotIn("test-secret", message)
        self.assertNotIn("sensitive-url", message)

class ThemeTests(unittest.TestCase):
    def test_palette_is_independent_and_unknown_uses_cream(self):
        from gemeow_theme import palette
        first = palette("night")
        first["text"] = "changed"
        self.assertNotEqual(palette("night")["text"], "changed")
        self.assertEqual(palette("not-a-theme"), palette("cream"))

    def test_both_stylesheets_have_all_tokens_resolved(self):
        from gemeow_theme import stylesheet, palette
        for theme in ("cream", "night"):
            text = stylesheet(theme)
            self.assertIn("QTextBrowser", text)
            self.assertIn(palette(theme)["background"], text)
            self.assertNotIn("{p[", text)

    def test_primary_text_and_normal_text_have_readable_contrast(self):
        from gemeow_theme import palette
        def luminance(color):
            channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
            linear = [value/12.92 if value <= 0.04045 else ((value+0.055)/1.055)**2.4
                      for value in channels]
            return sum(a*b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))
        def contrast(first, second):
            a, b = sorted((luminance(first), luminance(second)))
            return (b + .05)/(a + .05)
        for theme in ("cream", "night"):
            p = palette(theme)
            self.assertGreaterEqual(contrast(p["text"], p["surface"]), 4.5)
            self.assertGreaterEqual(contrast(p["text"], p["background"]), 4.5)
            self.assertGreaterEqual(contrast(p["primary_text"], p["accent"]), 4.5)
            self.assertGreaterEqual(contrast(p["text"], p["user_bubble"]), 4.5)
            self.assertGreaterEqual(contrast(p["text"], p["assistant_bubble"]), 4.5)

if __name__ == "__main__":
    unittest.main(verbosity=2)


