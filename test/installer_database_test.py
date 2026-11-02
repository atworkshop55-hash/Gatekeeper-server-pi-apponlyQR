import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch


loader = importlib.machinery.SourceFileLoader(
    "installer_database", str(Path(__file__).parent.parent / "scripts" / "gatekeeper-install"))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)

    def test_fresh_schema_matches_runtime(self):
        result = m.prepare_database(self.db)
        self.assertEqual(result["schema_version"], 1)
        for table, columns in m.DATABASE_COLUMNS.items():
            self.assertEqual(tuple(row[1] for row in self.db.execute(
                f"PRAGMA table_info({table})")), columns)
        self.assertEqual(dict(self.db.execute("SELECT key,value FROM settings")), m.DATABASE_DEFAULTS)
        for table in ("users", "zones", "gates", "audit_logs"):
            self.assertEqual(self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        self.assertFalse(result["ready_for_deployment"])
        self.assertFalse(result["network_configuration_seeded"])

    def test_rerun_preserves_all_existing_data(self):
        m.prepare_database(self.db)
        self.db.execute("INSERT INTO users VALUES ('operator', 'test-only', 'admin')")
        self.db.execute("INSERT INTO zones VALUES ('z1','Test zone')")
        self.db.execute("INSERT INTO gates VALUES ('g1','Test gate','10.1.1.2','',0,'z1')")
        self.db.execute("INSERT INTO audit_logs(category,user,type,text) VALUES ('SYSTEM','operator','info','test')")
        self.db.execute("UPDATE settings SET value='Existing site' WHERE key='siteName'")
        self.db.execute("INSERT INTO settings VALUES ('custom','preserved')")
        self.db.commit()
        before = list(self.db.iterdump())
        m.prepare_database(self.db)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_known_unversioned_schema_adopted_without_data_loss(self):
        m.prepare_database(self.db)
        self.db.execute("PRAGMA user_version=0")
        self.db.commit()
        before = list(self.db.iterdump())
        m.prepare_database(self.db)
        self.assertEqual(list(self.db.iterdump()), before)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_unknown_version_refused(self):
        self.db.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ValueError, "Unknown database version"):
            m.prepare_database(self.db)
        self.assertEqual(self.db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0], 0)

    def test_partial_database_not_silently_repaired(self):
        self.db.execute("CREATE TABLE users (username TEXT PRIMARY KEY, password TEXT, role TEXT)")
        before = list(self.db.iterdump())
        with self.assertRaisesRegex(ValueError, "exactly the known"):
            m.prepare_database(self.db)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_incompatible_constraints_refused(self):
        for table, definition in m.DATABASE_TABLES.items():
            if table == "users":
                definition = "username TEXT, password TEXT, role TEXT"
            self.db.execute(f"CREATE TABLE {table} ({definition})")
        before = list(self.db.iterdump())
        with self.assertRaisesRegex(ValueError, "column definitions"):
            m.prepare_database(self.db)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_duplicate_users_roll_back_index_creation(self):
        for table, definition in m.DATABASE_TABLES.items():
            self.db.execute(f"CREATE TABLE {table} ({definition})")
        self.db.executemany("INSERT INTO users VALUES (?, 'test', 'operator')",
                            [("Alice",), ("alice",)])
        self.db.commit()
        before = list(self.db.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            m.prepare_database(self.db)
        self.assertEqual(list(self.db.iterdump()), before)
        self.assertEqual(self.db.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_incorrect_named_index_refused(self):
        m.prepare_database(self.db)
        self.db.execute("DROP INDEX ux_users_username_ci")
        self.db.execute("CREATE INDEX ux_users_username_ci ON users(role)")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "index definition"):
            m.prepare_database(self.db)

    def test_trigger_refused(self):
        m.prepare_database(self.db)
        self.db.execute("CREATE TRIGGER unexpected AFTER INSERT ON users BEGIN SELECT 1; END")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "view/trigger"):
            m.prepare_database(self.db)

    def test_caller_transaction_is_not_committed(self):
        m.prepare_database(self.db)
        self.db.execute("INSERT INTO settings VALUES ('uncommitted','test')")
        with self.assertRaisesRegex(ValueError, "active transaction"):
            m.prepare_database(self.db)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertIsNone(self.db.execute("SELECT value FROM settings WHERE key='uncommitted'").fetchone())

    def test_audit_timestamp_and_identity_auto_generated(self):
        m.prepare_database(self.db)
        self.db.execute("INSERT INTO audit_logs(category,user,type,text) VALUES ('GATE_STATE','System','info','test')")
        row = self.db.execute("SELECT id,timestamp FROM audit_logs").fetchone()
        self.assertEqual(row[0], 1)
        self.assertRegex(row[1], r"^\d{4}-\d{2}-\d{2} ")

    def test_rehearsal_cli_never_collects_live_inventory(self):
        with patch.object(m, "collect", side_effect=AssertionError("Must not inspect live database")), patch(
                "builtins.print") as output:
            self.assertEqual(m.main(["--database-rehearsal", "--json"]), 0)
        result = json.loads(output.call_args.args[0])
        self.assertTrue(result["rerun_verified"])
        self.assertTrue(result["temporary_database_removed_on_exit"])
        self.assertEqual(result["empty_table_counts"], dict.fromkeys(
            ("users", "gates", "zones", "audit_logs"), 0))


if __name__ == "__main__":
    unittest.main()
