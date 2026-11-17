# iPhone Duo tracker

Tracks which apps mention "iPhone Duo" in the release notes of their current App Store version,
and publishes a static dashboard to GitHub Pages.

- `duo.py` — the tracker (standard library only). `check` polls the iTunes Lookup API and updates
  `watchlist.json`; `export` writes `site/apps.json`.
- `site/index.html` — the dashboard. Plain HTML and JS that reads `apps.json`.
- `.github/workflows/refresh.yml` — runs daily: check, export, commit changed state, deploy Pages.
- `watchlist.json`, `meta.json` — tracker state, committed by the workflow.

Run locally: `python3 duo.py check && python3 duo.py export && python3 -m http.server -d site`.
