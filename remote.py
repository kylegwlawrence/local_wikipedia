"""Transparent SQL-over-HTTP proxy for talking to a remote local_wikipedia DB.

When a wiki is configured via ``WIKI_REMOTE_<WIKI>`` (see ``paths.remote_url_for``),
``app.deps.connect`` returns a :class:`RemoteSqliteConnection` instead of a
``sqlite3.Connection``. The proxy quacks like the local connection — same
``execute / fetchone / fetchall / commit / close`` surface — so call sites in
``app.helpers`` and friends keep working unchanged.

The wire contract is one endpoint, ``POST /api/sql/{wiki}``, documented in
``docs/apis/REMOTE_WIKI_API.md``. Each statement is executed in its own
implicit transaction on the remote (autocommit) — cross-statement
``BEGIN/COMMIT`` is *not* supported.

Lives at the project root (not under ``app/``) so worker subprocesses can
import it without pulling in FastAPI.
"""

from collections.abc import Iterator, Sequence
from typing import Any

import httpx

_DEFAULT_TIMEOUT = 30.0


class RemoteSqliteError(Exception):
    """Raised when the remote SQL endpoint returns a non-2xx unexpectedly."""


class RemoteRow:
    """Dict + sequence row, mirroring the parts of ``sqlite3.Row`` we use.

    Supports ``row["col"]`` (case-insensitive, like ``sqlite3.Row``),
    ``row[i]``, ``row.keys()``, ``len(row)``, iteration over values, and
    ``dict(row)``. Anything beyond that is unsupported on purpose — we'd
    rather fail loudly than silently diverge from the local-connection
    behaviour.
    """

    __slots__ = ("_columns", "_values", "_lookup")

    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        self._columns: tuple[str, ...] = tuple(columns)
        self._values: tuple[Any, ...] = tuple(values)
        # Case-insensitive name → index, matching sqlite3.Row semantics.
        self._lookup: dict[str, int] = {c.lower(): i for i, c in enumerate(self._columns)}

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        try:
            return self._values[self._lookup[key.lower()]]
        except KeyError as exc:
            raise IndexError(f"No such column: {key}") from exc

    def keys(self) -> list[str]:
        return list(self._columns)

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[Any]:
        # sqlite3.Row iterates *values* (not column names) — match that.
        return iter(self._values)

    def __repr__(self) -> str:
        return f"RemoteRow({dict(zip(self._columns, self._values, strict=True))!r})"


class RemoteCursor:
    """Subset of ``sqlite3.Cursor`` covering fetchone/fetchall/iter."""

    def __init__(self, columns: Sequence[str], rows: Sequence[Sequence[Any]]):
        self._rows: list[RemoteRow] = [RemoteRow(columns, r) for r in rows]
        self._index = 0

    def fetchone(self) -> RemoteRow | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[RemoteRow]:
        remaining = self._rows[self._index :]
        self._index = len(self._rows)
        return remaining

    def __iter__(self) -> Iterator[RemoteRow]:
        while (row := self.fetchone()) is not None:
            yield row


class RemoteSqliteConnection:
    """Network-backed connection that mimics ``sqlite3.Connection``.

    Pass an existing ``httpx.Client`` (e.g. backed by ``httpx.MockTransport``)
    to inject behaviour in tests; otherwise the connection owns its own and
    closes it via ``close()`` / context-manager exit.
    """

    def __init__(
        self,
        base_url: str,
        wiki: str,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.wiki = wiki
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> RemoteCursor:
        """Send a single SQL statement to the remote and return a cursor.

        Each call is one round-trip. Statements autocommit on the remote, so
        there is no notion of an in-flight transaction tied to this object.
        """
        url = f"{self.base_url}/api/sql/{self.wiki}"
        try:
            response = self._client.post(url, json={"sql": sql, "params": list(params)})
        except httpx.HTTPError as exc:
            raise RemoteSqliteError(f"network error talking to {url}: {exc}") from exc
        if response.status_code >= 400:
            raise RemoteSqliteError(
                f"remote returned {response.status_code} for SQL: {sql!r} "
                f"(body: {response.text[:500]})"
            )
        payload = response.json()
        return RemoteCursor(payload.get("columns", []), payload.get("rows", []))

    def commit(self) -> None:
        # Each statement autocommits on the remote — see module docstring.
        return None

    def rollback(self) -> None:
        # Same reason as ``commit``: nothing local to undo. Surfacing a clear
        # error if someone *does* depend on transactional rollback is better
        # than silently doing nothing.
        raise RemoteSqliteError(
            "rollback() is not supported on remote connections — statements "
            "autocommit individually. See docs/apis/REMOTE_WIKI_API.md."
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "RemoteSqliteConnection":
        return self

    def __exit__(self, *args: object) -> None:
        del args
        self.close()
