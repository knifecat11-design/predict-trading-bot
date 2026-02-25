"""
Kalshi Prediction Market API Client

Public endpoints (no auth required) for market data.
API v2: https://api.elections.kalshi.com/trade-api/v2

Key features:
  - Prices included in /markets response (yes_ask_dollars, no_ask_dollars)
  - No separate orderbook calls needed
  - Cursor-based pagination, up to 1000 per page
  - Rate limit: 20 req/s (Basic tier)
"""

import time
import logging
import requests
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiClient:
    """Kalshi API client for public market data"""

    def __init__(self, config: Dict):
        self.config = config
        kalshi_cfg = config.get('kalshi', {})
        self.base_url = kalshi_cfg.get('base_url', DEFAULT_BASE_URL).rstrip('/')

        self.session = requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate',
            'User-Agent': 'PredictBot/1.0',
        })

        self._markets_cache: List[Dict] = []
        self._cache_time = 0
        self._cache_duration = kalshi_cfg.get('cache_seconds', 90)

    def get_markets(self, status: str = 'open', limit: int = 5000,
                    max_pages: int = 10) -> List[Dict]:
        """Fetch open markets with pagination.

        Args:
            status: Market status filter ('open', 'closed', 'settled')
            limit: Maximum markets to return
            max_pages: Maximum API pages to fetch (1000/page)

        Returns:
            List of market dicts in Kalshi's native format
        """
        if (time.time() - self._cache_time < self._cache_duration
                and self._markets_cache):
            return self._markets_cache[:limit]

        all_markets = []
        cursor = ""
        page_size = 1000  # max allowed by API

        for page in range(max_pages):
            try:
                params = {
                    'status': status,
                    'limit': page_size,
                    'mve_filter': 'exclude',  # skip multivariate combo markets
                }
                if cursor:
                    params['cursor'] = cursor

                resp = self.session.get(
                    f"{self.base_url}/markets",
                    params=params,
                    timeout=15,
                )

                if resp.status_code != 200:
                    logger.error(f"Kalshi API HTTP {resp.status_code}")
                    break

                data = resp.json()
                markets = data.get('markets', [])
                if not markets:
                    break

                all_markets.extend(markets)
                logger.debug(
                    f"Kalshi page {page + 1}: +{len(markets)} "
                    f"(total {len(all_markets)})"
                )

                cursor = data.get('cursor', '')
                if not cursor:
                    break

                if len(all_markets) >= limit:
                    break

                time.sleep(0.06)  # stay under 20 req/s

            except requests.Timeout:
                logger.warning(f"Kalshi page {page + 1} timeout")
                break
            except Exception as e:
                logger.error(f"Kalshi page {page + 1} error: {e}")
                break

        logger.info(f"Kalshi: fetched {len(all_markets)} markets in {page + 1} pages")
        self._markets_cache = all_markets
        self._cache_time = time.time()
        return all_markets[:limit]

    def get_orderbook(self, ticker: str, depth: int = 5) -> Optional[Dict]:
        """Get orderbook for a specific market (usually not needed since
        /markets already includes best bid/ask).

        Returns:
            {'yes_bid': float, 'yes_ask': float, 'no_bid': float, 'no_ask': float}
        """
        try:
            resp = self.session.get(
                f"{self.base_url}/markets/{ticker}/orderbook",
                params={'depth': depth},
                timeout=10,
            )
            if resp.status_code != 200:
                return None

            data = resp.json().get('orderbook', {})
            yes_bids = data.get('yes_dollars', [])
            no_bids = data.get('no_dollars', [])

            yes_bid = float(yes_bids[0][0]) if yes_bids else 0
            no_bid = float(no_bids[0][0]) if no_bids else 0

            return {
                'yes_bid': yes_bid,
                'yes_ask': 1 - no_bid if no_bid else 0,
                'no_bid': no_bid,
                'no_ask': 1 - yes_bid if yes_bid else 0,
            }
        except Exception as e:
            logger.error(f"Kalshi orderbook {ticker}: {e}")
            return None

    def clear_cache(self):
        self._markets_cache = []
        self._cache_time = 0
