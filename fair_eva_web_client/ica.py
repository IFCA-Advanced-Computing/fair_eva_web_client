"""Adapt data_01's versioned HTML envelope (and legacy tables) into safe UI data."""

from html.parser import HTMLParser
import json
import math
import re


DIMENSIONS = (
    (
        "Taxonomic",
        45,
        "#147d92",
        ("Genus", "Species", "Kingdom", "Class/Order", "Family", "Identifiers"),
    ),
    (
        "Geographic",
        35,
        "#3977c6",
        ("Coordinates", "Countries", "CoordinatesUncertainty", "IncorrectCoordinates"),
    ),
    ("Temporal", 20, "#8061b7", ("Years", "Months", "Days", "IncorrectDates")),
)
WEIGHTS = dict(
    Genus=20,
    Species=10,
    Kingdom=3,
    Family=3,
    Identifiers=6,
    Coordinates=20,
    Countries=10,
    CoordinatesUncertainty=5,
    IncorrectCoordinates=-20,
    Years=11,
    Months=7,
    Days=2,
    IncorrectDates=-15,
)
WEIGHTS["Class/Order"] = 3
LABELS = {
    "es": {
        "ICA": "Índice de Calidad Aparente",
        "Taxonomic": "Taxonómica",
        "Geographic": "Geográfica",
        "Temporal": "Temporal",
        "Genus": "Géneros en Catalogue of Life",
        "Species": "Epíteto específico presente",
        "Kingdom": "Jerarquía: reino",
        "Class/Order": "Jerarquía: clase u orden",
        "Family": "Jerarquía: familia",
        "Identifiers": "Responsable de identificación",
        "Coordinates": "Coordenadas presentes",
        "Countries": "Código de país válido",
        "CoordinatesUncertainty": "Incertidumbre positiva",
        "IncorrectCoordinates": "Coordenadas incorrectas",
        "Years": "Años válidos",
        "Months": "Meses válidos",
        "Days": "Días válidos",
        "IncorrectDates": "Fechas incorrectas",
        "subtitle": "Completitud y validez aparente de los registros del conjunto de datos.",
        "total": "Puntuación global",
        "points": "puntos",
        "contribution": "Contribución al ICA",
        "checks": "Ver comprobaciones",
        "penalties": "Penalizaciones incluidas",
        "penalty": "Penalización",
        "source": "Referencia taxonómica",
        "local": "Validación local · coincidencia exacta de géneros",
        "legacy": "Resultados de una versión anterior del plugin",
        "unknown": "No disponible",
        "method": "Cómo se calcula",
        "method_text": "El ICA suma las contribuciones taxonómica (45 puntos), geográfica (35) y temporal (20). Las penalizaciones ya están descontadas. Los porcentajes de cada dimensión se calculan respecto a su propio máximo.",
        "hierarchy": "higherClassification completo aporta los tres créditos de jerarquía. En su ausencia se evalúan reino, clase u orden y familia.",
        "negative": "El ICA puede ser negativo cuando las penalizaciones superan los puntos obtenidos. Se conserva el valor real.",
        "good": "≥ 80 % del máximo",
        "partial": "40–79 % del máximo",
        "low": "< 40 % del máximo",
        "coverage": "del máximo de la dimensión",
        "weight": "Peso",
        "no_source": "Versión del catálogo no informada",
        "separate": "La calidad aparente de los datos se evalúa por separado de los principios FAIR.",
    },
    "en": {
        "ICA": "Apparent Quality Index",
        "Taxonomic": "Taxonomic",
        "Geographic": "Geographic",
        "Temporal": "Temporal",
        "Genus": "Genera in Catalogue of Life",
        "Species": "Specific epithet present",
        "Kingdom": "Hierarchy: kingdom",
        "Class/Order": "Hierarchy: class or order",
        "Family": "Hierarchy: family",
        "Identifiers": "Identification attributed",
        "Coordinates": "Coordinates present",
        "Countries": "Valid country code",
        "CoordinatesUncertainty": "Positive uncertainty",
        "IncorrectCoordinates": "Incorrect coordinates",
        "Years": "Valid years",
        "Months": "Valid months",
        "Days": "Valid days",
        "IncorrectDates": "Incorrect dates",
        "subtitle": "Apparent completeness and validity of the dataset records.",
        "total": "Overall score",
        "points": "points",
        "contribution": "Contribution to ICA",
        "checks": "View checks",
        "penalties": "Penalties included",
        "penalty": "Penalty",
        "source": "Taxonomic reference",
        "local": "Local validation · exact genus matching",
        "legacy": "Results from an earlier plugin version",
        "unknown": "Not available",
        "method": "How it is calculated",
        "method_text": "ICA adds the taxonomic (45 points), geographic (35) and temporal (20) contributions. Penalties have already been deducted. Each dimension percentage is relative to its own maximum.",
        "hierarchy": "A populated higherClassification grants all three hierarchy credits. Otherwise kingdom, class or order, and family are evaluated.",
        "negative": "ICA can be negative when penalties exceed earned points. The actual score is retained.",
        "good": "≥ 80% of maximum",
        "partial": "40–79% of maximum",
        "low": "< 40% of maximum",
        "coverage": "of the dimension maximum",
        "weight": "Weight",
        "no_source": "Catalogue version not reported",
        "separate": "Apparent data quality is evaluated separately from the FAIR principles.",
    },
}


