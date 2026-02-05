"""
智能市场匹配模块
通过关键词和相似度算法自动匹配不同平台上的同一市场
"""

import re
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from difflib import SequenceMatcher
from collections import defaultdict
import time

logger = logging.getLogger(__name__)


@dataclass
class MarketMatch:
    """市场匹配结果"""
    polymarket_id: str
    polymarket_title: str
    predict_id: Optional[str] = None
    predict_title: Optional[str] = None
    probable_id: Optional[str] = None
    probable_title: Optional[str] = None
    confidence: float = 0.0  # 匹配置信度 0-1


class KeywordExtractor:
    """关键词提取器"""

    # 常见停用词
    STOP_WORDS = {
        'will', 'won', 'the', 'a', 'an', 'is', 'are', 'was', 'were',
        'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does',
        'did', 'can', 'could', 'should', 'would', 'may', 'might',
        'must', 'shall', 'to', 'for', 'of', 'in', 'on', 'at', 'by',
        'with', 'from', 'as', 'this', 'that', 'these', 'those',
        'by', 'end', 'before', 'after', 'during', 'occur', 'happen'
    }

    # 常见实体模式
    PATTERNS = {
        'year': r'\b(20[12][0-9]|20[3-9][0-9])\b',
        'price': r'\$[\d,]+(?:\.\d+)?|\d+\s*(?:dollars?|USD|million|billion)',
        'percent': r'\d+(?:\.\d+)?%',
        'trump': r'\bTrump\b',
        'biden': r'\bBiden\b',
        'crypto': r'\b(?:Bitcoin|BTC|Ethereum|ETH|crypto)\b',
    }

    @classmethod
    def extract_keywords(cls, text: str) -> Dict[str, List[str]]:
        """
        从文本中提取关键词

        Args:
            text: 市场标题或描述

        Returns:
            关键词字典 {'entities': [], 'numbers': [], 'words': []}
        """
        if not text:
            return {'entities': [], 'numbers': [], 'words': []}

        text = text.strip()
        keywords = {
            'entities': [],
            'numbers': [],
            'words': []
        }

        # 提取年份
        years = re.findall(cls.PATTERNS['year'], text, re.IGNORECASE)
        keywords['numbers'].extend([f"year_{y}" for y in years])

        # 提取价格
        prices = re.findall(cls.PATTERNS['price'], text, re.IGNORECASE)
        keywords['numbers'].extend([f"price_{p}" for p in prices])

        # 提取百分比
        percents = re.findall(cls.PATTERNS['percent'], text, re.IGNORECASE)
        keywords['numbers'].extend([f"percent_{p}" for p in percents])

        # 提取人名/实体
        for pattern_name, pattern in cls.PATTERNS.items():
            if pattern_name in ['trump', 'biden', 'crypto']:
                matches = re.findall(pattern, text, re.IGNORECASE)
                keywords['entities'].extend([m.lower() for m in matches])

        # 提取核心词汇
        # 清理文本
        clean_text = re.sub(r'[^\w\s]', ' ', text)
        words = clean_text.lower().split()

        # 过滤停用词和短词
        significant_words = [
            w for w in words
            if len(w) > 2 and w not in cls.STOP_WORDS
        ]

        keywords['words'] = list(set(significant_words))

        return keywords

    @classmethod
    def calculate_similarity(cls, text1: str, text2: str) -> float:
        """
        计算两个文本的相似度

        Args:
            text1: 文本1
            text2: 文本2

        Returns:
            相似度分数 0-1
        """
        keywords1 = cls.extract_keywords(text1)
        keywords2 = cls.extract_keywords(text2)

        score = 0.0

        # 实体匹配（权重高）
        entities1 = set(keywords1['entities'])
        entities2 = set(keywords2['entities'])
        if entities1 and entities2:
            entity_similarity = len(entities1 & entities2) / len(entities1 | entities2)
            score += entity_similarity * 0.4

        # 数字匹配（年份、价格等）
        numbers1 = set(keywords1['numbers'])
        numbers2 = set(keywords2['numbers'])
        if numbers1 and numbers2:
            number_similarity = len(numbers1 & numbers2) / len(numbers1 | numbers2)
            score += number_similarity * 0.3

        # 词汇相似度
        words1 = set(keywords1['words'])
        words2 = set(keywords2['words'])
        if words1 and words2:
            word_similarity = len(words1 & words2) / len(words1 | words2)
            score += word_similarity * 0.2

        # 字符串相似度（SequenceMatcher）
        str_similarity = SequenceMatcher(None, text1.lower(), text2.lower()).ratio()
        score += str_similarity * 0.1

        return min(1.0, score)


