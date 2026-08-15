#!/usr/bin/env python3
"""FS25 Server Hub Home Assistant app.

Polls the GIANTS dedicated-server feeds, stores history in SQLite, and serves
an Ingress-friendly dashboard using only the Python standard library.
"""

from __future__ import annotations

import csv
import ftplib
import hashlib
import io
import json
import logging
import mimetypes
import os
import posixpath
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import combinations
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "fs25.db"
MAP_PATH = CACHE_DIR / "map.jpg"

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8099"))
ALLOW_DIRECT = os.getenv("ALLOW_DIRECT", "false").lower() == "true"

STATS_URL = os.getenv("FS25_STATS_URL", "").strip()
MAP_URL = os.getenv("FS25_MAP_URL", "").strip()
CAREER_URL = os.getenv("FS25_CAREER_URL", "").strip()
VEHICLES_URL = os.getenv("FS25_VEHICLES_URL", "").strip()
ECONOMY_URL = os.getenv("FS25_ECONOMY_URL", "").strip()
PLACEABLES_URL = os.getenv("FS25_PLACEABLES_URL", "").strip()
MISSIONS_URL = os.getenv("FS25_MISSIONS_URL", "").strip()
MISSIONS_FTP_HOST = os.getenv("FS25_MISSIONS_FTP_HOST", "").strip()
MISSIONS_FTP_PORT = max(1, int(os.getenv("FS25_MISSIONS_FTP_PORT", "21")))
MISSIONS_FTP_USERNAME = os.getenv("FS25_MISSIONS_FTP_USERNAME", "").strip()
MISSIONS_FTP_PASSWORD = os.getenv("FS25_MISSIONS_FTP_PASSWORD", "")
MISSIONS_FTP_PATH = os.getenv("FS25_MISSIONS_FTP_PATH", "").strip()
MISSIONS_FTP_TLS = os.getenv("FS25_MISSIONS_FTP_TLS", "false").lower() == "true"
MISSIONS_FTP_PASSIVE = os.getenv("FS25_MISSIONS_FTP_PASSIVE", "true").lower() == "true"

STATS_POLL_SECONDS = max(2, int(os.getenv("STATS_POLL_SECONDS", "60")))
SAVE_POLL_SECONDS = max(15, int(os.getenv("SAVE_POLL_SECONDS", "120")))
MAP_POLL_SECONDS = max(10, int(os.getenv("MAP_POLL_SECONDS", "60")))
MAP_HD_SIZE = max(512, min(4096, int(os.getenv("MAP_HD_SIZE", "2048"))))
MAP_HD_QUALITY = max(60, min(100, int(os.getenv("MAP_HD_QUALITY", "95"))))
REQUEST_TIMEOUT_SECONDS = max(5, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "60")))
CURRENCY_SYMBOL = os.getenv("CURRENCY_SYMBOL", "£") or "£"
SITE_TITLE = os.getenv("SITE_TITLE", "Elite Farming Server Hub") or "Elite Farming Server Hub"
ADAPTIVE_POLLING = os.getenv("ADAPTIVE_POLLING", "true").lower() == "true"
EMPTY_SERVER_SAVE_POLL_SECONDS = max(SAVE_POLL_SECONDS, int(os.getenv("EMPTY_SERVER_SAVE_POLL_SECONDS", "300")))
EMPTY_SERVER_MAP_POLL_SECONDS = max(MAP_POLL_SECONDS, int(os.getenv("EMPTY_SERVER_MAP_POLL_SECONDS", "600")))
BALANCE_SAMPLE_RETENTION_DAYS = max(30, int(os.getenv("BALANCE_SAMPLE_RETENTION_DAYS", "90")))

USER_AGENT = "HomeAssistant-FS25-Server-Hub/0.5.5"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("fs25-hub")

STATE_LOCK = threading.RLock()
STATE_VERSION_CONDITION = threading.Condition(STATE_LOCK)
STOP_EVENT = threading.Event()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def unix_now() -> int:
    return int(time.time())


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def clean_text(value: Any) -> str:
    return str(value or "").strip()


VOLATILE_COMPARE_KEYS = {"last_success", "latency_ms"}


def stable_payload(value: Any) -> Any:
    """Remove request-only metadata before deciding whether the UI changed."""
    if isinstance(value, dict):
        return {
            key: stable_payload(item)
            for key, item in value.items()
            if key not in VOLATILE_COMPARE_KEYS
        }
    if isinstance(value, list):
        return [stable_payload(item) for item in value]
    return value


