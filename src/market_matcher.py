"""
统一市场匹配模块 - 参考 dr-manhattan 架构重构

两层匹配策略：
  层级 1：手动映射（ManualMapping）— 100% 准确，用于高价值市场
  层级 2：自动匹配（MarketMatcher + 多策略加权评分）— 用于发现新机会

特性：
  - 硬约束：年份/价格必须匹配（防止 "Trump 2024" vs "Trump 2028" 误匹配）
  - 加权评分：实体0.4 + 数字0.3 + 词汇0.2 + 字符串0.1
  - 一对一匹配：防止重复匹配
"""

import re
import logging
import os
import json
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


# ==================== 数据结构 ====================

@dataclass
class OutcomeRef:
    """跨平台 outcome 引用"""
    platform: str          # 'polymarket', 'opinion', 'predict'
    market_id: str         # 该平台上的市场 ID
    outcome: str           # 'Yes' 或 'No' 或具体 outcome 名


@dataclass
class ManualMapping:
    """一组手动映射的市场"""
    slug: str              # 人类可读标识，如 "trump-2028"
    description: str       # 描述
    outcomes: Dict[str, Dict[str, OutcomeRef]]  # outcome_key → platform → OutcomeRef


@dataclass
class MarketMatch:
    """市场匹配结果（保持兼容原有结构）"""
    polymarket_id: str
    polymarket_title: str
    predict_id: Optional[str] = None
    predict_title: Optional[str] = None
    opinion_id: Optional[str] = None
    opinion_title: Optional[str] = None
    confidence: float = 0.0  # 匹配置信度 0-1


# ==================== 手动映射数据 ====================

# 手动映射（高价值市场，保证 100% 准确）
# 格式：每个条目将同一个现实事件在不同平台上的市场和 outcome 对应起来
MANUAL_MAPPINGS: List[ManualMapping] = [
    # 示例：可以在这里添加已知的跨平台市场对
    # ManualMapping(
    #     slug="trump-president-2028",
    #     description="Will Trump be president in 2028?",
    #     outcomes={
    #         "yes": {
    #             "polymarket": OutcomeRef("polymarket", "condition-id-xxx", "Yes"),
    #             "opinion": OutcomeRef("opinion", "42", "Yes"),
    #             "predict": OutcomeRef("predict", "market-id-yyy", "Yes"),
    #         }
    #     }
    # ),
]


def load_manual_mappings_from_file(filepath: str = None) -> List[ManualMapping]:
    """从 JSON 文件加载手动映射（可选，方便运行时更新而不改代码）"""
    if filepath is None:
        filepath = os.path.join(
            os.path.dirname(__file__),
            '..',
            'config',
            'market_mappings.json'
        )

    if not os.path.exists(filepath):
        logger.debug(f"手动映射文件不存在: {filepath}")
        return MANUAL_MAPPINGS

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        mappings = []
        for item in raw:
            outcomes = {}
            for outcome_key, refs in item.get('outcomes', {}).items():
                outcomes[outcome_key] = {
                    platform: OutcomeRef(
                        platform=platform,
                        market_id=ref['market_id'],
                        outcome=ref['outcome']
                    )
                    for platform, ref in refs.items()
                }

            mappings.append(ManualMapping(
                slug=item['slug'],
                description=item.get('description', ''),
                outcomes=outcomes,
            ))

        logger.info(f"从 {filepath} 加载了 {len(mappings)} 个手动映射")
        return MANUAL_MAPPINGS + mappings

    except Exception as e:
        logger.warning(f"加载手动映射文件失败: {e}")
        return MANUAL_MAPPINGS


# ==================== 关键词提取器（保留原有实现）====================

