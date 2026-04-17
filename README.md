# AutoFeed Discovery for FreshRSS

Automatically discover and configure feed sources from any URL.

Paste a URL into AutoFeed and it will try every approach to turn it into an RSS feed — native RSS autodiscovery, JSON API endpoint detection, embedded JSON extraction (Next.js, Nuxt, etc.), heuristic XPath generation, and (in advanced mode) a headless browser that captures XHR/fetch calls and JS-rendered content that a plain HTTP fetch would miss.

## Architecture

```
┌─────────────────────────┐       HTTP        ┌──────────────────────────────┐
│       FreshRSS           │  ──────────────►  │   AutoFeed Sidecar           │
│                          │                   │   (Python / FastAPI)         │
│  xExtension-AutoFeed     │  ◄──────────────  │                              │
│  - Discovery UI          │      JSON         │  Phase 1 (always):           │
│  - Feed creation         │                   │  - RSS/Atom autodiscovery    │
│  - Settings              │                   │  - Embedded JSON detection   │
└─────────────────────────┘                   │  - Static JS API extraction  │
                                               │  - Heuristic XPath           │
                                               │                              │
                                               │  Phase 2 (advanced mode):    │
                                               │  - Playwright XHR capture    │
                                               │  - Scrapling selector gen    │
                                               └──────────────────────────────┘
```

## Quick Start

```bash
git clone <this-repo>
cd superscraper-freshrss
docker compose up -d
```

Then open FreshRSS at `http://localhost:8080`, enable the **AutoFeed Discovery** extension under Settings → Extensions, configure the sidecar URL (default `http://autofeed-sidecar:8000` works out of the box with Docker Compose), and click **Auto-Discover Feed** in the dropdown menu.

### Without Docker

**Sidecar:**

```bash
cd sidecar
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # for Phase 2 / advanced mode
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Extension:**

Copy `xExtension-AutoFeed/` into your FreshRSS `extensions/` directory and enable it.

## How Discovery Works

When you submit a URL, the sidecar runs a cascade of detection methods. Phase 1 steps always run; Phase 2 steps run automatically when no RSS is found and the page appears JS-rendered, or when you tick **Use advanced discovery** in the UI.

| Step | Phase | Method | What it finds |
|------|-------|--------|---------------|
| 1 | 1 | RSS/Atom autodiscovery | `<link rel="alternate">` tags and 19 common feed paths (`/feed`, `/rss`, `/atom.xml`, `/wp-json/wp/v2/posts`, …) |
| 2 | 1 | Embedded JSON detection | Next.js `__NEXT_DATA__`, Nuxt `__NUXT__`, `application/json` script tags, large inline JSON objects |
| 3 | 1 | Static JS analysis | API URL strings in page source and linked JS files (`/api/`, `/v1/`, `/wp-json/`, `/graphql`, …), probed for JSON feed-likeness |
| 4 | 1 | Heuristic XPath | Repeated DOM patterns (articles, list items, cards) generate XPath selectors |
| 5 | 2 | Network interception | Headless Chromium loads the page and captures every XHR/fetch JSON response before and after `networkidle` |
| 6 | 2 | Scrapling selector gen | Browser-rendered HTML is parsed by Scrapling's lxml engine to find repeated elements and auto-generate XPath selectors with nav/footer penalties |

Each discovered source is scored by a **feed-likeness algorithm** that checks for title, URL, date, content, and author keys, structural consistency across items, and reasonable item counts.

Results are presented in the FreshRSS UI ranked by score, with pre-filled configuration forms that map directly to FreshRSS's native feed types (RSS/Atom, JSON+DotNotation, HTML+XPath).

## Advanced Discovery (Phase 2)

Tick **Use advanced discovery (browser-based, slower)** on the discovery form to activate Phase 2. This launches a headless Chromium instance that:

- Intercepts all JSON responses the page makes (including authenticated AJAX calls visible to the browser)
- Waits for `networkidle` plus a 2.5s grace period for lazy-loaded requests
- Returns fully JS-rendered HTML to Scrapling for superior selector generation
- Filters out tracking, analytics, and CDN URLs automatically

Typical times: Phase 1 only < 5 s · Phase 2 mode 8–20 s.

## Configuration

### Extension Settings (in FreshRSS)

| Setting | Default | Description |
|---------|---------|-------------|
| Sidecar URL | `http://autofeed-sidecar:8000` | URL of the sidecar service |
| Default TTL | `86400` (24h) | Refresh interval for discovered feeds |
| LLM Endpoint | *(empty)* | OpenAI-compatible API for Phase 3 LLM analysis |
| LLM API Key | *(empty)* | API key for the LLM endpoint |
| LLM Model | `gpt-4o-mini` | Model name for LLM analysis |
| RSS-Bridge URL | *(empty)* | RSS-Bridge instance for fallback bridge generation |

