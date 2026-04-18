# AutoFeed Discovery for FreshRSS

Automatically discover, configure, and subscribe to feed sources from any URL.

Paste a URL into AutoFeed and it tries every reasonable approach to turn it into a feed — native RSS autodiscovery (with liveness probing), JSON API detection, embedded-JSON extraction (Next.js, Nuxt, Gatsby, etc.), heuristic XPath generation, headless-browser XHR capture, GraphQL operation detection, and optional LLM-assisted strategy selection and RSS-Bridge script generation. Preview any candidate inline before subscribing.

---

## Table of contents

- [AutoFeed Discovery for FreshRSS](#autofeed-discovery-for-freshrss)
  - [Table of contents](#table-of-contents)
  - [Architecture](#architecture)
  - [Quick start](#quick-start)
    - [With RSS-Bridge](#with-rss-bridge)
    - [Without Docker](#without-docker)
  - [How discovery works](#how-discovery-works)
    - [Dead advertised feeds](#dead-advertised-feeds)
  - [Inline preview](#inline-preview)
  - [Advanced discovery (Phase 2)](#advanced-discovery-phase-2)
  - [LLM analysis (Phase 3)](#llm-analysis-phase-3)
    - [Flow](#flow)
    - [Bridge naming contract](#bridge-naming-contract)
    - [Configuring an LLM](#configuring-an-llm)
  - [Routine scraping (Phase 4)](#routine-scraping-phase-4)
  - [RSS-Bridge installation](#rss-bridge-installation)
  - [Configuration](#configuration)
    - [Extension settings (in FreshRSS)](#extension-settings-in-freshrss)
    - [Sidecar API](#sidecar-api)
    - [Sidecar environment variables](#sidecar-environment-variables)
  - [Bring your own services](#bring-your-own-services)
  - [Security](#security)
    - [Inbound authentication](#inbound-authentication)
    - [CSRF](#csrf)
    - [CORS](#cors)
    - [Rate limiting](#rate-limiting)
    - [Auto-deploy risks](#auto-deploy-risks)
    - [LLM credentials](#llm-credentials)
  - [Running tests](#running-tests)
  - [Troubleshooting](#troubleshooting)
    - [The advertised RSS feed is broken](#the-advertised-rss-feed-is-broken)
    - [The site has 12 items but AutoFeed shows 4](#the-site-has-12-items-but-autofeed-shows-4)
    - [Preview is empty](#preview-is-empty)
    - [LLM returns 401 Unauthorized](#llm-returns-401-unauthorized)
    - [LLM returns JSON parse errors (`LLMMalformed`)](#llm-returns-json-parse-errors-llmmalformed)
    - [LLM times out](#llm-times-out)
    - [Generated bridge fails `php -l`](#generated-bridge-fails-php--l)
    - [Auto-deploy writes nothing](#auto-deploy-writes-nothing)
    - [Sidecar returns 401 on every request](#sidecar-returns-401-on-every-request)
    - [Sidecar returns 429](#sidecar-returns-429)
  - [Project structure](#project-structure)
  - [Roadmap](#roadmap)
  - [Requirements](#requirements)
  - [License](#license)

---

## Architecture

```
┌─────────────────────────┐       HTTP        ┌──────────────────────────────────┐
│        FreshRSS         │  ──────────────►  │     AutoFeed Sidecar             │
│                         │                   │     (Python / FastAPI)           │
│  xExtension-AutoFeed    │  ◄──────────────  │                                  │
│  - Discover URL         │      JSON         │  Phase 1 — always runs:          │
│  - Preview candidates   │                   │  - RSS/Atom autodiscovery        │
│  - LLM analysis         │                   │    + liveness probe (HEAD+GET)   │
│  - Bridge generation    │                   │  - Embedded JSON detection       │
│  - Apply / Subscribe    │                   │  - Static JS API extraction      │
│  - Settings             │                   │  - Heuristic XPath (+ union)     │
└─────────────────────────┘                   │                                  │
                                              │  Phase 2 — when RSS missing/dead │
                                              │  or advanced mode:               │
                                              │  - Playwright XHR capture        │
                                              │  - GraphQL operation detection   │
                                              │  - Scrapling selector gen        │
                                              │                                  │
           ┌──────────────────┐               │  Phase 3 — LLM configured:       │
           │  LLM API         │ ◄─────────────│  - /analyze   strategy pick      │
           │  (OpenAI-compat) │  ────────────►│  - /bridge/generate PHP script   │
           └──────────────────┘               │  - /bridge/deploy file write     │
                                              │                                  │
                                              │  Phase 4 — routine scraping:     │
                                              │  - /scrape/config persisted      │
                                              │  - /scrape/feed returns Atom XML │
                                              │  - Adaptive cache via Scrapling  │
                                              └──────────────────────────────────┘
                                                            │
                                       ┌────────────────────┴────────────────────┐
                                       ▼ local mount                  ▼ remote drop
                          ┌────────────────────────┐      ┌───────────────────────┐
                          │  ./generated-bridges/  │      │  HTTP POST or SFTP    │
                          │  (shared volume)       │      │  to an RSS-Bridge host│
                          └────────────┬───────────┘      └───────────┬───────────┘
                                       │                              │
                                       └─────────────┬────────────────┘
                                                     ▼
                                       ┌──────────────────────────────┐
                                       │  RSS-Bridge                  │
                                       │  (optional profile / remote) │
                                       └──────────────────────────────┘
```

---

## Quick start

```bash
git clone <this-repo>
cd autofeed-freshrss
docker compose up -d
```

Open FreshRSS at `http://localhost:8080`, enable the **AutoFeed Discovery** extension under Settings → Extensions, confirm the sidecar URL (default `http://autofeed-sidecar:8000` works out of the box), and click **Auto-Discover Feed** from the dropdown menu.

### With RSS-Bridge

```bash
docker compose --profile with-rss-bridge up -d
```

This also brings up RSS-Bridge on port 3000, sharing the `./generated-bridges/` directory with the sidecar so any bridge the LLM writes is served immediately — no RSS-Bridge restart required.

### Without Docker

**Sidecar:**

```bash
cd sidecar
python3 -m venv .venv && source .venv/bin/activate  # Python 3.10–3.12
pip install -r requirements.txt
playwright install chromium        # only needed for Phase 2 / advanced mode
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Extension:** copy `xExtension-AutoFeed/` into your FreshRSS `extensions/` directory and enable it. Requires FreshRSS 1.24.0+.

---

## How discovery works

When you submit a URL, the sidecar runs a cascade. Phase 1 steps always run; Phase 2 steps run automatically when **any** of these hold:

- No *live* RSS feed is found, and the page appears JS-rendered or has no reachable API endpoints.
- Anti-bot challenges (Cloudflare, Turnstile) are detected.
- You ticked **Use advanced discovery** in the UI (`use_browser: true`).
- You ticked **Ignore advertised RSS** (`force_skip_rss: true`) — useful when the site's `<link rel="alternate">` points at a broken feed.

| Step | Phase | Method | What it finds |
|------|-------|--------|---------------|
| 1 | 1 | RSS/Atom autodiscovery + liveness probe | `<link rel="alternate">` tags and 19 common feed paths (`/feed`, `/rss`, `/atom.xml`, `/wp-json/wp/v2/posts`, …), **each probed with HEAD+GET** and marked `is_alive` / `http_status` / `parse_error` |
| 2 | 1 | Embedded JSON detection | Next.js `__NEXT_DATA__`, Nuxt `__NUXT__`, `application/json` script tags, large inline JSON blocks |
| 3 | 1 | Static JS analysis | API URL strings in page source and linked JS files (`/api/`, `/v1/`, `/wp-json/`, `/graphql`), probed for JSON feed-likeness |
| 4 | 1 | Heuristic XPath | Repeated DOM patterns (articles, list items, cards) generate XPath selectors. **Union pass** emits an additional candidate (`A \| B`) when two sibling class-groups share a common ancestor — catches featured-plus-main layouts |
| 5 | 2 | Network interception | Headless Chromium captures every XHR / fetch JSON response before and after `networkidle` plus a 2.5 s grace window; tracking/analytics URLs are filtered out |
| 6 | 2 | GraphQL detection | Network capture results are scanned for GraphQL operations; the most feed-like queries are kept with their `response_path` and variables |
| 7 | 2 | Scrapling selector generation | Browser-rendered HTML fed to Scrapling's lxml engine; repeated elements become XPath candidates with nav/footer penalties applied |

Every candidate is scored by a **feed-likeness algorithm** checking for title, URL, date, content, author, and image keys, structural consistency across items, and reasonable item counts.

Results appear in the FreshRSS UI ranked by score, each with a **Preview** button and a **Subscribe** button. Subscribe maps directly to FreshRSS's native feed types (RSS/Atom, JSON+DotNotation, HTML+XPath) or to an RSS-Bridge feed URL.

### Dead advertised feeds

When a `<link rel="alternate">` points at a 404 or a non-feed content type, AutoFeed:

1. Marks the feed `is_alive: false` in the results and shows it in a separate **Advertised but not working** section with the HTTP code and error reason.
2. Keeps running the rest of the cascade (Phase 2 is no longer short-circuited by a broken advertised feed).
3. Surfaces a banner on the results page: *"The RSS feed advertised by this site returned HTTP 404 — the XPath candidates below will still work."*

You can also paste an `override_xpath_item` on the discovery form to skip auto-generated candidates entirely.

---

## Inline preview

Every candidate card on the results page has a **Preview** button. Clicking it fetches the first 10 items from that candidate and inserts a table into the card showing:

- How many items were found (of the 10 cap).
- A per-field success count: `T=8/10` means 8 out of 10 items had a title, etc.
- A short table of each item's title, link, and timestamp.

Adjusting any selector input on the card re-runs the preview with a 600 ms debounce — useful for iterating from `descendant::h2` to `descendant::h3/a` until every row has a title.

Previews go through `POST /preview`, which is `run_scrape` with `adaptive: false` and an empty `cache_key` so previews never pollute the adaptive selector cache.

---

## Advanced discovery (Phase 2)

Tick **Use advanced discovery (browser-based, slower)** on the discovery form to activate Phase 2 unconditionally. It:

- Intercepts all JSON responses the page makes (including authenticated AJAX visible to the browser).
- Waits for `networkidle` plus a 2.5 s grace period for lazy-loaded requests.
- Returns fully JS-rendered HTML to Scrapling for superior selector generation.
- Filters out tracking, analytics, and CDN URLs automatically.
- Detects GraphQL operations and records their variables, response path, and sample keys.

Typical times: Phase 1 only < 5 s · Phase 2 mode 8–20 s.

Browser-based discovery is rate-limited to **3 requests per minute per IP** by default (vs 30/min for normal `/discover`).

---

## LLM analysis (Phase 3)

When an LLM endpoint is configured, two extra buttons appear on the results page: **Analyse with LLM** and **Generate RSS-Bridge script**.

### Flow

```
User hits "Analyse with LLM"
    │
    ▼
Extension POSTs {discover_id, llm} to /analyze
    │
    ▼
Sidecar resolves discover_id from its discovery cache, builds a structured prompt
    │
    ▼
LLM picks the best strategy (rss > json_api > embedded_json > xpath > rss_bridge)
    │
    ▼
Star-card appears at top of results, apply form pre-filled


User hits "Generate RSS-Bridge script"
    │
    ▼
Extension POSTs {discover_id, llm, hint} to /bridge/generate
    │
    ▼
LLM returns {bridge_name: "FooBridge", php_code: "<?php\nclass FooBridge..."}
    │
    ▼
Sanity check:  <?php present · no closing ?> · extends BridgeAbstract · class name
               matches bridge_name exactly · collectData() present · required
               constants (NAME, URI, DESCRIPTION, MAINTAINER='AutoFeed-LLM',
               PARAMETERS) present
    │
    ▼
Warnings are split into HARD (shell_exec, system, passthru, popen, proc_open,
eval, assert, create_function, pcntl_exec) and SOFT (file_get_contents, fopen,
curl_, base64_decode — normal RSS-Bridge idioms; shown as "review if
unexpected")
    │
    ▼
bridge.phtml renders PHP with Copy / Deploy / Subscribe buttons
    │
    └── (if auto-deploy enabled) → /bridge/deploy writes the file atomically
                  │
                  └── RSS-Bridge picks it up → subscribe CTA appears
```

### Bridge naming contract

The LLM is instructed to return `bridge_name` **including the `Bridge` suffix** — e.g. `"ExampleBlogBridge"` — matching RSS-Bridge's filesystem convention (`ExampleBlogBridge.php`) and its regex for discovered classes. The PHP class name inside the file must be identical. The deploy endpoint rejects anything that doesn't match `^[A-Z][A-Za-z0-9]*Bridge$`.

When building a subscribe URL, `Bridge` is stripped from the query-string parameter because that's what RSS-Bridge expects:

```
https://rss-bridge.example/?action=display&bridge=ExampleBlog&format=Atom
```

### Configuring an LLM

In FreshRSS → Settings → Extensions → AutoFeed Discovery:

| Setting | Example |
|---------|---------|
| LLM endpoint | `https://api.openai.com/v1` |
| LLM API key | `sk-…` (masked on display — first 4 + `…` + last 4 chars) |
| LLM model | `gpt-4o-mini` |

Any OpenAI-compatible endpoint works — OpenAI, OpenRouter, Anthropic via a proxy, or a local Ollama instance.

---

## Routine scraping (Phase 4)

Once a candidate is applied, AutoFeed saves a **scrape config** to the sidecar, and FreshRSS subscribes to an Atom feed URL that the sidecar generates on demand.

```
subscribe → POST /scrape/config  → returns {config_id, feed_url}
         → feed_url = http://autofeed-sidecar:8000/scrape/feed?id=<config_id>
         → FreshRSS refreshes on its normal schedule via GET /scrape/feed?id=…
         → sidecar runs the saved config, re-parses, returns Atom XML
```

Configs store their own `cache_key` (equal to the `config_id`). When `adaptive: true`, Scrapling writes a small selector-recovery cache under `/app/data/cache/` that survives minor DOM drift without intervention.

**Rerun configs** `GET /scrape/config/{id}` to inspect, `DELETE /scrape/config/{id}` to remove.

---

## RSS-Bridge installation

The user question *"when RSS-Bridge is a provided image — not the bundled sidecar image, how do I install a bridge?"* has four answers. Pick the one that matches your setup:

| Scenario | Method | Setup |
|---|---|---|
| Bundled sidecar (compose default) | **Shared volume, automatic** | Enable *Auto-deploy bridges* in Settings. No extra steps. |
| RSS-Bridge running in the same compose file (separate image) | **Shared volume, automatic** | Add `./generated-bridges:/config/bridges` to the rss-bridge service. Already done if you used `--profile with-rss-bridge`. |
| RSS-Bridge on a remote host you can SSH into | **SFTP drop** | Fill the SFTP section in Settings (`sftp_host`, `sftp_user`, `sftp_key_path`, `sftp_target_dir`). Use the **Test SFTP connection** button to verify. |
| RSS-Bridge behind a reverse proxy on a custom image with a `/deploy-bridge` endpoint | **HTTP POST** | Set *RSS-Bridge URL* in Settings; the sidecar falls back to remote POST when the local mount isn't writable, or always when `deploy_mode: remote_only`. |
| Hosted RSS-Bridge you don't control | **Manual copy** | Copy the generated PHP from the bridge page; paste into the operator's `bridges/` directory. |

The `deploy_mode` setting controls precedence:

- `auto` (default) — try local first; fall back to SFTP or HTTP POST if local isn't writable.
- `local_only` — force the shared-volume path. Error if the mount is read-only.
- `remote_only` — force SFTP (if configured) or HTTP POST. Skip the local mount entirely.

> The stock RSS-Bridge image does **not** expose `/deploy-bridge`. The HTTP-POST path requires either a tiny sidecar proxy in front of RSS-Bridge or a custom image that adds the endpoint.

---

## Configuration

### Extension settings (in FreshRSS)

| Setting | Default | Description |
|---------|---------|-------------|
| Sidecar URL | `http://autofeed-sidecar:8000` | Base URL of the sidecar service. |
| Default TTL | `86400` (24 h) | Refresh interval for discovered feeds. |
| Sidecar auth token | *(empty)* | Sent as `Authorization: Bearer <token>` to all mutating endpoints. Must match `AUTOFEED_INBOUND_TOKEN` on the sidecar. |
| LLM endpoint | *(empty)* | OpenAI-compatible API base URL. |
| LLM API key | *(empty)* | Bearer token for the LLM. Masked on display. |
| LLM model | `gpt-4o-mini` | Model name sent in every request. |
| RSS-Bridge URL | *(empty)* | Public URL of your RSS-Bridge instance (for building subscribe URLs and for remote deploy). |
| Auto-deploy bridges | off | Write generated PHP to `./generated-bridges/` automatically. |
| Deploy mode | `auto` | `auto` / `local_only` / `remote_only`. |
| SFTP host / port / user / key / target | *(empty)* | For SFTP deployment to a remote RSS-Bridge host. |

### Sidecar API

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/health` | GET | open | Returns `{"status":"ok","version":"0.6.0","phase":5}`. |
| `/feed/health` | GET | open | `?url=…` — returns `{is_alive, http_status, parse_error}` for a single feed URL. |
| `/discover` | POST | optional | Full discovery cascade. `{"url","timeout":30,"use_browser":false,"force_skip_rss":false,"services":{…}}`. Returns `DiscoveryResults` plus a `discover_id` that can be passed to `/analyze` and `/bridge/generate`. |
| `/discover/{discover_id}` | GET | required | Fetch a cached discovery payload (15-minute TTL). |
| `/analyze` | POST | required | LLM strategy selection. Accepts either `results` inline or `discover_id`. |
| `/bridge/generate` | POST | required | LLM RSS-Bridge PHP generation. |
| `/bridge/deploy` | POST | required | Write the bridge PHP. Supports `deploy_mode` and SFTP fields. |
| `/scrape` | POST | required | Run a scrape inline. |
| `/preview` | POST | required | Same shape as `/scrape`; caps output to 10 items, disables cache, returns per-field success counts. |
| `/scrape/config` | POST | required | Save a scrape config; returns `{config_id, feed_url}`. |
| `/scrape/config/{id}` | GET | open | Retrieve a saved scrape config. |
| `/scrape/config/{id}` | DELETE | required | Delete a saved scrape config. |
| `/scrape/feed` | GET | open | Run saved config and return Atom XML (`?id=…`). FreshRSS hits this on its refresh schedule. |
| `/graphql/probe` | POST | required | Best-effort GraphQL introspection. |
| `/graphql/config` | POST | required | Save a GraphQL probe config; returns `{config_id, feed_url}`. |
| `/graphql/config/{id}` | GET / DELETE | open / required | Retrieve or delete a GraphQL config. |
| `/graphql/feed` | GET | open | Re-run a saved GraphQL operation and return Atom XML. |
| `/sftp/test` | POST | required | Test SFTP credentials against a target host. |
| `/sftp/deploy` | POST | required | Manual SFTP deploy of arbitrary PHP. |

**Auth column legend.** *open* = no token required. *optional* = token honoured if set, otherwise open. *required* = rejected with 401 when `AUTOFEED_INBOUND_TOKEN` is set and the header is missing or wrong.

**Rate limits.** 30 req/min per IP on mutating endpoints. 3 req/min on `/discover` with `use_browser=true`. Exceeded requests get 429.

### Sidecar environment variables

| Variable | Default | Purpose |
|---|---|---|
| `AUTOFEED_INBOUND_TOKEN` | *(unset)* | Bearer token required on mutating endpoints. When unset, the sidecar is open. |
| `AUTOFEED_CORS_ORIGINS` | *(empty)* | Comma-separated list of origins. Default is no CORS; required only if the FreshRSS *Test connection* button is served from a different host. |
| `AUTOFEED_BRIDGES_DIR` | `/app/bridges` | Where to write generated bridge PHP. |
| `AUTOFEED_DATA_DIR` | `/app/data` | Root for saved scrape / GraphQL configs. |
| `AUTOFEED_DISCOVERY_CACHE_DIR` | `/app/data/discover-cache` | Where `/discover` results are cached for `discover_id` lookups. |
| `AUTOFEED_DISCOVERY_CACHE_TTL` | `900` (15 min) | TTL for discovery cache entries. |
| `AUTOFEED_PUBLIC_URL` | `http://autofeed-sidecar:8000` | Base URL used when building `feed_url` for scrape configs. |
| `AUTOFEED_FETCH_BACKEND` | `bundled` | `bundled` / `playwright_server` / `browserless` / `scrapling_serve`. |
| `AUTOFEED_PLAYWRIGHT_WS` | *(empty)* | WebSocket URL of an external Playwright server. |
| `AUTOFEED_BROWSERLESS_WS` | *(empty)* | CDP URL of a Browserless instance. |
| `AUTOFEED_SCRAPLING_URL` | *(empty)* | HTTP URL of a Scrapling-serve cluster. |
| `AUTOFEED_RSS_BRIDGE_URL` | *(empty)* | RSS-Bridge base URL for subscribe links and remote deploy. |
| `AUTOFEED_SERVICES_TOKEN` | *(empty)* | Bearer token the sidecar sends to external services (Browserless, Scrapling, custom RSS-Bridge proxies). |

---

## Bring your own services

AutoFeed works out of the box with its bundled in-process Playwright and Scrapling. For production or high-volume use, point it at your own browser farm or stealth-fetcher. Uncomment and configure one profile in `docker-compose.yml`, or set the matching env vars on a standalone sidecar:

| Setting | Env var | Example |
|---------|---------|---------|
| Fetch backend | `AUTOFEED_FETCH_BACKEND` | `bundled` \| `playwright_server` \| `browserless` \| `scrapling_serve` |
| Playwright server WebSocket | `AUTOFEED_PLAYWRIGHT_WS` | `ws://playwright-server:3000/` |
| Browserless CDP endpoint | `AUTOFEED_BROWSERLESS_WS` | `ws://browserless:3000?token=…` |
| Scrapling-serve HTTP URL | `AUTOFEED_SCRAPLING_URL` | `http://scrapling-serve:8001` |
| RSS-Bridge URL | `AUTOFEED_RSS_BRIDGE_URL` | `http://rss-bridge:80` |
| Outbound auth token | `AUTOFEED_SERVICES_TOKEN` | any bearer token |

These are also configurable per-request from the FreshRSS Settings → AutoFeed → **External Services (advanced)** section (collapsed by default).

To start the optional browser containers alongside AutoFeed:

```bash
docker compose --profile with-external-browsers up -d
```

---

## Security

AutoFeed runs LLM-generated PHP that RSS-Bridge will execute. The defaults are sensible for a single-user home-lab setup but should be tightened for shared environments.

### Inbound authentication

Set `AUTOFEED_INBOUND_TOKEN` on the sidecar and the same value as **Sidecar auth token** in the extension Settings. When configured:

- Every mutating endpoint (`/discover`, `/analyze`, `/bridge/generate`, `/bridge/deploy`, `/scrape`, `/scrape/config`, `/preview`, `/graphql/*`, `/sftp/*`) requires `Authorization: Bearer <token>` — without it they return 401.
- GET endpoints used by the refresh scheduler (`/health`, `/feed/health`, `/scrape/feed`, `/graphql/feed`) remain open so FreshRSS's cron keeps working without per-request auth.

### CSRF

All mutating controller actions in the extension validate `_csrf` tokens against `FreshRSS_Auth::csrfToken()`. Requests without a valid token are rejected with `Minz_Request::bad()` and the user is sent back to the discovery page.

### CORS

The sidecar ships with `allow_origins=[]` by default (no CORS). The extension talks to the sidecar server-side via cURL, so browser origins don't need whitelisting. Loosen via `AUTOFEED_CORS_ORIGINS="https://fresh.example.com"` only when you're serving the *Test connection* button from a different host than the sidecar.

### Rate limiting

SlowAPI limits:

- 30 req/min per IP on mutating endpoints.
- 3 req/min per IP on `/discover` with `use_browser=true` (Playwright launches are expensive).

### Auto-deploy risks

Auto-deploy instructs the sidecar to write LLM-generated PHP directly to the bridge directory RSS-Bridge executes. Treat this the same as running arbitrary PHP:

- Only enable it if you control the LLM endpoint and trust its output.
- Review generated files in `./generated-bridges/` before restarting RSS-Bridge, especially if the LLM is public or shared.
- The sanity checker **blocks** `shell_exec`, `system()`, `passthru()`, `popen()`, `proc_open()`, `eval()`, `assert()`, `create_function()`, and `pcntl_exec()`. It **warns but allows** `file_get_contents`, `fopen`, `curl_*`, and `base64_decode` — these are normal RSS-Bridge idioms, but flag them for review if you didn't expect them.
- The sanity checker is **advisory, not a sandbox**. A determined adversarial LLM prompt could still produce PHP that evades the string checks (e.g. `$f='shell'.'_exec'; $f(...)`). Do not point AutoFeed at an untrusted LLM with auto-deploy on.

### LLM credentials

The API key lives in FreshRSS's user-configuration JSON on disk. It is masked on display (`sk-a…XYZ9`) and the settings form uses `autocomplete="new-password"` so browsers don't autofill it elsewhere. Submitting the masked placeholder leaves the stored key untouched.

---

## Running tests

```bash
cd sidecar
source .venv/bin/activate          # Python 3.10 – 3.12

# Full offline suite (fast, no network). 235 tests.
pytest tests -v

# Phase 1 integration (needs network, ~65 s)
pytest tests/test_integration.py -v --timeout=60

# Phase 2 browser tests (needs network + Playwright, ~60 s)
pytest tests/test_network_intercept.py tests/test_cascade_phase2.py \
       -v --timeout=120

# Everything
pytest tests -v --timeout=120
```

Test matrix (as of v0.6.0):

| Bucket | Count | Notable |
|---|---:|---|
| Scoring, embedded-JSON, skeleton, pruning, selectors | 56 | pure unit |
| RSS autodiscovery + heuristic XPath | 11 | offline |
| Network intercept + Phase 2 cascade | 11 | Playwright |
| LLM client + prompts + analyzer + JSON walker | 41 | respx-mocked |
| Bridge deploy + flow + contract | 20 | tmp_path |
| Scrape endpoint + adaptive + config | 20 | tmp_path |
| GraphQL detect + probe + Atom builder | 21 | offline |
| Preview endpoint | 4 | mocked |
| Dead-RSS handling | 4 | respx-mocked |
| Mixed-blocks / union selectors | 5 | offline |
| Inbound auth | 2 | header check |
| Scrape-config idempotency | 4 | tmp_path |
| Fetch dispatcher (bundled + remote) | 7 | mocked Playwright |
| Live network (opt-in) | 9 | real HTTP |

Run `pytest --co -q` to see every collected test.

---

## Troubleshooting

### The advertised RSS feed is broken

AutoFeed detects this automatically: the feed will show under **Advertised but not working** with the HTTP status and parse error, and the XPath candidates below remain available. You can also tick **Ignore advertised RSS** to re-run discovery as though no RSS was found, forcing the full Phase 2 cascade.

### The site has 12 items but AutoFeed shows 4

That usually means a "featured + main" layout. AutoFeed emits a **union candidate** (labelled *UNION*) combining both class groups — it captures all 12. If the union still doesn't appear, paste the HTML into a reproducer and open an issue; the detection heuristics can be extended.

### Preview is empty

- The candidate's title/link selectors may not resolve. Edit them in the candidate card and the preview refetches automatically (600 ms debounce).
- The page may be JS-rendered and you're previewing a Phase 1 candidate. Re-run discovery with advanced mode.
- The sidecar logs the raw HTML it fetched at `DEBUG` level: `docker compose logs autofeed-sidecar`.

### LLM returns 401 Unauthorized

Check that **LLM API key** matches the key your provider issued. For OpenAI it starts with `sk-`, for OpenRouter with `sk-or-`. Keys are sent as `Authorization: Bearer <key>` — no prefix needed in the settings field.

### LLM returns JSON parse errors (`LLMMalformed`)

Some providers (notably Ollama with certain models) ignore `response_format: {"type": "json_object"}` and return prose-wrapped or concatenated JSON. The sidecar has a **brace-balance walker** fallback that extracts the first balanced `{…}` object. If it still fails:

- Try a model with better instruction-following (e.g. `llama3.1` instead of `llama3`).
- For Ollama, confirm JSON mode: `ollama show <model> | grep json`.
- Set `LOG_LEVEL=debug` on the sidecar to see the raw content.

### LLM times out

Default timeouts: 60 s for `/analyze`, 90 s for `/bridge/generate`. The HTML skeleton is capped at 8 000 characters and each candidate summary at 1 500 characters before being concatenated into the prompt. Slow local models on big pages can still exceed this — try a smaller / quantised model.

### Generated bridge fails `php -l`

The sanity checker catches the most common problems. If the PHP still has syntax errors:

1. Copy the code from the bridge page.
2. Fix manually and paste into a new file in `./generated-bridges/`.
3. Run `php -l YourBridge.php` locally to confirm it's clean before restarting RSS-Bridge.

### Auto-deploy writes nothing

- Confirm **Auto-deploy bridges** is checked.
- Verify `./generated-bridges/` exists on the host and the sidecar container has write permission.
- Check sidecar logs: `docker compose logs autofeed-sidecar`.
- If `deploy_mode: remote_only` is set, check SFTP / RSS-Bridge URL credentials.

### Sidecar returns 401 on every request

`AUTOFEED_INBOUND_TOKEN` is set but the extension's **Sidecar auth token** is empty or different. Make them match, or unset the env var to disable auth (single-host dev only).

### Sidecar returns 429

You're hitting the rate limit. Default is 30/min per IP; browser-based discovery is 3/min. Wait or lower refresh frequency.

---

## Project structure

```
autofeed-freshrss/
├── docker-compose.yml
├── generated-bridges/                # Shared volume: sidecar writes, RSS-Bridge reads
├── xExtension-AutoFeed/              # FreshRSS extension (PHP)
│   ├── metadata.json
│   ├── extension.php                 # Hooks, config, sidecar HTTP client, auth header
│   ├── configure.phtml               # Settings UI (LLM + auto-deploy + SFTP + auth token)
│   ├── Controllers/
│   │   └── AutoFeedController.php    # discover / llmAnalyze / preview /
│   │                                 # bridgeGenerate / bridgeDeploy / apply actions,
│   │                                 # all with CSRF validation
│   ├── views/AutoFeed/
│   │   ├── discover.phtml            # URL input, advanced toggle, force_skip_rss,
│   │   │                             # override_xpath_item
│   │   ├── analyze.phtml             # Ranked candidates with Preview + Subscribe
│   │   ├── _preview_fragment.phtml   # Inline preview table (returned by previewAction)
│   │   └── bridge.phtml              # Generated PHP + Copy / Deploy / Subscribe
│   ├── static/
│   │   ├── autofeed.css
│   │   └── autofeed.js               # Spinner, clipboard, debounced preview fetch
│   └── i18n/en/ext.php
└── sidecar/                          # Python sidecar (FastAPI)
    ├── Dockerfile
    ├── pyproject.toml
    ├── requirements.txt
    ├── app/
    │   ├── main.py                   # FastAPI app + all endpoints, rate limiting,
    │   │                             # inbound-token check, CORS
    │   ├── models/schemas.py         # Pydantic models
    │   ├── discovery/
    │   │   ├── cascade.py            # Orchestrator (Phase 1 + 2 + skeleton)
    │   │   ├── rss_autodiscovery.py  # + liveness probe (HEAD/GET)
    │   │   ├── embedded_json.py
    │   │   ├── static_js_analysis.py
    │   │   ├── selector_generation.py # + union-selector pass
    │   │   ├── network_intercept.py  # Lazy semaphore factory
    │   │   ├── scrapling_selectors.py
    │   │   ├── graphql_detect.py
    │   │   ├── node_scoring.py
    │   │   └── scoring.py
    │   ├── utils/
    │   │   ├── skeleton.py           # HTML → compact DOM skeleton for LLM prompts
    │   │   └── tree_pruning.py
    │   ├── llm/
    │   │   ├── client.py             # Async httpx client + brace-balance JSON walker
    │   │   ├── prompts.py            # Strategy + bridge prompt templates (capped)
    │   │   └── analyzer.py           # recommend_strategy + generate_bridge
    │   │                             # + _sanity_check_php (hard / soft split)
    │   ├── bridge/
    │   │   ├── deploy.py             # Atomic file writer + _VALID_SLUG regex
    │   │   └── sftp_deploy.py        # asyncssh SFTP deployment
    │   ├── services/
    │   │   ├── config.py             # ServiceConfig + chosen_backend()
    │   │   ├── fetch.py              # 4 backends: bundled/playwright/browserless/scrapling
    │   │   └── discovery_cache.py    # 15-min TTL store for /discover payloads
    │   └── scraping/
    │       ├── config_store.py       # JSON-on-disk config store with post_process hook
    │       ├── scrape.py             # run_scrape + adaptive cache + drift detection
    │       └── rule_builder.py       # AutoScraper-port common-ancestor selector builder
    └── tests/
        ├── conftest.py               # Sets AUTOFEED_*_DIR tmp paths by default
        ├── test_scoring.py
        ├── test_embedded_json.py
        ├── test_rss_and_xpath.py
        ├── test_rss_deadfeed.py      # Dead-feed handling + force_skip_rss
        ├── test_scrapling_selectors.py
        ├── test_skeleton.py
        ├── test_llm_client.py
        ├── test_llm_json_split.py    # Brace-balance walker
        ├── test_analyzer.py
        ├── test_bridge_deploy.py
        ├── test_bridge_flow.py
        ├── test_bridge_contract.py   # bridge_name contract incl. Bridge suffix
        ├── test_fetch_dispatcher.py
        ├── test_fetch_dispatcher_remote.py
        ├── test_prompts.py
        ├── test_preview_endpoint.py
        ├── test_inbound_auth.py
        ├── test_mixed_blocks_xpath.py  # Union selectors
        ├── test_scrape_config.py
        ├── test_scrape_config_idempotent.py
        ├── test_scrape_adaptive.py
        ├── test_scrape_endpoint.py
        ├── test_graphql_detect.py
        ├── test_graphql_probe_endpoint.py
        ├── test_services_config.py
        ├── test_network_intercept.py  # online
        ├── test_integration.py        # online
        ├── test_cascade_phase1.py
        └── test_cascade_phase2.py     # online
```

---

## Roadmap

- **Phase 1** ✅ Core sidecar + discovery cascade + FreshRSS extension UI
- **Phase 2** ✅ Playwright network interception + Scrapling adaptive selector generation
- **Phase 3** ✅ LLM strategy selection + RSS-Bridge PHP generation + auto-deploy
- **Phase 4** ✅ Routine scraping with adaptive selectors + preview
- **Phase 5** ✅ GraphQL detection + probing + introspection
- **Tier 0–6 hardening** ✅ Bridge-name contract · remote-fetch semaphore · scrape-config idempotency · CSRF · inbound auth · CORS narrowing · rate limiting · dead-RSS handling · union selectors · preview endpoint · soft-vs-hard PHP warnings · brace-balance JSON parser
- **Future** 🟡 Pagination in `/scrape` · bookmarklet · RSS-Bridge `/deploy-bridge` proxy image · template-clustering for non-class-based repeats

---

## Requirements

- Docker and Docker Compose (recommended), or:
  - Python 3.10 – 3.12
  - Playwright Chromium (`playwright install chromium`) — only for Phase 2
  - FreshRSS 1.24.0+
  - PHP 7.4+ with cURL and `json` extensions

For SFTP deployment (Tier 3.3), `asyncssh` is bundled in `requirements.txt` — no extra setup needed.

---

## Compatibility

The extension is tested against FreshRSS **1.24.0–1.28.1** (stable releases).

It uses only the stable `Minz_Extension` API: string hook names,
`getUserConfigurationValue()` for reads, `setUserConfiguration(array)` for
writes, and no PHP namespaces. It does not depend on the typed-getter or
enum-hook additions present in the FreshRSS `edge` branch.

If you see PHP fatals like `Minz_HookType given` or `undefined method
AutoFeedExtension::getUserConfigurationString()`, your FreshRSS is newer
than expected (running `edge`) *and* someone has reverted the stable-API
rewrites in this extension. Re-check `extension.php` and
`configure.phtml` against this repo.

You can verify compatibility on your specific FreshRSS by running:

    docker exec freshrss php \
      /var/www/FreshRSS/extensions/xExtension-AutoFeed/tests/api_compat_check.php

Expected output: `OK — Minz_Extension has all methods this extension relies on.`

---

## License

AGPL-3.0, matching FreshRSS.