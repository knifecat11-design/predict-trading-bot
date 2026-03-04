"""
UMA Optimistic Oracle API 客户端
通过 Goldsky GraphQL 子图查询 Polymarket 争议事件

数据来源:
- OOv2 (Polygon): polygon-optimistic-oracle-v2
- Managed OOv2 (Polygon): polygon-managed-optimistic-oracle-v2

子图 Schema: OptimisticPriceRequest
- proposedPrice: 0 = No, 1e18 = Yes, 5e17 = Unknown
- state: Requested, Proposed, Disputed, Resolved, Settled
- ancillaryData: hex 编码的 UTF-8 文本，包含 market_id, 标题, res_data
"""

import re
import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

# Goldsky 子图端点
OOV2_ENDPOINT = (
    "https://api.goldsky.com/api/public/"
    "project_clus2fndawbcc01w31192938i/subgraphs/"
    "polygon-optimistic-oracle-v2/1.1.0/gn"
)
MOOV2_ENDPOINT = (
    "https://api.goldsky.com/api/public/"
    "project_clus2fndawbcc01w31192938i/subgraphs/"
    "polygon-managed-optimistic-oracle-v2/1.0.5/gn"
)

# Polymarket UmaCtfAdapter 合约地址（Polygon）
POLYMARKET_REQUESTERS = {
    "0x6a9d222616c90fca5754cd1333cfd9b7fb6a4f74",  # v2.0
    "0x2f5e3684cb1f318ec51b00edba38d79ac2c0aa9d",  # v3.0 (managed)
    "0xcb1822859cef82cd2eb4e6276c7916e692995130",  # v1.0
    "0x157ce2d672854c848c9b79c49a8cc6cc89176a49",  # v3.0 alt
}

# proposedPrice 解码
PRICE_YES = "1000000000000000000"  # 1e18
PRICE_NO = "0"
PRICE_UNKNOWN = "500000000000000000"  # 5e17


@dataclass
class OracleRequest:
    """UMA Oracle 价格请求"""
    request_id: str
    market_id: Optional[str]  # Polymarket market_id（从 ancillaryData 提取）
    title: str                # 市场标题（从 ancillaryData 提取）
    proposed_outcome: str     # "Yes", "No", "Unknown"
    proposed_price_raw: str   # 原始 proposedPrice
    state: str                # Requested, Proposed, Disputed, Resolved, Settled
    settlement_outcome: Optional[str] = None  # 结算结果
    settlement_price_raw: Optional[str] = None
    requester: str = ""
    proposer: Optional[str] = None
    disputer: Optional[str] = None
    bond_usdc: float = 0.0   # 保证金（USDC）
    request_timestamp: int = 0
    proposal_timestamp: Optional[int] = None
    dispute_timestamp: Optional[int] = None
    settlement_timestamp: Optional[int] = None
    expiration_timestamp: Optional[int] = None
    source_endpoint: str = ""  # 来源子图


