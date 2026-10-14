# FAIR_eva_web_client

This repository contains a lightweight web interface for the
[FAIR EVA](https://github.com/IFCA-Advanced-Computing/FAIR_eva) evaluator.  It
focuses exclusively on the user interface component so that you can embed it
into your own projects without pulling in the entire backend and plugin
framework.  The client calls the FAIR EVA API to evaluate the FAIRness of
digital objects and presents a simple, customisable dashboard showing the
results.

## Features

* **Flask web interface** – provides a small form where users enter a persistent
  identifier such as a DOI or handle.  Optional fields for the repository
  (plugin) name and OAI‑PMH endpoint are supported.
* **Configurable API endpoint** – use the `--api-url` option or the
  `FAIR_EVA_API_URL` environment variable to point the client at a remote FAIR
  EVA instance.  The default is `http://localhost:9090`. For admin-managed
  deployments, use `config.ini` to set endpoint values.
* **Plugin discovery from API** – on the home page, the client tries to fetch
  active plugins from the configured API endpoint. If this request fails, a
  local fallback plugin list is used. You can set a specific plugins endpoint
  in `config.ini` or leave it empty for automatic discovery.
* **Downloadable outputs** – each evaluation is stored on disk so users can
  optionally download the raw JSON response and a generated PDF report.
* **Development mode** – enable `--dev` to read evaluation results from a JSON
  file instead of sending HTTP requests.  This is handy during development or
  offline demonstrations.  Supply your own file via `--sample-file` or rely
  on the packaged `data/salida_new.json`.
* **Simple result page** – the interface computes weighted scores for the
  principles (Findable, Accessible, Interoperable, Reusable) and the overall
  FAIRness.  Colour coded bars and tables summarise each indicator’s points,
  weight, status and messages.
* **Customisable branding** – configure the page title, logo link and logo
  image using command line flags (`--title`, `--logo-url`, `--logo-image`) or
  the corresponding environment variables.
* **Minimal dependencies** – only `Flask` and `requests` are required.  No
  database is used and there are no external services beyond the FAIR EVA API.

## Installation

First, ensure you have Python 3.8 or newer.  Clone or unpack this repository
and then install it using pip:

```bash
pip install .
```

This command will register a console script called `fair-eva-web-client` in
your Python environment.

## Usage

Run the development server via the console script or by invoking the module
directly.  Command line options allow you to customise the behaviour:

```bash
fair-eva-web-client             # Run against a local FAIR EVA API (default)

fair-eva-web-client --port 8080 --api-url https://fair.example.org:9090
                              # Run on a different port and point to a remote API

fair-eva-web-client --dev      # Development mode using the packaged sample data

fair-eva-web-client --dev --sample-file /path/to/your/data.json
                              # Development mode with a custom sample file

fair-eva-web-client --title "My FAIR EVA" --logo-url https://example.com \
                   --logo-image mylogo.png
                              # Change the title and logo

fair-eva-web-client --config-file /etc/fair-eva/config.ini
                              # Use admin-managed INI configuration
```

You can supply the configuration via environment variables:

* `FAIR_EVA_API_URL` – base URL of the FAIR EVA API (e.g. `https://fair.example.org`).
* `FAIR_EVA_API_PORT` – port on which the FAIR EVA API listens (default `9090`).
* `FAIR_EVA_TITLE` – title shown in the browser tab.
* `FAIR_EVA_LOGO_URL` – hyperlink applied to the logo in the navigation bar.
* `FAIR_EVA_LOGO_IMAGE` – filename under `static/img/` to use as the logo.
* `FAIR_EVA_DEV` – set to `1` to enable development mode.
* `FAIR_EVA_SAMPLE_FILE` – path to a JSON file containing sample evaluation data for development mode.
* `FAIR_EVA_PLUGINS_FILE` – optional path to a JSON file describing available plugins.  The file should provide a list of two-element arrays (``["id", "Label"]``) or objects with ``id``/``name`` and ``label``/``title``/``display_name`` fields.  If this variable is not set the application will look for installed plugins under ``fair_eva/plugin/*`` inside your Python environment (for example under ``lib64/python3.12/site-packages/fair_eva/plugin/`` inside a virtualenv).
* `FAIR_EVA_API_PLUGINS_PATH` – optional plugins endpoint path or full URL (e.g. `/v1.0/plugins` or `https://fair.example.org/v1.0/plugins`). If not set, automatic plugin endpoint discovery is used.
* `FAIR_EVA_REPORTS_DIR` – folder used to persist evaluation reports (JSON + data for PDF download).

## Admin configuration (`config.ini`)

To keep endpoint settings out of the web UI, configure them in `config.ini`.
Use `config.ini.example` as a template.

Run with:

```bash
fair-eva-web-client --config-file /path/to/config.ini
```

If `--config-file` is not passed, the app looks for `config.ini` in the
current working directory.

When running in development mode the sample file is expected to follow the
same structure as the FAIR EVA API response, namely a top‑level mapping
containing an arbitrary persistent identifier key and, optionally, an
`evaluator_logs` entry.  This repository includes a sample file at
`fair_eva_web_client/data/salida_new.json` which can be used for testing.

## Repository contents

* `fair_eva_web_client/` – Python package containing the Flask application, templates and static assets.
* `fair_eva_web_client/static/img/` – images used by the interface.  You can drop your own logo images here.
* `fair_eva_web_client/data/` – example JSON files for development mode.
* `pyproject.toml` – package configuration for installation.
* `README.md` – this documentation.

## Contributing

Contributions are welcome!  If you wish to extend the functionality or fix a
bug, please open an issue or submit a pull request.  Note that this client is
not a drop‑in replacement for the full FAIR EVA project: it deliberately
omits the backend evaluation logic and plugin mechanism.  See the upstream
project for details on those components.

## License

This project is released under the MIT License.  It reuses ideas from the
FAIR EVA web interface but is not affiliated with the original maintainers.

## ICA dashboard and ICA-only mode

GBIF's `data_01` is shown as an ICA dashboard: an overall score, a weighted
contribution bar, three dimension cards and expandable checks. Dimension
percentages use their own maxima (taxonomic 45, geographic 35, temporal 20).
Penalties are displayed explicitly and are not subtracted twice. Negative scores
remain visible; only the drawn bar/ring lengths are limited to 0–100%.
Catalogue release and update warnings are shown when provided by the plugin.
The layout supports desktop/mobile and Spanish/English, and ICA itself does not
require Chart.js or JavaScript for its expandable details.

To execute **only** `data_01`, set the following in the web client's config file:

```ini
[fair_eva]
evaluation_mode = ica
api_ica_path = /v1.0/plugins/data_01
api_timeout = 900
```

The default is `evaluation_mode = full`, which continues using `api_eval_path`
(`/v1.0/rda/rda_all`). In `ica` mode the client makes one evaluation POST to
`api_ica_path` with `id`, `repo`, `lang` and `url` (the published data-test schema
requires `url`, while the API plugin loader uses `id` and `repo`). It does not
execute all tests and then hide FAIR results. Plugin-list discovery on the home
page can still make separate metadata requests.

If the API or selected plugin does not support `data_01`, the error is displayed;
there is no fallback to running the complete FAIR suite. `api_ica_path` may be an
absolute URL or a path on the configured API host. The 900-second request timeout
allows for the first catalogue download/import; align reverse-proxy/server
request timeouts when deploying. Subsequent requests use the local catalogue.

Also available:

- Environment: `FAIR_EVA_EVALUATION_MODE`, `FAIR_EVA_API_ICA_PATH`, `FAIR_EVA_API_TIMEOUT`.
- CLI: `--evaluation-mode full` or `--evaluation-mode ica`.
- Precedence follows the existing client: environment defaults, then INI, then CLI.

The updated GBIF plugin keeps the API's HTML `msg` contract and embeds precise
ICA values in a versioned `data-ica` JSON attribute. The client converts these
values into its own escaped view model. Older plugin tables remain supported,
with unknown fields/version shown as unavailable. A failed evaluation is not
shown as a zero-valued ICA gauge. JSON downloads retain the raw API response;
PDF reports include the ICA breakdown and omit FAIR scores in ICA-only mode.
No changes to the FAIR EVA API package are required. Install the updated GBIF
plugin and this client to obtain structured scores and provenance; the legacy
adapter is available while updating the deployments separately.

Preview with synthetic, explicitly illustrative data (no evaluation API calls):

```bash
fair-eva-web-client --dev --evaluation-mode ica \
  --sample-file fair_eva_web_client/data/ica_example.json
```

Regression tests:

```bash
python3 -m unittest discover -s tests -v
```
