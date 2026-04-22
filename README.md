# TradeStation Trade Copier

A Flask dashboard that mirrors orders from a **master TradeStation account** to a **client TradeStation account** using order-book polling, OAuth automation, and CSV-backed settings/logs.

## Current App Entry Point

This repository has multiple app files, but the active order-book workflow uses:

- `app_modified.py` (recommended)

You can launch it with either:

- `python app_modified.py`
- `run_tradecopier_app_modified.bat` (auto-creates `.venv`, installs requirements, opens browser, runs app)

## Features

- Start/Stop trading controls in UI
- Stop Trading is a disconnect action that clears active sessions/cache for safe environment switching
- Master/client credentials with automated OAuth + TOTP login
- Broker account ID auto-fetch after login (manual account ID entry not required)
- Per-account environment selection: `paper` or `live`
- Session reuse with environment/account safety checks
- Master/client balances and positions views
- Auto-relogin on position-fetch auth failures (with cooldown) for long-running hosted usage
- Order-book copier with order mirror logging
- Duration passthrough support (with TradeStation duration normalization)

## Prerequisites

- Windows
- Python 3.10+ recommended
- Google Chrome installed
- `chromedriver.exe` in repo root and compatible with local Chrome
- TradeStation API credentials:
  - API Key
  - API Secret
  - Master and client user credentials (User ID, Password, TOTP secret)

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

### Option A (recommended launcher)

```powershell
run_tradecopier_app_modified.bat
```

### Option B (manual)

```powershell
python app_modified.py
```

Open `http://127.0.0.1:5000`.

## Usage Workflow

1. Open **API Settings**.
2. Save **Global API Key/Secret**.
3. For Master and Client, save:
   - User ID
   - Password
   - TOTP
   - Environment (`Paper Trading` or `Live Trading`)
4. Click **Login** (Chrome automation completes OAuth).
5. App auto-fetches matching broker account ID for selected environment.
6. Click **Start Trading**.
7. Keep the positions page open for live polling; backend auto-relogin handles auth-expiry scenarios.

### Switching Paper <-> Live Safely

1. Click **Stop Trading** (stops copier if running and clears session cache/login state).
2. Change environment.
3. Save credentials.
4. Login again.
5. Start Trading again.

## Auto Login / Token Expiry Behavior

- Access tokens are reused while valid.
- If token expires, refresh-token flow is attempted automatically by backend login paths.
- During continuous position polling, if auth fails, backend triggers auto-relogin and retries once.
- A cooldown prevents repeated relogin loops (`30s` between auto-relogin attempts per account).

## Important Environment Rules

- `paper` uses `https://sim-api.tradestation.com/v3`
- `live` uses `https://api.tradestation.com/v3`
- OAuth token endpoint remains `https://signin.tradestation.com/oauth/token`
- If a SIM account is used in live environment, TradeStation returns:
  - `403 Forbidden: Invalid Account ID(s).`

## Data Files

- `data/api_settings.csv` - global/master/client settings
- `data/session_tokens.json` - cached access-token sessions
- `data/order_log_orderbook.csv` - copier order log

## Key Files

- `app_modified.py` - primary Flask app, login/session handling, copier endpoints
- `utils/tradestation_api.py` - TradeStation REST client wrapper
- `utils/oauth_automation.py` - Selenium OAuth/TOTP automation
- `templates/api_settings.html` - credentials + login + trading controls UI
- `static/js/main.js` - shared start/stop status logic
- `run_tradecopier_app_modified.bat` - launcher for `app_modified.py`

## Troubleshooting

- **Login succeeds but balances fail with `Invalid Account ID(s)`**
  - Environment/account mismatch (SIM account in live mode or vice versa)
  - Click **Stop Trading**, switch environment, save, login again
- **Login automation fails**
  - Verify API key/secret, credentials, TOTP
  - Verify `chromedriver.exe` matches Chrome version
- **Orders not copying**
  - Ensure both accounts are logged in
  - Ensure trading is running
  - Check `data/order_log_orderbook.csv` and terminal logs
- **Hosted app runs for long time and session expires**
  - Positions endpoint now attempts auto-relogin and retry on auth failure
  - If still failing, verify refresh token validity and TradeStation credentials

## References

- [TradeStation API docs](https://api.tradestation.com/docs/)
- [SIM vs LIVE](https://api.tradestation.com/docs/fundamentals/sim-vs-live/)
- [Auth overview](https://api.tradestation.com/docs/fundamentals/authentication/auth-overview/)
