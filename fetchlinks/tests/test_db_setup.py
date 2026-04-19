import sqlite3
import tempfile
import unittest
from pathlib import Path

import db_setup


class DbSetupTests(unittest.TestCase):
    def test_db_initial_setup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_dir = Path(tmp_dir) / "db"
            db_name = "fetchlinks.db"
            db_path = db_dir / db_name

            db_setup.db_initial_setup(str(db_dir), db_name)
            db_setup.db_initial_setup(str(db_dir), db_name)

            self.assertTrue(db_path.exists())

            with sqlite3.connect(db_path) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }

            self.assertIn("posts", tables)
            self.assertIn("urls", tables)


if __name__ == "__main__":
    unittest.main()
