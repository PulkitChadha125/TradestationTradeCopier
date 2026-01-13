import time
from datetime import datetime
from utils.tradestation_api import TradeStationAPI

class TradeCopier:
    def __init__(self, settings, log_callback):
        self.settings = settings
        self.log_callback = log_callback
        self.running = False
        self.master_api = None
        self.client_api = None
        self.last_positions = {}
        self.order_counter = 0
        
        # Initialize APIs
        if settings.get('master', {}).get('client_id'):
            self.master_api = TradeStationAPI(
                client_id=settings['master']['client_id'],
                client_secret=settings['master']['client_secret'],
                account_id=settings['master'].get('account_id', ''),
                environment=settings['master'].get('environment', 'paper'),
                refresh_token=settings['master'].get('refresh_token', '')
            )
        
        if settings.get('client', {}).get('client_id'):
            self.client_api = TradeStationAPI(
                client_id=settings['client']['client_id'],
                client_secret=settings['client']['client_secret'],
                account_id=settings['client'].get('account_id', ''),
                environment=settings['client'].get('environment', 'paper'),
                refresh_token=settings['client'].get('refresh_token', '')
            )
    
    def start(self):
        """Start monitoring and copying trades"""
        self.running = True
        
        while self.running:
            try:
                if not self.master_api or not self.client_api:
                    time.sleep(5)
                    continue
                
                # Get current master positions
                master_positions = self.master_api.get_positions()
                
                # Convert to dict for easier comparison
                current_positions = {}
                for pos in master_positions:
                    symbol = pos.get('Symbol', '')
                    quantity = pos.get('Quantity', 0)
                    if symbol:
                        current_positions[symbol] = quantity
                
                # Compare with last known positions
                for symbol, quantity in current_positions.items():
                    last_quantity = self.last_positions.get(symbol, 0)
                    
                    if quantity != last_quantity:
                        # Position changed, calculate difference
                        diff = quantity - last_quantity
                        
                        if diff != 0:
                            # Copy trade to client account
                            self.copy_trade(symbol, diff)
                
                # Update last positions
                self.last_positions = current_positions.copy()
                
                # Check for positions that were closed
                for symbol, last_quantity in self.last_positions.items():
                    if symbol not in current_positions and last_quantity != 0:
                        # Position was closed, close it in client account too
                        self.copy_trade(symbol, -last_quantity)
                
                # Update last positions again after closing
                self.last_positions = current_positions.copy()
                
                # Sleep before next check
                time.sleep(2)  # Check every 2 seconds
                
            except Exception as e:
                print(f"Error in trade copier: {e}")
                time.sleep(5)
    
    def copy_trade(self, symbol, quantity):
        """Copy a trade from master to client account"""
        if not self.master_api or not self.client_api:
            return
        
        self.order_counter += 1
        order_id = f"ORDER_{self.order_counter}_{int(time.time())}"
        
        # Place order on client account
        master_request_time = datetime.now().isoformat()
        client_result = self.client_api.place_order(
            symbol=symbol,
            quantity=quantity,
            side='BUY' if quantity > 0 else 'SELL',
            order_type='Market'
        )
        
        # Calculate latencies
        master_response_time = datetime.now().isoformat()
        master_latency = 0  # Master doesn't place order, just monitors
        
        if client_result['success']:
            client_request_time = client_result['request_time']
            client_response_time = client_result['response_time']
            
            # Calculate latency in milliseconds
            try:
                req_time = datetime.fromisoformat(client_request_time.replace('Z', '+00:00'))
                resp_time = datetime.fromisoformat(client_response_time.replace('Z', '+00:00'))
                client_latency = (resp_time - req_time).total_seconds() * 1000
            except:
                client_latency = 0
        else:
            client_request_time = client_result.get('request_time', '')
            client_response_time = client_result.get('response_time', '')
            client_latency = 0
        
        # Log the order
        order_data = {
            'timestamp': datetime.now().isoformat(),
            'order_id': order_id,
            'symbol': symbol,
            'quantity': abs(quantity),
            'side': 'BUY' if quantity > 0 else 'SELL',
            'order_type': 'Market',
            'master_request_time': master_request_time,
            'master_response_time': master_response_time,
            'master_latency': f"{master_latency:.2f}",
            'client_request_time': client_request_time,
            'client_response_time': client_response_time,
            'client_latency': f"{client_latency:.2f}",
            'status': 'SUCCESS' if client_result['success'] else 'FAILED',
            'error': client_result.get('error', '')
        }
        
        if self.log_callback:
            self.log_callback(order_data)
    
    def stop(self):
        """Stop the trade copier"""
        self.running = False
