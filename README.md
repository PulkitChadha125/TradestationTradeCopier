# TradeStation Trade Copier

A Flask web application that mirrors orders from a **master TradeStation account** to a **client TradeStation account**, with account login automation, position/tradebook views, and CSV-based order logging.

## Project Details

This project provides a local web dashboard to:
- configure TradeStation API credentials for global, master, and client contexts
- automate OAuth login (including TOTP) with Selenium to retrieve refresh tokens
- monitor master account orders and copy new orders to the client account
- view master/client positions and tradebook data
- inspect copied order logs and latency metrics

Core stack:
- Python 3 + Flask backend
- TradeStation REST API integration via `requests`
- Selenium + `pyotp` for automated login
- Jinja templates + vanilla JavaScript frontend
- CSV files in `data/` for persistent settings and logs

## Features

- Copy trading controls (start/stop/status) from the UI
- Separate master and client account configuration
- Automated OAuth login flow with TOTP support
- Auto-detection of account ID after successful login
- Account balance cards (cash, buying power, equity)
- Master/client live positions pages
- Master/client tradebook pages (current + historical orders)
- Order log with per-order detail modal and latency values
- White and maroon themed dashboard UI

## Installation

### 1) Prerequisites

- Windows (project is currently configured with `chromedriver.exe` in repo root)
- Python 3.10+ recommended
- Google Chrome installed
- TradeStation developer credentials:
  - API Key
  - API Secret
  - account credentials for master and client users
  - TOTP secret for each account

### 2) Clone and create virtual environment

```powershell
git clone <your-repo-url>
cd TradeStationTradeCopier
python -m venv .venv
.venv\Scripts\activate
```

### 3) Install dependencies

```powershell
pip install -r requirements.txt
```

### 4) Ensure ChromeDriver compatibility

The app uses `chromedriver.exe` at the project root. Make sure it matches your local Chrome version.

### 5) Run the app

```powershell
python app.py
```

Open:
- `http://127.0.0.1:5000` (or `http://localhost:5000`)

On first run, the app creates:
- `data/` (for API settings and order logs)
- `logs/` (reserved for runtime logs)

## How to Use

1. Open **API Settings**.
2. Save **Global API Key/Secret**.
3. Save **Master** and **Client** account credentials (User ID, Password, TOTP).
4. Click **Login** for each account (automation will open Chrome and complete OAuth).
5. Confirm both accounts are logged in and balances are visible.
6. Start copy trading with **Start Copy Trading**.
7. Monitor copied trades in **Order Log** and account pages.

## File Guide (What Each File Does)

### Root

- `app.py` - Main Flask application: routes, settings persistence, login endpoints, copier controls, and page rendering.
- `requirements.txt` - Python dependencies.
- `chromedriver.exe` - Local ChromeDriver binary used by Selenium automation.
- `Notes.txt` - Project notes and initial requirements/spec references.

### `utils/`

- `utils/tradestation_api.py` - TradeStation API client wrapper (auth, balances, positions, orders, historical orders, place order).
- `utils/trade_copier.py` - Background copy engine: polls master orders, deduplicates, and places mirrored orders in client account.
- `utils/oauth_automation.py` - Selenium workflow for TradeStation login + TOTP + OAuth code extraction.
- `utils/__init__.py` - Package marker for `utils`.

### `templates/`

- `templates/base.html` - Shared layout, sidebar navigation, and copier footer controls.
- `templates/api_settings.html` - Credential management, account login actions, and balance cards.
- `templates/master_position.html` - Master account positions table.
- `templates/client_position.html` - Client account positions table.
- `templates/order_log.html` - Copied order history table with modal details.
- `templates/tradebook_master.html` - Master account tradebook view.
- `templates/tradebook_client.html` - Client account tradebook view.

### `static/`

- `static/css/style.css` - Global styling (white/maroon theme, layout, cards, tables, modal, responsiveness).
- `static/js/main.js` - Frontend JS for copier controls, status polling, and shared interactions.

### `samplecode/`

- `samplecode/README.md` - Notes for TradeStation API sample scripts.
- `samplecode/async_data_pull.py` - Example async market data pull script (not required to run the web app).
- `samplecode/ts_api_demo.ipynb` - Notebook with API exploration/demo.

## API/Storage Notes

- API/account settings are saved to `data/api_settings.csv`.
- Order copy logs are saved to `data/order_log.csv`.
- The app currently stores sensitive fields (passwords, tokens, secrets) in plain CSV for convenience.
  - For production use, replace with encrypted secret storage.

## Troubleshooting

- Login fails:
  - confirm API key/secret are valid
  - confirm user credentials and TOTP secrets are correct
  - ensure `chromedriver.exe` matches installed Chrome
- No positions or balances:
  - verify both account login status and account IDs
  - check whether environment is `paper` vs live account context
- Copier does not copy:
  - ensure both master and client have valid refresh tokens
  - verify copier is running from UI and API calls are succeeding

## References

- [TradeStation API docs](https://api.tradestation.com/docs/)
- [TradeStation authentication overview](https://api.tradestation.com/docs/fundamentals/authentication/auth-overview/)
