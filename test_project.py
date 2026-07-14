import ast
import asyncio
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
        source = (ROOT / "join_manager.py").read_text(encoding="utf-8")
        captcha_source = (ROOT / "captcha.py").read_text(encoding="utf-8")
        self.assertIn("CAPTCHA_TIMEOUT_SECONDS = 120", source)
        self.assertIn("chunk_buttons(buttons, 1)", source)
        self.assertIn("asyncio.create_task(timeout_worker", source)
        forbidden_state = "passed" + "_users"
        self.assertNotIn(forbidden_state, captcha_source + source)
        self.assertIn("\u0414\u043e\u0431\u0440\u043e \u043f\u043e\u0436\u0430\u043b\u043e\u0432\u0430\u0442\u044c \u0432 \u0413\u0440\u0443\u043f\u043f\u0443", source)
        self.assertIn("\u041f\u0443\u0441\u0442\u044c \u0415\u0432\u0433\u0435\u043d\u0438\u0439 \u0431\u0443\u0434\u0435\u0442 \u043d\u0430 \u0432\u0430\u0448\u0435\u0439 \u0441\u0442\u043e\u0440\u043e\u043d\u0435", source)
        self.assertIn("STATE_PATH", source)
        self.assertIn("restore_pending", source)
        self.assertIn("JOIN DETECTED", source)
        self.assertIn("RESTRICTED", source)
        self.assertIn("CAPTCHA SENT", source)
        self.assertIn("PASSED", source)
        self.assertIn("TIMEOUT", source)
        self.assertIn("KICKED", source)

    def test_jokes_json_clean_and_unique(self):
        data = self.load_json("jokes.json")
        jokes = data["jokes"] if isinstance(data, dict) else data
        self.assertGreaterEqual(len(jokes), 1)
        self.assertEqual(len(jokes), len(set(jokes)))
        self.assertTrue(all(isinstance(joke, str) and joke.strip() for joke in jokes))
        self.assertFalse(any(joke.startswith("Анекдот №") for joke in jokes))

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

    def test_moderation_is_the_only_bad_language_filter(self):
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        conversation_source = (ROOT / "conversation.py").read_text(encoding="utf-8")
        project_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in ROOT.glob("*.py")
            if path.name != "test_project.py"
        )

        self.assertIn("moderation.handle_bad_language", main_source)
        self.assertNotIn("conversation.looks_rude", main_source)
        self.assertNotIn("looks_rude", conversation_source)
        self.assertNotIn("RUDE_MARKERS", conversation_source)
        self.assertNotIn("RUDE_REPLIES", conversation_source)
        self.assertNotIn("rude_reply", conversation_source)
        self.assertEqual(project_sources.count("async def handle_bad_language"), 1)

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

    def test_moderation_ignores_safe_phrases_and_discussions(self):
        aiogram_stub = types.ModuleType("aiogram")
        aiogram_stub.Bot = object
        aiogram_types_stub = types.ModuleType("aiogram.types")

        class ChatPermissions:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        aiogram_types_stub.ChatPermissions = ChatPermissions
        aiogram_types_stub.Message = object
        sys.modules["aiogram"] = aiogram_stub
        sys.modules["aiogram.types"] = aiogram_types_stub
        moderation = importlib.reload(importlib.import_module("moderation"))

        safe_phrases = [
            "\u041d\u0435\u043f\u043b\u043e\u0445\u043e \u043a\u0441\u0442\u0430\u0442\u0438",
            "\u041e\u043d \u0432\u0438\u0434\u0438\u0442 \u0441\u043a\u0440\u044b\u0442\u044b\u0439 \u043c\u0430\u0442",
            "\u042d\u0442\u043e \u043d\u0435 \u043c\u0430\u0442",
            "\u041e\u0431\u0441\u0443\u0436\u0434\u0430\u0435\u043c \u043f\u0440\u0430\u0432\u0438\u043b\u0430",
        ]
        for phrase in safe_phrases:
            with self.subTest(phrase=phrase):
                self.assertFalse(moderation.contains_bad_words(phrase))

        self.assertTrue(moderation.contains_bad_words("\u0442\u044b \u0434\u0443\u0440\u0430\u043a"))
        self.assertTrue(moderation.is_meta_discussion("\u041e\u0431\u0441\u0443\u0436\u0434\u0430\u0435\u043c \u0441\u043b\u043e\u0432\u043e \u0434\u0443\u0440\u0430\u043a"))
        self.assertEqual(moderation.matched_bad_markers("Неплохо кстати"), [])
        self.assertEqual(moderation.matched_bad_markers("ты дурак"), ["дурак"])

    def test_moderation_full_handler_ignores_safe_phrases(self):
        aiogram_stub = types.ModuleType("aiogram")
        aiogram_stub.Bot = object
        aiogram_types_stub = types.ModuleType("aiogram.types")

        class ChatPermissions:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        aiogram_types_stub.ChatPermissions = ChatPermissions
        aiogram_types_stub.Message = object
        sys.modules["aiogram"] = aiogram_stub
        sys.modules["aiogram.types"] = aiogram_types_stub
        moderation = importlib.reload(importlib.import_module("moderation"))

        class FakeUser:
            id = 10

        class FakeChat:
            id = -100

        class FakeMessage:
            def __init__(self, text):
                self.text = text
                self.caption = None
                self.from_user = FakeUser()
                self.chat = FakeChat()
                self.reply_to_message = None
                self.replies = []

            async def reply(self, text):
                self.replies.append(text)

        async def run_checks():
            safe_phrases = [
                "\u041d\u0435\u043f\u043b\u043e\u0445\u043e \u043a\u0441\u0442\u0430\u0442\u0438",
                "\u041e\u043d \u0432\u0438\u0434\u0438\u0442 \u0441\u043a\u0440\u044b\u0442\u044b\u0439 \u043c\u0430\u0442",
                "\u042d\u0442\u043e \u043d\u0435 \u043c\u0430\u0442",
                "\u041e\u0431\u0441\u0443\u0436\u0434\u0430\u0435\u043c \u043f\u0440\u0430\u0432\u0438\u043b\u0430",
            ]
            for phrase in safe_phrases:
                message = FakeMessage(phrase)
                self.assertFalse(await moderation.handle_bad_language(object(), message, False))
                self.assertEqual(message.replies, [])

        asyncio.run(run_checks())

    def test_moderation_warns_only_for_targeted_insult(self):
        aiogram_stub = types.ModuleType("aiogram")
        aiogram_stub.Bot = object
        aiogram_types_stub = types.ModuleType("aiogram.types")

        class ChatPermissions:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        aiogram_types_stub.ChatPermissions = ChatPermissions
        aiogram_types_stub.Message = object
        sys.modules["aiogram"] = aiogram_stub
        sys.modules["aiogram.types"] = aiogram_types_stub
        moderation = importlib.reload(importlib.import_module("moderation"))

        class FakeUser:
            id = 10

        class FakeChat:
            id = -100

        class FakeMessage:
            def __init__(self, text):
                self.text = text
                self.caption = None
                self.from_user = FakeUser()
                self.chat = FakeChat()
                self.reply_to_message = None
                self.replies = []

            async def reply(self, text):
                self.replies.append(text)

        async def run_checks():
            neutral = FakeMessage("\u0434\u0443\u0440\u0430\u043a")
            self.assertFalse(await moderation.handle_bad_language(object(), neutral, False))
            self.assertEqual(neutral.replies, [])

            discussion = FakeMessage("\u041e\u0431\u0441\u0443\u0436\u0434\u0430\u0435\u043c \u0441\u043b\u043e\u0432\u043e \u0434\u0443\u0440\u0430\u043a")
            self.assertFalse(await moderation.handle_bad_language(object(), discussion, False))
            self.assertEqual(discussion.replies, [])

            targeted = FakeMessage("\u0442\u044b \u0434\u0443\u0440\u0430\u043a")
            self.assertTrue(await moderation.handle_bad_language(object(), targeted, False))
            self.assertEqual(len(targeted.replies), 1)

        asyncio.run(run_checks())


