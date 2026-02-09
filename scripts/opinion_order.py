"""
Opinion.trade CLOB SDK Order Script
Place limit/market orders, cancel orders, view positions

Requirements:
    pip install opinion_clob_sdk

Usage:
    python scripts/opinion_order.py --action markets          # List markets
    python scripts/opinion_order.py --action orderbook --token TOKEN_ID
    python scripts/opinion_order.py --action buy --market 813 --token TOKEN_ID --price 0.45 --amount 100
    python scripts/opinion_order.py --action sell --market 813 --token TOKEN_ID --price 0.85 --amount 50
    python scripts/opinion_order.py --action orders --market 813
    python scripts/opinion_order.py --action cancel --order ORDER_ID
    python scripts/opinion_order.py --action cancel_all --market 813
    python scripts/opinion_order.py --action balances
    python scripts/opinion_order.py --action positions
"""

import os
import sys
import argparse
import logging
import json
from datetime import datetime

# Add project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

logger = logging.getLogger(__name__)

# ============================================================
# Configuration
# ============================================================

DEFAULT_CONFIG = {
    'host': 'https://proxy.opinion.trade:8443',
    'chain_id': 56,  # BNB Chain Mainnet
    'rpc_url': 'https://bsc-dataseed.binance.org',
}


def load_credentials():
    """Load credentials from config.yaml or environment"""
    api_key = os.getenv('OPINION_API_KEY', '')
    private_key = os.getenv('OPINION_PRIVATE_KEY', '')
    wallet_address = os.getenv('OPINION_WALLET_ADDRESS', '')

    # Try config.yaml
    if not api_key:
        try:
            import yaml
            config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                api_key = config.get('opinion', {}).get('api_key', '')
                wallet_address = wallet_address or config.get('opinion', {}).get('wallet_address', '')
        except Exception:
            pass

    return {
        'api_key': api_key,
        'private_key': private_key,
        'wallet_address': wallet_address,
    }


def create_client(credentials, read_only=False):
    """Create Opinion CLOB SDK client"""
    try:
        from opinion_clob_sdk import Client
    except ImportError:
        print("Error: opinion_clob_sdk not installed.")
        print("Run: pip install opinion_clob_sdk")
        sys.exit(1)

    api_key = credentials['api_key']
    if not api_key:
        print("Error: OPINION_API_KEY not set.")
        print("Set env var OPINION_API_KEY or configure in config.yaml")
        sys.exit(1)

    kwargs = {
        'host': DEFAULT_CONFIG['host'],
        'apikey': api_key,
        'chain_id': DEFAULT_CONFIG['chain_id'],
        'rpc_url': DEFAULT_CONFIG['rpc_url'],
    }

    # Trading requires private key
    if not read_only:
        private_key = credentials['private_key']
        wallet_address = credentials['wallet_address']

        if not private_key:
            print("Error: OPINION_PRIVATE_KEY not set (required for trading).")
            print("Set env var OPINION_PRIVATE_KEY")
            sys.exit(1)

        kwargs['private_key'] = private_key
        if wallet_address:
            kwargs['multi_sig_addr'] = wallet_address

    return Client(**kwargs)


# ============================================================
# Read-Only Operations (API key only)
# ============================================================

