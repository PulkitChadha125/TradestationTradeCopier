from flask import Flask, render_template, request, jsonify, redirect, url_for
import csv
import os
import json
import requests
from datetime import datetime
import threading
import time
from utils.tradestation_api import TradeStationAPI
from utils.trade_copier import TradeCopier
from utils.oauth_automation import OAuthAutomation

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'

# Ensure data directory exists
os.makedirs('data', exist_ok=True)
os.makedirs('logs', exist_ok=True)

API_SETTINGS_FILE = 'data/api_settings.csv'
ORDER_LOG_FILE = 'data/order_log.csv'
ORDER_LOG_FIELDNAMES = [
    'timestamp', 'order_id', 'copier_order_id', 'master_order_id', 'action', 'symbol', 'quantity', 'side', 'order_type',
    'mapping_key', 'client_order_id', 'client_response',
    'master_status', 'client_status',
    'master_request_time', 'master_response_time', 'master_latency',
    'client_request_time', 'client_response_time', 'client_latency',
    'status', 'error'
]

# Global instances
trade_copier = None
copier_thread = None
copier_running = False
copier_lock = threading.Lock()
order_log_lock = threading.Lock()

# Login status tracking
login_status = {
    'master': {'logged_in': False, 'error': None},
    'client': {'logged_in': False, 'error': None}
}

def ensure_order_log_file():
    """Ensure order log CSV exists with headers for web UI table rendering."""
    if not os.path.exists(ORDER_LOG_FILE):
        with open(ORDER_LOG_FILE, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=ORDER_LOG_FIELDNAMES)
            writer.writeheader()
        return

    # Migrate legacy header to current schema while preserving existing rows.
    with open(ORDER_LOG_FILE, 'r', newline='') as f:
        reader = csv.DictReader(f)
        existing_fields = reader.fieldnames or []
        if existing_fields == ORDER_LOG_FIELDNAMES:
            return
        rows = list(reader)

    migrated_rows = []
    for row in rows:
        # Skip accidental duplicate header rows saved as data.
        if row.get('timestamp') == 'timestamp':
            continue
        migrated_rows.append({field: row.get(field, '') for field in ORDER_LOG_FIELDNAMES})

    with open(ORDER_LOG_FILE, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=ORDER_LOG_FIELDNAMES)
        writer.writeheader()
        writer.writerows(migrated_rows)