def messages(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from messages(item)
    elif isinstance(value, dict):
        yield from messages(value.get("message", value.get("msg")))


class ICAParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.payload = None
        self.rows = []
        self.row = []
        self.cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("data-ica-version") == "1" and attrs.get("data-ica"):
            try:
                data = json.loads(attrs["data-ica"])
                if isinstance(data, dict):
                    self.payload = data
            except (TypeError, ValueError):
                pass
        if tag == "tr":
            self.row = []
        if tag in ("td", "th"):
            self.cell = []

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and len(self.row) >= 2:
            self.rows.append(self.row)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def status(pct):
    return "good" if pct >= 80 else "partial" if pct >= 40 else "low"


def build_ica(test, language="en"):
    parser = ICAParser()
    for message in messages(test.get("msg")):
        parser.feed(message)
    scores = parser.payload
    legacy = scores is None
    if legacy:
        aliases = {
            re.sub(r"\s", "", k).lower(): k
            for k in list(WEIGHTS) + ["ICA", "Taxonomic", "Geographic", "Temporal"]
        }
        aliases["icatotal"] = "ICA"
        scores = {}
        for label, value, *_ in parser.rows:
            key = aliases.get(re.sub(r"\s", "", label).lower())
            if key:
                scores[key] = number(value.replace("%", "").replace(",", "."))
        if "ICA" not in scores:
            return None
    required = ("ICA", "Taxonomic", "Geographic", "Temporal")
    if any(number(scores.get(key)) is None for key in required):
        return None
    t = LABELS["es" if language.startswith("es") else "en"]
    dimensions = []
    penalties = 0
    penalties_complete = True
    for key, maximum, color, components in DIMENSIONS:
        score = number(scores[key])
        pct = 100 * score / maximum
        checks = []
        for component in components:
            points = number(scores.get(component))
            weight = WEIGHTS[component]
            if weight < 0 and points is None:
                penalties_complete = False
            # Older GBIF versions reported IncorrectDates as a positive magnitude.
            if points is not None and weight < 0:
                points = -abs(points)
                penalties += points
            checks.append(
                dict(
                    key=component,
                    label=t[component],
                    score=points,
                    maximum=abs(weight),
                    penalty=weight < 0,
                    width=(
                        0
                        if points is None
                        else min(100, max(0, abs(points) * 100 / abs(weight)))
                    ),
                )
            )
        dimensions.append(
            dict(
                key=key,
                label=t[key],
                score=score,
                maximum=maximum,
                percent=pct,
                width=min(100, max(0, pct)),
                color=color,
                status=status(pct),
                checks=checks,
            )
        )
    source = scores.get("Catalogue") or {}
    if not isinstance(source, dict):
        source = {}
    total = number(scores["ICA"])
    return dict(
        score=total,
        width=min(100, max(0, total)),
        status=status(total),
        dimensions=dimensions,
        penalties=penalties if penalties_complete else None,
        source=source,
        legacy=legacy,
        text=t,
    )
