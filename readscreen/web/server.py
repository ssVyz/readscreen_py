"""HTTP server: JSON routes that map 1:1 to readscreen.api, and the static frontend.

Binds to 127.0.0.1 by default. v1 has no access token or Host/Origin checks (see instructions.md).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import traceback
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from ..api import Api, ApiError

log = logging.getLogger("readscreen.web")

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 1 << 20
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json",
}
ERROR_STATUS = {"invalid": HTTPStatus.BAD_REQUEST, "not_found": HTTPStatus.NOT_FOUND, "conflict": HTTPStatus.CONFLICT}


@dataclass
class Download:
    content: str
    content_type: str
    filename: str


@dataclass
class Request:
    api: Api
    match: re.Match
    query: dict[str, str]
    body: Any

    def id(self) -> int:
        return int(self.match.group("id"))

    def arg(self, name: str, default: Any = None) -> Any:
        if isinstance(self.body, dict) and name in self.body:
            return self.body[name]
        return self.query.get(name, default)


ROUTES: list[tuple[str, re.Pattern, Callable[[Request], Any]]] = []


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn

    return deco


def _flag(value: Any) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on") if not isinstance(value, bool) else value


# --------------------------------------------------------------------------- routes

_DB_CACHE: dict[str, Any] = {"time": 0.0, "data": None}
_DB_LOCK = threading.Lock()

J = r"/api/jobs/(?P<id>\d+)"
STAGE = r"(?P<stage>[a-z]+)"


@route("GET", r"/api/info")
def _info(r: Request):
    return r.api.info()


@route("GET", r"/api/stages")
def _stages(r: Request):
    return r.api.stage_info(r.query.get("platform") or None)


@route("GET", r"/api/dbs")
def _dbs(r: Request):
    with _DB_LOCK:
        if _DB_CACHE["data"] is None or time.monotonic() - _DB_CACHE["time"] > 30 or _flag(r.query.get("refresh", "")):
            _DB_CACHE["data"] = r.api.list_dbs()
            _DB_CACHE["time"] = time.monotonic()
        return _DB_CACHE["data"]


@route("GET", r"/api/dbs/sources")
def _db_sources(r: Request):
    remote = _flag(r.query.get("remote", ""))
    max_age = 0.0 if _flag(r.query.get("refresh", "")) else 600.0
    return r.api.db_sources(remote=remote, max_age=max_age)


@route("GET", r"/api/dbs/download")
def _download_status(r: Request):
    return r.api.download_status()


@route("POST", r"/api/dbs/download")
def _download_start(r: Request):
    return {"pid": r.api.start_download(r.arg("name") or None)}


@route("POST", r"/api/dbs/download/cancel")
def _download_cancel(r: Request):
    return {"cancelled": r.api.cancel_download()}


@route("GET", r"/api/settings")
def _settings(r: Request):
    return r.api.get_settings()


@route("GET", r"/api/settings/schema")
def _settings_schema(r: Request):
    return r.api.settings_schema()


@route("PUT", r"/api/settings")
def _set_settings(r: Request):
    if not isinstance(r.body, dict):
        raise ApiError("expected an object of settings")
    for key, value in r.body.items():
        r.api.set_setting(key, value)
    with _DB_LOCK:
        _DB_CACHE["data"] = None  # blast_db_dir may have changed
    return r.api.get_settings()


@route("GET", r"/api/jobs")
def _jobs(r: Request):
    return r.api.list_jobs()


@route("POST", r"/api/jobs")
def _create(r: Request):
    b = r.body or {}
    if not isinstance(b, dict):
        raise ApiError("expected a JSON object")
    return r.api.create_job(
        platform=b.get("platform", "illumina"), r1=b.get("r1") or "", r2=b.get("r2") or None,
        assembly=b.get("assembly") or None, name=b.get("name") or None, notes=b.get("notes") or "",
        params=b.get("params") or {}, pauses=b.get("pauses") or [], queue=_flag(b.get("queue", True)),
    )


@route("GET", J)
def _job(r: Request):
    return r.api.get_job(r.id())


@route("PATCH", J)
def _edit(r: Request):
    return r.api.update_job(r.id(), r.arg("name"), r.arg("notes"))


@route("DELETE", J)
def _delete(r: Request):
    return {"result": r.api.delete_job(r.id())}


@route("POST", J + r"/queue")
def _queue(r: Request):
    r.api.queue_job(r.id())
    return r.api.get_job(r.id())


@route("POST", J + r"/continue")
def _continue(r: Request):
    r.api.continue_job(r.id())
    return r.api.get_job(r.id())


@route("POST", J + r"/cancel")
def _cancel(r: Request):
    return {"result": r.api.cancel_job(r.id())}


@route("POST", J + r"/reset")
def _reset(r: Request):
    r.api.reset_job(r.id(), str(r.arg("stage", "")), queue=_flag(r.arg("queue", False)))
    return r.api.get_job(r.id())


@route("PUT", J + r"/params/" + STAGE)
def _params(r: Request):
    b = r.body or {}
    updates = b.get("updates", {}) if isinstance(b, dict) else {}
    return r.api.set_params(r.id(), r.match.group("stage"), updates, reset=_flag(b.get("reset", False)))


@route("PUT", J + r"/pause/" + STAGE)
def _pause(r: Request):
    r.api.set_pause(r.id(), r.match.group("stage"), _flag(r.arg("on", True)))
    return r.api.get_job(r.id())


@route("POST", J + r"/reassign")
def _reassign(r: Request):
    b = r.body or {}
    return r.api.reassign_inputs(r.id(), b.get("r1") or None, b.get("r2") or None, b.get("assembly") or None,
                                 force=_flag(b.get("force", False)))


@route("GET", J + r"/log/" + STAGE)
def _log(r: Request):
    return {"text": r.api.stage_log(r.id(), r.match.group("stage"), int(r.query.get("lines", 2000)))}


@route("GET", J + r"/ranking")
def _ranking(r: Request):
    return r.api.get_ranking(r.id())


@route("PUT", J + r"/selection")
def _selection(r: Request):
    refs = r.arg("refs", [])
    if not isinstance(refs, list):
        raise ApiError("refs must be a list")
    count = r.api.set_selection(r.id(), [str(x) for x in refs])
    if _flag(r.arg("continue", False)):
        r.api.continue_job(r.id())
    return {"selected": count}


@route("GET", J + r"/results")
def _results(r: Request):
    return r.api.get_results(r.id())


@route("GET", J + r"/results/(?P<ref>[^/]+)")
def _result(r: Request):
    return r.api.get_result(r.id(), r.match.group("ref"))


@route("GET", J + r"/export")
def _export(r: Request):
    fmt = r.query.get("format", "json")
    text = r.api.export_results(r.id(), fmt)
    ctype = "application/json" if fmt == "json" else "text/tab-separated-values; charset=utf-8"
    return Download(text, ctype, f"readscreen_job{r.id()}_results.{fmt}")


@route("GET", r"/api/worker")
def _worker(r: Request):
    return r.api.worker_status()


@route("POST", r"/api/worker/start")
def _worker_start(r: Request):
    return {"pid": r.api.start_worker()}


@route("POST", r"/api/worker/stop")
def _worker_stop(r: Request):
    return {"result": r.api.stop_worker(now=_flag(r.arg("now", False)))}


@route("GET", r"/api/fs/list")
def _fs_list(r: Request):
    return r.api.browse(r.query.get("path") or None, show_hidden=_flag(r.query.get("hidden", False)))


@route("GET", r"/api/fs/suggest")
def _fs_suggest(r: Request):
    return r.api.suggest_inputs(r.query.get("path", ""))


# --------------------------------------------------------------------------- handler


class Handler(BaseHTTPRequestHandler):
    server_version = "readscreen"
    protocol_version = "HTTP/1.1"

    @property
    def api(self) -> Api:
        return self.server.api  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # quiet by default
        log.debug("%s %s", self.address_string(), fmt % args)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        path = unquote(url.path)
        if path.startswith("/api/"):
            self._api(method, path, {k: v[-1] for k, v in parse_qs(url.query).items()})
        elif method == "GET":
            self._static(path)
        else:
            self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError("request body too large")
        if not length:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError("request body is not valid JSON") from None

    def _api(self, method: str, path: str, query: dict) -> None:
        try:
            body = self._read_body()
            allowed = False
            for m, pattern, fn in ROUTES:
                match = pattern.match(path)
                if not match:
                    continue
                allowed = True
                if m != method:
                    continue
                result = fn(Request(self.api, match, query, body))
                if isinstance(result, Download):
                    self._send(HTTPStatus.OK, result.content.encode("utf-8"), result.content_type,
                               {"Content-Disposition": f'attachment; filename="{result.filename}"'})
                else:
                    self._json(HTTPStatus.OK, result)
                return
            if allowed:
                self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": f"unknown API path: {path}", "code": "not_found"})
        except ApiError as exc:
            self._json(ERROR_STATUS.get(exc.code, HTTPStatus.BAD_REQUEST), {"error": str(exc), "code": exc.code})
        except Exception as exc:  # report, keep serving
            log.error("error in %s %s:\n%s", method, path, traceback.format_exc())
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"internal error: {exc!r}", "code": "internal"})

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        target = (STATIC / rel).resolve()
        try:
            target.relative_to(STATIC)
        except ValueError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not target.is_file():
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(HTTPStatus.OK, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    def _json(self, status: HTTPStatus, payload: Any) -> None:
        data = json.dumps(payload, default=str).encode("utf-8")
        self._send(status, data, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _send(self, status: HTTPStatus, data: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, api: Api, host: str, port: int):
        self.api = api
        super().__init__((host, port), Handler)


def make_server(api: Api, host: str = "127.0.0.1", port: int = 8765) -> Server:
    return Server(api, host, port)


def serve(api: Api, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> int:
    try:
        server = make_server(api, host, port)
    except OSError as exc:
        print(f"error: cannot listen on {host}:{port}: {exc}")
        return 1
    url = f"http://{'localhost' if host in ('127.0.0.1', '::1') else host}:{server.server_address[1]}/"
    print(f"readscreen {api.version()} web interface: {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser

        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        server.server_close()
    return 0
