import ast
import importlib
import json
import sys
import time
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


class ProjectDataTests(unittest.TestCase):
    def load_json(self, name):
        with (ROOT / name).open("r", encoding="utf-8") as file:
            return json.load(file)

    def test_all_json_files_are_valid(self):
        for path in ROOT.glob("*.json"):
            with self.subTest(path=path.name):
                self.load_json(path.name)

    def test_no_corrupted_markers_in_json(self):
        for path in ROOT.glob("*.json"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("?????", text)
                self.assertNotIn("\ufffd", text)

    def test_captcha_questions_shape(self):
        questions = self.load_json("captcha_questions.json")
        self.assertEqual(len(questions), 100)
        for question in questions:
            self.assertIn("question", question)
            self.assertIn("answers", question)
            self.assertIn("correct", question)
            self.assertEqual(len(question["answers"]), 3)
            self.assertIsInstance(question["correct"], int)
            self.assertGreaterEqual(question["correct"], 0)
            self.assertLess(question["correct"], 3)

    def test_captcha_static_rules(self):
        source = (ROOT / "captcha.py").read_text(encoding="utf-8")
        self.assertIn("CAPTCHA_TIMEOUT_SECONDS = 120", source)
        self.assertIn("chunk_buttons(buttons, 1)", source)
        self.assertEqual(source.count("asyncio.create_task(timeout"), 1)
        self.assertIn("Добро пожаловать в Группу", source)
        self.assertIn("Пусть Евгений будет на вашей стороне", source)

    def test_jokes_json_clean_and_unique(self):
        data = self.load_json("jokes.json")
        jokes = data["jokes"] if isinstance(data, dict) else data
        self.assertGreaterEqual(len(jokes), 1)
        self.assertEqual(len(jokes), len(set(jokes)))
        self.assertTrue(all(isinstance(joke, str) and joke.strip() for joke in jokes))
        self.assertFalse(any(joke.startswith("Анекдот №") for joke in jokes))
        short = self.load_json("short_jokes.json")["jokes"]
        self.assertGreaterEqual(len(short), 50)
        self.assertEqual(len(short), len(set(short)))
        dirty = self.load_json("dirty_jokes.json")["jokes"]
        self.assertGreaterEqual(len(dirty), 25)
        self.assertEqual(len(dirty), len(set(dirty)))

    def test_reply_categories_exist(self):
        replies = self.load_json("police_replies.json")
        required = {
            "call",
            "hello",
            "how_are_you",
            "what_doing",
            "thanks",
            "who_are_you",
            "sleep",
            "bored",
            "goodbye",
            "fallback",
            "frequent",
        }
        self.assertEqual(set(replies), required)
        for category, items in replies.items():
            with self.subTest(category=category):
                self.assertGreater(len(items), 0)
                self.assertTrue(all(str(item).strip() for item in items))

    def test_owner_and_yura_replies(self):
        self.assertEqual(len(self.load_json("owner_replies.json")), 100)
        self.assertEqual(len(self.load_json("yura_replies.json")), 100)

    def test_v2_content_databases(self):
        stories = self.load_json("stories.json")["stories"]
        toasts = self.load_json("toasts.json")["toasts"]
        predictions = self.load_json("predictions.json")["predictions"]
        comments = self.load_json("luck_comments.json")["comments"]
        self.assertEqual(len(stories), 1000)
        self.assertEqual(len(toasts), 1000)
        self.assertEqual(len(predictions), 1000)
        self.assertGreaterEqual(len(comments), 200)
        for name, items in {
            "stories": stories,
            "toasts": toasts,
            "predictions": predictions,
            "luck_comments": comments,
        }.items():
            with self.subTest(name=name):
                self.assertEqual(len(items), len(set(items)))
                self.assertTrue(all(isinstance(item, str) and item.strip() for item in items))


class LogicTests(unittest.TestCase):
    def test_joke_cooldown_is_per_user(self):
        jokes = importlib.reload(importlib.import_module("jokes"))
        self.assertEqual(jokes.COOLDOWN_SECONDS, 60)
        self.assertTrue(jokes.can_use(10))
        jokes.get_joke(10)
        self.assertFalse(jokes.can_use(10))
        self.assertTrue(jokes.can_use(11))

    def test_joke_recent_limit_and_empty_safety(self):
        jokes = importlib.reload(importlib.import_module("jokes"))
        self.assertEqual(jokes.RECENT_LIMIT, 500)
        original = jokes.JOKES
        try:
            jokes.JOKES = []
            self.assertIn("Анекдоты временно закончились", jokes.get_joke(1))
        finally:
            jokes.JOKES = original

    def test_v2_command_modules(self):
        modules = [
            ("stories", "STORIES", 1000, 300, "get_story"),
            ("toasts", "TOASTS", 1000, 300, "get_toast"),
            ("predictions", "PREDICTIONS", 1000, 300, "get_prediction"),
            ("luck", "COMMENTS", 200, 100, "get_luck"),
        ]
        for module_name, data_attr, expected_count, recent_limit, getter in modules:
            with self.subTest(module=module_name):
                module = importlib.reload(importlib.import_module(module_name))
                self.assertEqual(module.COOLDOWN_SECONDS, 60)
                self.assertEqual(module.RECENT_LIMIT, recent_limit)
                self.assertEqual(len(getattr(module, data_attr)), expected_count)
                self.assertTrue(module.can_use(100))
                text = getattr(module, getter)(100)
                self.assertTrue(text)
                self.assertFalse(module.can_use(100))
                self.assertTrue(module.can_use(101))

    def test_reply_selector_does_not_repeat_immediately(self):
        selector = importlib.reload(importlib.import_module("reply_selector"))
        items = ["one", "two", "three"]
        first = selector.choose("test-selector", items)
        second = selector.choose("test-selector", items)
        self.assertNotEqual(first, second)

    def test_reply_selector_cleans_old_histories(self):
        selector = importlib.reload(importlib.import_module("reply_selector"))
        selector.MAX_HISTORY_KEYS = 3
        selector.recent_indices["old-a"].append(0)
        selector.recent_indices["old-b"].append(0)
        selector.recent_indices["old-c"].append(0)
        selector.recent_indices["old-d"].append(0)
        old_stamp = time.monotonic() - selector.HISTORY_TTL_SECONDS - 1
        for key in list(selector.recent_indices):
            selector.last_used_at[key] = old_stamp
        selector.cleanup_histories()
        self.assertLessEqual(len(selector.recent_indices), 3)

    def test_dialog_memory_limits_and_expires(self):
        conversation = importlib.reload(importlib.import_module("conversation"))
        for index in range(12):
            conversation.remember(77, f"msg {index}")
        self.assertLessEqual(len(conversation.memory[77]), 8)
        conversation.last_explicit_address_at[77] = time.monotonic() - conversation.MEMORY_SECONDS - 1
        self.assertFalse(conversation.is_dialog_continuation(77))

    def test_moderation_static_rules(self):
        source = (ROOT / "moderation.py").read_text(encoding="utf-8")
        self.assertIn("OFFENSE_WINDOW_SECONDS = 10 * 60", source)
        self.assertIn("TEMP_MUTE_SECONDS = 60", source)
        self.assertNotIn("ban_chat_member", source)
        self.assertNotIn("kick", source.lower())
        tree = ast.parse(source)
        self.assertIsNotNone(tree)

    def test_moderation_offense_cleanup(self):
        aiogram_stub = types.ModuleType("aiogram")
        aiogram_stub.Bot = object
        aiogram_types_stub = types.ModuleType("aiogram.types")

        class ChatPermissions:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        aiogram_types_stub.ChatPermissions = ChatPermissions
        aiogram_types_stub.Message = object
        sys.modules.setdefault("aiogram", aiogram_stub)
        sys.modules.setdefault("aiogram.types", aiogram_types_stub)
        moderation = importlib.reload(importlib.import_module("moderation"))
        moderation.offenses[(1, 1)] = [time.monotonic() - moderation.OFFENSE_WINDOW_SECONDS - 1]
        moderation.cleanup_offenses()
        self.assertNotIn((1, 1), moderation.offenses)

    def test_bot_insult_without_comma_is_targeted(self):
        aiogram_stub = types.ModuleType("aiogram")
        aiogram_stub.Bot = object
        aiogram_types_stub = types.ModuleType("aiogram.types")
        aiogram_types_stub.ChatPermissions = object
        aiogram_types_stub.Message = object
        sys.modules.setdefault("aiogram", aiogram_stub)
        sys.modules.setdefault("aiogram.types", aiogram_types_stub)
        moderation = importlib.reload(importlib.import_module("moderation"))
        self.assertTrue(moderation.starts_with_bot_address("бот пошел нах"))
        self.assertTrue(moderation.contains_bad_words("бот пошел нах"))

    def test_short_bad_word_does_not_match_normal_words(self):
        moderation = importlib.reload(importlib.import_module("moderation"))
        self.assertFalse(moderation.contains_bad_words("Он находится дома"))
        self.assertFalse(moderation.contains_bad_words("Хорошая находка"))

    def test_owner_teasing_commands(self):
        teasing = importlib.reload(importlib.import_module("teasing"))
        self.assertEqual(teasing.parse_owner_command("начать шутить с @Example_User"), ("start", "example_user"))
        self.assertEqual(teasing.parse_owner_command("начать шутить https://t.me/BE3BASHENNIE"), ("start", "be3bashennie"))
        self.assertEqual(teasing.parse_owner_command("начни шутить @BE3BASHENNIE"), ("start", "be3bashennie"))
        self.assertEqual(teasing.parse_owner_command("перестать шутить с example_user"), ("stop", "example_user"))
        self.assertEqual(teasing.parse_owner_command("кого подкалываю"), ("list", None))
        self.assertNotIn("TEASE_COOLDOWN_SECONDS", vars(teasing))

    def test_question_reply_variety(self):
        question_chat = importlib.reload(importlib.import_module("question_chat"))
        replies = {question_chat.reply_for(500, "Почему так получилось?") for _ in range(30)}
        self.assertEqual(len(replies), 30)
        self.assertIsNone(question_chat.reply_for(500, "как дела?"))


class RenderFilesTests(unittest.TestCase):
    def test_render_files_exist_and_are_light(self):
        self.assertIn("aiogram>=3.4,<4", (ROOT / "requirements.txt").read_text(encoding="utf-8"))
        self.assertIn("python-dotenv", (ROOT / "requirements.txt").read_text(encoding="utf-8"))
        self.assertEqual((ROOT / "Procfile").read_text(encoding="utf-8").strip(), "web: python main.py")
        self.assertIn("startCommand: python main.py", (ROOT / "render.yaml").read_text(encoding="utf-8"))

    def test_no_token_literal_in_project_files(self):
        for path in ROOT.glob("*"):
            if path.resolve() == Path(__file__).resolve():
                continue
            if path.is_file() and path.suffix in {".py", ".json", ".txt", ".yaml", ".md", ""}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                with self.subTest(path=path.name):
                    self.assertNotIn("ВСТАВЬ_ТОКЕН", text)
                    self.assertNotIn("bot_token_here:", text.lower())


if __name__ == "__main__":
    unittest.main()