def list_markets(client, limit=20):
    """List active markets"""
    print(f"\n{'='*80}")
    print(f"  Opinion.trade Active Markets")
    print(f"{'='*80}\n")

    try:
        result = client.get_markets(status='activated', limit=limit)
        markets = result.get('result', {}).get('list', []) if isinstance(result, dict) else []

        if not markets:
            # Fallback: use OpenAPI directly
            import requests
            resp = requests.get(
                f"{DEFAULT_CONFIG['host']}/openapi/market",
                headers={'apikey': client.apikey if hasattr(client, 'apikey') else ''},
                params={'status': 'activated', 'limit': limit},
                timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get('code') == 0:
                    markets = data.get('result', {}).get('list', [])

        for i, m in enumerate(markets[:limit], 1):
            title = m.get('marketTitle', 'Unknown')
            market_id = m.get('marketId', '')
            yes_token = m.get('yesTokenId', '')[:20]
            no_token = m.get('noTokenId', '')[:20]
            volume = m.get('volume24h', m.get('volume', 0))
            status = m.get('statusEnum', '')

            print(f"[{i:2d}] {title}")
            print(f"     ID: {market_id}  Status: {status}")
            print(f"     Yes Token: {yes_token}...")
            print(f"     No Token:  {no_token}...")
            print(f"     24h Volume: ${float(volume or 0):,.0f}")
            print()

        print(f"Total: {len(markets)} markets")

    except Exception as e:
        print(f"Error: {e}")


def show_orderbook(client, token_id):
    """Show orderbook for a token"""
    print(f"\n{'='*60}")
    print(f"  Orderbook: {token_id[:30]}...")
    print(f"{'='*60}\n")

    try:
        result = client.get_orderbook(token_id=token_id)
        book = result.get('result', result) if isinstance(result, dict) else {}

        bids = book.get('bids', [])
        asks = book.get('asks', [])

        print(f"  {'BIDS (Buy)':>30}  |  {'ASKS (Sell)':<30}")
        print(f"  {'Price':>14} {'Size':>14}  |  {'Price':<14} {'Size':<14}")
        print(f"  {'-'*30}  |  {'-'*30}")

        max_rows = max(len(bids), len(asks))
        for i in range(min(max_rows, 10)):
            bid_str = ""
            ask_str = ""
            if i < len(bids):
                bid_str = f"  {float(bids[i]['price']):>14.4f} {float(bids[i]['size']):>14.2f}"
            else:
                bid_str = f"  {'':>14} {'':>14}"
            if i < len(asks):
                ask_str = f"  {float(asks[i]['price']):<14.4f} {float(asks[i]['size']):<14.2f}"
            else:
                ask_str = f"  {'':>14} {'':>14}"
            print(f"{bid_str}  |{ask_str}")

        if bids and asks:
            spread = float(asks[0]['price']) - float(bids[0]['price'])
            print(f"\n  Spread: {spread:.4f} ({spread*100:.2f}%)")
            print(f"  Best Bid: {float(bids[0]['price']):.4f}")
            print(f"  Best Ask: {float(asks[0]['price']):.4f}")

    except Exception as e:
        print(f"Error: {e}")


def show_orders(client, market_id):
    """Show open orders"""
    print(f"\n{'='*60}")
    print(f"  Open Orders - Market {market_id}")
    print(f"{'='*60}\n")

    try:
        result = client.get_my_orders(market_id=int(market_id), limit=50)
        orders = result.get('result', {}).get('data', []) if isinstance(result, dict) else []

        if not orders:
            print("  No open orders.")
            return

        for i, o in enumerate(orders, 1):
            side = o.get('side', '')
            price = o.get('price', '')
            size = o.get('size', o.get('amount', ''))
            status = o.get('status', '')
            order_id = o.get('orderId', '')
            print(f"  [{i}] {side.upper()} {size} @ {price}")
            print(f"      Status: {status}  ID: {order_id}")
            print()

        print(f"  Total: {len(orders)} orders")

    except Exception as e:
        print(f"Error: {e}")


def show_balances(client):
    """Show account balances"""
    print(f"\n{'='*60}")
    print(f"  Account Balances")
    print(f"{'='*60}\n")

    try:
        result = client.get_my_balances()
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        print(f"Error: {e}")


def show_positions(client):
    """Show positions"""
    print(f"\n{'='*60}")
    print(f"  Positions")
    print(f"{'='*60}\n")

    try:
        result = client.get_my_positions()
        positions = result.get('result', {}).get('data', []) if isinstance(result, dict) else []

        if not positions:
            print("  No open positions.")
            return

        for i, p in enumerate(positions, 1):
            print(f"  [{i}] Market: {p.get('marketTitle', p.get('marketId', ''))}")
            print(f"      Side: {p.get('side', '')}  Size: {p.get('size', '')}")
            print(f"      Entry: {p.get('avgPrice', '')}  PnL: {p.get('pnl', '')}")
            print()

    except Exception as e:
        print(f"Error: {e}")


# ============================================================
# Trading Operations (requires private key)
# ============================================================

def place_order(client, market_id, token_id, side, order_type, price, amount, amount_field='quote'):
    """Place an order"""
    try:
        from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
        from opinion_clob_sdk.chain.py_order_utils.model.sides import OrderSide
        from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER, MARKET_ORDER
    except ImportError:
        print("Error: opinion_clob_sdk not properly installed.")
        return

    order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL
    otype = MARKET_ORDER if order_type == 'market' else LIMIT_ORDER

    order_params = {
        'marketId': int(market_id),
        'tokenId': token_id,
        'side': order_side,
        'orderType': otype,
        'price': str(price) if otype == LIMIT_ORDER else '0',
    }

    # Amount: quote token (USDT) or base token (outcome tokens)
    if side.lower() == 'buy' or amount_field == 'quote':
        order_params['makerAmountInQuoteToken'] = int(amount)
    else:
        order_params['makerAmountInBaseToken'] = int(amount)

    order = PlaceOrderDataInput(**order_params)

    print(f"\n  Placing {'MARKET' if otype == MARKET_ORDER else 'LIMIT'} {side.upper()} order:")
    print(f"    Market ID: {market_id}")
    print(f"    Token: {token_id[:30]}...")
    print(f"    Price: {price}")
    print(f"    Amount: {amount} {'USDT' if amount_field == 'quote' else 'tokens'}")

    # Confirmation
    confirm = input("\n  Confirm? (y/N): ").strip().lower()
    if confirm != 'y':
        print("  Cancelled.")
        return

    try:
        result = client.place_order(order)
        print(f"\n  Order placed successfully!")
        print(f"  Result: {json.dumps(result, indent=2, default=str)}")
    except Exception as e:
        print(f"\n  Order failed: {e}")


def cancel_order(client, order_id):
    """Cancel an order"""
    print(f"\n  Cancelling order: {order_id}")

    try:
        result = client.cancel_order(orderId=order_id)
        print(f"  Cancelled: {json.dumps(result, indent=2, default=str)}")
    except Exception as e:
        print(f"  Error: {e}")


def cancel_all_orders(client, market_id):
    """Cancel all orders for a market"""
    print(f"\n  Cancelling all orders for market {market_id}...")

    confirm = input("  Confirm cancel ALL? (y/N): ").strip().lower()
    if confirm != 'y':
        print("  Cancelled.")
        return

    try:
        result = client.cancel_all_orders(market_id=int(market_id))
        print(f"  Result: {json.dumps(result, indent=2, default=str)}")
    except Exception as e:
        print(f"  Error: {e}")


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='Opinion.trade Order Script')
    parser.add_argument('--action', required=True,
                        choices=['markets', 'orderbook', 'buy', 'sell', 'orders',
                                 'cancel', 'cancel_all', 'balances', 'positions'],
                        help='Action to perform')
    parser.add_argument('--market', type=str, help='Market ID')
    parser.add_argument('--token', type=str, help='Token ID')
    parser.add_argument('--price', type=float, default=0, help='Order price (0.01-0.99)')
    parser.add_argument('--amount', type=float, default=0, help='Order amount')
    parser.add_argument('--order', type=str, help='Order ID (for cancel)')
    parser.add_argument('--type', choices=['limit', 'market'], default='limit', help='Order type')
    parser.add_argument('--limit', type=int, default=20, help='Number of results')

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    credentials = load_credentials()

    print()
    print("=" * 60)
    print("  Opinion.trade CLOB Order Tool")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  API Key: {'Configured' if credentials['api_key'] else 'NOT SET'}")
    print(f"  Private Key: {'Configured' if credentials['private_key'] else 'NOT SET (read-only)'}")
    print("=" * 60)

    # Read-only actions
    read_only_actions = {'markets', 'orderbook', 'orders', 'balances', 'positions'}
    is_read_only = args.action in read_only_actions

    client = create_client(credentials, read_only=is_read_only)

    if args.action == 'markets':
        list_markets(client, limit=args.limit)

    elif args.action == 'orderbook':
        if not args.token:
            print("Error: --token required")
            return
        show_orderbook(client, args.token)

    elif args.action in ('buy', 'sell'):
        if not args.market or not args.token:
            print("Error: --market and --token required")
            return
        if args.type == 'limit' and args.price <= 0:
            print("Error: --price required for limit orders")
            return
        if args.amount <= 0:
            print("Error: --amount required")
            return

        place_order(
            client,
            market_id=args.market,
            token_id=args.token,
            side=args.action,
            order_type=args.type,
            price=args.price,
            amount=args.amount,
            amount_field='quote' if args.action == 'buy' else 'base',
        )

    elif args.action == 'orders':
        if not args.market:
            print("Error: --market required")
            return
        show_orders(client, args.market)

    elif args.action == 'cancel':
        if not args.order:
            print("Error: --order required")
            return
        cancel_order(client, args.order)

    elif args.action == 'cancel_all':
        if not args.market:
            print("Error: --market required")
            return
        cancel_all_orders(client, args.market)

    elif args.action == 'balances':
        show_balances(client)

    elif args.action == 'positions':
        show_positions(client)


if __name__ == '__main__':
    main()
