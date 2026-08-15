#!/usr/bin/env python3
"""One-time database migration server for FS25 Server Hub."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "fs25.db"
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8099"))
ALLOW_DIRECT = os.getenv("ALLOW_DIRECT", "false").lower() == "true"
MAX_UPLOAD_BYTES = 256 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("fs25-migration")


def database_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "size_bytes": 0, "counts": {}}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity.lower() != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"events", "sessions", "balance_samples", "snapshots"}
        missing = sorted(required - tables)
        if missing:
            raise ValueError(f"Not an FS25 Server Hub database; missing tables: {', '.join(missing)}")
        counts: dict[str, int] = {}
        for table in (
            "events",
            "sessions",
            "balance_samples",
            "snapshots",
            "classification_rules",
            "daily_balance_samples",
        ):
            if table in tables:
                counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return {
            "exists": True,
            "size_bytes": path.stat().st_size,
            "integrity": integrity,
            "counts": counts,
        }
    finally:
        connection.close()


def backup_database(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(source_path, timeout=30)
    destination = sqlite3.connect(destination_path, timeout=30)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def import_database(content: bytes) -> dict[str, Any]:
    if len(content) < 100 or not content.startswith(b"SQLite format 3\x00"):
        raise ValueError("The uploaded file is not a SQLite database")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    uploaded = DATA_DIR / ".fs25-migration-upload.db"
    replacement = DATA_DIR / ".fs25-migration-replacement.db"
    uploaded.write_bytes(content)
    replacement.unlink(missing_ok=True)

    try:
        incoming = database_summary(uploaded)
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safety_path: Path | None = None

        if DB_PATH.exists():
            safety_path = DATA_DIR / f"fs25-before-migration-{timestamp}.db"
            backup_database(DB_PATH, safety_path)

        backup_database(uploaded, replacement)
        installed = database_summary(replacement)

        for suffix in ("-wal", "-shm"):
            Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
        DB_PATH.unlink(missing_ok=True)
        replacement.replace(DB_PATH)
        installed = database_summary(DB_PATH)

        return {
            "imported": True,
            "incoming": incoming,
            "installed": installed,
            "safety_backup": safety_path.name if safety_path else None,
        }
    finally:
        uploaded.unlink(missing_ok=True)
        replacement.unlink(missing_ok=True)


def page() -> bytes:
    return b'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FS25 Server Hub Migration</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#101510;color:#edf5ed;margin:0;padding:28px}
main{max-width:760px;margin:auto;background:#182018;border:1px solid #324432;border-radius:14px;padding:24px}
h1{margin-top:0}.note{background:#222d22;border-left:4px solid #8fcf67;padding:12px 14px;border-radius:6px}
input,button{font:inherit}input{display:block;margin:18px 0;width:100%}button{padding:10px 16px;border:0;border-radius:8px;font-weight:700;cursor:pointer}
pre{white-space:pre-wrap;background:#0c110c;padding:14px;border-radius:8px;min-height:48px}
</style>
</head>
<body><main>
<h1>FS25 Server Hub database migration</h1>
<p class="note">Migration mode is active. Upload the consolidated <strong>fs25.db</strong> from the old Local FS25 Server Hub backup.</p>
<p>The file is integrity-checked and must contain the FS25 history tables. If this new installation already has a database, a timestamped safety copy is created first.</p>
<form id="form"><input id="db" type="file" accept=".db,application/vnd.sqlite3,application/octet-stream" required><button type="submit">Import database</button></form>
<pre id="result">Choose fs25.db to begin.</pre>
<script>
const form=document.getElementById('form'),input=document.getElementById('db'),result=document.getElementById('result');
form.addEventListener('submit',async(e)=>{e.preventDefault();const file=input.files[0];if(!file)return;if(!confirm('Import '+file.name+' into FS25 Server Hub?'))return;result.textContent='Checking and importing database...';try{const response=await fetch('api/migration/database',{method:'POST',headers:{'Content-Type':'application/octet-stream'},credentials:'same-origin',body:file});const payload=await response.json();if(!response.ok)throw new Error(payload.error||('HTTP '+response.status));const c=payload.installed.counts||{};result.textContent='Import complete.\n\nEvents: '+(c.events??0)+'\nSessions: '+(c.sessions??0)+'\nSnapshots: '+(c.snapshots??0)+'\n\nSafety backup: '+(payload.safety_backup||'not needed')+'\n\nNext: turn Migration mode OFF in Configuration, Save, then Restart the app.';}catch(error){result.textContent='Import failed: '+error.message;}});
</script>
</main></body></html>'''


class MigrationHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FS25HubMigration/0.5.6"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.debug("%s - %s", self.client_address[0], fmt % args)

    def ingress_allowed(self) -> bool:
        if ALLOW_DIRECT:
            return True
        client_ip = self.client_address[0]
        return client_ip == "172.30.32.2" or client_ip.startswith("127.")

    def send_body(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_body(json.dumps(payload, separators=(",", ":")).encode(), "application/json; charset=utf-8", status)

    def read_body(self) -> bytes:
        transfer_encoding = (self.headers.get("Transfer-Encoding") or "").lower()
        if transfer_encoding:
            encodings = [item.strip() for item in transfer_encoding.split(",") if item.strip()]
            if encodings[-1:] != ["chunked"]:
                raise ValueError("Unsupported request transfer encoding")
            body = bytearray()
            while True:
                size_line = self.rfile.readline(4096)
                if not size_line:
                    raise ValueError("Incomplete chunked request")
                try:
                    chunk_size = int(size_line.split(b";", 1)[0].strip(), 16)
                except ValueError as error:
                    raise ValueError("Invalid chunk header") from error
                if chunk_size == 0:
                    while True:
                        trailer = self.rfile.readline(8192)
                        if trailer in (b"\r\n", b"\n"):
                            break
                        if not trailer:
                            raise ValueError("Incomplete chunked request trailer")
                    break
                if len(body) + chunk_size > MAX_UPLOAD_BYTES:
                    self.close_connection = True
                    raise ValueError("Database file is too large")
                chunk = self.rfile.read(chunk_size)
                if len(chunk) != chunk_size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("Incomplete chunked request")
                body.extend(chunk)
            return bytes(body)

        value = self.headers.get("Content-Length")
        if not value:
            return b""
        try:
            length = int(value)
        except ValueError as error:
            raise ValueError("Invalid Content-Length header") from error
        if length < 0 or length > MAX_UPLOAD_BYTES:
            self.close_connection = True
            raise ValueError("Database file is too large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Incomplete request body")
        return body

    def do_GET(self) -> None:  # noqa: N802
        if not self.ingress_allowed():
            self.send_json({"error": "Ingress access only"}, HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        try:
            if path in ("/", "/migration"):
                self.send_body(page(), "text/html; charset=utf-8")
            elif path == "/api/migration/status":
                self.send_json(database_summary(DB_PATH) if DB_PATH.exists() else {"exists": False})
            else:
                self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("Migration GET failed")
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        if not self.ingress_allowed():
            self.send_json({"error": "Ingress access only"}, HTTPStatus.FORBIDDEN)
            return
        if urlparse(self.path).path != "/api/migration/database":
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            content = self.read_body()
            if not content:
                raise ValueError("No database file was uploaded")
            self.send_json(import_database(content))
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("Database import failed")
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGGER.warning("FS25 Server Hub is running in one-time database migration mode")
    server = ThreadingHTTPServer((HOST, PORT), MigrationHandler)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
