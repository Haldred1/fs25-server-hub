#!/usr/bin/env python3
"First-run setup checker for FS25 Server Hub."
from __future__ import annotations

import ftplib
import json
import logging
import os
import posixpath
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8099"))
ALLOW_DIRECT = os.getenv("ALLOW_DIRECT", "false").lower() == "true"
TIMEOUT = max(5, min(30, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))))
USER_AGENT = "HomeAssistant-FS25-Server-Hub-Setup/0.5.9"

HTTP_SOURCES = {
    "stats": ("Server statistics", "FS25_STATS_URL", "xml"),
    "map": ("Live map", "FS25_MAP_URL", "image"),
    "career": ("Career savegame", "FS25_CAREER_URL", "xml"),
    "vehicles": ("Vehicles", "FS25_VEHICLES_URL", "xml"),
    "economy": ("Economy", "FS25_ECONOMY_URL", "xml"),
    "missions": ("Missions", "FS25_MISSIONS_URL", "xml"),
    "placeables": ("Placeables", "FS25_PLACEABLES_URL", "xml"),
}
REQUIRED_SOURCES = ("stats", "map", "career", "vehicles", "economy")
MAX_HTTP_BYTES = 8 * 1024 * 1024
MAX_FTP_TEST_BYTES = 2 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("fs25-setup")
FTP_ERRORS = (OSError,) + ftplib.all_errors


def env(name: str) -> str:
    return os.getenv(name, "").strip()


def safe_tag_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def configured_status() -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for key, (label, env_name, kind) in HTTP_SOURCES.items():
        value = env(env_name)
        sources[key] = {
            "label": label,
            "kind": kind,
            "required": key in REQUIRED_SOURCES,
            "configured": bool(value),
        }

    ftp_host = env("FS25_MISSIONS_FTP_HOST")
    ftp_username = env("FS25_MISSIONS_FTP_USERNAME")
    ftp_password = os.getenv("FS25_MISSIONS_FTP_PASSWORD", "")
    ftp_path = env("FS25_MISSIONS_FTP_PATH")
    ftp_port = env("FS25_MISSIONS_FTP_PORT") or "21"
    ftp = {
        "configured": bool(ftp_host and ftp_username and ftp_password and ftp_path),
        "host_set": bool(ftp_host),
        "port": ftp_port,
        "username_set": bool(ftp_username),
        "password_set": bool(ftp_password),
        "path_set": bool(ftp_path),
        "tls": env("FS25_MISSIONS_FTP_TLS").lower() == "true",
        "passive": env("FS25_MISSIONS_FTP_PASSIVE").lower() != "false",
    }
    missing = [key for key in REQUIRED_SOURCES if not sources[key]["configured"]]
    mission_configured = sources["missions"]["configured"] or ftp["configured"]
    production_configured = sources["placeables"]["configured"] or ftp["configured"]
    return {
        "ready_for_dashboard": not missing,
        "missing_required": missing,
        "sources": sources,
        "ftp": ftp,
        "mission_source_configured": mission_configured,
        "production_source_configured": production_configured,
    }


