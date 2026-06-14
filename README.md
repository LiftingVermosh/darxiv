# Paper Research

A lightweight desktop application for tracking arXiv papers — subscribe to topics, periodically sync new papers, and manage your reading workflow with starred/read/hidden statuses, ratings, notes, and tags.

## Requirements

- **Python** 3.11+
- **Windows** 10+ (primary target), macOS, or Linux

## Quick Start (Developer)

```powershell
# 1. Clone & enter
git clone <repo-url> paper-research
cd paper-research

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1   # Windows
# source .venv/bin/activate   # macOS / Linux

# 3. Install in editable mode
pip install -e .

# 4. Run the app (dev mode — data stored in ./runtime/)
$env:PAPER_RESEARCH_DEV_MODE = "1"
python -m app.main
```

On first launch the app creates `runtime/db/paper_research.db` (schema auto-initialized) and `runtime/logs/paper_research.log`.

## Quick Start (Release / End-User)

Run the standalone executable:

```powershell
.\PaperResearch.exe
```

Data (database, logs) is stored in your platform's user data directory:

| Platform | Data location |
| --- | --- |
| Windows | `%APPDATA%\paper-research\` |
| macOS | `~/Library/Application Support/paper-research/` |
| Linux | `~/.local/share/paper-research/` |

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `PAPER_RESEARCH_DEV_MODE` | (off) | Set to `1` to use `./runtime/` as data root |
| `PAPER_RESEARCH_DB_PATH` | *(auto)* | Explicit SQLite database file path |
| `PAPER_RESEARCH_LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

## Running Tests

```powershell
# All tests
python -m pytest tests/ -v

# By slice
python -m pytest tests/test_slice_01_models.py -v
```

Tests use `:memory:` databases — no side effects on your real data.

## Smoke Test

Before releasing, run the smoke check:

```powershell
pwsh scripts/smoke.ps1
```

This verifies:

- All key modules are importable
- Runtime config resolves correctly in dev and release modes
- Database schema initializes cleanly
- Unit test suite passes

## Packaging

To create a standalone Windows executable:

```powershell
pip install pyinstaller
pwsh scripts/pack.ps1
```

Output: `dist/PaperResearch/PaperResearch.exe`

## Architecture

```text
app/
├── main.py                    # Entry point, AppContext assembly
├── domain/                    # Domain models (Paper, Subscription, …)
│   ├── models/
│   └── enums/
├── application/               # Service layer + DTOs
│   ├── services/              # Business logic (sync, query, status, …)
│   └── dto/                   # Data transfer objects for UI
├── infrastructure/            # External dependencies
│   ├── arxiv/                 # arXiv API client & Atom parser
│   ├── db/                    # SQLite connection, schema, repositories
│   ├── config/                # Runtime paths & app config
│   ├── logging/               # Centralized logging setup
│   └── scheduler/             # Background auto-sync scheduler
└── ui/                        # Flet UI layer
    ├── app_shell.py           # Main shell, routing, lifecycle
    ├── pages/                 # Dashboard, Subscriptions, Settings, Paper Detail
    └── components/            # Filter panel, status bar, notification bar
```

## Project Status

This is the **MVP v0.1.0** release — the last slice of the initial development plan focused on packaging, release hardening, and documentation. Future iterations may add LLM integration, PDF parsing, and cloud sync.
