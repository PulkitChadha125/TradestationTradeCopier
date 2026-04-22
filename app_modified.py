from flask import Flask, jsonify, request, render_template, redirect, url_for
import csv
import json
import os
import re
import threading
import time
import uuid
import copy
import requests
from datetime import datetime, timedelta

from utils.oauth_automation import OAuthAutomation
from utils.tradestation_api import TradeStationAPI

app = Flask(__name__)
app.config["SECRET_KEY"] = "your-secret-key-here"

os.makedirs("data", exist_ok=True)

API_SETTINGS_FILE = "data/api_settings.csv"
ORDER_BOOK_LOG_FILE = "data/order_log_orderbook.csv"
TOKEN_CACHE_FILE = "data/session_tokens.json"

orderbook_copier = None
copier_thread = None
copier_running = False
copier_lock = threading.Lock()

login_status = {
    "master": {"logged_in": False, "error": None},
    "client": {"logged_in": False, "error": None},
}

ORDER_BOOK_LOG_FIELDS = [
    "timestamp",
    "order_id",
    "copier_order_id",
    "master_order_id",
    "client_order_id",
    "event_type",
    "action",
    "symbol",
    "quantity",
    "trade_action",
    "order_type",
    "limit_price",
    "master_status",
    "client_status",
    "error",
    "notes",
]

OPEN_ORDER_STATUSES = {
    "ACK",
    "QUEUED",
    "RECEIVED",
    "SENT",
    "WORKING",
    "OPEN",
    "ACCEPTED",
    "PARTIALLYFILLED",
    "PARTIALFILL",
}
CANCEL_MIRROR_STATUSES = {"CANCELLED", "CANCELED", "EXPIRED", "REJECTED", "OUT", "CXL"}


def ensure_order_book_log_file():
    if not os.path.exists(ORDER_BOOK_LOG_FILE):
        with open(ORDER_BOOK_LOG_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=ORDER_BOOK_LOG_FIELDS)
            writer.writeheader()


def log_orderbook_event(event):
    ensure_order_book_log_file()
    with open(ORDER_BOOK_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ORDER_BOOK_LOG_FIELDS)
        writer.writerow({k: event.get(k, "") for k in ORDER_BOOK_LOG_FIELDS})


def load_token_cache():
    if not os.path.exists(TOKEN_CACHE_FILE):
        return {"master": {}, "client": {}}
    try:
        with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"master": {}, "client": {}}
            data.setdefault("master", {})
            data.setdefault("client", {})
            return data
    except Exception:
        return {"master": {}, "client": {}}


def save_token_cache(cache):
    payload = cache if isinstance(cache, dict) else {"master": {}, "client": {}}
    payload.setdefault("master", {})
    payload.setdefault("client", {})
    with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _today_date_text():
    return datetime.now().date().isoformat()


def _normalize_environment(value):
    return "live" if str(value or "").strip().lower() == "live" else "paper"


def _pick_account_id_for_environment(accounts_payload, environment):
    env = _normalize_environment(environment)
    candidates = []
    if isinstance(accounts_payload, list):
        candidates = [x for x in accounts_payload if isinstance(x, dict)]
    elif isinstance(accounts_payload, dict):
        possible_lists = [
            accounts_payload.get("Accounts"),
            accounts_payload.get("accounts"),
            accounts_payload.get("Items"),
        ]
        for item in possible_lists:
            if isinstance(item, list):
                candidates = [x for x in item if isinstance(x, dict)]
                break
        if not candidates:
            if any(k in accounts_payload for k in ("AccountID", "Account", "accountId", "account")):
                candidates = [accounts_payload]

    ids = []
    for acct in candidates:
        aid = str(acct.get("AccountID") or acct.get("Account") or acct.get("accountId") or acct.get("account") or "").strip()
        if aid:
            ids.append(aid)
    if not ids:
        return ""

    if env == "paper":
        sim_ids = [x for x in ids if x.upper().startswith("SIM")]
        return sim_ids[0] if sim_ids else ids[0]

    live_ids = [x for x in ids if not x.upper().startswith("SIM")]
    return live_ids[0] if live_ids else ids[0]


def _account_id_matches_environment(account_id, environment):
    aid = str(account_id or "").strip()
    if not aid:
        return False
    env = _normalize_environment(environment)
    is_sim = aid.upper().startswith("SIM")
    return is_sim if env == "paper" else (not is_sim)


def _resolve_account_id_if_missing(account_type, account_settings, api_key, api_secret, access_token="", token_expiry=0):
    if account_type not in {"master", "client"}:
        return account_settings.get("account_id", "")
    existing_account_id = str(account_settings.get("account_id", "")).strip()
    env = _normalize_environment(account_settings.get("environment", "paper"))
    needs_resolution = (not existing_account_id) or (not _account_id_matches_environment(existing_account_id, env))
    if not needs_resolution:
        return existing_account_id

    try:
        lookup_api = ModifiedTradeStationAPI(
            client_id=api_key,
            client_secret=api_secret,
            account_id="",
            environment=env,
            refresh_token=account_settings.get("refresh_token", ""),
        )
        if access_token and float(token_expiry or 0) > (time.time() + 30):
            lookup_api.access_token = access_token
            lookup_api.token_expiry = float(token_expiry)
        else:
            apply_cached_access_token(account_type, lookup_api)
        lookup_api.ensure_authenticated()
        accounts_payload = lookup_api.get_account_info()
        resolved = _pick_account_id_for_environment(accounts_payload, env)
        if resolved:
            account_settings["account_id"] = resolved
            save_account_credentials(account_type, account_settings)
            cache_account_session(
                account_type=account_type,
                access_token=lookup_api.access_token,
                token_expiry=lookup_api.token_expiry,
                account_id=resolved,
                environment=env,
            )
            return resolved
    except Exception as e:
        print(f"[ACCOUNT RESOLVE] Failed to auto-detect account id for {account_type}: {e}")
    return ""


def cache_account_session(account_type, access_token, token_expiry, login_time=None, account_id="", environment="paper"):
    if account_type not in {"master", "client"}:
        return
    cache = load_token_cache()
    cache[account_type] = {
        "access_token": access_token or "",
        "token_expiry": float(token_expiry or 0),
        "last_login_time": login_time or datetime.now().isoformat(),
        "login_date": _today_date_text(),
        "account_id": str(account_id or "").strip(),
        "environment": _normalize_environment(environment),
    }
    save_token_cache(cache)


def _session_is_usable_today(session):
    if not isinstance(session, dict):
        return False
    if session.get("login_date") != _today_date_text():
        return False
    access_token = session.get("access_token")
    token_expiry = float(session.get("token_expiry") or 0)
    return bool(access_token) and token_expiry > (time.time() + 30)


def _session_matches_context(session, account_id="", environment="paper"):
    if not isinstance(session, dict):
        return False
    cached_env = _normalize_environment(session.get("environment"))
    current_env = _normalize_environment(environment)
    cached_account_id = str(session.get("account_id") or "").strip()
    current_account_id = str(account_id or "").strip()
    return cached_env == current_env and cached_account_id == current_account_id


