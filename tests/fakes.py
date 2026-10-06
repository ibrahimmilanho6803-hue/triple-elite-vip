"""Base de licences de test : SQLite avec la même interface que psycopg2 (%s -> ?)."""
import sqlite3

from license_manager import LicenseManager


class _Cursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        self._cursor.execute(sql.replace("%s", "?"), tuple(params))
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _Connection:
    def __init__(self, path):
        self._conn = sqlite3.connect(path, timeout=10)

    def cursor(self):
        return _Cursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def sqlite_license_manager(path):
    return LicenseManager(db_url="sqlite", connect=lambda: _Connection(path))


def raw(path, sql, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(sql, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()
