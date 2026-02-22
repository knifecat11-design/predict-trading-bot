"""
Real-time WebSocket price feeds for Polymarket and Kalshi.

Architecture:
  - Each platform feed runs in its own daemon thread with an asyncio event loop
  - Subscribes to markets currently in active arbitrage
  - Price updates trigger a callback (used by dashboard to recalc arb in real-time)
  - Auto-reconnect with exponential backoff
  - Heartbeat/PING to keep connections alive

Polymarket: wss://ws-subscriptions-clob.polymarket.com/ws/market (public)
Kalshi:     wss://api.elections.kalshi.com/trade-api/ws/v2 (public ticker channel)
"""

import json
import time
import logging
import threading
import asyncio
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Max assets per WS connection (Polymarket stops sending book snapshots above ~500)
POLY_MAX_SUBS_PER_CONN = 200
KALSHI_MAX_SUBS_PER_CONN = 500
RECONNECT_DELAYS = [2, 4, 8, 16, 30, 60]  # exponential backoff caps at 60s


class PolymarketFeed:
    """Polymarket CLOB WebSocket feed for real-time best_bid/best_ask."""

    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    PING_INTERVAL = 10  # seconds

    def __init__(self, on_price_update: Callable[[str, str, float, float], None]):
        """
        Args:
            on_price_update: callback(platform, market_id, yes_ask, no_ask)
        """
        self._callback = on_price_update
        self._subscribed_ids: Set[str] = set()  # asset_ids currently subscribed
        self._pending_subscribe: Set[str] = set()
        self._pending_unsubscribe: Set[str] = set()
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_count = 0
        # Map: asset_id → {market_condition_id, side ('yes'/'no'), market_id (our internal)}
        self._asset_map: Dict[str, Dict] = {}
        # Current best prices per our market_id
        self._prices: Dict[str, Dict] = {}  # market_id → {yes_ask, no_ask}
        self._lock = threading.Lock()

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name='poly-ws')
        self._thread.start()
        logger.info("[PolyWS] Feed thread started")

    def stop(self):
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def update_subscriptions(self, asset_map: Dict[str, Dict]):
        """Update which assets to subscribe to.

        Args:
            asset_map: {asset_id: {market_id, side, condition_id}}
        """
        with self._lock:
            new_ids = set(asset_map.keys())
            self._pending_subscribe = new_ids - self._subscribed_ids
            self._pending_unsubscribe = self._subscribed_ids - new_ids
            self._asset_map = asset_map.copy()

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        max_retries = 10  # stop after 10 consecutive failures
        while self._running:
            try:
                self._loop.run_until_complete(self._connect())
            except Exception as e:
                logger.warning(f"[PolyWS] Loop error: {e}")
            if self._running:
                self._reconnect_count += 1
                if self._reconnect_count > max_retries:
                    logger.warning(f"[PolyWS] Max retries ({max_retries}) reached, stopping feed")
                    self._running = False
                    break
                delay = RECONNECT_DELAYS[min(self._reconnect_count - 1, len(RECONNECT_DELAYS) - 1)]
                logger.info(f"[PolyWS] Reconnecting in {delay}s (attempt #{self._reconnect_count})")
                time.sleep(delay)

    async def _connect(self):
        try:
            import websockets
        except ImportError:
            logger.error("[PolyWS] websockets package not installed")
            return

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Origin": "https://polymarket.com",
            }
            async with websockets.connect(
                self.WS_URL, ping_interval=None,
                additional_headers=headers, open_timeout=15,
            ) as ws:
                self._ws = ws
                self._reconnect_count = 0
                logger.info("[PolyWS] Connected")

                # Subscribe to any pending assets
                with self._lock:
                    initial_ids = list(self._asset_map.keys())[:POLY_MAX_SUBS_PER_CONN]
                    self._subscribed_ids = set(initial_ids)
                    self._pending_subscribe.clear()

                if initial_ids:
                    sub_msg = {
                        "assets_ids": initial_ids,
                        "type": "market",
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info(f"[PolyWS] Subscribed to {len(initial_ids)} assets")

                # Heartbeat + message loop
                ping_task = asyncio.create_task(self._heartbeat(ws))
                sub_task = asyncio.create_task(self._subscription_updater(ws))
                try:
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        if raw_msg == "PONG":
                            continue
                        try:
                            self._process_message(raw_msg)
                        except Exception as e:
                            logger.debug(f"[PolyWS] Message parse error: {e}")
                finally:
                    ping_task.cancel()
                    sub_task.cancel()

        except Exception as e:
            logger.warning(f"[PolyWS] Connection error: {e}")

    async def _heartbeat(self, ws):
        while self._running:
            try:
                await asyncio.sleep(self.PING_INTERVAL)
                await ws.send("PING")
            except Exception:
                break

    async def _subscription_updater(self, ws):
        """Periodically send dynamic subscribe/unsubscribe messages."""
        while self._running:
            await asyncio.sleep(5)
            try:
                with self._lock:
                    to_sub = list(self._pending_subscribe)[:50]
                    to_unsub = list(self._pending_unsubscribe)[:50]
                    if to_sub:
                        self._pending_subscribe -= set(to_sub)
                        self._subscribed_ids.update(to_sub)
                    if to_unsub:
                        self._pending_unsubscribe -= set(to_unsub)
                        self._subscribed_ids -= set(to_unsub)

                if to_sub:
                    await ws.send(json.dumps({
                        "assets_ids": to_sub,
                        "operation": "subscribe",
                        "custom_feature_enabled": True,
                    }))
                    logger.debug(f"[PolyWS] +{len(to_sub)} subscriptions")
            except Exception:
                break

    def _process_message(self, raw_msg: str):
        data = json.loads(raw_msg)
        events = data if isinstance(data, list) else [data]

        for event in events:
            etype = event.get("event_type", "")

            if etype == "book":
                asset_id = event.get("asset_id", "")
                bids = event.get("bids", [])
                asks = event.get("asks", [])
                best_bid = float(bids[0]["price"]) if bids else 0
                best_ask = float(asks[0]["price"]) if asks else 0
                self._handle_price(asset_id, best_bid, best_ask)

            elif etype == "price_change":
                for pc in event.get("price_changes", []):
                    asset_id = pc.get("asset_id", "")
                    best_bid = float(pc.get("best_bid", 0) or 0)
                    best_ask = float(pc.get("best_ask", 0) or 0)
                    self._handle_price(asset_id, best_bid, best_ask)

            elif etype == "best_bid_ask":
                asset_id = event.get("asset_id", "")
                best_bid = float(event.get("best_bid", 0) or 0)
                best_ask = float(event.get("best_ask", 0) or 0)
                self._handle_price(asset_id, best_bid, best_ask)

    def _handle_price(self, asset_id: str, best_bid: float, best_ask: float):
        with self._lock:
            info = self._asset_map.get(asset_id)
        if not info:
            return

        market_id = info.get("market_id", "")
        side = info.get("side", "yes")

        with self._lock:
            if market_id not in self._prices:
                self._prices[market_id] = {"yes_ask": 0, "no_ask": 0}
            if side == "yes":
                self._prices[market_id]["yes_ask"] = best_ask
                # No ask = 1 - yes_bid
                if best_bid > 0:
                    self._prices[market_id]["no_ask"] = round(1.0 - best_bid, 4)
            else:
                self._prices[market_id]["no_ask"] = best_ask
                if best_bid > 0:
                    self._prices[market_id]["yes_ask"] = round(1.0 - best_bid, 4)

            yes_ask = self._prices[market_id]["yes_ask"]
            no_ask = self._prices[market_id]["no_ask"]

        if yes_ask > 0 and no_ask > 0:
            try:
                self._callback("polymarket", market_id, yes_ask, no_ask)
            except Exception as e:
                logger.debug(f"[PolyWS] Callback error: {e}")


class KalshiFeed:
    """Kalshi WebSocket feed for real-time ticker updates."""

    WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    PING_INTERVAL = 15  # seconds

    def __init__(self, on_price_update: Callable[[str, str, float, float], None]):
        self._callback = on_price_update
        self._subscribed_tickers: Set[str] = set()
        self._pending_tickers: Set[str] = set()
        self._ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reconnect_count = 0
        self._msg_id = 0
        self._lock = threading.Lock()

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name='kalshi-ws')
        self._thread.start()
        logger.info("[KalshiWS] Feed thread started")

    def stop(self):
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def update_subscriptions(self, tickers: Set[str]):
        with self._lock:
            self._pending_tickers = tickers - self._subscribed_tickers

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        max_retries = 10
        while self._running:
            try:
                self._loop.run_until_complete(self._connect())
            except Exception as e:
                logger.warning(f"[KalshiWS] Loop error: {e}")
            if self._running:
                self._reconnect_count += 1
                if self._reconnect_count > max_retries:
                    logger.warning(f"[KalshiWS] Max retries ({max_retries}) reached, stopping feed")
                    self._running = False
                    break
                delay = RECONNECT_DELAYS[min(self._reconnect_count - 1, len(RECONNECT_DELAYS) - 1)]
                logger.info(f"[KalshiWS] Reconnecting in {delay}s (attempt #{self._reconnect_count})")
                time.sleep(delay)

    async def _connect(self):
        try:
            import websockets
        except ImportError:
            logger.error("[KalshiWS] websockets package not installed")
            return

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Origin": "https://kalshi.com",
            }
            async with websockets.connect(
                self.WS_URL, ping_interval=None,
                additional_headers=headers, open_timeout=15,
            ) as ws:
                self._ws = ws
                self._reconnect_count = 0
                logger.info("[KalshiWS] Connected")

                # Subscribe to pending tickers
                with self._lock:
                    initial_tickers = list(self._pending_tickers)[:KALSHI_MAX_SUBS_PER_CONN]
                    self._subscribed_tickers = set(initial_tickers)
                    self._pending_tickers.clear()

                if initial_tickers:
                    sub_msg = {
                        "id": self._next_id(),
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["ticker"],
                            "market_tickers": initial_tickers,
                        }
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info(f"[KalshiWS] Subscribed to {len(initial_tickers)} tickers")

                ping_task = asyncio.create_task(self._heartbeat(ws))
                sub_task = asyncio.create_task(self._subscription_updater(ws))
                try:
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            self._process_message(raw_msg)
                        except Exception as e:
                            logger.debug(f"[KalshiWS] Message parse error: {e}")
                finally:
                    ping_task.cancel()
                    sub_task.cancel()

        except Exception as e:
            logger.warning(f"[KalshiWS] Connection error: {e}")

    async def _heartbeat(self, ws):
        while self._running:
            try:
                await asyncio.sleep(self.PING_INTERVAL)
                await ws.ping()
            except Exception:
                break

    async def _subscription_updater(self, ws):
        while self._running:
            await asyncio.sleep(5)
            try:
                with self._lock:
                    to_sub = list(self._pending_tickers)[:50]
                    if to_sub:
                        self._pending_tickers -= set(to_sub)
                        self._subscribed_tickers.update(to_sub)

                if to_sub:
                    await ws.send(json.dumps({
                        "id": self._next_id(),
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["ticker"],
                            "market_tickers": to_sub,
                        }
                    }))
                    logger.debug(f"[KalshiWS] +{len(to_sub)} ticker subscriptions")
            except Exception:
                break

    def _process_message(self, raw_msg: str):
        data = json.loads(raw_msg)
        msg_type = data.get("type", "")

        if msg_type == "ticker":
            msg = data.get("msg", {})
            ticker = msg.get("market_ticker", "")
            if not ticker:
                return

            yes_ask = float(msg.get("yes_ask_dollars", 0) or
                            msg.get("yes_ask", 0) or 0)
            no_ask = float(msg.get("no_ask_dollars", 0) or
                           msg.get("no_ask", 0) or 0)

            # Kalshi: derive from bid if ask not available
            if yes_ask <= 0:
                no_bid = float(msg.get("no_bid_dollars", 0) or
                               msg.get("no_bid", 0) or 0)
                if no_bid > 0:
                    yes_ask = round(1.0 - no_bid, 4)
            if no_ask <= 0:
                yes_bid = float(msg.get("yes_bid_dollars", 0) or
                                msg.get("yes_bid", 0) or 0)
                if yes_bid > 0:
                    no_ask = round(1.0 - yes_bid, 4)

            if yes_ask > 0 and no_ask > 0:
                try:
                    self._callback("kalshi", ticker, yes_ask, no_ask)
                except Exception as e:
                    logger.debug(f"[KalshiWS] Callback error: {e}")


