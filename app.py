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

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'

# Ensure data directory exists
os.makedirs('data', exist_ok=True)
os.makedirs('logs', exist_ok=True)

API_SETTINGS_FILE = 'data/api_settings.csv'
ORDER_LOG_FILE = 'data/order_log.csv'

# Global instances
trade_copier = None
copier_thread = None
copier_running = False

# Login status tracking
login_status = {
    'master': {'logged_in': False, 'error': None},
    'client': {'logged_in': False, 'error': None}
}

def load_api_settings():
    """Load API settings from CSV"""
    settings = {'master': {}, 'client': {}}
    if os.path.exists(API_SETTINGS_FILE):
        with open(API_SETTINGS_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                account_type = row.get('account_type', '').lower()
                if account_type in ['master', 'client']:
                    settings[account_type] = {
                        'client_id': row.get('client_id', ''),
                        'client_secret': row.get('client_secret', ''),
                        'refresh_token': row.get('refresh_token', ''),
                        'account_id': row.get('account_id', ''),
                        'environment': row.get('environment', 'paper')
                    }
    return settings

def save_api_settings(settings):
    """Save API settings to CSV"""
    # Load existing settings first
    existing_settings = load_api_settings()
    
    # Update with new settings
    for account_type in ['master', 'client']:
        if settings.get(account_type):
            existing_settings[account_type] = settings[account_type]
    
    # Write all settings to CSV
    with open(API_SETTINGS_FILE, 'w', newline='') as f:
        fieldnames = ['account_type', 'client_id', 'client_secret', 'refresh_token', 'account_id', 'environment']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for account_type in ['master', 'client']:
            if existing_settings.get(account_type):
                writer.writerow({
                    'account_type': account_type,
                    'client_id': existing_settings[account_type].get('client_id', ''),
                    'client_secret': existing_settings[account_type].get('client_secret', ''),
                    'refresh_token': existing_settings[account_type].get('refresh_token', ''),
                    'account_id': existing_settings[account_type].get('account_id', ''),
                    'environment': existing_settings[account_type].get('environment', 'paper')
                })

def save_account_credentials(account_type, credentials):
    """Save credentials for a single account type to CSV"""
    # Load existing settings
    settings = load_api_settings()
    
    # Update the specific account type
    settings[account_type] = credentials
    
    # Write all settings back to CSV
    with open(API_SETTINGS_FILE, 'w', newline='') as f:
        fieldnames = ['account_type', 'client_id', 'client_secret', 'refresh_token', 'account_id', 'environment']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for acc_type in ['master', 'client']:
            if settings.get(acc_type):
                writer.writerow({
                    'account_type': acc_type,
                    'client_id': settings[acc_type].get('client_id', ''),
                    'client_secret': settings[acc_type].get('client_secret', ''),
                    'refresh_token': settings[acc_type].get('refresh_token', ''),
                    'account_id': settings[acc_type].get('account_id', ''),
                    'environment': settings[acc_type].get('environment', 'paper')
                })

def log_order(order_data):
    """Log order to CSV"""
    file_exists = os.path.exists(ORDER_LOG_FILE)
    with open(ORDER_LOG_FILE, 'a', newline='') as f:
        fieldnames = ['timestamp', 'order_id', 'symbol', 'quantity', 'side', 'order_type', 
                     'master_request_time', 'master_response_time', 'master_latency',
                     'client_request_time', 'client_response_time', 'client_latency',
                     'status', 'error']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(order_data)

@app.route('/')
def index():
    return redirect(url_for('api_settings'))

@app.route('/api-settings', methods=['GET'])
def api_settings():
    global login_status
    settings = load_api_settings()
    return render_template('api_settings.html', settings=settings, login_status=login_status, copier_running=copier_running)

@app.route('/api/get-settings')
def get_settings():
    """Get current settings as JSON"""
    settings = load_api_settings()
    return jsonify(settings)

@app.route('/api/save-credentials/<account_type>', methods=['POST'])
def save_credentials(account_type):
    """Save credentials for a specific account type"""
    global login_status
    
    if account_type not in ['master', 'client']:
        return jsonify({'success': False, 'message': 'Invalid account type'}), 400
    
    # Get credentials from request
    data = request.get_json()
    credentials = {
        'client_id': data.get('client_id', ''),
        'client_secret': data.get('client_secret', ''),
        'refresh_token': data.get('refresh_token', ''),
        'account_id': data.get('account_id', ''),
        'environment': data.get('environment', 'paper')
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
    settings = load_api_settings()
    positions = []
    error = None
    
    if settings.get('master', {}).get('client_id'):
        try:
            api = TradeStationAPI(
                client_id=settings['master']['client_id'],
                client_secret=settings['master']['client_secret'],
                account_id=settings['master'].get('account_id', ''),
                environment=settings['master'].get('environment', 'paper'),
                refresh_token=settings['master'].get('refresh_token', '')
            )
            positions = api.get_positions()
        except Exception as e:
            error = str(e)
    
    return render_template('master_position.html', positions=positions, error=error)

@app.route('/client-position')
def client_position():
    settings = load_api_settings()
    positions = []
    error = None
    
    if settings.get('client', {}).get('client_id'):
        try:
            api = TradeStationAPI(
                client_id=settings['client']['client_id'],
                client_secret=settings['client']['client_secret'],
                account_id=settings['client'].get('account_id', ''),
                environment=settings['client'].get('environment', 'paper'),
                refresh_token=settings['client'].get('refresh_token', '')
            )
            positions = api.get_positions()
        except Exception as e:
            error = str(e)
    
    return render_template('client_position.html', positions=positions, error=error)

@app.route('/order-log')
def order_log():
    orders = []
    if os.path.exists(ORDER_LOG_FILE):
        with open(ORDER_LOG_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            orders = list(reader)
    # Reverse to show newest first
    orders.reverse()
    return render_template('order_log.html', orders=orders)

@app.route('/api/order-details/<order_id>')
def order_details(order_id):
    """Get detailed order information"""
    orders = []
    if os.path.exists(ORDER_LOG_FILE):
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
    
    if copier_running:
        return jsonify({'success': False, 'message': 'Copier is already running'})
    
    settings = load_api_settings()
    if not settings.get('master', {}).get('client_id') or not settings.get('client', {}).get('client_id'):
        return jsonify({'success': False, 'message': 'Please configure both master and client API settings'})
    
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
    account_settings = settings.get(account_type, {})
    client_id = account_settings.get('client_id', '')
    
    if not client_id:
        return jsonify({'error': 'Client ID not configured'}), 400
    
    # Generate OAuth URL (as shown in sample code)
    oauth_url = (
        f"https://signin.tradestation.com/authorize?"
        f"response_type=code&"
        f"client_id={client_id}&"
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
    account_settings = settings.get(account_type, {})
    client_id = account_settings.get('client_id', '')
    client_secret = account_settings.get('client_secret', '')
    
    if not client_id or not client_secret:
        return jsonify({'success': False, 'message': 'Client ID and Secret must be configured first'}), 400
    
    data = request.get_json()
    code = data.get('code', '')
    
    if not code:
        return jsonify({'success': False, 'message': 'Authorization code is required'}), 400
    
    try:
        # Exchange code for refresh token (as shown in sample code)
        url = "https://signin.tradestation.com/oauth/token"
        payload = f'grant_type=authorization_code&client_id={client_id}&client_secret={client_secret}&code={code}&redirect_uri=http%3A%2F%2Flocalhost%3A3000'
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

@app.route('/api/login/<account_type>', methods=['POST'])
def login_account(account_type):
    """Login to master or client account"""
    global login_status
    
    if account_type not in ['master', 'client']:
        return jsonify({'success': False, 'message': 'Invalid account type'}), 400
    
    settings = load_api_settings()
    account_settings = settings.get(account_type, {})
    
    if not account_settings.get('client_id') or not account_settings.get('client_secret'):
        login_status[account_type] = {'logged_in': False, 'error': 'API credentials not configured'}
        return jsonify({'success': False, 'message': 'Please configure API credentials first'})
    
    try:
        # Default to paper trading if not specified
        environment = account_settings.get('environment', 'paper')
        
        api = TradeStationAPI(
            client_id=account_settings['client_id'],
            client_secret=account_settings['client_secret'],
            account_id=account_settings.get('account_id', ''),
            environment=environment,
            refresh_token=account_settings.get('refresh_token', '')
        )
        # Test authentication
        api.authenticate()
        # Get account list and auto-detect account_id if not set
        accounts = api.get_account_info()
        
        # Auto-detect and save account_id if not already set
        if not account_settings.get('account_id'):
            detected_account_id = None
            if isinstance(accounts, list) and len(accounts) > 0:
                # Use the first account
                first_account = accounts[0]
                detected_account_id = first_account.get('AccountID') or first_account.get('Account') or first_account.get('AccountKey', '')
            elif isinstance(accounts, dict):
                detected_account_id = accounts.get('AccountID') or accounts.get('Account') or accounts.get('AccountKey', '')
            
            if detected_account_id:
                # Preserve existing refresh_token when updating
                existing_refresh_token = account_settings.get('refresh_token', '')
                account_settings['account_id'] = detected_account_id
                account_settings['environment'] = environment
                account_settings['refresh_token'] = existing_refresh_token
                save_account_credentials(account_type, account_settings)
        
        login_status[account_type] = {'logged_in': True, 'error': None}
        return jsonify({'success': True, 'message': f'{account_type.capitalize()} account logged in successfully'})
    except Exception as e:
        error_msg = str(e)
        # Extract more detailed error if available
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = e.response.json()
                error_msg = error_detail.get('error_description', error_detail.get('error', str(e)))
            except:
                try:
                    error_msg = e.response.text[:200] or str(e)
                except:
                    pass
        login_status[account_type] = {'logged_in': False, 'error': error_msg}
        return jsonify({'success': False, 'message': f'Login failed: {error_msg}'})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
