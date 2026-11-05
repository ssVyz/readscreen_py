"""Milestone 1.1: version, DB schema, settings, parameter validation."""

import re
import unittest

from readscreen import __version__, settings, store
from readscreen.params import Param, ParamError, coerce, parse_assignment, validate
from readscreen.paths import ROOT

from .helpers import TempEnvCase


class VersionTest(unittest.TestCase):
    def test_versions_match(self):
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        top = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M).group(1)
        pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
        self.assertEqual(top, __version__)
        self.assertEqual(pyproject.group(1), __version__)


class StoreTest(TempEnvCase):
    def test_schema_and_wal(self):
        conn = store.connect(self.env.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
            self.assertEqual(int(version), store.SCHEMA_VERSION)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM worker").fetchone()[0], 1)
        finally:
            conn.close()
        store.init_db(self.env.db_path)  # idempotent

    def test_newer_schema_refused(self):
        conn = store.connect(self.env.db_path)
        conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
        conn.close()
        with self.assertRaises(store.SchemaError):
            store.init_db(self.env.db_path)

    def test_transaction_rollback(self):
        conn = store.connect(self.env.db_path)
        try:
            with self.assertRaises(RuntimeError):
                with store.transaction(conn):
                    conn.execute("INSERT INTO settings (key, value) VALUES ('x', '1')")
                    raise RuntimeError("boom")
            self.assertIsNone(conn.execute("SELECT * FROM settings WHERE key = 'x'").fetchone())
        finally:
            conn.close()


class SettingsTest(TempEnvCase):
    def test_defaults_and_set(self):
        values = self.api.get_settings()
        self.assertGreaterEqual(values["threads"], 1)
        self.assertEqual(self.api.set_setting("threads", "4"), 4)
        self.assertEqual(self.api.get_settings()["threads"], 4)
        self.api.set_setting("threads", None)
        self.assertEqual(self.api.get_settings()["threads"], values["threads"])

    def test_stage_defaults(self):
        self.assertEqual(self.api.set_setting("default.map.sensitivity", "normal"), "normal")
        self.assertEqual(settings.stage_overrides(self.api.get_settings(), "map"), {"sensitivity": "normal"})

    def test_invalid(self):
        from readscreen.api import ApiError

        for key, value in (("nope", 1), ("threads", 0), ("default.map.nope", 1), ("default.nostage.x", 1),
                           ("default.map.sensitivity", "max"), ("default.select.confirmed", True)):
            with self.subTest(key=key), self.assertRaises(ApiError):
                self.api.set_setting(key, value)


class ParamsTest(unittest.TestCase):
    def test_coerce(self):
        self.assertIs(coerce("b", Param(bool, False), "yes"), True)
        self.assertIs(coerce("b", Param(bool, False), "off"), False)
        self.assertEqual(coerce("i", Param(int, 1), "2_000"), 2000)
        self.assertEqual(coerce("f", Param(float, 1.0), "1e-10"), 1e-10)
        self.assertEqual(coerce("l", Param(list, []), "-k 15 -w 5"), ["-k", "15", "-w", "5"])
        self.assertEqual(coerce("l", Param(list, []), '["-a", "b c"]'), ["-a", "b c"])
        self.assertIsNone(coerce("i", Param(int, None), "none"))
        for p, v in ((Param(int, 1), "x"), (Param(int, 1), 1.5), (Param(bool, False), "maybe"),
                     (Param(int, 1, min=1), 0), (Param(str, "a", choices=("a", "b")), "c")):
            with self.subTest(v=v), self.assertRaises(ParamError):
                coerce("p", p, v)

    def test_validate(self):
        schema = {"a": Param(int, 1), "b": Param(str, lambda pl: "long" if pl == "ont" else "short"),
                  "c": Param(bool, False, internal=True)}
        self.assertEqual(validate(schema, {}, "ont"), {"a": 1, "b": "long", "c": False})
        self.assertEqual(validate(schema, {"a": "5"}, "illumina")["a"], 5)
        self.assertEqual(validate(schema, {"a": None}, None, base={"a": 7, "b": "x", "c": False})["a"], 1)
        with self.assertRaises(ParamError):
            validate(schema, {"zzz": 1}, None)
        with self.assertRaises(ParamError):
            validate(schema, {"c": True}, None)
        self.assertTrue(validate(schema, {"c": True}, None, allow_internal=True)["c"])

    def test_parse_assignment(self):
        self.assertEqual(parse_assignment("map.sensitivity=normal"), ("map.sensitivity", "normal"))
        self.assertEqual(parse_assignment("x=[1, 2]"), ("x", [1, 2]))
        with self.assertRaises(ParamError):
            parse_assignment("novalue")


if __name__ == "__main__":
    unittest.main()
