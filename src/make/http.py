"""HTTP checks without curl.

Three repos shelled out to `curl -sS -o /dev/null -w '%{http_code}'` and then
parsed the printed code back out of a string. The request is one stdlib call;
what the shell versions were really carrying was the *conventions* -- never
raise on a bad status, report an unreachable host as code 0, honour
`--dry-run`. Those live here now:

    from make import http

    http.get(url).status                  # 200, 404, ... or 0 when unreachable
    http.ok(url)                          # bool: 2xx/3xx
    http.wait("http://localhost:8002/", timeout=60)   # poll until it answers

A non-2xx status is a *result*, not an exception -- smoke tests want to compare
it, and a deploy wants to name it in its own error. Only `wait()` raises, and
only on timeout.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .context import current, echo, paint
from .poll import poll as _poll

__all__ = ["Response", "get", "ok", "request", "status", "wait"]


@dataclass(frozen=True)
class Response:
    """Outcome of one request. `status` 0 means the host never answered."""

    url: str
    status: int
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    """Response headers, keys lower-cased."""
    skipped: bool = False
    """True when the request was not made because of `--dry-run`."""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400

    def __bool__(self) -> bool:
        return self.ok


def request(
    url: str,
    *,
    method: str = "GET",
    timeout: float = 20.0,
    headers: dict[str, str] | None = None,
    data: bytes | str | None = None,
    read_body: bool = True,
    echo_cmd: bool = True,
    dry_status: int = 200,
    dry_body: str = "",
) -> Response:
    """Perform one request, returning a `Response` whatever happens.

    Give `dry_status=` a stand-in when the status steers later logic, for the
    same reason `sh.out(dry=...)` exists.
    """
    context = current()
    if echo_cmd and (not context.quiet or context.dry_run):
        prefix = paint("[dry-run] ", "yellow") if context.dry_run else ""
        echo(prefix + paint("~ ", "cyan", "bold") + paint(f"{method} {url}", "cyan"))
    if context.dry_run:
        return Response(url=url, status=dry_status, body=dry_body, skipped=True)

    import urllib.error
    import urllib.request

    payload = data.encode() if isinstance(data, str) else data
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=payload)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as answer:
            body = answer.read().decode(errors="replace") if read_body else ""
            heads = {k.lower(): v for k, v in answer.getheaders()}
            return Response(url=url, status=answer.status, body=body, headers=heads)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace") if read_body else ""
        heads = {k.lower(): v for k, v in (exc.headers or {}).items()}
        return Response(url=url, status=exc.code, body=body, headers=heads)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return Response(url=url, status=0)


def get(url: str, **kwargs: object) -> Response:
    return request(url, **kwargs)  # type: ignore[arg-type]


def status(url: str, **kwargs: object) -> int:
    return request(url, **kwargs).status  # type: ignore[arg-type]


def ok(url: str, **kwargs: object) -> bool:
    kwargs.setdefault("echo_cmd", False)
    return request(url, **kwargs).ok  # type: ignore[arg-type]


def wait(
    url: str,
    *,
    timeout: float = 60.0,
    interval: float = 1.0,
    request_timeout: float = 5.0,
    message: str | None = None,
) -> Response:
    """Poll `url` until it answers with a 2xx/3xx; raise `WaitTimeout` otherwise."""

    def ready() -> Response | None:
        answer = request(url, timeout=request_timeout, read_body=False, echo_cmd=False)
        return answer if answer.ok else None

    result: Response = _poll(
        ready,
        timeout=timeout,
        interval=interval,
        message=message or url,
        dry=Response(url=url, status=200, skipped=True),
    )
    return result