def load_api_settings():
    """Load API settings from CSV"""
    settings = {'global': {}, 'master': {}, 'client': {}}
    if os.path.exists(API_SETTINGS_FILE):
        with open(API_SETTINGS_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                row_type = row.get('type', '').lower()
                if row_type == 'global':
                    settings['global'] = {
                        'api_key': row.get('api_key', ''),
                        'api_secret': row.get('api_secret', '')
                    }
                elif row_type in ['master', 'client']:
                    settings[row_type] = {
                        'user_id': row.get('user_id', ''),
                        'password': row.get('password', ''),
                        'totp': row.get('totp', ''),
                        'refresh_token': row.get('refresh_token', ''),
                        'account_id': row.get('account_id', ''),
                        'environment': row.get('environment', 'paper')
                    }
    return settings

def save_api_settings(settings):
    """Save API settings to CSV (legacy function, kept for compatibility)"""
    _write_settings_to_csv(settings)

def save_global_credentials(api_key, api_secret):
    """Save global API credentials to CSV"""
    settings = load_api_settings()
    settings['global'] = {
        'api_key': api_key,
        'api_secret': api_secret
    }
    _write_settings_to_csv(settings)

def save_account_credentials(account_type, credentials):
    """Save credentials for a single account type to CSV"""
    # Load existing settings
    settings = load_api_settings()
    
    # Update the specific account type
    settings[account_type] = credentials
    
    # Write all settings back to CSV
    _write_settings_to_csv(settings)

def _write_settings_to_csv(settings):
    """Write all settings to CSV file"""
    with open(API_SETTINGS_FILE, 'w', newline='') as f:
        fieldnames = ['type', 'api_key', 'api_secret', 'user_id', 'password', 'totp', 'refresh_token', 'account_id', 'environment']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        # Write global settings
        if settings.get('global'):
            writer.writerow({
                'type': 'global',
                'api_key': settings['global'].get('api_key', ''),
                'api_secret': settings['global'].get('api_secret', ''),
                'user_id': '',
                'password': '',
                'totp': '',
                'refresh_token': '',
                'account_id': '',
                'environment': ''
            })
        
        # Write account settings
        for acc_type in ['master', 'client']:
            if settings.get(acc_type):
                writer.writerow({
                    'type': acc_type,
                    'api_key': '',
                    'api_secret': '',
                    'user_id': settings[acc_type].get('user_id', ''),
                    'password': settings[acc_type].get('password', ''),
                    'totp': settings[acc_type].get('totp', ''),
                    'refresh_token': settings[acc_type].get('refresh_token', ''),
                    'account_id': settings[acc_type].get('account_id', ''),
                    'environment': settings[acc_type].get('environment', 'paper')
                })

def log_order(order_data):
    """Log order to CSV"""
    ensure_order_log_file()
    with order_log_lock:
        with open(ORDER_LOG_FILE, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=ORDER_LOG_FIELDNAMES)
            # Enforce order log action vocabulary.
            action = (order_data.get('action') or '').strip().lower()
            if action not in {'buy', 'buy exit', 'sell', 'sell exit'}:
                action = 'buy' if (order_data.get('side') or '').upper() == 'BUY' else 'sell'
            order_data['action'] = action
            if not order_data.get('copier_order_id'):
                order_data['copier_order_id'] = order_data.get('order_id', '')
            writer.writerow(order_data)

@app.route('/')
def index():
    return redirect(url_for('api_settings'))

@app.route('/api-settings', methods=['GET'])
def api_settings():
    global login_status
    settings = load_api_settings()
    return render_template('api_settings.html', settings=settings, login_status=login_status, copier_running=copier_running)

@app.route('/api/get-balance/<account_type>', methods=['GET'])
def get_account_balance(account_type):
    """Get account balance for master or client account"""
    if account_type not in ['master', 'client']:
        return jsonify({'success': False, 'message': 'Invalid account type'}), 400
    
    settings = load_api_settings()
    global_settings = settings.get('global', {})
    account_settings = settings.get(account_type, {})
    
    api_key = global_settings.get('api_key', '')
    api_secret = global_settings.get('api_secret', '')
    account_id = account_settings.get('account_id', '')
    refresh_token = account_settings.get('refresh_token', '')
    environment = account_settings.get('environment', 'paper')
    
    if not api_key or not api_secret or not refresh_token:
        print(f"[{account_type.upper()}] Balance check failed: Missing API credentials or refresh token")
        return jsonify({'success': False, 'message': f'{account_type.capitalize()} account not configured or not logged in'})
    
    if not account_id:
        print(f"[{account_type.upper()}] Balance check failed: Account ID not set (account_id='{account_id}')")
        return jsonify({'success': False, 'message': f'{account_type.capitalize()} Account ID not set. Please log in again to auto-detect account ID.'})
    
    print(f"[{account_type.upper()}] Fetching balance for Account ID: {account_id}")
    
    try:
        api = TradeStationAPI(
            client_id=api_key,
            client_secret=api_secret,
            account_id=account_id,
            environment=environment,
            refresh_token=refresh_token
        )
        api.authenticate()
        balance_data = api.get_account_balance()
        
        print(f"\n{'='*60}")
        print(f"[{account_type.upper()}] BALANCE API RESPONSE")
        print(f"{'='*60}")
        print(f"[{account_type.upper()}] Raw balance data type: {type(balance_data)}")
        print(f"[{account_type.upper()}] Raw balance data (full): {json.dumps(balance_data, indent=2)}")
        print(f"{'='*60}\n")
        
        # Handle different response formats
        # TradeStation API might return a list or a dict
        if isinstance(balance_data, list) and len(balance_data) > 0:
            balance_data = balance_data[0]
            print(f"[{account_type.upper()}] Extracted first item from list")
        elif isinstance(balance_data, dict) and 'Balances' in balance_data:
            balance_data = balance_data['Balances']
            if isinstance(balance_data, list) and len(balance_data) > 0:
                balance_data = balance_data[0]
                print(f"[{account_type.upper()}] Extracted from Balances list")
        elif isinstance(balance_data, dict) and isinstance(balance_data.get('Balances'), list) and len(balance_data.get('Balances', [])) > 0:
            balance_data = balance_data['Balances'][0]
            print(f"[{account_type.upper()}] Extracted from nested Balances")
        
        # If balance_data is still a dict, extract values
        if not isinstance(balance_data, dict):
            # Try to convert or use default
            balance_data = balance_data if balance_data else {}
            print(f"[{account_type.upper()}] Converted to dict or used empty dict")
        
        print(f"[{account_type.upper()}] Processed balance data keys: {list(balance_data.keys()) if isinstance(balance_data, dict) else 'Not a dict'}")
        print(f"[{account_type.upper()}] Processed balance data: {balance_data}")
        
        # Print ALL keys recursively to help debug
        if isinstance(balance_data, dict):
            print(f"[{account_type.upper()}] All available keys in balance_data:")
            def print_keys(d, prefix=""):
                for k, v in d.items():
                    if isinstance(v, dict):
                        print(f"{prefix}{k}: (dict with keys: {list(v.keys())})")
                        print_keys(v, prefix + "  ")
                    elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                        print(f"{prefix}{k}: (list of dicts, first item keys: {list(v[0].keys())})")
                    else:
                        print(f"{prefix}{k}: {type(v).__name__} = {v}")
            print_keys(balance_data)
        
        # Extract key balance information with multiple fallback options
        # Check all possible field names from TradeStation API
        cash_balance = None
        for key in ['CashBalance', 'Cash', 'CashAvailable', 'SettledCash', 'CashBalanceAmount', 'CashBalanceValue']:
            if isinstance(balance_data, dict) and key in balance_data and balance_data[key] is not None:
                cash_balance = balance_data[key]
                print(f"[{account_type.upper()}] Found cash balance in field: {key} = {cash_balance}")
                break
        
        buying_power = None
        for key in ['DayTradingBuyingPower', 'BuyingPower', 'DayTradingBuyingPowerDayTrade', 'MarginBuyingPower', 'BuyingPowerAmount', 'BuyingPowerValue']:
            if isinstance(balance_data, dict) and key in balance_data and balance_data[key] is not None:
                buying_power = balance_data[key]
                print(f"[{account_type.upper()}] Found buying power in field: {key} = {buying_power}")
                break
        
        equity = None
        for key in ['Equity', 'NetLiquidation', 'TotalEquity', 'NetWorth', 'EquityAmount', 'EquityValue']:
            if isinstance(balance_data, dict) and key in balance_data and balance_data[key] is not None:
                equity = balance_data[key]
                print(f"[{account_type.upper()}] Found equity in field: {key} = {equity}")
                break
        
        balance_info = {
            'account_id': account_id,
            'balance': cash_balance if cash_balance is not None else 'N/A',
            'buying_power': buying_power if buying_power is not None else 'N/A',
            'equity': equity if equity is not None else 'N/A',
            'full_data': balance_data
        }
        
        print(f"[{account_type.upper()}] Final balance info: {json.dumps(balance_info, indent=2)}")
        print(f"[{account_type.upper()}] Returning JSON response to frontend...")
        print(f"[{account_type.upper()}] Response will contain: success=True, balance={balance_info}")
        
        response_data = {'success': True, 'balance': balance_info}
        print(f"[{account_type.upper()}] Full JSON response: {json.dumps(response_data, indent=2)}")
        
        return jsonify(response_data)
    except Exception as e:
        error_msg = str(e)
        print(f"[{account_type.upper()}] ERROR in balance endpoint: {error_msg}")
        import traceback
        print(f"[{account_type.upper()}] Traceback: {traceback.format_exc()}")
        return jsonify({'success': False, 'message': f'Error fetching balance: {error_msg}'})

@app.route('/api/get-settings')
def get_settings():
    """Get current settings as JSON"""
    settings = load_api_settings()
    return jsonify(settings)

@app.route('/api/save-global-credentials', methods=['POST'])
def save_global_credentials_endpoint():
    """Save global API credentials"""
    data = request.get_json()
    api_key = data.get('api_key', '')
    api_secret = data.get('api_secret', '')
    
    if not api_key or not api_secret:
        return jsonify({'success': False, 'message': 'API Key and Secret are required'})
    
    try:
        save_global_credentials(api_key, api_secret)
        return jsonify({'success': True, 'message': 'Global API credentials saved successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error saving credentials: {str(e)}'})

@app.route('/api/save-account-credentials/<account_type>', methods=['POST'])
def save_account_credentials_endpoint(account_type):
    """Save credentials for a specific account type"""
    global login_status
    
    if account_type not in ['master', 'client']:
        return jsonify({'success': False, 'message': 'Invalid account type'}), 400
    
    # Get credentials from request
    data = request.get_json()
    
    # Load existing settings to preserve refresh_token and account_id
    settings = load_api_settings()
    existing_account = settings.get(account_type, {})
    
    credentials = {
        'user_id': data.get('user_id', ''),
        'password': data.get('password', ''),
        'totp': data.get('totp', ''),
        'refresh_token': existing_account.get('refresh_token', ''),
        'account_id': existing_account.get('account_id', ''),
        'environment': existing_account.get('environment', 'paper')
    }
    
    try:
        save_account_credentials(account_type, credentials)
        # Reset login status for this account when credentials are saved
        login_status[account_type] = {'logged_in': False, 'error': None}
        return jsonify({'success': True, 'message': f'{account_type.capitalize()} credentials saved successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error saving credentials: {str(e)}'})

@app.route('/master-position')
def master_position():
    return redirect(url_for('positions'))

@app.route('/positions')
def positions():
    return render_template('positions.html')

@app.route('/api/master-positions', methods=['GET'])
def api_master_positions():
    """Live master positions for dynamic UI refresh."""
    settings = load_api_settings()
    global_settings = settings.get('global', {})
    master_settings = settings.get('master', {})

    if not global_settings.get('api_key') or not global_settings.get('api_secret'):
        return jsonify({'success': False, 'message': 'Global API credentials not configured'}), 400
    if not master_settings.get('refresh_token'):
        return jsonify({'success': False, 'message': 'Master account not logged in'}), 400

    try:
        api = TradeStationAPI(
            client_id=global_settings['api_key'],
            client_secret=global_settings['api_secret'],
            account_id=master_settings.get('account_id', ''),
            environment=master_settings.get('environment', 'paper'),
            refresh_token=master_settings.get('refresh_token', '')
        )
        positions = api.get_positions()
        return jsonify({'success': True, 'positions': positions})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/master-position-exit', methods=['POST'])
def api_master_position_exit():
    """Exit a specific master position selected from live positions table."""
    settings = load_api_settings()
    global_settings = settings.get('global', {})
    master_settings = settings.get('master', {})

    if not global_settings.get('api_key') or not global_settings.get('api_secret'):
        return jsonify({'success': False, 'message': 'Global API credentials not configured'}), 400
    if not master_settings.get('refresh_token'):
        return jsonify({'success': False, 'message': 'Master account not logged in'}), 400

    payload = request.get_json() or {}
    symbol = payload.get('symbol')
    quantity = payload.get('quantity')
    long_short = str(payload.get('long_short', '')).upper()
    position_id = payload.get('position_id')

    if not symbol or quantity is None:
        return jsonify({'success': False, 'message': 'symbol and quantity are required'}), 400

    try:
        qty = int(float(quantity))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'Invalid quantity'}), 400

    if qty == 0:
        return jsonify({'success': False, 'message': 'Quantity is zero'}), 400

    # Opposite-side market order to close selected position.
    if long_short == 'SHORT' or qty < 0:
        close_qty = abs(qty)      # BUY to cover short
    else:
        close_qty = -abs(qty)     # SELL to close long

    try:
        api = TradeStationAPI(
            client_id=global_settings['api_key'],
            client_secret=global_settings['api_secret'],
            account_id=master_settings.get('account_id', ''),
            environment=master_settings.get('environment', 'paper'),
            refresh_token=master_settings.get('refresh_token', '')
        )
        result = api.place_order(
            symbol=symbol,
            quantity=close_qty,
            side='BUY' if close_qty > 0 else 'SELL',
            order_type='Market'
        )
        if result.get('success'):
            print(
                f"[MASTER EXIT] Closed position request successful: "
                f"position_id={position_id}, symbol={symbol}, quantity={quantity}"
            )
            return jsonify({'success': True, 'result': result})

        error_msg = result.get('error', 'Unknown error')
        print(
            f"[MASTER EXIT] Close failed: position_id={position_id}, symbol={symbol}, "
            f"quantity={quantity}, error={error_msg}"
        )
        return jsonify({'success': False, 'message': error_msg}), 400
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/client-position')
def client_position():
    return redirect(url_for('positions'))