class UMAOracleClient:
    """
    UMA Optimistic Oracle GraphQL 客户端

    功能：
    - 查询争议中的价格请求
    - 查询最近的提案（含挑战期内的）
    - 查询已结算的请求
    - 解码 ancillaryData 提取 Polymarket market_id 和标题
    """

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'PredictTradingBot/1.0',
        })

        # 缓存
        self._cache: Dict[str, dict] = {}  # endpoint -> {data, timestamp}
        self._cache_ttl = int(self.config.get('dispute', {}).get('cache_seconds', 60))

    def _graphql_query(self, endpoint: str, query: str, timeout: int = 15) -> Optional[dict]:
        """执行 GraphQL 查询"""
        try:
            resp = self.session.post(
                endpoint,
                json={"query": query},
                timeout=timeout,
            )
            if resp.status_code != 200:
                logger.warning(f"GraphQL error: HTTP {resp.status_code} from {endpoint}")
                return None
            data = resp.json()
            if "errors" in data:
                logger.warning(f"GraphQL errors: {data['errors']}")
                return None
            return data.get("data")
        except requests.RequestException as e:
            logger.error(f"GraphQL request failed: {e}")
            return None

    def _get_cached_or_query(self, cache_key: str, endpoint: str, query: str) -> Optional[dict]:
        """带缓存的 GraphQL 查询"""
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached['timestamp']) < self._cache_ttl:
            return cached['data']

        data = self._graphql_query(endpoint, query)
        if data is not None:
            self._cache[cache_key] = {'data': data, 'timestamp': now}
        return data

    @staticmethod
    def decode_ancillary_data(hex_str: str) -> str:
        """解码 hex ancillaryData 为 UTF-8 文本"""
        try:
            if hex_str.startswith("0x"):
                hex_str = hex_str[2:]
            return bytes.fromhex(hex_str).decode("utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def extract_market_info(ancillary_text: str) -> Dict[str, Optional[str]]:
        """从 ancillaryData 文本提取 market_id 和标题

        ancillaryData 格式:
        q: title: Will X happen?, description: ..., market_id: 1323424 res_data: p1: 0, p2: 1, p3: 0.5. ...
        """
        info: Dict[str, Optional[str]] = {
            "title": None,
            "market_id": None,
            "description": None,
        }

        # 提取标题: "title: ... ," 或 "title: ... \n"
        title_match = re.search(r"title:\s*(.+?)(?:,\s*description:|$)", ancillary_text)
        if title_match:
            info["title"] = title_match.group(1).strip()

        # 提取 market_id
        mid_match = re.search(r"market_id:\s*(\d+)", ancillary_text)
        if mid_match:
            info["market_id"] = mid_match.group(1)

        # 提取描述（截断）
        desc_match = re.search(r"description:\s*(.+?)(?:\s*market_id:|\s*res_data:|$)", ancillary_text, re.DOTALL)
        if desc_match:
            info["description"] = desc_match.group(1).strip()[:200]

        return info

    @staticmethod
    def parse_proposed_price(price_wei: Optional[str]) -> str:
        """将 wei 价格转换为可读结果"""
        if price_wei is None:
            return "Unknown"
        if price_wei == PRICE_YES:
            return "Yes"
        if price_wei == PRICE_NO:
            return "No"
        if price_wei == PRICE_UNKNOWN:
            return "Unknown/50-50"
        # 其它值
        try:
            val = int(price_wei) / 1e18
            return f"Custom({val:.4f})"
        except (ValueError, TypeError):
            return "Unknown"

    def _parse_request(self, raw: dict, source: str) -> Optional[OracleRequest]:
        """解析子图返回的原始请求数据"""
        try:
            # 解码 ancillaryData
            ancillary_text = self.decode_ancillary_data(raw.get("ancillaryData", ""))
            market_info = self.extract_market_info(ancillary_text)

            title = market_info.get("title") or "Unknown Market"
            market_id = market_info.get("market_id")

            proposed_price = raw.get("proposedPrice")
            settlement_price = raw.get("settlementPrice")

            # bond: USDC 有 6 位小数
            bond_raw = int(raw.get("bond") or 0)
            bond_usdc = bond_raw / 1e6

            return OracleRequest(
                request_id=raw.get("id", ""),
                market_id=market_id,
                title=title,
                proposed_outcome=self.parse_proposed_price(proposed_price),
                proposed_price_raw=proposed_price or "",
                state=raw.get("state", "Unknown"),
                settlement_outcome=self.parse_proposed_price(settlement_price) if settlement_price else None,
                settlement_price_raw=settlement_price,
                requester=raw.get("requester", "").lower(),
                proposer=raw.get("proposer"),
                disputer=raw.get("disputer"),
                bond_usdc=bond_usdc,
                request_timestamp=int(raw.get("requestTimestamp") or 0),
                proposal_timestamp=int(raw.get("proposalTimestamp") or 0) if raw.get("proposalTimestamp") else None,
                dispute_timestamp=int(raw.get("disputeTimestamp") or 0) if raw.get("disputeTimestamp") else None,
                settlement_timestamp=int(raw.get("settlementTimestamp") or 0) if raw.get("settlementTimestamp") else None,
                expiration_timestamp=int(raw.get("proposalExpirationTimestamp") or 0) if raw.get("proposalExpirationTimestamp") else None,
                source_endpoint=source,
            )
        except Exception as e:
            logger.warning(f"Failed to parse oracle request: {e}")
            return None

    def _filter_polymarket_requests(self, requests_list: List[OracleRequest]) -> List[OracleRequest]:
        """过滤只保留 Polymarket 相关的请求"""
        return [
            r for r in requests_list
            if r.requester in POLYMARKET_REQUESTERS or r.market_id is not None
        ]

    def _query_both_endpoints(self, query_template: str, cache_prefix: str) -> List[OracleRequest]:
        """同时查询 OOv2 和 MOOV2 端点，合并去重"""
        results = []
        seen_ids = set()

        for label, endpoint in [("OOv2", OOV2_ENDPOINT), ("MOOV2", MOOV2_ENDPOINT)]:
            cache_key = f"{cache_prefix}_{label}"
            data = self._get_cached_or_query(cache_key, endpoint, query_template)
            if not data:
                continue

            raw_list = data.get("optimisticPriceRequests", [])
            for raw in raw_list:
                req = self._parse_request(raw, label)
                if req and req.request_id not in seen_ids:
                    seen_ids.add(req.request_id)
                    results.append(req)

        return self._filter_polymarket_requests(results)

    # ============================================================
    # 公开查询方法
    # ============================================================

    def query_active_disputes(self, first: int = 50) -> List[OracleRequest]:
        """查询活跃的争议请求（state=Disputed）"""
        query = f"""{{
  optimisticPriceRequests(
    first: {first},
    orderBy: disputeTimestamp,
    orderDirection: desc,
    where: {{ disputer_not: null, state: Disputed }}
  ) {{
    id identifier ancillaryData time requester
    proposer proposedPrice proposalExpirationTimestamp
    disputer disputeTimestamp state
    settlementPrice settlementTimestamp
    bond requestTimestamp proposalTimestamp
  }}
}}"""
        results = self._query_both_endpoints(query, "disputes")
        logger.info(f"UMA Oracle: {len(results)} active disputes found")
        return results

    def query_recent_proposals(self, first: int = 50) -> List[OracleRequest]:
        """查询最近的提案（包含挑战期内的）"""
        query = f"""{{
  optimisticPriceRequests(
    first: {first},
    orderBy: proposalTimestamp,
    orderDirection: desc,
    where: {{ state: Proposed }}
  ) {{
    id identifier ancillaryData time requester
    proposer proposedPrice proposalExpirationTimestamp
    disputer disputeTimestamp state
    settlementPrice settlementTimestamp
    bond requestTimestamp proposalTimestamp
  }}
}}"""
        results = self._query_both_endpoints(query, "proposals")
        logger.info(f"UMA Oracle: {len(results)} active proposals found")
        return results

    def query_recent_settlements(self, first: int = 30) -> List[OracleRequest]:
        """查询最近结算的请求"""
        query = f"""{{
  optimisticPriceRequests(
    first: {first},
    orderBy: settlementTimestamp,
    orderDirection: desc,
    where: {{ state_in: [Resolved, Settled] }}
  ) {{
    id identifier ancillaryData time requester
    proposer proposedPrice proposalExpirationTimestamp
    disputer disputeTimestamp state
    settlementPrice settlementTimestamp
    bond requestTimestamp proposalTimestamp
  }}
}}"""
        results = self._query_both_endpoints(query, "settlements")
        logger.info(f"UMA Oracle: {len(results)} recent settlements found")
        return results

    def query_all_active(self) -> Dict[str, List[OracleRequest]]:
        """一次性查询所有活跃状态的请求（争议 + 提案 + 最近结算）"""
        return {
            "disputes": self.query_active_disputes(),
            "proposals": self.query_recent_proposals(),
            "settlements": self.query_recent_settlements(),
        }
