---
name: AutoFeed standalone UI pivot
description: Repo is pivoting from a FreshRSS PHP extension to a self-contained web UI inside the sidecar
type: project
---

The FreshRSS extension (`xExtension-AutoFeed/`) accumulated too many compat bugs against the unstable `Minz_Extension` API (3 rounds of fixes). The decision was made to build a proper web UI directly in the `autofeed-sidecar` FastAPI service instead.

**Why:** Every FreshRSS upgrade risks breaking the extension again. The sidecar's discovery/scraping/LLM logic is solid; it just needs its own frontend.

**How to apply:** The extension is NOT being actively maintained anymore. New UI work goes into `sidecar/app/ui/` and `sidecar/app/static/`. The extension directory will be deleted in a final cleanup PR.

PR sequence:
- PR 1 (done): UI scaffold, home page, design system CSS — `app/ui/router.py`, `app/ui/templates/`, `app/static/`
- PR 2 (done): `settings_store.py`, settings page, API fallbacks to settings_store
- PR 3: Discovery results page with auto-preview, partials, candidate cards
- PR 4: Saved feeds page + save flow
- PR 5: Analyze + Bridge pages
- PR 6: Delete `xExtension-AutoFeed/`, update README