class KeywordExtractor:
    """关键词提取器（保留原有良好实现，新增硬约束支持）"""

    # 常见停用词
    STOP_WORDS = {
        'will', 'won', 'the', 'a', 'an', 'is', 'are', 'was', 'were',
        'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does',
        'did', 'can', 'could', 'should', 'would', 'may', 'might',
        'must', 'shall', 'to', 'for', 'of', 'in', 'on', 'at', 'by',
        'with', 'from', 'as', 'this', 'that', 'these', 'those',
        'end', 'before', 'after', 'during', 'occur', 'happen'
    }

    # 常见实体模式
    PATTERNS = {
        'year': r'\b(20[12][0-9]|20[3-9][0-9])\b',
        'price': r'\$[\d,]+(?:\.\d+)?|\d+\s*(?:dollars?|USD|million|billion)',
        'percent': r'\d+(?:\.\d+)?%',
        'trump': r'\bTrump\b',
        'biden': r'\bBiden\b',
        'crypto': r'\b(?:Bitcoin|BTC|Ethereum|ETH|crypto)\b',
        'gta': r'\bGTA\s*(?:VI|V|4|5|6)?\b',
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

        # 提取价格（归一化：去掉 $, 逗号, 空格, 单位词，只保留数字）
        prices = re.findall(cls.PATTERNS['price'], text, re.IGNORECASE)
        for p in prices:
            normalized = re.sub(r'[^\d.]', '', p)  # "$90,000" → "90000", "90000 USD" → "90000"
            if normalized:
                keywords['numbers'].append(f"price_{normalized}")

        # 提取百分比
        percents = re.findall(cls.PATTERNS['percent'], text, re.IGNORECASE)
        keywords['numbers'].extend([f"percent_{p}" for p in percents])

        # 提取人名/实体
        for pattern_name, pattern in cls.PATTERNS.items():
            if pattern_name in ['trump', 'biden', 'crypto', 'gta']:
                matches = re.findall(pattern, text, re.IGNORECASE)
                keywords['entities'].extend([m.lower() for m in matches])

        # 提取核心词汇
        clean_text = re.sub(r'[^\w\s]', ' ', text)
        words = clean_text.lower().split()

        # 过滤停用词、短词、纯数字碎片（如 $1,500,000 拆出的 "000", "500"）
        significant_words = [
            w for w in words
            if len(w) > 2 and w not in cls.STOP_WORDS and not w.isdigit()
        ]

        keywords['words'] = list(set(significant_words))

        return keywords

    @classmethod
    def calculate_similarity(cls, text1: str, text2: str,
                             keywords1: Dict = None, keywords2: Dict = None) -> float:
        """
        计算两个文本的相似度（v3：修复单实体误匹配问题）

        修复问题：
          旧版本中 "Trump cabinet member" vs "Trump deport people" 会得到 0.6 分
          因为仅 "Trump" 实体匹配就给了 0.4，轻松超过 0.5 阈值
          导致大量完全不同主题的市场被错误匹配，产生虚假套利

        改进：
          1. 新增硬约束：核心词汇必须有重叠（仅实体相同不够）
          2. 调整权重：降低实体权重(0.25)，提高词汇权重(0.35)
          3. 不再给"都没有其他数字"免费加分

        Args:
            text1: 文本1
            text2: 文本2
            keywords1: 预计算的关键词（可选，避免重复提取）
            keywords2: 预计算的关键词（可选，避免重复提取）

        Returns:
            相似度分数 0-1
        """
        if keywords1 is None:
            keywords1 = cls.extract_keywords(text1)
        if keywords2 is None:
            keywords2 = cls.extract_keywords(text2)

        # === 硬约束 1：年份/价格不同则直接判 0 ===
        numbers1 = set(keywords1['numbers'])
        numbers2 = set(keywords2['numbers'])

        years1 = {n for n in numbers1 if n.startswith('year_')}
        years2 = {n for n in numbers2 if n.startswith('year_')}
        if years1 and years2 and not (years1 & years2):
            return 0.0

        prices1 = {n for n in numbers1 if n.startswith('price_')}
        prices2 = {n for n in numbers2 if n.startswith('price_')}
        if prices1 and prices2 and not (prices1 & prices2):
            return 0.0

        # === 硬约束 2：核心词汇必须有交集 ===
        # 防止 "Trump cabinet member" 与 "Trump deport people" 被匹配
        words1 = set(keywords1['words'])
        words2 = set(keywords2['words'])
        entities1 = set(keywords1['entities'])
        entities2 = set(keywords2['entities'])

        # 从词汇中排除实体名（避免重复计算）
        entity_words = {e.lower() for e in (entities1 | entities2)}
        core_words1 = words1 - entity_words
        core_words2 = words2 - entity_words

        # 如果两边都有 >=2 个核心词但没有任何交集 → 主题不同，直接判 0
        if len(core_words1) >= 2 and len(core_words2) >= 2:
            core_overlap = core_words1 & core_words2
            if len(core_overlap) == 0:
                return 0.0

        # === 硬约束 3：实体相同时的语义反转检测 ===
        # 同一人/事件的"离职"问题 vs "留任"问题不能互相匹配。
        # 示例：Poly "Trump out as President by March?" (2.8c) 与
        #       Opinion "Will Trump remain president?" (54c) 共享 trump/president
        #       但方向完全相反，不应匹配。
        if entities1 & entities2:  # 存在共同实体（同一个人/事件）
            exit_words = {
                'out', 'leave', 'leaves', 'leaving', 'left',
                'resign', 'resigns', 'resigned', 'resignation',
                'removed', 'removal', 'remove',
                'fired', 'fire', 'dismiss', 'dismissed',
                'oust', 'ousted', 'ousting',
                'impeach', 'impeached', 'impeachment',
                'depart', 'departed', 'departure', 'step', 'steps', 'stepped',
                'quit', 'quits', 'quitting',
            }
            stay_words = {
                'remain', 'remains', 'remained', 'remaining',
                'stay', 'stays', 'stayed', 'staying',
                'continue', 'continues', 'continued', 'continuing',
                'retain', 'retains', 'retained',
                'keep', 'keeps', 'kept', 'keeping',
                'hold', 'holds', 'held', 'holding',
                'serve', 'serves', 'served', 'serving',
                'reelect', 'reelected', 'win', 'wins', 'winning', 'won',
            }
            has_exit1 = bool(words1 & exit_words)
            has_stay1 = bool(words1 & stay_words)
            has_exit2 = bool(words2 & exit_words)
            has_stay2 = bool(words2 & stay_words)
            # 一边有"离职"词，另一边有"留任"词 → 问的是互斥的事件方向 → 判 0
            if (has_exit1 and has_stay2) or (has_exit2 and has_stay1):
                return 0.0

        # === 加权评分（调整后的权重）===
        score = 0.0

        # 实体匹配（权重 0.25，从 0.4 降低）
        if entities1 and entities2:
            entity_similarity = len(entities1 & entities2) / len(entities1 | entities2)
            score += entity_similarity * 0.25

        # 数字匹配（权重 0.2）
        other_numbers1 = {n for n in numbers1 if not n.startswith(('year_', 'price_', 'percent_'))}
        other_numbers2 = {n for n in numbers2 if not n.startswith(('year_', 'price_', 'percent_'))}
        if other_numbers1 and other_numbers2:
            number_similarity = len(other_numbers1 & other_numbers2) / len(other_numbers1 | other_numbers2)
            score += number_similarity * 0.2

        # 词汇相似度（权重 0.35，从 0.2 提高）
        if words1 and words2:
            word_similarity = len(words1 & words2) / len(words1 | words2)
            score += word_similarity * 0.35

        # Early exit: if keyword-based score (max 0.8) is too low,
        # skip the expensive SequenceMatcher (saves ~70% of compute time)
        if score < 0.15:
            return score

        # 字符串相似度（权重 0.2，从 0.1 提高）
        str_similarity = SequenceMatcher(None, text1.lower(), text2.lower()).ratio()
        score += str_similarity * 0.2

        return min(1.0, score)


# ==================== 统一市场匹配器 ====================

class MarketMatcher:
    """
    统一市场匹配器（v2：倒排索引优化）

    支持：
      - 手动映射（100% 准确）
      - 自动匹配（倒排索引候选 + 加权多因子评分 + 硬约束）

    性能：
      - 旧版 O(n×m)：Poly(5000) × Opinion(500) = 2,500,000 次完整相似度计算
      - 新版 O(n+m+c)：预索引 + 候选过滤，仅对共享关键词的市场对计算相似度
      - 关键词提取缓存：每个市场只提取一次，避免 O(n×m) 次重复提取
    """

    def __init__(self, config: Dict):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.extractor = KeywordExtractor()

        # 加载手动映射
        self.manual_mappings = load_manual_mappings_from_file()
        self.logger.info(f"加载了 {len(self.manual_mappings)} 个手动映射")

    @staticmethod
    def _get_index_tokens(keywords: Dict) -> Set[str]:
        """从关键词字典中提取用于倒排索引的 token 集合

        将 entities, numbers, words 合并为统一的 token 集合，
        用于快速候选筛选（两个市场必须共享至少一个 token 才进入精确匹配）
        """
        tokens = set()
        tokens.update(keywords.get('entities', []))
        tokens.update(keywords.get('numbers', []))
        tokens.update(keywords.get('words', []))
        return tokens

    def match_markets_cross_platform(
        self,
        markets_a: List[Dict],
        markets_b: List[Dict],
        title_field_a: str = 'title',
        title_field_b: str = 'title',
        id_field_a: str = 'id',
        id_field_b: str = 'id',
        platform_a: str = '',
        platform_b: str = '',
        min_similarity: float = 0.35,
    ) -> List[Tuple[Dict, Dict, float]]:
        """
        统一的跨平台市场匹配（v2：倒排索引优化）

        层级 1：先查手动映射
        层级 2：倒排索引生成候选对 → 精确相似度计算

        性能优化：
          - 关键词预计算：O(n+m) 次提取，而非 O(n×m)
          - 倒排索引：keyword → [market_indices]，快速找到共享关键词的候选对
          - 仅对候选对计算完整相似度（SequenceMatcher 等昂贵操作）

        Args:
            markets_a, markets_b: 两个平台的市场列表
            title_field_a/b: 标题字段名
            id_field_a/b: ID 字段名
            platform_a/b: 平台名（'polymarket', 'opinion', 'predict'）
            min_similarity: 最低相似度阈值

        Returns:
            [(market_a, market_b, confidence), ...] 按置信度降序
        """
        results = []
        matched_b_ids = set()  # 防止 B 侧重复匹配

        # === 层级 1：手动映射（100% 准确）===
        for mapping in self.manual_mappings:
            for outcome_key, refs in mapping.outcomes.items():
                ref_a = refs.get(platform_a)
                ref_b = refs.get(platform_b)
                if not ref_a or not ref_b:
                    continue

                ma = next(
                    (m for m in markets_a if str(m.get(id_field_a, '')) == ref_a.market_id),
                    None
                )
                mb = next(
                    (m for m in markets_b if str(m.get(id_field_b, '')) == ref_b.market_id),
                    None
                )

                if ma and mb:
                    mb_id = str(mb.get(id_field_b, ''))
                    matched_b_ids.add(mb_id)
                    results.append((ma, mb, 1.0))
                    self.logger.debug(f"手动映射匹配: {mapping.slug} - {outcome_key}")

        # === 层级 2：倒排索引 + 自动匹配 ===

        # Step 1: 预计算所有 B 侧市场的关键词 — O(m)
        b_entries = []  # [(index, market, title, id, keywords, tokens)]
        for i, mb in enumerate(markets_b):
            mb_id = str(mb.get(id_field_b, ''))
            if mb_id in matched_b_ids:
                continue
            title_b = mb.get(title_field_b, '')
            if not title_b:
                continue
            kw_b = self.extractor.extract_keywords(title_b)
            tokens_b = self._get_index_tokens(kw_b)
            b_entries.append((i, mb, title_b, mb_id, kw_b, tokens_b))

        # Step 2: 构建倒排索引 — O(m × avg_tokens)
        # token → set of indices in b_entries
        inverted_index: Dict[str, Set[int]] = {}
        for idx, (i, mb, title_b, mb_id, kw_b, tokens_b) in enumerate(b_entries):
            for token in tokens_b:
                if token not in inverted_index:
                    inverted_index[token] = set()
                inverted_index[token].add(idx)

        # 过滤高频 token（出现在 >20% 的 B 市场中 = 噪音，无区分度）
        if b_entries:
            max_df = max(len(b_entries) // 5, 10)  # at least 10 to not over-prune small sets
            noisy_tokens = {t for t, ids in inverted_index.items() if len(ids) > max_df}
            for t in noisy_tokens:
                del inverted_index[t]
            if noisy_tokens:
                self.logger.debug(f"[Matcher] Pruned {len(noisy_tokens)} noisy tokens (df>{max_df})")

        # Step 3: 预计算 A 侧关键词 + 倒排索引查询候选 — O(n × avg_tokens)
        total_candidates = 0
        total_a = 0

        for ma in markets_a:
            title_a = ma.get(title_field_a, '')
            if not title_a:
                continue
            total_a += 1

            kw_a = self.extractor.extract_keywords(title_a)
            tokens_a = self._get_index_tokens(kw_a)

            # 通过倒排索引获取候选 B 市场（共享至少一个 token）
            candidate_indices = set()
            for token in tokens_a:
                if token in inverted_index:
                    candidate_indices.update(inverted_index[token])

            total_candidates += len(candidate_indices)

            # Step 4: 仅对候选对计算完整相似度
            best_match = None
            best_score = 0.0

            for b_idx in candidate_indices:
                _, mb, title_b, mb_id, kw_b, _ = b_entries[b_idx]
                if mb_id in matched_b_ids:
                    continue

                # 传入预计算的关键词，避免重复提取
                score = self.extractor.calculate_similarity(
                    title_a, title_b, keywords1=kw_a, keywords2=kw_b
                )

                if score > best_score and score >= min_similarity:
                    best_score = score
                    best_match = (mb, mb_id)

            if best_match:
                mb, mb_id = best_match
                matched_b_ids.add(mb_id)
                results.append((ma, mb, best_score))

        # 性能日志
        b_count = len(b_entries)
        brute_force = total_a * b_count
        self.logger.info(
            f"[Matcher] A={total_a} B={b_count} "
            f"Brute-force={brute_force:,} Candidates={total_candidates:,} "
            f"Reduction={((1 - total_candidates / brute_force) * 100):.1f}% "
            f"Matched={len(results)}"
            if brute_force > 0 else
            f"[Matcher] A={total_a} B={b_count} (empty)"
        )

        results.sort(key=lambda x: x[2], reverse=True)
        return results


# ==================== 兼容性入口 ====================

def create_market_matcher(config: Dict) -> MarketMatcher:
    """
    创建市场匹配器

    Args:
        config: 配置字典

    Returns:
        MarketMatcher 实例
    """
    return MarketMatcher(config)
