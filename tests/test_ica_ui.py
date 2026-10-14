import copy
from html import escape
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fair_eva_web_client.app import (
    Settings,
    apply_ini_overrides,
    build_default_eval_endpoint,
    create_app,
    normalize_evaluation_response,
)
from fair_eva_web_client.ica import build_ica

EXAMPLE = (
    Path(__file__).resolve().parents[1] / "fair_eva_web_client/data/ica_example.json"
)
RAW = json.loads(EXAMPLE.read_text())
TEST = RAW["demo:ica"]


class ICAModelTests(unittest.TestCase):
    def test_exact_scores_and_penalties(self):
        result = build_ica(TEST, "es")
        self.assertEqual(result["score"], 84)
        self.assertEqual(result["penalties"], -5.5)
        self.assertAlmostEqual(result["dimensions"][0]["percent"], 38 / 45 * 100)
        self.assertEqual(result["source"]["version"], "2026-08-20")
        self.assertEqual(result["text"]["Taxonomic"], "Taxonómica")
        self.assertFalse(result["legacy"])
        self.assertEqual(build_ica({"msg": [{"message": TEST["msg"]}]})["score"], 84)

    def test_legacy_table(self):
        pairs = {
            "ICA total": "42%",
            "Taxonomic": "30%",
            "Geographic": "15%",
            "Temporal": "-3%",
            "IncorrectDates": "15%",
        }
        html = (
            "<table>"
            + "".join(
                "<tr><td>" + k + "</td><td>" + v + "</td></tr>"
                for k, v in pairs.items()
            )
            + "</table>"
        )
        result = build_ica({"msg": html})
        self.assertTrue(result["legacy"])
        self.assertEqual(result["dimensions"][2]["percent"], -15)
        self.assertEqual(result["dimensions"][2]["width"], 0)
        self.assertEqual(result["dimensions"][2]["checks"][-1]["score"], -15)
        self.assertIsNone(result["dimensions"][0]["checks"][0]["score"])

    def test_missing_invalid_and_non_ica_results(self):
        for msg in [
            "ICA unavailable",
            '<div data-ica-version="1" data-ica="bad">x</div>',
            "<table><tr><td>ICA total</td><td>NaN</td></tr></table>",
        ]:
            self.assertIsNone(build_ica({"msg": msg}))
        negative = {"ICA": -10, "Taxonomic": 0, "Geographic": 0, "Temporal": -10}
        result = build_ica(
            {
                "msg": '<div data-ica-version="1" data-ica="'
                + escape(json.dumps(negative))
                + '"></div>'
            }
        )
        self.assertEqual(result["score"], -10)
        self.assertEqual(result["width"], 0)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = Settings(
            evaluation_mode="ica",
            reports_dir=self.temp.name,
            plugins_file=str(EXAMPLE.parent / "plugins_example.json"),
        )
        self.app = create_app(self.cfg)
        self.client = self.app.test_client()

    def response(self, data=RAW):
        response = Mock()
        response.json.return_value = data
        return response

    def test_only_ica_request_render_and_exports(self):
        with patch(
            "fair_eva_web_client.app.requests.post", return_value=self.response()
        ) as post:
            response = self.client.get("/es/evaluator?item_id=demo:ica&plugin=gbif")
        self.assertEqual(response.status_code, 200)
        args, kwargs = post.call_args
        self.assertEqual(post.call_count, 1)
        self.assertEqual(args[0], "http://localhost:9090/v1.0/plugins/data_01")
        self.assertEqual(
            kwargs["json"],
            {"id": "demo:ica", "url": "demo:ica", "repo": "gbif", "lang": "es"},
        )
        self.assertEqual(kwargs["timeout"], 900)
        html = response.get_data(as_text=True)
        self.assertIn("ica-dashboard", html)
        self.assertNotIn('id="areasAccordion"', html)
        self.assertNotIn("chart.js", html)
        self.assertIn("84.00", html)
        report_path = next(Path(self.temp.name).glob("*.json"))
        report = json.loads(report_path.read_text())
        self.assertEqual(report["metadata"]["evaluation_mode"], "ica")
        self.assertEqual(report["ica"]["score"], 84)
        download = self.client.get("/es/download-json/" + report_path.stem)
        self.assertEqual(download.json, RAW)
        pdf = self.client.get("/es/export_pdf/" + report_path.stem)
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))

    def test_full_request_preserves_fair_and_adds_ica(self):
        self.cfg.evaluation_mode = "full"
        full = {
            "demo:ica": {
                "data_test": {"data_01": TEST},
                "findable": {
                    "rda_f1_01m": {
                        "name": "RDA_F1_01M",
                        "points": 100,
                        "score": {"weight": 20},
                        "msg": [],
                    }
                },
            }
        }
        with patch(
            "fair_eva_web_client.app.requests.post", return_value=self.response(full)
        ) as post:
            response = self.client.get("/en/evaluator?item_id=demo:ica&plugin=gbif")
        self.assertEqual(
            post.call_args.args[0], "http://localhost:9090/v1.0/rda/rda_all"
        )
        self.assertNotIn("url", post.call_args.kwargs["json"])
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="areasAccordion"', html)
        self.assertIn("ica-dashboard", html)
        self.assertIn("RDA_F1_01M", html)

    def test_no_fallback_when_api_does_not_support_data01(self):
        with patch(
            "fair_eva_web_client.app.requests.post",
            side_effect=requests.HTTPError("404 not found"),
        ) as post:
            result = self.client.get("/en/evaluator?item_id=x&plugin=gbif")
        self.assertEqual(post.call_count, 1)
        self.assertIn(b"No FAIR tests were requested", result.data)
        self.assertNotIn(b"ica-dashboard", result.data)

    def test_unavailable_does_not_show_zero_ica(self):
        unavailable = {
            "name": "DATA_01",
            "points": 0,
            "msg": "ICA unavailable: catalogue offline",
            "score": {"total": 100},
        }
        with patch(
            "fair_eva_web_client.app.requests.post",
            return_value=self.response(unavailable),
        ):
            result = self.client.get("/en/evaluator?item_id=x&plugin=gbif")
        self.assertIn(b"No valid ICA breakdown", result.data)
        self.assertNotIn(b'class="ica-gauge', result.data)
        self.assertIn(b"catalogue offline", result.data)

    def test_config_and_dev_mode(self):
        cfg_path = Path(self.temp.name) / "config.ini"
        cfg_path.write_text(
            "[fair_eva]\nevaluation_mode=ica\napi_ica_path=/prefix/plugins/data_01\napi_timeout=1200\n"
        )
        cfg = Settings()
        apply_ini_overrides(cfg, str(cfg_path))
        self.assertTrue(
            build_default_eval_endpoint(cfg).endswith("/prefix/plugins/data_01")
        )
        self.assertEqual(cfg.api_timeout, 1200)
        cfg.dev_mode = True
        cfg.sample_file = str(EXAMPLE)
        cfg.reports_dir = self.temp.name
        with patch(
            "fair_eva_web_client.app.requests.post",
            side_effect=AssertionError("No API in dev"),
        ):
            result = (
                create_app(cfg)
                .test_client()
                .get("/es/evaluator?item_id=demo:ica&plugin=gbif")
            )
        self.assertIn(b"ica-dashboard", result.data)
        with self.assertRaises(ValueError):
            create_app(Settings(evaluation_mode="unexpected"))

    def test_envelope_shapes_and_missing_test(self):
        for raw in [TEST, RAW, {"data_test": {"DATA_01": TEST}}]:
            result = normalize_evaluation_response(raw, "demo:ica", "ica")
            self.assertEqual(result["data_test"]["data_01"], TEST)
        with self.assertRaises(ValueError):
            normalize_evaluation_response(
                {"demo:ica": {"findable": {}}}, "demo:ica", "ica"
            )

    def test_source_strings_are_escaped(self):
        from fair_eva_web_client.ica import ICAParser

        parser = ICAParser()
        parser.feed(TEST["msg"])
        parser.payload["Catalogue"]["version"] = "<img src=x onerror=alert(1)>"
        test = copy.deepcopy(TEST)
        test["msg"] = (
            '<div data-ica-version="1" data-ica="'
            + escape(json.dumps(parser.payload))
            + '"></div>'
        )
        with patch(
            "fair_eva_web_client.app.requests.post", return_value=self.response(test)
        ):
            result = self.client.get("/en/evaluator?item_id=x&plugin=gbif")
        self.assertNotIn(b"<img src=x", result.data)
        self.assertIn(b"&lt;img src=x", result.data)


if __name__ == "__main__":
    unittest.main()