def test_http_source(source: str) -> dict[str, Any]:
    if source not in HTTP_SOURCES:
        raise ValueError("Unknown source")
    label, env_name, kind = HTTP_SOURCES[source]
    url = env(env_name)
    if not url:
        raise ValueError(f"{label} URL is not configured")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/*,*/*;q=0.8" if kind == "image" else "application/xml,text/xml,*/*;q=0.8",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
            content = response.read(MAX_HTTP_BYTES + 1)
            if len(content) > MAX_HTTP_BYTES:
                raise ValueError("Response exceeded the setup check size limit")
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as error:
        raise ValueError(f"HTTP {error.code} {error.reason}") from error
    except urllib.error.URLError as error:
        raise ValueError(f"Connection failed: {error.reason}") from error
    except TimeoutError as error:
        raise ValueError(f"Connection timed out after {TIMEOUT}s") from error

    result: dict[str, Any] = {
        "ok": True,
        "source": source,
        "label": label,
        "status": status,
        "bytes": len(content),
        "content_type": content_type or "unknown",
        "latency_ms": round((time.monotonic() - started) * 1000, 1),
    }
    if kind == "xml":
        try:
            root = ET.fromstring(content)
        except ET.ParseError as error:
            raise ValueError(f"Connected, but response is not valid XML: {error}") from error
        result["xml_root"] = safe_tag_name(root.tag)
    else:
        if not content:
            raise ValueError("Connected, but the map response was empty")
        if content_type and not content_type.startswith("image/"):
            result["warning"] = f"Connected, but Content-Type is {content_type}"
    return result


def ftp_placeables_path(missions_path: str) -> str:
    folder = posixpath.dirname(missions_path)
    return posixpath.join(folder, "placeables.xml")


def ftp_retrieve_sample(ftp: ftplib.FTP, path: str) -> tuple[int, bytes]:
    total = 0
    sample = bytearray()

    def consume(chunk: bytes) -> None:
        nonlocal total
        total += len(chunk)
        if len(sample) < MAX_FTP_TEST_BYTES:
            remaining = MAX_FTP_TEST_BYTES - len(sample)
            sample.extend(chunk[:remaining])

    ftp.retrbinary(f"RETR {path}", consume, blocksize=32768)
    return total, bytes(sample)


def test_ftp() -> dict[str, Any]:
    host = env("FS25_MISSIONS_FTP_HOST")
    username = env("FS25_MISSIONS_FTP_USERNAME")
    password = os.getenv("FS25_MISSIONS_FTP_PASSWORD", "")
    missions_path = env("FS25_MISSIONS_FTP_PATH")
    if not host:
        raise ValueError("FTP host is not configured")
    if not username:
        raise ValueError("FTP username is not configured")
    if not password:
        raise ValueError("FTP password is not configured")
    if not missions_path:
        raise ValueError("FTP missions.xml path is not configured")

    try:
        port = int(env("FS25_MISSIONS_FTP_PORT") or "21")
    except ValueError as error:
        raise ValueError("FTP port is not valid") from error
    use_tls = env("FS25_MISSIONS_FTP_TLS").lower() == "true"
    passive = env("FS25_MISSIONS_FTP_PASSIVE").lower() != "false"
    started = time.monotonic()
    ftp: ftplib.FTP = ftplib.FTP_TLS(timeout=TIMEOUT) if use_tls else ftplib.FTP(timeout=TIMEOUT)

    try:
        ftp.connect(host, port)
        ftp.login(username, password)
        if use_tls and isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()
        ftp.set_pasv(passive)

        missions_bytes, missions_sample = ftp_retrieve_sample(ftp, missions_path)
        try:
            missions_root = safe_tag_name(ET.fromstring(missions_sample).tag)
        except ET.ParseError as error:
            raise ValueError(f"missions.xml was downloaded but is not valid XML: {error}") from error

        placeables_path = ftp_placeables_path(missions_path)
        placeables: dict[str, Any]
        try:
            placeables_bytes, placeables_sample = ftp_retrieve_sample(ftp, placeables_path)
            try:
                placeables_root = safe_tag_name(ET.fromstring(placeables_sample).tag)
                placeables = {
                    "ok": True,
                    "path": placeables_path,
                    "bytes": placeables_bytes,
                    "xml_root": placeables_root,
                }
            except ET.ParseError as error:
                placeables = {
                    "ok": False,
                    "path": placeables_path,
                    "error": f"Downloaded but XML is invalid: {error}",
                }
        except ftplib.all_errors as error:
            placeables = {"ok": False, "path": placeables_path, "error": str(error)}

        return {
            "ok": True,
            "host": host,
            "port": port,
            "tls": use_tls,
            "passive": passive,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "missions": {
                "ok": True,
                "path": missions_path,
                "bytes": missions_bytes,
                "xml_root": missions_root,
            },
            "placeables": placeables,
        }
    except FTP_ERRORS as error:
        raise ValueError(f"FTP connection failed: {error}") from error
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


def page() -> bytes:
    return b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FS25 Server Hub Setup</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#07110b;color:#eaf5ed;font-family:system-ui,-apple-system,sans-serif}
main{max-width:1000px;margin:auto;padding:28px}.hero,.card{background:#101b13;border:1px solid #28402e;border-radius:16px;padding:22px}
.hero h1{margin:0 0 8px}.hero p{color:#b8cbbb;line-height:1.55}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:18px}
.card h2{margin:0 0 12px;font-size:18px}.row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid #203026}.row:last-child{border:0}
.badge{padding:4px 9px;border-radius:999px;background:#253126;font-size:12px;font-weight:700}.good{background:#163d20;color:#9ff1ae}.warn{background:#493719;color:#ffd98b}
button{border:0;border-radius:9px;padding:9px 12px;font:inherit;font-weight:700;cursor:pointer;background:#8fd66e;color:#0a150b}button:disabled{opacity:.5;cursor:not-allowed}
.test-result{white-space:pre-wrap;background:#08100a;border-radius:9px;padding:10px;margin-top:10px;min-height:42px;color:#c9dacd;font-size:13px}
.callout{margin-top:18px;padding:14px 16px;border-left:4px solid #8fd66e;background:#101b13;border-radius:8px;line-height:1.5}.callout.warn{border-left-color:#ffc45c}
small{color:#9eb4a3}.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.footer{margin-top:18px;color:#9eb4a3}
code{background:#172319;padding:2px 5px;border-radius:5px}
</style>
</head>
<body><main>
<section class="hero">
  <h1>FS25 Server Hub setup checker</h1>
  <p>This page appears when the required Farming Simulator feeds are incomplete, or when Setup mode is enabled. Enter or correct values in <strong>Settings -&gt; Apps -&gt; FS25 Server Hub -&gt; Configuration</strong>, save them, then restart the app and use the tests below.</p>
  <div id="ready" class="callout">Checking configuration...</div>
</section>
<section id="sources" class="grid"></section>
<section class="grid">
  <article class="card">
    <h2>GPORTAL FTP</h2>
    <div id="ftp-status"></div>
    <div class="actions" style="margin-top:12px"><button id="test-ftp">Test FTP connection</button></div>
    <pre id="ftp-result" class="test-result">Not tested yet.</pre>
  </article>
  <article class="card">
    <h2>What happens next?</h2>
    <p><small>Once all five required HTTP feeds are configured, turn <code>setup_mode</code> off if you enabled it manually, save the Configuration and restart this app. The normal dashboard will then start.</small></p>
    <p><small>Missions and Placeables are optional. They can use HTTP URLs, or one GPORTAL FTP connection; Placeables is automatically read beside <code>missions.xml</code>.</small></p>
  </article>
</section>
<p class="footer">The setup checker never displays your FTP password. Tests use only the values already stored in Home Assistant.</p>
<script>
const sourceNames={stats:"Stats",map:"Map",career:"Career",vehicles:"Vehicles",economy:"Economy",missions:"Missions",placeables:"Placeables"};
const sourceOrder=["stats","map","career","vehicles","economy","missions","placeables"];
const ready=document.getElementById("ready"),sources=document.getElementById("sources"),ftpStatus=document.getElementById("ftp-status"),ftpResult=document.getElementById("ftp-result");
function esc(v){return String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")}
async function json(path,options={}){const r=await fetch(path,{credentials:"same-origin",cache:"no-store",...options});const p=await r.json();if(!r.ok)throw new Error(p.error||("HTTP "+r.status));return p}
function resultText(p){let out=`OK - ${p.label||p.source}\\nLatency: ${p.latency_ms} ms\\nPayload: ${p.bytes} bytes`;if(p.xml_root)out+=`\\nXML root: ${p.xml_root}`;if(p.content_type)out+=`\\nType: ${p.content_type}`;if(p.warning)out+=`\\nWarning: ${p.warning}`;return out}
async function refresh(){
 const data=await json("api/setup/status");
 ready.className="callout "+(data.ready_for_dashboard?"":"warn");
 ready.innerHTML=data.ready_for_dashboard
   ? "<strong>Required feeds are configured.</strong> You can test them below. If Setup mode was enabled manually, turn it off and restart to launch the dashboard."
   : "<strong>Setup is not complete.</strong> Missing required values: "+data.missing_required.map(x=>sourceNames[x]).join(", ")+".";
 sources.innerHTML=sourceOrder.map(key=>{const s=data.sources[key];return `<article class="card"><h2>${esc(s.label)} ${s.required?'<span class="badge">Required</span>':'<span class="badge">Optional</span>'}</h2><div class="row"><span>Configuration</span><span class="badge ${s.configured?'good':'warn'}">${s.configured?'Set':'Missing'}</span></div><div class="actions" style="margin-top:12px"><button data-test="${key}" ${s.configured?'':'disabled'}>Test ${esc(sourceNames[key])}</button></div><pre class="test-result" id="result-${key}">Not tested yet.</pre></article>`}).join("");
 const f=data.ftp;
 ftpStatus.innerHTML=`<div class="row"><span>Host</span><span class="badge ${f.host_set?'good':'warn'}">${f.host_set?'Set':'Missing'}</span></div><div class="row"><span>Port</span><strong>${esc(f.port)}</strong></div><div class="row"><span>Username</span><span class="badge ${f.username_set?'good':'warn'}">${f.username_set?'Set':'Missing'}</span></div><div class="row"><span>Password</span><span class="badge ${f.password_set?'good':'warn'}">${f.password_set?'Stored':'Missing'}</span></div><div class="row"><span>missions.xml path</span><span class="badge ${f.path_set?'good':'warn'}">${f.path_set?'Set':'Missing'}</span></div><div class="row"><span>Mode</span><strong>${f.tls?'Explicit FTPS':'FTP'} / ${f.passive?'Passive':'Active'}</strong></div>`;
 document.getElementById("test-ftp").disabled=!f.configured;
 document.querySelectorAll("[data-test]").forEach(button=>button.onclick=async()=>{const key=button.dataset.test,r=document.getElementById("result-"+key);button.disabled=true;r.textContent="Testing...";try{r.textContent=resultText(await json("api/setup/test?source="+encodeURIComponent(key),{method:"POST"}))}catch(e){r.textContent="FAILED - "+e.message}finally{button.disabled=false}});
}
document.getElementById("test-ftp").onclick=async()=>{const b=document.getElementById("test-ftp");b.disabled=true;ftpResult.textContent="Testing FTP, missions.xml and placeables.xml...";try{const p=await json("api/setup/test-ftp",{method:"POST"});let t=`OK - FTP connection\\nLatency: ${p.latency_ms} ms\\nmissions.xml: ${p.missions.bytes} bytes (${p.missions.xml_root})`;t+=p.placeables.ok?`\\nplaceables.xml: ${p.placeables.bytes} bytes (${p.placeables.xml_root})`:`\\nplaceables.xml: WARNING - ${p.placeables.error}`;ftpResult.textContent=t}catch(e){ftpResult.textContent="FAILED - "+e.message}finally{b.disabled=false}};
refresh().catch(e=>{ready.className="callout warn";ready.textContent="Could not read setup status: "+e.message});
</script>
</main></body></html>"""


class SetupHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FS25HubSetup/0.5.9"

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

    def do_GET(self) -> None:  # noqa: N802
        if not self.ingress_allowed():
            self.send_json({"error": "Ingress access only"}, HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        if path in ("/", "/setup"):
            self.send_body(page(), "text/html; charset=utf-8")
        elif path == "/api/setup/status":
            self.send_json(configured_status())
        else:
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not self.ingress_allowed():
            self.send_json({"error": "Ingress access only"}, HTTPStatus.FORBIDDEN)
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/setup/test":
                source = (parse_qs(parsed.query).get("source") or [""])[0]
                self.send_json(test_http_source(source))
            elif parsed.path == "/api/setup/test-ftp":
                self.send_json(test_ftp())
            else:
                self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("Setup test failed")
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    status = configured_status()
    if status["missing_required"]:
        LOGGER.warning("FS25 Server Hub setup mode: missing required sources: %s", ", ".join(status["missing_required"]))
    else:
        LOGGER.warning("FS25 Server Hub is running in manual setup/test mode")
    server = ThreadingHTTPServer((HOST, PORT), SetupHandler)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