@app.route('/api/client-positions', methods=['GET'])
def api_client_positions():
    """Live client positions for dynamic UI refresh."""
    settings = load_api_settings()
    global_settings = settings.get('global', {})
    client_settings = settings.get('client', {})

    if not global_settings.get('api_key') or not global_settings.get('api_secret'):
        return jsonify({'success': False, 'message': 'Global API credentials not configured'}), 400
    if not client_settings.get('refresh_token'):
        return jsonify({'success': False, 'message': 'Client account not logged in'}), 400

    try:
        api = TradeStationAPI(
            client_id=global_settings['api_key'],
            client_secret=global_settings['api_secret'],
            account_id=client_settings.get('account_id', ''),
            environment=client_settings.get('environment', 'paper'),
            refresh_token=client_settings.get('refresh_token', '')
        )
        positions = api.get_positions()
        return jsonify({'success': True, 'positions': positions})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/order-log')
def order_log():
    ensure_order_log_file()
    orders = []
    with open(ORDER_LOG_FILE, 'r', newline='') as f:
        reader = csv.DictReader(f)
        orders = list(reader)
    # Reverse to show newest first
    orders.reverse()
    return render_template('order_log.html', orders=orders)

@app.route('/order-book')
def order_book():
    # In position-based mode, order log is the primary order view.
    return redirect(url_for('order_log'))