def safe_tag_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def format_game_time(day_time_ms: int) -> str:
    if day_time_ms < 0:
        return "—"
    total_seconds = (day_time_ms // 1000) % 86400
    hour = total_seconds // 3600
    minute = (total_seconds % 3600) // 60
    return f"{hour:02d}:{minute:02d}"


def humanise_identifier(value: str) -> str:
    text = value.replace("_", " ").replace("-", " ")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    replacements = {
        "Mf": "MF",
        "Jd": "JD",
        "Gps": "GPS",
        "Ibc": "IBC",
        "Ai": "AI",
        "Eu": "EU",
    }
    words = []
    for word in text.split():
        words.append(replacements.get(word.title(), word.upper() if len(word) <= 2 else word.title()))
    return " ".join(words) or "Unknown Vehicle"


def vehicle_name_from_filename(filename: str) -> str:
    basename = Path(filename.replace("$moddir$", "")).stem
    return humanise_identifier(basename)


FILL_TYPE_LABELS = {
    "LIQUIDFERTILIZER": "Liquid Fertilizer",
    "FERTILIZER": "Fertilizer",
    "HERBICIDE": "Herbicide",
    "SEEDS": "Seeds",
    "LIME": "Lime",
    "MINERAL_FEED": "Mineral Feed",
    "MINERALFEED": "Mineral Feed",
    "CHICKEN_FOOD": "Chicken Food",
    "CHICKENFOOD": "Chicken Food",
    "PIG_FOOD": "Pig Food",
    "PIGFOOD": "Pig Food",
    "HORSE_FOOD": "Horse Food",
    "HORSEFOOD": "Horse Food",
    "SILAGE_ADDITIVE": "Silage Additive",
    "SILAGEADDITIVE": "Silage Additive",
    "TOTAL_MIXED_RATION": "Total Mixed Ration",
    "TMR": "Total Mixed Ration",
    "FORAGE": "Forage",
    "FORAGE_MIXING": "Forage Mix",
    "DIESEL": "Diesel",
    "DEF": "DEF",
    "METHANE": "Methane",
    "WATER": "Water",
    "ROAD_SALT": "Road Salt",
    "ROADSALT": "Road Salt",
    "EGG": "Eggs",
    "HONEY": "Honey",
    "WOOL": "Wool",
    "FLOUR": "Flour",
    "BREAD": "Bread",
    "CAKE": "Cake",
    "BUTTER": "Butter",
    "CHEESE": "Cheese",
    "CHOCOLATE": "Chocolate",
    "CEREAL": "Cereal",
    "CLOTHES": "Clothes",
    "FABRIC": "Fabric",
    "FURNITURE": "Furniture",
    "PLANKS": "Planks",
    "PAPER": "Paper",
    "ROPE": "Rope",
    "OLIVE_OIL": "Olive Oil",
    "CANOLA_OIL": "Canola Oil",
    "SUNFLOWER_OIL": "Sunflower Oil",
    "GRAPE_JUICE": "Grape Juice",
    "RAISINS": "Raisins",
    "SUGAR": "Sugar",
    "STRAWBERRY": "Strawberries",
    "LETTUCE": "Lettuce",
    "TOMATO": "Tomatoes",
}

CROP_INPUT_FILL_TYPES = {
    "FERTILIZER", "LIQUIDFERTILIZER", "HERBICIDE", "SEEDS", "LIME",
    "SILAGE_ADDITIVE", "SILAGEADDITIVE", "ROAD_SALT", "ROADSALT",
}

ANIMAL_FEED_FILL_TYPES = {
    "MINERAL_FEED", "MINERALFEED", "CHICKEN_FOOD", "CHICKENFOOD",
    "PIG_FOOD", "PIGFOOD", "HORSE_FOOD", "HORSEFOOD",
    "TOTAL_MIXED_RATION", "TMR", "FORAGE", "FORAGE_MIXING",
}

FUEL_UTILITY_FILL_TYPES = {"DIESEL", "DEF", "METHANE", "WATER"}
SUPPLY_FILL_TYPES = CROP_INPUT_FILL_TYPES | ANIMAL_FEED_FILL_TYPES | FUEL_UTILITY_FILL_TYPES

PRODUCT_FILL_TYPES = {
    "EGG", "HONEY", "WOOL", "FLOUR", "BREAD", "CAKE", "BUTTER",
    "CHEESE", "CHOCOLATE", "CEREAL", "CLOTHES", "FABRIC", "FURNITURE",
    "PLANKS", "PAPER", "ROPE", "OLIVE_OIL", "CANOLA_OIL", "SUNFLOWER_OIL",
    "GRAPE_JUICE", "RAISINS", "SUGAR", "STRAWBERRY", "LETTUCE", "TOMATO",
}

SUPPLY_PATH_MARKERS = (
    "chickenfood", "chicken_food", "pigfood", "pig_food", "horsefood", "horse_food",
    "mineralfeed", "mineral_feed", "totalmixedration", "total_mixed_ration", "foragemix",
    "fertilizer", "liquidfertilizer", "herbicide", "silageadditive", "silage_additive",
    "/seed", "seeds", "/lime", "roadsalt", "road_salt", "/diesel", "/def/", "methane",
)

STRONG_SUPPLY_EVENT_MARKERS = (
    "big bag chicken food", "chicken food pallet", "big bag pig food", "pig food pallet",
    "big bag horse food", "horse food pallet", "mineral feed pallet", "fertilizer pallet",
    "liquid fertilizer tank", "herbicide tank", "seed pallet", "big bag seeds",
    "big bag fertilizer", "big bag lime", "lime pallet", "silage additive pallet",
)

MISSION_LABELS = {
    "treeTransport": "Tree transport",
    "destructibleRock": "Rock breaking",
    "deadwood": "Deadwood removal",
    "plow": "Ploughing",
    "cultivate": "Cultivating",
    "harvest": "Harvesting",
    "mowBale": "Mowing and baling",
    "bale": "Baling",
    "sow": "Sowing",
    "fertilize": "Fertilizing",
    "weed": "Weeding",
    "stonePick": "Stone picking",
    "transport": "Transport",
    "supplyTransport": "Supply transport",
    "herbicide": "Spraying",
}



def fill_type_label(value: str) -> str:
    value = clean_text(value).upper()
    return FILL_TYPE_LABELS.get(value, humanise_identifier(value))


def primary_fill(fills: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not fills:
        return None
    return max(fills, key=lambda item: as_float(item.get("level")))


def supply_group_for_object(filename: str, fills: list[dict[str, Any]]) -> str | None:
    path = filename.replace("\\", "/").lower()
    fill_types = {clean_text(item.get("fill_type")).upper() for item in fills}
    if fill_types & ANIMAL_FEED_FILL_TYPES or any(
        marker in path
        for marker in (
            "chickenfood", "chicken_food", "pigfood", "pig_food", "horsefood", "horse_food",
            "mineralfeed", "mineral_feed", "totalmixedration", "total_mixed_ration", "foragemix",
        )
    ):
        return "animal_feed"
    if fill_types & CROP_INPUT_FILL_TYPES or any(
        marker in path
        for marker in ("fertilizer", "herbicide", "/seed", "seeds", "/lime", "silageadditive", "roadsalt")
    ):
        return "crop_input"
    if fill_types & FUEL_UTILITY_FILL_TYPES or any(marker in path for marker in ("/diesel", "/def/", "methane", "watertank")):
        return "fuel_utility"
    return "other_supply"


def classify_saved_object(filename: str, vehicle: ET.Element, fills: list[dict[str, Any]]) -> str:
    path = filename.replace("\\", "/").lower()
    fill_types = {clean_text(item.get("fill_type")).upper() for item in fills}
    has_pallet = vehicle.find("pallet") is not None
    has_bale = vehicle.find("bale") is not None or "/bale" in path
    object_like = has_pallet or has_bale or "/objects/" in path or "/bigbag" in path or "/pallet" in path

    # A drivable machine or implement remains fleet even when it currently carries
    # seed, fertilizer or a sellable product. Only saved object-style records are
    # classified as pallets, supplies, products or bales.
    if not object_like:
        return "fleet"
    if fill_types & SUPPLY_FILL_TYPES or any(marker in path for marker in SUPPLY_PATH_MARKERS):
        return "supply"
    if fill_types & PRODUCT_FILL_TYPES:
        return "product"
    if has_bale:
        return "bale"
    return "pallet"


def saved_object_name(filename: str, kind: str, fills: list[dict[str, Any]]) -> str:
    path = filename.replace("\\", "/").lower()
    fill = primary_fill(fills)
    label = fill_type_label(clean_text(fill.get("fill_type"))) if fill is not None else ""

    path_names = (
        (("chickenfood", "chicken_food"), "Chicken Food"),
        (("pigfood", "pig_food"), "Pig Food"),
        (("horsefood", "horse_food"), "Horse Food"),
        (("mineralfeed", "mineral_feed"), "Mineral Feed"),
        (("totalmixedration", "total_mixed_ration"), "Total Mixed Ration"),
        (("silageadditive", "silage_additive"), "Silage Additive"),
    )
    for markers, path_label in path_names:
        if any(marker in path for marker in markers):
            label = path_label
            break

    if label:
        if kind == "supply":
            if "bigbag" in path:
                return f"Big Bag {label}"
            if "tank" in path:
                return f"{label} Tank"
            return f"{label} Pallet"
        if kind == "product":
            return label
        if kind in {"pallet", "bale"}:
            return label

    name = vehicle_name_from_filename(filename)
    name = re.sub(r"\b(Base|Pallet)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Saved Object"


def mission_type_from_tag(tag: str) -> str:
    return re.sub(r"Mission$", "", safe_tag_name(tag))


def mission_label(mission_type: str) -> str:
    return MISSION_LABELS.get(mission_type, humanise_identifier(mission_type))


def mission_title(item: dict[str, Any]) -> str:
    label = clean_text(item.get("label")) or "Contract"
    field_id = item.get("field_id")
    return f"{label} — Field {field_id}" if field_id is not None else label


UNCLASSIFIED_EVENT_TYPES = {"income", "expense", "money_change"}
REVIEW_CATEGORIES: dict[str, dict[str, str]] = {
    "production_autosale": {"label": "Production autosale", "income_title": "Production autosale", "expense_title": "Production adjustment"},
    "contract_payment": {"label": "Contract payment", "income_title": "Contract payment", "expense_title": "Contract adjustment"},
    "product_sale": {"label": "Crop or product sale", "income_title": "Product sale", "expense_title": "Product adjustment"},
    "animal_sale": {"label": "Animal sale", "income_title": "Animal sale", "expense_title": "Animal adjustment"},
    "animal_purchase": {"label": "Animal purchase", "income_title": "Animal refund", "expense_title": "Animal purchase"},
    "supply_purchase": {"label": "Supplies", "income_title": "Supply refund", "expense_title": "Supplies purchased"},
    "vehicle_purchase": {"label": "Vehicle or machinery purchase", "income_title": "Vehicle refund", "expense_title": "Vehicle purchase"},
    "vehicle_sale": {"label": "Vehicle or machinery sale", "income_title": "Vehicle sale", "expense_title": "Vehicle adjustment"},
    "vehicle_repair": {"label": "Vehicle repairs", "income_title": "Vehicle repair refund", "expense_title": "Vehicle repairs"},
    "land_purchase": {"label": "Land purchase", "income_title": "Land refund", "expense_title": "Land purchase"},
    "land_sale": {"label": "Land sale", "income_title": "Land sale", "expense_title": "Land adjustment"},
    "building_purchase": {"label": "Building or construction", "income_title": "Construction refund", "expense_title": "Building or construction purchase"},
    "loan_income": {"label": "Loan received", "income_title": "Loan received", "expense_title": "Loan adjustment"},
    "loan_repayment": {"label": "Loan repayment", "income_title": "Loan adjustment", "expense_title": "Loan repayment"},
    "lease_expense": {"label": "Lease or rental", "income_title": "Lease refund", "expense_title": "Lease or rental cost"},
    "operating_expense": {"label": "Operating expense", "income_title": "Operating refund", "expense_title": "Operating expense"},
    "other_income": {"label": "Other income", "income_title": "Other income", "expense_title": "Other adjustment"},
    "other_expense": {"label": "Other expense", "income_title": "Other adjustment", "expense_title": "Other expense"},
    "ignored": {"label": "Ignore from economy", "income_title": "Ignored credit", "expense_title": "Ignored debit"},
}


def request_bytes(url: str, timeout: int | None = None) -> bytes:
    if not url:
        raise ValueError("Feed URL is not configured")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout or REQUEST_TIMEOUT_SECONDS) as response:
        return response.read()


def missions_ftp_configured() -> bool:
    return bool(MISSIONS_FTP_HOST and MISSIONS_FTP_USERNAME and MISSIONS_FTP_PATH)


def missions_source_configured() -> bool:
    return bool(MISSIONS_URL or missions_ftp_configured())


def request_ftp_file(
    host: str,
    port: int,
    username: str,
    password: str,
    remote_path: str,
    *,
    use_tls: bool = False,
    passive: bool = True,
    timeout: int | None = None,
) -> bytes:
    """Download one file from FTP/explicit FTPS without logging credentials."""
    cleaned_host = host.strip()
    if "://" in cleaned_host:
        parsed = urlparse(cleaned_host)
        cleaned_host = parsed.hostname or cleaned_host
        if parsed.port:
            port = parsed.port
        if parsed.scheme.lower() == "ftps":
            use_tls = True
    if not cleaned_host:
        raise ValueError("FTP host is not configured")

    normalised_path = remote_path.replace("\\", "/").strip()
    directory, filename = posixpath.split(normalised_path)
    if not filename:
        raise ValueError("FTP path must include a filename")

    client_class = ftplib.FTP_TLS if use_tls else ftplib.FTP
    client = client_class(timeout=timeout or REQUEST_TIMEOUT_SECONDS)
    buffer = io.BytesIO()
    try:
        client.connect(cleaned_host, port)
        client.login(username, password)
        if use_tls:
            client.prot_p()
        client.set_pasv(passive)
        if directory and directory != ".":
            client.cwd(directory)
        client.retrbinary(f"RETR {filename}", buffer.write)
        try:
            client.quit()
        except (OSError, EOFError, ftplib.Error):
            client.close()
    except Exception:
        try:
            client.close()
        except OSError:
            pass
        raise
    return buffer.getvalue()


def request_ftp_files(remote_paths: dict[str, str]) -> dict[str, bytes]:
    """Download several savegame files over one FTP/FTPS login."""
    if not remote_paths:
        return {}
    cleaned_host = MISSIONS_FTP_HOST.strip()
    port = MISSIONS_FTP_PORT
    use_tls = MISSIONS_FTP_TLS
    if "://" in cleaned_host:
        parsed = urlparse(cleaned_host)
        cleaned_host = parsed.hostname or cleaned_host
        if parsed.port:
            port = parsed.port
        if parsed.scheme.lower() == "ftps":
            use_tls = True
    if not cleaned_host:
        raise ValueError("FTP host is not configured")
    client_class = ftplib.FTP_TLS if use_tls else ftplib.FTP
    client = client_class(timeout=REQUEST_TIMEOUT_SECONDS)
    results: dict[str, bytes] = {}
    try:
        client.connect(cleaned_host, port)
        client.login(MISSIONS_FTP_USERNAME, MISSIONS_FTP_PASSWORD)
        if use_tls:
            client.prot_p()
        client.set_pasv(MISSIONS_FTP_PASSIVE)
        for key, remote_path in remote_paths.items():
            normalised = remote_path.replace("\\", "/").strip()
            directory, filename = posixpath.split(normalised)
            if not filename:
                raise ValueError(f"FTP path for {key} must include a filename")
            try:
                client.cwd("/")
            except ftplib.Error:
                pass
            if directory and directory not in {".", "/"}:
                client.cwd(directory)
            buffer = io.BytesIO()
            client.retrbinary(f"RETR {filename}", buffer.write)
            results[key] = buffer.getvalue()
        try:
            client.quit()
        except (OSError, EOFError, ftplib.Error):
            client.close()
    except Exception:
        try:
            client.close()
        except OSError:
            pass
        raise
    return results


def request_missions_bytes() -> bytes:
    if MISSIONS_URL:
        return request_bytes(MISSIONS_URL)
    if missions_ftp_configured():
        return request_ftp_file(
            MISSIONS_FTP_HOST,
            MISSIONS_FTP_PORT,
            MISSIONS_FTP_USERNAME,
            MISSIONS_FTP_PASSWORD,
            MISSIONS_FTP_PATH,
            use_tls=MISSIONS_FTP_TLS,
            passive=MISSIONS_FTP_PASSIVE,
        )
    raise ValueError("Neither missions_url nor the missions FTP connection is configured")


def derived_placeables_ftp_path() -> str:
    """Use placeables.xml from the same active savegame folder as missions.xml."""
    normalised = MISSIONS_FTP_PATH.replace("\\", "/").strip()
    if not normalised:
        return ""
    directory = posixpath.dirname(normalised)
    return posixpath.join(directory, "placeables.xml") if directory else "placeables.xml"


def placeables_source_configured() -> bool:
    return bool(PLACEABLES_URL or (MISSIONS_FTP_HOST and MISSIONS_FTP_USERNAME and derived_placeables_ftp_path()))


def request_placeables_bytes() -> bytes:
    if PLACEABLES_URL:
        return request_bytes(PLACEABLES_URL)
    remote_path = derived_placeables_ftp_path()
    if MISSIONS_FTP_HOST and MISSIONS_FTP_USERNAME and remote_path:
        return request_ftp_file(
            MISSIONS_FTP_HOST,
            MISSIONS_FTP_PORT,
            MISSIONS_FTP_USERNAME,
            MISSIONS_FTP_PASSWORD,
            remote_path,
            use_tls=MISSIONS_FTP_TLS,
            passive=MISSIONS_FTP_PASSIVE,
        )
    raise ValueError("Neither placeables_url nor a reusable savegame FTP connection is configured")


def map_url_with_quality(url: str, size: int, quality: int) -> str:
    """Return a GIANTS map URL with its image parameters upgraded."""
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["size"] = str(size)
    query["quality"] = str(quality)
    return urlunparse(parsed._replace(query=urlencode(query)))


def map_url_candidates(url: str) -> list[str]:
    sizes = [MAP_HD_SIZE]
    if MAP_HD_SIZE > 2048:
        sizes.append(2048)
    if MAP_HD_SIZE > 1024:
        sizes.append(1024)
    sizes.append(512)

    candidates: list[str] = []
    for size in sizes:
        candidate = map_url_with_quality(url, size, MAP_HD_QUALITY)
        if candidate not in candidates:
            candidates.append(candidate)
    if url not in candidates:
        candidates.append(url)
    return candidates


def image_dimensions(content: bytes) -> tuple[int, int]:
    """Read PNG or JPEG dimensions without an additional image library."""
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
        return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")

    if content[:2] == b"\xff\xd8":
        offset = 2
        while offset + 9 < len(content):
            if content[offset] != 0xFF:
                offset += 1
                continue
            while offset < len(content) and content[offset] == 0xFF:
                offset += 1
            if offset >= len(content):
                break
            marker = content[offset]
            offset += 1
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(content):
                break
            length = int.from_bytes(content[offset:offset + 2], "big")
            if length < 2 or offset + length > len(content):
                break
            if marker in {
                0xC0, 0xC1, 0xC2, 0xC3,
                0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB,
                0xCD, 0xCE, 0xCF,
            } and length >= 7:
                height = int.from_bytes(content[offset + 3:offset + 5], "big")
                width = int.from_bytes(content[offset + 5:offset + 7], "big")
                return width, height
            offset += length
    return 0, 0


def image_content_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG"):
        return "image/png"
    return "image/jpeg"


def safe_map_source_label(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    size = query.get("size", "auto")
    quality = query.get("quality", "auto")
    return f"GIANTS live feed · requested {size}px / quality {quality}"


def parse_xml_bytes(content: bytes) -> ET.Element:
    # utf-8-sig handles the BOM present in some GIANTS responses.
    return ET.fromstring(content.decode("utf-8-sig", errors="replace"))


def text_of(parent: ET.Element | None, tag: str, default: str = "") -> str:
    if parent is None:
        return default
    found = parent.find(tag)
    return clean_text(found.text if found is not None else default)


def database_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def legacy_event_looks_like_supply(title: str, detail: str, meta: dict[str, Any]) -> bool:
    haystack = f"{title} {detail} {json.dumps(meta, ensure_ascii=False)}".lower()
    return any(marker in haystack for marker in STRONG_SUPPLY_EVENT_MARKERS)


def migrate_legacy_economy_events() -> int:
    """Repair old big-bag/pallet entries that earlier builds labelled as vehicles."""
    changed = 0
    with database_connection() as db:
        rows = db.execute(
            """
            SELECT id, event_type, title, detail, meta_json
            FROM events
            WHERE event_type IN ('vehicle_purchase', 'farm_purchase')
            """
        ).fetchall()
        for row in rows:
            try:
                meta = json.loads(row["meta_json"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            if meta.get("legacy_supply_reclassified") or not legacy_event_looks_like_supply(row["title"], row["detail"], meta):
                continue
            meta["legacy_supply_reclassified"] = True
            meta.setdefault("sources", ["vehicles.xml", "careerSavegame.xml"])
            evidence = meta.setdefault("evidence", [])
            message = "Reclassified from an older vehicle label using the saved big-bag/pallet description"
            if message not in evidence:
                evidence.append(message)
            title = re.sub(
                r"^(Vehicle purchase(?: detected)?|Farm purchases detected)\s*:\s*",
                "Supplies purchased: ",
                row["title"],
                flags=re.IGNORECASE,
            )
            if title == row["title"]:
                title = f"Supplies purchased: {row['title']}"
            db.execute(
                """
                UPDATE events
                SET event_type = 'supply_purchase', title = ?,
                    detail = 'Consumable pallet or big bag identified from the saved object record',
                    meta_json = ?
                WHERE id = ?
                """,
                (title, json.dumps(meta, ensure_ascii=False), row["id"]),
            )
            changed += 1
    return changed


def migrate_legacy_rock_contract_payments() -> int:
    """Recover rock-contract income that 0.4.0 could label as unclassified.

    The old matcher could record a destructible-rock mission as cancelled when
    the final SUCCESS snapshot was skipped.  When a nearby positive ledger
    entry exactly matches that mission's saved payout, retain the audit trail
    but reclassify it as an inferred contract payment.
    """
    changed = 0
    with database_connection() as db:
        incomes = db.execute(
            """
            SELECT id, ts, event_type, title, detail, amount, confidence, meta_json
            FROM events
            WHERE event_type IN ('income', 'money_change') AND amount > 0
            ORDER BY ts ASC, id ASC
            """
        ).fetchall()
        removals = db.execute(
            """
            SELECT id, ts, event_type, title, detail, amount, confidence, meta_json
            FROM events
            WHERE event_type = 'contract_cancelled'
            ORDER BY ts ASC, id ASC
            """
        ).fetchall()
        used_removals: set[int] = set()

        for income in incomes:
            try:
                income_meta = json.loads(income["meta_json"] or "{}")
            except json.JSONDecodeError:
                income_meta = {}
            if income_meta.get("legacy_rock_contract_reclassified"):
                continue

            amount = as_float(income["amount"])
            best: tuple[tuple[int, float], sqlite3.Row, dict[str, Any], dict[str, Any]] | None = None
            for removal in removals:
                if removal["id"] in used_removals or abs(as_int(income["ts"]) - as_int(removal["ts"])) > 1800:
                    continue
                try:
                    removal_meta = json.loads(removal["meta_json"] or "{}")
                except json.JSONDecodeError:
                    removal_meta = {}
                mission = removal_meta.get("mission") or {}
                if clean_text(mission.get("type")) != "destructibleRock":
                    continue
                expected = as_float(mission.get("expected_payout"))
                if expected <= 0:
                    expected = as_float(mission.get("reward")) + as_float(mission.get("reimbursement"))
                tolerance = max(1.0, expected * 0.02)
                difference = abs(amount - expected)
                if expected <= 0 or difference > tolerance:
                    continue
                score = (abs(as_int(income["ts"]) - as_int(removal["ts"])), difference)
                if best is None or score < best[0]:
                    best = (score, removal, removal_meta, mission)

            if best is None:
                continue

            _, removal, removal_meta, mission = best
            used_removals.add(removal["id"])
            expected = round(as_float(mission.get("expected_payout")) or (as_float(mission.get("reward")) + as_float(mission.get("reimbursement"))), 2)
            sources = income_meta.setdefault("sources", [])
            for source in ("careerSavegame.xml", "missions.xml"):
                if source not in sources:
                    sources.append(source)
            evidence = income_meta.setdefault("evidence", [])
            recovery_evidence = "Recovered by matching a removed rock-breaking contract to its listed payout"
            if recovery_evidence not in evidence:
                evidence.append(recovery_evidence)
            income_meta.update(
                {
                    "legacy_rock_contract_reclassified": True,
                    "missions": [mission],
                    "confidence_reason": "The accepted rock-breaking contract disappeared and its listed payout matched the balance increase; the final SUCCESS save was missed",
                    "contract_match": {
                        "quality": "historical_removed_mission_amount_match",
                        "mission_count": 1,
                        "listed_reward": round(as_float(mission.get("reward")), 2),
                        "reimbursement": round(as_float(mission.get("reimbursement")), 2),
                        "expected_payout": expected,
                        "captured_balance_change": amount,
                        "variance": round(amount - expected, 2),
                    },
                }
            )
            db.execute(
                """
                UPDATE events
                SET event_type = 'contract_payment', title = ?, detail = ?,
                    confidence = 'inferred', meta_json = ?
                WHERE id = ?
                """,
                (
                    f"Contract payment: {mission_title(mission)}",
                    "Recovered from the rock-breaking contract lifecycle and an exact listed-payout match",
                    json.dumps(income_meta, ensure_ascii=False),
                    income["id"],
                ),
            )

            removal_meta["recovered_as_contract_payment"] = True
            removal_meta["matched_transaction_id"] = income["id"]
            db.execute(
                """
                UPDATE events
                SET event_type = 'contract_completed', title = ?, detail = ?,
                    confidence = 'inferred', meta_json = ?
                WHERE id = ?
                """,
                (
                    f"Contract completed: {mission_title(mission)}",
                    "Completion recovered from the matching rock-breaking payout after the final SUCCESS save was missed",
                    json.dumps(removal_meta, ensure_ascii=False),
                    removal["id"],
                ),
            )
            changed += 1
    return changed


def migrate_recent_production_income(productions: dict[str, Any] | None) -> int:
    """Conservatively relabel repeated recent unknown income as likely autosales."""
    outputs = production_autosale_candidates(productions)
    if not outputs:
        return 0
    since = unix_now() - 7 * 86400
    candidates: list[tuple[sqlite3.Row, dict[str, Any]]] = []
    with database_connection() as db:
        rows = db.execute(
            """
            SELECT id, ts, title, detail, amount, confidence, meta_json
            FROM events
            WHERE event_type = 'income' AND amount > 0 AND ts >= ?
            ORDER BY ts ASC, id ASC
            """,
            (since,),
        ).fetchall()
        for row in rows:
            try:
                meta = json.loads(row["meta_json"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            if meta.get("historical_production_autosale_reclassified"):
                continue
            if meta.get("missions") or meta.get("added_assets") or meta.get("removed_assets") or meta.get("added_supplies") or meta.get("removed_products"):
                continue
            if meta.get("inventory_decreases") or meta.get("inventory_increases"):
                continue
            sources = set(meta.get("sources") or [])
            if sources and sources - {"careerSavegame.xml"}:
                continue
            candidates.append((row, meta))

        # Repeated unknown credits are the pattern produced by periodic autoselling.
        # A single unknown credit remains untouched to avoid over-classifying it.
        if len(candidates) < 3:
            return 0

        base_title, base_detail = production_autosale_summary(outputs)
        title = base_title.replace("Production autosale", "Likely production autosale", 1)
        changed = 0
        for row, meta in candidates:
            meta["historical_production_autosale_reclassified"] = True
            meta["production_autosale"] = {"mode": "DIRECT_SELL", "outputs": outputs, "historical_inference": True}
            sources = meta.setdefault("sources", [])
            for source in ("careerSavegame.xml", "placeables.xml"):
                if source not in sources:
                    sources.append(source)
            evidence = meta.setdefault("evidence", [])
            note = "Repeated unknown income reclassified because owned production outputs are configured for direct selling"
            if note not in evidence:
                evidence.append(note)
            meta["confidence_reason"] = "This older credit had no contract, product, fleet or inventory evidence and matches the repeated-income pattern expected from configured production autosales"
            db.execute(
                """
                UPDATE events
                SET event_type = 'production_autosale', title = ?, detail = ?,
                    confidence = 'inferred', meta_json = ?
                WHERE id = ?
                """,
                (title, f"{base_detail} · historical repeated-income match", json.dumps(meta, ensure_ascii=False), row["id"]),
            )
            changed += 1
    return changed


def initialise_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with database_connection() as db:
        db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                amount REAL,
                confidence TEXT NOT NULL DEFAULT 'confirmed',
                meta_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player TEXT NOT NULL,
                joined_at INTEGER NOT NULL,
                left_at INTEGER,
                duration_seconds INTEGER,
                is_admin INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_player ON sessions(player, joined_at DESC);
            CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(left_at);

            CREATE TABLE IF NOT EXISTS balance_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER NOT NULL,
                balance REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_balance_ts ON balance_samples(ts DESC);

            CREATE TABLE IF NOT EXISTS snapshots (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS classification_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction INTEGER NOT NULL,
                original_event_type TEXT NOT NULL,
                min_amount REAL NOT NULL,
                max_amount REAL NOT NULL,
                category TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                use_count INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_classification_rules_match
                ON classification_rules(enabled, direction, original_event_type, min_amount, max_amount);

            CREATE TABLE IF NOT EXISTS daily_balance_samples (
                day TEXT PRIMARY KEY,
                opening_balance REAL NOT NULL,
                closing_balance REAL NOT NULL,
                low_balance REAL NOT NULL,
                high_balance REAL NOT NULL,
                sample_count INTEGER NOT NULL
            );
            """
        )
    repaired = migrate_legacy_economy_events()
    if repaired:
        LOGGER.info("Reclassified %s legacy supply purchase entr%s", repaired, "y" if repaired == 1 else "ies")
    recovered_rock_payments = migrate_legacy_rock_contract_payments()
    if recovered_rock_payments:
        LOGGER.info(
            "Recovered %s legacy rock-contract payment%s",
            recovered_rock_payments,
            "" if recovered_rock_payments == 1 else "s",
        )
    database_housekeeping()


def load_snapshot(key: str) -> Any | None:
    with database_connection() as db:
        row = db.execute("SELECT value_json FROM snapshots WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return None


def save_snapshot(key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    with database_connection() as db:
        db.execute(
            """
            INSERT INTO snapshots(key, value_json, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (key, payload, unix_now()),
        )


def add_event(
    event_type: str,
    title: str,
    detail: str = "",
    amount: float | None = None,
    confidence: str = "confirmed",
    meta: dict[str, Any] | None = None,
    ts: int | None = None,
) -> None:
    with database_connection() as db:
        db.execute(
            """
            INSERT INTO events(ts, event_type, title, detail, amount, confidence, meta_json)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts or unix_now(),
                event_type,
                title,
                detail,
                amount,
                confidence,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )


def start_session(player: str, is_admin: bool, joined_at: int | None = None) -> None:
    with database_connection() as db:
        active = db.execute(
            "SELECT id FROM sessions WHERE player = ? AND left_at IS NULL ORDER BY id DESC LIMIT 1",
            (player,),
        ).fetchone()
        if active:
            return
        db.execute(
            "INSERT INTO sessions(player, joined_at, is_admin) VALUES(?, ?, ?)",
            (player, joined_at or unix_now(), 1 if is_admin else 0),
        )


def close_session(player: str, left_at: int | None = None) -> int:
    ended = left_at or unix_now()
    with database_connection() as db:
        row = db.execute(
            "SELECT id, joined_at FROM sessions WHERE player = ? AND left_at IS NULL ORDER BY id DESC LIMIT 1",
            (player,),
        ).fetchone()
        if not row:
            return 0
        duration = max(0, ended - as_int(row["joined_at"]))
        db.execute(
            "UPDATE sessions SET left_at = ?, duration_seconds = ? WHERE id = ?",
            (ended, duration, row["id"]),
        )
    return duration


def record_balance(balance: float) -> None:
    with database_connection() as db:
        db.execute(
            "INSERT INTO balance_samples(ts, balance) VALUES(?, ?)",
            (unix_now(), balance),
        )


def get_recent_events(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 500)
    offset = max(offset, 0)
    with database_connection() as db:
        rows = db.execute(
            """
            SELECT id, ts, event_type, title, detail, amount, confidence, meta_json
            FROM events ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    events = []
    for row in rows:
        item = dict(row)
        try:
            item["meta"] = json.loads(item.pop("meta_json"))
        except json.JSONDecodeError:
            item["meta"] = {}
            item.pop("meta_json", None)
        events.append(item)
    return events


def database_housekeeping() -> dict[str, int]:
    """Compress old balance samples without removing transaction or play history."""
    cutoff = unix_now() - BALANCE_SAMPLE_RETENTION_DAYS * 86400
    aggregated = 0
    deleted = 0
    with database_connection() as db:
        days = db.execute(
            """
            SELECT date(ts, 'unixepoch', 'localtime') AS day,
                   MIN(ts) AS first_ts, MAX(ts) AS last_ts,
                   MIN(balance) AS low_balance, MAX(balance) AS high_balance,
                   COUNT(*) AS sample_count
            FROM balance_samples
            WHERE ts < ?
            GROUP BY day
            """,
            (cutoff,),
        ).fetchall()
        for day in days:
            opening = db.execute(
                "SELECT balance FROM balance_samples WHERE ts = ? ORDER BY id ASC LIMIT 1",
                (day["first_ts"],),
            ).fetchone()
            closing = db.execute(
                "SELECT balance FROM balance_samples WHERE ts = ? ORDER BY id DESC LIMIT 1",
                (day["last_ts"],),
            ).fetchone()
            if not opening or not closing:
                continue
            db.execute(
                """
                INSERT INTO daily_balance_samples(day, opening_balance, closing_balance, low_balance, high_balance, sample_count)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET
                    opening_balance = excluded.opening_balance,
                    closing_balance = excluded.closing_balance,
                    low_balance = MIN(daily_balance_samples.low_balance, excluded.low_balance),
                    high_balance = MAX(daily_balance_samples.high_balance, excluded.high_balance),
                    sample_count = MAX(daily_balance_samples.sample_count, excluded.sample_count)
                """,
                (day["day"], opening["balance"], closing["balance"], day["low_balance"], day["high_balance"], day["sample_count"]),
            )
            aggregated += 1
        cursor = db.execute("DELETE FROM balance_samples WHERE ts < ?", (cutoff,))
        deleted = max(cursor.rowcount, 0)
        db.execute("PRAGMA optimize")
    if deleted:
        LOGGER.info("Compressed %s old balance samples into %s daily summaries", deleted, aggregated)
    return {"compressed_days": aggregated, "deleted_samples": deleted}


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["meta"] = json.loads(item.pop("meta_json"))
    except json.JSONDecodeError:
        item["meta"] = {}
        item.pop("meta_json", None)
    return item


def get_review_queue(limit: int = 50) -> list[dict[str, Any]]:
    with database_connection() as db:
        rows = db.execute(
            """
            SELECT id, ts, event_type, title, detail, amount, confidence, meta_json
            FROM events
            WHERE event_type IN ('income', 'expense', 'money_change') AND amount IS NOT NULL
            ORDER BY ts DESC, id DESC LIMIT ?
            """,
            (min(max(limit, 1), 200),),
        ).fetchall()
    return [_event_from_row(row) for row in rows]


def get_classification_rules() -> list[dict[str, Any]]:
    with database_connection() as db:
        rows = db.execute(
            """
            SELECT id, direction, original_event_type, min_amount, max_amount,
                   category, label, enabled, use_count, created_at, updated_at
            FROM classification_rules
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def review_payload() -> dict[str, Any]:
    return {
        "queue": get_review_queue(),
        "rules": get_classification_rules(),
        "categories": [
            {"value": key, "label": value["label"]}
            for key, value in REVIEW_CATEGORIES.items()
        ],
    }


def classify_event(event_id: int, category: str, label: str = "", remember_rule: bool = False) -> dict[str, Any]:
    if category not in REVIEW_CATEGORIES:
        raise ValueError("Unknown review category")
    with database_connection() as db:
        row = db.execute(
            """
            SELECT id, ts, event_type, title, detail, amount, confidence, meta_json
            FROM events WHERE id = ?
            """,
            (event_id,),
        ).fetchone()
        if not row:
            raise ValueError("Transaction was not found")
        if row["event_type"] not in UNCLASSIFIED_EVENT_TYPES:
            raise ValueError("Transaction has already been classified")
        amount = as_float(row["amount"])
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        original = {
            "event_type": row["event_type"],
            "title": row["title"],
            "detail": row["detail"],
            "confidence": row["confidence"],
        }
        category_info = REVIEW_CATEGORIES[category]
        chosen_label = clean_text(label)
        default_title = category_info["income_title"] if amount >= 0 else category_info["expense_title"]
        new_title = chosen_label or default_title
        meta["manual_review"] = {
            "reviewed_at": unix_now(),
            "category": category,
            "label": chosen_label,
            "original": original,
        }
        meta["confidence_reason"] = "Manually reviewed and classified in the dashboard"
        evidence = meta.setdefault("evidence", [])
        note = f"Manually classified as {category_info['label']}"
        if note not in evidence:
            evidence.append(note)
        db.execute(
            """
            UPDATE events
            SET event_type = ?, title = ?, detail = ?, confidence = 'manual', meta_json = ?
            WHERE id = ?
            """,
            (
                category,
                new_title,
                f"{row['detail']} · reviewed manually" if row["detail"] else "Reviewed manually",
                json.dumps(meta, ensure_ascii=False),
                event_id,
            ),
        )
        rule_id = None
        if remember_rule and category != "ignored":
            tolerance = max(1.0, abs(amount) * 0.05)
            minimum = max(0.0, abs(amount) - tolerance)
            maximum = abs(amount) + tolerance
            now = unix_now()
            cursor = db.execute(
                """
                INSERT INTO classification_rules(
                    direction, original_event_type, min_amount, max_amount, category, label,
                    enabled, use_count, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                """,
                (1 if amount >= 0 else -1, row["event_type"], minimum, maximum, category, chosen_label, now, now),
            )
            rule_id = cursor.lastrowid
    bump_version()
    return {"event_id": event_id, "category": category, "rule_id": rule_id}


def delete_classification_rule(rule_id: int) -> None:
    with database_connection() as db:
        db.execute("DELETE FROM classification_rules WHERE id = ?", (rule_id,))
    bump_version()


def apply_classification_rule(
    event_type: str,
    amount: float,
    title: str,
    detail: str,
    confidence: str,
    meta: dict[str, Any],
) -> tuple[str, str, str, str, dict[str, Any]]:
    if event_type not in UNCLASSIFIED_EVENT_TYPES or not amount:
        return event_type, title, detail, confidence, meta
    direction = 1 if amount > 0 else -1
    absolute = abs(amount)
    with database_connection() as db:
        rule = db.execute(
            """
            SELECT id, category, label, min_amount, max_amount
            FROM classification_rules
            WHERE enabled = 1 AND direction = ? AND original_event_type = ?
              AND ? BETWEEN min_amount AND max_amount
            ORDER BY (max_amount - min_amount) ASC, updated_at DESC, id DESC
            LIMIT 1
            """,
            (direction, event_type, absolute),
        ).fetchone()
        if not rule:
            return event_type, title, detail, confidence, meta
        category = clean_text(rule["category"])
        if category not in REVIEW_CATEGORIES:
            return event_type, title, detail, confidence, meta
        db.execute(
            "UPDATE classification_rules SET use_count = use_count + 1, updated_at = ? WHERE id = ?",
            (unix_now(), rule["id"]),
        )
    info = REVIEW_CATEGORIES[category]
    chosen_label = clean_text(rule["label"])
    new_title = chosen_label or (info["income_title"] if amount > 0 else info["expense_title"])
    meta["classification_rule"] = {
        "id": rule["id"],
        "category": category,
        "amount_range": [round(as_float(rule["min_amount"]), 2), round(as_float(rule["max_amount"]), 2)],
    }
    meta["confidence_reason"] = "Matched a user-approved classification rule based on direction and amount range"
    evidence = meta.setdefault("evidence", [])
    evidence.append(f"Matched saved review rule #{rule['id']}")
    return category, new_title, f"{detail} · matched saved review rule" if detail else "Matched saved review rule", "inferred", meta


def diagnostics_payload() -> dict[str, Any]:
    with STATE_LOCK:
        collector = deepcopy(APP_STATE["collector"])
    with database_connection() as db:
        counts = {
            "events": db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "unclassified": db.execute("SELECT COUNT(*) FROM events WHERE event_type IN ('income','expense','money_change') AND amount IS NOT NULL").fetchone()[0],
            "sessions": db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            "balance_samples": db.execute("SELECT COUNT(*) FROM balance_samples").fetchone()[0],
            "daily_balance_summaries": db.execute("SELECT COUNT(*) FROM daily_balance_samples").fetchone()[0],
            "classification_rules": db.execute("SELECT COUNT(*) FROM classification_rules").fetchone()[0],
        }
    try:
        db_size = DB_PATH.stat().st_size
    except OSError:
        db_size = 0
    collector["database"] = {**counts, "size_bytes": db_size}
    collector["retention"] = {
        "balance_sample_days": BALANCE_SAMPLE_RETENTION_DAYS,
        "transactions": "kept indefinitely",
        "sessions": "kept indefinitely",
    }
    collector["adaptive_polling"] = ADAPTIVE_POLLING
    collector["review_categories"] = len(REVIEW_CATEGORIES)
    return collector


def get_session_summary(days: int = 30) -> dict[str, Any]:
    since = unix_now() - max(days, 1) * 86400
    now = unix_now()
    with database_connection() as db:
        rows = db.execute(
            """
            SELECT player,
                   COUNT(*) AS sessions,
                   SUM(CASE
                         WHEN left_at IS NULL THEN ? - joined_at
                         ELSE COALESCE(duration_seconds, left_at - joined_at)
                       END) AS seconds,
                   MAX(COALESCE(left_at, joined_at)) AS last_seen
            FROM sessions
            WHERE joined_at >= ?
            GROUP BY player
            ORDER BY seconds DESC
            """,
            (now, since),
        ).fetchall()
        recent = db.execute(
            """
            SELECT id, player, joined_at, left_at,
                   CASE WHEN left_at IS NULL THEN ? - joined_at
                        ELSE COALESCE(duration_seconds, left_at - joined_at)
                   END AS duration_seconds,
                   is_admin
            FROM sessions
            ORDER BY joined_at DESC LIMIT 100
            """,
            (now,),
        ).fetchall()
        daily = db.execute(
            """
            SELECT date(joined_at, 'unixepoch', 'localtime') AS day,
                   SUM(CASE WHEN left_at IS NULL THEN ? - joined_at
                            ELSE COALESCE(duration_seconds, left_at - joined_at)
                       END) AS seconds
            FROM sessions
            WHERE joined_at >= ?
            GROUP BY day ORDER BY day ASC
            """,
            (now, since),
        ).fetchall()
    return {
        "days": days,
        "players": [dict(row) for row in rows],
        "recent_sessions": [dict(row) for row in recent],
        "daily": [dict(row) for row in daily],
    }


def get_economy_history(days: int = 30) -> dict[str, Any]:
    since = unix_now() - max(days, 1) * 86400
    with database_connection() as db:
        transactions = db.execute(
            """
            SELECT id, ts, event_type, title, detail, amount, confidence, meta_json
            FROM events
            WHERE ts >= ? AND amount IS NOT NULL
            ORDER BY ts DESC, id DESC LIMIT 500
            """,
            (since,),
        ).fetchall()
        daily = db.execute(
            """
            SELECT date(ts, 'unixepoch', 'localtime') AS day,
                   SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS income,
                   SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END) AS spending,
                   SUM(amount) AS net
            FROM events
            WHERE ts >= ? AND amount IS NOT NULL AND event_type != 'ignored'
            GROUP BY day ORDER BY day ASC
            """,
            (since,),
        ).fetchall()
        categories = db.execute(
            """
            SELECT event_type,
                   COUNT(*) AS entries,
                   SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS income,
                   SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END) AS spending,
                   SUM(amount) AS net,
                   SUM(CASE WHEN confidence != 'inferred' THEN 1 ELSE 0 END) AS confirmed_entries
            FROM events
            WHERE ts >= ? AND amount IS NOT NULL AND event_type != 'ignored'
            GROUP BY event_type ORDER BY ABS(SUM(amount)) DESC
            """,
            (since,),
        ).fetchall()
        balances = db.execute(
            """
            SELECT ts, balance FROM balance_samples
            WHERE ts >= ? ORDER BY ts ASC LIMIT 2000
            """,
            (since,),
        ).fetchall()
        compressed_balances = db.execute(
            """
            SELECT CAST(strftime('%s', day || ' 12:00:00') AS INTEGER) AS ts,
                   closing_balance AS balance
            FROM daily_balance_samples
            WHERE day >= date(?, 'unixepoch', 'localtime')
            ORDER BY day ASC
            """,
            (since,),
        ).fetchall()
        activity = db.execute(
            """
            SELECT id, ts, event_type, title, detail, amount, confidence, meta_json
            FROM events
            WHERE ts >= ?
              AND event_type IN ('contract_started', 'contract_completed', 'contract_failed', 'contract_cancelled')
            ORDER BY ts DESC, id DESC LIMIT 100
            """,
            (since,),
        ).fetchall()

    def parse_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["meta"] = json.loads(item.pop("meta_json"))
            except json.JSONDecodeError:
                item["meta"] = {}
                item.pop("meta_json", None)
            parsed.append(item)
        return parsed

    parsed_transactions = parse_rows(transactions)
    parsed_activity = parse_rows(activity)
    counted_transactions = [item for item in parsed_transactions if item.get("event_type") != "ignored"]
    total_value = sum(abs(as_float(item.get("amount"))) for item in counted_transactions)
    confirmed_value = sum(
        abs(as_float(item.get("amount")))
        for item in counted_transactions
        if item.get("confidence") != "inferred"
    )
    confirmed_entries = sum(1 for item in counted_transactions if item.get("confidence") != "inferred")
    unclassified_types = {"income", "expense", "money_change"}
    unclassified_income = sum(
        as_float(item.get("amount"))
        for item in counted_transactions
        if item.get("event_type") in unclassified_types and as_float(item.get("amount")) > 0
    )
    unclassified_spending = sum(
        abs(as_float(item.get("amount")))
        for item in counted_transactions
        if item.get("event_type") in unclassified_types and as_float(item.get("amount")) < 0
    )

    contract_payments = [item for item in counted_transactions if item.get("event_type") == "contract_payment"]
    contract_values = [as_float(item.get("amount")) for item in contract_payments if as_float(item.get("amount")) > 0]
    contract_types: dict[str, dict[str, Any]] = {}
    for payment in contract_payments:
        mission_items = (payment.get("meta") or {}).get("missions") or []
        allocation = as_float(payment.get("amount")) / max(len(mission_items), 1)
        if not mission_items:
            mission_items = [{"type": "unknown", "label": "Contract"}]
        for mission in mission_items:
            key = clean_text(mission.get("type")) or "unknown"
            bucket = contract_types.setdefault(
                key,
                {"type": key, "label": mission_label(key) if key != "unknown" else "Contract", "count": 0, "income": 0.0},
            )
            bucket["count"] += 1
            bucket["income"] += allocation
    type_rows = sorted(contract_types.values(), key=lambda item: (-item["income"], item["label"].lower()))
    for item in type_rows:
        item["income"] = round(item["income"], 2)

    lifecycle_counts = {
        "accepted": sum(1 for item in parsed_activity if item.get("event_type") == "contract_started"),
        "completed": sum(1 for item in parsed_activity if item.get("event_type") == "contract_completed"),
        "failed": sum(1 for item in parsed_activity if item.get("event_type") == "contract_failed"),
        "cancelled": sum(1 for item in parsed_activity if item.get("event_type") == "contract_cancelled"),
    }

    return {
        "days": days,
        "transactions": parsed_transactions,
        "activity": parsed_activity,
        "daily": [dict(row) for row in daily],
        "categories": [dict(row) for row in categories],
        "balances": sorted([dict(row) for row in compressed_balances] + [dict(row) for row in balances], key=lambda item: item["ts"]),
        "ledger_summary": {
            "entry_count": len(counted_transactions),
            "confirmed_entries": confirmed_entries,
            "inferred_entries": len(counted_transactions) - confirmed_entries,
            "total_value": round(total_value, 2),
            "confirmed_value": round(confirmed_value, 2),
            "confirmed_entry_rate": round(confirmed_entries / len(counted_transactions) * 100, 1) if counted_transactions else 0.0,
            "confirmed_value_rate": round(confirmed_value / total_value * 100, 1) if total_value else 0.0,
            "unclassified_income": round(unclassified_income, 2),
            "unclassified_spending": round(unclassified_spending, 2),
        },
        "contract_stats": {
            "paid_count": len(contract_payments),
            "income": round(sum(contract_values), 2),
            "average_payment": round(sum(contract_values) / len(contract_values), 2) if contract_values else 0.0,
            "largest_payment": round(max(contract_values), 2) if contract_values else 0.0,
            "types": type_rows,
            **lifecycle_counts,
        },
    }


APP_STATE: dict[str, Any] = {
    "version": 0,
    "generated_at": utc_now(),
    "site_title": SITE_TITLE,
    "currency_symbol": CURRENCY_SYMBOL,
    "server": {
        "online": False,
        "name": "FS25 Server",
        "game": "Farming Simulator 25",
        "version": "—",
        "map_name": "—",
        "map_size": 0,
        "day_time_ms": 0,
        "game_time": "—",
        "capacity": 4,
        "players_used": 0,
        "players": [],
        "latency_ms": None,
        "last_success": None,
        "last_error": None,
    },
    "live": {
        "mods": [],
        "mod_count": 0,
        "vehicles": [],
        "controlled_vehicles": [],
        "fields": [],
        "owned_field_count": 0,
        "farmlands": [],
        "owned_farmland_count": 0,
        "owned_area": 0.0,
        "owned_land_value": 0.0,
    },
    "career": {
        "available": False,
        "last_success": None,
        "last_error": None,
        "savegame_name": "—",
        "map_title": "—",
        "save_date": None,
        "creation_date": None,
        "money": None,
        "play_time_seconds": 0,
        "slot_usage": 0,
        "settings": {},
        "mod_count": 0,
    },
    "fleet": {
        "available": False,
        "last_success": None,
        "last_error": None,
        "vehicles": [],
        "owned_count": 0,
        "leased_count": 0,
        "total_value": 0.0,
        "maintenance_count": 0,
        "supply_count": 0,
        "product_object_count": 0,
        "objects": [],
    },
    "missions": {
        "available": False,
        "last_success": None,
        "last_error": None,
        "missions": [],
        "active": [],
        "completed": [],
        "available_contracts": [],
        "active_count": 0,
        "completed_count": 0,
        "available_count": 0,
        "active_reward_total": 0.0,
        "available_reward_total": 0.0,
    },
    "productions": {
        "available": False,
        "last_success": None,
        "last_error": None,
        "sites": [],
        "site_count": 0,
        "active_site_count": 0,
        "direct_sell_outputs": [],
        "direct_sell_output_count": 0,
    },
    "economy": {
        "available": False,
        "last_success": None,
        "last_error": None,
        "fill_types": [],
        "great_demands": [],
        "inventory_count": 0,
        "inventory_total": 0.0,
    },
    "collector": {
        "stats_poll_seconds": STATS_POLL_SECONDS,
        "save_poll_seconds": SAVE_POLL_SECONDS,
        "map_poll_seconds": MAP_POLL_SECONDS,
        "started_at": utc_now(),
        "map_updated_at": None,
        "map_width": 0,
        "map_height": 0,
        "map_bytes": 0,
        "map_source": "Waiting for map image",
        "map_hd_requested": MAP_HD_SIZE,
        "map_quality_requested": MAP_HD_QUALITY,
        "adaptive_polling": ADAPTIVE_POLLING,
        "empty_server_save_poll_seconds": EMPTY_SERVER_SAVE_POLL_SECONDS,
        "empty_server_map_poll_seconds": EMPTY_SERVER_MAP_POLL_SECONDS,
        "current_save_poll_seconds": SAVE_POLL_SECONDS,
        "current_map_poll_seconds": MAP_POLL_SECONDS,
        "sources": {
            name: {
                "last_attempt": None, "last_success": None, "last_error": None,
                "latency_ms": None, "bytes": 0, "hash": None,
                "polls": 0, "changes": 0, "unchanged": 0, "failures": 0,
                "current_interval_seconds": None, "next_poll_at": None,
            }
            for name in ("stats", "map", "career", "vehicles", "economy", "missions", "placeables")
        },
    },
}


def bump_version() -> None:
    with STATE_VERSION_CONDITION:
        APP_STATE["version"] += 1
        APP_STATE["generated_at"] = utc_now()
        STATE_VERSION_CONDITION.notify_all()


def source_status_update(
    name: str,
    *,
    success: bool,
    started: float,
    content: bytes | None = None,
    changed: bool | None = None,
    error: str | None = None,
    interval: int | None = None,
) -> None:
    now_iso = utc_now()
    latency = round((time.monotonic() - started) * 1000, 1)
    with STATE_LOCK:
        status = APP_STATE["collector"]["sources"].setdefault(name, {})
        status["last_attempt"] = now_iso
        status["latency_ms"] = latency
        status["polls"] = as_int(status.get("polls")) + 1
        if interval is not None:
            status["current_interval_seconds"] = interval
            status["next_poll_at"] = datetime.fromtimestamp(unix_now() + interval, tz=timezone.utc).isoformat(timespec="seconds")
        if success:
            status["last_success"] = now_iso
            status["last_error"] = None
            if content is not None:
                status["bytes"] = len(content)
                status["hash"] = hashlib.sha256(content).hexdigest()[:16]
            if changed is True:
                status["changes"] = as_int(status.get("changes")) + 1
            elif changed is False:
                status["unchanged"] = as_int(status.get("unchanged")) + 1
        else:
            status["last_error"] = clean_text(error) or "Unknown source error"
            status["failures"] = as_int(status.get("failures")) + 1


def server_has_players() -> bool:
    with STATE_LOCK:
        return as_int(APP_STATE.get("server", {}).get("players_used")) > 0


def active_contracts_present() -> bool:
    with STATE_LOCK:
        return as_int(APP_STATE.get("missions", {}).get("active_count")) > 0


def current_save_poll_interval() -> int:
    if not ADAPTIVE_POLLING or server_has_players() or active_contracts_present():
        return SAVE_POLL_SECONDS
    return EMPTY_SERVER_SAVE_POLL_SECONDS


def current_map_poll_interval() -> int:
    if not ADAPTIVE_POLLING or server_has_players():
        return MAP_POLL_SECONDS
    return EMPTY_SERVER_MAP_POLL_SECONDS


def adaptive_wait(kind: str, interval: int) -> None:
    """Sleep in short chunks so a player joining can immediately restore fast polling."""
    deadline = time.monotonic() + max(interval, 1)
    while not STOP_EVENT.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        STOP_EVENT.wait(min(15, remaining))
        if STOP_EVENT.is_set():
            return
        current = current_map_poll_interval() if kind == "map" else current_save_poll_interval()
        if current < interval:
            return


def parse_stats(content: bytes, latency_ms: float) -> dict[str, Any]:
    root = parse_xml_bytes(content)
    attrs = root.attrib
    slots = root.find("Slots")
    players: list[dict[str, Any]] = []
    capacity = as_int(slots.attrib.get("capacity") if slots is not None else None, 4)
    if slots is not None:
        for player in slots.findall("Player"):
            if not as_bool(player.attrib.get("isUsed")):
                continue
            name = clean_text(player.text) or "Unknown Player"
            players.append(
                {
                    "name": name,
                    "is_admin": as_bool(player.attrib.get("isAdmin")),
                    "api_uptime_minutes": as_int(player.attrib.get("uptime")),
                }
            )

    mods = []
    mods_node = root.find("Mods")
    if mods_node is not None:
        for mod in mods_node.findall("Mod"):
            mods.append(
                {
                    "name": clean_text(mod.text) or mod.attrib.get("name", "Unknown Mod"),
                    "internal_name": mod.attrib.get("name", ""),
                    "author": mod.attrib.get("author", ""),
                    "version": mod.attrib.get("version", ""),
                    "hash": mod.attrib.get("hash", ""),
                }
            )

    farmlands = []
    farmlands_node = root.find("Farmlands")
    if farmlands_node is not None:
        for farmland in farmlands_node.findall("Farmland"):
            farmlands.append(
                {
                    "id": as_int(farmland.attrib.get("id")),
                    "name": farmland.attrib.get("name", ""),
                    "owner": as_int(farmland.attrib.get("owner")),
                    "area": as_float(farmland.attrib.get("area")),
                    "price": as_float(farmland.attrib.get("price")),
                    "x": as_float(farmland.attrib.get("x")),
                    "z": as_float(farmland.attrib.get("z")),
                }
            )

    fields = []
    fields_node = root.find("Fields")
    if fields_node is not None:
        for field in fields_node.findall("Field"):
            fields.append(
                {
                    "id": as_int(field.attrib.get("id")),
                    "x": as_float(field.attrib.get("x")),
                    "z": as_float(field.attrib.get("z")),
                    "is_owned": as_bool(field.attrib.get("isOwned")),
                }
            )

    live_vehicles = []
    vehicles_node = root.find("Vehicles")
    if vehicles_node is not None:
        for vehicle in vehicles_node.findall("Vehicle"):
            fill_types = clean_text(vehicle.attrib.get("fillTypes")).split()
            fill_levels = [as_float(value) for value in clean_text(vehicle.attrib.get("fillLevels")).split()]
            fills = []
            for index, fill_type in enumerate(fill_types):
                fills.append(
                    {
                        "fill_type": fill_type,
                        "level": fill_levels[index] if index < len(fill_levels) else 0.0,
                    }
                )
            live_vehicles.append(
                {
                    "name": vehicle.attrib.get("name", "Unknown Vehicle"),
                    "category": vehicle.attrib.get("category", ""),
                    "type": vehicle.attrib.get("type", ""),
                    "x": as_float(vehicle.attrib.get("x")),
                    "y": as_float(vehicle.attrib.get("y")),
                    "z": as_float(vehicle.attrib.get("z")),
                    "controller": vehicle.attrib.get("controller", ""),
                    "is_ai_active": as_bool(vehicle.attrib.get("isAIActive")),
                    "fills": fills,
                }
            )

    controller_lookup = {
        vehicle["controller"]: vehicle for vehicle in live_vehicles if vehicle.get("controller")
    }
    for player in players:
        controlled = controller_lookup.get(player["name"])
        player["vehicle"] = controlled["name"] if controlled else None
        player["vehicle_type"] = controlled["type"] if controlled else None
        player["x"] = controlled["x"] if controlled else None
        player["z"] = controlled["z"] if controlled else None

    owned_farmlands = [item for item in farmlands if item["owner"] > 0]
    owned_fields = [item for item in fields if item["is_owned"]]
    online = bool(attrs.get("name") or attrs.get("game") or slots is not None and slots.attrib.get("capacity"))
    day_time_ms = as_int(attrs.get("dayTime"))

    return {
        "server": {
            "online": online,
            "name": attrs.get("name", "FS25 Server"),
            "game": attrs.get("game", "Farming Simulator 25"),
            "version": attrs.get("version", "—"),
            "map_name": attrs.get("mapName", "—"),
            "map_size": as_int(attrs.get("mapSize")),
            "day_time_ms": day_time_ms,
            "game_time": format_game_time(day_time_ms),
            "capacity": capacity,
            "players_used": len(players),
            "players": players,
            "latency_ms": round(latency_ms, 1),
            "last_success": utc_now(),
            "last_error": None,
        },
        "live": {
            "mods": mods,
            "mod_count": len(mods),
            "vehicles": live_vehicles,
            "controlled_vehicles": [item for item in live_vehicles if item.get("controller")],
            "fields": fields,
            "owned_field_count": len(owned_fields),
            "farmlands": farmlands,
            "owned_farmland_count": len(owned_farmlands),
            "owned_area": round(sum(item["area"] for item in owned_farmlands), 3),
            "owned_land_value": round(sum(item["price"] for item in owned_farmlands), 2),
        },
    }


def parse_career(content: bytes) -> dict[str, Any]:
    root = parse_xml_bytes(content)
    settings_node = root.find("settings")
    statistics_node = root.find("statistics")
    settings: dict[str, Any] = {}
    if settings_node is not None:
        for child in settings_node:
            tag = safe_tag_name(child.tag)
            value = clean_text(child.text)
            if value.lower() in {"true", "false"}:
                settings[tag] = as_bool(value)
            elif re.fullmatch(r"-?\d+(?:\.\d+)?", value or ""):
                number = as_float(value)
                settings[tag] = int(number) if number.is_integer() else number
            else:
                settings[tag] = value

    money = as_float(text_of(statistics_node, "money"), 0.0) if statistics_node is not None else None
    play_time = as_float(text_of(statistics_node, "playTime"), 0.0) if statistics_node is not None else 0.0
    slot_system = root.find("slotSystem")
    mods = root.findall("mod")
    return {
        "available": True,
        "last_success": utc_now(),
        "last_error": None,
        "savegame_name": settings.get("savegameName", "—"),
        "map_title": settings.get("mapTitle", "—"),
        "save_date": settings.get("saveDate"),
        "creation_date": settings.get("creationDate"),
        "money": money,
        "play_time_seconds": round(play_time, 2),
        "slot_usage": as_int(slot_system.attrib.get("slotUsage") if slot_system is not None else None),
        "settings": settings,
        "mod_count": len(mods),
    }


def parse_vehicle_fills(vehicle: ET.Element) -> list[dict[str, Any]]:
    fills = []
    fill_unit = vehicle.find("fillUnit")
    if fill_unit is not None:
        for unit in fill_unit.findall("unit"):
            fills.append(
                {
                    "index": as_int(unit.attrib.get("index")),
                    "fill_type": unit.attrib.get("fillType", "UNKNOWN"),
                    "level": round(as_float(unit.attrib.get("fillLevel")), 3),
                }
            )
    return fills


def parse_vehicle_damage(vehicle: ET.Element) -> tuple[float, float, float | None, float | None]:
    wearable = vehicle.find("wearable")
    damage = as_float(wearable.attrib.get("damage") if wearable is not None else None)
    dirt_values = [as_float(node.attrib.get("amount")) for node in vehicle.findall("./washable/dirtNode")]
    dirt = sum(dirt_values) / len(dirt_values) if dirt_values else 0.0
    ads = vehicle.find("AdvancedDamageSystem")
    condition = as_float(ads.attrib.get("condition")) if ads is not None and "condition" in ads.attrib else None
    service = as_float(ads.attrib.get("service")) if ads is not None and "service" in ads.attrib else None
    return damage, dirt, condition, service


def parse_vehicles(content: bytes) -> dict[str, Any]:
    root = parse_xml_bytes(content)
    saved_objects = []
    for vehicle in root.findall("vehicle"):
        filename = vehicle.attrib.get("filename", "")
        component = vehicle.find("component")
        position = clean_text(component.attrib.get("position") if component is not None else "").split()
        x = as_float(position[0]) if len(position) >= 1 else None
        y = as_float(position[1]) if len(position) >= 2 else None
        z = as_float(position[2]) if len(position) >= 3 else None
        damage, dirt, condition, service = parse_vehicle_damage(vehicle)
        drivable = vehicle.find("drivable")
        odometer = as_float(drivable.attrib.get("odometerMilage")) if drivable is not None and "odometerMilage" in drivable.attrib else None
        fills = parse_vehicle_fills(vehicle)
        object_kind = classify_saved_object(filename, vehicle, fills)
        item = {
            "id": vehicle.attrib.get("uniqueId", "") or vehicle.attrib.get("id", ""),
            "name": saved_object_name(filename, object_kind, fills),
            "filename": filename,
            "mod_name": vehicle.attrib.get("modName", ""),
            "farm_id": as_int(vehicle.attrib.get("farmId")),
            "property_state": vehicle.attrib.get("propertyState", "NONE"),
            "age_months": round(as_float(vehicle.attrib.get("age")), 1),
            "price": round(as_float(vehicle.attrib.get("price")), 2),
            "operating_time_seconds": round(as_float(vehicle.attrib.get("operatingTime")), 1),
            "damage": round(damage, 4),
            "dirt": round(dirt, 4),
            "condition": round(condition, 4) if condition is not None else None,
            "service": round(service, 4) if service is not None else None,
            "odometer_km": round(odometer, 2) if odometer is not None else None,
            "fills": fills,
            "position": {"x": x, "y": y, "z": z},
            "is_ai_active": as_bool(vehicle.find("aiFieldWorker").attrib.get("isActive")) if vehicle.find("aiFieldWorker") is not None else False,
            "object_kind": object_kind,
            "supply_group": supply_group_for_object(filename, fills) if object_kind == "supply" else None,
            "is_fleet_asset": object_kind == "fleet",
        }
        saved_objects.append(item)

    vehicles = [item for item in saved_objects if item["is_fleet_asset"]]
    owned = [item for item in vehicles if item["property_state"] == "OWNED" and item["farm_id"] > 0]
    leased = [item for item in vehicles if item["property_state"] == "LEASED" and item["farm_id"] > 0]
    supplies = [item for item in saved_objects if item["object_kind"] == "supply" and item["farm_id"] > 0]
    products = [item for item in saved_objects if item["object_kind"] in {"product", "pallet", "bale"} and item["farm_id"] > 0]
    fleet_assets = owned + leased
    maintenance = [
        item
        for item in fleet_assets
        if item["damage"] >= 0.3
        or (item["condition"] is not None and item["condition"] < 0.55)
        or (item["service"] is not None and item["service"] < 0.55)
    ]
    return {
        "available": True,
        "last_success": utc_now(),
        "last_error": None,
        "vehicles": vehicles,
        "objects": saved_objects,
        "owned_count": len(owned),
        "leased_count": len(leased),
        "supply_count": len(supplies),
        "product_object_count": len(products),
        "total_value": round(sum(item["price"] for item in owned), 2),
        "maintenance_count": len(maintenance),
    }


def parse_economy(content: bytes) -> dict[str, Any]:
    root = parse_xml_bytes(content)
    fill_types = []
    fill_types_node = root.find("fillTypes")
    if fill_types_node is not None:
        for fill_type in fill_types_node.findall("fillType"):
            name = fill_type.attrib.get("fillType", "UNKNOWN")
            total_amount = as_float(fill_type.attrib.get("totalAmount"))
            history = []
            history_node = fill_type.find("history")
            if history_node is not None:
                for period in history_node.findall("period"):
                    history.append(
                        {
                            "period": period.attrib.get("period", ""),
                            "value": as_float(period.text),
                        }
                    )
            fill_types.append(
                {
                    "name": name,
                    "label": humanise_identifier(name),
                    "total_amount": round(total_amount, 3),
                    "history": history,
                    "peak_period": max(history, key=lambda item: item["value"])["period"] if history else None,
                    "peak_value": max((item["value"] for item in history), default=0.0),
                }
            )

    demands = []
    demands_node = root.find("greatDemands")
    if demands_node is not None:
        for demand in demands_node.findall("greatDemand"):
            demands.append(
                {
                    "fill_type": demand.attrib.get("fillTypeName", "UNKNOWN"),
                    "label": humanise_identifier(demand.attrib.get("fillTypeName", "UNKNOWN")),
                    "multiplier": as_float(demand.attrib.get("demandMultiplier"), 1.0),
                    "start_day": as_int(demand.attrib.get("demandStartDay")),
                    "start_hour": as_int(demand.attrib.get("demandStartHour")),
                    "duration_hours": as_int(demand.attrib.get("demandDuration")),
                    "is_running": as_bool(demand.attrib.get("isRunning")),
                    "is_valid": as_bool(demand.attrib.get("isValid")),
                }
            )

    inventories = [item for item in fill_types if item["total_amount"] > 0]
    return {
        "available": True,
        "last_success": utc_now(),
        "last_error": None,
        "fill_types": fill_types,
        "great_demands": demands,
        "inventory_count": len(inventories),
        "inventory_total": round(sum(item["total_amount"] for item in inventories), 3),
    }


def production_site_name(filename: str) -> str:
    basename = Path(filename.replace("\\", "/")).stem
    label = humanise_identifier(basename)
    replacements = {
        "Greenhouse Large": "Large Greenhouse",
        "Greenhouse Medium": "Medium Greenhouse",
        "Greenhouse Small": "Small Greenhouse",
    }
    return replacements.get(label, label or "Production Building")


def parse_placeables(content: bytes) -> dict[str, Any]:
    """Parse owned production points and outputs configured for direct selling."""
    root = parse_xml_bytes(content)
    sites: list[dict[str, Any]] = []
    output_index: dict[str, dict[str, Any]] = {}

    for placeable in root.findall(".//placeable"):
        farm_id = as_int(placeable.attrib.get("farmId"))
        if farm_id <= 0:
            continue
        filename = placeable.attrib.get("filename", "")
        unique_id = placeable.attrib.get("uniqueId", "") or placeable.attrib.get("id", "")
        for point_index, point in enumerate(placeable.findall(".//productionPoint")):
            direct_sell = []
            for node in point.findall("directSellFillType"):
                fill_type = clean_text(node.text).upper()
                if fill_type and fill_type not in direct_sell:
                    direct_sell.append(fill_type)
            auto_deliver = []
            for node in point.findall("autoDeliverFillType"):
                fill_type = clean_text(node.text).upper()
                if fill_type and fill_type not in auto_deliver:
                    auto_deliver.append(fill_type)
            productions = []
            for node in point.findall("production"):
                productions.append(
                    {
                        "id": clean_text(node.attrib.get("id")),
                        "is_enabled": as_bool(node.attrib.get("isEnabled")),
                    }
                )
            active_count = sum(1 for item in productions if item["is_enabled"])
            storage = []
            storage_node = point.find("storage")
            if storage_node is not None:
                for node in storage_node.findall("node"):
                    storage.append(
                        {
                            "fill_type": clean_text(node.attrib.get("fillType")).upper(),
                            "label": fill_type_label(node.attrib.get("fillType", "UNKNOWN")),
                            "fill_level": round(as_float(node.attrib.get("fillLevel")), 3),
                        }
                    )

            site_name = production_site_name(filename)
            if point_index > 0:
                site_name = f"{site_name} {point_index + 1}"
            site = {
                "id": f"{unique_id}:{point_index}",
                "unique_id": unique_id,
                "name": site_name,
                "filename": filename,
                "farm_id": farm_id,
                "active_production_count": active_count,
                "production_count": len(productions),
                "is_active": active_count > 0,
                "direct_sell_fill_types": direct_sell,
                "auto_deliver_fill_types": auto_deliver,
                "production_costs_to_claim": round(as_float(point.attrib.get("productionCostsToClaim")), 2),
                "storage": storage,
            }
            sites.append(site)

            # Direct-sell settings are still useful when a mod omits production state
            # nodes, but disabled standard productions are excluded.
            eligible = active_count > 0 or not productions
            if eligible:
                for fill_type in direct_sell:
                    bucket = output_index.setdefault(
                        fill_type,
                        {
                            "fill_type": fill_type,
                            "label": fill_type_label(fill_type),
                            "sites": [],
                        },
                    )
                    if site_name not in bucket["sites"]:
                        bucket["sites"].append(site_name)

    outputs = sorted(output_index.values(), key=lambda item: item["label"].lower())
    return {
        "available": True,
        "last_success": utc_now(),
        "last_error": None,
        "sites": sites,
        "site_count": len(sites),
        "active_site_count": sum(1 for item in sites if item["is_active"]),
        "direct_sell_outputs": outputs,
        "direct_sell_output_count": len(outputs),
    }


def production_autosale_candidates(
    productions: dict[str, Any] | None,
    inventory_decreases: list[tuple[str, float]] | None = None,
) -> list[dict[str, Any]]:
    if not productions or not productions.get("available"):
        return []
    outputs = [dict(item) for item in productions.get("direct_sell_outputs", [])]
    if not outputs:
        return []
    decreased = {clean_text(item[0]).upper() for item in (inventory_decreases or [])}
    matched = [item for item in outputs if clean_text(item.get("fill_type")).upper() in decreased]
    return matched or outputs


def production_autosale_summary(outputs: list[dict[str, Any]]) -> tuple[str, str]:
    labels = [clean_text(item.get("label")) or fill_type_label(item.get("fill_type", "UNKNOWN")) for item in outputs]
    sites = []
    for item in outputs:
        for site in item.get("sites", []):
            if site not in sites:
                sites.append(site)
    if len(labels) == 1:
        title = f"Production autosale: {labels[0]}"
    else:
        title = "Production autosale income"
    product_text = ", ".join(labels[:4]) + (f" +{len(labels) - 4} more" if len(labels) > 4 else "")
    site_text = ", ".join(sites[:3]) + (f" +{len(sites) - 3} more" if len(sites) > 3 else "")
    detail = f"Direct selling is enabled for {product_text}"
    if site_text:
        detail += f" at {site_text}"
    return title, detail


def mission_progress_detail(node: ET.Element, mission_type: str, completion: float) -> tuple[str, dict[str, Any]]:
    metrics: dict[str, Any] = {}
    for key, value in node.attrib.items():
        if key.startswith("num") or key in {"spotIndex"}:
            numeric = as_int(value, -1)
            if numeric >= 0:
                metrics[key] = numeric

    if mission_type == "treeTransport":
        delivered = as_int(node.attrib.get("numDeliveredTrees"))
        total = max(as_int(node.attrib.get("numTrees")), delivered)
        deleted = as_int(node.attrib.get("numDeletedTrees"))
        metrics.update({"delivered": delivered, "target": total, "deleted": deleted})
        if total > 0:
            return f"{delivered} of {total} trees delivered", metrics
    if mission_type == "destructibleRock":
        destroyed = as_int(node.attrib.get("numRocksDestroyed"))
        targets = [item for item in node.findall("destructible") if clean_text(item.attrib.get("isPartOfMission", "true")).lower() != "false"]
        total = max(len(targets), destroyed)
        metrics.update({"destroyed": destroyed, "target": total})
        if total > 0:
            return f"{destroyed} of {total} rocks destroyed", metrics
    if mission_type == "deadwood":
        total = len(node.findall("originalTree"))
        estimated = min(total, round(total * completion)) if total else 0
        metrics.update({"target": total, "estimated_completed": estimated})
        if total > 0:
            return f"About {estimated} of {total} trees cleared", metrics
    return f"{round(completion * 100)}% complete", metrics


def parse_missions(content: bytes) -> dict[str, Any]:
    root = parse_xml_bytes(content)
    missions: list[dict[str, Any]] = []
    for node in root:
        if safe_tag_name(node.tag) == "meta":
            continue
        mission_type = mission_type_from_tag(node.tag)
        info = node.find("info")
        vehicles = node.find("vehicles")
        field = node.find("field")
        end_date = node.find("endDate")
        selling_station = node.find("sellingStation")
        status = clean_text(node.attrib.get("status", "CREATED")).upper()
        finish_state = clean_text(node.attrib.get("finishState", "NONE")).upper()
        farm_id = as_int(node.attrib.get("farmId"), 0)
        raw_completion = as_float(info.attrib.get("completion") if info is not None else 0.0)
        completion = raw_completion / 100 if raw_completion > 1 else raw_completion
        completion = max(0.0, min(1.0, completion))
        reward = round(as_float(info.attrib.get("reward") if info is not None else 0.0), 2)
        reimbursement = round(as_float(info.attrib.get("reimbursement") if info is not None else 0.0), 2)
        is_finished = status == "FINISHED" or finish_state not in {"", "NONE"}
        is_active = not is_finished and (farm_id > 0 or status in {"PREPARING", "RUNNING"})
        state = "completed" if is_finished else "active" if is_active else "available"
        progress_detail, metrics = mission_progress_detail(node, mission_type, completion)
        item = {
            "id": node.attrib.get("uniqueId", ""),
            "type": mission_type,
            "label": mission_label(mission_type),
            "status": status,
            "finish_state": finish_state or "NONE",
            "state": state,
            "farm_id": farm_id,
            "reward": reward,
            "reimbursement": reimbursement,
            "expected_payout": round(reward + reimbursement, 2),
            "completion": round(completion, 4),
            "progress_detail": progress_detail,
            "metrics": metrics,
            "field_id": as_int(field.attrib.get("id")) if field is not None and field.attrib.get("id") is not None else None,
            "borrowed_vehicles": as_bool(vehicles.attrib.get("spawned")) if vehicles is not None else False,
            "vehicle_group": as_int(vehicles.attrib.get("group")) if vehicles is not None else None,
            "end_day": as_int(end_date.attrib.get("endDay")) if end_date is not None else None,
            "end_time": format_game_time(as_int(end_date.attrib.get("endDayTime"), -1)) if end_date is not None else None,
            "selling_station_id": selling_station.attrib.get("uniqueId") if selling_station is not None else None,
            "unloading_station_index": as_int(selling_station.attrib.get("unloadingStationIndex")) if selling_station is not None else None,
            "source_tag": safe_tag_name(node.tag),
        }
        item["title"] = mission_title(item)
        missions.append(item)

    state_order = {"active": 0, "completed": 1, "available": 2}
    missions.sort(key=lambda item: (state_order.get(item["state"], 9), item["title"].lower()))
    active = [item for item in missions if item["state"] == "active"]
    completed = [item for item in missions if item["state"] == "completed"]
    available = [item for item in missions if item["state"] == "available"]
    return {
        "available": True,
        "last_success": utc_now(),
        "last_error": None,
        "missions": missions,
        "active": active,
        "completed": completed,
        "available_contracts": available,
        "active_count": len(active),
        "completed_count": len(completed),
        "available_count": len(available),
        "active_reward_total": round(sum(item["expected_payout"] for item in active), 2),
        "available_reward_total": round(sum(item["expected_payout"] for item in available), 2),
    }


def current_session_started(player: str) -> int | None:
    with database_connection() as db:
        row = db.execute(
            "SELECT joined_at FROM sessions WHERE player = ? AND left_at IS NULL ORDER BY id DESC LIMIT 1",
            (player,),
        ).fetchone()
    return as_int(row["joined_at"]) if row else None


def reconcile_players(previous_players: list[dict[str, Any]], current_players: list[dict[str, Any]], initial: bool) -> None:
    previous = {item["name"]: item for item in previous_players}
    current = {item["name"]: item for item in current_players}
    now = unix_now()

    for name, player in current.items():
        if name not in previous:
            api_minutes = max(0, as_int(player.get("api_uptime_minutes")))
            inferred_join = now - api_minutes * 60 if initial and api_minutes else now
            start_session(name, bool(player.get("is_admin")), inferred_join)
            if not initial:
                add_event(
                    "player_join",
                    f"{name} joined the server",
                    "Administrator" if player.get("is_admin") else "Player connected",
                    meta={"player": name},
                )
        elif current_session_started(name) is None:
            start_session(name, bool(player.get("is_admin")), now)

    for name in previous:
        if name not in current:
            duration = close_session(name, now)
            if not initial:
                add_event(
                    "player_leave",
                    f"{name} left the server",
                    f"Session lasted {duration // 60} minutes",
                    meta={"player": name, "duration_seconds": duration},
                )

    for player in current_players:
        joined_at = current_session_started(player["name"])
        player["session_started"] = joined_at
        player["session_seconds"] = max(0, now - joined_at) if joined_at else 0


def inventory_map(economy: dict[str, Any] | None) -> dict[str, float]:
    if not economy:
        return {}
    return {item["name"]: as_float(item["total_amount"]) for item in economy.get("fill_types", [])}


def fleet_map(fleet: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not fleet:
        return {}

    def is_fleet_asset(item: dict[str, Any]) -> bool:
        # New snapshots carry the explicit classifier. Older v0.2 snapshots did
        # not, so infer obvious pallet/big-bag/object records from the path to
        # avoid a one-off false vehicle sale after upgrading.
        if item.get("is_fleet_asset") is not None:
            return bool(item.get("is_fleet_asset"))
        if item.get("object_kind"):
            return item.get("object_kind") == "fleet"
        path = clean_text(item.get("filename")).replace("\\", "/").lower()
        return not any(marker in path for marker in ("/objects/", "/bigbag", "/pallet", "/bale"))

    return {
        item["id"]: item
        for item in fleet.get("vehicles", [])
        if item.get("id")
        and item.get("farm_id", 0) > 0
        and item.get("property_state") in {"OWNED", "LEASED"}
        and is_fleet_asset(item)
    }


def saved_object_map(fleet: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not fleet:
        return {}
    objects = fleet.get("objects") or fleet.get("vehicles", [])
    return {
        item["id"]: item
        for item in objects
        if item.get("id") and item.get("farm_id", 0) > 0 and item.get("property_state") in {"OWNED", "LEASED"}
    }


def missions_map(missions: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not missions:
        return {}
    return {item["id"]: item for item in missions.get("missions", []) if item.get("id")}


def summarise_objects(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (clean_text(item.get("name")) or "Unknown item", clean_text(item.get("object_kind")) or "object")
        summary = grouped.setdefault(
            key,
            {
                "name": key[0],
                "kind": key[1],
                "group": item.get("supply_group"),
                "count": 0,
                "fill_type": None,
                "fill_amount": 0.0,
                "price_total": 0.0,
                "unit_price": 0.0,
            },
        )
        summary["count"] += 1
        summary["price_total"] += as_float(item.get("price"))
        fill = primary_fill(item.get("fills", []))
        if fill:
            summary["fill_type"] = fill_type_label(clean_text(fill.get("fill_type")))
            summary["fill_amount"] += as_float(fill.get("level"))
    for summary in grouped.values():
        summary["price_total"] = round(summary["price_total"], 2)
        summary["fill_amount"] = round(summary["fill_amount"], 3)
        summary["unit_price"] = round(summary["price_total"] / summary["count"], 2) if summary["count"] else 0.0
    return sorted(grouped.values(), key=lambda item: (-item["count"], item["name"].lower()))


def summary_names(items: list[dict[str, Any]], limit: int = 3) -> str:
    summaries = summarise_objects(items)
    labels = [f"{item['name']} ×{item['count']}" if item["count"] > 1 else item["name"] for item in summaries[:limit]]
    if len(summaries) > limit:
        labels.append(f"+{len(summaries) - limit} more")
    return ", ".join(labels)


def mission_is_started(item: dict[str, Any]) -> bool:
    return as_int(item.get("farm_id")) > 0 or clean_text(item.get("status")).upper() in {"PREPARING", "RUNNING", "FINISHED"}


def mission_is_success(item: dict[str, Any]) -> bool:
    return clean_text(item.get("finish_state")).upper() == "SUCCESS"


def mission_completion_ratio(item: dict[str, Any]) -> float:
    """Return the strongest completion signal saved for a mission type.

    Some non-field contracts, especially destructible-rock jobs, do not keep
    `info.completion` in step with their dedicated counters.  Using the
    mission-specific metrics prevents a fully cleared rock job being mistaken
    for a cancellation when it disappears on collection.
    """
    ratios = [max(0.0, min(1.0, as_float(item.get("completion"))))]
    metrics = item.get("metrics") or {}
    target = as_float(metrics.get("target"))
    if target > 0:
        for key in ("destroyed", "delivered", "estimated_completed"):
            if key in metrics:
                ratios.append(max(0.0, min(1.0, as_float(metrics.get(key)) / target)))
    return max(ratios)


def mission_is_probably_complete(item: dict[str, Any]) -> bool:
    return mission_is_success(item) or mission_completion_ratio(item) >= 0.98


def match_removed_missions_by_amount(candidates: list[dict[str, Any]], balance_delta: float) -> list[dict[str, Any]]:
    """Find removed accepted mission(s) whose saved payout matches new income.

    This covers save intervals where the server writes the mission while it is
    active, then the next available snapshot is after collection.  In that
    case a SUCCESS frame can be missed entirely even though the listed reward
    and the balance increase still provide strong evidence.
    """
    if balance_delta <= 0:
        return []
    eligible = [item for item in candidates if as_float(item.get("expected_payout")) > 0][:10]
    if not eligible:
        return []

    best: tuple[tuple[float, float, int], list[dict[str, Any]]] | None = None
    max_group = min(5, len(eligible))
    for size in range(1, max_group + 1):
        for group in combinations(eligible, size):
            expected = sum(as_float(item.get("expected_payout")) for item in group)
            tolerance = max(1.0, expected * 0.02)
            difference = abs(balance_delta - expected)
            if difference > tolerance:
                continue
            completion = sum(mission_completion_ratio(item) for item in group) / len(group)
            score = (difference, -completion, size)
            if best is None or score < best[0]:
                best = (score, list(group))
    return best[1] if best else []


def detect_mission_lifecycle(
    previous_missions: dict[str, Any] | None,
    missions: dict[str, Any] | None,
    balance_delta: float = 0.0,
) -> dict[str, list[dict[str, Any]]]:
    changes = {
        "accepted": [],
        "completed": [],
        "failed": [],
        "collected": [],
        "probable_collected": [],
        "amount_matched_removed": [],
        "cancelled": [],
    }
    if not previous_missions or not missions:
        return changes
    old_map = missions_map(previous_missions)
    new_map = missions_map(missions)

    for mission_id, current in new_map.items():
        previous = old_map.get(mission_id)
        if previous is None:
            continue
        if mission_is_started(current) and not mission_is_started(previous):
            changes["accepted"].append(current)
            expected = as_float(current.get("expected_payout"))
            detail = f"Farm {current.get('farm_id')} accepted the contract"
            if expected > 0:
                detail += f" · listed payout {CURRENCY_SYMBOL}{expected:,.0f}"
            add_event(
                "contract_started",
                f"Contract accepted: {mission_title(current)}",
                detail,
                None,
                "confirmed",
                {"mission": current, "sources": ["missions.xml"]},
            )

        current_finish = clean_text(current.get("finish_state")).upper()
        previous_finish = clean_text(previous.get("finish_state")).upper()
        if current_finish == "SUCCESS" and previous_finish != "SUCCESS":
            changes["completed"].append(current)
            add_event(
                "contract_completed",
                f"Contract completed: {mission_title(current)}",
                "Successful mission state recorded; waiting for the reward to be collected",
                None,
                "confirmed",
                {"mission": current, "sources": ["missions.xml"]},
            )
        elif current_finish in {"FAILED", "TIMED_OUT"} and current_finish != previous_finish:
            changes["failed"].append(current)
            add_event(
                "contract_failed",
                f"Contract {current_finish.replace('_', ' ').lower()}: {mission_title(current)}",
                "The mission finished without a payment",
                None,
                "confirmed",
                {"mission": current, "sources": ["missions.xml"]},
            )

    removed_started = [
        previous
        for mission_id, previous in old_map.items()
        if mission_id not in new_map and mission_is_started(previous)
    ]
    amount_matched = match_removed_missions_by_amount(removed_started, balance_delta)
    amount_matched_ids = {item.get("id") for item in amount_matched}

    for previous in removed_started:
        if mission_is_success(previous):
            changes["collected"].append(previous)
        elif mission_is_probably_complete(previous):
            changes["probable_collected"].append(previous)
        elif previous.get("id") in amount_matched_ids:
            changes["amount_matched_removed"].append(previous)
        else:
            changes["cancelled"].append(previous)
            add_event(
                "contract_cancelled",
                f"Contract removed: {mission_title(previous)}",
                "The accepted mission disappeared before a successful finish state or matching payout was captured",
                None,
                "inferred",
                {"mission": previous, "sources": ["missions.xml"]},
            )
    return changes


def object_summary_groups(items: list[dict[str, Any]]) -> str:
    groups = {clean_text(item.get("supply_group")) for item in items if clean_text(item.get("supply_group"))}
    labels = {
        "animal_feed": "animal feed",
        "crop_input": "crop inputs",
        "fuel_utility": "fuel or utilities",
        "other_supply": "supplies",
    }
    return ", ".join(sorted(labels.get(group, group.replace("_", " ")) for group in groups)) or "supplies"


def detect_savegame_changes(
    previous_career: dict[str, Any] | None,
    previous_fleet: dict[str, Any] | None,
    previous_economy: dict[str, Any] | None,
    previous_missions: dict[str, Any] | None,
    previous_productions: dict[str, Any] | None,
    career: dict[str, Any],
    fleet: dict[str, Any],
    economy: dict[str, Any],
    missions: dict[str, Any] | None,
    productions: dict[str, Any] | None,
) -> None:
    if not previous_career:
        balance = career.get("money")
        if balance is not None:
            record_balance(as_float(balance))
        detect_mission_lifecycle(previous_missions, missions, 0.0)
        return

    old_balance = previous_career.get("money")
    new_balance = career.get("money")
    delta = (
        round(as_float(new_balance) - as_float(old_balance), 2)
        if old_balance is not None and new_balance is not None
        else 0.0
    )
    mission_changes = detect_mission_lifecycle(previous_missions, missions, delta)
    if old_balance is None or new_balance is None or delta == 0:
        return

    old_fleet = fleet_map(previous_fleet)
    new_fleet = fleet_map(fleet)
    added_asset_ids = [object_id for object_id in new_fleet if object_id not in old_fleet]
    removed_asset_ids = [object_id for object_id in old_fleet if object_id not in new_fleet]
    added_assets = [new_fleet[item] for item in added_asset_ids]
    removed_assets = [old_fleet[item] for item in removed_asset_ids]

    old_objects = saved_object_map(previous_fleet)
    new_objects = saved_object_map(fleet)
    added_objects = [new_objects[item] for item in new_objects if item not in old_objects]
    removed_objects = [old_objects[item] for item in old_objects if item not in new_objects]
    added_supplies = [item for item in added_objects if item.get("object_kind") == "supply"]
    removed_products = [item for item in removed_objects if item.get("object_kind") in {"product", "pallet", "bale"}]

    old_inventory = inventory_map(previous_economy)
    new_inventory = inventory_map(economy)
    decreases: list[tuple[str, float]] = []
    increases: list[tuple[str, float]] = []
    for fill_type in set(old_inventory) | set(new_inventory):
        change = round(new_inventory.get(fill_type, 0.0) - old_inventory.get(fill_type, 0.0), 3)
        if change < 0:
            decreases.append((fill_type, abs(change)))
        elif change > 0:
            increases.append((fill_type, change))
    decreases.sort(key=lambda item: item[1], reverse=True)
    increases.sort(key=lambda item: item[1], reverse=True)
    sellable_decreases = [item for item in decreases if clean_text(item[0]).upper() not in SUPPLY_FILL_TYPES]
    supply_increases = [item for item in increases if clean_text(item[0]).upper() in SUPPLY_FILL_TYPES]
    autosale_outputs = production_autosale_candidates(productions or previous_productions, sellable_decreases)
    autosale_fill_types = {clean_text(item.get("fill_type")).upper() for item in autosale_outputs}
    autosale_inventory_match = any(clean_text(item[0]).upper() in autosale_fill_types for item in sellable_decreases)

    collected_missions = mission_changes["collected"]
    probable_missions = mission_changes["probable_collected"]
    amount_matched_missions = mission_changes["amount_matched_removed"]
    completed_missions = mission_changes["completed"]
    completed_expected = round(sum(as_float(item.get("expected_payout")) for item in completed_missions), 2)
    completed_tolerance = max(1.0, completed_expected * 0.02)
    completed_amount_match = bool(
        delta > 0
        and completed_missions
        and completed_expected > 0
        and abs(delta - completed_expected) <= completed_tolerance
    )
    payment_missions = (
        collected_missions
        or probable_missions
        or amount_matched_missions
        or (completed_missions if completed_amount_match else [])
    )
    match_quality = (
        "exact_success"
        if collected_missions
        else "near_complete_disappearance"
        if probable_missions
        else "removed_mission_amount_match"
        if amount_matched_missions
        else "success_transition_amount_match"
        if completed_amount_match
        else None
    )

    confidence = "confirmed"
    confidence_reason = "The balance change is supported by a matching savegame record"
    title = "Farm balance changed"
    detail = "Savegame balance update detected"
    event_type = "money_change"
    evidence: list[str] = [f"Balance changed from {CURRENCY_SYMBOL}{as_float(old_balance):,.2f} to {CURRENCY_SYMBOL}{as_float(new_balance):,.2f}"]
    sources = ["careerSavegame.xml"]
    meta: dict[str, Any] = {
        "old_balance": old_balance,
        "new_balance": new_balance,
        "inventory_decreases": [{"fill_type": item[0], "label": fill_type_label(item[0]), "amount": item[1]} for item in decreases[:8]],
        "inventory_increases": [{"fill_type": item[0], "label": fill_type_label(item[0]), "amount": item[1]} for item in increases[:8]],
        "added_assets": summarise_objects(added_assets),
        "removed_assets": summarise_objects(removed_assets),
        "added_supplies": summarise_objects(added_supplies),
        "removed_products": summarise_objects(removed_products),
        "missions": payment_missions,
        "sources": sources,
        "evidence": evidence,
    }

    if delta > 0 and payment_missions:
        sources.append("missions.xml")
        listed_reward = round(sum(as_float(item.get("reward")) for item in payment_missions), 2)
        reimbursements = round(sum(as_float(item.get("reimbursement")) for item in payment_missions), 2)
        expected = round(listed_reward + reimbursements, 2)
        variance = round(delta - expected, 2) if expected > 0 else None
        meta["contract_match"] = {
            "quality": match_quality,
            "mission_count": len(payment_missions),
            "listed_reward": listed_reward,
            "reimbursement": reimbursements,
            "expected_payout": expected,
            "captured_balance_change": delta,
            "variance": variance,
        }
        if len(payment_missions) == 1:
            mission = payment_missions[0]
            title = f"Contract payment: {mission_title(mission)}"
            details = ["Matched to the contract lifecycle in missions.xml"]
            if as_float(mission.get("reward")) > 0:
                details.append(f"reward {CURRENCY_SYMBOL}{as_float(mission.get('reward')):,.0f}")
            if as_float(mission.get("reimbursement")) > 0:
                details.append(f"reimbursement {CURRENCY_SYMBOL}{as_float(mission.get('reimbursement')):,.0f}")
            detail = " · ".join(details)
        else:
            title = f"Contract payments collected: {len(payment_missions)} contracts"
            detail = ", ".join(mission_title(item) for item in payment_missions[:4])
        event_type = "contract_payment"
        evidence.append(f"Matched {len(payment_missions)} completed or collected contract record(s)")
        if expected > 0:
            evidence.append(f"Listed payout total {CURRENCY_SYMBOL}{expected:,.2f}")
        if match_quality == "near_complete_disappearance":
            confidence = "inferred"
            confidence_reason = "The mission disappeared at 98%+ completion, but a SUCCESS state was missed between saves"
        elif match_quality == "removed_mission_amount_match":
            confidence = "inferred"
            confidence_reason = "The accepted contract disappeared and its listed payout matched the balance increase, although the final SUCCESS save was missed"
        elif match_quality == "success_transition_amount_match":
            confidence = "inferred"
            confidence_reason = "The contract reached SUCCESS and its listed payout matched the balance increase, but collection was not separately observed"
        else:
            confidence_reason = "A successfully finished contract disappeared when its payment entered the farm balance"
    elif delta < 0 and added_assets and added_supplies:
        sources.append("vehicles.xml")
        title = "Farm purchases detected"
        detail = f"Fleet: {summary_names(added_assets)} · Supplies: {summary_names(added_supplies)}"
        event_type = "farm_purchase"
        evidence.extend([f"{len(added_assets)} new fleet asset(s)", f"{len(added_supplies)} new supply object(s)"])
    elif delta < 0 and added_assets:
        sources.append("vehicles.xml")
        title = f"Vehicle purchase: {summary_names(added_assets)}"
        detail = "New fleet asset recorded in vehicles.xml"
        event_type = "vehicle_purchase"
        evidence.append(f"{len(added_assets)} new fleet asset(s)")
    elif delta < 0 and added_supplies:
        sources.append("vehicles.xml")
        title = f"Supplies purchased: {summary_names(added_supplies)}"
        detail = f"New {object_summary_groups(added_supplies)} recorded in vehicles.xml"
        event_type = "supply_purchase"
        evidence.append(f"{len(added_supplies)} new supply object(s)")
    elif delta < 0 and supply_increases:
        sources.append("economy.xml")
        main_fill, amount_added = supply_increases[0]
        title = f"Possible bulk {fill_type_label(main_fill)} purchase"
        detail = f"Stored amount increased by {amount_added:,.0f} units while the balance fell"
        event_type = "supply_purchase"
        confidence = "inferred"
        confidence_reason = "The bulk inventory increase supports a purchase, but no new pallet or bag was saved"
        evidence.append(f"{fill_type_label(main_fill)} increased by {amount_added:,.0f} units")
    elif delta > 0 and removed_assets:
        sources.append("vehicles.xml")
        title = f"Vehicle sale: {summary_names(removed_assets)}"
        detail = "Fleet asset removed from vehicles.xml"
        event_type = "vehicle_sale"
        evidence.append(f"{len(removed_assets)} fleet asset(s) removed")
    elif delta > 0 and removed_products:
        sources.append("vehicles.xml")
        title = f"Product sale: {summary_names(removed_products)}"
        detail = "Product pallet or bale disappeared while the balance increased"
        event_type = "product_sale"
        evidence.append(f"{len(removed_products)} product object(s) removed")
    elif delta > 0 and autosale_outputs and (not sellable_decreases or autosale_inventory_match):
        sources.append("placeables.xml")
        title, detail = production_autosale_summary(autosale_outputs)
        event_type = "production_autosale"
        confidence = "inferred"
        confidence_reason = "The farm balance rose while owned production outputs were configured for direct selling; FS25 does not save a separate autosale transaction line"
        meta["production_autosale"] = {
            "mode": "DIRECT_SELL",
            "outputs": autosale_outputs,
            "inventory_match": autosale_inventory_match,
        }
        for output in autosale_outputs[:5]:
            site_text = ", ".join(output.get("sites", [])[:3]) or "owned production building"
            evidence.append(f"{output.get('label') or fill_type_label(output.get('fill_type', 'UNKNOWN'))} is set to direct sell at {site_text}")
    elif delta > 0 and sellable_decreases:
        sources.append("economy.xml")
        main_fill, amount_removed = sellable_decreases[0]
        title = f"Possible {fill_type_label(main_fill)} sale"
        detail = f"Reported amount fell by {amount_removed:,.0f} units"
        event_type = "product_sale"
        confidence = "inferred"
        confidence_reason = "The product quantity fell with a positive balance change, but the game did not save a direct sale label"
        evidence.append(f"{fill_type_label(main_fill)} decreased by {amount_removed:,.0f} units")
    elif delta > 0:
        title = "Unclassified income"
        detail = "No matching mission, product or fleet change was captured in this save interval"
        event_type = "income"
        confidence = "inferred"
        confidence_reason = "Only the farm balance increase was available in the sampled files"
    else:
        title = "Operating expense or unclassified purchase"
        detail = "No matching fleet, supply or bulk inventory addition was captured in this save interval"
        event_type = "expense"
        confidence = "inferred"
        confidence_reason = "Only the farm balance decrease was available in the sampled files"

    meta["sources"] = list(dict.fromkeys(sources))
    meta["confidence_reason"] = confidence_reason
    event_type, title, detail, confidence, meta = apply_classification_rule(
        event_type, delta, title, detail, confidence, meta
    )
    add_event(event_type, title, detail, delta, confidence, meta)
    record_balance(as_float(new_balance))


def stats_worker() -> None:
    initial = load_snapshot("stats") is None
    previous_snapshot = load_snapshot("stats") or {}
    previous_players = previous_snapshot.get("server", {}).get("players", [])
    previous_content_hash: str | None = None
    failures = 0
    while not STOP_EVENT.is_set():
        started = time.monotonic()
        try:
            content = request_bytes(STATS_URL)
            latency_ms = (time.monotonic() - started) * 1000
            content_hash = hashlib.sha256(content).hexdigest()
            parsed = parse_stats(content, latency_ms)
            content_changed = content_hash != previous_content_hash

            if content_changed:
                current_players = parsed["server"]["players"]
                reconcile_players(previous_players, current_players, initial)

                with STATE_LOCK:
                    was_online = APP_STATE["server"].get("online", False)
                    APP_STATE["server"] = parsed["server"]
                    APP_STATE["live"] = parsed["live"]
                if not initial and not was_online and parsed["server"]["online"]:
                    add_event("server_online", "Server is online", parsed["server"]["name"])
                save_snapshot("stats", parsed)
                previous_players = deepcopy(current_players)
                previous_snapshot = parsed
                previous_content_hash = content_hash
                initial = False
                bump_version()
            else:
                # Keep health metadata fresh without rebuilding the complete page.
                with STATE_LOCK:
                    APP_STATE["server"]["latency_ms"] = round(latency_ms, 1)
                    APP_STATE["server"]["last_success"] = parsed["server"]["last_success"]
                    APP_STATE["server"]["last_error"] = None

            source_status_update(
                "stats", success=True, started=started, content=content,
                changed=content_changed, interval=STATS_POLL_SECONDS,
            )
            failures = 0
        except Exception as error:  # noqa: BLE001 - service must remain alive
            failures += 1
            LOGGER.warning("Stats feed failed (%s): %s", failures, error)
            source_status_update(
                "stats", success=False, started=started, error=str(error), interval=STATS_POLL_SECONDS
            )
            with STATE_LOCK:
                APP_STATE["server"]["last_error"] = str(error)
                if failures >= 2 and APP_STATE["server"].get("online"):
                    APP_STATE["server"]["online"] = False
                    APP_STATE["server"]["players_used"] = 0
                    APP_STATE["server"]["players"] = []
                    add_event("server_offline", "Server appears offline", str(error))
                    bump_version()
        STOP_EVENT.wait(STATS_POLL_SECONDS)


def map_worker() -> None:
    previous_content_hash: str | None = None
    active_url: str | None = None
    if MAP_PATH.is_file():
        try:
            cached = MAP_PATH.read_bytes()
            previous_content_hash = hashlib.sha256(cached).hexdigest()
            width, height = image_dimensions(cached)
            with STATE_LOCK:
                APP_STATE["collector"]["map_width"] = width
                APP_STATE["collector"]["map_height"] = height
                APP_STATE["collector"]["map_bytes"] = len(cached)
                APP_STATE["collector"]["map_source"] = "Cached map image"
        except OSError:
            previous_content_hash = None

    candidates = map_url_candidates(MAP_URL)

    while not STOP_EVENT.is_set():
        interval = current_map_poll_interval()
        with STATE_LOCK:
            APP_STATE["collector"]["current_map_poll_seconds"] = interval
        started = time.monotonic()
        errors: list[str] = []
        ordered = ([active_url] if active_url else []) + [url for url in candidates if url != active_url]
        content: bytes | None = None
        selected_url: str | None = None

        for candidate in ordered:
            if not candidate:
                continue
            try:
                downloaded = request_bytes(candidate)
                if downloaded[:2] != b"\xff\xd8" and not downloaded.startswith(b"\x89PNG"):
                    raise ValueError("response was not an image")
                content = downloaded
                selected_url = candidate
                break
            except Exception as error:  # noqa: BLE001
                errors.append(f"{urlparse(candidate).path}: {error}")

        if content is None or selected_url is None:
            message = "; ".join(errors) or "No map response"
            LOGGER.warning("Map feed failed for all resolution candidates: %s", message)
            source_status_update("map", success=False, started=started, error=message, interval=interval)
            adaptive_wait("map", interval)
            continue

        active_url = selected_url
        content_hash = hashlib.sha256(content).hexdigest()
        width, height = image_dimensions(content)
        changed = content_hash != previous_content_hash
        with STATE_LOCK:
            needs_initial_stamp = APP_STATE["collector"].get("map_updated_at") is None
            metadata_changed = (
                APP_STATE["collector"].get("map_width") != width
                or APP_STATE["collector"].get("map_height") != height
                or APP_STATE["collector"].get("map_bytes") != len(content)
                or APP_STATE["collector"].get("map_source") != safe_map_source_label(selected_url)
            )

        if changed:
            temporary = MAP_PATH.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(MAP_PATH)

        if changed or needs_initial_stamp or metadata_changed:
            with STATE_LOCK:
                APP_STATE["collector"]["map_updated_at"] = utc_now()
                APP_STATE["collector"]["map_width"] = width
                APP_STATE["collector"]["map_height"] = height
                APP_STATE["collector"]["map_bytes"] = len(content)
                APP_STATE["collector"]["map_source"] = safe_map_source_label(selected_url)
            previous_content_hash = content_hash
            bump_version()
        source_status_update("map", success=True, started=started, content=content, changed=changed, interval=interval)
        adaptive_wait("map", interval)

def savegame_worker() -> None:
    previous_career = load_snapshot("career")
    previous_fleet = load_snapshot("fleet")
    previous_economy = load_snapshot("economy")
    previous_missions = load_snapshot("missions")
    previous_productions = load_snapshot("placeables")
    previous_hashes: dict[str, str | None] = {
        "career": None, "vehicles": None, "economy": None, "missions": None, "placeables": None
    }
    production_history_repaired = False
    housekeeping_day = ""

    while not STOP_EVENT.is_set():
        interval = current_save_poll_interval()
        with STATE_LOCK:
            APP_STATE["collector"]["current_save_poll_seconds"] = interval
        career = previous_career
        fleet = previous_fleet
        economy = previous_economy
        missions = previous_missions
        productions = previous_productions
        loaded: dict[str, bool] = {key: False for key in previous_hashes}
        raw_changed: dict[str, bool] = {key: False for key in previous_hashes}
        history_repaired = 0

        def fetch_and_parse(name: str, loader: Any, parser: Any, previous: Any) -> Any:
            started = time.monotonic()
            try:
                content = loader()
                digest = hashlib.sha256(content).hexdigest()
                changed = digest != previous_hashes.get(name)
                parsed = parser(content) if changed or previous is None else previous
                previous_hashes[name] = digest
                loaded[name] = True
                raw_changed[name] = changed
                source_status_update(name, success=True, started=started, content=content, changed=changed, interval=interval)
                return parsed
            except Exception as error:  # noqa: BLE001
                LOGGER.warning("%s feed failed: %s", name.title(), error)
                source_status_update(name, success=False, started=started, error=str(error), interval=interval)
                with STATE_LOCK:
                    target = "fleet" if name == "vehicles" else "productions" if name == "placeables" else name
                    if target in APP_STATE:
                        APP_STATE[target]["last_error"] = str(error)
                return previous

        career = fetch_and_parse("career", lambda: request_bytes(CAREER_URL), parse_career, previous_career)
        fleet = fetch_and_parse("vehicles", lambda: request_bytes(VEHICLES_URL), parse_vehicles, previous_fleet)
        economy = fetch_and_parse("economy", lambda: request_bytes(ECONOMY_URL), parse_economy, previous_economy)

        # Reuse one FTP login for missions.xml and placeables.xml when both use the savegame FTP source.
        if not MISSIONS_URL and not PLACEABLES_URL and missions_ftp_configured() and placeables_source_configured():
            started = time.monotonic()
            try:
                auxiliary = request_ftp_files({
                    "missions": MISSIONS_FTP_PATH,
                    "placeables": derived_placeables_ftp_path(),
                })
                for name, parser, previous in (
                    ("missions", parse_missions, previous_missions),
                    ("placeables", parse_placeables, previous_productions),
                ):
                    content = auxiliary[name]
                    digest = hashlib.sha256(content).hexdigest()
                    changed = digest != previous_hashes.get(name)
                    parsed = parser(content) if changed or previous is None else previous
                    previous_hashes[name] = digest
                    loaded[name] = True
                    raw_changed[name] = changed
                    source_status_update(name, success=True, started=started, content=content, changed=changed, interval=interval)
                    if name == "missions":
                        missions = parsed
                    else:
                        productions = parsed
            except Exception as error:  # noqa: BLE001
                LOGGER.warning("Savegame FTP feeds failed: %s", error)
                for name in ("missions", "placeables"):
                    source_status_update(name, success=False, started=started, error=str(error), interval=interval)
                with STATE_LOCK:
                    APP_STATE["missions"]["last_error"] = str(error)
                    APP_STATE["productions"]["last_error"] = str(error)
        else:
            if missions_source_configured():
                missions = fetch_and_parse("missions", request_missions_bytes, parse_missions, previous_missions)
            else:
                with STATE_LOCK:
                    APP_STATE["missions"]["last_error"] = "No missions HTTP or FTP source is configured"
            if placeables_source_configured():
                productions = fetch_and_parse("placeables", request_placeables_bytes, parse_placeables, previous_productions)
            else:
                with STATE_LOCK:
                    APP_STATE["productions"]["last_error"] = "No placeables HTTP source or reusable savegame FTP connection is configured"

        if loaded.get("placeables") and productions and not production_history_repaired:
            history_repaired = migrate_recent_production_income(productions)
            if history_repaired:
                LOGGER.info(
                    "Reclassified %s recent unknown income entr%s as likely production autosales",
                    history_repaired, "y" if history_repaired == 1 else "ies",
                )
            production_history_repaired = True

        changed = False
        if career and fleet and economy and loaded.get("career") and loaded.get("vehicles") and loaded.get("economy"):
            career_changed = stable_payload(career) != stable_payload(previous_career)
            fleet_changed = stable_payload(fleet) != stable_payload(previous_fleet)
            economy_changed = stable_payload(economy) != stable_payload(previous_economy)
            missions_changed = loaded.get("missions") and stable_payload(missions) != stable_payload(previous_missions)
            productions_changed = loaded.get("placeables") and stable_payload(productions) != stable_payload(previous_productions)
            changed = career_changed or fleet_changed or economy_changed or missions_changed or productions_changed or history_repaired > 0

            if changed:
                try:
                    detect_savegame_changes(
                        previous_career,
                        previous_fleet,
                        previous_economy,
                        previous_missions,
                        previous_productions,
                        career,
                        fleet,
                        economy,
                        missions,
                        productions or previous_productions,
                    )
                except Exception as error:  # noqa: BLE001
                    LOGGER.exception("Change detection failed: %s", error)
                save_snapshot("career", career)
                save_snapshot("fleet", fleet)
                save_snapshot("economy", economy)
                if loaded.get("missions") and missions is not None:
                    save_snapshot("missions", missions)
                if loaded.get("placeables") and productions is not None:
                    save_snapshot("placeables", productions)

            previous_career = deepcopy(career)
            previous_fleet = deepcopy(fleet)
            previous_economy = deepcopy(economy)
            if loaded.get("missions") and missions is not None:
                previous_missions = deepcopy(missions)
            if loaded.get("placeables") and productions is not None:
                previous_productions = deepcopy(productions)

        with STATE_LOCK:
            if career:
                APP_STATE["career"] = career
            if fleet:
                APP_STATE["fleet"] = fleet
            if economy:
                APP_STATE["economy"] = economy
            if missions:
                APP_STATE["missions"] = missions
            if productions:
                APP_STATE["productions"] = productions
        if changed:
            bump_version()

        today = datetime.now().strftime("%Y-%m-%d")
        if today != housekeeping_day:
            database_housekeeping()
            housekeeping_day = today
        adaptive_wait("save", interval)

def attach_live_session_times(state: dict[str, Any]) -> None:
    now = unix_now()
    for player in state.get("server", {}).get("players", []):
        started = current_session_started(player.get("name", ""))
        player["session_started"] = started
        player["session_seconds"] = max(0, now - started) if started else 0


def overview_payload() -> dict[str, Any]:
    with STATE_LOCK:
        state = deepcopy(APP_STATE)
    attach_live_session_times(state)
    state["recent_events"] = get_recent_events(10)
    state["summaries"] = {
        "fleet": {
            "owned": state["fleet"].get("owned_count", 0),
            "leased": state["fleet"].get("leased_count", 0),
            "maintenance": state["fleet"].get("maintenance_count", 0),
        },
        "economy": {
            "money": state["career"].get("money"),
            "inventory_count": state["economy"].get("inventory_count", 0),
            "active_demands": sum(1 for item in state["economy"].get("great_demands", []) if item.get("is_running")),
        },
        "mods": {
            "count": state["live"].get("mod_count", 0) or state["career"].get("mod_count", 0),
        },
        "history": get_session_summary(7),
    }
    return state


def filtered_fleet_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    with STATE_LOCK:
        fleet = deepcopy(APP_STATE["fleet"])
    vehicles = fleet.get("vehicles", [])
    search = clean_text(query.get("q", [""])[0]).lower()
    state_filter = clean_text(query.get("state", [""])[0]).upper()
    farm_only = clean_text(query.get("farm_only", ["true"])[0]).lower() != "false"
    maintenance_only = clean_text(query.get("maintenance", ["false"])[0]).lower() == "true"

    if farm_only:
        vehicles = [item for item in vehicles if item.get("farm_id", 0) > 0 and item.get("property_state") in {"OWNED", "LEASED"}]
    if state_filter:
        vehicles = [item for item in vehicles if item.get("property_state") == state_filter]
    if search:
        vehicles = [
            item
            for item in vehicles
            if search in f"{item.get('name')} {item.get('filename')} {item.get('mod_name')}".lower()
        ]
    if maintenance_only:
        vehicles = [
            item
            for item in vehicles
            if item.get("damage", 0) >= 0.3
            or (item.get("condition") is not None and item["condition"] < 0.55)
            or (item.get("service") is not None and item["service"] < 0.55)
        ]
    fleet["vehicles"] = vehicles
    fleet["returned_count"] = len(vehicles)
    return fleet


def economy_payload(days: int) -> dict[str, Any]:
    with STATE_LOCK:
        career = deepcopy(APP_STATE["career"])
        economy = deepcopy(APP_STATE["economy"])
        missions = deepcopy(APP_STATE["missions"])
        productions = deepcopy(APP_STATE["productions"])
        fleet = deepcopy(APP_STATE["fleet"])
        collector = deepcopy(APP_STATE["collector"])
    return {
        "career": career,
        "economy": economy,
        "missions": missions,
        "productions": productions,
        "fleet_summary": {
            "supply_count": fleet.get("supply_count", 0),
            "product_object_count": fleet.get("product_object_count", 0),
        },
        "collector": collector,
        "history": get_economy_history(days),
        "review": review_payload(),
        "currency_symbol": CURRENCY_SYMBOL,
    }


def mods_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    with STATE_LOCK:
        mods = deepcopy(APP_STATE["live"].get("mods", []))
    search = clean_text(query.get("q", [""])[0]).lower()
    if search:
        mods = [
            item
            for item in mods
            if search in f"{item.get('name')} {item.get('internal_name')} {item.get('author')} {item.get('version')}".lower()
        ]
    authors: dict[str, int] = {}
    for mod in mods:
        author = mod.get("author") or "Unknown"
        authors[author] = authors.get(author, 0) + 1
    return {
        "mods": mods,
        "count": len(mods),
        "authors": sorted(
            ({"author": key, "count": value} for key, value in authors.items()),
            key=lambda item: (-item["count"], item["author"].lower()),
        ),
    }


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def events_csv() -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Type", "Title", "Detail", "Amount", "Confidence", "Confidence reason", "Sources"])
    for event in get_recent_events(500):
        date = datetime.fromtimestamp(event["ts"], tz=timezone.utc).isoformat()
        meta = event.get("meta") or {}
        writer.writerow(
            [
                date,
                event["event_type"],
                event["title"],
                event["detail"],
                event["amount"],
                event["confidence"],
                meta.get("confidence_reason", ""),
                ", ".join(meta.get("sources") or []),
            ]
        )
    return output.getvalue().encode("utf-8-sig")


class DashboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FS25Hub/0.5.5"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.debug("%s - %s", self.client_address[0], fmt % args)

    def ingress_allowed(self) -> bool:
        if ALLOW_DIRECT:
            return True
        client_ip = self.client_address[0]
        return client_ip == "172.30.32.2" or client_ip.startswith("127.")

    def send_body(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache_control: str = "no-store",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_body(json_bytes(payload), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802
        if not self.ingress_allowed():
            self.send_json({"error": "Ingress access only"}, HTTPStatus.FORBIDDEN)
            return

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/api/health":
                self.send_json({"status": "ok", "version": APP_STATE["version"]})
            elif path == "/api/overview":
                self.send_json(overview_payload())
            elif path == "/api/vehicles":
                self.send_json(filtered_fleet_payload(query))
            elif path == "/api/economy":
                days = min(max(as_int(query.get("days", ["30"])[0], 30), 1), 365)
                self.send_json(economy_payload(days))
            elif path == "/api/review":
                self.send_json(review_payload())
            elif path == "/api/diagnostics":
                self.send_json(diagnostics_payload())
            elif path == "/api/mods":
                self.send_json(mods_payload(query))
            elif path == "/api/history":
                days = min(max(as_int(query.get("days", ["30"])[0], 30), 1), 365)
                self.send_json({"history": get_session_summary(days), "events": get_recent_events(100)})
            elif path == "/api/events.csv":
                self.send_body(
                    events_csv(),
                    "text/csv; charset=utf-8",
                    extra_headers={"Content-Disposition": 'attachment; filename="fs25-events.csv"'},
                )
            elif path == "/api/map.jpg":
                if MAP_PATH.exists():
                    content = MAP_PATH.read_bytes()
                    self.send_body(content, image_content_type(content), cache_control="no-cache")
                else:
                    self.send_json({"error": "Map image not available yet"}, HTTPStatus.NOT_FOUND)
            elif path == "/api/stream":
                self.handle_stream()
            else:
                self.serve_static(path)
        except BrokenPipeError:
            return
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("Request failed for %s", path)
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def read_request_body(self, maximum_bytes: int = 65536) -> bytes:
        """Read fixed-length or HTTP/1.1 chunked request bodies.

        Home Assistant Ingress can proxy browser POST requests using
        ``Transfer-Encoding: chunked`` instead of ``Content-Length``. Leaving
        that body unread causes an empty JSON payload and can corrupt the next
        keep-alive request, producing a mixture of 400, 501 and 502 errors.
        """
        transfer_encoding = clean_text(self.headers.get("Transfer-Encoding")).lower()
        if transfer_encoding:
            encodings = [item.strip() for item in transfer_encoding.split(",") if item.strip()]
            if encodings[-1:] != ["chunked"]:
                raise ValueError("Unsupported request transfer encoding")

            body = bytearray()
            while True:
                size_line = self.rfile.readline(4096)
                if not size_line:
                    raise ValueError("Incomplete chunked request")
                if len(size_line) >= 4096 and not size_line.endswith(b"\n"):
                    raise ValueError("Invalid chunk header")
                try:
                    size_text = size_line.split(b";", 1)[0].strip()
                    chunk_size = int(size_text, 16)
                except ValueError as error:
                    raise ValueError("Invalid chunk header") from error

                if chunk_size < 0:
                    raise ValueError("Invalid chunk size")
                if chunk_size == 0:
                    # Consume optional trailer headers and the terminating CRLF.
                    while True:
                        trailer = self.rfile.readline(8192)
                        if not trailer:
                            raise ValueError("Incomplete chunked request trailer")
                        if trailer in (b"\r\n", b"\n"):
                            break
                    break

                if len(body) + chunk_size > maximum_bytes:
                    # Close the connection because the unread remainder cannot
                    # safely be reused by the HTTP/1.1 server.
                    self.close_connection = True
                    raise ValueError("JSON request is too large")

                chunk = self.rfile.read(chunk_size)
                if len(chunk) != chunk_size:
                    raise ValueError("Incomplete chunked request")
                body.extend(chunk)

                ending = self.rfile.read(2)
                if ending != b"\r\n":
                    raise ValueError("Invalid chunk terminator")
            return bytes(body)

        content_length = clean_text(self.headers.get("Content-Length"))
        if not content_length:
            return b""
        try:
            length = int(content_length)
        except ValueError as error:
            raise ValueError("Invalid Content-Length header") from error
        if length < 0:
            raise ValueError("Invalid Content-Length header")
        if length > maximum_bytes:
            self.close_connection = True
            raise ValueError("JSON request is too large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Incomplete request body")
        return body

    def read_json_request(self) -> dict[str, Any]:
        body = self.read_request_body()
        if not body:
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid JSON request") from error
        if not isinstance(payload, dict):
            raise ValueError("JSON request must be an object")
        return payload

    def do_POST(self) -> None:  # noqa: N802
        if not self.ingress_allowed():
            self.send_json({"error": "Ingress access only"}, HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        try:
            payload = self.read_json_request()
            if path == "/api/review/classify":
                result = classify_event(
                    as_int(payload.get("event_id")),
                    clean_text(payload.get("category")),
                    clean_text(payload.get("label")),
                    bool(payload.get("remember_rule")),
                )
                self.send_json({"ok": True, **result})
            elif path == "/api/review/rules/delete":
                delete_classification_rule(as_int(payload.get("rule_id")))
                self.send_json({"ok": True})
            else:
                self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("POST request failed for %s", path)
            self.send_json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_stream(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last_version = -1
        try:
            while not STOP_EVENT.is_set():
                with STATE_VERSION_CONDITION:
                    current_version = APP_STATE["version"]
                    if current_version == last_version:
                        STATE_VERSION_CONDITION.wait(timeout=15)
                        current_version = APP_STATE["version"]
                if current_version != last_version:
                    payload = json.dumps(
                        {"version": current_version, "generated_at": APP_STATE["generated_at"]},
                        separators=(",", ":"),
                    )
                    self.wfile.write(f"event: update\ndata: {payload}\n\n".encode("utf-8"))
                    last_version = current_version
                else:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            relative = "index.html"
        else:
            relative = path.lstrip("/")
        requested = (WEB_DIR / relative).resolve()
        if WEB_DIR.resolve() not in requested.parents and requested != WEB_DIR.resolve():
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        if not requested.is_file():
            # SPA routes use hash navigation, but returning index keeps refreshes safe.
            requested = WEB_DIR / "index.html"
        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        cache_control = "no-cache" if requested.name == "index.html" else "public, max-age=3600"
        self.send_body(requested.read_bytes(), content_type, cache_control=cache_control)


def validate_configuration() -> None:
    required = {
        "stats_url": STATS_URL,
        "map_url": MAP_URL,
        "career_url": CAREER_URL,
        "vehicles_url": VEHICLES_URL,
        "economy_url": ECONOMY_URL,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(f"Missing required configuration values: {', '.join(missing)}")
    if not missions_source_configured():
        LOGGER.warning("No missions HTTP or FTP source is configured; contract payments will remain inferred")
    elif not MISSIONS_URL:
        missing = [
            name
            for name, value in {
                "missions_ftp_host": MISSIONS_FTP_HOST,
                "missions_ftp_username": MISSIONS_FTP_USERNAME,
                "missions_ftp_path": MISSIONS_FTP_PATH,
            }.items()
            if not value
        ]
        if missing:
            LOGGER.warning("Incomplete missions FTP configuration: %s", ", ".join(missing))
    if not placeables_source_configured():
        LOGGER.warning("No placeables source is configured; production autosales will remain unclassified")


def restore_cached_state() -> None:
    with STATE_LOCK:
        stats = load_snapshot("stats")
        career = load_snapshot("career")
        fleet = load_snapshot("fleet")
        economy = load_snapshot("economy")
        missions = load_snapshot("missions")
        productions = load_snapshot("placeables")
        if stats:
            APP_STATE["server"] = stats.get("server", APP_STATE["server"])
            APP_STATE["live"] = stats.get("live", APP_STATE["live"])
        if career:
            APP_STATE["career"] = career
        if fleet:
            APP_STATE["fleet"] = fleet
        if economy:
            APP_STATE["economy"] = economy
        if missions:
            APP_STATE["missions"] = missions
        if productions:
            APP_STATE["productions"] = productions


def main() -> None:
    validate_configuration()
    initialise_database()
    restore_cached_state()

    workers = [
        threading.Thread(target=stats_worker, name="stats-worker", daemon=True),
        threading.Thread(target=map_worker, name="map-worker", daemon=True),
        threading.Thread(target=savegame_worker, name="savegame-worker", daemon=True),
    ]
    for worker in workers:
        worker.start()

    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    LOGGER.info("FS25 Server Hub listening on %s:%s", HOST, PORT)
    LOGGER.info(
        "Polling stats every %ss, map every %ss, savegame every %ss; adaptive=%s (empty map %ss / save %ss); requesting map up to %spx at quality %s",
        STATS_POLL_SECONDS,
        MAP_POLL_SECONDS,
        SAVE_POLL_SECONDS,
        ADAPTIVE_POLLING,
        EMPTY_SERVER_MAP_POLL_SECONDS,
        EMPTY_SERVER_SAVE_POLL_SECONDS,
        MAP_HD_SIZE,
        MAP_HD_QUALITY,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        STOP_EVENT.set()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
