"""Single SQLite connection helper used by the web app and parser."""
import pathlib
import sqlite3


def connect(path: pathlib.Path) -> sqlite3.Connection:
    """Open a SQLite connection with ``sqlite3.Row`` row factory."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