class RealtimePriceFeed:
    """Unified manager for all platform WebSocket feeds.

    Usage:
        feed = RealtimePriceFeed(on_price_update=my_callback)
        feed.start()
        # After each scan, update which markets to watch:
        feed.update_arb_markets(arb_opportunities, all_platform_markets)
    """

    def __init__(self, on_price_update: Callable[[str, str, float, float], None]):
        """
        Args:
            on_price_update: callback(platform, market_id, yes_ask, no_ask)
                Called from WS threads whenever a subscribed market's price changes.
        """
        self._callback = on_price_update
        self._poly_feed = PolymarketFeed(on_price_update)
        self._kalshi_feed = KalshiFeed(on_price_update)
        self._started = False
        self._stats = {
            "poly_subscribed": 0,
            "kalshi_subscribed": 0,
            "last_update": None,
        }

    def start(self):
        if self._started:
            return
        self._started = True
        self._poly_feed.start()
        self._kalshi_feed.start()
        logger.info("[RealtimeFeed] Started Polymarket + Kalshi WebSocket feeds")

    def stop(self):
        self._started = False
        self._poly_feed.stop()
        self._kalshi_feed.stop()
        logger.info("[RealtimeFeed] Stopped all feeds")

    def update_arb_markets(self, arb_list: List[Dict], platform_markets: Dict[str, List[Dict]]):
        """Update WebSocket subscriptions based on current arbitrage opportunities.

        Subscribes to markets that are currently in active arbitrage,
        plus top markets by volume for early detection.

        Args:
            arb_list: Current arbitrage opportunities from scanner
            platform_markets: {'polymarket': [...], 'kalshi': [...], ...}
        """
        # Collect Polymarket asset IDs from arb opportunities
        poly_asset_map = {}
        kalshi_tickers = set()

        # From active arb opportunities — subscribe for real-time tracking
        for opp in arb_list:
            direction = opp.get("direction", "")
            # Extract market IDs from the arb opportunity
            market_key = opp.get("market_key", "")

            if "Polymarket" in direction or "Polymarket" in str(opp.get("platform_a", "")):
                # We need the asset_id (token_id) for Polymarket WS
                # These come from the market data, stored as 'token_ids' or 'clobTokenIds'
                pass  # Handled via platform_markets below

            if "Kalshi" in direction or "Kalshi" in str(opp.get("platform_a", "")):
                # For Kalshi, the market_id IS the ticker
                parts = market_key.split("-")
                for part_group in [parts]:
                    for p in part_group:
                        if p.startswith("KX") or p.startswith("kx"):
                            kalshi_tickers.add(p)

        # From platform markets — subscribe top markets by volume for early arb detection
        poly_markets = platform_markets.get("polymarket", [])
        for m in poly_markets[:100]:  # Top 100 by volume
            token_ids = m.get("clobTokenIds", []) or m.get("token_ids", [])
            market_id = m.get("id", "")
            if isinstance(token_ids, list) and len(token_ids) >= 1:
                for i, tid in enumerate(token_ids[:2]):
                    poly_asset_map[tid] = {
                        "market_id": market_id,
                        "side": "yes" if i == 0 else "no",
                        "condition_id": m.get("condition_id", ""),
                    }

        kalshi_markets = platform_markets.get("kalshi", [])
        for m in kalshi_markets[:200]:  # Top 200 by volume
            ticker = m.get("id", "")
            if ticker:
                kalshi_tickers.add(ticker)

        # Update feeds
        if poly_asset_map:
            self._poly_feed.update_subscriptions(poly_asset_map)
        if kalshi_tickers:
            self._kalshi_feed.update_subscriptions(kalshi_tickers)

        self._stats["poly_subscribed"] = len(poly_asset_map)
        self._stats["kalshi_subscribed"] = len(kalshi_tickers)
        self._stats["last_update"] = time.time()

        logger.debug(
            f"[RealtimeFeed] Subscriptions: Poly={len(poly_asset_map)} "
            f"Kalshi={len(kalshi_tickers)}"
        )

    @property
    def stats(self) -> Dict:
        return {
            **self._stats,
            "poly_connected": self._poly_feed._ws is not None,
            "kalshi_connected": self._kalshi_feed._ws is not None,
        }