@app.route('/tradebook-master')
def tradebook_master():
    return redirect(url_for('order_log'))

@app.route('/tradebook-client')
def tradebook_client():
    return redirect(url_for('order_log'))

@app.route('/api/order-details/<order_id>')
def order_details(order_id):
    """Get detailed order information"""
    ensure_order_log_file()
    orders = []
    with open(ORDER_LOG_FILE, 'r', newline='') as f:
        reader = csv.DictReader(f)
        orders = list(reader)
    
    order = next((o for o in orders if o.get('order_id') == order_id), None)
    if order:
        return jsonify(order)
    return jsonify({'error': 'Order not found'}), 404

@app.route('/api/start-copier', methods=['POST'])
def start_copier():
    global trade_copier, copier_thread, copier_running
    with copier_lock:
        if copier_running:
            return jsonify({'success': False, 'message': 'Copier is already running'})
        
        settings = load_api_settings()
        global_settings = settings.get('global', {})
        master_settings = settings.get('master', {})
        client_settings = settings.get('client', {})
        
        if not global_settings.get('api_key'):
            return jsonify({'success': False, 'message': 'Please configure global API credentials'})
        if not master_settings.get('refresh_token') or not client_settings.get('refresh_token'):
            return jsonify({'success': False, 'message': 'Please login to both master and client accounts first'})
        
        try:
            trade_copier = TradeCopier(settings, log_order)
            copier_thread = threading.Thread(target=trade_copier.start, daemon=True)
            copier_thread.start()
            copier_running = True
            return jsonify({'success': True, 'message': 'Trade copier started'})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)})

