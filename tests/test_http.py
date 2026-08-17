"""`http` carries the conventions the curl-in-a-string versions carried by hand."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from make import http
from make.testing import context


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # the http.server API's spelling
        code = 404 if self.path == "/missing" else 200
        body = b"hello"
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # keep pytest output clean
        pass


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_get_returns_status_and_body(server: str):
    with context():
        answer = http.get(f"{server}/")
    assert answer.status == 200
    assert answer.ok
    assert answer.body == "hello"


def test_a_404_is_a_result_not_an_exception(server: str):
    with context():
        answer = http.get(f"{server}/missing")
    assert answer.status == 404
    assert not answer.ok


def test_an_unreachable_host_is_status_zero():
    with context():
        # A reserved port on localhost nothing listens on.
        answer = http.get("http://127.0.0.1:1/", timeout=0.5)
    assert answer.status == 0
    assert not answer


def test_dry_run_makes_no_request():
    with context(dry_run=True):
        answer = http.get("http://127.0.0.1:1/", dry_status=204)
    assert answer.skipped
    assert answer.status == 204


def test_wait_polls_until_the_url_answers(server: str):
    with context():
        answer = http.wait(f"{server}/", timeout=5, interval=0)
    assert answer.ok


def test_wait_raises_on_timeout():
    from make.errors import WaitTimeout

    with context():
        with pytest.raises(WaitTimeout):
            http.wait("http://127.0.0.1:1/", timeout=0, interval=0, request_timeout=0.2)
