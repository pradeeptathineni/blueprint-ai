"""A loopback settings page over the native project settings service."""

from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from blueprint_ai.settings import SettingsConflict, preview_settings, read_settings, write_settings

ASSETS = Path(__file__).parent / "settings_web"


def settings_server(root: Path, port: int = 0) -> ThreadingHTTPServer:
    """Bind one explicit project to loopback, without opening a browser or starting work."""
    root = root.resolve(strict=True)
    read_settings(root)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _reply(self, code: int, body: Any, content_type: str = "application/json") -> None:
            data = (
                json.dumps(body).encode() if content_type == "application/json" else body.encode()
            )
            self.send_response(code)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def _local(self) -> bool:
            host = f"127.0.0.1:{cast(ThreadingHTTPServer, self.server).server_port}"
            valid = (
                self.headers.get("Host") == host
                and self.headers.get("Origin") in (None, "http://" + host)
                and self.headers.get("Sec-Fetch-Site") in (None, "same-origin", "none")
            )
            if not valid:
                self._reply(403, {"error": "Use the printed local settings URL."})
            return valid

        def do_GET(self) -> None:
            if not self._local():
                return
            assets = {
                "/": ((ASSETS / "index.html").read_text().replace("TOKEN", token), "text/html"),
                "/settings.js": ((ASSETS / "settings.js").read_text(), "text/javascript"),
                "/settings.css": ((ASSETS / "settings.css").read_text(), "text/css"),
            }
            if self.path in assets:
                self._reply(200, *assets[self.path])
            elif self.path == "/api/settings":
                try:
                    self._reply(200, read_settings(root))
                except (ValueError, OSError):
                    self._reply(422, {"error": "Cannot read valid project settings."})
            else:
                self._reply(404, {"error": "Unknown settings route."})

        def do_POST(self) -> None:
            if not self._local():
                return
            if self.path != "/api/settings":
                self._reply(404, {"error": "Unknown settings route."})
                return
            if not secrets.compare_digest(
                self.headers.get("X-Settings-Token", "").encode("utf-8"), token.encode("utf-8")
            ):
                self._reply(403, {"error": "Reload the local settings page before editing."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if (
                    not 0 < length <= 16_384
                    or self.headers.get("Content-Type") != "application/json"
                ):
                    raise ValueError("Use a bounded JSON settings request.")
                body = json.loads(self.rfile.read(length))
                if (
                    not isinstance(body, dict)
                    or set(body) != {"action", "updates", "resets", "expectedRevision"}
                    or body["action"] not in ("preview", "save")
                ):
                    raise ValueError("Unsupported settings request.")
                operation = preview_settings if body["action"] == "preview" else write_settings
                result = operation(root, body["updates"], body["resets"], body["expectedRevision"])
                self._reply(200, result)
            except SettingsConflict as exc:
                self._reply(409, {"error": str(exc)})
            except (ValueError, TypeError, RecursionError):
                self._reply(422, {"error": "Invalid settings. Check values and reload if needed."})
            except OSError:
                self._reply(
                    422, {"error": "Cannot save project settings. Check local permissions."}
                )

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
