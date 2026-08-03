import unittest

import challenges
import conversation
import dialog_database
import riddles


class ConversationV2Tests(unittest.TestCase):
    def setUp(self):
        conversation.memory.clear()
        conversation.last_explicit_address_at.clear()
        conversation.consecutive_addresses.clear()

    def test_common_phrases_work_when_bot_is_addressed(self):
        for phrase in ("привет", "спасибо", "как дела", "кто ты", "пока", "что умеешь"):
            with self.subTest(phrase=phrase):
                self.assertTrue(conversation.should_respond(100, phrase, addressed=True))
                self.assertTrue(conversation.reply_for(100, phrase, addressed=True))

    def test_bot_never_enters_unaddressed_conversation(self):
        for phrase in ("привет", "спасибо", "как дела", "боты бывают полезны"):
            with self.subTest(phrase=phrase):
                self.assertFalse(conversation.should_respond(101, phrase, addressed=False))
                self.assertIsNone(conversation.reply_for(101, phrase, addressed=False))

    def test_unrelated_message_does_not_start_dialog(self):
        self.assertFalse(conversation.should_respond(200, "купил хлеб и молоко"))

    def test_immediate_reply_is_not_repeated(self):
        first = conversation.reply_for(300, "привет", addressed=True)
        second = conversation.reply_for(300, "привет", addressed=True)
        self.assertNotEqual(first, second)

    def test_databases_have_requested_scale(self):
        for category in ("hello", "thanks", "how_are_you", "what_doing", "who_are_you", "bored", "goodbye"):
            self.assertGreaterEqual(len(dialog_database.RESPONSES[category]), 20)
        self.assertGreaterEqual(len(challenges.CHALLENGES), 20)
        from fun_responses import HUMOR_RESPONSES
        self.assertGreaterEqual(len(HUMOR_RESPONSES), 300)
        self.assertEqual(set(riddles.RIDDLES), {"обычные", "смешные", "логические", "с_подвохом"})
        self.assertTrue(all(len(items) >= 12 for items in riddles.RIDDLES.values()))

    def test_riddle_answer_is_scoped_to_author_and_chat(self):
        riddles.new_riddle(-100, 10, "обычная")
        self.assertIsNone(riddles.check_answer(-100, 11, "ёлка"))
        self.assertIsNone(riddles.check_answer(-200, 10, "ёлка"))
        self.assertIsNotNone(riddles.check_answer(-100, 10, "ответ"))


if __name__ == "__main__":
    unittest.main()
