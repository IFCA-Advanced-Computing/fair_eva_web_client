"""Flask application for the FAIR EVA web client.

This module implements a lightweight user interface for submitting a
persistent identifier to the FAIR EVA API and visualising the results.
It supports a development mode where evaluation data is loaded from a
local JSON file instead of calling the API.  Configuration values
(such as the API URL, page title and logo) can be provided via
environment variables or command‑line flags.
"""

from __future__ import annotations
from datetime import datetime
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, List

from flask import Flask, render_template, redirect, url_for, request
# forms.py (o dentro de app.py si prefieres no separar)

from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, SubmitField
from wtforms.validators import DataRequired



import requests

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
    api_port: int = int(os.getenv("FAIR_EVA_API_PORT", "9090"))
    title: str = os.getenv("FAIR_EVA_TITLE", "FAIR EVA")
    logo_url: str = os.getenv("FAIR_EVA_LOGO_URL", "https://digital.csic.es")
    logo_image: str = os.getenv("FAIR_EVA_LOGO_IMAGE", "logo_fair_eosc.png")
    dev_mode: bool = os.getenv("FAIR_EVA_DEV", "0") == "1"
    sample_file: str = os.getenv(
        "FAIR_EVA_SAMPLE_FILE",
        os.path.join(os.path.dirname(__file__), "data", "salida_new.json"),
    )


###############################################################################
# Utility functions
###############################################################################

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
                formatted_msg = [m.get("message", "") for m in messages]
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
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(
    {
        "SECRET_KEY": "sdafasfwefq3egthyjtyhwef",
        "TESTING": True,
        "DEBUG": True,
        "FLASK_DEBUG": 1,
        "PATHS": ["about_us", "evaluator", "export_pdf", "evaluations"],
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
    app.secret_key = "super-secret"  # Necesario para CSRF en Flask-WTF

    # Simulación de plugins activos (esto lo puedes cambiar por la lectura real desde config)
    AVAILABLE_PLUGINS = [
        ("signposting", "Signposting (Zenodo/CSIC)"),
        ("oai_pmh", "OAI-PMH"),
        ("custom_plugin", "Custom Plugin"),
    ]
    app.config.setdefault("SECRET_KEY", "dev-change-me")       # Necesario para CSRF de Flask-WTF
    app.config.setdefault("WTF_CSRF_ENABLED", True)
    app.config.setdefault("BABEL_DEFAULT_LOCALE", "en")

    # Inject configuration values into templates via context processor
    try:
        from flask_babel import Babel, _
        babel = Babel(app)

        # Si usas selector de locale:
        # @babel.localeselector
        # def get_locale():
        #     return request.accept_languages.best_match(["en", "es"])
        app.jinja_env.globals["_"] = _
    except Exception:
        # Fallback por si no quieres Babel en dev
        app.jinja_env.globals["_"] = lambda s: s

    @app.route("/", methods=["GET", "POST"])
    def index():
        
        form = IdentifierForm()
        form.plugin.choices = AVAILABLE_PLUGINS
        


        if form.validate_on_submit():
            item_id = form.item_id.data.strip()
            plugin = form.plugin.data
            return redirect(url_for("evaluate", item_id=item_id, plugin=plugin))

        return render_template("index.html", form=form)

    @app.route("/evaluate", methods=["GET", "POST"])# añade este import arriba del fichero

    @app.route("/evaluator", methods=["GET", "POST"])
    def evaluator():
        """Perform an evaluation and render the results page (compatible legacy + modern UI)."""
        # Acepta tanto GET como POST (request.values cubre ambos)
        item_id = (request.values.get("item_id") or "").strip()
        plugin = (request.values.get("plugin") or "").strip()
        repo = (request.values.get("repo") or "").strip()
        oai_base = (request.values.get("oai_base") or "").strip()

        if not item_id:
            return redirect(url_for("index"))

        # ------------------------------
        # 1) Carga datos (dev o API)
        # ------------------------------
        result_data: Optional[Dict[str, Any]] = None

        # TO CHANGE si quieres que venga de config/env
        cfg.dev_mode = True  # <-- respeta tu comentario, fuerza dev para pruebas
        try:
            if cfg.dev_mode:
                with open(cfg.sample_file, "r", encoding="utf-8") as f:
                    json_data = json.load(f)
                # Selecciona la primera clave que no sea evaluator_logs
                for k, v in json_data.items():
                    if k != "evaluator_logs":
                        result_data = v
                        break
            else:
                base = cfg.api_url.rstrip("/") + f":{cfg.api_port}"
                # Ajusta el endpoint real si es distinto:
                endpoint = f"{base}/v1.0/rda/rda_all"
                payload: Dict[str, Any] = {"id": item_id, "lang": "en"}
                resp = requests.post(endpoint, json=payload, timeout=30)
                resp.raise_for_status()
                resp_json = resp.json()
                for k, v in resp_json.items():
                    if k != "evaluator_logs":
                        result_data = v
                        break
        except Exception as exc:
            return render_template("error.html", error=f"Error loading evaluation data: {exc}")

        if not result_data:
            return render_template("error.html", error="No evaluation data returned.")

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
                    priority = "essential" #TODO
                    # logs/feedback pueden venir como string o lista
                    logs_raw = group[key]['msg']
                    recommendation = "RECommendations"
                    details = "details"
                    items.append({
                        "id": rid,
                        "title": title,
                        "description": desc,
                        "score": score,
                        "max_score": max_s,
                        "priority": priority,
                        "logs": "logs",
                        "recommendation": recommendation,
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
            plugin_name=plugin_name,
            now=datetime.utcnow().isoformat(timespec="seconds") + "Z",
            summary_by_area=summary_by_area,
            indicators_by_area=indicators_by_area,
            chart_kind="radar",
            download_url=None,  # si ofreces JSON descargable, pon aquí la URL
        )


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
    parser.add_argument("--api-url", default=None, help="Base URL of the FAIR EVA API")
    parser.add_argument("--api-port", type=int, default=None, help="Port of the FAIR EVA API")
    parser.add_argument("--title", default=None, help="Page title")
    parser.add_argument("--logo-url", default=None, help="URL to link the logo")
    parser.add_argument("--logo-image", default=None, help="Logo image file in static/img")
    parser.add_argument("--dev", action="store_true", help="Enable development mode (load JSON file)")
    parser.add_argument("--sample-file", default=None, help="Path to sample JSON file for --dev")
    args = parser.parse_args()

    # Build settings based on defaults and overrides
    cfg = Settings()
    if args.api_url:
        cfg.api_url = args.api_url
    if args.api_port:
        cfg.api_port = args.api_port
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
    app = create_app(cfg)
    app.run(host=args.host, port=args.port, debug=cfg.dev_mode)