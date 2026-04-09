import time
import uuid
import json
from datetime import datetime
from utils.tradestation_api import TradeStationAPI

class TradeCopier:
    def __init__(self, settings, log_callback):
        self.settings = settings
        self.log_callback = log_callback
        self.running = False
        self.master_api = None
        self.client_api = None
        self.master_positions_by_order_id = {}
        self.master_to_copier_map = {}
        self.client_mirrored_by_map = {}
        self.empty_position_poll_count = 0
        self.initialized_baseline = False
        
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
        """Start monitoring and copying net positions from master to client."""
        self.running = True
        print("Trade copier started. Monitoring master positions every second...")
        
        while self.running:
            try:
                if not self.master_api or not self.client_api:
                    time.sleep(1)
                    continue

                if not self.initialized_baseline:
                    self.initialize_startup_baseline()
                    time.sleep(1)
                    continue

                self.sync_positions_once()

                # Poll interval: 500ms, as requested.
                time.sleep(0.5)
                
            except Exception as e:
                print(f"Error in trade copier: {e}")
                time.sleep(0.5)

    def initialize_startup_baseline(self):
        """
        Capture current master positions at copier startup and DO NOT copy them.
        Only positions created/changed after this point are mirrored.
        """
        startup_positions = self.master_api.get_positions()
        baseline = self._normalize_master_positions(startup_positions)
        self.master_positions_by_order_id = baseline
        self.initialized_baseline = True
        print(
            "[COPIER] Startup baseline initialized. "
            f"Existing master positions ignored for copying: {len(baseline)}"
        )
        for baseline_order_id, position in baseline.items():
            print(
                "[COPIER] Baseline position -> "
                f"master_order_id={baseline_order_id}, symbol={position.get('symbol')}, qty={position.get('quantity')}"
            )

    def sync_positions_once(self):
        """Fetch master net positions and mirror deltas to client by master order ID."""
        master_positions = self.master_api.get_positions()
        self._print_position_poll(master_positions)
        current_positions = self._normalize_master_positions(master_positions)

        previous_order_ids = set(self.master_positions_by_order_id.keys())
        current_order_ids = set(current_positions.keys())

        # New positions + quantity changes
        for order_id in current_order_ids:
            current = current_positions[order_id]
            previous = self.master_positions_by_order_id.get(order_id)

            if previous is None:
                # New open position in master -> open in client
                self.copy_order(
                    symbol=current['symbol'],
                    quantity=current['quantity'],
                    side='BUY' if current['quantity'] > 0 else 'SELL',
                    order_type='Market',
                    master_order_id=order_id,
                    action_label='buy' if current['quantity'] > 0 else 'sell'
                )
            else:
                prev_qty = previous['quantity']
                curr_qty = current['quantity']

                if curr_qty == prev_qty:
                    continue

                # If master flips side on same position key, close old side first.
                if prev_qty * curr_qty < 0:
                    self.copy_order(
                        symbol=previous['symbol'],
                        quantity=-prev_qty,
                        side='BUY' if -prev_qty > 0 else 'SELL',
                        order_type='Market',
                        master_order_id=order_id,
                        action_label='buy exit' if prev_qty < 0 else 'sell exit'
                    )
                    self.copy_order(
                        symbol=current['symbol'],
                        quantity=curr_qty,
                        side='BUY' if curr_qty > 0 else 'SELL',
                        order_type='Market',
                        master_order_id=order_id,
                        action_label='buy' if curr_qty > 0 else 'sell'
                    )
                    continue

                delta_qty = curr_qty - prev_qty
                if delta_qty != 0:
                    # Position scaled up/down in master -> mirror net delta in client
                    is_expanding = abs(curr_qty) > abs(prev_qty)
                    if is_expanding:
                        action_label = 'buy' if delta_qty > 0 else 'sell'
                    else:
                        action_label = 'buy exit' if delta_qty > 0 else 'sell exit'

                    self.copy_order(
                        symbol=current['symbol'],
                        quantity=delta_qty,
                        side='BUY' if delta_qty > 0 else 'SELL',
                        order_type='Market',
                        master_order_id=order_id,
                        action_label=action_label
                    )

        # Closed positions
        closed_order_ids = previous_order_ids - current_order_ids
        for closed_order_id in closed_order_ids:
            closed_position = self.master_positions_by_order_id.get(closed_order_id)
            if not closed_position:
                continue

            map_key = str(closed_order_id)
            mirrored_state = self.client_mirrored_by_map.get(map_key, {})
            mirrored_qty = mirrored_state.get('quantity', 0)

            # Close only the quantity tracked for this map key.
            if mirrored_qty:
                close_qty = -mirrored_qty
                close_symbol = mirrored_state.get('symbol') or closed_position['symbol']
            else:
                # Fallback if mirrored state is unavailable.
                close_qty = -closed_position['quantity']
                close_symbol = closed_position['symbol']

            if close_qty == 0:
                continue

            print(
                "[COPIER] Closing mapped position -> "
                f"master_order_id={closed_order_id}, close_qty={close_qty}, "
                f"tracked_client_qty={mirrored_qty}"
            )
            self.copy_order(
                symbol=close_symbol,
                quantity=close_qty,
                side='BUY' if close_qty > 0 else 'SELL',
                order_type='Market',
                master_order_id=closed_order_id,
                action_label='buy exit' if closed_position['quantity'] < 0 else 'sell exit'
            )

        self.master_positions_by_order_id = current_positions

    def _print_position_poll(self, master_positions):
        """Print compact position summary every poll."""
        poll_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n[POSITION POLL {poll_time}] Master net positions:")
        if self.master_api:
            print(
                f"[POSITION POLL] Master environment={self.master_api.environment}, "
                f"account_id={self.master_api.account_id}"
            )
        if not isinstance(master_positions, list):
            print("[POSITION POLL] Position count: 0")
            print("[POSITION POLL] No position list returned.")
            return

        print(f"[POSITION POLL] Position count: {len(master_positions)}")
        if not master_positions:
            print("[POSITION POLL] No open positions.")
        else:
            for position in master_positions:
                if not isinstance(position, dict):
                    continue
                position_id = self._extract_order_id(position)
                symbol = position.get('Symbol') or position.get('symbol') or 'N/A'
                qty = self._extract_signed_quantity(position)
                side = (position.get('LongShort') or position.get('Side') or '').upper() or ('LONG' if qty > 0 else 'SHORT')
                avg_price = position.get('AveragePrice') or position.get('AvgPrice') or 'N/A'
                print(
                    f"[POSITION] id={position_id}, symbol={symbol}, qty={qty}, side={side}, avg={avg_price}"
                )

        if isinstance(master_positions, list) and len(master_positions) == 0:
            self.empty_position_poll_count += 1
            # Every 5 empty polls, print recent orders for debugging visibility.
            if self.empty_position_poll_count % 5 == 0:
                self._print_recent_master_orders_debug()
        else:
            self.empty_position_poll_count = 0

    def _print_recent_master_orders_debug(self):
        """Print a compact master-order snapshot when positions remain empty."""
        if not self.master_api:
            return
        try:
            recent_orders = self.master_api.get_orders()
            if not isinstance(recent_orders, list):
                recent_orders = []
            print(f"[POSITION POLL][DEBUG] Recent master orders count: {len(recent_orders)}")
            for order in recent_orders[:5]:
                if not isinstance(order, dict):
                    continue
                print(
                    "[POSITION POLL][DEBUG] "
                    f"OrderID={order.get('OrderID')}, "
                    f"Symbol={order.get('Symbol')}, "
                    f"Status={order.get('Status')}, "
                    f"TradeAction={order.get('TradeAction')}, "
                    f"Qty={order.get('Quantity')}"
                )
        except Exception as e:
            print(f"[POSITION POLL][DEBUG] Could not fetch recent master orders: {e}")

    def _normalize_master_positions(self, raw_positions):
        """Normalize positions into {master_order_id: {symbol, quantity}}."""
        normalized = {}
        if not isinstance(raw_positions, list):
            return normalized

        for position in raw_positions:
            if not isinstance(position, dict):
                continue

            order_id = self._extract_order_id(position)
            symbol = position.get('Symbol') or position.get('symbol')
            signed_qty = self._extract_signed_quantity(position)

            if not order_id or not symbol or signed_qty == 0:
                continue

            normalized[str(order_id)] = {
                'symbol': str(symbol),
                'quantity': signed_qty
            }

        return normalized

    def _extract_order_id(self, position):
        """Try common TradeStation keys that identify the opening order/position."""
        return (
            position.get('OrderID')
            or position.get('orderId')
            or position.get('PositionID')
            or position.get('PositionId')
            or position.get('ID')
            or position.get('Id')
        )

    def _extract_signed_quantity(self, position):
        """Build signed quantity from Quantity and optional side hints."""
        raw_qty = (
            position.get('Quantity')
            or position.get('quantity')
            or position.get('OpenQuantity')
            or position.get('openQuantity')
            or 0
        )
        try:
            qty = int(float(raw_qty))
        except (TypeError, ValueError):
            return 0

        side = (position.get('LongShort') or position.get('Side') or '').upper()
        if side in ('SHORT', 'SELL'):
            qty = -abs(qty)
        elif side in ('LONG', 'BUY'):
            qty = abs(qty)

        return qty
    
    def copy_order(self, symbol, quantity, side, order_type='Market', price=None, master_order_id=None, action_label='buy'):
        """Copy an order from master to client account and log it."""
        if not self.master_api or not self.client_api:
            return
        
        # Unique ID generated by copier system for traceability.
        order_id = f"COPY-{uuid.uuid4().hex[:12].upper()}"
        mapping_key = str(master_order_id or '')
        if mapping_key:
            self.master_to_copier_map[mapping_key] = order_id
        print(
            "[COPIER] Replication request -> "
            f"copier_order_id={order_id}, master_order_id={master_order_id}, "
            f"action={action_label}, symbol={symbol}, quantity={quantity}, side={side}"
        )
        
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
            client_order_id = self._extract_client_order_id(client_result.get('order', {}))
            client_response_text = self._to_compact_json(client_result.get('order', {}))
            print(
                "[COPIER] Replication SUCCESS -> "
                f"copier_order_id={order_id}, master_order_id={master_order_id}, client_order_id={client_order_id}"
            )
            print(f"[COPIER] Client response: {client_response_text}")
            self._update_mirrored_tracking(str(master_order_id or ''), symbol, quantity)
            
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
            client_order_id = ''
            client_response_text = client_result.get('error', '')
            print(
                "[COPIER] Replication FAILED -> "
                f"copier_order_id={order_id}, master_order_id={master_order_id}, "
                f"error={client_result.get('error', '')}"
            )
            print(f"[COPIER] Client response: {client_response_text}")
        
        # Log the order
        order_data = {
            'timestamp': datetime.now().isoformat(),
            'order_id': order_id,
            'copier_order_id': order_id,
            'master_order_id': master_order_id or '',
            'action': action_label,
            'symbol': symbol,
            'quantity': abs(quantity),
            'side': side,
            'order_type': order_type,
            'mapping_key': mapping_key,
            'client_order_id': client_order_id,
            'client_response': client_response_text,
            'master_status': 'DETECTED',
            'client_status': 'SUCCESS' if client_result['success'] else 'FAILED',
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

    def _update_mirrored_tracking(self, map_key, symbol, quantity):
        """Track client mirrored quantity by map key for precise close handling."""
        if not map_key:
            return
        state = self.client_mirrored_by_map.get(map_key, {'symbol': symbol, 'quantity': 0})
        state['symbol'] = symbol or state.get('symbol')
        state['quantity'] = int(state.get('quantity', 0)) + int(quantity)
        if state['quantity'] == 0:
            self.client_mirrored_by_map.pop(map_key, None)
        else:
            self.client_mirrored_by_map[map_key] = state

    def _extract_client_order_id(self, order_response):
        """Extract best-effort client order ID from order placement response."""
        if not isinstance(order_response, dict):
            return ''
        for key in ('OrderID', 'orderID', 'OrderId', 'orderId', 'Id', 'ID'):
            value = order_response.get(key)
            if value:
                return str(value)
        if isinstance(order_response.get('Orders'), list) and order_response['Orders']:
            first = order_response['Orders'][0]
            if isinstance(first, dict):
                return self._extract_client_order_id(first)
        return ''

    def _to_compact_json(self, value):
        try:
            return json.dumps(value, separators=(',', ':'), default=str)
        except Exception:
            return str(value)
    
    def stop(self):
        """Stop the trade copier"""
        self.running = False
        print("Trade copier stopped.")