"""Base de licences de test : SQLite avec la même interface que psycopg2 (%s -> ?)."""
import datetime
import sqlite3

import license_manager
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


def insert_license(path, email, key, active=True, days=1094):
    """Ajoute une ligne de licence telle qu'une ancienne version ou un geste manuel aurait pu la laisser : sans passer
    par LicenseManager, donc sans normaliser l'e-mail (« Client@… » à côté de « client@… » est un doublon réel)."""
    expires = license_manager._utcnow() + datetime.timedelta(days=days)
    raw(path, "INSERT INTO licenses (email, key, created, expires, active) VALUES (?, ?, ?, ?, ?)",
        (email, key, "2026-10-07 10:00:00", str(expires), int(active)))