class MarketMatcher:
    """
    市场匹配器
    自动匹配不同平台上的同一市场
    """

    def __init__(self, config: Dict):
        self.config = config
        self.logger = logging.getLogger(__name__)

        # 匹配阈值（降低以提高匹配成功率）
        self.min_confidence = config.get('market_match', {}).get('min_confidence', 0.2)
        self.max_matches_per_market = config.get('market_match', {}).get('max_matches', 3)

        # 缓存
        self._match_cache: Dict[str, MarketMatch] = {}
        self._cache_time: float = 0
        self._cache_duration = config.get('market_match', {}).get('cache_minutes', 30) * 60

        # 关键词提取器
        self.extractor = KeywordExtractor()

    def build_market_map(self,
                         poly_client,
                         predict_client,
                         probable_client) -> Dict[str, MarketMatch]:
        """
        构建跨平台市场映射

        Args:
            poly_client: Polymarket 客户端
            predict_client: Predict.fun 客户端
            probable_client: Probable.markets 客户端

        Returns:
            市场映射字典 {polymarket_id: MarketMatch}
        """
        self.logger.info("开始构建跨平台市场映射...")

        # 检查缓存
        if time.time() - self._cache_time < self._cache_duration and self._match_cache:
            self.logger.info(f"使用缓存的市场映射 ({len(self._match_cache)} 个匹配)")
            return self._match_cache

        # 获取各平台市场列表
        poly_markets = self._get_polymarket_markets(poly_client)
        predict_markets = self._get_predict_markets(predict_client)
        probable_markets = self._get_probable_markets(probable_client)

        self.logger.info(f"Polymarket: {len(poly_markets)} 个市场")
        self.logger.info(f"Predict.fun: {len(predict_markets)} 个市场")
        self.logger.info(f"Probable.markets: {len(probable_markets)} 个市场")

        # 构建匹配
        market_map = {}

        for poly_market in poly_markets:
            poly_id = poly_market.get('condition_id') or poly_market.get('question_id', '')
            if not poly_id:
                continue

            poly_title = poly_market.get('question', '')

            # 查找 Predict.fun 匹配
            predict_match = self._find_best_match(
                poly_title,
                predict_markets,
                'id',
                'question'
            )

            # 查找 Probable.markets 匹配
            probable_match = self._find_best_match(
                poly_title,
                probable_markets,
                'id',
                'question'
            )

            # 计算置信度
            confidence = 0.5  # Polymarket 基础分
            if predict_match:
                confidence += predict_match['score'] * 0.25
            if probable_match:
                confidence += probable_match['score'] * 0.25

            # 只保留高置信度匹配
            if confidence >= self.min_confidence:
                match = MarketMatch(
                    polymarket_id=poly_id,
                    polymarket_title=poly_title,
                    predict_id=predict_match['id'] if predict_match else None,
                    predict_title=predict_match['title'] if predict_match else None,
                    probable_id=probable_match['id'] if probable_match else None,
                    probable_title=probable_match['title'] if probable_match else None,
                    confidence=round(confidence, 2)
                )
                market_map[poly_id] = match

                self.logger.debug(
                    f"匹配: {poly_title[:40]}... "
                    f"(置信度: {confidence:.2f}, "
                    f"Predict: {bool(predict_match)}, "
                    f"Probable: {bool(probable_match)})"
                )

        # 更新缓存
        self._match_cache = market_map
        self._cache_time = time.time()

        self.logger.info(f"市场映射构建完成: {len(market_map)} 个有效匹配")
        return market_map

    def _get_polymarket_markets(self, poly_client) -> List[Dict]:
        """获取 Polymarket 市场列表"""
        try:
            markets = poly_client.get_all_markets(limit=1000)  # 全站监控
            # 过滤活跃市场
            return [m for m in markets if m.get('active', True)]
        except Exception as e:
            self.logger.error(f"获取 Polymarket 市场失败: {e}")
            return []

    def _get_predict_markets(self, predict_client) -> List[Dict]:
        """获取 Predict.fun 市场列表（全站监控）"""
        try:
            return predict_client.get_markets(active_only=True)
        except Exception as e:
            self.logger.error(f"获取 Predict.fun 市场失败: {e}")
            return []

    def _get_probable_markets(self, probable_client) -> List[Dict]:
        """获取 Probable.markets 市场列表（全站监控）"""
        try:
            return probable_client.get_markets(active_only=True)
        except Exception as e:
            self.logger.error(f"获取 Probable.markets 市场失败: {e}")
            return []

    def _find_best_match(self,
                         target_title: str,
                         markets: List[Dict],
                         id_field: str,
                         title_field: str) -> Optional[Dict]:
        """
        查找最佳匹配市场

        Args:
            target_title: 目标市场标题
            markets: 候选市场列表
            id_field: ID 字段名
            title_field: 标题字段名

        Returns:
            最佳匹配 {'id': ..., 'title': ..., 'score': ...} 或 None
        """
        if not markets:
            return None

        best_match = None
        best_score = 0

        for market in markets:
            market_title = market.get(title_field, '')
            if not market_title:
                continue

            # 计算相似度
            score = self.extractor.calculate_similarity(target_title, market_title)

            if score > best_score and score >= self.min_confidence:
                best_score = score
                best_match = {
                    'id': market.get(id_field, ''),
                    'title': market_title,
                    'score': score
                }

        return best_match

    def get_match(self, polymarket_id: str) -> Optional[MarketMatch]:
        """
        获取指定市场的匹配信息

        Args:
            polymarket_id: Polymarket 市场 ID

        Returns:
            市场匹配信息或 None
        """
        return self._match_cache.get(polymarket_id)

    def get_statistics(self) -> Dict:
        """获取匹配统计信息"""
        if not self._match_cache:
            return {'total': 0, 'with_predict': 0, 'with_probable': 0}

        with_predict = sum(1 for m in self._match_cache.values() if m.predict_id)
        with_probable = sum(1 for m in self._match_cache.values() if m.probable_id)
        avg_confidence = sum(m.confidence for m in self._match_cache.values()) / len(self._match_cache)

        return {
            'total': len(self._match_cache),
            'with_predict': with_predict,
            'with_probable': with_probable,
            'avg_confidence': round(avg_confidence, 2)
        }

    def clear_cache(self):
        """清除缓存"""
        self._match_cache.clear()
        self._cache_time = 0
        self.logger.info("市场匹配缓存已清除")


def create_market_matcher(config: Dict) -> MarketMatcher:
    """
    创建市场匹配器

    Args:
        config: 配置字典

    Returns:
        MarketMatcher 实例
    """
    return MarketMatcher(config)
