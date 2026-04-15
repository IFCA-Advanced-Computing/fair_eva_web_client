"""Flask application for the FAIR EVA web client.

This module implements a lightweight user interface for submitting a
persistent identifier to the FAIR EVA API and visualising the results.
It supports a development mode where evaluation data is loaded from a
local JSON file instead of calling the API.  Configuration values
(such as the API URL, page title and logo) can be provided via
environment variables or command‑line flags.
"""

from __future__ import annotations
import configparser
from datetime import datetime, timezone
from html import unescape
from io import BytesIO
import importlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import uuid
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Dict, Optional, Tuple, List
from urllib.parse import urlparse

from flask import (
    Flask,
    Response,
    current_app,
    g,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import Babel, gettext
from flask_babel import lazy_gettext as _l
from werkzeug.middleware.proxy_fix import ProxyFix

from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, SubmitField
from wtforms.validators import DataRequired



import requests

try:
    import idutils
except Exception:
    idutils = None

###############################################################################
# Configuration dataclass
###############################################################################

@dataclass
class Settings:
    """Holds runtime configuration values for the application.

    Attributes
    ----------
    api_url: str
        Base URL of the FAIR EVA API (without trailing slash).
    api_port: int
        Port on which the FAIR EVA API listens.
    title: str
        Browser tab title and header for the web client.
    logo_url: str
        Hyperlink applied to the logo in the navigation bar.
    logo_image: str
        Relative path (within ``static/img``) to the logo image.
    dev_mode: bool
        When true, use a local JSON file instead of contacting the API.
    sample_file: str
        Path to a JSON file containing evaluation results for development
        mode.  This file must follow the structure returned by the FAIR EVA
        API.
    """

    api_url: str = os.getenv("FAIR_EVA_API_URL", "http://localhost")
    api_port: int = int(os.getenv("FAIR_EVA_API_PORT", "8080"))
    api_timeout: int = int(os.getenv("FAIR_EVA_API_TIMEOUT", "90"))
    title: str = os.getenv("FAIR_EVA_TITLE", "FAIR EVA")
    logo_url: str = os.getenv("FAIR_EVA_LOGO_URL", "https://digital.csic.es")
    logo_image: str = os.getenv("FAIR_EVA_LOGO_IMAGE", "logo_fair_eosc.png")
    dev_mode: bool = os.getenv("FAIR_EVA_DEV", "0") == "1"
    sample_file: str = os.getenv(
        "FAIR_EVA_SAMPLE_FILE",
        os.path.join(os.path.dirname(__file__), "data", "salida_new.json"),
    )
    api_eval_path: str = os.getenv("FAIR_EVA_API_EVAL_PATH", "/v1.0/rda/rda_all")
    api_plugins_path: str = os.getenv("FAIR_EVA_API_PLUGINS_PATH", "")
    plugins_file: str = os.getenv(
        "FAIR_EVA_PLUGINS_FILE",
        os.path.join(os.path.dirname(__file__), "data", "plugins_example.json"),
    )
    reports_dir: str = os.getenv(
        "FAIR_EVA_REPORTS_DIR",
        os.path.join(tempfile.gettempdir(), "fair_eva_web_client_reports"),
    )
    store_evaluations: bool = os.getenv("FAIR_EVA_STORE_EVALUATIONS", "0") == "1"
    evaluations_db_path: str = os.getenv(
        "FAIR_EVA_EVALUATIONS_DB_PATH",
        os.path.join(tempfile.gettempdir(), "fair_eva_web_client_evaluations.sqlite"),
    )


def apply_ini_overrides(cfg: Settings, config_path: str) -> None:
    """Load runtime overrides from an INI file into Settings."""
    if not config_path or not os.path.isfile(config_path):
        return

    print(config_path)
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")

    section = None
    for candidate in ("fair_eva", "FAIR_EVA"):
        if parser.has_section(candidate):
            section = parser[candidate]
            break
    if section is None:
        return

    if section.get("api_url"):
        cfg.api_url = section.get("api_url", cfg.api_url)
    if section.get("api_port"):
        cfg.api_port = int(section.get("api_port", cfg.api_port))
    if section.get("api_timeout"):
        cfg.api_timeout = int(section.get("api_timeout", cfg.api_timeout))
    if section.get("api_eval_path"):
        cfg.api_eval_path = section.get("api_eval_path", cfg.api_eval_path)
    if section.get("api_plugins_path") is not None:
        cfg.api_plugins_path = section.get("api_plugins_path", cfg.api_plugins_path)
    if section.get("plugins_file"):
        cfg.plugins_file = section.get("plugins_file", cfg.plugins_file)
    if section.get("reports_dir"):
        cfg.reports_dir = section.get("reports_dir", cfg.reports_dir)
    if section.get("title"):
        cfg.title = section.get("title", cfg.title)
    if section.get("logo_url"):
        cfg.logo_url = section.get("logo_url", cfg.logo_url)
    if section.get("logo_image"):
        cfg.logo_image = section.get("logo_image", cfg.logo_image)
    if section.get("sample_file"):
        cfg.sample_file = section.get("sample_file", cfg.sample_file)
    if section.get("dev_mode") is not None:
        cfg.dev_mode = section.getboolean("dev_mode", fallback=cfg.dev_mode)
    if section.get("store_evaluations") is not None:
        cfg.store_evaluations = section.getboolean(
            "store_evaluations",
            fallback=cfg.store_evaluations,
        )
    if section.get("evaluations_db_path"):
        cfg.evaluations_db_path = section.get(
            "evaluations_db_path",
            cfg.evaluations_db_path,
        )


###############################################################################
# Plugin helpers
###############################################################################


def load_available_plugins(config_path: Optional[str] = None) -> List[Tuple[str, str]]:
    """Return the list of plugins defined by configuration or installed packages.

    The loader follows a three-step approach:

    1. If the ``FAIR_EVA_PLUGINS_FILE`` environment variable (or the explicit
       ``config_path`` argument) points to a JSON file, this function expects it
       to contain either a list of ``{"id": "...", "label": "..."}`` objects
       or a list of two-element arrays/tuples.  Only entries with both parts are
       kept.
    2. If no config file is provided, it searches for installed plugins under
       ``fair_eva/plugin`` directories within any entry in ``sys.path`` (for
       example ``lib64/python3.12/site-packages/fair_eva/plugin`` inside a
       virtual environment).  Each subdirectory is treated as a plugin and, if
       importable, its ``DISPLAY_NAME`` or ``name`` attribute is used as the
       label; otherwise the folder name is used.
    3. Failing that, it inspects Python entry points under the
       ``fair_eva.plugins`` group.  The entry point name is treated as the
       plugin identifier.  If the loaded object exposes ``DISPLAY_NAME`` or
       ``name`` attributes they are used as the label; otherwise, the entry
       point name is used for both fields.

    When no plugins can be resolved dynamically, an empty list is returned so
    the caller can decide on a fallback strategy.
    """

    plugins: List[Tuple[str, str]] = []

    path = config_path or os.getenv("FAIR_EVA_PLUGINS_FILE")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            file_plugins: List[Tuple[str, str]] = []
            if isinstance(data, list):
                for entry in data:
                    plugin_id: Optional[str] = None
                    label: Optional[str] = None
                    if isinstance(entry, dict):
                        plugin_id = entry.get("id") or entry.get("name")
                        label = (
                            entry.get("label")
                            or entry.get("title")
                            or entry.get("display_name")
                        )
                    elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                        plugin_id, label = entry[0], entry[1]
                    if plugin_id and label:
                        file_plugins.append((str(plugin_id), str(label)))
            if file_plugins:
                return file_plugins
        except Exception:
            # If the file cannot be read or parsed, continue with the next
            # discovery strategy.
            pass

    seen: set[str] = set()

    # Discover plugins installed in site-packages under fair_eva/plugin/*
    plugin_roots = []
    for path_entry in sys.path:
        candidate = os.path.join(path_entry, "fair_eva", "plugin")
        if os.path.isdir(candidate):
            plugin_roots.append(candidate)

    for root in plugin_roots:
        for entry in os.listdir(root):
            full_path = os.path.join(root, entry)
            if not os.path.isdir(full_path) or entry.startswith("__"):
                continue
            plugin_id = entry
            label: Optional[str] = None
            if plugin_id in seen:
                continue
            try:
                plugin_mod = importlib.import_module(f"fair_eva.plugin.{plugin_id}")
                label = getattr(plugin_mod, "DISPLAY_NAME", None) or getattr(
                    plugin_mod, "name", None
                )
            except Exception:
                label = None
            seen.add(plugin_id)
            plugins.append((plugin_id, str(label or plugin_id)))
    if plugins:
        return plugins

    try:
        entry_points = metadata.entry_points()
        candidates = (
            entry_points.select(group="fair_eva.plugins")
            if hasattr(entry_points, "select")
            else entry_points.get("fair_eva.plugins", [])
        )
        plugins = []
        for ep in candidates:
            try:
                plugin_obj = ep.load()
                label = getattr(plugin_obj, "DISPLAY_NAME", None) or getattr(
                    plugin_obj, "name", None
                )
            except Exception:
                label = None
            plugins.append((ep.name, str(label or ep.name)))
        if plugins:
            return plugins
    except Exception:
        pass

    return []


def _extract_plugin_entries(payload: Any) -> List[Dict[str, str]]:
    """Normalize plugin payloads into entries with optional repository label."""
    candidates = payload
    if isinstance(payload, dict):
        for key in ("plugins", "data", "results", "items"):
            if isinstance(payload.get(key), list):
                candidates = payload[key]
                break
    if not isinstance(candidates, list):
        return []

    parsed: List[Dict[str, str]] = []
    for entry in candidates:
        plugin_id: Optional[str] = None
        label: Optional[str] = None
        repository_label: Optional[str] = None
        if isinstance(entry, str):
            plugin_id = entry
            label = entry
        elif isinstance(entry, dict):
            plugin_id = entry.get("id") or entry.get("name") or entry.get("plugin")
            label = (
                entry.get("label")
                or entry.get("title")
                or entry.get("display_name")
                or entry.get("name")
                or plugin_id
            )
            repository_label = (
                entry.get("repository_label")
                or entry.get("repository_name")
                or entry.get("translation_repository_label")
            )
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            plugin_id = str(entry[0])
            label = str(entry[1])
        if plugin_id:
            parsed.append(
                {
                    "id": str(plugin_id),
                    "label": str(label or plugin_id),
                    "repository_label": str(repository_label or label or plugin_id),
                }
            )
    return parsed


def plugin_choices_from_entries(entries: List[Dict[str, str]]) -> List[Tuple[str, str]]:
    """Build SelectField choices from normalized plugin entries."""
    return [(entry["id"], entry["label"]) for entry in entries if entry.get("id")]


def plugin_metadata_from_entries(entries: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """Map plugin id to UI and translation metadata."""
    metadata: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        plugin_id = entry.get("id")
        if not plugin_id:
            continue
        metadata[plugin_id] = {
            "label": entry.get("label", plugin_id),
            "repository_label": entry.get("repository_label", entry.get("label", plugin_id)),
        }
    return metadata


def resolve_repository_label(plugin_id: str) -> Optional[str]:
    """Return the repository label to inject in translated strings."""
    if not plugin_id:
        return None
    metadata = current_app.config.get("PLUGIN_METADATA", {}) or {}
    plugin_meta = metadata.get(plugin_id, {}) or {}
    return (
        plugin_meta.get("repository_label")
        or plugin_meta.get("label")
        or plugin_id
    )


def apply_translation_parameters(value: Any, repository_label: Optional[str] = None) -> Any:
    """Replace translation placeholders with runtime values."""
    if isinstance(value, str) and repository_label:
        return value.replace("REPOSITORY", repository_label)
    return value


def translated_gettext(message: str, *args: Any, **kwargs: Any) -> str:
    """Translate a message and apply runtime placeholder replacements."""
    translated = gettext(message, *args, **kwargs)
    return apply_translation_parameters(
        translated,
        repository_label=getattr(g, "repository_label", None),
    )


def build_default_eval_endpoint(cfg: Settings) -> str:
    """Build the default FAIR EVA evaluation endpoint from settings."""
    payload = ''
    endpoint = f"{cfg.api_url.rstrip('/')}:8080{cfg.api_eval_path}"
    print("FAIR EVA endpoint=%s payload=%s", endpoint, payload)
    return endpoint


def parse_plugins_payload(payload: Any) -> List[Tuple[str, str]]:
    """Normalize plugin payloads into SelectField choices."""
    return plugin_choices_from_entries(_extract_plugin_entries(payload))


def plugin_endpoint_candidates(api_endpoint: str, plugins_endpoint: Optional[str] = None) -> List[str]:
    """Generate candidate plugin-list endpoints from an evaluation endpoint."""
    parsed = urlparse(api_endpoint)
    if not parsed.scheme or not parsed.netloc:
        return []
    root = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    if plugins_endpoint:
        custom = plugins_endpoint.strip()
        custom_parsed = urlparse(custom)
        if custom_parsed.scheme and custom_parsed.netloc:
            return [custom]
        if custom.startswith("/"):
            return [f"{root}{custom}"]
        return [f"{root}/{custom}"]

    candidates: List[str] = [
        f"{root}/v1.0/plugins",
        f"{root}/plugins",
    ]

    if path:
        parent = path.rsplit("/", 1)[0]
        candidates.append(f"{root}{parent}/plugins")
        if "/" in parent.strip("/"):
            grand_parent = parent.rsplit("/", 1)[0]
            candidates.append(f"{root}{grand_parent}/plugins")

    # Deduplicate preserving order
    return list(dict.fromkeys(candidates))


def fetch_plugins_from_api(
    api_endpoint: str,
    timeout: int = 8,
    plugins_endpoint: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Try to fetch available plugins from FAIR EVA API."""
    for plugins_url in plugin_endpoint_candidates(api_endpoint, plugins_endpoint):
        for method in ("GET", "POST"):
            try:
                if method == "GET":
                    response = requests.get(plugins_url, timeout=timeout)
                else:
                    response = requests.post(plugins_url, json={}, timeout=timeout)
                if response.status_code >= 400:
                    continue
                parsed = _extract_plugin_entries(response.json())
                if parsed:
                    return parsed
            except Exception:
                continue
    return []


###############################################################################
# Utility functions
###############################################################################

def build_api_error_message(exc: Exception, endpoint: str) -> str:
    """Return a user-friendly English message for API failures."""
    if isinstance(exc, requests.exceptions.Timeout):
        return (
            "The FAIR EVA API request timed out. "
            f"Please verify that the API is reachable: {endpoint}"
        )
    if isinstance(exc, requests.exceptions.ConnectionError):
        return (
            "Could not connect to the FAIR EVA API. "
            f"Please verify endpoint and network access: {endpoint}"
        )
    if isinstance(exc, requests.exceptions.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", "unknown")
        return f"The FAIR EVA API returned an HTTP error ({status}) at {endpoint}."
    if isinstance(exc, requests.exceptions.RequestException):
        return f"The FAIR EVA API request failed at {endpoint}: {exc}"
    if isinstance(exc, ValueError):
        return (
            "The FAIR EVA API returned an invalid response format. "
            f"Endpoint: {endpoint}"
        )
    return f"Unexpected error while contacting FAIR EVA API: {exc}"


def _safe_report_id(value: str) -> Optional[str]:
    """Validate report identifier used in download endpoints."""
    if re.fullmatch(r"[a-f0-9]{32}", value or ""):
        return value
    return None


def _report_path(cfg: Settings, report_id: str) -> str:
    return os.path.join(cfg.reports_dir, f"{report_id}.json")


def save_evaluation_report(cfg: Settings, payload: Dict[str, Any], report_id: Optional[str] = None) -> str:
    """Persist a report payload to disk and return report identifier."""
    os.makedirs(cfg.reports_dir, exist_ok=True)
    report_id = report_id or uuid.uuid4().hex
    with open(_report_path(cfg, report_id), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return report_id


def load_evaluation_report(cfg: Settings, report_id: str) -> Optional[Dict[str, Any]]:
    """Read persisted report payload from disk."""
    safe_id = _safe_report_id(report_id)
    if not safe_id:
        return None
    report_file = _report_path(cfg, safe_id)
    if not os.path.isfile(report_file):
        return None
    try:
        with open(report_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _utc_now_iso() -> str:
    """Return UTC timestamp without microseconds in ISO 8601 format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    """Parse an ISO timestamp into an aware UTC datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_evaluation_datetime(value: str, language: str) -> str:
    """Format evaluation timestamp for UI display."""
    parsed = _parse_iso_datetime(value)
    if not parsed:
        return value
    if (language or "").startswith("es"):
        return parsed.strftime("%d/%m/%Y %H:%M:%S UTC")
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")


def _ui_text(language: str, spanish: str, english: str) -> str:
    """Return a language-aware short UI message."""
    if (language or "").startswith("es"):
        return spanish
    return english


def normalize_identifier(value: str) -> str:
    """Normalize incoming persistent identifier using idutils when available."""
    candidate = re.sub(r"\s+", "", (value or "").strip())
    if not candidate:
        return ""
    if idutils is None:
        return candidate.lower()

    try:
        schemes = list(idutils.detect_identifier_schemes(candidate) or [])
    except Exception:
        schemes = []

    if not schemes:
        return candidate.lower()

    preferred = [
        "doi",
        "handle",
        "urn",
        "ark",
        "url",
        "orcid",
        "pmid",
        "arxiv",
        "ads",
        "ror",
    ]
    attempted: set[str] = set()
    for scheme in preferred + sorted(schemes):
        if scheme in attempted or scheme not in schemes:
            continue
        attempted.add(scheme)
        try:
            normalized = idutils.normalize_pid(candidate, scheme)
            if normalized:
                return str(normalized).strip().lower()
        except Exception:
            continue
    return candidate.lower()


def init_evaluations_db(cfg: Settings) -> None:
    """Create SQLite database/table for stored evaluations when enabled."""
    if not cfg.store_evaluations:
        return

    db_dir = os.path.dirname(cfg.evaluations_db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with sqlite3.connect(cfg.evaluations_db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                plugin_name TEXT NOT NULL,
                evaluated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_evaluations_identifier_time
            ON evaluations(normalized_id, evaluated_at DESC)
            """
        )
        conn.commit()


def save_evaluation_record(
    cfg: Settings,
    source_id: str,
    plugin_name: str,
    evaluated_at: str,
    payload: Dict[str, Any],
) -> Optional[int]:
    """Persist a full evaluation payload in SQLite and return inserted row id."""
    if not cfg.store_evaluations:
        return None

    normalized_id = normalize_identifier(source_id)
    payload_json = json.dumps(payload, ensure_ascii=False)

    with sqlite3.connect(cfg.evaluations_db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO evaluations (
                normalized_id,
                source_id,
                plugin_name,
                evaluated_at,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (normalized_id, source_id, plugin_name, evaluated_at, payload_json),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_stored_identifiers(cfg: Settings, language: str) -> List[Dict[str, Any]]:
    """Return stored normalized identifiers plus summary metadata."""
    if not cfg.store_evaluations:
        return []

    with sqlite3.connect(cfg.evaluations_db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                normalized_id,
                COUNT(*) AS total,
                MAX(evaluated_at) AS last_evaluated_at
            FROM evaluations
            GROUP BY normalized_id
            ORDER BY last_evaluated_at DESC, normalized_id ASC
            """
        ).fetchall()

    result: List[Dict[str, Any]] = []
    for normalized_id, total, last_evaluated_at in rows:
        result.append(
            {
                "normalized_id": str(normalized_id),
                "total": int(total or 0),
                "last_evaluated_at": str(last_evaluated_at or ""),
                "last_display": format_evaluation_datetime(str(last_evaluated_at or ""), language),
            }
        )
    return result


def list_evaluation_runs(cfg: Settings, normalized_id: str, language: str) -> List[Dict[str, Any]]:
    """Return all evaluation timestamps for a normalized identifier."""
    if not cfg.store_evaluations or not normalized_id:
        return []

    with sqlite3.connect(cfg.evaluations_db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, source_id, plugin_name, evaluated_at
            FROM evaluations
            WHERE normalized_id = ?
            ORDER BY evaluated_at DESC, id DESC
            """,
            (normalized_id,),
        ).fetchall()

    result: List[Dict[str, Any]] = []
    for row_id, source_id, plugin_name, evaluated_at in rows:
        result.append(
            {
                "id": int(row_id),
                "source_id": str(source_id),
                "plugin_name": str(plugin_name),
                "evaluated_at": str(evaluated_at),
                "evaluated_at_display": format_evaluation_datetime(str(evaluated_at), language),
            }
        )
    return result


def get_stored_evaluation(cfg: Settings, evaluation_id: int) -> Optional[Dict[str, Any]]:
    """Return one stored evaluation and decoded payload."""
    if not cfg.store_evaluations:
        return None

    with sqlite3.connect(cfg.evaluations_db_path) as conn:
        row = conn.execute(
            """
            SELECT
                id,
                normalized_id,
                source_id,
                plugin_name,
                evaluated_at,
                payload_json
            FROM evaluations
            WHERE id = ?
            """,
            (evaluation_id,),
        ).fetchone()

    if not row:
        return None

    payload_json = str(row[5] or "{}")
    try:
        payload = json.loads(payload_json)
    except Exception:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    return {
        "id": int(row[0]),
        "normalized_id": str(row[1]),
        "source_id": str(row[2]),
        "plugin_name": str(row[3]),
        "evaluated_at": str(row[4]),
        "payload": payload,
    }


def _pdf_color_for_pct(pct: float):
    from reportlab.lib import colors
    if pct >= 80:
        return colors.HexColor("#2ecc71")
    if pct >= 40:
        return colors.HexColor("#f1c40f")
    return colors.HexColor("#e74c3c")


def _draw_wrapped_text(c, text: str, x: float, y: float, max_width: float, line_h: float):
    """Draw wrapped text and return the next y position."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    words = (text or "").split()
    if not words:
        return y - line_h
    line: List[str] = []
    for word in words:
        candidate = (" ".join(line + [word])).strip()
        if stringWidth(candidate, "Helvetica", 9) <= max_width:
            line.append(word)
            continue
        c.drawString(x, y, " ".join(line))
        y -= line_h
        line = [word]
    if line:
        c.drawString(x, y, " ".join(line))
        y -= line_h
    return y


def _html_to_text(value: str) -> str:
    """Convert simple HTML content into readable plain text."""
    text = value or ""
    # Block/line tags to line breaks
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</\s*li\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "- ", text)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode entities and normalize whitespace/newlines
    text = unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _draw_wrapped_text_paginated(
    c,
    text: str,
    x: float,
    y: float,
    max_width: float,
    line_h: float,
    page_h: float,
    margin: float,
    font_name: str = "Helvetica",
    font_size: float = 9.0,
):
    """Draw wrapped text across pages and return final y."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    c.setFont(font_name, font_size)
    paragraphs = (text or "").split("\n")
    for p_idx, paragraph in enumerate(paragraphs):
        words = paragraph.split()
        if not words:
            y -= line_h
            if y < (margin + line_h):
                c.showPage()
                c.setFont(font_name, font_size)
                y = page_h - margin
            continue

        line_words: List[str] = []
        for word in words:
            candidate = (" ".join(line_words + [word])).strip()
            if stringWidth(candidate, font_name, font_size) <= max_width:
                line_words.append(word)
                continue

            c.drawString(x, y, " ".join(line_words))
            y -= line_h
            if y < (margin + line_h):
                c.showPage()
                c.setFont(font_name, font_size)
                y = page_h - margin
            line_words = [word]

        if line_words:
            c.drawString(x, y, " ".join(line_words))
            y -= line_h
            if y < (margin + line_h):
                c.showPage()
                c.setFont(font_name, font_size)
                y = page_h - margin

        # Extra space between paragraphs
        if p_idx < len(paragraphs) - 1:
            y -= (line_h * 0.4)

    return y


def _wrap_text_lines(text: str, max_width: float, font_name: str, font_size: float) -> List[str]:
    """Wrap text into as many lines as needed without truncation."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    words = (text or "").split()
    if not words:
        return [""]

    lines: List[str] = []
    current: List[str] = []
    idx = 0
    for idx, word in enumerate(words):
        candidate = (" ".join(current + [word])).strip()
        if stringWidth(candidate, font_name, font_size) <= max_width:
            current.append(word)
            continue

        if current:
            lines.append(" ".join(current))
        else:
            lines.append(word)
        current = [word]

    if current:
        lines.append(" ".join(current))
    return lines


def generate_report_pdf(report_data: Dict[str, Any]) -> bytes:
    """Build an evaluation PDF report and return its bytes."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except Exception as exc:
        raise RuntimeError("PDF generation requires reportlab. Install dependency 'reportlab'.") from exc

    summary = report_data.get("summary_by_area", {}) or {}
    indicators = report_data.get("indicators_by_area", {}) or {}
    metadata_info = report_data.get("metadata", {}) or {}

    out = BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    page_w, page_h = A4
    margin = 42
    y = page_h - margin

    # Header bar
    c.setFillColor(colors.HexColor("#0b3d5c"))
    c.rect(0, page_h - 92, page_w, 92, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(margin, page_h - 42, "FAIR EVA Evaluation Report")
    c.setFont("Helvetica", 10)
    c.drawString(margin, page_h - 62, f"Resource: {metadata_info.get('resource_id', '-')}")
    c.drawString(margin, page_h - 76, f"Plugin: {metadata_info.get('plugin_name', '-')}")
    c.drawRightString(page_w - margin, page_h - 62, f"Date: {metadata_info.get('generated_at', '-')}")
    c.drawRightString(page_w - margin, page_h - 76, f"Report ID: {metadata_info.get('report_id', '-')}")

    y = page_h - 118

    # Summary bars
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Summary by FAIR area")
    y -= 18

    for area in ("Findable", "Accessible", "Interoperable", "Reusable"):
        item = summary.get(area, {}) or {}
        score = float(item.get("score", 0) or 0)
        max_score = float(item.get("max", 100) or 100)
        denom = max_score if max_score > 0 else 1
        pct = max(0.0, min(100.0, (100.0 * score) / denom))
        bar_color = _pdf_color_for_pct(pct)

        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(colors.HexColor("#1f2937"))
        c.drawString(margin, y, area)
        c.setFont("Helvetica", 9)
        c.drawRightString(page_w - margin, y, f"{score:.2f} / {max_score:.2f} ({pct:.1f}%)")
        y -= 9

        c.setFillColor(colors.HexColor("#e5e7eb"))
        c.roundRect(margin, y - 6, page_w - (2 * margin), 8, 3, stroke=0, fill=1)
        c.setFillColor(bar_color)
        c.roundRect(margin, y - 6, (page_w - (2 * margin)) * (pct / 100.0), 8, 3, stroke=0, fill=1)
        y -= 16

    y -= 2
    c.setStrokeColor(colors.HexColor("#d1d5db"))
    c.line(margin, y, page_w - margin, y)
    y -= 18

    # Indicator details
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(colors.black)
    c.drawString(margin, y, "Indicators detail")
    y -= 18

    for area in ("Findable", "Accessible", "Interoperable", "Reusable"):
        tests = indicators.get(area, []) or []
        if y < 110:
            c.showPage()
            y = page_h - margin

        # Area header box for cleaner sectioning
        c.setFillColor(colors.HexColor("#f3f4f6"))
        c.roundRect(margin, y - 10, page_w - (2 * margin), 18, 4, stroke=0, fill=1)
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(colors.HexColor("#0b3d5c"))
        c.drawString(margin + 8, y - 1, f"{area} ({len(tests)} tests)")
        y -= 22

        for test in tests:
            test_id = str(test.get("id", ""))
            title = str(test.get("display_name") or test.get("title") or test_id)
            title_text = f"{test_id} - {title}"
            score = float(test.get("score", 0) or 0)
            max_score = float(test.get("max_score", 100) or 100)
            denom = max_score if max_score > 0 else 1
            pct = max(0.0, min(100.0, (100.0 * score) / denom))
            status_color = _pdf_color_for_pct(pct)
            logs = test.get("logs", []) or []
            if isinstance(logs, str):
                logs = [logs]
            logs_text = " | ".join(str(m) for m in logs[:2]) if logs else "No logs."
            tip_text = _html_to_text(str(test.get("tips") or test.get("recommendation") or "").strip())
            if tip_text and tip_text == test_id + ".tips":
                tip_text = ""

            title_font_size = 9.5
            title_line_step = 10.0
            title_lines = _wrap_text_lines(
                title_text,
                max_width=(page_w - (2 * margin) - 190),
                font_name="Helvetica-Bold",
                font_size=title_font_size,
            )
            if len(title_lines) > 4:
                title_font_size = 8.5
                title_line_step = 9.0
                title_lines = _wrap_text_lines(
                    title_text,
                    max_width=(page_w - (2 * margin) - 190),
                    font_name="Helvetica-Bold",
                    font_size=title_font_size,
                )
            extra_title_height = max(0, (len(title_lines) - 1) * title_line_step)

            # Space estimation for this card (keeps page breaks clean)
            estimated_height = 64 + extra_title_height + (16 if tip_text else 0)
            if y < (margin + estimated_height):
                c.showPage()
                y = page_h - margin

            # Card background + left status bar
            card_h = 58 + extra_title_height + (16 if tip_text else 0)
            c.setFillColor(colors.HexColor("#fafafa"))
            c.roundRect(margin, y - card_h + 10, page_w - (2 * margin), card_h, 4, stroke=0, fill=1)
            c.setFillColor(status_color)
            c.roundRect(margin, y - card_h + 10, 6, card_h, 2, stroke=0, fill=1)

            # Indicator header
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", title_font_size)
            title_y = y + 4
            for line in title_lines:
                c.drawString(margin + 12, title_y, line)
                title_y -= title_line_step

            # Score on the right
            c.setFont("Helvetica", 9)
            c.drawRightString(page_w - margin - 8, y - 6, f"{score:.2f} / {max_score:.2f} ({pct:.1f}%)")

            # Evidence block
            y = _draw_wrapped_text_paginated(
                c,
                f"Evidence: {logs_text}",
                margin + 12,
                y - (18 + extra_title_height),
                page_w - (2 * margin) - 20,
                10.5,
                page_h=page_h,
                margin=margin,
                font_name="Helvetica",
                font_size=9.0,
            )
            if tip_text:
                y = _draw_wrapped_text_paginated(
                    c,
                    f"Tip: {tip_text}",
                    margin + 12,
                    y - 1,
                    page_w - (2 * margin) - 20,
                    10.5,
                    page_h=page_h,
                    margin=margin,
                    font_name="Helvetica",
                    font_size=9.0,
                )
            y -= 10

        y -= 8

    c.save()
    return out.getvalue()

def compute_scores(data: Dict[str, Any]) -> Tuple[Dict[str, Any], float, str]:
    """Compute aggregated scores per FAIR principle and overall FAIRness.

    Parameters
    ----------
    data:
        Parsed JSON structure for a single evaluation (one PID).  It must
        contain the keys ``findable``, ``accessible``, ``interoperable`` and
        ``reusable``.  Each of those keys maps to a dictionary of indicator
        results.  Indicator entries should provide a ``points`` value and a
        nested ``score`` dictionary with a ``weight`` field.

    Returns
    -------
    tuple
        A tuple ``(principles, fair_points, fair_color)`` where ``principles``
        maps each principle (e.g. ``findable``) to a dictionary containing a
        ``result`` entry with aggregated ``points`` and ``color`` plus the
        individual indicator entries.  ``fair_points`` is the overall FAIR
        score and ``fair_color`` is a CSS colour code based on that score.
    """

    def colour_for(value: float) -> str:
        """Return a CSS colour depending on the numeric score."""
        if value >= 75:
            return "#2ECC71"  # green
        if value >= 50:
            return "#F4D03F"  # yellow
        return "#E74C3C"      # red

    principles: Dict[str, Any] = {}
    total_points: float = 0.0
    total_weight: float = 0.0
    for dim in ["findable", "accessible", "interoperable", "reusable"]:
        items = data.get(dim, {}) or {}
        points_sum: float = 0.0
        weight_sum: float = 0.0
        # Process each indicator
        processed: Dict[str, Any] = {}
        for key, test in items.items():
            if not isinstance(test, dict):
                continue
            points = float(test.get("points", 0.0) or 0.0)
            weight = float(test.get("score", {}).get("weight", 0.0) or 0.0)
            # Use existing color if provided, otherwise compute
            colour = test.get("color") or colour_for(points)
            # Messages may be provided as a list of dicts with a 'message' key
            messages = test.get("msg")
            # To avoid HTML injection, join messages safely
            if isinstance(messages, list):
                try:
                    formatted_msg = [m.get("message", "") for m in messages]
                except:
                    formatted_msg = "Problem generating message" # TODO
            else:
                formatted_msg = messages or []
            processed[key] = {
                "name": test.get("name") or key,
                "name_smart": test.get("name_smart") or test.get("name") or key,
                "points": points,
                "score": {
                    "weight": weight,
                },
                "color": colour,
                "test_status": test.get("test_status", ""),
                "msg": formatted_msg,
            }
            points_sum += points * weight
            weight_sum += weight
        # Compute aggregated result for the principle
        result_points = round(points_sum / weight_sum, 2) if weight_sum > 0 else 0.0
        result_color = colour_for(result_points)
        processed["result"] = {"points": result_points, "color": result_color}
        principles[dim] = processed
        total_points += points_sum
        total_weight += weight_sum
    fair_points = round(total_points / total_weight, 2) if total_weight > 0 else 0.0
    fair_color = colour_for(fair_points)
    return principles, fair_points, fair_color

class IdentifierForm(FlaskForm):
    item_id = StringField("Handle PID, DOI or DIGITAL.CSIC ID", validators=[DataRequired()])
    plugin = SelectField("Select plugin", choices=[], validators=[DataRequired()])
    submit = SubmitField("Evaluate")

###############################################################################
# Application factory and routes
###############################################################################

def create_app(config: Optional[Settings] = None) -> Flask:
    """Create and configure the Flask application.

    Parameters
    ----------
    config:
        Optional :class:`Settings` instance.  If omitted, default settings
        based on environment variables are used.

    Returns
    -------
    flask.Flask
        The configured Flask application.
    """

    cfg = config or Settings()
    if config is None:
        config_file = os.getenv("FAIR_EVA_CONFIG_FILE", os.path.join(os.getcwd(), "config.ini"))
        apply_ini_overrides(cfg, config_file)
    app = Flask(__name__, template_folder="templates", static_folder="static")
     # Trust reverse-proxy headers (including URL prefix) for URL generation.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config.update(
    {
        "SECRET_KEY": "sdafasfwefq3egthyjtyhwef",
        "TESTING": True,
        "DEBUG": True,
        "FLASK_DEBUG": 1,
        "PATHS": ["about_us", "evaluator", "export_pdf", "evaluations"],
        "TITLE": cfg.title,
        "LOGO_URL": cfg.logo_url,
        "LOGO_IMAGE": cfg.logo_image,
        "EVALUATIONS_ENABLED": cfg.store_evaluations,
        "EVALUATIONS_DB_PATH": cfg.evaluations_db_path,
        "BABEL_DEFAULT_LOCALE": "es",
        "BABEL_LOCALES": [
            "en",
            "en-CA",
            "en-IE",
            "en-GB",
            "en-US",
            "es",
            "es-ES",
            "es-MX",
        ],
    }
)
    babel = Babel(app)
    app.secret_key = "super-secret"  # Necesario para CSRF en Flask-WTF

    if cfg.store_evaluations:
        try:
            init_evaluations_db(cfg)
        except Exception:
            cfg.store_evaluations = False
            app.config["EVALUATIONS_ENABLED"] = False


    @app.before_request
    def get_global_language():
        g.babel = babel
        success_message = _l("Test")
        g.language = get_locale()
        g.repository_label = resolve_repository_label(
            (request.values.get("plugin") or "").strip()
        )


    def get_locale():
        lang = request.path[1:].split("/", 1)[0]

        if lang in app.config["BABEL_LOCALES"]:
            session["lang"] = lang
            return lang

        if lang_in_session():
            return session.get("lang")

        default_lang = fallback_lang()
        session["lang"] = default_lang
        return default_lang


    babel.init_app(app, locale_selector=get_locale)


    def lang_in_session():
        return (
            session.get("lang") is not None
            and session.get("lang") in app.config["BABEL_LOCALES"]
        )


    def fallback_lang():
        best_match = request.accept_languages.best_match(app.config["BABEL_LOCALES"])

        if best_match is None:
            return app.config["BABEL_DEFAULT_LOCALE"]

        if "en" in best_match:
            return "en"

        return "es"


    @app.route("/", defaults={"path": ""}, methods=["GET", "POST"])
    @app.route("/<path:path>", methods=["GET", "POST"])
    def catch_all(path):
        # Normalize trailing slashes so "/es/" behaves like "/es".
        normalized_path = path.strip("/")
        if normalized_path == "":
            return redirect(url_for("home_" + g.language))
        subpaths = [segment for segment in normalized_path.split("/") if segment]
        if not subpaths:
            return redirect(url_for("home_" + g.language))
        if len(subpaths) > 2:
            subpaths.pop(0)
        if subpaths[0] in app.config["BABEL_LOCALES"]:
            if len(subpaths) > 1:
                if subpaths[1] in app.config["PATHS"]:
                    return redirect(
                        url_for(subpaths[1] + "_" + subpaths[0], **request.args)
                    )
                else:
                    return redirect(url_for("not-found_" + subpaths[0]))
            else:
                return redirect(url_for("home_" + subpaths[0]))
        else:
            if subpaths[0] in app.config["PATHS"]:
                return redirect(url_for(
                    subpaths[0] + "_" + g.language,
                    item_id=request.values.get("item_id"),
                    plugin=request.values.get("plugin"),
                ))
            else:
                if len(subpaths) > 1:
                    if subpaths[1] in app.config["PATHS"]:
                        return redirect(
                            url_for(subpaths[1] + "_" + g.language, **request.args)
                        )
                return redirect(url_for("not-found_" + g.language))

    LOCAL_AVAILABLE_PLUGIN_ENTRIES = _extract_plugin_entries([])
    if cfg.plugins_file:
        try:
            with open(cfg.plugins_file, "r", encoding="utf-8") as fh:
                LOCAL_AVAILABLE_PLUGIN_ENTRIES = _extract_plugin_entries(json.load(fh))
        except Exception:
            LOCAL_AVAILABLE_PLUGIN_ENTRIES = []
    LOCAL_AVAILABLE_PLUGINS = plugin_choices_from_entries(LOCAL_AVAILABLE_PLUGIN_ENTRIES)
    HAS_CONFIGURED_PLUGIN_LIST = bool(LOCAL_AVAILABLE_PLUGINS)
    if not LOCAL_AVAILABLE_PLUGINS:
        LOCAL_AVAILABLE_PLUGIN_ENTRIES = [
            {
                "id": "signposting",
                "label": "Signposting (Zenodo/CSIC)",
                "repository_label": "Signposting (Zenodo/CSIC)",
            },
            {
                "id": "oai_pmh",
                "label": "OAI-PMH",
                "repository_label": "OAI-PMH",
            },
            {
                "id": "ai4os",
                "label": "AI4EOSC Plugin",
                "repository_label": "AI4EOSC Plugin",
            },
        ]
        LOCAL_AVAILABLE_PLUGINS = plugin_choices_from_entries(LOCAL_AVAILABLE_PLUGIN_ENTRIES)
    app.config["PLUGIN_METADATA"] = plugin_metadata_from_entries(LOCAL_AVAILABLE_PLUGIN_ENTRIES)
    app.config.setdefault("SECRET_KEY", "dev-change-me")       # Necesario para CSRF de Flask-WTF
    app.config.setdefault("WTF_CSRF_ENABLED", True)
    app.config.setdefault("BABEL_DEFAULT_LOCALE", "en")

    # Inject configuration values into templates via context processor
    try:
        babel = Babel(app)

        # Si usas selector de locale:
        # @babel.localeselector
        # def get_locale():
        #     return request.accept_languages.best_match(["en", "es"])
        app.jinja_env.globals["_"] = translated_gettext
    except Exception:
        # Fallback por si no quieres Babel en dev
        app.jinja_env.globals["_"] = lambda s, *args, **kwargs: s

    @app.route("/es", endpoint="home_es")
    @app.route("/en", endpoint="home_en")
    def index():
        form = IdentifierForm()
        plugin_choices = LOCAL_AVAILABLE_PLUGINS
        plugins_source = "local"
        plugins_notice = ""

        # If plugins are already configured locally, skip API discovery.
        if not cfg.dev_mode and not HAS_CONFIGURED_PLUGIN_LIST:
            api_plugins = fetch_plugins_from_api(
                build_default_eval_endpoint(cfg),
                plugins_endpoint=cfg.api_plugins_path,
            )
            if api_plugins:
                plugin_choices = plugin_choices_from_entries(api_plugins)
                app.config["PLUGIN_METADATA"].update(plugin_metadata_from_entries(api_plugins))
                plugins_source = "api"
            else:
                plugins_notice = (
                    "Could not load plugins from API endpoint. "
                    "Using local plugin list."
                )
        form.plugin.choices = plugin_choices

        if form.validate_on_submit():
            item_id = form.item_id.data.strip()
            plugin = form.plugin.data
            return redirect(url_for(f"evaluator_{g.language}", item_id=item_id, plugin=plugin))
        return render_template(
            "index.html",
            form=form,
            plugins_source=plugins_source,
            plugins_notice=plugins_notice,
        )

    @app.route("/es/evaluator", endpoint="evaluator_es", methods=["GET", "POST"])
    @app.route("/en/evaluator", endpoint="evaluator_en", methods=["GET", "POST"])
    def evaluator():
        app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"
        babel.init_app(app, locale_selector=get_locale)
        """Perform an evaluation and render the results page (compatible legacy + modern UI)."""
        # Accept both query params (GET) and form body (POST from index form).
        item_id = (request.values.get("item_id") or "").strip()
        plugin = (request.values.get("plugin") or "").strip()
        repo = plugin
        oai_base = ""
        g.repository_label = resolve_repository_label(plugin)

        if not item_id:
            return redirect(url_for("home_"+ g.language))
        
        # ------------------------------
        # 1) plugin translation TODO
        # ------------------------------
        
        if os.path.exists("plugins/%s/translations" % plugin):
            app.config["BABEL_TRANSLATION_DIRECTORIES"] = (
                "plugins/%s/translations" % repo
            )
            babel.init_app(app, locale_selector=get_locale)

        # ------------------------------
        # 1) Carga datos (dev o API)
        # ------------------------------
        result_data: Optional[Dict[str, Any]] = None
        raw_response: Dict[str, Any] = {}
        endpoint = build_default_eval_endpoint(cfg)

        try:
            if cfg.dev_mode:
                with open(cfg.sample_file, "r", encoding="utf-8") as f:
                    json_data = json.load(f)
                raw_response = json_data if isinstance(json_data, dict) else {}
                # Selecciona la primera clave que no sea evaluator_logs
                for k, v in json_data.items():
                    if k != "evaluator_logs":
                        result_data = v
                        break
            else:
                payload: Dict[str, Any] = {"id": item_id, "repo": repo, "lang": g.language}
                resp = requests.post(endpoint, json=payload, timeout=cfg.api_timeout)
                resp.raise_for_status()
                resp_json = resp.json()
                raw_response = resp_json if isinstance(resp_json, dict) else {}
                for k, v in resp_json.items():
                    if k != "evaluator_logs":
                        result_data = v
                        break
        except Exception as exc:
            return render_template("error.html", error=build_api_error_message(exc, endpoint))

        if not result_data:
            return render_template(
                "error.html",
                error=(
                    "The FAIR EVA API returned no evaluation data for this request. "
                    "Please verify the identifier and selected plugin."
                ),
            )

        # --------------------------------
        # 2) Puntuaciones (tu función)
        # --------------------------------
        # compute_scores debe devolver:
        #   - principles: dict por cada grupo con 'result' y los tests/indicators
        #   - fair_points: puntuación FAIR global
        #   - fair_color: color global
        principles, fair_points, fair_color = compute_scores(result_data)

        # --------------------------------
        # 3) Compat: variables originales
        # --------------------------------
        aggregated = {
            "findable": principles["findable"]["result"]["points"],
            "accessible": principles["accessible"]["result"]["points"],
            "interoperable": principles["interoperable"]["result"]["points"],
            "reusable": principles["reusable"]["result"]["points"],
            "fair": fair_points,
        }
        colours = {
            "findable": principles["findable"]["result"].get("color"),
            "accessible": principles["accessible"]["result"].get("color"),
            "interoperable": principles["interoperable"]["result"].get("color"),
            "reusable": principles["reusable"]["result"].get("color"),
            "fair": fair_color,
        }

        # ---------------------------------------------------------
        # 4) Datos para el template moderno (resumen + indicadores)
        # ---------------------------------------------------------
        def _max_for_group(group: Dict[str, Any]) -> int:
            """
            Intenta deducir el máximo de puntos por grupo.
            Preferencias:
            - group['result']['max_points']
            - suma de cada test['result']['max_points']
            - fallback 100
            """
            res = group.get("result", {})
            if "max_points" in res and isinstance(res["max_points"], (int, float)):
                return int(res["max_points"])
            tests = group.get("indicators") or group.get("tests") or {}
            total = 0
            # tests puede ser dict o lista según la salida
            if isinstance(tests, dict):
                iters = tests.values()
            else:
                iters = tests
            for t in iters:
                t_res = (t or {}).get("result", {})
                if isinstance(t_res.get("max_points"), (int, float)):
                    total += int(t_res["max_points"])
                elif isinstance(t.get("max_points"), (int, float)):
                    total += int(t["max_points"])
                else:
                    # si no hay info, contamos 1 como mínimo
                    total += 1
            return total or 100

        def _tests_list(group: Dict[str, Any]) -> List[Dict[str, Any]]:
            """
            Normaliza los tests/indicadores para el acordeón moderno.
            Estructura de salida por test:
            id, title, description, score, max_score, priority, logs(list), recommendation, details(dict opcional)
            """
            items: List[Dict[str, Any]] = []
            for key in group:
                if key != "result":
                    rid = group[key]['name']
                    title = group[key]['name']
                    desc = group[key]['name']
                    tres = group[key]['score']
                    score = group[key]["points"]
                    max_s = 100
                    #TODO Check priority
                    priority = "optional"
                    if group[key]['score']['weight'] == 20:
                        priority = "essential" #TODO
                    elif group[key]['score']['weight'] > 10:
                        priority = "important"
                    # logs/feedback pueden venir como string o lista
                    logs_raw = group[key]['msg']
                    recommendation = translated_gettext(f"{rid}.tips")
                    details = "details"
                    display_name = translated_gettext(rid)
                    items.append({
                        "id": rid,
                        "title": title,
                        "display_name": display_name,
                        "description": desc,
                        "score": score,
                        "max_score": max_s,
                        "priority": priority,
                        "logs": logs_raw,
                        "recommendation": recommendation,
                        "tips": recommendation,
                        "details": details,
                    })
            return items

        # Mapea a nombres con mayúscula para el gráfico/tarjetas
        p_find = principles.get("findable", {})
        p_acc  = principles.get("accessible", {})
        p_int  = principles.get("interoperable", {})
        p_reu  = principles.get("reusable", {})

        summary_by_area = {
            "Findable": {
                "score": int(p_find.get("result", {}).get("points", 0)),
                "max": _max_for_group(p_find),
            },
            "Accessible": {
                "score": int(p_acc.get("result", {}).get("points", 0)),
                "max": _max_for_group(p_acc),
            },
            "Interoperable": {
                "score": int(p_int.get("result", {}).get("points", 0)),
                "max": _max_for_group(p_int),
            },
            "Reusable": {
                "score": int(p_reu.get("result", {}).get("points", 0)),
                "max": _max_for_group(p_reu),
            },
        }

        indicators_by_area = {
            "Findable": _tests_list(p_find),
            "Accessible": _tests_list(p_acc),
            "Interoperable": _tests_list(p_int),
            "Reusable": _tests_list(p_reu),
        }

        # Nombre del plugin/identificador para cabecera del eval moderno
        plugin_name = plugin or "default"
        resource_id = item_id
        evaluated_at = _utc_now_iso()
        report_payload: Dict[str, Any] = {
            "metadata": {
                "resource_id": resource_id,
                "plugin_name": plugin_name,
                "generated_at": evaluated_at,
                "language": g.language,
                "endpoint": endpoint,
            },
            "raw_response": raw_response,
            "summary_by_area": summary_by_area,
            "indicators_by_area": indicators_by_area,
        }
        report_id = uuid.uuid4().hex
        report_payload["metadata"]["report_id"] = report_id
        save_evaluation_report(cfg, report_payload, report_id=report_id)
        if cfg.store_evaluations:
            try:
                save_evaluation_record(
                    cfg=cfg,
                    source_id=resource_id,
                    plugin_name=plugin_name,
                    evaluated_at=evaluated_at,
                    payload=report_payload,
                )
            except Exception:
                pass
        download_url = url_for(f"download_json_{g.language}", report_id=report_id)
        pdf_url = url_for(f"export_pdf_{g.language}", report_id=report_id)

        # --------------------------------
        # 5) Render (legacy + moderno)
        # --------------------------------
        # Mantengo variables legacy para tus templates antiguos
        # y añado las modernas para el nuevo eval.html.
        return render_template(
            "eval.html",
            # --- legacy ---
            item_id=item_id,
            principles=principles,
            aggregated=aggregated,
            colours=colours,
            result_points=fair_points,
            result_color=fair_color,
            script="",
            div="",
            script_f="",
            div_f="",
            data_test=None,
            # --- moderno ---
            resource_id=resource_id,
            original_resource_id=None,
            plugin_name=plugin_name,
            now=format_evaluation_datetime(evaluated_at, g.language),
            summary_by_area=summary_by_area,
            indicators_by_area=indicators_by_area,
            chart_kind="radar",
            download_url=download_url,
            pdf_url=pdf_url,
            evaluation_source_label=None,
        )

    @app.route("/es/download-json/<report_id>", endpoint="download_json_es")
    @app.route("/en/download-json/<report_id>", endpoint="download_json_en")
    def download_json(report_id: str) -> Response:
        report_data = load_evaluation_report(cfg, report_id)
        if not report_data:
            return render_template("error.html", error="Requested report was not found.")
        raw = report_data.get("raw_response", {}) or {}
        body = json.dumps(raw, ensure_ascii=False, indent=2)
        response = make_response(body)
        response.headers["Content-Type"] = "application/json; charset=utf-8"
        response.headers["Content-Disposition"] = f'attachment; filename="fair_eva_report_{report_id}.json"'
        return response

    @app.route("/es/export_pdf/<report_id>", endpoint="export_pdf_es")
    @app.route("/en/export_pdf/<report_id>", endpoint="export_pdf_en")
    def export_pdf(report_id: str) -> Response:
        report_data = load_evaluation_report(cfg, report_id)
        if not report_data:
            return render_template("error.html", error="Requested report was not found.")
        try:
            pdf_bytes = generate_report_pdf(report_data)
        except Exception as exc:
            return render_template("error.html", error=f"Could not generate PDF report: {exc}")
        response = make_response(pdf_bytes)
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = f'attachment; filename="fair_eva_report_{report_id}.pdf"'
        return response

    @app.route("/es/evaluations", endpoint="evaluations_es")
    @app.route("/en/evaluations", endpoint="evaluations_en")
    def evaluations():
        if not cfg.store_evaluations:
            return redirect(url_for("home_" + g.language))

        selected_identifier = (request.args.get("identifier") or "").strip()
        selected_run_raw = (request.args.get("run_id") or "").strip()

        identifiers = list_stored_identifiers(cfg, g.language)
        available_identifiers = [entry["normalized_id"] for entry in identifiers]

        if not selected_identifier and available_identifiers:
            selected_identifier = available_identifiers[0]
        if selected_identifier and selected_identifier not in available_identifiers:
            selected_identifier = ""

        runs = list_evaluation_runs(cfg, selected_identifier, g.language) if selected_identifier else []

        selected_run_id: Optional[int] = None
        if selected_run_raw.isdigit():
            selected_run_id = int(selected_run_raw)
        elif runs:
            selected_run_id = runs[0]["id"]

        run_ids = {entry["id"] for entry in runs}
        if selected_run_id is not None and selected_run_id not in run_ids:
            selected_run_id = None

        evaluation_view_url = None
        if selected_run_id is not None:
            evaluation_view_url = url_for(
                f"evaluation_record_{g.language}",
                evaluation_id=selected_run_id,
            )

        return render_template(
            "evaluations.html",
            identifiers=identifiers,
            runs=runs,
            selected_identifier=selected_identifier,
            selected_run_id=selected_run_id,
            evaluation_view_url=evaluation_view_url,
            storage_path=cfg.evaluations_db_path,
            backups_notice=_ui_text(
                g.language,
                "La copia de seguridad de esta base de datos es responsabilidad del administrador del sistema.",
                "Backing up this database is the system administrator's responsibility.",
            ),
        )

    @app.route("/es/evaluations/view/<int:evaluation_id>", endpoint="evaluation_record_es")
    @app.route("/en/evaluations/view/<int:evaluation_id>", endpoint="evaluation_record_en")
    def evaluation_record(evaluation_id: int):
        if not cfg.store_evaluations:
            return redirect(url_for("home_" + g.language))

        stored = get_stored_evaluation(cfg, evaluation_id)
        if not stored:
            return render_template("error.html", error="Requested evaluation was not found.")

        payload = stored.get("payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        metadata_info = payload.get("metadata", {}) or {}
        if not isinstance(metadata_info, dict):
            metadata_info = {}
        g.repository_label = resolve_repository_label(stored.get("plugin_name", ""))

        report_id = metadata_info.get("report_id")
        download_url = None
        pdf_url = None
        if isinstance(report_id, str) and _safe_report_id(report_id):
            download_url = url_for(f"download_json_{g.language}", report_id=report_id)
            pdf_url = url_for(f"export_pdf_{g.language}", report_id=report_id)

        summary_by_area = payload.get("summary_by_area", {}) or {}
        indicators_by_area = payload.get("indicators_by_area", {}) or {}
        if not isinstance(summary_by_area, dict):
            summary_by_area = {}
        if not isinstance(indicators_by_area, dict):
            indicators_by_area = {}

        return render_template(
            "eval.html",
            item_id=stored.get("source_id", ""),
            principles={},
            aggregated={},
            colours={},
            result_points=0,
            result_color="#6c757d",
            script="",
            div="",
            script_f="",
            div_f="",
            data_test=None,
            resource_id=stored.get("normalized_id", stored.get("source_id", "")),
            original_resource_id=stored.get("source_id", ""),
            plugin_name=stored.get("plugin_name", metadata_info.get("plugin_name", "default")),
            now=format_evaluation_datetime(stored.get("evaluated_at", ""), g.language),
            summary_by_area=summary_by_area,
            indicators_by_area=indicators_by_area,
            chart_kind="radar",
            download_url=download_url,
            pdf_url=pdf_url,
            evaluation_source_label=_ui_text(
                g.language,
                "Evaluación recuperada de la base de datos local.",
                "Evaluation loaded from the local database.",
            ),
        )


    @app.route("/es/not-found", endpoint="not-found_es")
    @app.route("/en/not-found", endpoint="not-found_en")
    def not_found():
        return render_template("not-found.html")


    @app.route("/es/faq", endpoint="faq_es")
    @app.route("/en/faq", endpoint="faq_en")
    def faq():
        return render_template("faq.html")


    @app.route("/es/about_us", endpoint="about_us_es")
    @app.route("/en/about_us", endpoint="about_us_en")
    def about_us():
        if session.get("lang") == "es":
            return render_template("acerca_de.html")
        else:
            return render_template("about_us.html")
        
    @app.route("/error")
    def error() -> str:
        """Simple error page."""
        return render_template("error.html", error="An unexpected error occurred.")

    return app


###############################################################################
# Command line entry point
###############################################################################

def main() -> None:
    """Parse command line arguments and run the Flask development server."""
    import argparse

    parser = argparse.ArgumentParser(description="Run the FAIR EVA web client")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port for the web server")
    parser.add_argument(
        "--config-file",
        default=os.getenv("FAIR_EVA_CONFIG_FILE", "config.ini"),
        help="Path to config.ini with FAIR EVA settings",
    )
    parser.add_argument("--api-url", default=None, help="Base URL of the FAIR EVA API")
    parser.add_argument("--api-port", type=int, default=None, help="Port of the FAIR EVA API")
    parser.add_argument(
        "--api-timeout",
        type=int,
        default=None,
        help="Timeout in seconds for FAIR EVA API requests",
    )
    parser.add_argument("--title", default=None, help="Page title")
    parser.add_argument("--logo-url", default=None, help="URL to link the logo")
    parser.add_argument("--logo-image", default=None, help="Logo image file in static/img")
    parser.add_argument("--dev", action="store_true", help="Enable development mode (load JSON file)")
    parser.add_argument("--sample-file", default=None, help="Path to sample JSON file for --dev")
    parser.add_argument(
        "--store-evaluations",
        action="store_true",
        help="Store evaluation payloads in a local SQLite database",
    )
    parser.add_argument(
        "--evaluations-db-path",
        default=None,
        help="Path to the SQLite database for stored evaluations",
    )
    args = parser.parse_args()

    # Build settings based on defaults and overrides
    cfg = Settings()
    apply_ini_overrides(cfg, args.config_file)
    if args.api_url:
        cfg.api_url = args.api_url
    if args.api_port:
        cfg.api_port = args.api_port
    if args.api_timeout is not None:
        cfg.api_timeout = args.api_timeout
    if args.title:
        cfg.title = args.title
    if args.logo_url:
        cfg.logo_url = args.logo_url
    if args.logo_image:
        cfg.logo_image = args.logo_image
    if args.dev:
        cfg.dev_mode = True
    if args.sample_file:
        cfg.sample_file = args.sample_file
    if args.store_evaluations:
        cfg.store_evaluations = True
    if args.evaluations_db_path:
        cfg.evaluations_db_path = args.evaluations_db_path
    app = create_app(cfg)
    app.run(host=args.host, port=args.port, debug=cfg.dev_mode)
