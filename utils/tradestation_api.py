import requests
import base64
import json
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
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            positions = response.json()
            return positions if isinstance(positions, list) else []
        except Exception as e:
            print(f"Error fetching positions: {e}")
            raise
    
    def place_order(self, symbol, quantity, side, order_type='Market', price=None):
        """Place an order"""
        self.ensure_authenticated()
        
        url = f"{self.base_url}/orderexecution/orderconfirm"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        order_data = {
            'AccountID': self.account_id,
            'Symbol': symbol,
            'Quantity': str(abs(quantity)),
            'OrderType': order_type,
            'TradeAction': 'BUY' if quantity > 0 else 'SELL',
            'TimeInForce': {'Duration': 'DAY'},
            'Route': 'Intelligent'
        }
        
        if order_type == 'Limit' and price:
            order_data['LimitPrice'] = str(price)
        
        try:
            request_time = datetime.now().isoformat()
            response = requests.post(url, headers=headers, json=order_data, timeout=10)
            response_time = datetime.now().isoformat()
            response.raise_for_status()
            order_result = response.json()
            
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
