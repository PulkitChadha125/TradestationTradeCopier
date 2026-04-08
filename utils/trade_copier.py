import time
from datetime import datetime, timedelta
from utils.tradestation_api import TradeStationAPI

class TradeCopier:
    def __init__(self, settings, log_callback):
        self.settings = settings
        self.log_callback = log_callback
        self.running = False
        self.master_api = None
        self.client_api = None
        self.processed_order_ids = set()  # Track processed orders
        self.order_counter = 0
        self.start_time = datetime.now()
        
        # Initialize APIs
        global_settings = settings.get('global', {})
        master_settings = settings.get('master', {})
        client_settings = settings.get('client', {})
        
        if global_settings.get('api_key') and master_settings.get('refresh_token'):
            self.master_api = TradeStationAPI(
                client_id=global_settings['api_key'],
                client_secret=global_settings['api_secret'],
                account_id=master_settings.get('account_id', ''),
                environment=master_settings.get('environment', 'paper'),
                refresh_token=master_settings.get('refresh_token', '')
            )
        
        if global_settings.get('api_key') and client_settings.get('refresh_token'):
            self.client_api = TradeStationAPI(
                client_id=global_settings['api_key'],
                client_secret=global_settings['api_secret'],
                account_id=client_settings.get('account_id', ''),
                environment=client_settings.get('environment', 'paper'),
                refresh_token=client_settings.get('refresh_token', '')
            )
    
    def start(self):
        """Start monitoring and copying orders"""
        self.running = True
        self.start_time = datetime.now()
        
        print("Trade copier started. Monitoring master account orders...")
        
        while self.running:
            try:
                if not self.master_api or not self.client_api:
                    time.sleep(5)
                    continue
                
                # Get orders from master account (only new orders since start time)
                # Format: YYYY-MM-DD
                since_date = self.start_time.strftime('%Y-%m-%d')
                
                try:
                    # Get current orders (active + today's orders)
                    current_orders = self.master_api.get_orders()
                    
                    # Also get historical orders from today
                    historical_orders = self.master_api.get_historical_orders(since_date=since_date)
                    
                    # Combine and deduplicate
                    all_orders = {}
                    for order in current_orders:
                        order_id = order.get('OrderID') or order.get('ID') or str(order)
                        if order_id:
                            all_orders[order_id] = order
                    
                    for order in historical_orders:
                        order_id = order.get('OrderID') or order.get('ID') or str(order)
                        if order_id:
                            all_orders[order_id] = order
                    
                    # Process only new orders (not in processed_order_ids)
                    for order_id, order in all_orders.items():
                        if order_id not in self.processed_order_ids:
                            # This is a new order, copy it to client account
                            self.process_new_order(order, order_id)
                            self.processed_order_ids.add(order_id)
                
                except Exception as e:
                    print(f"Error fetching orders: {e}")
                    time.sleep(5)
                    continue
                
                # Sleep before next check
                time.sleep(2)  # Check every 2 seconds
                
            except Exception as e:
                print(f"Error in trade copier: {e}")
                time.sleep(5)
    
    def process_new_order(self, order, order_id):
        """Process a new order from master account and copy to client"""
        if not self.master_api or not self.client_api:
            return
        
        try:
            # Extract order details
            symbol = order.get('Symbol', '')
            quantity = order.get('Quantity', 0)
            trade_action = order.get('TradeAction', '')
            order_type = order.get('OrderType', 'Market')
            limit_price = order.get('LimitPrice')
            
            if not symbol or quantity == 0:
                print(f"Skipping order {order_id}: Invalid symbol or quantity")
                return
            
            # Determine side based on TradeAction
            if trade_action.upper() in ['BUY', 'BUYTOCOVER']:
                side = 'BUY'
                qty = abs(quantity)
            elif trade_action.upper() in ['SELL', 'SELLSHORT']:
                side = 'SELL'
                qty = -abs(quantity)
            else:
                # Try to infer from quantity
                qty = quantity
                side = 'BUY' if quantity > 0 else 'SELL'
            
            print(f"Processing new order: {symbol} {side} {abs(qty)} (Order ID: {order_id})")
            
            # Copy order to client account
            self.copy_order(symbol, qty, side, order_type, limit_price, order_id)
            
        except Exception as e:
            print(f"Error processing order {order_id}: {e}")
    
    def copy_order(self, symbol, quantity, side, order_type='Market', price=None, master_order_id=None):
        """Copy an order from master to client account"""
        if not self.master_api or not self.client_api:
            return
        
        self.order_counter += 1
        order_id = f"ORDER_{self.order_counter}_{int(time.time())}"
        
        # Place order on client account
        master_request_time = datetime.now().isoformat()
        client_result = self.client_api.place_order(
            symbol=symbol,
            quantity=quantity,
            side=side,
            order_type=order_type,
            price=price
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
            'master_order_id': master_order_id or '',
            'symbol': symbol,
            'quantity': abs(quantity),
            'side': side,
            'order_type': order_type,
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
        print("Trade copier stopped.")