@app.route('/api/stop-copier', methods=['POST'])
def stop_copier():
    global trade_copier, copier_running
    with copier_lock:
        if not copier_running:
            return jsonify({'success': False, 'message': 'Copier is not running'})
        
        if trade_copier:
            trade_copier.stop()
        copier_running = False
        return jsonify({'success': True, 'message': 'Trade copier stopped'})

@app.route('/api/copier-status')
def copier_status():
    return jsonify({'running': copier_running})

@app.route('/api/get-oauth-url/<account_type>')
def get_oauth_url(account_type):
    """Generate OAuth URL for getting refresh token"""
    if account_type not in ['master', 'client']:
        return jsonify({'error': 'Invalid account type'}), 400
    
    settings = load_api_settings()
    global_settings = settings.get('global', {})
    api_key = global_settings.get('api_key', '')
    
    if not api_key:
        return jsonify({'error': 'Global API Key not configured'}), 400
    
    # Generate OAuth URL (as shown in sample code)
    oauth_url = (
        f"https://signin.tradestation.com/authorize?"
        f"response_type=code&"
        f"client_id={api_key}&"
        f"audience=https%3A%2F%2Fapi.tradestation.com&"
        f"redirect_uri=http%3A%2F%2Flocalhost%3A3000&"
        f"scope=openid%20MarketData%20profile%20ReadAccount%20Trade%20offline_access%20Matrix%20OptionSpreads"
    )
    
    return jsonify({'oauth_url': oauth_url, 'instructions': 'Visit this URL, login, and copy the "code" from the redirect URL'})