def apply_cached_access_token(account_type, api):
    if account_type not in {"master", "client"} or not api:
        return False
    session = load_token_cache().get(account_type, {})
    if not _session_is_usable_today(session):
        return False
    # Avoid cross-environment/account token reuse when users switch settings.
    if not _session_matches_context(
        session,
        account_id=getattr(api, "account_id", ""),
        environment=getattr(api, "environment", "paper"),
    ):
        return False
    api.access_token = session.get("access_token")
    api.token_expiry = float(session.get("token_expiry") or 0)
    return True


def clear_cached_session(account_type):
    if account_type not in {"master", "client"}:
        return
    cache = load_token_cache()
    cache[account_type] = {}
    save_token_cache(cache)


def load_api_settings():
    settings = {"global": {}, "master": {}, "client": {}}
    if os.path.exists(API_SETTINGS_FILE):
        with open(API_SETTINGS_FILE, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_type = row.get("type", "").lower()
                if row_type == "global":
                    settings["global"] = {
                        "api_key": row.get("api_key", ""),
                        "api_secret": row.get("api_secret", ""),
                    }
                elif row_type in ["master", "client"]:
                    settings[row_type] = {
                        "user_id": row.get("user_id", ""),
                        "password": row.get("password", ""),
                        "totp": row.get("totp", ""),
                        "refresh_token": row.get("refresh_token", ""),
                        "account_id": row.get("account_id", ""),
                        "environment": row.get("environment", "paper"),
                    }
    return settings


def _write_settings_to_csv(settings):
    with open(API_SETTINGS_FILE, "w", newline="") as f:
        fieldnames = [
            "type", "api_key", "api_secret", "user_id", "password", "totp", "refresh_token", "account_id", "environment"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        if settings.get("global"):
            writer.writerow(
                {
                    "type": "global",
                    "api_key": settings["global"].get("api_key", ""),
                    "api_secret": settings["global"].get("api_secret", ""),
                    "user_id": "",
                    "password": "",
                    "totp": "",
                    "refresh_token": "",
                    "account_id": "",
                    "environment": "",
                }
            )
        for acc_type in ["master", "client"]:
            if settings.get(acc_type):
                writer.writerow(
                    {
                        "type": acc_type,
                        "api_key": "",
                        "api_secret": "",
                        "user_id": settings[acc_type].get("user_id", ""),
                        "password": settings[acc_type].get("password", ""),
                        "totp": settings[acc_type].get("totp", ""),
                        "refresh_token": settings[acc_type].get("refresh_token", ""),
                        "account_id": settings[acc_type].get("account_id", ""),
                        "environment": settings[acc_type].get("environment", "paper"),
                    }
                )


def save_global_credentials(api_key, api_secret):
    settings = load_api_settings()
    settings["global"] = {"api_key": api_key, "api_secret": api_secret}
    _write_settings_to_csv(settings)


def save_account_credentials(account_type, credentials):
    settings = load_api_settings()
    settings[account_type] = credentials
    _write_settings_to_csv(settings)


class ModifiedTradeStationAPI(TradeStationAPI):
    def _normalize_orders_payload(self, payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            if isinstance(payload.get("Orders"), list):
                return payload.get("Orders", [])
            if isinstance(payload.get("orders"), list):
                return payload.get("orders", [])
        return []

    def fetch_orders(self, since_date=None):
        self.ensure_authenticated()
        url = f"{self.base_url}/brokerage/accounts/{self.account_id}/orders"
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        params = {"since": since_date} if since_date else {}
        response = requests.get(url, headers=headers, params=params, timeout=10)
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError:
            # Some accounts/environments reject the since filter; retry once without it.
            if since_date:
                response = requests.get(url, headers=headers, timeout=10)
                response.raise_for_status()
            else:
                raise
        return self._normalize_orders_payload(response.json())

    def fetch_historical_orders(self, since_date=None):
        self.ensure_authenticated()
        url = f"{self.base_url}/brokerage/accounts/{self.account_id}/historicalorders"
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        params = {"since": since_date} if since_date else {}
        response = requests.get(url, headers=headers, params=params, timeout=10)
        try:
            response.raise_for_status()
            return self._normalize_orders_payload(response.json())
        except requests.exceptions.HTTPError as e:
            # Keep copier alive even if historical endpoint rejects filters.
            if since_date:
                try:
                    response = requests.get(url, headers=headers, timeout=10)
                    response.raise_for_status()
                    return self._normalize_orders_payload(response.json())
                except Exception:
                    pass
            detail = ""
            if e.response is not None:
                detail = e.response.text
            print(f"[ORDERBOOK COPIER] Historical orders fetch skipped: {e}. {detail}")
            return []

    def cancel_order(self, order_id):
        self.ensure_authenticated()
        url = f"{self.base_url}/orderexecution/orders/{order_id}"
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        try:
            response = requests.delete(url, headers=headers, timeout=10)
            response.raise_for_status()
            payload = {}
            try:
                payload = response.json()
            except Exception:
                payload = {"raw": response.text}
            return {"success": True, "response": payload}
        except Exception as e:
            error_msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    error_msg = str(e.response.json())
                except Exception:
                    error_msg = e.response.text or str(e)
            return {"success": False, "error": error_msg}


class OrderBookTradeCopier:
    def __init__(self, settings, log_callback):
        self.log_callback = log_callback
        self.running = False
        self.last_orderbook_print_ts = 0.0
        self.order_map = {}  # master_order_id -> data
        self.baseline_order_ids = set()
        self.cancel_states = set(CANCEL_MIRROR_STATUSES)
        self.working_states = set(OPEN_ORDER_STATUSES)
        # Master signed position qty by symbol; used to detect closes / scale-downs.
        self.last_master_positions_qty = {}
        self._positions_snapshot_initialized = False

        g = settings.get("global", {})
        m = settings.get("master", {})
        c = settings.get("client", {})
        self.master_api = ModifiedTradeStationAPI(
            client_id=g.get("api_key", ""),
            client_secret=g.get("api_secret", ""),
            account_id=m.get("account_id", ""),
            environment=m.get("environment", "paper"),
            refresh_token=m.get("refresh_token", ""),
        )
        self.client_api = ModifiedTradeStationAPI(
            client_id=g.get("api_key", ""),
            client_secret=g.get("api_secret", ""),
            account_id=c.get("account_id", ""),
            environment=c.get("environment", "paper"),
            refresh_token=c.get("refresh_token", ""),
        )
        apply_cached_access_token("master", self.master_api)
        apply_cached_access_token("client", self.client_api)

    def _normalize_order_id(self, value):
        return str(value or "").strip()

    def _extract_order_id(self, order):
        return self._normalize_order_id(
            order.get("OrderID") or order.get("orderId") or order.get("ID") or order.get("Id") or ""
        )

    def _extract_client_order_id(self, order_response):
        if not isinstance(order_response, dict):
            return ""
        for key in ("OrderID", "orderID", "OrderId", "orderId", "ID", "Id"):
            value = order_response.get(key)
            if value:
                return self._normalize_order_id(value)
        if isinstance(order_response.get("Orders"), list) and order_response["Orders"]:
            first = order_response["Orders"][0]
            if isinstance(first, dict):
                return self._extract_client_order_id(first)
        return ""

    def _extract_duration(self, order):
        raw_tif = order.get("TimeInForce") or order.get("timeInForce") or {}
        duration = ""
        if isinstance(raw_tif, dict):
            duration = raw_tif.get("Duration") or raw_tif.get("duration") or ""
        elif isinstance(raw_tif, str):
            duration = raw_tif
        if not duration:
            duration = order.get("Duration") or order.get("duration") or "DAY"
        return str(duration).strip().upper()

    def _extract_passthrough_order_fields(self, order):
        # Copy request-safe fields from master order so client order preserves behavior.
        passthrough = {}
        for key in ("TimeInForce", "Route", "StopPrice", "AdvancedOptions"):
            value = order.get(key)
            if value in (None, ""):
                continue
            passthrough[key] = copy.deepcopy(value)
        return passthrough

    def _signature(self, order):
        first_leg = {}
        legs = order.get("Legs")
        if isinstance(legs, list) and legs and isinstance(legs[0], dict):
            first_leg = legs[0]

        qty = (
            order.get("Quantity")
            or order.get("quantity")
            or first_leg.get("Quantity")
            or first_leg.get("QuantityOrdered")
            or 0
        )
        try:
            qty_val = int(float(qty))
        except Exception:
            qty_val = 0
        return {
            "symbol": str(order.get("Symbol") or first_leg.get("Symbol") or ""),
            "qty": abs(qty_val),
            "trade_action": str(
                order.get("TradeAction")
                or order.get("BuyOrSell")
                or first_leg.get("TradeAction")
                or first_leg.get("BuyOrSell")
                or ""
            ).upper(),
            "open_or_close": str(order.get("OpenOrClose") or first_leg.get("OpenOrClose") or "").upper(),
            "asset_type": str(order.get("AssetType") or first_leg.get("AssetType") or "").upper(),
            "order_type": str(order.get("OrderType", "Market")),
            "duration": self._extract_duration(order),
            "limit_price": str(order.get("LimitPrice", "")) if order.get("LimitPrice") is not None else "",
            "stop_price": str(order.get("StopPrice", "")) if order.get("StopPrice") is not None else "",
            "passthrough_order_fields": self._extract_passthrough_order_fields(order),
        }

    def _is_order_currently_open(self, order):
        status = str(order.get("Status", "")).upper()
        if status in self.cancel_states or status in {"REJ", "OUT", "FLL", "FILLED"}:
            return False
        if status in self.working_states:
            return True
        remaining_qty = _order_remaining_qty(order)
        has_closed_time = bool(_order_filled_or_cancelled_time(order))
        return remaining_qty > 0 and not has_closed_time

    def _order_looks_filled(self, order):
        status = str(order.get("Status", "")).upper()
        status_desc = str(order.get("StatusDescription") or order.get("statusDescription") or "").upper()
        if status in {"FLL", "FILLED", "FIL", "FULLYFILLED", "COMPLETE", "DONE"}:
            return True
        if "FILLED" in status_desc:
            return True
        # Some broker payloads use OUT for terminal states; only treat as filled
        # when quantities indicate execution happened.
        try:
            filled_qty = float(order.get("FilledQuantity") or 0)
        except Exception:
            filled_qty = 0.0
        remaining_qty = _order_remaining_qty(order)
        return filled_qty > 0 and remaining_qty <= 0

    def _should_copy_fast_filled_master_order(self, order):
        """If an order is already filled by the time we poll, still replicate it once."""
        status = str(order.get("Status", "")).upper()
        if status in {"REJ", "REJECTED"}:
            return False
        if status in self.working_states:
            return False
        if status in self.cancel_states and not self._order_looks_filled(order):
            return False
        return self._order_looks_filled(order)

    def _combine_master_orders(self):
        combined = {}
        # Fetch open/working orders without restrictive date filtering.
        try:
            for order in self.master_api.fetch_orders():
                oid = self._extract_order_id(order)
                if oid:
                    combined[oid] = order
        except Exception as e:
            print(f"[ORDERBOOK COPIER] Active orders fetch failed: {e}")

        # Fetch recent historical orders for cancel/filled state transitions.
        historical_since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        for order in self.master_api.fetch_historical_orders(since_date=historical_since):
            oid = self._extract_order_id(order)
            if oid:
                combined[oid] = order
        return combined

    def _signed_qty_from_sig(self, sig):
        return abs(int(sig.get("qty", 0))) if sig.get("trade_action") in {"BUY", "BUYTOCOVER"} else -abs(int(sig.get("qty", 0)))

    def _symbol_looks_option(self, symbol, asset_type=""):
        sym_u = str(symbol or "").upper().strip()
        at_u = str(asset_type or "").upper()
        option_symbol_like = bool(re.search(r"\s\d{6}[CP]\d", sym_u))
        return ("OPTION" in at_u) or option_symbol_like

    def _positions_list_to_map(self, positions_list):
        """Merge API positions into symbol -> {qty (signed float), asset_type}."""
        out = {}
        if not isinstance(positions_list, list):
            return out
        for p in positions_list:
            if not isinstance(p, dict):
                continue
            sym = str(p.get("Symbol") or p.get("symbol") or "").strip()
            if not sym:
                continue
            raw_q = p.get("Quantity") or p.get("quantity") or 0
            try:
                qf = float(raw_q)
            except (TypeError, ValueError):
                qf = 0.0
            at = str(p.get("AssetType") or p.get("assetType") or "").upper()
            if sym in out:
                out[sym]["qty"] += qf
                if at and not out[sym].get("asset_type"):
                    out[sym]["asset_type"] = at
            else:
                out[sym] = {"qty": qf, "asset_type": at}
        return out

    def _closing_signature_for_master_reduction(self, symbol, qty, master_was_long, asset_type=""):
        """Build a signature that closes long (SELL*) or covers short (BUY* / BUYTOCOVER) on the client."""
        is_opt = self._symbol_looks_option(symbol, asset_type)
        sig = {
            "symbol": symbol,
            "qty": max(1, int(round(abs(qty)))),
            "order_type": "Market",
            "limit_price": "",
            "asset_type": asset_type,
            "open_or_close": "",
        }
        if master_was_long:
            sig["trade_action"] = "SELL"
            sig["open_or_close"] = "CLOSE" if is_opt else ""
        else:
            if is_opt:
                sig["trade_action"] = "BUY"
                sig["open_or_close"] = "CLOSE"
            else:
                sig["trade_action"] = "BUYTOCOVER"
        return sig

    def _mirror_master_position_closes(self):
        """When master position size drops (or flips toward flat), mirror the reduction on the client."""
        try:
            master_list = self.master_api.get_positions()
            client_list = self.client_api.get_positions()
        except Exception as e:
            print(f"[ORDERBOOK COPIER] Position sync skipped (fetch error): {e}")
            return

        master_map = self._positions_list_to_map(master_list)
        client_map = self._positions_list_to_map(client_list)

        if not self._positions_snapshot_initialized:
            self.last_master_positions_qty = {s: float(d["qty"]) for s, d in master_map.items()}
            self._positions_snapshot_initialized = True
            print(
                f"[ORDERBOOK COPIER] Position snapshot initialized ({len(self.last_master_positions_qty)} symbols). "
                "Subsequent master position reductions will be mirrored on the client."
            )
            return

        symbols = set(self.last_master_positions_qty.keys()) | set(master_map.keys())
        snapshot_retry_syms = set()

        for sym in symbols:
            prev_qty = float(self.last_master_positions_qty.get(sym, 0.0))
            curr_qty = float(master_map.get(sym, {}).get("qty", 0.0))
            asset_type = str(master_map.get(sym, {}).get("asset_type") or client_map.get(sym, {}).get("asset_type") or "")

            client_qty = float(client_map.get(sym, {}).get("qty", 0.0))

            if prev_qty > 0 and curr_qty < prev_qty:
                master_long_closed = min(prev_qty, prev_qty - curr_qty)
                if master_long_closed <= 0:
                    continue
                avail = client_qty if client_qty > 0 else 0.0
                to_close = int(round(min(master_long_closed, avail)))
                if to_close <= 0:
                    continue
                sig = self._closing_signature_for_master_reduction(sym, to_close, master_was_long=True, asset_type=asset_type)
                print(
                    f"[ORDERBOOK COPIER] MASTER LONG REDUCED | symbol={sym} prev={prev_qty} curr={curr_qty} "
                    f"mirroring client SELL/close qty={to_close}"
                )
                client_result = self._place_from_signature(sig)
                copier_id = f"POSMAP-{uuid.uuid4().hex[:10].upper()}"
                self.log_callback(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "order_id": copier_id,
                        "copier_order_id": copier_id,
                        "master_order_id": "",
                        "client_order_id": self._extract_client_order_id(client_result.get("order", {}))
                        if client_result.get("success")
                        else "",
                        "event_type": "MASTER_POSITION_CLOSE_MIRRORED",
                        "action": "sell",
                        "symbol": sym,
                        "quantity": to_close,
                        "trade_action": sig.get("trade_action", ""),
                        "order_type": "Market",
                        "limit_price": "",
                        "master_status": f"pos {prev_qty}->{curr_qty}",
                        "client_status": "SUCCESS" if client_result.get("success") else "FAILED",
                        "error": client_result.get("error", ""),
                        "notes": "Mirrored master long / long-option reduction",
                    }
                )
                if not client_result.get("success"):
                    snapshot_retry_syms.add(sym)
                    print(f"[ORDERBOOK COPIER] Client position close failed: {client_result.get('error', '')}")

            elif prev_qty < 0 and curr_qty > prev_qty:
                master_short_covered = min(abs(prev_qty), curr_qty - prev_qty)
                if master_short_covered <= 0:
                    continue
                avail = abs(client_qty) if client_qty < 0 else 0.0
                to_cover = int(round(min(master_short_covered, avail)))
                if to_cover <= 0:
                    continue
                sig = self._closing_signature_for_master_reduction(sym, to_cover, master_was_long=False, asset_type=asset_type)
                print(
                    f"[ORDERBOOK COPIER] MASTER SHORT REDUCED | symbol={sym} prev={prev_qty} curr={curr_qty} "
                    f"mirroring client cover qty={to_cover}"
                )
                client_result = self._place_from_signature(sig)
                copier_id = f"POSMAP-{uuid.uuid4().hex[:10].upper()}"
                self.log_callback(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "order_id": copier_id,
                        "copier_order_id": copier_id,
                        "master_order_id": "",
                        "client_order_id": self._extract_client_order_id(client_result.get("order", {}))
                        if client_result.get("success")
                        else "",
                        "event_type": "MASTER_POSITION_COVER_MIRRORED",
                        "action": "buy",
                        "symbol": sym,
                        "quantity": to_cover,
                        "trade_action": sig.get("trade_action", ""),
                        "order_type": "Market",
                        "limit_price": "",
                        "master_status": f"pos {prev_qty}->{curr_qty}",
                        "client_status": "SUCCESS" if client_result.get("success") else "FAILED",
                        "error": client_result.get("error", ""),
                        "notes": "Mirrored master short / short-option reduction",
                    }
                )
                if not client_result.get("success"):
                    snapshot_retry_syms.add(sym)
                    print(f"[ORDERBOOK COPIER] Client position cover failed: {client_result.get('error', '')}")

        new_last = {}
        for s in symbols:
            if s in snapshot_retry_syms:
                new_last[s] = float(self.last_master_positions_qty.get(s, 0.0))
            else:
                new_last[s] = float(master_map.get(s, {}).get("qty", 0.0))
        self.last_master_positions_qty = new_last

    def _client_trade_action_from_signature(self, sig):
        action = str(sig.get("trade_action", "")).upper()
        open_or_close = str(sig.get("open_or_close", "")).upper()
        asset_type = str(sig.get("asset_type", "")).upper()
        symbol = str(sig.get("symbol", "")).upper().strip()

        # Option symbols often look like: "SPXW 260414C6965"
        option_symbol_like = bool(re.search(r"\s\d{6}[CP]\d", symbol))
        is_option = ("OPTION" in asset_type) or option_symbol_like

        # Option orders require TOOPEN/TOCLOSE style actions.
        if is_option:
            if action == "BUY":
                return "BUYTOCLOSE" if open_or_close == "CLOSE" else "BUYTOOPEN"
            if action in {"SELL", "SELLSHORT"}:
                return "SELLTOCLOSE" if open_or_close == "CLOSE" else "SELLTOOPEN"
            if action in {"BUYTOOPEN", "BUYTOCLOSE", "SELLTOOPEN", "SELLTOCLOSE"}:
                return action

        # Equity/futures style actions
        if action in {"BUY", "SELL", "SELLSHORT", "BUYTOCOVER"}:
            return action
        return "BUY" if sig.get("qty", 0) > 0 else "SELL"

    def _place_from_signature(self, sig):
        signed_qty = self._signed_qty_from_sig(sig)
        client_trade_action = self._client_trade_action_from_signature(sig)
        return self.client_api.place_order(
            symbol=sig.get("symbol", ""),
            quantity=signed_qty,
            side="BUY" if signed_qty > 0 else "SELL",
            order_type=sig.get("order_type", "Market"),
            price=sig.get("limit_price") or None,
            trade_action=client_trade_action,
            duration=sig.get("duration", "DAY"),
            stop_price=sig.get("stop_price") or None,
            passthrough_fields=sig.get("passthrough_order_fields", {}),
        )

    def _copy_new(self, master_order_id, order):
        existing_mapping = self.order_map.get(master_order_id)
        if existing_mapping and existing_mapping.get("copier_order_id") and existing_mapping.get("client_order_id"):
            print(
                f"[ORDERBOOK COPIER] SKIP DUPLICATE COPY | "
                f"master_order_id={master_order_id} "
                f"copier_order_id={existing_mapping.get('copier_order_id', '')} "
                f"client_order_id={existing_mapping.get('client_order_id', '')}"
            )
            return

        sig = self._signature(order)
        if not sig["symbol"] or sig["qty"] == 0:
            return
        print(
            f"[ORDERBOOK COPIER] NEW MASTER ORDER DETECTED | "
            f"master_order_id={master_order_id} symbol={sig['symbol']} "
            f"action={sig['trade_action']} qty={sig['qty']} "
            f"type={sig['order_type']} duration={sig['duration']} status={str(order.get('Status', ''))}"
        )
        copier_id = f"ORDMAP-{uuid.uuid4().hex[:10].upper()}"
        client_result = self._place_from_signature(sig)
        client_order_id = self._extract_client_order_id(client_result.get("order", {})) if client_result.get("success") else ""
        if client_result.get("success"):
            print(
                f"[ORDERBOOK COPIER] NEW ORDER COPIED | "
                f"master_order_id={master_order_id} client_order_id={client_order_id} "
                f"copier_order_id={copier_id}"
            )
        else:
            print(
                f"[ORDERBOOK COPIER] NEW ORDER COPY FAILED | "
                f"master_order_id={master_order_id} error={client_result.get('error', 'Unknown error')}"
            )
        self.order_map[master_order_id] = {
            "copier_order_id": copier_id,
            "client_order_id": client_order_id,
            "last_master_status": str(order.get("Status", "")),
            "signature": sig,
        }
        self.log_callback(
            {
                "timestamp": datetime.now().isoformat(),
                "order_id": copier_id,
                "copier_order_id": copier_id,
                "master_order_id": master_order_id,
                "client_order_id": client_order_id,
                "event_type": "NEW_ORDER_COPIED",
                "action": "buy" if sig["trade_action"] in {"BUY", "BUYTOCOVER"} else "sell",
                "symbol": sig["symbol"],
                "quantity": sig["qty"],
                "trade_action": sig["trade_action"],
                "order_type": sig["order_type"],
                "limit_price": sig["limit_price"],
                "master_status": order.get("Status", ""),
                "client_status": "SUCCESS" if client_result.get("success") else "FAILED",
                "error": client_result.get("error", ""),
                "notes": "Replicated new master order",
            }
        )

    def _mirror_modify(self, master_order_id, order):
        mapping = self.order_map.get(master_order_id)
        if not mapping:
            return
        status = str(order.get("Status", "")).upper()
        if status in self.cancel_states:
            return
        new_sig = self._signature(order)
        old_sig = mapping.get("signature", {})
        if new_sig == old_sig:
            return

        old_client_order_id = mapping.get("client_order_id", "")
        cancel_result = {"success": True}
        if old_client_order_id:
            cancel_result = self.client_api.cancel_order(old_client_order_id)
        client_result = self._place_from_signature(new_sig)
        new_client_order_id = self._extract_client_order_id(client_result.get("order", {})) if client_result.get("success") else ""
        mapping["client_order_id"] = new_client_order_id
        mapping["signature"] = new_sig
        mapping["last_master_status"] = str(order.get("Status", ""))
        self.log_callback(
            {
                "timestamp": datetime.now().isoformat(),
                "order_id": mapping.get("copier_order_id", ""),
                "copier_order_id": mapping.get("copier_order_id", ""),
                "master_order_id": master_order_id,
                "client_order_id": new_client_order_id,
                "event_type": "MASTER_MODIFY_MIRRORED",
                "action": "buy" if new_sig["trade_action"] in {"BUY", "BUYTOCOVER"} else "sell",
                "symbol": new_sig["symbol"],
                "quantity": new_sig["qty"],
                "trade_action": new_sig["trade_action"],
                "order_type": new_sig["order_type"],
                "limit_price": new_sig["limit_price"],
                "master_status": order.get("Status", ""),
                "client_status": "SUCCESS" if client_result.get("success") else "FAILED",
                "error": client_result.get("error", "") or cancel_result.get("error", ""),
                "notes": "Master modify mirrored with cancel+replace",
            }
        )

    def _mirror_cancel(self, master_order_id, order):
        mapping = self.order_map.get(master_order_id)
        if not mapping:
            return
        status = str(order.get("Status", "")).upper()
        prev_status = str(mapping.get("last_master_status", "")).upper()
        mapping["last_master_status"] = status
        if status in self.cancel_states and prev_status not in self.cancel_states:
            client_order_id = mapping.get("client_order_id", "")
            cancel_result = self.client_api.cancel_order(client_order_id) if client_order_id else {"success": False, "error": "No mapped client order id"}
            sig = mapping.get("signature", {})
            self.log_callback(
                {
                    "timestamp": datetime.now().isoformat(),
                    "order_id": mapping.get("copier_order_id", ""),
                    "copier_order_id": mapping.get("copier_order_id", ""),
                    "master_order_id": master_order_id,
                    "client_order_id": client_order_id,
                    "event_type": "MASTER_CANCEL_MIRRORED",
                    "action": "buy exit" if sig.get("trade_action") in {"SELL", "SELLSHORT"} else "sell exit",
                    "symbol": sig.get("symbol", ""),
                    "quantity": sig.get("qty", ""),
                    "trade_action": "CANCEL",
                    "order_type": sig.get("order_type", ""),
                    "limit_price": sig.get("limit_price", ""),
                    "master_status": status,
                    "client_status": "SUCCESS" if cancel_result.get("success") else "FAILED",
                    "error": cancel_result.get("error", ""),
                    "notes": "Master cancel mirrored to client",
                }
            )

    def initialize_baseline(self):
        self.baseline_order_ids = set(self._combine_master_orders().keys())
        print(
            f"[ORDERBOOK COPIER] Baseline initialized with {len(self.baseline_order_ids)} master orders. "
            "Pre-existing open orders are ignored."
        )

    def sync_once(self):
        orders = self._combine_master_orders()
        for master_order_id, order in orders.items():
            if master_order_id not in self.order_map:
                if master_order_id in self.baseline_order_ids:
                    continue
                if self._is_order_currently_open(order) or self._should_copy_fast_filled_master_order(order):
                    self._copy_new(master_order_id, order)
            else:
                self._mirror_modify(master_order_id, order)
                self._mirror_cancel(master_order_id, order)
        self._mirror_master_position_closes()
        return orders

    def _print_live_master_orderbook(self, orders_map):
        # Print only "open now" every second.
        current_orders = []
        try:
            current_orders = self.master_api.fetch_orders()
        except Exception as e:
            print(f"[ORDERBOOK][MASTER] Could not fetch current orders: {e}")
        open_now = _open_orders_only(current_orders)
        _print_orders_to_console(
            account_type="master",
            account_id=self.master_api.account_id,
            orders=open_now,
            title="LIVE MASTER OPEN ORDERS NOW (every 0.3s)",
            include_raw=False,
        )

    def start(self):
        self.running = True
        self.initialize_baseline()
        while self.running:
            try:
                current_orders = self.sync_once()
                now_ts = time.time()
                if now_ts - self.last_orderbook_print_ts >= 0.3:
                    self._print_live_master_orderbook(current_orders)
                    self.last_orderbook_print_ts = now_ts
                time.sleep(0.3)
            except Exception as e:
                print(f"[ORDERBOOK COPIER] Error: {e}")
                time.sleep(0.3)

    def stop(self):
        self.running = False


def _perform_account_login(account_type):
    global login_status
    settings = load_api_settings()
    global_settings = settings.get("global", {})
    account_settings = settings.get(account_type, {})
    api_key = global_settings.get("api_key", "")
    api_secret = global_settings.get("api_secret", "")
    user_id = account_settings.get("user_id", "")
    password = account_settings.get("password", "")
    totp = account_settings.get("totp", "")

    if not api_key or not api_secret:
        login_status[account_type] = {"logged_in": False, "error": "Global API credentials not configured"}
        return {"success": False, "message": "Please configure global API credentials first"}

    cached_session = load_token_cache().get(account_type, {})
    session_matches_current_settings = _session_matches_context(
        cached_session,
        account_id=account_settings.get("account_id", ""),
        environment=account_settings.get("environment", "paper"),
    )
    if _session_is_usable_today(cached_session) and session_matches_current_settings:
        resolved = _resolve_account_id_if_missing(
            account_type=account_type,
            account_settings=account_settings,
            api_key=api_key,
            api_secret=api_secret,
            access_token=cached_session.get("access_token", ""),
            token_expiry=cached_session.get("token_expiry", 0),
        )
        if not resolved:
            login_status[account_type] = {"logged_in": False, "error": "Could not auto-detect account id from broker"}
            return {"success": False, "message": "Could not auto-detect account id from broker"}
        login_status[account_type] = {"logged_in": True, "error": None}
        return {"success": True, "message": f"{account_type.capitalize()} already logged in today (cached token reused)"}

    # If login happened today but token expired, refresh via refresh token without browser relogin.
    if (
        cached_session.get("login_date") == _today_date_text()
        and session_matches_current_settings
        and account_settings.get("refresh_token")
    ):
        try:
            session_api = ModifiedTradeStationAPI(
                client_id=api_key,
                client_secret=api_secret,
                account_id=account_settings.get("account_id", ""),
                environment=account_settings.get("environment", "paper"),
                refresh_token=account_settings.get("refresh_token", ""),
            )
            session_api.authenticate()
            cache_account_session(
                account_type=account_type,
                access_token=session_api.access_token,
                token_expiry=session_api.token_expiry,
                account_id=account_settings.get("account_id", ""),
                environment=account_settings.get("environment", "paper"),
            )
            if session_api.refresh_token and session_api.refresh_token != account_settings.get("refresh_token"):
                account_settings["refresh_token"] = session_api.refresh_token
                save_account_credentials(account_type, account_settings)
            resolved = _resolve_account_id_if_missing(
                account_type=account_type,
                account_settings=account_settings,
                api_key=api_key,
                api_secret=api_secret,
                access_token=session_api.access_token,
                token_expiry=session_api.token_expiry,
            )
            if not resolved:
                login_status[account_type] = {"logged_in": False, "error": "Could not auto-detect account id from broker"}
                return {"success": False, "message": "Could not auto-detect account id from broker"}
            login_status[account_type] = {"logged_in": True, "error": None}
            return {"success": True, "message": f"{account_type.capitalize()} token refreshed without relogin"}
        except Exception:
            pass

    if not user_id or not password:
        login_status[account_type] = {"logged_in": False, "error": "Account credentials not configured"}
        return {"success": False, "message": f"Please configure {account_type} User ID and Password first"}
    if not totp:
        login_status[account_type] = {"logged_in": False, "error": "TOTP secret not configured"}
        return {"success": False, "message": f"Please configure {account_type} TOTP secret first"}

    try:
        oauth_url = (
            f"https://signin.tradestation.com/authorize?"
            f"response_type=code&client_id={api_key}&audience=https%3A%2F%2Fapi.tradestation.com&"
            f"redirect_uri=http%3A%2F%2Flocalhost%3A3000&"
            f"scope=openid%20MarketData%20profile%20ReadAccount%20Trade%20offline_access%20Matrix%20OptionSpreads"
        )
        oauth = OAuthAutomation(user_id, password, totp)
        try:
            code = oauth.automate_oauth_login(oauth_url)
            if not code:
                raise Exception("Failed to obtain authorization code")
        finally:
            oauth.close()
        payload = (
            f"grant_type=authorization_code&client_id={api_key}&client_secret={api_secret}"
            f"&code={code}&redirect_uri=http%3A%2F%2Flocalhost%3A3000"
        )
        response = requests.post(
            "https://signin.tradestation.com/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
            timeout=10,
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")
        expires_in = int(token_data.get("expires_in", 1200) or 1200)
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            raise Exception("No refresh token in response")
        account_settings["refresh_token"] = refresh_token
        save_account_credentials(account_type, account_settings)
        if access_token:
            cache_account_session(
                account_type=account_type,
                access_token=access_token,
                token_expiry=time.time() + expires_in,
                account_id=account_settings.get("account_id", ""),
                environment=account_settings.get("environment", "paper"),
            )
        resolved = _resolve_account_id_if_missing(
            account_type=account_type,
            account_settings=account_settings,
            api_key=api_key,
            api_secret=api_secret,
            access_token=access_token,
            token_expiry=time.time() + expires_in,
        )
        if not resolved:
            login_status[account_type] = {"logged_in": False, "error": "Could not auto-detect account id from broker"}
            return {"success": False, "message": "Could not auto-detect account id from broker"}
        login_status[account_type] = {"logged_in": True, "error": None}
        return {"success": True, "message": f"{account_type.capitalize()} account logged in successfully"}
    except Exception as e:
        login_status[account_type] = {"logged_in": False, "error": str(e)}
        return {"success": False, "message": f"Login failed: {e}"}


@app.route("/")
def index():
    return redirect(url_for("api_settings"))


@app.route("/api-settings")
def api_settings():
    settings = load_api_settings()
    return render_template("api_settings.html", settings=settings, login_status=login_status, copier_running=copier_running)


@app.route("/positions")
def positions():
    return render_template("positions.html")


@app.route("/order-book")
def order_book():
    return render_template("order_book.html")


@app.route("/order-log")
def order_log():
    ensure_order_book_log_file()
    with open(ORDER_BOOK_LOG_FILE, "r", newline="") as f:
        orders = list(csv.DictReader(f))
    orders.reverse()
    return render_template("order_log.html", orders=orders)


@app.route("/api/order-details/<order_id>")
def api_order_details(order_id):
    ensure_order_book_log_file()
    with open(ORDER_BOOK_LOG_FILE, "r", newline="") as f:
        rows = list(csv.DictReader(f))
    order = next((o for o in rows if o.get("order_id") == order_id or o.get("copier_order_id") == order_id), None)
    if order:
        return jsonify(order)
    return jsonify({"error": "Order not found"}), 404


@app.route("/api/save-global-credentials", methods=["POST"])
def api_save_global_credentials():
    data = request.get_json() or {}
    api_key = data.get("api_key", "")
    api_secret = data.get("api_secret", "")
    if not api_key or not api_secret:
        return jsonify({"success": False, "message": "API Key and Secret are required"})
    save_global_credentials(api_key, api_secret)
    return jsonify({"success": True, "message": "Global API credentials saved successfully"})


@app.route("/api/save-account-credentials/<account_type>", methods=["POST"])
def api_save_account_credentials(account_type):
    if account_type not in ["master", "client"]:
        return jsonify({"success": False, "message": "Invalid account type"}), 400
    data = request.get_json() or {}
    settings = load_api_settings()
    existing = settings.get(account_type, {})
    creds = {
        "user_id": data.get("user_id", ""),
        "password": data.get("password", ""),
        "totp": data.get("totp", ""),
        "refresh_token": existing.get("refresh_token", ""),
        # Account ID is now broker-resolved post-login; do not take manual UI input.
        "account_id": "",
        "environment": _normalize_environment(data.get("environment", existing.get("environment", "paper"))),
    }
    save_account_credentials(account_type, creds)
    clear_cached_session(account_type)
    login_status[account_type] = {"logged_in": False, "error": None}
    return jsonify({"success": True, "message": f"{account_type.capitalize()} credentials saved successfully"})


@app.route("/api/login/<account_type>", methods=["POST"])
def api_login(account_type):
    if account_type not in ["master", "client"]:
        return jsonify({"success": False, "message": "Invalid account type"}), 400
    return jsonify(_perform_account_login(account_type))


@app.route("/api/get-balance/<account_type>", methods=["GET"])
def api_get_balance(account_type):
    if account_type not in ["master", "client"]:
        return jsonify({"success": False, "message": "Invalid account type"}), 400
    settings = load_api_settings()
    g = settings.get("global", {})
    a = settings.get(account_type, {})
    if not g.get("api_key") or not g.get("api_secret") or not a.get("refresh_token"):
        return jsonify({"success": False, "message": f"{account_type.capitalize()} account not configured or not logged in"})
    try:
        api = ModifiedTradeStationAPI(
            client_id=g["api_key"],
            client_secret=g["api_secret"],
            account_id=a.get("account_id", ""),
            environment=a.get("environment", "paper"),
            refresh_token=a.get("refresh_token", ""),
        )
        apply_cached_access_token(account_type, api)
        api.ensure_authenticated()
        cache_account_session(
            account_type,
            api.access_token,
            api.token_expiry,
            account_id=a.get("account_id", ""),
            environment=a.get("environment", "paper"),
        )
        balance = api.get_account_balance()
        if isinstance(balance, dict) and isinstance(balance.get("Balances"), list) and balance["Balances"]:
            balance = balance["Balances"][0]
        return jsonify({"success": True, "balance": {"account_id": a.get("account_id", ""), "balance": balance.get("CashBalance", "N/A"), "buying_power": balance.get("BuyingPower", "N/A"), "equity": balance.get("Equity", "N/A"), "full_data": balance}})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/start-trading", methods=["POST"])
def api_start_trading():
    global orderbook_copier, copier_thread, copier_running
    with copier_lock:
        if copier_running:
            return jsonify({"success": False, "message": "Order-book copier already running"})
        master_result = _perform_account_login("master")
        if not master_result.get("success"):
            return jsonify({"success": False, "message": f"Master login failed: {master_result.get('message')}", "master_logged_in": False, "client_logged_in": False})
        client_result = _perform_account_login("client")
        if not client_result.get("success"):
            return jsonify({"success": False, "message": f"Client login failed: {client_result.get('message')}", "master_logged_in": True, "client_logged_in": False})
        settings = load_api_settings()
        orderbook_copier = OrderBookTradeCopier(settings, log_orderbook_event)
        copier_thread = threading.Thread(target=orderbook_copier.start, daemon=True)
        copier_thread.start()
        copier_running = True
        return jsonify({"success": True, "message": "Order-book copy trading started.", "master_logged_in": True, "client_logged_in": True})


@app.route("/api/stop-copier", methods=["POST"])
def api_stop_copier():
    global copier_running
    with copier_lock:
        was_running = copier_running
        if orderbook_copier:
            orderbook_copier.stop()
        clear_cached_session("master")
        clear_cached_session("client")
        login_status["master"] = {"logged_in": False, "error": None}
        login_status["client"] = {"logged_in": False, "error": None}
        copier_running = False
        if was_running:
            msg = "Trading stopped. Sessions cleared. Safe to switch live/paper."
        else:
            msg = "Sessions cleared. Safe to switch live/paper."
        return jsonify({"success": True, "message": msg})


@app.route("/api/stop-trading", methods=["POST"])
def api_stop_trading():
    return api_stop_copier()


@app.route("/api/copier-status", methods=["GET"])
def api_copier_status():
    return jsonify({"running": copier_running, "mode": "order-book"})


def _positions_for(account_type):
    settings = load_api_settings()
    g = settings.get("global", {})
    a = settings.get(account_type, {})
    api = ModifiedTradeStationAPI(
        client_id=g.get("api_key", ""),
        client_secret=g.get("api_secret", ""),
        account_id=a.get("account_id", ""),
        environment=a.get("environment", "paper"),
        refresh_token=a.get("refresh_token", ""),
    )
    apply_cached_access_token(account_type, api)
    return api.get_positions()


def _api_for(account_type):
    settings = load_api_settings()
    g = settings.get("global", {})
    a = settings.get(account_type, {})
    api = ModifiedTradeStationAPI(
        g.get("api_key", ""),
        g.get("api_secret", ""),
        a.get("account_id", ""),
        a.get("environment", "paper"),
        a.get("refresh_token", ""),
    )
    apply_cached_access_token(account_type, api)
    return api, a.get("account_id", "")


def _order_identifier(order):
    return str(order.get("OrderID") or order.get("orderId") or order.get("ID") or order.get("Id") or "N/A")


def _first_leg(order):
    legs = order.get("Legs")
    if isinstance(legs, list) and legs:
        first = legs[0]
        if isinstance(first, dict):
            return first
    return {}


def _order_symbol(order):
    leg = _first_leg(order)
    return str(order.get("Symbol") or order.get("symbol") or order.get("Underlying") or leg.get("Symbol") or "")


def _order_action(order):
    leg = _first_leg(order)
    return str(
        order.get("TradeAction")
        or order.get("tradeAction")
        or order.get("Action")
        or order.get("BuyOrSell")
        or leg.get("TradeAction")
        or leg.get("Action")
        or leg.get("BuyOrSell")
        or ""
    )


def _order_qty(order):
    leg = _first_leg(order)
    return str(
        order.get("Quantity")
        or order.get("quantity")
        or order.get("RemainingQuantity")
        or order.get("FilledQuantity")
        or leg.get("Quantity")
        or leg.get("QuantityOrdered")
        or ""
    )


def _order_remaining_qty(order):
    leg = _first_leg(order)
    value = order.get("RemainingQuantity") or leg.get("QuantityRemaining") or ""
    try:
        return float(value)
    except Exception:
        return 0.0


def _order_open_or_close(order):
    leg = _first_leg(order)
    return str(order.get("OpenOrClose") or leg.get("OpenOrClose") or "")


def _order_type(order):
    return str(order.get("OrderType") or order.get("Type") or order.get("orderType") or "")


def _order_limit(order):
    return str(order.get("LimitPrice") or order.get("Price") or order.get("limitPrice") or "")


def _order_status(order):
    return str(
        order.get("Status")
        or order.get("status")
        or order.get("OrderStatus")
        or order.get("StatusDescription")
        or ""
    )


def _order_status_description(order):
    return str(order.get("StatusDescription") or order.get("statusDescription") or "")


def _order_entered_time(order):
    return str(
        order.get("OpenedDateTime")
        or order.get("EnteredTime")
        or order.get("EnteredDateTime")
        or order.get("CreateTimestamp")
        or order.get("TimeStamp")
        or ""
    )


def _order_filled_or_cancelled_time(order):
    return str(
        order.get("ClosedDateTime")
        or order.get("FilledDateTime")
        or order.get("CancelledDateTime")
        or order.get("CompletedTime")
        or order.get("LastUpdateTime")
        or ""
    )


def _print_orders_to_console(account_type, account_id, orders, title, include_raw=False):
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    divider = "=" * 110
    print(divider)
    print(
        f"[ORDERBOOK][{account_type.upper()}] {title} | account_id={account_id or 'N/A'} "
        f"| total={len(orders)} | {now_text}"
    )
    print(divider)
    if not orders:
        print("No orders found.")
        print(divider)
        return
    for index, order in enumerate(orders, start=1):
        entered_at = _order_entered_time(order)
        closed_at = _order_filled_or_cancelled_time(order)
        status_value = _order_status(order)
        status_desc = _order_status_description(order)
        timestamp_value = entered_at or now_text
        print(
            f"{index:03d}. order_id={_order_identifier(order)} "
            f"timestamp={timestamp_value} "
            f"symbol={_order_symbol(order)} "
            f"action={_order_action(order)} "
            f"qty={_order_qty(order)} "
            f"open_or_close={_order_open_or_close(order)} "
            f"type={_order_type(order)} "
            f"limit={_order_limit(order)} "
            f"status={status_value} "
            f"status_desc={status_desc} "
            f"closed={closed_at}"
        )
        if include_raw:
            print(f"     raw={json.dumps(order, sort_keys=False, default=str)}")
    print(divider)


def _print_raw_orders_payload(account_type, account_id, orders, source):
    divider = "=" * 110
    print(divider)
    print(f"[ORDERBOOK][{account_type.upper()}] RAW PAYLOAD FROM {source} | account_id={account_id or 'N/A'}")
    print(divider)
    print(json.dumps(orders, indent=2, sort_keys=False, default=str))
    print(divider)


def _open_orders_only(orders):
    # "Open now" = still working/alive in order book.
    # OpenOrClose means position intent, not whether order is currently active.
    filtered = []
    for order in orders:
        status = _order_status(order).upper()
        remaining_qty = _order_remaining_qty(order)
        has_closed_time = bool(_order_filled_or_cancelled_time(order))
        if status in OPEN_ORDER_STATUSES:
            filtered.append(order)
            continue
        if remaining_qty > 0 and not has_closed_time and status not in {"REJ", "OUT", "FLL"}:
            filtered.append(order)
    return filtered


def _combined_orderbook(api):
    combined = {}
    for order in api.fetch_orders():
        oid = str(order.get("OrderID") or order.get("orderId") or order.get("ID") or order.get("Id") or "")
        if oid:
            combined[oid] = order
    historical_since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    for order in api.fetch_historical_orders(since_date=historical_since):
        oid = str(order.get("OrderID") or order.get("orderId") or order.get("ID") or order.get("Id") or "")
        if oid:
            combined[oid] = order
    return list(combined.values())


@app.route("/api/master-positions", methods=["GET"])
def api_master_positions():
    try:
        return jsonify({"success": True, "positions": _positions_for("master")})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/client-positions", methods=["GET"])
def api_client_positions():
    try:
        return jsonify({"success": True, "positions": _positions_for("client")})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/master-orderbook", methods=["GET"])
def api_master_orderbook():
    try:
        api, _ = _api_for("master")
        return jsonify({"success": True, "orders": _combined_orderbook(api)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/client-orderbook", methods=["GET"])
def api_client_orderbook():
    try:
        api, _ = _api_for("client")
        return jsonify({"success": True, "orders": _combined_orderbook(api)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/print-orderbook/<account_type>", methods=["GET"])
def api_print_orderbook(account_type):
    if account_type not in ["master", "client"]:
        return jsonify({"success": False, "message": "Invalid account type"}), 400

    try:
        api, account_id = _api_for(account_type)
        # Notebook-aligned source:
        # GET /v3/brokerage/accounts/{account_id}/orders
        fetched_orders = api.fetch_orders()
        open_only = _open_orders_only(fetched_orders)
        full_orderbook = _combined_orderbook(api)

        _print_raw_orders_payload(
            account_type=account_type,
            account_id=account_id,
            orders=fetched_orders,
            source="fetch_orders()",
        )
        _print_orders_to_console(
            account_type=account_type,
            account_id=account_id,
            orders=open_only,
            title="OPEN ORDERS",
        )
        _print_orders_to_console(
            account_type=account_type,
            account_id=account_id,
            orders=full_orderbook,
            title="ORDERBOOK (OPEN + HISTORICAL)",
        )

        return jsonify(
            {
                "success": True,
                "message": f"Printed {account_type} open orders and orderbook in terminal.",
                "account_id": account_id,
                "fetched_orders_count": len(fetched_orders),
                "open_orders_count": len(open_only),
                "orderbook_count": len(full_orderbook),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


if __name__ == "__main__":
    ensure_order_book_log_file()
    app.run(debug=True, host="0.0.0.0", port=5000)
