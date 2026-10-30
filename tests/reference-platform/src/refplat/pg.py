"""A minimal PostgreSQL client over a Unix socket (standard library only).

refplat keeps its tables in the platform's database like any platform;
being standard-library only, it speaks the protocol itself: the startup
message, SCRAM-SHA-256 authentication (the only method infra's server offers
on its sockets) and simple queries with text results. Values are passed as SQL
literals quoted by :func:`literal` (:func:`sql` fills a statement with them).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import socket
import struct
from collections.abc import Sequence
from pathlib import Path

PROTOCOL = 196608  # 3.0
AUTH_OK, AUTH_SASL, AUTH_SASL_CONTINUE, AUTH_SASL_FINAL = 0, 10, 11, 12
MECHANISM = "SCRAM-SHA-256"


class PgError(RuntimeError):
    """The server refused something; the message is its own."""


def literal(value: object) -> str:
    """A value as an SQL literal (``NULL``, a number or a quoted string)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def sql(template: str, *values: object) -> str:
    """A statement with each ``{}`` of the template replaced by a value quoted by :func:`literal`.

    Args:
        template: The statement; its only braces are the placeholders.
        *values: The values, in order.

    Returns:
        The statement.
    """
    return template.format(*(literal(v) for v in values))


def identifier(name: str) -> str:
    """A name as a quoted SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def _message(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("!i", len(body) + 4) + body


def _fields(body: bytes) -> dict[str, str]:
    out = {}
    for part in body.split(b"\0"):
        if part:
            out[chr(part[0])] = part[1:].decode("utf-8", "replace")
    return out


class Connection:
    """One session as one role (close it with :meth:`close`)."""

    def __init__(self, socket_dir: str, user: str, database: str, password: str) -> None:
        """Connect and authenticate.

        Args:
            socket_dir: Directory of ``.s.PGSQL.5432``.
            user: Role.
            database: Database.
            password: The role's password.

        Raises:
            PgError: If the server refuses the login.
            OSError: If the socket cannot be reached.
        """
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.buffer = b""
        try:
            self.sock.settimeout(30)
            self.sock.connect(str(Path(socket_dir) / ".s.PGSQL.5432"))
            params = f"user\0{user}\0database\0{database}\0\0".encode()
            body = struct.pack("!i", PROTOCOL) + params
            self.sock.sendall(struct.pack("!i", len(body) + 4) + body)
            self._authenticate(password)
        except BaseException:
            self.sock.close()
            raise

    def _read(self) -> tuple[bytes, bytes]:
        while (
            len(self.buffer) < 5 or len(self.buffer) < 1 + struct.unpack("!i", self.buffer[1:5])[0]
        ):
            chunk = self.sock.recv(65536)
            if not chunk:
                raise PgError("the server closed the connection")
            self.buffer += chunk
        length = struct.unpack("!i", self.buffer[1:5])[0]
        kind, body = self.buffer[:1], self.buffer[5 : 1 + length]
        self.buffer = self.buffer[1 + length :]
        if kind == b"E":
            fields = _fields(body)
            raise PgError(f"{fields.get('C', '')}: {fields.get('M', 'error')}")
        return kind, body

    def _authenticate(self, password: str) -> None:
        state: dict[str, bytes] = {}
        while True:
            kind, body = self._read()
            if kind == b"R":
                code = struct.unpack("!i", body[:4])[0]
                if code == AUTH_SASL:
                    if MECHANISM.encode() not in body[4:].split(b"\0"):
                        raise PgError("the server offers no SCRAM-SHA-256")
                    state["nonce"] = base64.b64encode(secrets.token_bytes(18))
                    state["first"] = b"n=,r=" + state["nonce"]
                    initial = b"n,," + state["first"]
                    payload = MECHANISM.encode() + b"\0" + struct.pack("!i", len(initial))
                    self.sock.sendall(_message(b"p", payload + initial))
                elif code == AUTH_SASL_CONTINUE:
                    self.sock.sendall(_message(b"p", _scram_final(password, state, body[4:])))
                elif code == AUTH_SASL_FINAL:
                    if not hmac.compare_digest(body[4:], state["expected"]):
                        raise PgError("the server's SCRAM signature does not match")
                elif code != AUTH_OK:
                    raise PgError(f"unsupported authentication method {code}")
            elif kind == b"Z":
                return

    def query(self, sql: str) -> list[tuple[str | None, ...]]:
        """Run a simple query (one or more statements).

        Args:
            sql: The SQL.

        Returns:
            The rows of the last statement that returned rows, as text.

        Raises:
            PgError: If a statement fails.
        """
        self.sock.sendall(_message(b"Q", sql.encode() + b"\0"))
        rows: list[tuple[str | None, ...]] = []
        failure: PgError | None = None
        while True:
            try:
                kind, body = self._read()
            except PgError as exc:
                if "closed" in str(exc):
                    raise
                failure = failure or exc
                continue
            if kind == b"T":
                rows = []
            elif kind == b"D":
                rows.append(_row(body))
            elif kind == b"Z":
                if failure:
                    raise failure
                return rows

    def close(self) -> None:
        """Say goodbye and close the socket."""
        try:
            self.sock.sendall(_message(b"X", b""))
        finally:
            self.sock.close()


def _row(body: bytes) -> tuple[str | None, ...]:
    count = struct.unpack("!h", body[:2])[0]
    offset = 2
    values: list[str | None] = []
    for _ in range(count):
        size = struct.unpack("!i", body[offset : offset + 4])[0]
        offset += 4
        if size < 0:
            values.append(None)
        else:
            values.append(body[offset : offset + size].decode("utf-8"))
            offset += size
    return tuple(values)


def _scram_final(password: str, state: dict[str, bytes], server_first: bytes) -> bytes:
    """The client-final-message of SCRAM-SHA-256 (RFC 5802, RFC 7677)."""
    attrs = dict(part.split(b"=", 1) for part in server_first.split(b","))
    nonce, salt, rounds = attrs[b"r"], base64.b64decode(attrs[b"s"]), int(attrs[b"i"])
    if not nonce.startswith(state["nonce"]):
        raise PgError("the server's SCRAM nonce does not extend ours")
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    client_key = hmac.digest(salted, b"Client Key", "sha256")
    stored_key = hashlib.sha256(client_key).digest()
    without_proof = b"c=biws,r=" + nonce
    auth = state["first"] + b"," + server_first + b"," + without_proof
    signature = hmac.digest(stored_key, auth, "sha256")
    proof = bytes(a ^ b for a, b in zip(client_key, signature, strict=True))
    server_key = hmac.digest(salted, b"Server Key", "sha256")
    state["expected"] = b"v=" + base64.b64encode(hmac.digest(server_key, auth, "sha256"))
    return without_proof + b",p=" + base64.b64encode(proof)


def connect(socket_dir: str, role: str, database: str, password_dir: Path) -> Connection:
    """Log in as a role with its password file (``/etc/pinka/secrets/postgres/<role>``).

    Args:
        socket_dir: Directory of the server's socket.
        role: Role.
        database: Database.
        password_dir: Directory of the password files.

    Returns:
        The connection.
    """
    password = (password_dir / role).read_text(encoding="utf-8").strip()
    return Connection(socket_dir, role, database, password)


def execute(conn: Connection, statements: Sequence[str]) -> None:
    """Run statements in one transaction; a failure rolls all of them back."""
    try:
        conn.query("BEGIN;\n" + ";\n".join(statements) + ";\nCOMMIT;")
    except PgError:
        conn.query("ROLLBACK")
        raise
