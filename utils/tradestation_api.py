import requests
import base64
import json
import copy
from datetime import datetime

class TradeStationAPI:
    def __init__(self, client_id, client_secret, account_id, environment='paper', refresh_token=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.account_id = account_id
        self.environment = environment
        self.refresh_token = refresh_token
        
        # Set base URL based on environment
        if environment == 'paper':
            self.base_url = 'https://sim-api.tradestation.com/v3'
        else:
            self.base_url = 'https://api.tradestation.com/v3'
        
        self.access_token = None
        self.token_expiry = None

    def _normalize_duration_for_order_api(self, duration):
        raw = str(duration or '').strip().upper()
        compact = raw.replace(' ', '')
        if not compact:
            return 'DAY'
        duration_aliases = {
            'DAY+': 'DYP',
            'DYP': 'DYP',
            'GTC+': 'GCP',
            'GCP': 'GCP',
            'GTD+': 'GDP',
            'GDP': 'GDP',
            '1MIN': '1',
            '1': '1',
            '3MIN': '3',
            '3': '3',
            '5MIN': '5',
            '5': '5',
            'DAY': 'DAY',
            'GTC': 'GTC',
            'GTD': 'GTD',
            'OPG': 'OPG',
            'CLO': 'CLO',
            'IOC': 'IOC',
            'FOK': 'FOK',
        }
        return duration_aliases.get(compact, raw)
        
    def authenticate(self):
        """Authenticate and get access token using refresh token"""
        if not self.refresh_token:
            raise ValueError("Refresh token is required. Please obtain a refresh token first through OAuth flow.")
        
        # TradeStation uses signin.tradestation.com for OAuth
        auth_url = "https://signin.tradestation.com/oauth/token"
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        # Use refresh token to get access token (as shown in sample code)
        payload = f'grant_type=refresh_token&client_id={self.client_id}&client_secret={self.client_secret}&refresh_token={self.refresh_token}'
        
        try:
            response = requests.post(auth_url, headers=headers, data=payload, timeout=10)
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data.get('access_token')
            # Access tokens last for 20 minutes (1200 seconds)
            expires_in = token_data.get('expires_in', 1200)
            self.token_expiry = datetime.now().timestamp() + expires_in
            
            # Optionally update refresh token if a new one is provided
            if 'refresh_token' in token_data:
                self.refresh_token = token_data.get('refresh_token')
            
            return True
        except Exception as e:
            print(f"Authentication error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    print(f"Error details: {error_detail}")
                    error_msg = error_detail.get('error_description', error_detail.get('error', str(e)))
                    raise Exception(f"Authentication failed: {error_msg}")
                except:
                    print(f"Response text: {e.response.text}")
            raise
    
    def ensure_authenticated(self):
        """Ensure we have a valid access token"""
        if not self.access_token or (self.token_expiry and datetime.now().timestamp() >= self.token_expiry):
            self.authenticate()
    
    def get_positions(self):
        """Get current positions for the account"""
        self.ensure_authenticated()
        
        url = f"{self.base_url}/brokerage/accounts/{self.account_id}/positions"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        try:
            print(f"[API][POSITIONS] Environment: {self.environment}")
            print(f"[API][POSITIONS] Account ID: {self.account_id}")
            print(f"[API][POSITIONS] Endpoint: {url}")
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            positions_payload = response.json()
            print(f"[API][POSITIONS] HTTP {response.status_code}")
            print(f"[API][POSITIONS] Raw payload: {positions_payload}")

            # TradeStation responses can be list or wrapped object.
            if isinstance(positions_payload, list):
                return positions_payload
            if isinstance(positions_payload, dict):
                if isinstance(positions_payload.get('Positions'), list):
                    return positions_payload.get('Positions', [])
                if isinstance(positions_payload.get('positions'), list):
                    return positions_payload.get('positions', [])
                if isinstance(positions_payload.get('Items'), list):
                    return positions_payload.get('Items', [])
                # Some payloads return a single position object
                if positions_payload.get('Symbol') and (
                    positions_payload.get('Quantity') is not None or positions_payload.get('quantity') is not None
                ):
                    return [positions_payload]
            return []
        except Exception as e:
            print(f"Error fetching positions: {e}")
            raise
    
    def place_order(
        self,
        symbol,
        quantity,
        side,
        order_type='Market',
        price=None,
        trade_action=None,
        duration='DAY',
        stop_price=None,
        passthrough_fields=None
    ):
        """Place an order"""
        self.ensure_authenticated()
        
        # Use actual order placement endpoint (not orderconfirm preview endpoint).
        url = f"{self.base_url}/orderexecution/orders"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        normalized_trade_action = str(trade_action or "").upper().replace(" ", "")
        if not normalized_trade_action:
            normalized_trade_action = 'BUY' if quantity > 0 else 'SELL'
        normalized_duration = self._normalize_duration_for_order_api(duration)

        order_data = {
            'AccountID': self.account_id,
            'Symbol': symbol,
            'Quantity': str(abs(quantity)),
            'OrderType': order_type,
            'TradeAction': normalized_trade_action,
        }

        # Carry master order fields that are safe/valid for order placement.
        if isinstance(passthrough_fields, dict):
            for key in ('TimeInForce', 'Route', 'AdvancedOptions', 'StopPrice'):
                value = passthrough_fields.get(key)
                if value in (None, ''):
                    continue
                order_data[key] = copy.deepcopy(value)

        tif_value = order_data.get('TimeInForce')
        if isinstance(tif_value, dict):
            existing_duration = tif_value.get('Duration') or tif_value.get('duration')
            if existing_duration:
                normalized_duration = self._normalize_duration_for_order_api(existing_duration)
        elif isinstance(tif_value, str):
            normalized_duration = self._normalize_duration_for_order_api(tif_value)
        order_data['TimeInForce'] = {'Duration': normalized_duration}
        order_data.setdefault('Route', 'Intelligent')
        
        if order_type == 'Limit' and price:
            order_data['LimitPrice'] = str(price)
        if stop_price not in (None, ''):
            order_data['StopPrice'] = str(stop_price)
        
        try:
            request_time = datetime.now().isoformat()
            print(f"[API][PLACE_ORDER] Environment: {self.environment}")
            print(f"[API][PLACE_ORDER] Account ID: {self.account_id}")
            print(f"[API][PLACE_ORDER] Endpoint: {url}")
            print(f"[API][PLACE_ORDER] Payload: {order_data}")
            response = requests.post(url, headers=headers, json=order_data, timeout=10)
            response_time = datetime.now().isoformat()
            response.raise_for_status()
            order_result = response.json()
            print(f"[API][PLACE_ORDER] HTTP {response.status_code}")
            print(f"[API][PLACE_ORDER] Response: {order_result}")

            # TradeStation can return HTTP 200 with order-level failure in payload.
            if isinstance(order_result, dict) and isinstance(order_result.get('Orders'), list) and order_result['Orders']:
                first_order = order_result['Orders'][0]
                if isinstance(first_order, dict):
                    error_value = str(first_order.get('Error', '')).upper()
                    if error_value in {'FAILED', 'ERROR'}:
                        message = first_order.get('Message', 'Order rejected')
                        print(f"[API][PLACE_ORDER] ORDER-LEVEL FAILURE: {message}")
                        return {
                            'success': False,
                            'error': message,
                            'order': order_result,
                            'request_time': request_time,
                            'response_time': response_time
                        }
            
            return {
                'success': True,
                'order': order_result,
                'request_time': request_time,
                'response_time': response_time
            }
        except Exception as e:
            response_time = datetime.now().isoformat()
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    error_msg = str(error_detail)
                except:
                    error_msg = e.response.text or str(e)

            # Some environments reject DAY+ (or other durations) even when present
            # on source orders. Retry once with DAY so replication still executes.
            if 'INVALID DURATION' in error_msg.upper() and normalized_duration != 'DAY':
                fallback_payload = copy.deepcopy(order_data)
                fallback_payload['TimeInForce'] = {'Duration': 'DAY'}
                fallback_request_time = datetime.now().isoformat()
                try:
                    print(
                        "[API][PLACE_ORDER] Invalid duration rejected; "
                        f"retrying with DAY (original={normalized_duration})"
                    )
                    print(f"[API][PLACE_ORDER] Fallback payload: {fallback_payload}")
                    fallback_response = requests.post(url, headers=headers, json=fallback_payload, timeout=10)
                    fallback_response_time = datetime.now().isoformat()
                    fallback_response.raise_for_status()
                    fallback_result = fallback_response.json()
                    print(f"[API][PLACE_ORDER] FALLBACK HTTP {fallback_response.status_code}")
                    print(f"[API][PLACE_ORDER] FALLBACK Response: {fallback_result}")
                    return {
                        'success': True,
                        'order': fallback_result,
                        'request_time': fallback_request_time,
                        'response_time': fallback_response_time
                    }
                except Exception as retry_error:
                    retry_msg = str(retry_error)
                    if hasattr(retry_error, 'response') and retry_error.response is not None:
                        try:
                            retry_msg = str(retry_error.response.json())
                        except:
                            retry_msg = retry_error.response.text or str(retry_error)
                    error_msg = f"{error_msg} | fallback DAY failed: {retry_msg}"
            print(f"[API][PLACE_ORDER] ERROR: {error_msg}")
            return {
                'success': False,
                'error': error_msg,
                'request_time': request_time,
                'response_time': response_time
            }
    
    def get_account_info(self):
        """Get account information"""
        self.ensure_authenticated()
        
        # Get all accounts and find the matching one
        url = f"{self.base_url}/brokerage/accounts"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            accounts = response.json()
            if isinstance(accounts, list) and self.account_id:
                # Find the account matching account_id
                for account in accounts:
                    if account.get('AccountID') == self.account_id or account.get('Account') == self.account_id:
                        return account
            return accounts
        except Exception as e:
            print(f"Error fetching account info: {e}")
            raise
    
    def get_orders(self, since_date=None):
        """Get orders for the account"""
        self.ensure_authenticated()
        
        url = f"{self.base_url}/brokerage/accounts/{self.account_id}/orders"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        params = {}
        if since_date:
            params['since'] = since_date
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            orders = response.json()
            return orders if isinstance(orders, list) else []
        except Exception as e:
            print(f"Error fetching orders: {e}")
            raise
    
    def get_historical_orders(self, since_date=None):
        """Get historical orders for the account"""
        self.ensure_authenticated()
        
        url = f"{self.base_url}/brokerage/accounts/{self.account_id}/historicalorders"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        params = {}
        if since_date:
            params['since'] = since_date
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            orders = response.json()
            return orders if isinstance(orders, list) else []
        except Exception as e:
            print(f"Error fetching historical orders: {e}")
            raise
    
    def get_account_balance(self):
        """Get account balance"""
        self.ensure_authenticated()
        
        if not self.account_id:
            raise ValueError("Account ID is required to get balance")
        
        url = f"{self.base_url}/brokerage/accounts/{self.account_id}/balances"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        print(f"[API] Calling balance endpoint: {url}")
        print(f"[API] Account ID: {self.account_id}")
        print(f"[API] Access token present: {bool(self.access_token)}")
        print(f"[API] Access token (first 20 chars): {self.access_token[:20] if self.access_token else 'None'}...")
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            print(f"[API] Response status code: {response.status_code}")
            print(f"[API] Response headers: {dict(response.headers)}")
            
            # Print raw response text before parsing (full response)
            print(f"[API] Raw response text (full): {response.text}")
            
            response.raise_for_status()
            balance_data = response.json()
            
            print(f"[API] Parsed JSON response type: {type(balance_data)}")
            print(f"[API] Parsed JSON response: {balance_data}")
            
            return balance_data
        except requests.exceptions.HTTPError as e:
            print(f"[API] HTTP Error fetching account balance: {e}")
            print(f"[API] Response status: {response.status_code}")
            print(f"[API] Response text: {response.text}")
            raise
        except Exception as e:
            print(f"[API] Error fetching account balance: {e}")
            print(f"[API] Error type: {type(e)}")
            import traceback
            print(f"[API] Traceback: {traceback.format_exc()}")
            raise
