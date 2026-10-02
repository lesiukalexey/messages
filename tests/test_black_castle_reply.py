import unittest
from pathlib import Path

from messages.black_castle_reply import BlackCastleReplyAlgorithm, BlackCastleReplyError
from messages.game_routing import selected_game_folder


SOURCE = next(
    (
        path for path in (
            Path("/game/games/black_castle/src/reply_algorithm.py"),
            Path("/var/www/game/games/black_castle/src/reply_algorithm.py"),
        ) if path.exists()
    ),
    Path("/game/games/black_castle/src/reply_algorithm.py"),
)


class BlackCastleReplyTests(unittest.TestCase):
    def test_black_castle_wins_if_contact_is_in_both_folders(self) -> None:
        self.assertEqual(selected_game_folder(True, True), "BlackCastle")
        self.assertEqual(selected_game_folder(True, False), "BlackCastle")
        self.assertEqual(selected_game_folder(False, True), "Game")
        self.assertIsNone(selected_game_folder(False, False))

    @unittest.skipUnless(SOURCE.exists(), "Game source is mounted only in the active workspace")
    def test_fixed_reply_is_exact_for_text_and_empty_message(self) -> None:
        algorithm = BlackCastleReplyAlgorithm(SOURCE)
        self.assertEqual(algorithm.reply("personal", 123, "Привет"), "black")
        self.assertEqual(algorithm.reply("personal2", 123, ""), "black")

    @unittest.skipUnless(SOURCE.exists(), "Game source is mounted only in the active workspace")
    def test_unsupported_account_fails_closed(self) -> None:
        with self.assertRaises(BlackCastleReplyError):
            BlackCastleReplyAlgorithm(SOURCE).reply("unknown", 123, "Привет")


if __name__ == "__main__":
    unittest.main()
