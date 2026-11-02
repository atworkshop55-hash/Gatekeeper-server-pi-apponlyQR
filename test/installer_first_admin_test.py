import importlib.machinery
import importlib.util
from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).parent.parent


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / "scripts" / file))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


m = load("first_admin_test", "gatekeeper-first-admin")
installer = load("first_admin_database", "gatekeeper-install")


class FirstAdminTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        installer.prepare_database(self.db)

    def create(self):
        return m.create_first_admin(self.db, "admin", "long-test-password", "Test site")

    def test_creates_only_app_admin_and_secret_free_audit(self):
        result = self.create()
        self.assertEqual(self.db.execute(
            "SELECT username,role FROM users").fetchall(), [("admin", "admin")])
        self.assertFalse(result["linux_user_created"])
        self.assertFalse(result["tablet_registered"])
        self.assertFalse(result["app_flows_active"])
        self.assertFalse(result["ready_for_deployment"])
        self.assertEqual(self.db.execute(
            "SELECT value FROM settings WHERE key='siteName'").fetchone()[0], "Test site")
        audit = self.db.execute("SELECT category,user,text FROM audit_logs").fetchone()
        self.assertEqual(audit[:2], ("AUTH", "admin"))
        self.assertNotIn("long-test-password", str(audit))

    def test_existing_user_preserved_and_no_duplicate_audit(self):
        self.create()
        before = list(self.db.iterdump())
        with self.assertRaisesRegex(ValueError, "already exist"):
            self.create()
        self.assertEqual(list(self.db.iterdump()), before)

    def test_deleted_users_do_not_reopen_first_setup(self):
        self.create()
        self.db.execute("DELETE FROM users")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "already recorded"):
            self.create()

    def test_unknown_version_and_missing_schema_refused(self):
        self.db.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ValueError, "version"):
            self.create()
        self.db.execute("PRAGMA user_version=1")
        self.db.execute("DROP TABLE audit_logs")
        with self.assertRaisesRegex(ValueError, "schema"):
            self.create()

    def test_bad_fields_refused(self):
        for username, password, site in (
            ("__proto__", "valid-test-password", "Site"),
            ("constructor", "valid-test-password", "Site"),
            ("admin;reboot", "valid-test-password", "Site"),
            ("admin", "short", "Site"),
            ("admin", " leading-test-password", "Site"),
            ("admin", "valid-test-password", ""),
            ("admin", "valid-test-password", "A\nB"),
        ):
            with self.subTest(username=username, site=site), self.assertRaises(ValueError):
                m.create_first_admin(self.db, username, password, site)
        self.assertEqual(self.db.execute("SELECT count(*) FROM users").fetchone()[0], 0)

    def test_missing_site_default_rolls_back_account(self):
        self.db.execute("DELETE FROM settings WHERE key='siteName'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.create()
        self.assertEqual(self.db.execute("SELECT count(*) FROM users").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM audit_logs").fetchone()[0], 0)

    def test_parameterized_quoted_values(self):
        m.create_first_admin(self.db, "admin", "quoted'password-test", "Keeper's site")
        self.assertEqual(self.db.execute("SELECT password FROM users").fetchone()[0], "quoted'password-test")
        self.assertEqual(self.db.execute(
            "SELECT value FROM settings WHERE key='siteName'").fetchone()[0], "Keeper's site")

    def test_caller_transaction_preserved(self):
        self.db.execute("INSERT INTO settings VALUES('custom','test')")
        with self.assertRaisesRegex(ValueError, "caller transaction"):
            self.create()
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()

    def test_password_reset_preserves_role_site_marker_and_other_data(self):
        self.create()
        self.db.execute("INSERT INTO users VALUES('operator','operator-password','operator')")
        self.db.execute("INSERT INTO zones VALUES('zone1','Test zone')")
        self.db.commit()
        settings = self.db.execute("SELECT * FROM settings").fetchall()
        result = m.reset_admin_password(self.db, "admin", "new'long-test-password")
        self.assertEqual(self.db.execute("SELECT password,role FROM users WHERE username='admin'").fetchone(),
                         ("new'long-test-password", "admin"))
        self.assertEqual(self.db.execute("SELECT * FROM settings").fetchall(), settings)
        self.assertEqual(self.db.execute("SELECT * FROM zones").fetchall(), [("zone1", "Test zone")])
        self.assertEqual(self.db.execute("SELECT password FROM users WHERE username='operator'").fetchone(),
                         ("operator-password",))
        self.assertFalse(result["tablet_enrollment_changed"])
        self.assertNotIn("new'long-test-password", str(self.db.execute("SELECT text FROM audit_logs").fetchall()))

    def test_reset_refuses_wrong_user_short_password_and_multiple_admins(self):
        self.create()
        before = list(self.db.iterdump())
        for username, password in (("other_user", "long-new-password"), ("admin", "short")):
            with self.assertRaises(ValueError):
                m.reset_admin_password(self.db, username, password)
            self.assertEqual(list(self.db.iterdump()), before)
        self.db.execute("INSERT INTO users VALUES('other_admin','test-password','admin')")
        self.db.commit()
        before = list(self.db.iterdump())
        with self.assertRaisesRegex(ValueError, "exactly one"):
            m.reset_admin_password(self.db, "admin", "long-new-password")
        self.assertEqual(list(self.db.iterdump()), before)

    def test_reset_refuses_missing_completion_marker(self):
        self.create()
        self.db.execute("DELETE FROM settings WHERE key='firstAdminProvisionedAt'")
        self.db.commit()
        before = list(self.db.iterdump())
        with self.assertRaisesRegex(ValueError, "marker"):
            m.reset_admin_password(self.db, "admin", "long-new-password")
        self.assertEqual(list(self.db.iterdump()), before)

    def test_reset_preserves_caller_transaction(self):
        self.create()
        self.db.execute("INSERT INTO settings VALUES('custom','test')")
        with self.assertRaisesRegex(ValueError, "caller transaction"):
            m.reset_admin_password(self.db, "admin", "long-new-password")
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()

    def test_recovery_renames_to_default_admin_without_rewriting_history(self):
        self.create()
        self.db.execute("UPDATE users SET username='original_admin' WHERE username='admin'")
        self.db.commit()
        before = self.db.execute("SELECT * FROM audit_logs").fetchall()
        result = m.reset_admin_password(self.db, "original_admin", "new-test-password", use_default_admin=True)
        self.assertEqual(self.db.execute("SELECT username,password,role FROM users").fetchall(),
                         [("admin", "new-test-password", "admin")])
        self.assertTrue(result["username_changed"])
        self.assertFalse(result["tablet_enrollment_changed"])
        after = self.db.execute("SELECT * FROM audit_logs ORDER BY id").fetchall()
        self.assertEqual(after[:-1], before)
        self.assertEqual(after[-1][3], "admin")
        self.assertNotIn("new-test-password", str(after))

    def test_recovery_refuses_existing_admin_name_collision(self):
        self.create()
        self.db.execute("UPDATE users SET username='original_admin' WHERE username='admin'")
        self.db.execute("INSERT INTO users VALUES('ADMIN','operator-password','operator')")
        self.db.commit()
        before = list(self.db.iterdump())
        with self.assertRaisesRegex(ValueError, "already used"):
            m.reset_admin_password(self.db, "original_admin", "new-test-password", use_default_admin=True)
        self.assertEqual(list(self.db.iterdump()), before)


if __name__ == "__main__":
    unittest.main()