### Sidecar API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Healthcheck — returns `{"status":"ok","version":"0.2.0","phase":2}` |
| `/discover` | POST | Full discovery cascade. Body: `{"url": "…", "timeout": 30, "use_browser": false}` |

`use_browser: true` forces Phase 2 even when RSS feeds are found.

## Running Tests

```bash
cd sidecar
source .venv/bin/activate          # Python 3.10+ recommended

# Offline unit tests (fast, no network)
pytest tests/test_scoring.py tests/test_embedded_json.py \
       tests/test_rss_and_xpath.py tests/test_scrapling_selectors.py -v

# Phase 1 integration tests (needs network, ~65s)
pytest tests/test_integration.py -v --timeout=60

# Phase 2 browser tests (needs network + Playwright, ~60s)
pytest tests/test_network_intercept.py tests/test_cascade_phase2.py \
       -v --timeout=120

# Everything
pytest tests/ -v --timeout=120
```

48 tests total: 23 unit · 7 Phase 1 integration · 6 network interception · 7 Scrapling selectors · 5 Phase 2 cascade.

## Project Structure

```
superscraper-freshrss/
├── docker-compose.yml
├── xExtension-AutoFeed/              # FreshRSS extension (PHP)
│   ├── metadata.json
│   ├── extension.php                 # Hooks, config, sidecar HTTP client
│   ├── configure.phtml               # Settings UI
│   ├── Controllers/
│   │   └── AutoFeedController.php    # discover / analyze / apply actions
│   ├── views/AutoFeed/
│   │   ├── discover.phtml            # URL input + advanced discovery toggle
│   │   └── analyze.phtml            # Results display with subscribe forms
│   ├── static/autofeed.css
│   └── i18n/en/ext.php
└── sidecar/                          # Python sidecar (FastAPI)
    ├── Dockerfile
    ├── requirements.txt
    ├── app/
    │   ├── main.py                   # FastAPI app + lifespan
    │   ├── models/schemas.py         # Pydantic models
    │   └── discovery/
    │       ├── cascade.py            # Orchestrator (Phase 1 + 2)
    │       ├── rss_autodiscovery.py  # Step 1
    │       ├── embedded_json.py      # Step 2
    │       ├── static_js_analysis.py # Step 3
    │       ├── selector_generation.py # Step 4 heuristic XPath (Phase 1)
    │       ├── network_intercept.py  # Step 5 Playwright XHR (Phase 2)
    │       ├── scrapling_selectors.py # Step 6 Scrapling selectors (Phase 2)
    │       └── scoring.py
    └── tests/
        ├── test_scoring.py
        ├── test_embedded_json.py
        ├── test_rss_and_xpath.py
        ├── test_scrapling_selectors.py  # Phase 2 offline
        ├── test_integration.py          # Phase 1 network
        ├── test_network_intercept.py    # Phase 2 network
        └── test_cascade_phase2.py       # Phase 2 end-to-end
```

## Roadmap

- **Phase 1** ✅ Core sidecar + discovery cascade + FreshRSS extension UI
- **Phase 2** ✅ Playwright network interception + Scrapling adaptive selector generation
- **Phase 3** — LLM-assisted analysis (OpenAI-compatible endpoint) + RSS-Bridge script generation
- **Phase 4** — Routine scraping via sidecar with Scrapling's adaptive element tracking and stealth fetching
- **Phase 5** — Crowdsourced config sharing, GraphQL detection, pagination, browser companion bookmarklet

## Requirements

- Docker and Docker Compose (recommended), or:
  - Python 3.10+ with pip
  - Playwright Chromium (`playwright install chromium`) for Phase 2
  - FreshRSS 1.24.0+
  - PHP 7.4+ with cURL

## License

AGPL-3.0 (matching FreshRSS)
