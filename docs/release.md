# Release Notes — v0.1.0 (MVP)

## Overview

Paper Research v0.1.0 is the first deliverable desktop MVP for tracking arXiv papers. This release closes the initial development plan (Slices 01–11), forming a self-contained, installable, and verifiable application.

## What's Included

### Core Features

- **arXiv Paper Sync** — Subscribe to topics (by category, keyword, author, or raw query) and fetch new papers from the arXiv API.
- **Dashboard** — Browse your paper collection with keyword/category/status filters.
- **Paper Detail** — View full metadata (title, abstract, authors, categories, DOI, journal ref) with external links to arXiv and PDF.
- **Paper Status** — Mark papers as starred, read, or hidden; assign ratings (1–5 stars), notes, and tags.
- **Subscriptions Management** — Create, edit, delete, enable/disable, and manually trigger sync for individual subscriptions.
- **Auto-Sync Scheduler** — Background thread that periodically syncs all enabled subscriptions. Configurable global interval or per-subscription intervals.
- **Settings** — Toggle auto-sync on/off, choose sync interval preset, toggle hidden paper visibility.

### Engineering

- **Runtime Config** — Platform-aware data/log/db paths with dev-mode (`./runtime/`) and release-mode (`%APPDATA%`) strategies.
- **Centralized Logging** — File-based logging (DEBUG level, `paper_research.log`) with console stderr output (WARNING+). Suppresses noisy third-party loggers.
- **Startup Diagnostics** — Fatal errors during startup (DB init failure, path unwritable) are logged to file and shown as a visible error page in the UI.
- **Resource Lifecycle** — Ordered shutdown: scheduler → HTTP client → DB connection. Idempotent close, timeout-safe scheduler stop.
- **Test Suite** — 11 test modules (slices 01–10) covering domain models, repositories, arXiv integration, all services, UI components, scheduler, and query optimization. All tests use `:memory:` databases.

### Packaging

- `scripts/pack.ps1` — PyInstaller-based packaging for standalone Windows `.exe`.
- `scripts/smoke.ps1` — Automated smoke check (imports, config, DB init, unit tests).

## How to Verify

### On a developer machine

```powershell
# 1. Run the full test suite
python -m pytest tests/ -v

# 2. Run the smoke check
pwsh scripts/smoke.ps1

# 3. Launch the app in dev mode
$env:PAPER_RESEARCH_DEV_MODE = "1"
python -m app.main
```

### On a fresh machine (release candidate)

```powershell
# 1. Launch the executable
.\PaperResearch.exe

# 2. Verify:
#    - App window opens with title "Paper Research"
#    - Navigation bar shows Dashboard / Subscriptions / Settings
#    - Create a subscription → manual sync → papers appear on dashboard
#    - Open a paper → star/rate/note/tag work
#    - Settings page saves and controls auto-sync
#    - Closing the window → clean exit (check logs)
#    - Reopen → last page restored, data intact
```

## Data & Files

| What | Where (dev) | Where (release) |
|------|-------------|-----------------|
| Database | `./runtime/db/paper_research.db` | `%APPDATA%\paper-research\db\paper_research.db` |
| Logs | `./runtime/logs/paper_research.log` | `%APPDATA%\paper-research\logs\paper_research.log` |
| Config | `PAPER_RESEARCH_*` env vars | same |

## Known Limitations

- **Flet 0.85.x** pinned — requires this specific version range. Future Flet upgrades may need API migration.
- **Single instance** — no multi-instance guard; two processes using the same DB file may conflict.
- **No PDF download** — the "Open PDF" button opens the arXiv PDF URL in the default browser.
- **Windows primary** — tested primarily on Windows; macOS/Linux should work but may have minor platform-specific issues.
- **No LLM / AI features** — these are planned for post-MVP iterations.

## Breaking Changes from Development

None — this is the initial release.

## Dependencies

```
flet>=0.85,<0.86
httpx>=0.28,<0.29
feedparser>=6.0.12,<7
pydantic>=2.7,<3
python-dateutil>=2.8.2,<3
python-dotenv>=1.0,<2
```

## Next Steps (Post-MVP)

- Python 3.12+ support and Flet version upgrade
- LLM-powered paper summarization
- PDF full-text parsing
- Cloud sync (multi-device)
- CI/CD pipeline
- macOS / Linux packaging