class JoinManagerTests(unittest.IsolatedAsyncioTestCase):
    def install_aiogram_stubs(self):
        aiogram_stub = types.ModuleType("aiogram")
        aiogram_stub.Bot = object
        aiogram_types_stub = types.ModuleType("aiogram.types")

        class ChatPermissions:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class InlineKeyboardButton:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class InlineKeyboardMarkup:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        aiogram_types_stub.CallbackQuery = object
        aiogram_types_stub.ChatMemberUpdated = object
        aiogram_types_stub.ChatPermissions = ChatPermissions
        aiogram_types_stub.InlineKeyboardButton = InlineKeyboardButton
        aiogram_types_stub.InlineKeyboardMarkup = InlineKeyboardMarkup
        aiogram_types_stub.Message = object
        aiogram_types_stub.User = object
        sys.modules["aiogram"] = aiogram_stub
        sys.modules["aiogram.types"] = aiogram_types_stub

    async def test_mass_join_50_users_are_muted_and_get_captcha(self):
        self.install_aiogram_stubs()
        join_manager = importlib.reload(importlib.import_module("join_manager"))
        join_manager.STATE_PATH = ROOT / "test_join_state.json"
        join_manager.pending.clear()
        join_manager.processing_users.clear()
        join_manager.timeout_tasks.clear()

        class FakeMessage:
            def __init__(self, message_id):
                self.message_id = message_id

        class FakeBot:
            def __init__(self):
                self.actions = []
                self.next_message_id = 100

            async def restrict_chat_member(self, chat_id, user_id, permissions):
                self.actions.append(("restrict", chat_id, user_id, permissions.kwargs))

            async def send_message(self, chat_id, text, reply_markup=None):
                self.actions.append(("send_captcha", chat_id, text, reply_markup))
                self.next_message_id += 1
                return FakeMessage(self.next_message_id)

            async def ban_chat_member(self, chat_id, user_id):
                self.actions.append(("ban", chat_id, user_id))

            async def unban_chat_member(self, chat_id, user_id):
                self.actions.append(("unban", chat_id, user_id))

        class FakeUser:
            def __init__(self, user_id):
                self.id = user_id
                self.username = f"user{user_id}"
                self.is_bot = False

        bot = FakeBot()
        users = [FakeUser(10_000 + index) for index in range(50)]
        for user in users:
            await join_manager.start_for_user(bot, -100, user)

        self.assertEqual(len(join_manager.pending), 50)
        self.assertEqual(len([action for action in bot.actions if action[0] == "restrict"]), 50)
        self.assertEqual(len([action for action in bot.actions if action[0] == "send_captcha"]), 50)
        for user in users:
            user_actions = [action[0] for action in bot.actions if len(action) > 2 and action[2] == user.id]
            self.assertEqual(user_actions[0], "restrict")

        for user in users:
            join_manager.clear_user(-100, user.id)
        if join_manager.STATE_PATH.exists():
            join_manager.STATE_PATH.unlink()


class RenderFilesTests(unittest.TestCase):
    def test_render_files_exist_and_are_light(self):
        self.assertIn("aiogram>=3.4,<4", (ROOT / "requirements.txt").read_text(encoding="utf-8"))
        self.assertIn("python-dotenv", (ROOT / "requirements.txt").read_text(encoding="utf-8"))
        self.assertIn((ROOT / "Procfile").read_text(encoding="utf-8").strip(), {"web: python main.py", "worker: python main.py"})
        self.assertIn("startCommand: python main.py", (ROOT / "render.yaml").read_text(encoding="utf-8"))

    def test_no_token_literal_in_project_files(self):
        for path in ROOT.glob("*"):
            if path.is_file() and path.suffix in {".py", ".json", ".txt", ".yaml", ".md", ""}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                with self.subTest(path=path.name):
                    token_placeholder = "\u0412\u0421\u0422\u0410\u0412\u042c" + "_\u0422\u041e\u041a\u0415\u041d"
                    self.assertNotIn(token_placeholder, text)
                    token_example = "bot" + "_token" + "_here:"
                    self.assertNotIn(token_example, text.lower())


if __name__ == "__main__":
    unittest.main()