@app.route('/api/exchange-code-for-token/<account_type>', methods=['POST'])
def exchange_code_for_token(account_type):
    """Exchange authorization code for refresh token"""
    if account_type not in ['master', 'client']:
        return jsonify({'success': False, 'message': 'Invalid account type'}), 400
    
    settings = load_api_settings()
    global_settings = settings.get('global', {})
    account_settings = settings.get(account_type, {})
    api_key = global_settings.get('api_key', '')
    api_secret = global_settings.get('api_secret', '')
    
    if not api_key or not api_secret:
        return jsonify({'success': False, 'message': 'Global API Key and Secret must be configured first'}), 400
    
    data = request.get_json()
    code = data.get('code', '')
    
    if not code:
        return jsonify({'success': False, 'message': 'Authorization code is required'}), 400
    
    try:
        # Exchange code for refresh token (as shown in sample code)
        url = "https://signin.tradestation.com/oauth/token"
        payload = f'grant_type=authorization_code&client_id={api_key}&client_secret={api_secret}&code={code}&redirect_uri=http%3A%2F%2Flocalhost%3A3000'
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        response = requests.post(url, headers=headers, data=payload, timeout=10)
        response.raise_for_status()
        token_data = response.json()
        
        refresh_token = token_data.get('refresh_token')
        if refresh_token:
            # Update and save the refresh token
            account_settings['refresh_token'] = refresh_token
            save_account_credentials(account_type, account_settings)
            return jsonify({
                'success': True, 
                'message': 'Refresh token obtained and saved successfully',
                'refresh_token': refresh_token
            })
        else:
            return jsonify({'success': False, 'message': 'No refresh token in response'})
    except Exception as e:
        error_msg = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = e.response.json()
                error_msg = error_detail.get('error_description', error_detail.get('error', str(e)))
            except:
                error_msg = e.response.text[:200] or str(e)
        return jsonify({'success': False, 'message': f'Error exchanging code: {error_msg}'})

