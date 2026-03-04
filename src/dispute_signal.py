"""
争议信号检测引擎

信号类型（按严重度）:
1. NEW_DISPUTE (HIGH): 新提案被争议 — 有人支付保证金挑战
2. SETTLEMENT_REVERSAL (HIGH): DVM 投票推翻原提案 — 重大反转
3. ORACLE_MARKET_DIVERGENCE (MEDIUM): Oracle 结果与市场价格严重偏差
4. PROPOSAL_CONTRADICTION (LOW): 挑战期内提案与市场价格矛盾
"""

import time
import logging
from enum import Enum
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from datetime import datetime

from src.uma_oracle_api import UMAOracleClient, OracleRequest

logger = logging.getLogger(__name__)


class SignalType(Enum):
    NEW_DISPUTE = "new_dispute"
    SETTLEMENT_REVERSAL = "settlement_reversal"
    ORACLE_MARKET_DIVERGENCE = "oracle_market_divergence"
    PROPOSAL_CONTRADICTION = "proposal_contradiction"


class Severity(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class DisputeSignal:
    """争议信号"""
    signal_type: SignalType
    severity: Severity
    market_id: Optional[str]      # Polymarket market_id
    title: str                     # 市场标题
    oracle_outcome: str            # Oracle 提议/结算结果 (Yes/No)
    market_price_yes: Optional[float]  # 当前市场 Yes 价格 (0-1)
    divergence_pct: Optional[float]    # Oracle vs 市场偏差百分比
    bond_usdc: float               # 保证金金额
    details: str                   # 详细描述
    request_id: str                # Oracle request ID
    timestamp: int                 # 事件时间戳
    source: str                    # 来源子图 (OOv2/MOOV2)
    request_hash: Optional[str] = None      # 链上 requestHash (for UMA URL)
    request_log_index: Optional[int] = None # 链上 eventLogIndex

    @property
    def signal_key(self) -> str:
        """用于去重和冷却的唯一 key"""
        return f"{self.signal_type.value}-{self.request_id}"


class DisputeSignalDetector:
    """
    争议信号检测器

    检测流程:
    1. 从 UMA Oracle 获取活跃争议、提案、最近结算
    2. 与 Polymarket 当前市场价格对比
    3. 生成争议信号并按严重度排序
    """

    def __init__(self, uma_client: UMAOracleClient, config: Dict = None):
        self.uma_client = uma_client
        self.config = config or {}

        dispute_config = self.config.get('dispute', {})
        self.divergence_threshold = float(dispute_config.get('divergence_threshold', 20.0))

        # 状态跟踪
        self._notified_signals: Set[str] = set()  # 已通知的信号 key
        self._known_dispute_ids: Set[str] = set()  # 已知的争议 request_id
        self._known_settlement_ids: Set[str] = set()  # 已知的结算 request_id

    def detect_signals(self, poly_markets: List[Dict] = None) -> List[DisputeSignal]:
        """
        主检测方法：扫描所有信号类型

        Args:
            poly_markets: Polymarket 市场列表（用于价格对比）
                         格式: [{'id': str, 'title': str, 'yes': float, 'no': float}, ...]
        """
        signals: List[DisputeSignal] = []
        poly_markets = poly_markets or []

        # 构建 Polymarket 价格查询索引 (market_id -> market_data)
        poly_price_index = self._build_price_index(poly_markets)

        try:
            oracle_data = self.uma_client.query_all_active()
        except Exception as e:
            logger.error(f"Failed to query UMA Oracle: {e}")
            return signals

        # 1. 新争议检测
        new_disputes = self._check_new_disputes(oracle_data.get("disputes", []))
        signals.extend(new_disputes)

        # 2. 结算反转检测
        reversals = self._check_settlement_reversals(oracle_data.get("settlements", []))
        signals.extend(reversals)

        # 3. Oracle vs 市场价格偏差检测
        if poly_price_index:
            all_oracle_requests = (
                oracle_data.get("disputes", [])
                + oracle_data.get("proposals", [])
                + oracle_data.get("settlements", [])
            )
            divergences = self._check_oracle_market_divergence(all_oracle_requests, poly_price_index)
            signals.extend(divergences)

            # 4. 挑战期内提案矛盾检测
            contradictions = self._check_pending_proposals(
                oracle_data.get("proposals", []), poly_price_index
            )
            signals.extend(contradictions)

        # 按严重度排序: HIGH > MEDIUM > LOW
        severity_order = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}
        signals.sort(key=lambda s: severity_order.get(s.severity, 99))

        if signals:
            logger.info(
                f"Dispute signals detected: {len(signals)} "
                f"(HIGH: {sum(1 for s in signals if s.severity == Severity.HIGH)}, "
                f"MEDIUM: {sum(1 for s in signals if s.severity == Severity.MEDIUM)}, "
                f"LOW: {sum(1 for s in signals if s.severity == Severity.LOW)})"
            )

        return signals

    def _build_price_index(self, poly_markets: List[Dict]) -> Dict[str, Dict]:
        """构建 Polymarket 价格查询索引

        通过 condition_id 和 question_id（转为字符串）作为备选 key
        """
        index = {}
        for m in poly_markets:
            mid = str(m.get('id', ''))
            if mid:
                index[mid] = m
        return index

    def _find_market_price(self, oracle_req: OracleRequest, poly_index: Dict[str, Dict]) -> Optional[Dict]:
        """通过 market_id 查找 Polymarket 市场价格"""
        if oracle_req.market_id and oracle_req.market_id in poly_index:
            return poly_index[oracle_req.market_id]
        return None

    # ============================================================
    # 信号检测
    # ============================================================

    def _check_new_disputes(self, disputes: List[OracleRequest]) -> List[DisputeSignal]:
        """检测活跃争议 — 每次扫描都返回所有活跃争议"""
        signals = []
        for req in disputes:
            signals.append(DisputeSignal(
                signal_type=SignalType.NEW_DISPUTE,
                severity=Severity.HIGH,
                market_id=req.market_id,
                title=req.title,
                oracle_outcome=req.proposed_outcome,
                market_price_yes=None,
                divergence_pct=None,
                bond_usdc=req.bond_usdc,
                details=(
                    f"提案 '{req.proposed_outcome}' 被争议。"
                    f"争议人: {req.disputer[:10]}... "
                    f"保证金: ${req.bond_usdc:.0f} USDC"
                ),
                request_id=req.request_id,
                timestamp=req.dispute_timestamp or int(time.time()),
                source=req.source_endpoint,
                request_hash=req.request_hash,
                request_log_index=req.request_log_index,
            ))

        return signals

    def _check_settlement_reversals(self, settlements: List[OracleRequest]) -> List[DisputeSignal]:
        """检测结算反转：DVM 投票结果 ≠ 原提案"""
        signals = []
        for req in settlements:
            # 需要有原始提案和结算价格，且两者不同
            if (req.proposed_price_raw and req.settlement_price_raw
                    and req.proposed_price_raw != req.settlement_price_raw
                    and req.disputer is not None):

                signals.append(DisputeSignal(
                    signal_type=SignalType.SETTLEMENT_REVERSAL,
                    severity=Severity.HIGH,
                    market_id=req.market_id,
                    title=req.title,
                    oracle_outcome=req.settlement_outcome or "Unknown",
                    market_price_yes=None,
                    divergence_pct=None,
                    bond_usdc=req.bond_usdc,
                    details=(
                        f"结算反转! 原提案: {req.proposed_outcome} → "
                        f"DVM 结算: {req.settlement_outcome}。"
                        f"争议人胜出。"
                    ),
                    request_id=req.request_id,
                    timestamp=req.settlement_timestamp or int(time.time()),
                    source=req.source_endpoint,
                ))

        return signals

    def _check_oracle_market_divergence(
        self, oracle_requests: List[OracleRequest], poly_index: Dict[str, Dict]
    ) -> List[DisputeSignal]:
        """检测 Oracle 结果与市场价格严重偏差"""
        signals = []
        seen = set()

        for req in oracle_requests:
            if req.request_id in seen:
                continue
            seen.add(req.request_id)

            market = self._find_market_price(req, poly_index)
            if not market:
                continue

            market_yes = market.get('yes', 0)
            if not market_yes or market_yes <= 0:
                continue

            # 将 Oracle 结果转为价格
            oracle_yes = self._outcome_to_price(req)
            if oracle_yes is None:
                continue

            divergence = abs(oracle_yes - market_yes) * 100
            if divergence < self.divergence_threshold:
                continue

            # 确定信号来源描述
            if req.state == "Disputed":
                state_desc = "争议中"
            elif req.state in ("Resolved", "Settled"):
                state_desc = "已结算"
                oracle_yes = self._outcome_to_price_settlement(req) or oracle_yes
            else:
                state_desc = "提案中"

            signals.append(DisputeSignal(
                signal_type=SignalType.ORACLE_MARKET_DIVERGENCE,
                severity=Severity.MEDIUM,
                market_id=req.market_id,
                title=req.title,
                oracle_outcome=f"{req.proposed_outcome} ({state_desc})",
                market_price_yes=market_yes,
                divergence_pct=divergence,
                bond_usdc=req.bond_usdc,
                details=(
                    f"Oracle {state_desc}: {req.proposed_outcome} "
                    f"(≈{oracle_yes*100:.0f}%) vs 市场 Yes={market_yes*100:.1f}%。"
                    f"偏差: {divergence:.1f}%"
                ),
                request_id=req.request_id,
                timestamp=req.proposal_timestamp or req.request_timestamp,
                source=req.source_endpoint,
                request_hash=req.request_hash,
                request_log_index=req.request_log_index,
            ))

        return signals

    def _check_pending_proposals(
        self, proposals: List[OracleRequest], poly_index: Dict[str, Dict]
    ) -> List[DisputeSignal]:
        """检测挑战期内提案与市场价格矛盾"""
        signals = []
        now = int(time.time())

        for req in proposals:
            # 只检查仍在挑战期内的
            if req.expiration_timestamp and req.expiration_timestamp < now:
                continue

            market = self._find_market_price(req, poly_index)
            if not market:
                continue

            market_yes = market.get('yes', 0)
            if not market_yes or market_yes <= 0:
                continue

            oracle_yes = self._outcome_to_price(req)
            if oracle_yes is None:
                continue

            divergence = abs(oracle_yes - market_yes) * 100
            if divergence < self.divergence_threshold:
                continue

            remaining_secs = req.expiration_timestamp - now if req.expiration_timestamp else 0
            remaining_mins = max(0, remaining_secs // 60)

            signals.append(DisputeSignal(
                signal_type=SignalType.PROPOSAL_CONTRADICTION,
                severity=Severity.LOW,
                market_id=req.market_id,
                title=req.title,
                oracle_outcome=req.proposed_outcome,
                market_price_yes=market_yes,
                divergence_pct=divergence,
                bond_usdc=req.bond_usdc,
                details=(
                    f"提案 '{req.proposed_outcome}' 与市场价格矛盾。"
                    f"市场 Yes={market_yes*100:.1f}%，"
                    f"挑战期剩余 {remaining_mins} 分钟"
                ),
                request_id=req.request_id,
                timestamp=req.proposal_timestamp or req.request_timestamp,
                source=req.source_endpoint,
                request_hash=req.request_hash,
                request_log_index=req.request_log_index,
            ))

        return signals

    # ============================================================
    # 辅助方法
    # ============================================================

    def _outcome_to_price(self, req: OracleRequest) -> Optional[float]:
        """将 Oracle 提案结果转为 Yes 价格 (0-1)"""
        if req.proposed_outcome == "Yes":
            return 1.0
        elif req.proposed_outcome == "No":
            return 0.0
        elif req.proposed_outcome == "Unknown/50-50":
            return 0.5
        return None

    def _outcome_to_price_settlement(self, req: OracleRequest) -> Optional[float]:
        """将 Oracle 结算结果转为 Yes 价格 (0-1)"""
        if req.settlement_outcome == "Yes":
            return 1.0
        elif req.settlement_outcome == "No":
            return 0.0
        elif req.settlement_outcome == "Unknown/50-50":
            return 0.5
        return None

    def is_already_notified(self, signal: DisputeSignal) -> bool:
        """检查信号是否已通知"""
        return signal.signal_key in self._notified_signals

    def mark_notified(self, signal: DisputeSignal):
        """标记信号为已通知"""
        self._notified_signals.add(signal.signal_key)


def format_dispute_signal_message(signal: DisputeSignal, scan_count: int = 0) -> str:
    """格式化争议信号为 Telegram HTML 消息"""

    # 信号类型图标和标题
    type_info = {
        SignalType.NEW_DISPUTE: ("⚠️", "新争议"),
        SignalType.SETTLEMENT_REVERSAL: ("🔄", "结算反转"),
        SignalType.ORACLE_MARKET_DIVERGENCE: ("📊", "Oracle vs 市场偏差"),
        SignalType.PROPOSAL_CONTRADICTION: ("🔍", "提案矛盾"),
    }
    icon, type_name = type_info.get(signal.signal_type, ("❓", "未知"))

    severity_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(signal.severity.value, "⚪")

    lines = [
        f"<b>{icon} {type_name} #{scan_count}</b>",
        f"<b>严重度:</b> {severity_icon} {signal.severity.value}",
        f"",
        f"<b>市场:</b> {signal.title[:80]}",
    ]

    if signal.market_id:
        lines.append(f"<b>Market ID:</b> {signal.market_id}")

    lines.append(f"<b>Oracle 结果:</b> {signal.oracle_outcome}")

    if signal.market_price_yes is not None:
        lines.append(f"<b>市场价格:</b> Yes={signal.market_price_yes*100:.1f}%")

    if signal.divergence_pct is not None:
        lines.append(f"<b>偏差:</b> {signal.divergence_pct:.1f}%")

    if signal.bond_usdc > 0:
        lines.append(f"<b>保证金:</b> ${signal.bond_usdc:,.0f} USDC")

    lines.extend([
        f"",
        f"<b>详情:</b> {signal.details}",
        f"",
        f"<b>来源:</b> UMA Oracle ({signal.source})",
        f"<b>时间:</b> {datetime.now().strftime('%H:%M:%S')}",
    ])

    return "\n".join(lines)
