# -*- coding: utf-8 -*-
"""HTTP 服务：标准库 http.server，不用任何 Web 框架。"""
from __future__ import annotations

import json
import socket
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import api, config, db, multipart

STATIC_DIR = config.ROOT / "static"

_STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


class Ctx(object):
    def __init__(self, method, path, query, body, headers):
        self.method = method
        self.path = path
        self.query = query
        self.body = body
        self.headers = headers
        self._json = None
        self._parts = None

    def json(self) -> dict:
        if self._json is None:
            if not self.body:
                self._json = {}
            else:
                try:
                    obj = json.loads(self.body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise api.ApiError("请求内容不是合法的 JSON。")
                self._json = obj if isinstance(obj, dict) else {"value": obj}
        return self._json

    def parts(self) -> list:
        if self._parts is None:
            ctype = self.headers.get("Content-Type", "")
            self._parts = multipart.parse(self.body, ctype)
        return self._parts


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperGrading"
    protocol_version = "HTTP/1.1"

    # ---- 基础工具 -------------------------------------------------------

    def log_message(self, fmt, *args):
        # 默认那行日志太吵，只在出错时打
        pass

    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            n = int(length)
        except ValueError:
            return b""
        if n > api.MAX_UPLOAD:
            raise api.ApiError("文件太大了（超过 %d MB）。请分几次导入。"
                               % (api.MAX_UPLOAD // 1024 // 1024), 413)
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send(self, status, body: bytes, content_type: str, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8",
                   {"Cache-Control": "no-store"})

    def _send_error_json(self, status, message):
        self._send_json(status, {"error": message})

    # ---- 分发 -----------------------------------------------------------

    def _handle(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if path.startswith("/api/") or path.startswith("/files/"):
            try:
                body = self._read_body()
                ctx = Ctx(self.command, path, query, body, self.headers)
                result = api.dispatch(ctx)
            except api.ApiError as exc:
                self._send_error_json(exc.status, exc.message)
                return
            except Exception:
                traceback.print_exc()
                self._send_error_json(
                    500, "系统内部出错了。请把黑色窗口里的报错内容发给开发者。")
                return
            if isinstance(result, api.FileResponse):
                self._send_file_response(result)
            else:
                self._send_json(200, result if result is not None else {})
            return

        if self.command not in ("GET", "HEAD"):
            self._send_error_json(405, "不支持的请求方式。")
            return
        self._serve_static(path)

    def _send_file_response(self, resp: api.FileResponse):
        extra = {}
        if resp.filename:
            # 中文文件名走 RFC 5987，老浏览器退回到 ASCII 名
            from urllib.parse import quote
            disposition = "inline" if resp.inline else "attachment"
            extra["Content-Disposition"] = (
                "%s; filename=\"download%s\"; filename*=UTF-8''%s"
                % (disposition, Path(resp.filename).suffix, quote(resp.filename))
            )
        if resp.max_age:
            extra["Cache-Control"] = "private, max-age=%d" % resp.max_age
        else:
            extra["Cache-Control"] = "no-store"
        self._send(200, resp.data, resp.content_type, extra)

    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/")
        if ".." in rel.split("/"):
            self._send_error_json(400, "路径不合法。")
            return
        target = (STATIC_DIR / rel).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send_error_json(400, "路径不合法。")
            return
        if not target.exists() or not target.is_file():
            self._send(404, "页面不存在".encode("utf-8"),
                       "text/plain; charset=utf-8")
            return
        ctype = _STATIC_MIME.get(target.suffix.lower(), "application/octet-stream")
        # 一律不缓存：老师更新完代码就该看到新界面，缓存住会让人以为更新没生效
        self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def handle_one_request(self):
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def process_request_thread(self, request, client_address):
        try:
            ThreadingHTTPServer.process_request_thread(self, request, client_address)
        finally:
            db.close()  # 每个请求线程用完就还掉连接，别泄漏


def pick_port(preferred: int) -> int:
    """端口被占就往后找，最多试 20 个。"""
    for offset in range(20):
        port = preferred + offset
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            sock.close()
    raise SystemExit("找不到可用端口，请关掉其它程序后重试。")


def run(port=None, open_browser=True):
    db.init()
    key_path = config.ensure_key_file()
    has_key, _ = config.key_from_file()
    port = pick_port(port or config.DEFAULT_PORT)
    url = "http://127.0.0.1:%d" % port
    httpd = Server(("127.0.0.1", port), Handler)

    print("=" * 60)
    print("  试卷批改系统  v%s" % config.version())
    print("  已启动：%s" % url)
    print("  数据存放：%s" % config.data_dir())
    if has_key:
        print("  AI 密钥：已从 %s 读到，AI 批改可用。" % key_path.name)
    else:
        print("  AI 密钥：还没填。把智谱的 API Key 粘进下面这个文件、保存即可：")
        print("           %s" % key_path)
    print("  关掉这个窗口就是停止服务。")
    print("=" * 60)
    sys.stdout.flush()

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        httpd.server_close()
        db.close()
