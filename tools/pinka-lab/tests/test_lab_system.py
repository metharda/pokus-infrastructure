"""Tests for RealSystem, the host access of pinka."""

from __future__ import annotations

import http.server
import os
import socketserver
import tempfile
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager

from pinka_lab.system import EXIT_NOT_FOUND, EXIT_TIMEOUT, RealSystem, static_server


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status = 200 if self.path == "/ok" else 404
        body = b'{"path": "' + self.path.encode() + b'"}'
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.send_response(202)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def address_string(self) -> str:
        return "test"

    def log_message(self, *args: object) -> None:
        pass


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@contextmanager
def serving(server: socketserver.BaseServer) -> Iterator[socketserver.BaseServer]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


class CommandTest(unittest.TestCase):
    def test_run(self) -> None:
        system = RealSystem()
        done = system.run(["sh", "-c", 'printf "%s" "$LC_ALL"; echo err >&2; exit 3'])
        self.assertEqual((done.returncode, done.stdout, done.stderr), (3, "C.UTF-8", "err\n"))
        self.assertFalse(done.ok)
        self.assertEqual(system.run(["cat"], stdin="in").stdout, "in")
        self.assertEqual(system.run(["sh", "-c", 'printf "%s" "$X"'], env={"X": "y"}).stdout, "y")
        self.assertEqual(system.run(["/nonexistent/program"]).returncode, EXIT_NOT_FOUND)
        self.assertEqual(system.run(["sleep", "5"], timeout=0.2).returncode, EXIT_TIMEOUT)
        self.assertEqual(system.run(["echo", "[1]"]).json(), [1])
        self.assertIsNone(system.run(["echo", "x"]).json())

    def test_interactive(self) -> None:
        system = RealSystem()
        self.assertEqual(system.interactive(["sh", "-c", "exit 4"]), 4)
        self.assertEqual(system.interactive(["/nonexistent/program"]), EXIT_NOT_FOUND)


class FileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_read_write_info(self) -> None:
        system = RealSystem()
        path = os.path.join(self.root, "sub", "file.json")
        self.assertIsNone(system.read_text(path))
        system.write_text(path, "text\n", 0o640)
        self.assertEqual(system.read_text(path), "text\n")
        found = system.info(path)
        assert found is not None
        self.assertEqual((found.mode, found.is_dir, found.size), (0o640, False, 5))
        self.assertEqual(system.listdir(os.path.dirname(path)), ["file.json"])
        os.symlink(path, os.path.join(self.root, "link"))
        link = system.info(os.path.join(self.root, "link"))
        assert link is not None
        self.assertTrue(link.is_link)
        self.assertIsNone(system.info(os.path.join(self.root, "missing")))
        self.assertEqual(system.listdir(os.path.join(self.root, "missing")), [])

    def test_walk_dirs(self) -> None:
        for d in ("a/b/c/d", "e"):
            os.makedirs(os.path.join(self.root, d))
        os.symlink(os.path.join(self.root, "a"), os.path.join(self.root, "e", "link"))
        found = {os.path.relpath(p, self.root) for p in RealSystem().walk_dirs(self.root, 2)}
        self.assertEqual(found, {"a", "e", "a/b", "e/link"})
        self.assertNotIn("e/link/b", found)


class NetworkTest(unittest.TestCase):
    def test_http(self) -> None:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        with serving(server):
            system = RealSystem()
            answer = system.http("GET", f"http://127.0.0.1:{port}/ok")
            self.assertEqual((answer.status, answer.json()), (200, {"path": "/ok"}))
            self.assertEqual(system.http("GET", f"http://127.0.0.1:{port}/no").status, 404)
            posted = system.http("POST", f"http://127.0.0.1:{port}/p", b'{"a": 1}')
            self.assertEqual((posted.status, posted.json()), (202, {"a": 1}))
            self.assertTrue(system.tcp_connect("127.0.0.1", port))
        self.assertEqual(RealSystem().http("GET", f"http://127.0.0.1:{port}/ok").status, 0)
        self.assertFalse(RealSystem().tcp_connect("127.0.0.1", port, timeout=0.5))

    def test_unix_http(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "api.sock")
            with serving(UnixServer(path, Handler)):
                system = RealSystem()
                answer = system.unix_http(path, "POST", "/lab/drain", b'{"timeout_seconds": 1}')
                self.assertEqual((answer.status, answer.json()), (202, {"timeout_seconds": 1}))
                self.assertEqual(system.unix_http(path, "GET", "/ok").status, 200)
            self.assertEqual(RealSystem().unix_http(path, "GET", "/ok").status, 0)


class StaticServerTest(unittest.TestCase):
    def test_serves_files_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as handle:
                handle.write("<h1>pinka</h1>")
            server = static_server(tmp, "127.0.0.1", 0)
            self.assertEqual(server.server_address[0], "127.0.0.1")
            port = server.server_address[1]
            with serving(server):
                system = RealSystem()
                page = system.http("GET", f"http://127.0.0.1:{port}/index.html")
                self.assertEqual((page.status, page.body), (200, "<h1>pinka</h1>"))
                self.assertEqual(system.http("GET", f"http://127.0.0.1:{port}/none").status, 404)
                self.assertEqual(system.http("POST", f"http://127.0.0.1:{port}/", b"x").status, 501)


class IdentityTest(unittest.TestCase):
    def test_identity(self) -> None:
        system = RealSystem()
        self.assertEqual(system.euid(), os.geteuid())
        self.assertEqual(system.now().utcoffset().total_seconds(), 0)  # type: ignore[union-attr]
        self.assertIsNone(system.user_groups("no-such-user-pinka"))
        self.assertIsNone(system.group_members("no-such-group-pinka"))
        import pwd

        me = pwd.getpwuid(os.geteuid()).pw_name
        self.assertTrue(system.user_groups(me))


if __name__ == "__main__":
    unittest.main()
