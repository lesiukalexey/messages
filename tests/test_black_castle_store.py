import unittest
from unittest.mock import MagicMock, patch

from messages.black_castle_store import BlackCastleStore


class BlackCastleStoreTest(unittest.TestCase):
    def test_get_book_page_queries_only_columns_in_book_pages_schema(self):
        store = object.__new__(BlackCastleStore)
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = {
            "page_key": "preface",
            "title": "Книга-игра",
            "body": "Предисловие",
        }
        store.connection = MagicMock()
        store.connection.cursor.return_value = cursor

        with patch.object(store, "ensure_connected"):
            page = store.get_book_page("preface")

        query = cursor.execute.call_args.args[0]
        self.assertIn("SELECT page_key, title, body FROM book_pages", query)
        self.assertNotIn("photo_file_id", query)
        self.assertEqual(page["body"], "Предисловие")


if __name__ == "__main__":
    unittest.main()