def _perform_account_login(account_type):
    """Shared account login logic used by single and combined start flows."""
    global login_status

    settings = load_api_settings()
    global_settings = settings.get('global', {})
    account_settings = settings.get(account_type, {})

    api_key = global_settings.get('api_key', '')
    api_secret = global_settings.get('api_secret', '')
    user_id = account_settings.get('user_id', '')
    password = account_settings.get('password', '')
    totp = account_settings.get('totp', '')

    if not api_key or not api_secret:
        login_status[account_type] = {'logged_in': False, 'error': 'Global API credentials not configured'}
        return {'success': False, 'message': 'Please configure global API credentials first'}

    if not user_id or not password:
        login_status[account_type] = {'logged_in': False, 'error': 'Account credentials not configured'}
        return {'success': False, 'message': f'Please configure {account_type} User ID and Password first'}

    if not totp:
        login_status[account_type] = {'logged_in': False, 'error': 'TOTP secret not configured'}
        return {'success': False, 'message': f'Please configure {account_type} TOTP secret first'}

    try:
        print(f"Starting Selenium automation for {account_type} account...")

        oauth_url = (
            f"https://signin.tradestation.com/authorize?"
            f"response_type=code&"
            f"client_id={api_key}&"
            f"audience=https%3A%2F%2Fapi.tradestation.com&"
            f"redirect_uri=http%3A%2F%2Flocalhost%3A3000&"
            f"scope=openid%20MarketData%20profile%20ReadAccount%20Trade%20offline_access%20Matrix%20OptionSpreads"
        )

        oauth_automation = OAuthAutomation(user_id, password, totp)
        try:
            code = oauth_automation.automate_oauth_login(oauth_url)
            if not code:
                raise Exception("Failed to obtain authorization code")
        finally:
            oauth_automation.close()

        url = "https://signin.tradestation.com/oauth/token"
        payload = f'grant_type=authorization_code&client_id={api_key}&client_secret={api_secret}&code={code}&redirect_uri=http%3A%2F%2Flocalhost%3A3000'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        response = requests.post(url, headers=headers, data=payload, timeout=10)
        response.raise_for_status()
        token_data = response.json()

        refresh_token = token_data.get('refresh_token')
        if not refresh_token:
            raise Exception("No refresh token in response")

        account_settings['refresh_token'] = refresh_token
        environment = account_settings.get('environment', 'paper')

        api = TradeStationAPI(
            client_id=api_key,
            client_secret=api_secret,
            account_id=account_settings.get('account_id', ''),
            environment=environment,
            refresh_token=refresh_token
        )
        api.authenticate()
        accounts = api.get_account_info()

        accounts_list = []
        if isinstance(accounts, dict) and isinstance(accounts.get('Accounts'), list):
            accounts_list = accounts.get('Accounts', [])
        elif isinstance(accounts, list):
            accounts_list = accounts
        elif isinstance(accounts, dict):
            accounts_list = [accounts]

        available_account_ids = []
        for acc in accounts_list:
            if isinstance(acc, dict):
                acc_id = acc.get('AccountID') or acc.get('Account') or acc.get('AccountKey')
                if acc_id:
                    available_account_ids.append(acc_id)

        detected_account_id = None
        for acc in accounts_list:
            if isinstance(acc, dict) and acc.get('AccountType') == 'Margin':
                detected_account_id = acc.get('AccountID') or acc.get('Account') or acc.get('AccountKey')
                if detected_account_id:
                    break
        if not detected_account_id and available_account_ids:
            detected_account_id = available_account_ids[0]

        current_account_id = account_settings.get('account_id', '')
        if not current_account_id and detected_account_id:
            account_settings['account_id'] = detected_account_id
        elif available_account_ids and current_account_id not in available_account_ids:
            account_settings['account_id'] = detected_account_id or current_account_id

        account_settings['environment'] = environment
        save_account_credentials(account_type, account_settings)

        login_status[account_type] = {'logged_in': True, 'error': None}
        return {'success': True, 'message': f'{account_type.capitalize()} account logged in successfully'}
    except Exception as e:
        error_msg = str(e)
        login_status[account_type] = {'logged_in': False, 'error': error_msg}
        return {'success': False, 'message': f'Login failed: {error_msg}'}

@app.route('/api/login/<account_type>', methods=['POST'])
def login_account(account_type):
    """Login to master or client account using Selenium automation."""
    if account_type not in ['master', 'client']:
        return jsonify({'success': False, 'message': 'Invalid account type'}), 400
    result = _perform_account_login(account_type)
    return jsonify(result)

@app.route('/api/start-trading', methods=['POST'])
def start_trading():
    """
    One-click flow:
    1) login master
    2) login client
    3) start copier
    """
    global trade_copier, copier_thread, copier_running
    with copier_lock:
        if copier_running:
            return jsonify({'success': False, 'message': 'Copier is already running'})

        master_result = _perform_account_login('master')
        if not master_result.get('success'):
            return jsonify({
                'success': False,
                'message': f"Master login failed: {master_result.get('message')}",
                'master_logged_in': bool(login_status.get('master', {}).get('logged_in')),
                'client_logged_in': bool(login_status.get('client', {}).get('logged_in'))
            })

        client_result = _perform_account_login('client')
        if not client_result.get('success'):
            return jsonify({
                'success': False,
                'message': f"Client login failed: {client_result.get('message')}",
                'master_logged_in': bool(login_status.get('master', {}).get('logged_in')),
                'client_logged_in': bool(login_status.get('client', {}).get('logged_in'))
            })

        settings = load_api_settings()
        try:
            trade_copier = TradeCopier(settings, log_order)
            copier_thread = threading.Thread(target=trade_copier.start, daemon=True)
            copier_thread.start()
            copier_running = True
            return jsonify({
                'success': True,
                'message': 'Master+Client login successful. Copy trading started.',
                'master_logged_in': bool(login_status.get('master', {}).get('logged_in')),
                'client_logged_in': bool(login_status.get('client', {}).get('logged_in'))
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'message': str(e),
                'master_logged_in': bool(login_status.get('master', {}).get('logged_in')),
                'client_logged_in': bool(login_status.get('client', {}).get('logged_in'))
            })

if __name__ == '__main__':
    ensure_order_log_file()
    app.run(debug=True, host='0.0.0.0', port=5000)
