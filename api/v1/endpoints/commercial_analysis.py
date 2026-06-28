# -*- coding: utf-8 -*-
"""Public commercial stock analysis endpoint for 每日股研AI."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import statistics
import subprocess
import time
from html import unescape as html_unescape
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from unicodedata import normalize as normalize_unicode

import requests
from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from api.v1.schemas.commercial_analysis import (
    CommercialAiRecommendation,
    CommercialAnalysisResponse,
    CommercialDataAuditItem,
    CommercialDataQuality,
    CommercialDecisionReason,
    CommercialIndustryTrend,
    CommercialIndustryTrendItem,
    CommercialInvestmentHypothesis,
    CommercialNewsItem,
    CommercialNewsSummary,
    CommercialQuantMetric,
    CommercialRelatedSector,
    CommercialScore,
    CommercialSearchItem,
    CommercialSearchResponse,
    CommercialSniperPoint,
    CommercialStockIdentity,
    CommercialValuationRange,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_SUPPORTED_CODE_RE = re.compile(
    r"^(?:HK\d{1,5}|\d{1,5}\.HK|\d{1,5}|\d{6}|(?:SH|SZ|BJ)\d{6}|\d{6}\.(?:SH|SZ|SS|BJ))$",
    re.IGNORECASE,
)
_MARKET_ONLY_QUERIES = {"a", "a股", "h", "h股", "hk", "sh", "sz", "cn", "沪", "深", "港股"}
_TENCENT_KLINE_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_AASTOCKS_BASE = "https://www.aastocks.com"
_EASTMONEY_ANN_ENDPOINT = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}
_QUOTE_CACHE: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}
_QUOTE_CACHE_SECONDS = 45
_NEWS_CACHE: Dict[str, tuple[float, List[Dict[str, str]]]] = {}
_NEWS_CACHE_SECONDS = 300
_NEWS_DISPLAY_LIMIT = 10
_NEWS_POOL_LIMIT = 24
_NEWS_RESULT_LIMIT = _NEWS_DISPLAY_LIMIT
_SECTOR_CACHE: Dict[str, tuple[float, List[Dict[str, str]]]] = {}
_SECTOR_CACHE_SECONDS = 120
_FINANCIAL_CACHE: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}
_FINANCIAL_CACHE_SECONDS = 1800
_VALUATION_RATIO_CACHE: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}
_VALUATION_RATIO_CACHE_SECONDS = 21600
_COMPANY_PROFILE_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_COMPANY_PROFILE_CACHE_SECONDS = 3600

_VERIFIED_COMPANY_PROFILES: Dict[str, Dict[str, Any]] = {
    "HK6651": {
        "name": "五一视界",
        "legal_name": "北京五一视界数字孪生科技股份有限公司",
        "stock_code": "6651.HK",
        "verified_position": "中国首家登陆资本市场的“物理AI”核心基础设施企业",
        "business": "构建数字世界与物理世界之间的桥梁，推动物理AI技术创新与应用落地",
        "products": ["51Aes", "51Sim", "51Earth"],
        "industry": "物理AI核心基础设施",
        "business_model": "通过51Aes、51Sim、51Earth等平台，为城市、工业、水利、智能驾驶等场景提供数字孪生、仿真训练、空间智能与合成数据能力",
        "industry_logic": "物理AI需要空间数据、仿真训练和世界模型底座，公司价值取决于平台商业化落地、客户续费和行业场景复制",
        "watch_points": "重点看收入增速、毛利率、经营现金流、客户留存、新客户获取和平台复购",
        "risk_boundary": "若商业化放量不及预期、客户预算收缩、现金流弱化或同类技术竞争加剧，基本面支撑会减弱",
        "source_name": "51WORLD官网",
        "source_url": "https://www.51world.com.cn/about",
        "status": "verified_official_profile",
        "identity_locked": True,
        "protected_terms": ["物理AI", "核心基础设施"],
    },
    "06651.HK": {
        "alias_of": "HK6651",
    },
}

_POSITIVE_NEWS_KEYWORDS = (
    "增长",
    "增長",
    "同比增",
    "高增",
    "盈利",
    "扭亏",
    "扭虧",
    "回购",
    "回購",
    "增持",
    "获批",
    "獲批",
    "获纳入",
    "獲納入",
    "调入",
    "調入",
    "中标",
    "中標",
    "合作",
    "完成",
    "上调",
    "上調",
    "评级上调",
    "信用评级上调",
    "突破",
    "上线",
    "上線",
    "扩张",
    "擴張",
    "落地",
    "扩展",
    "擴展",
    "破顶",
    "破頂",
    "造好",
    "强势",
    "強勢",
    "涨",
    "漲",
    "净流入",
    "淨流入",
    "分派",
    "派息",
    "分红",
    "分紅",
    "净买入",
    "淨買入",
)
_RISK_NEWS_KEYWORDS = (
    "亏损",
    "虧損",
    "亏损扩大",
    "虧損擴",
    "下滑",
    "下降",
    "减持",
    "減持",
    "配售",
    "折让",
    "折讓",
    "停牌",
    "退市",
    "处罚",
    "處罰",
    "立案",
    "诉讼",
    "訴訟",
    "警示",
    "违约",
    "違約",
    "风险",
    "風險",
    "终止",
    "終止",
    "不及预期",
    "不及預期",
    "毛利率降",
    "跌",
    "挫",
    "剔出",
    "失效",
    "死亡交叉",
    "死叉",
    "净流出",
    "淨流出",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _stock_api_bridge_path() -> Path:
    return _repo_root() / "apps" / "dsa-web" / "scripts" / "stock_api_bridge.mjs"


def _compact_query(value: str) -> str:
    return (
        normalize_unicode("NFKC", str(value or ""))
        .strip()
        .lower()
        .replace(" ", "")
        .replace("\t", "")
    )


def _clean_realtime_stock_name(value: Any) -> str:
    """Remove exchange status prefixes from quote names while preserving risk labels like ST."""

    name = normalize_unicode("NFKC", str(value or "")).strip()
    if not name:
        return ""
    return re.sub(r"^(?:XD|XR|DR)(?=[\u4e00-\u9fff])", "", name, flags=re.IGNORECASE).strip()


def _is_market_only_query(query: str) -> bool:
    return query.replace(".", "").replace("_", "").replace("-", "").replace("/", "") in _MARKET_ONLY_QUERIES


def _contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _mean(values: List[float]) -> Optional[float]:
    cleaned = [item for item in values if isinstance(item, (int, float)) and item > 0]
    if not cleaned:
        return None
    return sum(cleaned) / len(cleaned)


def _quantile(values: List[float], q: float) -> Optional[float]:
    cleaned = sorted(float(item) for item in values if isinstance(item, (int, float)) and item > 0)
    if not cleaned:
        return None
    q = _clamp(q, 0.0, 1.0)
    if len(cleaned) == 1:
        return cleaned[0]
    position = (len(cleaned) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return cleaned[int(position)]
    weight = position - lower
    return cleaned[lower] * (1.0 - weight) + cleaned[upper] * weight


def _weighted_average(items: List[tuple[Optional[float], float]]) -> Optional[float]:
    weighted_sum = 0.0
    total_weight = 0.0
    for value, weight in items:
        if isinstance(value, (int, float)) and value > 0 and weight > 0:
            weighted_sum += float(value) * weight
            total_weight += weight
    if total_weight <= 0:
        return None
    return weighted_sum / total_weight


def _stable_price_anchor(current_price: float, closes: List[float]) -> float:
    """Blend realtime price with multi-period averages to reduce one-day noise."""

    ma5 = _mean(closes[-5:])
    ma20 = _mean(closes[-20:])
    ma60 = _mean(closes[-60:])
    return _weighted_average(
        [
            (current_price, 0.42),
            (ma5, 0.14),
            (ma20, 0.28),
            (ma60, 0.16),
        ]
    ) or current_price


def _bounded_signal(value: Optional[float], scale: float, limit: float) -> float:
    if value is None or scale <= 0:
        return 0.0
    return math.tanh(float(value) / scale) * limit


def _format_signed_percent(value: Optional[float]) -> str:
    if value is None:
        return "待读取"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


def _format_ratio(value: Optional[float]) -> str:
    if value is None:
        return "待读取"
    return f"{value:.2f}x"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def _load_stock_catalog() -> List[Dict[str, Any]]:
    index_path = _repo_root() / "apps" / "dsa-web" / "public" / "stocks.index.json"
    try:
        raw_items = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - search can fall back to curated items.
        logger.warning("[commercial-analysis] stock index unavailable: %s", exc)
        raw_items = []

    catalog: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, list) or len(item) < 10:
            continue
        canonical, display_code, name, pinyin, abbr, aliases, market, asset_type, active, popularity = item[:10]
        if not active or asset_type != "stock" or market not in {"CN", "HK"}:
            continue

        canonical_code = str(canonical or "").strip().upper()
        short_code = str(display_code or "").strip().upper()
        stock_name = str(name or "").strip()
        if not canonical_code or not short_code or not stock_name:
            continue

        display_for_analysis = _catalog_analysis_code(canonical_code, short_code, str(market))
        catalog.append(
            {
                "name": stock_name,
                "code": display_for_analysis,
                "canonical_code": canonical_code,
                "display_code": short_code,
                "market": "H股" if market == "HK" else "A股",
                "exchange": "HKEX" if market == "HK" else _exchange_from_code(canonical_code),
                "pinyin": str(pinyin or ""),
                "abbr": str(abbr or ""),
                "aliases": [str(alias) for alias in aliases if str(alias).strip()] if isinstance(aliases, list) else [],
                "popularity": float(popularity or 0),
            }
        )

    curated = [
        {
            "name": "五一视界",
            "code": "HK6651",
            "canonical_code": "06651.HK",
            "display_code": "06651",
            "market": "H股",
            "exchange": "HKEX",
            "pinyin": "wuyishijie",
            "abbr": "wysj",
            "aliases": ["五一", "51WORLD", "51World", "HK6651", "6651.HK"],
            "popularity": 1000.0,
        },
        {
            "name": "腾讯控股",
            "code": "0700.HK",
            "canonical_code": "00700.HK",
            "display_code": "00700",
            "market": "H股",
            "exchange": "HKEX",
            "pinyin": "tengxunkonggu",
            "abbr": "txkg",
            "aliases": ["腾讯", "Tencent", "HK700", "700.HK"],
            "popularity": 900.0,
        },
        {
            "name": "摩尔线程-U",
            "code": "688795.SH",
            "canonical_code": "688795.SH",
            "display_code": "688795",
            "market": "A股",
            "exchange": "SH",
            "pinyin": "moerxiancheng",
            "abbr": "mexc",
            "aliases": ["摩尔线程", "688795"],
            "popularity": 850.0,
        },
    ]
    deduped: Dict[str, Dict[str, Any]] = {item["canonical_code"]: item for item in catalog}
    for item in curated:
        deduped[item["canonical_code"]] = {**deduped.get(item["canonical_code"], {}), **item}
    return list(deduped.values())


def _catalog_analysis_code(canonical_code: str, display_code: str, market: str) -> str:
    if market == "HK":
        digits = (display_code or canonical_code.split(".", 1)[0]).lstrip("0") or "0"
        if digits == "6651":
            return "HK6651"
        return f"{digits.zfill(4)}.HK"
    if canonical_code.endswith(".SS"):
        return f"{canonical_code[:-3]}.SH"
    return canonical_code


def _exchange_from_code(code: str) -> Optional[str]:
    upper = (code or "").upper()
    if upper.endswith(".SH") or upper.endswith(".SS") or upper.startswith("SH"):
        return "SH"
    if upper.endswith(".SZ") or upper.startswith("SZ"):
        return "SZ"
    if upper.endswith(".BJ") or upper.startswith("BJ"):
        return "BJ"
    return None


def _score_catalog_item(query: str, item: Dict[str, Any]) -> float:
    normalized = _compact_query(query)
    if not normalized or _is_market_only_query(normalized):
        return 0.0

    aliases = [str(alias) for alias in item.get("aliases", [])]
    name_terms = [_compact_query(item.get("name", "")), *[_compact_query(alias) for alias in aliases]]
    code_terms = {
        _compact_query(item.get("code", "")),
        _compact_query(item.get("canonical_code", "")),
        _compact_query(item.get("display_code", "")),
    }
    if item.get("market") == "H股":
        digits = str(item.get("display_code", "")).lstrip("0") or str(item.get("display_code", ""))
        code_terms.update({_compact_query(digits), _compact_query(f"hk{digits}"), _compact_query(f"{digits}.hk")})
    pinyin_terms = [_compact_query(item.get("pinyin", "")), _compact_query(item.get("abbr", ""))]

    score = 0.0
    if _contains_chinese(normalized):
        if any(term == normalized for term in name_terms):
            score = 120.0
        elif any(term.startswith(normalized) for term in name_terms if term):
            score = 95.0
        elif any(normalized in term for term in name_terms if term):
            score = 72.0
    elif any(ch.isdigit() for ch in normalized):
        if normalized in code_terms:
            score = 120.0
        elif any(term.startswith(normalized) for term in code_terms if term):
            score = 88.0
        elif len(normalized) >= 3 and any(normalized in term for term in code_terms if term):
            score = 58.0
    elif re.fullmatch(r"[a-z]+", normalized or "") and len(normalized) >= 2:
        if normalized in pinyin_terms:
            score = 100.0
        elif any(term.startswith(normalized) for term in pinyin_terms if term):
            score = 76.0
        elif len(normalized) >= 4 and any(normalized in term for term in pinyin_terms if term):
            score = 48.0

    if score <= 0:
        return 0.0
    return score + min(float(item.get("popularity") or 0) / 100.0, 10.0)


def _search_market_priority(query: str, item: Dict[str, Any]) -> int:
    compact = _compact_query(query)
    market = item.get("market")
    explicit_hk = compact.startswith("hk") or compact.endswith(".hk")
    explicit_a = (
        compact.startswith(("sh", "sz", "bj"))
        or compact.endswith((".sh", ".sz", ".ss", ".bj"))
    )
    if explicit_hk:
        return 0 if market == "H股" else 1
    if explicit_a:
        return 0 if market == "A股" else 1
    return 0 if market == "A股" else 1


def _query_has_explicit_market(query: str) -> bool:
    compact = _compact_query(query)
    return (
        compact.startswith(("hk", "sh", "sz", "bj"))
        or compact.endswith((".hk", ".sh", ".sz", ".ss", ".bj"))
    )


def _sort_scored_catalog_results(
    query: str,
    scored: List[tuple[Dict[str, Any], float]],
    limit: int,
) -> List[tuple[Dict[str, Any], float]]:
    positive = [(item, score) for item, score in scored if score > 0]
    by_score = lambda pair: (-pair[1], pair[0].get("code", ""))
    if _query_has_explicit_market(query):
        return sorted(
            positive,
            key=lambda pair: (
                _search_market_priority(query, pair[0]),
                -pair[1],
                pair[0].get("code", ""),
            ),
        )[:limit]

    a_results = sorted([pair for pair in positive if pair[0].get("market") == "A股"], key=by_score)
    h_results = sorted([pair for pair in positive if pair[0].get("market") == "H股"], key=by_score)
    if a_results and h_results:
        a_take = min(len(a_results), max(1, math.ceil(limit / 2)))
        selected = a_results[:a_take]
        selected.extend(h_results[: max(0, limit - len(selected))])
        if len(selected) < limit:
            selected.extend(a_results[a_take : a_take + (limit - len(selected))])
        return selected[:limit]

    return sorted(positive, key=by_score)[:limit]


def _search_catalog(query: str, limit: int) -> CommercialSearchResponse:
    normalized = _compact_query(query)
    bounded_limit = max(1, min(limit, 12))
    if not normalized or _is_market_only_query(normalized):
        return CommercialSearchResponse(query=query, results=[], updated_at=_now_iso())

    scored = [
        (item, _score_catalog_item(normalized, item))
        for item in _load_stock_catalog()
    ]
    sorted_results = _sort_scored_catalog_results(normalized, scored, bounded_limit)
    results = [
        CommercialSearchItem(
            name=item["name"],
            code=item["code"],
            market=item["market"],
            exchange=item.get("exchange"),
            aliases=item.get("aliases") or [],
            score=round(score, 2),
        )
        for item, score in sorted_results
    ]
    return CommercialSearchResponse(query=query, results=results, updated_at=_now_iso())


def _normalize_code(raw_code: str) -> str:
    code = (raw_code or "").strip().upper().replace(" ", "")
    if not code or not _SUPPORTED_CODE_RE.fullmatch(code):
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_stock_code", "message": "请输入有效的A股或H股代码"},
        )
    if re.fullmatch(r"HK\d{1,5}", code):
        return code
    if re.fullmatch(r"\d{1,5}\.HK", code):
        return f"HK{code.split('.', 1)[0]}"
    if re.fullmatch(r"\d{1,5}", code):
        return f"HK{code}"
    if re.fullmatch(r"(?:SH|SZ|BJ)\d{6}", code):
        return f"{code[2:]}.{code[:2]}"
    if code.endswith(".SS"):
        return code[:-3] + ".SH"
    return code


def _tencent_symbol(code: str) -> Optional[str]:
    upper = (code or "").strip().upper()
    if upper.startswith("HK"):
        digits = upper[2:]
        if digits.isdigit() and 1 <= len(digits) <= 5:
            return f"hk{digits.zfill(5)}"
    if upper.endswith(".HK"):
        digits = upper.split(".", 1)[0]
        if digits.isdigit() and 1 <= len(digits) <= 5:
            return f"hk{digits.zfill(5)}"
    if upper.startswith(("SH", "SZ", "BJ")) and upper[2:].isdigit():
        return f"{upper[:2].lower()}{upper[2:]}"
    if re.fullmatch(r"\d{6}\.(?:SH|SS|SZ|BJ)", upper):
        digits, suffix = upper.split(".", 1)
        prefix = "sh" if suffix in {"SH", "SS"} else suffix.lower()
        return f"{prefix}{digits}"
    if re.fullmatch(r"\d{6}", upper):
        if upper.startswith(("6", "5", "9")):
            return f"sh{upper}"
        if upper.startswith(("8", "9")):
            return f"bj{upper}"
        return f"sz{upper}"
    return None


def _parse_tencent_rows(rows: Any) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    if not isinstance(rows, list):
        return parsed
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        open_price = _safe_float(row[1])
        close_price = _safe_float(row[2])
        high_price = _safe_float(row[3])
        low_price = _safe_float(row[4])
        volume = _safe_float(row[5])
        if close_price is None:
            continue
        parsed.append(
            {
                "date": str(row[0]),
                "open": open_price,
                "close": close_price,
                "high": high_price,
                "low": low_price,
                "volume": volume,
            }
        )
    return parsed


def _parse_tencent_quote(fields: Any, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    quote_fields = fields if isinstance(fields, list) else []
    latest = history[-1] if history else {}
    price = _safe_float(quote_fields[3] if len(quote_fields) > 3 else None) or latest.get("close")
    previous_close = _safe_float(quote_fields[4] if len(quote_fields) > 4 else None)
    if previous_close is None and len(history) > 1:
        previous_close = history[-2].get("close")
    change = _safe_float(quote_fields[31] if len(quote_fields) > 31 else None)
    change_percent = _safe_float(quote_fields[32] if len(quote_fields) > 32 else None)
    if change is None and price is not None and previous_close:
        change = float(price) - float(previous_close)
    if change_percent is None and change is not None and previous_close:
        change_percent = change / float(previous_close) * 100

    return {
        "name": _clean_realtime_stock_name(quote_fields[1]) if len(quote_fields) > 1 and quote_fields[1] else None,
        "price": float(price) if isinstance(price, (int, float)) else None,
        "previous_close": float(previous_close) if isinstance(previous_close, (int, float)) else None,
        "change": float(change) if isinstance(change, (int, float)) else None,
        "change_percent": float(change_percent) if isinstance(change_percent, (int, float)) else None,
        "high": _safe_float(quote_fields[33] if len(quote_fields) > 33 else None) or latest.get("high"),
        "low": _safe_float(quote_fields[34] if len(quote_fields) > 34 else None) or latest.get("low"),
        "volume": _safe_float(quote_fields[36] if len(quote_fields) > 36 else None) or latest.get("volume"),
        "amount": _safe_float(quote_fields[37] if len(quote_fields) > 37 else None),
        "update_time": str(quote_fields[30]).strip() if len(quote_fields) > 30 and quote_fields[30] else _now_iso(),
        "currency": str(quote_fields[77]).strip() if len(quote_fields) > 77 and quote_fields[77] else None,
    }


def _try_tencent_market_data(code: str) -> Optional[Dict[str, Any]]:
    symbol = _tencent_symbol(code)
    if not symbol:
        return None

    cached = _QUOTE_CACHE.get(symbol)
    now = time.time()
    if cached and now - cached[0] < _QUOTE_CACHE_SECONDS:
        return cached[1]

    result: Optional[Dict[str, Any]] = None
    try:
        response = requests.get(
            _TENCENT_KLINE_ENDPOINT,
            params={"param": f"{symbol},day,,,160,qfq"},
            headers=_HTTP_HEADERS,
            timeout=7,
        )
        response.raise_for_status()
        payload = response.json()
        symbol_payload = (payload.get("data") or {}).get(symbol) or {}
        rows = symbol_payload.get("qfqday") or symbol_payload.get("day") or []
        history = _parse_tencent_rows(rows)
        qt_fields = ((symbol_payload.get("qt") or {}).get(symbol)) or []
        quote = _parse_tencent_quote(qt_fields, history)
        if quote.get("price"):
            result = {
                "provider": "实时行情",
                "provider_symbol": symbol,
                "quote": quote,
                "history": history,
            }
    except Exception as exc:  # noqa: BLE001 - public market source is best-effort.
        logger.info("[commercial-analysis] Tencent quote unavailable for %s: %s", code, exc)

    _QUOTE_CACHE[symbol] = (now, result)
    return result


def _stock_api_symbol(code: str) -> Optional[str]:
    upper = (code or "").strip().upper()
    if upper.startswith("HK"):
        digits = upper[2:]
        if digits.isdigit() and 1 <= len(digits) <= 5:
            return f"HK{digits.zfill(5)}"
    if upper.endswith(".HK"):
        digits = upper.split(".", 1)[0]
        if digits.isdigit() and 1 <= len(digits) <= 5:
            return f"HK{digits.zfill(5)}"
    if re.fullmatch(r"\d{6}\.(?:SH|SS|SZ|BJ)", upper):
        digits, suffix = upper.split(".", 1)
        return f"{'SH' if suffix == 'SS' else suffix}{digits}"
    if upper.startswith(("SH", "SZ", "BJ")) and upper[2:].isdigit():
        return upper
    if re.fullmatch(r"\d{6}", upper):
        if upper.startswith(("6", "5", "9")):
            return f"SH{upper}"
        if upper.startswith("8"):
            return f"BJ{upper}"
        return f"SZ{upper}"
    return None


def _parse_stock_api_rows(rows: Any) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    if not isinstance(rows, list):
        return parsed
    for row in rows:
        if not isinstance(row, dict):
            continue
        close_price = _safe_float(row.get("close"))
        if close_price is None:
            continue
        parsed.append(
            {
                "date": str(row.get("date") or ""),
                "open": _safe_float(row.get("open")),
                "close": close_price,
                "high": _safe_float(row.get("high")),
                "low": _safe_float(row.get("low")),
                "volume": _safe_float(row.get("volume")),
            }
        )
    return parsed


def _try_stock_api_market_data(code: str) -> Optional[Dict[str, Any]]:
    """Domestic fallback via zhangxiangliang/stock-api.

    stock-api auto uses Tencent -> Sina -> Eastmoney. It is intentionally used
    after the direct Tencent path, because our direct Tencent call returns HK
    quote and HK K-line in one request while stock-api may only return quote for
    some HK symbols.
    """

    symbol = _stock_api_symbol(code)
    bridge_path = _stock_api_bridge_path()
    if not symbol or not bridge_path.exists():
        return None

    cache_key = f"stockapi:{symbol}"
    cached = _QUOTE_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _QUOTE_CACHE_SECONDS:
        return cached[1]

    result: Optional[Dict[str, Any]] = None
    try:
        completed = subprocess.run(
            ["node", str(bridge_path), "--code", symbol, "--count", "160", "--period", "day"],
            cwd=str(_repo_root()),
            text=True,
            capture_output=True,
            timeout=14,
            check=True,
        )
        payload = json.loads(completed.stdout)
        stock = payload.get("stock") if isinstance(payload, dict) else None
        if not isinstance(stock, dict):
            return None
        price = _safe_float(stock.get("now"))
        previous_close = _safe_float(stock.get("yesterday"))
        if price is None or price <= 0:
            return None
        change = (price - previous_close) if previous_close else None
        percent = _safe_float(stock.get("percent"))
        history = _parse_stock_api_rows(payload.get("klines"))
        result = {
            "provider": "实时行情",
            "provider_symbol": str(payload.get("providerCode") or symbol),
            "quote": {
                "name": str(stock.get("name") or "").strip() or None,
                "price": price,
                "previous_close": previous_close,
                "change": change,
                "change_percent": percent * 100 if percent is not None else None,
                "high": _safe_float(stock.get("high")),
                "low": _safe_float(stock.get("low")),
                "update_time": _now_iso(),
                "currency": "HKD" if symbol.startswith("HK") else "CNY",
            },
            "history": history,
        }
    except Exception as exc:  # noqa: BLE001 - domestic fallback is best-effort.
        logger.info("[commercial-analysis] stock-api quote unavailable for %s: %s", code, exc)

    _QUOTE_CACHE[cache_key] = (now, result)
    return result


def _requests_get_json_no_proxy(url: str, params: Dict[str, Any], timeout: int = 8) -> Optional[Dict[str, Any]]:
    """GET JSON from public finance endpoints without inheriting flaky local proxies."""

    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(url, params=params, headers=_HTTP_HEADERS, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:  # noqa: BLE001 - public data source must be best-effort.
        logger.info("[commercial-analysis] public JSON fetch unavailable: %s", exc)
        return None


def _requests_get_text_no_proxy(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 9,
    attempts: int = 2,
) -> Optional[str]:
    """GET text from public finance pages without inheriting flaky local proxies."""

    session = requests.Session()
    session.trust_env = False
    request_headers = {**_HTTP_HEADERS, **(headers or {})}
    last_error: Optional[Exception] = None
    for attempt in range(max(1, attempts)):
        try:
            response = session.get(url, headers=request_headers, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001 - public data source must be best-effort.
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.35)
    if last_error:
        logger.info("[commercial-analysis] public text fetch unavailable: %s", last_error)
    return None


def _eastmoney_secid(code: str) -> Optional[str]:
    normalized_code = _normalize_code(code)
    if normalized_code.startswith("HK"):
        digits = normalized_code[2:].zfill(5)
        return f"116.{digits}"
    if normalized_code.endswith(".HK"):
        return f"116.{normalized_code.split('.', 1)[0].zfill(5)}"
    if re.fullmatch(r"\d{6}\.(?:SH|SS)", normalized_code):
        return f"1.{normalized_code[:6]}"
    if re.fullmatch(r"\d{6}\.SZ", normalized_code):
        return f"0.{normalized_code[:6]}"
    if re.fullmatch(r"\d{6}\.BJ", normalized_code):
        return f"0.{normalized_code[:6]}"
    if re.fullmatch(r"\d{6}", normalized_code):
        return f"1.{normalized_code}" if normalized_code.startswith(("6", "5", "9")) else f"0.{normalized_code}"
    return None


def _format_board_change(value: Any) -> str:
    numeric = _safe_float(value)
    if numeric is None:
        return "实时"
    percent = numeric / 100.0
    direction = "涨" if percent > 0 else "跌" if percent < 0 else "平"
    return f"{direction}{abs(percent):.2f}%"


def _sector_relevance_by_rank(index: int) -> str:
    if index <= 1:
        return "高"
    if index <= 3:
        return "中高"
    return "中"


def _board_heat_rank(name: str, change_value: Any) -> int:
    compact = _compact_query(name)
    broad_penalty = 0
    broad_terms = ("港股通", "沪股通", "深股通", "融资融券", "标准普尔", "富时罗素", "msci", "百元股", "大盘股", "上证", "hs300")
    if any(_compact_query(term) in compact for term in broad_terms):
        broad_penalty = 35
    numeric = _safe_float(change_value)
    momentum = numeric if numeric is not None else 0
    return int(momentum) - broad_penalty


def _is_broad_market_board(name: str) -> bool:
    compact = _compact_query(name)
    broad_terms = (
        "港股通",
        "沪股通",
        "深股通",
        "融资融券",
        "标准普尔",
        "富时罗素",
        "msci",
        "百元股",
        "热股",
        "央视50",
        "hs300",
        "上证50",
        "上证180",
        "深证100",
        "深证100r",
        "深成500",
        "创业成份",
        "创业板综",
        "大盘股",
        "中盘股",
        "小盘股",
        "大盘成长",
        "权重股",
        "行业龙头",
        "茅指数",
        "宁组合",
        "周期股",
        "ah股",
        "科技风格",
        "消费风格",
        "先进制造风格",
        "机构重仓",
        "qfii重仓",
        "证金持股",
        "次新股",
    )
    return any(_compact_query(term) in compact for term in broad_terms)


def _fetch_eastmoney_related_sectors(code: str, limit: int = 6) -> List[Dict[str, str]]:
    normalized_code = _normalize_code(code)
    cache_key = f"sectors:{normalized_code}"
    cached = _SECTOR_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _SECTOR_CACHE_SECONDS:
        return cached[1]

    secid = _eastmoney_secid(normalized_code)
    if not secid:
        return []

    payload = _requests_get_json_no_proxy(
        "https://push2.eastmoney.com/api/qt/slist/get",
        {
            "forcect": "1",
            "spt": "3",
            "fields": "f1,f12,f152,f3,f14,f128,f136,f140,f141",
            "pi": "0",
            "pz": "1000",
            "po": "1",
            "fid": "f3",
            "fid0": "f4003",
            "invt": "2",
            "secid": secid,
        },
        timeout=8,
    )
    raw_diff = ((payload or {}).get("data") or {}).get("diff")
    if isinstance(raw_diff, dict):
        rows = list(raw_diff.values())
    elif isinstance(raw_diff, list):
        rows = raw_diff
    else:
        rows = []

    sectors: List[Dict[str, str]] = []
    seen: set[str] = set()
    # Eastmoney returns the board list in stock-association order. Keep that order:
    # industry/theme relevance matters more here than short-term board涨跌排名.
    ordered_rows = [row for row in rows if isinstance(row, dict) and row.get("f14")]
    for row in ordered_rows:
        name = str(row.get("f14") or "").strip()
        if not name or name in seen:
            continue
        if _is_broad_market_board(name):
            continue
        compact = _compact_query(name)
        seen.add(name)
        leader = str(row.get("f128") or "").strip()
        leader_change = _safe_float(row.get("f136"))
        leader_text = ""
        if leader:
            if leader_change is not None:
                leader_text = f"，领涨股{leader}{leader_change / 100.0:+.2f}%"
            else:
                leader_text = f"，领涨股{leader}"
        heat = _format_board_change(row.get("f3"))
        sectors.append(
            {
                "name": name,
                "heat": heat,
                "relevance": _sector_relevance_by_rank(len(sectors)),
                "realtime_change": heat,
                "realtime_board": name,
                "data_source": "实时所属板块",
                "data_status": "latest_public_source",
                "reason": f"实时板块联动显示，板块{heat}{leader_text}。",
            }
        )
        if len(sectors) >= limit:
            break

    _SECTOR_CACHE[cache_key] = (now, sectors)
    return sectors


def _fetch_hk_company_profile_sectors(code: str) -> List[Dict[str, str]]:
    normalized_code = _normalize_code(code)
    if not normalized_code.startswith("HK"):
        return []
    digits = normalized_code[2:].zfill(5)
    sectors: List[Dict[str, str]] = []
    try:
        import akshare as ak  # type: ignore

        df = ak.stock_hk_company_profile_em(symbol=digits)
    except Exception as exc:  # noqa: BLE001 - optional library/source.
        logger.info("[commercial-analysis] HK company profile unavailable for %s: %s", code, exc)
        return sectors
    if getattr(df, "empty", True):
        return sectors
    row = df.iloc[0].to_dict()
    industry = str(row.get("所属行业") or "").strip()
    intro = str(row.get("公司介绍") or "")
    seen = {_compact_query(item["name"]) for item in sectors}
    industry_item = (
        {
            "name": industry,
            "heat": "公司行业",
            "relevance": "中高",
            "data_source": "公司资料",
            "data_status": "latest_public_source",
            "reason": "公开公司资料返回的所属行业。",
        }
        if industry
        else None
    )
    keyword_rules = [
        ("物理AI", ("仿真", "人工智能"), "公司资料同时命中仿真与AI相关关键词。"),
        ("物理AI", ("仿真", "AI"), "公司资料同时命中仿真与AI相关关键词。"),
        ("物理AI", ("仿真", "空间"), "公司资料同时命中仿真与空间相关关键词。"),
        ("物理AI", ("仿真", "智能驾驶"), "公司资料同时命中仿真与智能驾驶相关关键词。"),
        ("空间智能", ("空间", "智能"), "公司资料命中空间智能相关关键词。"),
        ("数字孪生", ("数字孪生",), "公司资料命中数字孪生关键词。"),
        ("智能驾驶仿真", ("智能驾驶", "仿真"), "公司资料命中智能驾驶仿真关键词。"),
        ("合成数据", ("合成数据",), "公司资料命中合成数据关键词。"),
        ("数字地球", ("数字地球",), "公司资料命中数字地球关键词。"),
    ]
    compact_intro = _compact_query(intro)
    for name, keywords, reason in keyword_rules:
        key = _compact_query(name)
        if key not in seen and all(_compact_query(keyword) in compact_intro for keyword in keywords):
            sectors.append(
                {
                    "name": name,
                    "heat": "公司资料",
                    "relevance": "高",
                    "data_source": "公司资料关键词",
                    "data_status": "latest_public_source",
                    "reason": reason,
                }
            )
            seen.add(key)
    if industry_item and _compact_query(industry_item["name"]) not in seen:
        sectors.append(industry_item)
    return sectors[:6]


def _theme_realtime_keywords(theme_name: str) -> List[str]:
    compact = _compact_query(theme_name)
    if "物理ai" in compact or "物理AI" in theme_name:
        return ["人工智能", "AIGC", "机器人", "无人驾驶", "软件服务"]
    if "空间" in compact:
        return ["AIGC", "人工智能", "软件服务", "云计算"]
    if "孪生" in compact or "地球" in compact:
        return ["软件服务", "大数据", "云计算", "人工智能"]
    if "驾驶" in compact or "仿真" in compact or "合成数据" in compact:
        return ["无人驾驶", "机器人", "人工智能", "软件服务"]
    return [theme_name]


def _match_realtime_sector(theme: Dict[str, str], realtime_sectors: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    theme_name = str(theme.get("name") or "")
    candidates = [_compact_query(item) for item in _theme_realtime_keywords(theme_name) if item]
    for sector in realtime_sectors:
        sector_name = str(sector.get("name") or "")
        sector_key = _compact_query(sector_name)
        if not sector_key:
            continue
        if any(candidate and (candidate in sector_key or sector_key in candidate) for candidate in candidates):
            return sector
    return None


def _attach_realtime_sector_reference(
    theme: Dict[str, str],
    realtime_sectors: List[Dict[str, str]],
) -> Dict[str, str]:
    enriched = dict(theme)
    match = _match_realtime_sector(enriched, realtime_sectors)
    if not match:
        return enriched
    board = match.get("realtime_board") or match.get("name") or ""
    change = match.get("realtime_change") or match.get("heat") or ""
    enriched["realtime_board"] = board
    enriched["realtime_change"] = change
    if change:
        enriched["heat"] = enriched.get("heat") or change
    reason = str(enriched.get("reason") or "")
    if board and change and board != enriched.get("name"):
        reason = f"{reason} 实时参考板块：{board}{change}。"
    enriched["reason"] = reason.strip()
    return enriched


def _fetch_financial_snapshot(code: str) -> Optional[Dict[str, Any]]:
    normalized_code = _normalize_code(code)
    cache_key = f"financial:{normalized_code}"
    cached = _FINANCIAL_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _FINANCIAL_CACHE_SECONDS:
        return cached[1]

    def _latest_financial_period(columns: List[Any]) -> Optional[str]:
        periods = [str(item) for item in columns if re.fullmatch(r"\d{8}", str(item))]
        return sorted(periods, reverse=True)[0] if periods else None

    def _financial_abstract_value(
        df: Any,
        period: str,
        indicators: List[str],
        option_priority: Optional[List[str]] = None,
    ) -> Optional[float]:
        if not period:
            return None
        priorities = option_priority or ["常用指标", "盈利能力", "每股指标", "成长能力", "收益质量", "财务风险"]
        for option in priorities:
            for indicator in indicators:
                matches = df[
                    (df.get("选项").astype(str) == option)
                    & (df.get("指标").astype(str) == indicator)
                ]
                if not getattr(matches, "empty", True):
                    value = _safe_float(matches.iloc[0].get(period))
                    if value is not None:
                        return value
        for indicator in indicators:
            matches = df[df.get("指标").astype(str) == indicator]
            if not getattr(matches, "empty", True):
                value = _safe_float(matches.iloc[0].get(period))
                if value is not None:
                    return value
        return None

    def _a_share_dividend_metrics(ak_module: Any, symbol: str) -> Dict[str, Any]:
        """Fetch recent A-share cash dividend metrics from public dividend history.

        A-share dividend detail usually reports cash dividend as "10派X元".
        Convert it to per-share cash dividend and aggregate recent implemented
        events so dividend score does not collapse when TTM yield fields are
        absent from the financial abstract endpoint.
        """
        metrics: Dict[str, Any] = {}
        try:
            dividend_df = ak_module.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        except Exception as exc:  # noqa: BLE001 - dividend source is best-effort.
            logger.info("[commercial-analysis] dividend history unavailable for %s: %s", symbol, exc)
            return metrics
        if getattr(dividend_df, "empty", True):
            return metrics

        today = datetime.now()
        recent_cash_per_share: List[float] = []
        latest_date: Optional[str] = None
        latest_cash_per_share: Optional[float] = None
        implemented_count = 0
        for _, row in dividend_df.iterrows():
            progress = str(row.get("进度") or row.get("方案进度") or "")
            if progress and "实施" not in progress:
                continue
            raw_cash = _safe_float(row.get("派息") or row.get("现金分红-现金分红比例"))
            if raw_cash is None or raw_cash <= 0:
                continue
            cash_per_share = raw_cash / 10.0
            date_text = str(row.get("公告日期") or row.get("最新公告日期") or row.get("除权除息日") or "")[:10]
            event_date: Optional[datetime] = None
            try:
                event_date = datetime.strptime(date_text, "%Y-%m-%d")
            except Exception:  # noqa: BLE001
                event_date = None
            if latest_date is None:
                latest_date = date_text or None
                latest_cash_per_share = cash_per_share
            if event_date is not None and 0 <= (today - event_date).days <= 370:
                recent_cash_per_share.append(cash_per_share)
                implemented_count += 1

        if recent_cash_per_share:
            metrics["dividend_per_share_ttm"] = round(sum(recent_cash_per_share), 4)
            metrics["dividend_events_ttm"] = implemented_count
        elif latest_cash_per_share is not None:
            metrics["dividend_per_share_ttm"] = round(latest_cash_per_share, 4)
            metrics["dividend_events_ttm"] = 1
        if latest_date:
            metrics["latest_dividend_announcement_date"] = latest_date
        return metrics

    snapshot: Optional[Dict[str, Any]] = None
    if normalized_code.startswith("HK"):
        digits = normalized_code[2:].zfill(5)
        try:
            import akshare as ak  # type: ignore

            df = ak.stock_hk_financial_indicator_em(symbol=digits)
            if not getattr(df, "empty", True):
                row = df.iloc[0].to_dict()
                snapshot = {
                    "source": "港股财务指标",
                    "data_status": "latest_public_source",
                    "eps": _safe_float(row.get("基本每股收益(元)")),
                    "book_value_per_share": _safe_float(row.get("每股净资产(元)")),
                    "dividend_per_share_ttm": _safe_float(row.get("每股股息TTM(港元)")),
                    "dividend_payout_ratio": _safe_float(row.get("派息比率(%)")),
                    "operating_cash_flow_per_share": _safe_float(row.get("每股经营现金流(元)")),
                    "dividend_yield_ttm": _safe_float(row.get("股息率TTM(%)")),
                    "revenue": _safe_float(row.get("营业总收入")),
                    "revenue_growth_qoq": _safe_float(row.get("营业总收入滚动环比增长(%)")),
                    "net_margin": _safe_float(row.get("销售净利率(%)")),
                    "net_profit": _safe_float(row.get("净利润")),
                    "net_profit_growth_qoq": _safe_float(row.get("净利润滚动环比增长(%)")),
                    "roe": _safe_float(row.get("股东权益回报率(%)")),
                    "pe": _safe_float(row.get("市盈率")),
                    "pb": _safe_float(row.get("市净率")),
                    "roa": _safe_float(row.get("总资产回报率(%)")),
                }
        except Exception as exc:  # noqa: BLE001 - financial source is best-effort.
            logger.info("[commercial-analysis] financial snapshot unavailable for %s: %s", code, exc)
    else:
        digits = _a_stock_digits(normalized_code)
        if digits:
            try:
                import akshare as ak  # type: ignore

                df = ak.stock_financial_abstract(symbol=digits)
                if not getattr(df, "empty", True):
                    period = _latest_financial_period(list(df.columns))
                    if period:
                        revenue = _financial_abstract_value(df, period, ["营业总收入"])
                        net_profit = _financial_abstract_value(df, period, ["归母净利润", "净利润"])
                        net_margin = (
                            net_profit / revenue * 100
                            if isinstance(net_profit, (int, float)) and isinstance(revenue, (int, float)) and revenue
                            else _financial_abstract_value(df, period, ["销售净利率"])
                        )
                        snapshot = {
                            "source": "A股财务摘要",
                            "data_status": "latest_public_source",
                            "report_date": period,
                            "revenue": revenue,
                            "revenue_growth_qoq": _financial_abstract_value(df, period, ["营业总收入增长率"], ["成长能力"]),
                            "net_profit": net_profit,
                            "deducted_net_profit": _financial_abstract_value(df, period, ["扣非净利润"]),
                            "net_profit_growth_qoq": _financial_abstract_value(df, period, ["归属母公司净利润增长率"], ["成长能力"]),
                            "eps": _financial_abstract_value(df, period, ["基本每股收益", "摊薄每股收益_最新股数"]),
                            "book_value_per_share": _financial_abstract_value(df, period, ["每股净资产", "每股净资产_最新股数", "摊薄每股净资产_期末股数"]),
                            "operating_cash_flow_per_share": _financial_abstract_value(df, period, ["每股经营现金流", "每股现金流"]),
                            "net_margin": net_margin,
                            "roe": _financial_abstract_value(df, period, ["净资产收益率_平均", "净资产收益率(ROE)"], ["盈利能力", "常用指标"]),
                            "roa": _financial_abstract_value(df, period, ["总资产报酬率(ROA)", "总资产报酬率"], ["常用指标", "盈利能力"]),
                            "debt_ratio": _financial_abstract_value(df, period, ["资产负债率"], ["财务风险", "常用指标"]),
                            "cash_flow_to_revenue": _financial_abstract_value(
                                df,
                                period,
                                ["经营性现金净流量/营业总收入", "经营活动净现金/销售收入"],
                                ["收益质量"],
                            ),
                        }
                        snapshot.update(_a_share_dividend_metrics(ak, digits))
            except Exception as exc:  # noqa: BLE001 - financial source is best-effort.
                logger.info("[commercial-analysis] A-share financial snapshot unavailable for %s: %s", code, exc)

    _FINANCIAL_CACHE[cache_key] = (now, snapshot)
    return snapshot


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html_unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _short_text(value: Any, limit: int = 96) -> str:
    text = _strip_html(str(value or ""))
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，。、；; ") + "…"


def _clause_text(value: Any, limit: int = 96) -> str:
    return _short_text(value, limit).rstrip("，。、；; ")


def _complete_clause_text(value: Any, limit: int = 150) -> str:
    """Keep explanatory text concise without ending with an ellipsis."""

    text = _strip_html(str(value or "")).replace("…", "").strip()
    if len(text) <= limit:
        return text.rstrip("，,；;:：、 ")
    cut_candidates = [
        text.rfind(mark, 0, limit)
        for mark in ("。", "；", ";", "，", ",")
    ]
    cut = max(cut_candidates)
    if cut >= max(34, int(limit * 0.52)):
        return text[:cut].rstrip("，,；;:：、 ")
    return text[:limit].rstrip("，,；;:：、 ")


def _profile_phrase(value: Any, limit: int = 120) -> str:
    """Return a phrase that can be safely embedded before commas."""

    return _complete_clause_text(value, limit).rstrip("，,；;:：、。.!！?？ ")


def _sentence(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.endswith(("。", "！", "？")):
        return text
    return f"{text}。"


def _split_profile_terms(value: Any, limit: int = 5) -> List[str]:
    text = _strip_html(str(value or ""))
    if not text:
        return []
    parts = [
        item.strip()
        for item in re.split(r"[、,，;；/]+", text)
        if item.strip() and item.strip() not in {"其他", "业务"}
    ]
    deduped: List[str] = []
    seen: set[str] = set()
    for item in parts:
        item = _short_text(item, 18)
        key = _compact_query(item)
        if not key or key in seen:
            continue
        deduped.append(item)
        seen.add(key)
        if len(deduped) >= limit:
            break
    return deduped


def _verified_company_profile(code: str, stock_name: str = "") -> Optional[Dict[str, Any]]:
    normalized_code = _normalize_code(code)
    keys = {normalized_code}
    if normalized_code.startswith("HK"):
        digits = normalized_code[2:].zfill(5)
        keys.update({f"{digits}.HK", f"HK{int(digits)}" if digits.isdigit() else normalized_code})
    if normalized_code.endswith(".HK"):
        digits = normalized_code.split(".", 1)[0].zfill(5)
        keys.update({f"HK{int(digits)}" if digits.isdigit() else normalized_code, f"{digits}.HK"})

    compact_name = _compact_query(stock_name)
    if compact_name in {"五一视界", "51world"}:
        keys.add("HK6651")

    for key in keys:
        profile = _VERIFIED_COMPANY_PROFILES.get(key)
        if not profile:
            continue
        alias_key = profile.get("alias_of")
        if alias_key:
            profile = _VERIFIED_COMPANY_PROFILES.get(str(alias_key), profile)
        copied = {
            copied_key: (list(copied_value) if isinstance(copied_value, list) else copied_value)
            for copied_key, copied_value in profile.items()
            if copied_key != "alias_of"
        }
        return copied
    return None


def _merge_verified_company_profile(profile: Dict[str, Any], verified: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not verified:
        return profile

    raw_profile = {
        key: value
        for key, value in profile.items()
        if value and key not in {"status", "source_name", "source_url"}
    }
    profile.update(
        {
            "name": verified.get("name") or profile.get("name"),
            "legal_name": verified.get("legal_name") or profile.get("legal_name", ""),
            "industry": verified.get("industry") or profile.get("industry", ""),
            "business": verified.get("business") or profile.get("business", ""),
            "products": verified.get("products") or profile.get("products", []),
            "intro": verified.get("verified_position") or verified.get("business") or profile.get("intro", ""),
            "verified_position": verified.get("verified_position", ""),
            "business_model": verified.get("business_model", ""),
            "industry_logic": verified.get("industry_logic", ""),
            "watch_points": verified.get("watch_points", ""),
            "risk_boundary": verified.get("risk_boundary", ""),
            "source_name": verified.get("source_name", ""),
            "source_url": verified.get("source_url", ""),
            "status": verified.get("status", "verified_official_profile"),
            "identity_locked": bool(verified.get("identity_locked")),
            "protected_terms": verified.get("protected_terms") or [],
            "raw_public_profile": raw_profile,
        }
    )
    return profile


def _fetch_company_profile(code: str, market: str, stock_name: str) -> Dict[str, Any]:
    """Best-effort company profile used to explain conclusions to non-professional users."""

    normalized_code = _normalize_code(code)
    cache_key = f"profile:{normalized_code}:{_compact_query(stock_name)}"
    cached = _COMPANY_PROFILE_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _COMPANY_PROFILE_CACHE_SECONDS:
        return cached[1]

    profile: Dict[str, Any] = {
        "name": stock_name,
        "industry": "",
        "business": "",
        "products": [],
        "intro": "",
        "source_name": "",
        "source_url": "",
        "verified_position": "",
        "business_model": "",
        "industry_logic": "",
        "watch_points": "",
        "risk_boundary": "",
        "identity_locked": False,
        "protected_terms": [],
        "status": "pending",
    }

    try:
        import akshare as ak  # type: ignore

        if market == "H股":
            digits = normalized_code[2:].zfill(5) if normalized_code.startswith("HK") else normalized_code.split(".", 1)[0].zfill(5)
            df = ak.stock_hk_company_profile_em(symbol=digits)
            if not getattr(df, "empty", True):
                row = df.iloc[0].to_dict()
                profile.update(
                    {
                        "industry": _short_text(row.get("所属行业"), 28),
                        "business": _complete_clause_text(row.get("主营业务") or row.get("业务") or "", 150),
                        "intro": _complete_clause_text(row.get("公司介绍") or row.get("公司简介") or "", 260),
                        "status": "latest_public_profile",
                    }
                )
        else:
            digits = _a_stock_digits(normalized_code)
            if digits:
                df = ak.stock_zyjs_ths(symbol=digits)
                if not getattr(df, "empty", True):
                    row = df.iloc[0].to_dict()
                    profile.update(
                        {
                            "business": _complete_clause_text(row.get("主营业务"), 150),
                            "products": _split_profile_terms(row.get("产品名称") or row.get("产品类型"), limit=6),
                            "intro": _complete_clause_text(row.get("经营范围"), 260),
                            "status": "latest_public_profile",
                        }
                    )
    except Exception as exc:  # noqa: BLE001 - profile is explanatory, analysis can continue without it.
        logger.info("[commercial-analysis] company profile unavailable for %s: %s", code, exc)

    profile = _merge_verified_company_profile(profile, _verified_company_profile(normalized_code, stock_name))

    _COMPANY_PROFILE_CACHE[cache_key] = (now, profile)
    return profile


def _profile_theme_hint(profile: Dict[str, Any], sectors: List[Dict[str, str]]) -> str:
    terms = " ".join(
        [
            str(profile.get("verified_position") or ""),
            str(profile.get("business") or ""),
            str(profile.get("business_model") or ""),
            str(profile.get("industry_logic") or ""),
            " ".join(str(item) for item in profile.get("products") or []),
            str(profile.get("intro") or ""),
            " ".join(str(item.get("name") or "") for item in sectors[:4]),
        ]
    )
    compact = _compact_query(terms)
    hints: List[str] = []
    if any(keyword in compact for keyword in ("光刻胶", "cmp", "高纯溶剂", "电子专用材料", "半导体")):
        hints.append("半导体材料国产替代")
    if any(keyword in compact for keyword in ("橡胶", "助剂", "树脂")):
        hints.append("橡胶助剂景气")
    if any(keyword in compact for keyword in ("生物可降解", "可降解")):
        hints.append("可降解材料需求")
    if any(keyword in compact for keyword in ("仿真", "数字孪生", "空间智能", "物理ai")):
        hints.append("物理AI与数字孪生落地")
    if any(keyword in compact for keyword in ("机器人", "无人驾驶", "智能驾驶")):
        hints.append("智能制造场景扩张")
    if any(keyword in compact for keyword in ("影视", "电影", "娱乐", "传媒", "品牌授权", "实景娱乐", "文旅", "院线")):
        hints.append("内容IP、影视娱乐和文旅消费")
    return "、".join(hints[:2])


def _company_basic_reason(
    stock: Dict[str, Any],
    profile: Dict[str, Any],
    sectors: List[Dict[str, str]],
) -> str:
    name = str(stock.get("name") or profile.get("name") or stock.get("code") or "该公司")
    verified_position = _profile_phrase(profile.get("verified_position"), 120)
    if verified_position:
        business_model = _complete_clause_text(profile.get("business_model") or profile.get("business"), 150)
        industry_logic = _complete_clause_text(profile.get("industry_logic"), 150)
        watch_points = _complete_clause_text(profile.get("watch_points"), 120)
        risk_boundary = _complete_clause_text(profile.get("risk_boundary"), 120)
        parts = [f"公司定位：{name}，{verified_position}。"]
        if business_model:
            parts.append(f"业务主线：{business_model}。")
        if industry_logic:
            parts.append(f"行业逻辑：{industry_logic}。")
        if watch_points:
            parts.append(f"观察重点：{watch_points}。")
        if risk_boundary:
            parts.append(f"风险边界：{risk_boundary}。")
        return "".join(parts)

    business = _profile_phrase(profile.get("business"), 120)
    products = profile.get("products") or []
    intro = _complete_clause_text(profile.get("intro"), 140)
    sector = next((item.get("name") for item in sectors if item.get("name") and item.get("name") != "待读取"), "")
    theme_hint = _profile_theme_hint(profile, sectors)
    terms = _compact_query(
        " ".join(
            [
                business,
                intro,
                " ".join(str(item) for item in products),
                " ".join(str(item.get("name") or "") for item in sectors[:4]),
            ]
        )
    )
    product_text = "、".join(_profile_phrase(item, 18) for item in products[:4] if str(item).strip())
    st_risk_note = "公司带ST标签，基本面验证权重要高于题材弹性。" if "st" in _compact_query(name) else ""

    if "物理ai" in terms or "数字孪生" in terms or "仿真" in terms:
        platforms = [keyword for keyword in ("51Aes", "51Sim", "51Earth") if keyword.lower() in terms]
        if "五一视界" in name:
            platforms = ["51Aes", "51Sim"]
        platform_text = f"{'、'.join(platforms)}等数字孪生与仿真平台" if platforms else "数字孪生与仿真平台"
        return (
            f"公司定位：{name}可理解为物理AI/数字孪生链条公司。"
            f"业务主线：核心看{platform_text}在企业仿真、空间数据和AI训练场景中的商业化落地。"
            "行业趋势：物理AI、工业仿真和空间智能需求在升温，但兑现节奏比概念热度更重要。"
            "观察重点：收入增长、客户续费、项目交付和毛利率。"
        )

    if "光刻胶" in terms or "cmp" in terms or "半导体" in terms or "电子专用材料" in terms:
        product_line = f"，核心产品包括{product_text}" if product_text else ""
        return (
            f"公司定位：{name}属于电子材料/半导体材料链条公司{product_line}。"
            "业务主线：看高端材料验证导入、客户扩产和国产替代份额提升。"
            "行业趋势：半导体材料长期空间较大，但短期受下游资本开支和认证周期影响。"
            "观察重点：订单放量、产品认证、毛利率和现金流。"
        )

    if "橡胶" in terms or "助剂" in terms or "树脂" in terms:
        product_line = f"，核心产品包括{product_text}" if product_text else ""
        return (
            f"公司定位：{name}属于化工材料/橡胶助剂链条公司{product_line}。"
            "业务主线：看轮胎与汽车产业链需求、产品价格和成本传导能力。"
            "行业趋势：化工材料景气度有周期性，涨价或补库会带来弹性。"
            "观察重点：销量、价差、库存周期和环保产能约束。"
        )

    if any(keyword in terms for keyword in ("影视", "电影", "娱乐", "传媒", "品牌授权", "实景娱乐", "文旅", "院线")):
        product_line = f"的核心业务包括{product_text}" if product_text else (f"主营{business}" if business else "主要围绕内容与娱乐消费业务展开")
        risk_prefix = f"{st_risk_note}" if st_risk_note else ""
        return (
            f"公司定位：{name}{product_line}，属于内容IP、影视娱乐与文旅消费链条。"
            "行业逻辑：这类公司核心看内容储备、票房或剧集表现、IP授权变现、线下项目运营和回款，不能只看题材热度。"
            f"{risk_prefix}观察重点：收入恢复、毛利率、现金流、债务压力和最新公告是否同步改善。"
        )

    if business and products:
        return (
            f"公司定位：{name}主营{business}，核心产品包括{product_text}。"
            f"业务主线：先看{sector or '核心业务'}景气度、收入兑现和现金流变化。"
            f"行业趋势：{theme_hint or '行业景气、政策和竞争格局'}会影响估值弹性。"
            "观察重点：收入增速、利润率、现金流和公告验证。"
        )
    if business:
        return (
            f"公司定位：{name}主营{business}。"
            f"业务主线：当前主要跟踪{sector or '主营业务'}景气度、收入兑现和现金流变化。"
            "行业趋势：如果板块、业绩和资金共振，股价弹性会更强；若只有概念上涨，持续性要打折。"
            "观察重点：收入兑现、利润率和最新公告。"
        )
    if intro:
        return (
            f"公司定位：{name}公开资料显示，{intro}。"
            f"业务主线：可先按{sector or '主营业务'}链条理解。"
            "行业趋势：需要用最新公告、板块联动和量价结构继续验证。"
            "观察重点：商业化进度、订单和现金流。"
        )
    if sector:
        return (
            f"公司定位：{name}当前主要按{sector}方向跟踪。"
            "业务主线：先用实时行情、板块联动和最新资讯交叉验证。"
            "行业趋势：板块强弱决定短线资金弹性。"
            "观察重点：是否进入更有赔率的位置。"
        )
    return f"公司定位：{name}资料仍在读取。业务主线：先按实时行情、量化结构和最新资讯交叉验证。观察重点：价格是否进入更有赔率的位置。"


def _normalize_news_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now().date().isoformat()
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if not match:
        return datetime.now().date().isoformat()
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _news_tone(title: str) -> str:
    compact = _compact_query(title)
    risk_hits = sum(1 for keyword in _RISK_NEWS_KEYWORDS if _compact_query(keyword) in compact)
    positive_hits = sum(1 for keyword in _POSITIVE_NEWS_KEYWORDS if _compact_query(keyword) in compact)
    if risk_hits > positive_hits:
        return "risk"
    if positive_hits > 0:
        return "positive"
    return "neutral"


def _news_matches_stock(title: str, href: str, code: str, stock_name: str) -> bool:
    compact_title = _compact_query(title)
    compact_href = _compact_query(href)
    compact_name = _compact_query(stock_name)
    normalized_code = _normalize_code(code)
    code_terms = {
        _compact_query(code),
        _compact_query(normalized_code),
    }
    if normalized_code.startswith("HK"):
        digits = normalized_code[2:].lstrip("0") or normalized_code[2:]
        code_terms.update({_compact_query(digits), _compact_query(digits.zfill(5)), _compact_query(f"{digits}.hk")})
    elif re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", normalized_code):
        digits = normalized_code[:6]
        code_terms.add(_compact_query(digits))

    if compact_name and compact_name in compact_title:
        return True
    return any(term and (term in compact_title or term in compact_href) for term in code_terms)


def _news_relevance_text(title: str, content: str, code: str, stock_name: str) -> bool:
    compact_title = _compact_query(title)
    compact_content = _compact_query(content)
    compact_name = _compact_query(stock_name)
    normalized_code = _normalize_code(code)
    terms = {_compact_query(code), _compact_query(normalized_code)}
    if normalized_code.startswith("HK"):
        digits = normalized_code[2:].lstrip("0") or normalized_code[2:]
        terms.update({_compact_query(digits), _compact_query(digits.zfill(5)), _compact_query(f"{digits}.hk")})
    elif re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", normalized_code):
        digits = normalized_code[:6]
        terms.update({_compact_query(digits), _compact_query(f"{digits}.sh"), _compact_query(f"{digits}.sz")})
    if compact_name and (compact_name in compact_title or compact_name in compact_content):
        return True
    return any(term and (term in compact_title or term in compact_content) for term in terms)


def _dedupe_news(items: List[Dict[str, str]], limit: int = _NEWS_RESULT_LIMIT) -> List[Dict[str, str]]:
    seen: set[str] = set()
    deduped: List[Dict[str, str]] = []
    for item in sorted(items, key=lambda value: value.get("date", ""), reverse=True):
        title_key = _compact_query(item.get("title", ""))
        if not title_key or title_key in seen:
            continue
        seen.add(title_key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def _fetch_aastocks_news(code: str, stock_name: str, limit: int = _NEWS_RESULT_LIMIT) -> List[Dict[str, str]]:
    normalized_code = _normalize_code(code)
    if normalized_code.startswith("HK"):
        digits = normalized_code[2:].zfill(5)
    elif normalized_code.endswith(".HK"):
        digits = normalized_code.split(".", 1)[0].zfill(5)
    else:
        return []

    url = f"{_AASTOCKS_BASE}/tc/stocks/analysis/stock-aafn/{digits}/0/hk-stock-news/1"
    response_text = _requests_get_text_no_proxy(
        url,
        headers={
            "Accept-Language": "zh-CN,zh;q=0.9,zh-HK;q=0.8,en;q=0.7",
            "Referer": _AASTOCKS_BASE,
        },
        timeout=9,
        attempts=2,
    )
    if not response_text:
        logger.info("[commercial-analysis] AASTOCKS news unavailable for %s", code)
        return []

    items: List[Dict[str, str]] = []
    blocks = re.split(r'(?=<div\s+ref="[^"]+">)', response_text)
    for block in blocks:
        if f"stock-aafn-con/{digits}" not in block:
            continue
        match = re.search(
            r'<a[^>]+title="([^"]+)"[^>]+href="([^"]*stock-aafn-con/'
            + re.escape(digits)
            + r'/[^"]+)"',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            continue
        title = _strip_html(match.group(1))
        href = html_unescape(match.group(2))
        if not title or not _news_matches_stock(title, href, normalized_code, stock_name):
            continue
        source_match = re.search(r"<span class='vw1200'[^>]*>(.*?)</span>", block, flags=re.IGNORECASE | re.DOTALL)
        date_match = re.search(r"dt:'([^']+)'", block, flags=re.IGNORECASE)
        items.append(
            {
                "title": title,
                "source": "市场资讯",
                "date": _normalize_news_date(date_match.group(1) if date_match else None),
                "url": urljoin(_AASTOCKS_BASE, href),
                "tone": _news_tone(title),
            }
        )

    return _dedupe_news(items, limit)


def _a_stock_digits(code: str) -> Optional[str]:
    normalized_code = _normalize_code(code)
    if re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", normalized_code):
        return normalized_code[:6]
    if re.fullmatch(r"\d{6}", normalized_code):
        return normalized_code
    return None


def _fetch_eastmoney_announcements(code: str, stock_name: str, limit: int = _NEWS_RESULT_LIMIT) -> List[Dict[str, str]]:
    digits = _a_stock_digits(code)
    if not digits:
        return []

    try:
        response = requests.get(
            _EASTMONEY_ANN_ENDPOINT,
            params={
                "sr": "-1",
                "page_size": str(limit),
                "page_index": "1",
                "ann_type": "A",
                "client_source": "web",
                "stock_list": digits,
            },
            headers={**_HTTP_HEADERS, "Referer": "https://data.eastmoney.com/"},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - public news source is best-effort.
        logger.info("[commercial-analysis] Eastmoney announcements unavailable for %s: %s", code, exc)
        return []

    rows = ((payload.get("data") or {}).get("list") or []) if isinstance(payload, dict) else []
    items: List[Dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _strip_html(str(row.get("title") or row.get("title_ch") or ""))
        if not title:
            continue
        columns = row.get("columns") if isinstance(row.get("columns"), list) else []
        column_name = _strip_html(str((columns[0] or {}).get("column_name"))) if columns else "公告"
        art_code = str(row.get("art_code") or "").strip()
        href = f"https://data.eastmoney.com/notices/detail/{digits}/{art_code}.html" if art_code else "https://data.eastmoney.com/notices/"
        items.append(
            {
                "title": title,
                "source": f"公司公告 · {column_name}",
                "date": _normalize_news_date(row.get("notice_date") or row.get("display_time")),
                "url": href,
                "tone": _news_tone(title),
            }
        )

    return _dedupe_news(items, limit)


def _fetch_akshare_a_stock_news(code: str, stock_name: str, limit: int = _NEWS_RESULT_LIMIT) -> List[Dict[str, str]]:
    digits = _a_stock_digits(code)
    if not digits:
        return []
    try:
        import akshare as ak  # type: ignore

        df = ak.stock_news_em(symbol=digits)
    except Exception as exc:  # noqa: BLE001 - optional library/source.
        logger.info("[commercial-analysis] AkShare stock news unavailable for %s: %s", code, exc)
        return []
    if getattr(df, "empty", True):
        return []

    items: List[Dict[str, str]] = []
    for row in df.head(limit * 4).to_dict("records"):
        title = _strip_html(str(row.get("新闻标题") or ""))
        if not title:
            continue
        content = _strip_html(str(row.get("新闻内容") or ""))
        if not _news_relevance_text(title, content, code, stock_name):
            continue
        display_title = title
        if stock_name and _compact_query(stock_name) not in _compact_query(title):
            display_title = f"{stock_name}相关：{title}"
        date = _normalize_news_date(row.get("发布时间"))
        url = str(row.get("新闻链接") or "https://finance.eastmoney.com/").strip()
        items.append(
            {
                "title": display_title,
                "source": "市场资讯",
                "date": date,
                "url": url,
                "tone": _news_tone(f"{title} {content}"),
            }
        )
    return _dedupe_news(items, limit)


def _fetch_latest_news(code: str, stock_name: str, market: str, limit: int = _NEWS_RESULT_LIMIT) -> List[Dict[str, str]]:
    cache_key = f"{_normalize_code(code)}:{_compact_query(stock_name)}:{limit}"
    cached = _NEWS_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _NEWS_CACHE_SECONDS:
        return cached[1]

    if market == "H股":
        items = _fetch_aastocks_news(code, stock_name, limit=limit)
    else:
        items = _dedupe_news(
            [
                *_fetch_akshare_a_stock_news(code, stock_name, limit=limit),
                *_fetch_eastmoney_announcements(code, stock_name, limit=limit),
            ],
            limit,
        )

    if items:
        _NEWS_CACHE[cache_key] = (now, items)
    return items


def _fetch_related_sectors(code: str, market: str) -> List[Dict[str, str]]:
    realtime_sectors = _fetch_eastmoney_related_sectors(code)
    if market != "H股":
        return realtime_sectors

    profile_sectors = _fetch_hk_company_profile_sectors(code)
    if not realtime_sectors:
        return profile_sectors

    profile_with_realtime = [
        _attach_realtime_sector_reference(item, realtime_sectors)
        for item in profile_sectors
    ]
    combined: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in profile_with_realtime[:3]:
        key = _compact_query(item.get("name", ""))
        if key and key not in seen:
            combined.append(item)
            seen.add(key)
    for item in realtime_sectors:
        key = _compact_query(item.get("name", ""))
        if key and key not in seen:
            combined.append(item)
            seen.add(key)
        if len(combined) >= 6:
            break
    for item in profile_with_realtime[3:]:
        key = _compact_query(item.get("name", ""))
        if key and key not in seen:
            combined.append(item)
            seen.add(key)
        if len(combined) >= 6:
            break
    return combined[:6]


def _marker_percent(current_price: float, low: float, high: float) -> float:
    if high <= low:
        return 50.0
    fair_start = 28.0
    fair_width = 44.0
    fair_end = fair_start + fair_width
    raw = (current_price - low) / (high - low)
    if raw < 0:
        return max(8.0, fair_start + raw * fair_start)
    if raw > 1:
        return min(92.0, fair_end + (raw - 1) * (100.0 - fair_end))
    return fair_start + raw * fair_width


def _price_position(current_price: float, low: float, high: float) -> str:
    if current_price < low:
        return "低于合理区间下沿"
    if current_price > high:
        return "高于合理区间上沿"
    span = high - low
    if span <= 0:
        return "位于合理区间"
    ratio = (current_price - low) / span
    if ratio < 0.28:
        return "处于合理区间下沿"
    if ratio > 0.72:
        return "处于合理区间上沿"
    return "处于合理区间中部"


def _history_values(history: List[Dict[str, Any]], field: str) -> List[float]:
    values: List[float] = []
    for row in history:
        value = row.get(field)
        if isinstance(value, (int, float)) and value > 0:
            values.append(float(value))
    return values


def _return_percent(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if not current or not base or base <= 0:
        return None
    return (current / base - 1.0) * 100.0


def _annualized_volatility(closes: List[float]) -> Optional[float]:
    if len(closes) < 22:
        return None
    returns = [
        math.log(closes[index] / closes[index - 1])
        for index in range(1, len(closes))
        if closes[index] > 0 and closes[index - 1] > 0
    ]
    recent_returns = returns[-60:]
    if len(recent_returns) < 8:
        return None
    return statistics.stdev(recent_returns) * math.sqrt(252) * 100


def _average_true_range_percent(history: List[Dict[str, Any]], window: int = 14) -> Optional[float]:
    """Estimate recent daily price noise as ATR/current close percent."""

    if len(history) < 3:
        return None
    true_ranges: List[float] = []
    previous_close: Optional[float] = None
    for row in history:
        high = _safe_float(row.get("high"))
        low = _safe_float(row.get("low"))
        close = _safe_float(row.get("close"))
        if high is None or low is None or close is None or high <= 0 or low <= 0:
            previous_close = close
            continue
        ranges = [high - low]
        if previous_close and previous_close > 0:
            ranges.extend([abs(high - previous_close), abs(low - previous_close)])
        true_ranges.append(max(ranges))
        previous_close = close
    if not true_ranges or previous_close is None or previous_close <= 0:
        return None
    atr = _mean(true_ranges[-window:])
    if atr is None:
        return None
    return atr / previous_close * 100.0


def _range_position(current_price: float, lows: List[float], highs: List[float]) -> Optional[float]:
    if not lows or not highs:
        return None
    low = min(lows[-120:])
    high = max(highs[-120:])
    if high <= low:
        return None
    return (current_price - low) / (high - low) * 100


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp_score(value: float) -> float:
    return round(_clamp(value, 0.1, 9.8), 1)


def _a_share_em_symbol(code: str) -> Optional[str]:
    normalized_code = _normalize_code(code)
    digits = _a_stock_digits(normalized_code)
    if not digits:
        return None
    if normalized_code.endswith(".SH"):
        return f"SH{digits}"
    if normalized_code.endswith(".SZ"):
        return f"SZ{digits}"
    if normalized_code.endswith(".BJ"):
        return f"BJ{digits}"
    return digits


def _latest_positive_series_value(df: Any, value_column: str = "value") -> tuple[Optional[float], List[float]]:
    if getattr(df, "empty", True) or value_column not in getattr(df, "columns", []):
        return None, []
    values: List[float] = []
    for raw_value in df[value_column].tolist():
        value = _safe_float(raw_value)
        if value is not None and value > 0:
            values.append(float(value))
    return (values[-1] if values else None), values


def _valuation_percentile(current: Optional[float], values: List[float]) -> Optional[float]:
    if current is None or current <= 0 or len(values) < 60:
        return None
    clean_values = sorted(value for value in values if value > 0)
    if not clean_values:
        return None
    below_or_equal = sum(1 for value in clean_values if value <= current)
    return _clamp(below_or_equal / len(clean_values), 0.0, 1.0)


def _score_from_low_percentile(percentile: Optional[float]) -> Optional[float]:
    if percentile is None:
        return None
    return _clamp(9.8 - percentile * 8.8, 1.0, 9.8)


def _score_relative_discount(current: Optional[float], peer: Optional[float]) -> Optional[float]:
    if current is None or peer is None or current <= 0 or peer <= 0:
        return None
    ratio = current / peer
    if ratio <= 0.55:
        return 9.0
    if ratio <= 0.8:
        return 7.2
    if ratio <= 1.1:
        return 5.8
    if ratio <= 1.6:
        return 4.2
    if ratio <= 2.3:
        return 2.8
    return 1.6


def _score_from_peg(peg: Optional[float]) -> Optional[float]:
    if peg is None or peg <= 0:
        return None
    if peg <= 0.6:
        return 9.0
    if peg <= 1.0:
        return 8.0
    if peg <= 1.5:
        return 6.4
    if peg <= 2.2:
        return 4.7
    if peg <= 3.5:
        return 3.2
    return 1.8


def _score_from_absolute_valuation(pe: Optional[float], pb: Optional[float], market: str) -> Optional[float]:
    components: List[float] = []
    if pe is not None and pe > 0:
        if pe <= 8:
            components.append(9.0)
        elif pe <= 15:
            components.append(7.2)
        elif pe <= 25:
            components.append(5.5)
        elif pe <= 40:
            components.append(3.8)
        else:
            components.append(2.2)
    if pb is not None and pb > 0:
        if pb <= 0.8:
            components.append(9.0)
        elif pb <= 1.2:
            components.append(7.8)
        elif pb <= 2.2:
            components.append(6.2)
        elif pb <= 4.5:
            components.append(4.4)
        elif pb <= 8:
            components.append(3.1)
        else:
            components.append(1.8)
    if not components:
        return None
    score = _mean(components)
    if market == "H股":
        score = min((score or 5.0) + 0.35, 9.8)
    return score


def _fetch_valuation_ratio_context(code: str) -> Optional[Dict[str, Any]]:
    normalized_code = _normalize_code(code)
    cache_key = f"valuation-ratio:{normalized_code}"
    cached = _VALUATION_RATIO_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _VALUATION_RATIO_CACHE_SECONDS:
        return cached[1]

    context: Optional[Dict[str, Any]] = None
    try:
        import akshare as ak  # type: ignore

        market = "H股" if normalized_code.startswith("HK") else "A股"
        if market == "H股":
            symbol = normalized_code[2:].zfill(5)
            period = "近三年"
            pe_df = ak.stock_hk_valuation_baidu(symbol=symbol, indicator="市盈率(TTM)", period=period)
            pb_df = ak.stock_hk_valuation_baidu(symbol=symbol, indicator="市净率", period=period)
            current_pe, pe_values = _latest_positive_series_value(pe_df)
            current_pb, pb_values = _latest_positive_series_value(pb_df)
            pe_percentile = _valuation_percentile(current_pe, pe_values)
            pb_percentile = _valuation_percentile(current_pb, pb_values)
            relative_score = _score_from_absolute_valuation(current_pe, current_pb, market)
            try:
                comparison_df = ak.stock_hk_valuation_comparison_em(symbol=symbol)
                if not getattr(comparison_df, "empty", True):
                    row = comparison_df.iloc[0]
                    current_pe = current_pe or _safe_float(row.get("市盈率-TTM"))
                    current_pb = current_pb or _safe_float(row.get("市净率-MRQ"))
                    relative_score = relative_score or _score_from_absolute_valuation(current_pe, current_pb, market)
            except Exception as exc:  # noqa: BLE001
                logger.info("[commercial-analysis] HK valuation comparison unavailable for %s: %s", code, exc)
            context = {
                "market": market,
                "period": period,
                "current_pe": current_pe,
                "current_pb": current_pb,
                "pe_percentile": pe_percentile,
                "pb_percentile": pb_percentile,
                "relative_score": relative_score,
                "relative_basis": "港股同业明细不足时，使用当前PE/PB与港股流动性折价代理",
                "peg": None,
                "peg_score": None,
                "history_points": min(len(pe_values), len(pb_values)),
                "source": "valuation_baidu_hk",
            }
        else:
            symbol = _a_stock_digits(normalized_code)
            em_symbol = _a_share_em_symbol(normalized_code)
            if not symbol:
                return None
            period = "近五年"
            pe_df = ak.stock_zh_valuation_baidu(symbol=symbol, indicator="市盈率(TTM)", period=period)
            pb_df = ak.stock_zh_valuation_baidu(symbol=symbol, indicator="市净率", period=period)
            current_pe, pe_values = _latest_positive_series_value(pe_df)
            current_pb, pb_values = _latest_positive_series_value(pb_df)
            pe_percentile = _valuation_percentile(current_pe, pe_values)
            pb_percentile = _valuation_percentile(current_pb, pb_values)
            relative_score = None
            peg = None
            peg_score = None
            relative_basis = "同业估值暂未读取"
            if em_symbol:
                try:
                    comparison_df = ak.stock_zh_valuation_comparison_em(symbol=em_symbol)
                    if not getattr(comparison_df, "empty", True):
                        target = comparison_df.iloc[0]
                        median_rows = comparison_df[
                            comparison_df.get("简称").astype(str).str.contains("行业中值", na=False)
                        ]
                        peer = median_rows.iloc[0] if not getattr(median_rows, "empty", True) else None
                        current_pe = current_pe or _safe_float(target.get("市盈率-TTM"))
                        current_pb = current_pb or _safe_float(target.get("市净率-MRQ"))
                        peg = _safe_float(target.get("PEG"))
                        components: List[float] = []
                        if peer is not None:
                            components.extend(
                                item
                                for item in [
                                    _score_relative_discount(current_pe, _safe_float(peer.get("市盈率-TTM"))),
                                    _score_relative_discount(current_pb, _safe_float(peer.get("市净率-MRQ"))),
                                ]
                                if item is not None
                            )
                        peg_score = _score_from_peg(peg)
                        if peg_score is not None:
                            components.append(peg_score)
                        relative_score = _mean(components) if components else None
                        relative_basis = "同行估值比较与PEG"
                except Exception as exc:  # noqa: BLE001
                    logger.info("[commercial-analysis] A-share valuation comparison unavailable for %s: %s", code, exc)
            if relative_score is None:
                relative_score = _score_from_absolute_valuation(current_pe, current_pb, market)
                relative_basis = "同业比较缺失，使用当前PE/PB绝对估值代理"
            context = {
                "market": market,
                "period": period,
                "current_pe": current_pe,
                "current_pb": current_pb,
                "pe_percentile": pe_percentile,
                "pb_percentile": pb_percentile,
                "relative_score": relative_score,
                "relative_basis": relative_basis,
                "peg": peg,
                "peg_score": peg_score,
                "history_points": min(len(pe_values), len(pb_values)),
                "source": "valuation_baidu_a_share",
            }
    except Exception as exc:  # noqa: BLE001 - valuation context is best-effort.
        logger.info("[commercial-analysis] valuation ratio context unavailable for %s: %s", code, exc)
        context = None

    _VALUATION_RATIO_CACHE[cache_key] = (now, context)
    return context


def _valuation_cost_effectiveness(
    code: str,
    current_price: float,
    valuation: Dict[str, Any],
    financials: Optional[Dict[str, Any]],
    *,
    is_st_stock: bool = False,
    distress_flags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    context = _fetch_valuation_ratio_context(code) if code else None
    historical_components: List[float] = []
    if context:
        historical_components.extend(
            item
            for item in [
                _score_from_low_percentile(context.get("pe_percentile")),
                _score_from_low_percentile(context.get("pb_percentile")),
            ]
            if item is not None
        )
    historical_score = _mean(historical_components)

    relative_score = _safe_float(context.get("relative_score")) if context else None

    low = _safe_float(valuation.get("low"))
    high = _safe_float(valuation.get("high"))
    dcf_proxy_score = None
    if low is not None and high is not None and high > low:
        ratio = (current_price - low) / (high - low)
        if ratio < 0:
            dcf_proxy_score = 8.8 + min(abs(ratio) * 1.2, 1.0)
        elif ratio <= 0.35:
            dcf_proxy_score = 7.4
        elif ratio <= 0.7:
            dcf_proxy_score = 5.9
        elif ratio <= 1:
            dcf_proxy_score = 4.4
        else:
            dcf_proxy_score = 2.6 - min((ratio - 1) * 1.1, 1.4)

    dividend_yield = _safe_float((financials or {}).get("dividend_yield_ttm"))
    dividend_per_share = _safe_float((financials or {}).get("dividend_per_share_ttm"))
    if dividend_yield is None and dividend_per_share and current_price > 0:
        dividend_yield = dividend_per_share / current_price * 100.0
    if dcf_proxy_score is not None and dividend_yield is not None and dividend_yield >= 3:
        dcf_proxy_score = min(dcf_proxy_score + 0.45, 9.8)

    weighted_items: List[tuple[float, float]] = []
    if historical_score is not None:
        weighted_items.append((historical_score, 0.5))
    if relative_score is not None:
        weighted_items.append((relative_score, 0.3))
    if dcf_proxy_score is not None:
        weighted_items.append((dcf_proxy_score, 0.2))
    if not weighted_items:
        score = 5.0
        status = "computed_proxy_without_ratio_history"
    else:
        total_weight = sum(weight for _, weight in weighted_items)
        score = sum(value * weight for value, weight in weighted_items) / total_weight
        status = "computed_from_historical_percentile_relative_and_margin"

    risk_cap: Optional[float] = None
    cap_reasons: List[str] = []
    net_profit = _safe_float((financials or {}).get("net_profit"))
    eps = _safe_float((financials or {}).get("eps"))
    book_value = _safe_float((financials or {}).get("book_value_per_share"))
    roe = _safe_float((financials or {}).get("roe"))
    operating_cash_flow = _safe_float((financials or {}).get("operating_cash_flow_per_share"))
    debt_ratio = _safe_float((financials or {}).get("debt_ratio"))
    if is_st_stock:
        risk_cap = 3.2
        cap_reasons.append("ST风险")
    distress_flags = distress_flags or []
    if "清盘/退市/停牌风险" in distress_flags:
        risk_cap = min(risk_cap or 2.4, 2.4)
        cap_reasons.append("清盘/退市/停牌风险")
    if "高风险困境资产" in distress_flags:
        risk_cap = min(risk_cap or 3.6, 3.6)
        cap_reasons.append("困境资产")
    if "债务/流动性风险" in distress_flags:
        risk_cap = min(risk_cap or 4.2, 4.2)
        cap_reasons.append("债务/流动性风险")
    if "持续经营/审计风险" in distress_flags:
        risk_cap = min(risk_cap or 4.0, 4.0)
        cap_reasons.append("持续经营风险")
    if "低价股流动性风险" in distress_flags:
        risk_cap = min(risk_cap or 5.0, 5.0)
        cap_reasons.append("低价股流动性风险")
    if (net_profit is not None and net_profit < 0) or (eps is not None and eps < 0):
        risk_cap = min(risk_cap or 4.2, 4.2)
        cap_reasons.append("盈利亏损")
    if (book_value is not None and book_value <= 0) or (roe is not None and roe < 0):
        risk_cap = min(risk_cap or 4.6, 4.6)
        cap_reasons.append("净资产或ROE承压")
    if operating_cash_flow is not None and operating_cash_flow < 0:
        risk_cap = min(risk_cap or 5.2, 5.2)
        cap_reasons.append("经营现金流为负")
    if debt_ratio is not None and debt_ratio > 80:
        risk_cap = min(risk_cap or 5.4, 5.4)
        cap_reasons.append("负债率偏高")
    if risk_cap is not None:
        score = min(score, risk_cap)

    pe_pct = context.get("pe_percentile") if context else None
    pb_pct = context.get("pb_percentile") if context else None
    parts: List[str] = []
    if historical_score is not None:
        pe_text = f"PE历史分位{pe_pct * 100:.0f}%" if pe_pct is not None else "PE分位缺失"
        pb_text = f"PB历史分位{pb_pct * 100:.0f}%" if pb_pct is not None else "PB分位缺失"
        parts.append(f"历史估值{historical_score:.1f}分（{pe_text}、{pb_text}）")
    if relative_score is not None:
        peg = context.get("peg") if context else None
        peg_text = f"，PEG{peg:.2f}" if isinstance(peg, (int, float)) and peg > 0 else ""
        parts.append(f"同业/PEG{relative_score:.1f}分（{(context or {}).get('relative_basis')}{peg_text}）")
    if dcf_proxy_score is not None:
        parts.append(f"安全边际代理{dcf_proxy_score:.1f}分（当前价相对AI动态估值区间）")
    if cap_reasons:
        parts.append(f"价值陷阱风控上限：{'、'.join(cap_reasons[:3])}")
    description = "；".join(parts) if parts else "估值历史和同业数据暂未完整读取，使用动态估值区间代理。"

    return {
        "score": _clamp_score(score),
        "description": description,
        "status": status,
        "inputs": ["historical_pe_pb_percentile", "relative_valuation_or_peg", "dynamic_valuation_margin"],
    }


def _market_price_round(value: float) -> float:
    if value >= 100:
        return round(value, 3)
    if value >= 10:
        return round(value, 3)
    return round(value, 4)


def _parse_change_percent_text(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text or text in {"实时", "待读取"}:
        return None
    match = re.search(r"([+-]?\d+(?:\.\d+)?)%", text)
    if not match:
        return None
    number = _safe_float(match.group(1))
    if number is None:
        return None
    if text.startswith("跌") or "跌" in text[:3]:
        return -abs(number)
    if text.startswith("涨") or "涨" in text[:3]:
        return abs(number)
    return number


def _metric_by_label(metrics: List[Dict[str, str]], label: str) -> Optional[Dict[str, str]]:
    for metric in metrics:
        if metric.get("label") == label:
            return metric
    return None


def _parse_metric_float(value: Any) -> Optional[float]:
    text = str(value or "").replace(",", "").strip()
    if not text or text == "待读取":
        return None
    match = re.search(r"[+-]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return _safe_float(match.group(0))


def _build_trend_action_reason(
    current_price: float,
    valuation: Dict[str, Any],
    quant_metrics: List[Dict[str, str]],
) -> str:
    momentum_20_text = (_metric_by_label(quant_metrics, "20日动量") or {}).get("value", "待读取")
    momentum_60_text = (_metric_by_label(quant_metrics, "60日动量") or {}).get("value", "待读取")
    volume_ratio_text = (_metric_by_label(quant_metrics, "量价确认") or {}).get("value", "待读取")
    ma_state = (_metric_by_label(quant_metrics, "均线结构") or {}).get("value", "待读取")
    momentum_20 = _parse_metric_float(momentum_20_text)
    momentum_60 = _parse_metric_float(momentum_60_text)
    volume_ratio = _parse_metric_float(volume_ratio_text)
    unit = str(valuation.get("currency_label") or "元/股").split("/")[0]
    low = _safe_float(valuation.get("low"))
    high = _safe_float(valuation.get("high"))
    price_position = str(valuation.get("price_position") or "待读取")
    prefix = f"20日{momentum_20_text}、60日{momentum_60_text}、量价{volume_ratio_text}"
    if ma_state and ma_state != "待读取":
        prefix += f"、均线{ma_state}"
    prefix += "。"

    strong_short = momentum_20 is not None and momentum_20 >= 18
    strong_mid = momentum_60 is not None and momentum_60 >= 25
    weak_short = momentum_20 is not None and momentum_20 < 0
    weak_mid = momentum_60 is not None and momentum_60 < 0
    volume_confirmed = volume_ratio is not None and volume_ratio >= 1.35
    volume_near = volume_ratio is not None and 1.05 <= volume_ratio < 1.35
    over_valuation = (high is not None and current_price > high) or "高于" in price_position
    under_valuation = (low is not None and current_price < low) or "低于" in price_position
    upper_value = f"{high:.3f}{unit}" if high is not None else "估值上沿"
    lower_value = f"{low:.3f}{unit}" if low is not None else "估值下沿"

    if over_valuation:
        if strong_short and strong_mid:
            if volume_confirmed:
                action = f"走势很强，但价格已高于{upper_value}，现在买的是情绪溢价，不是低估修复；不追第一波，等回踩{upper_value}附近仍有承接，或二次放量突破后再评估。"
            elif volume_near:
                action = f"短中期涨幅已经兑现，量能只接近确认，当前又高于{upper_value}；优先按冲高后分歧处理，等回踩承接比追高更有胜率。"
            else:
                action = f"涨幅领先但量能没有同步放大，当前高于{upper_value}，容易变成缩量冲高；策略是等放量确认或回到合理区间再看。"
        else:
            action = f"价格高于{upper_value}，但趋势强度不够扎实；当前更像估值透支，先看资金是否愿意继续承接，不宜因为上涨就追。"
    elif under_valuation:
        if weak_short or weak_mid:
            action = f"价格低于{lower_value}有估值吸引力，但趋势仍弱；先等止跌和量价修复，不能只因为便宜就提前买。"
        elif volume_confirmed:
            action = f"价格低于{lower_value}，同时量能开始确认，属于低位修复信号；可重点观察回踩不破和板块是否同步走强。"
        else:
            action = f"价格低于{lower_value}，但资金参与度还不够；先观察是否放量修复，确认前只当作低估候选。"
    else:
        if strong_short and volume_confirmed:
            action = "价格仍在合理区间内，且动量和量能同步改善，趋势质量较好；适合等回踩承接或突破确认，不适合无计划追涨。"
        elif strong_short:
            action = "价格在合理区间内，短线动量强但量能确认不足；先看回踩是否缩量、再看突破是否放量。"
        elif weak_short:
            action = "价格在合理区间内，但短线动量转弱；先等止跌信号，避免在趋势没修复时提前下注。"
        else:
            action = "价格在合理区间内，趋势没有明显失控；重点看后续量能能否放大，否则先按震荡处理。"

    return prefix + action


def _valuation_unit(valuation: Dict[str, Any]) -> str:
    return str(valuation.get("currency_label") or "元/股").split("/")[0]


def _format_reason_price(value: Optional[float], unit: str) -> str:
    if value is None:
        return "待读取"
    return f"{value:.3f}{unit}"


def _sniper_point_by_label(sniper_points: List[Dict[str, Any]], label: str) -> Optional[Dict[str, Any]]:
    return next((item for item in sniper_points if item.get("label") == label), None)


def _sniper_price_text(sniper_points: List[Dict[str, Any]], label: str, unit: str) -> str:
    point = _sniper_point_by_label(sniper_points, label)
    price = _safe_float(point.get("price")) if point else None
    return _format_reason_price(price, unit)


def _build_recommendation_reason(
    current_price: float,
    valuation: Dict[str, Any],
    quant_metrics: List[Dict[str, str]],
    sniper_points: List[Dict[str, Any]],
) -> str:
    unit = _valuation_unit(valuation)
    low = _safe_float(valuation.get("low"))
    high = _safe_float(valuation.get("high"))
    price_position = str(valuation.get("price_position") or "待读取")
    confirm_text = _sniper_price_text(sniper_points, "确认位", unit)
    invalid_text = _sniper_price_text(sniper_points, "失效位", unit)
    volume_state = (_metric_by_label(quant_metrics, "量价确认") or {}).get("percentile", "待读取")
    ma_state = (_metric_by_label(quant_metrics, "均线结构") or {}).get("value", "待读取")

    if high is not None and current_price > high:
        return (
            f"当前建议偏谨慎：当前价{current_price:.3f}{unit}已高于动态估值上沿{high:.3f}{unit}，"
            f"安全边际不足。只有放量站稳确认位{confirm_text}且均线继续改善，才考虑右侧跟踪；"
            f"若跌破失效位{invalid_text}，说明承接失败，先降级观察。"
        )
    if low is not None and current_price < low:
        return (
            f"当前属于低估候选：当前价{current_price:.3f}{unit}低于动态估值下沿{low:.3f}{unit}，"
            f"有赔率但不能只因便宜就出手。先等价格止跌、量价状态从{volume_state}转为确认，"
            f"再看能否站上确认位{confirm_text}。"
        )
    return (
        f"当前建议以观察和确认位为主：当前价{current_price:.3f}{unit}，{price_position}，"
        f"估值没有明显失控。先看失效位{invalid_text}是否守住，再看确认位{confirm_text}能否放量突破；"
        f"均线结构为{ma_state}时，不宜提前重仓押方向。"
    )


def _build_valuation_reason(current_price: float, valuation: Dict[str, Any]) -> str:
    unit = _valuation_unit(valuation)
    low = _safe_float(valuation.get("low"))
    high = _safe_float(valuation.get("high"))
    price_position = str(valuation.get("price_position") or "待读取")
    if low is None or high is None:
        return "动态估值区间待读取，强结论会自动降级。"
    if current_price > high:
        advice = "当前更像在交易预期溢价，后续必须用业绩、订单或趋势强度继续消化。"
    elif current_price < low:
        advice = "当前更像低估观察区，重点确认基本面没有恶化、资金承接开始修复。"
    else:
        advice = "当前处在可观察区间，关键不是猜涨跌，而是看量能和趋势是否继续确认。"
    return (
        f"动态估值区间为{low:.3f}{unit}-{high:.3f}{unit}，由最新价、近阶段日线、均线、支撑阻力、"
        f"波动率、板块和资讯倾斜共同校准，每日重算；当前{price_position}。{advice}"
    )


def _build_sniper_reason(sniper_points: List[Dict[str, Any]], valuation: Dict[str, Any]) -> str:
    unit = _valuation_unit(valuation)
    focus = _sniper_point_by_label(sniper_points, "关注区")
    focus_desc = (
        str(focus.get("description") or "先等价格回到更舒服的位置").rstrip("，。、；; ")
        if focus
        else "待读取"
    )
    confirm_text = _sniper_price_text(sniper_points, "确认位", unit)
    invalid_text = _sniper_price_text(sniper_points, "失效位", unit)
    return (
        f"点位计划按三步执行：关注区看承接，{focus_desc}；确认位{confirm_text}用于验证资金愿意继续上攻；"
        f"失效位{invalid_text}是纪律线，跌破后说明原有承接逻辑被破坏。"
    )


def _build_trend_risk_reason(
    current_price: float,
    valuation: Dict[str, Any],
    quant_metrics: List[Dict[str, str]],
    sectors: List[Dict[str, str]],
    news: List[Dict[str, str]],
) -> str:
    trend_text = _build_trend_action_reason(current_price, valuation, quant_metrics)
    volatility = (_metric_by_label(quant_metrics, "年化波动率") or {}).get("value", "待读取")
    first_sector = next((item for item in sectors if item.get("name") and item.get("name") != "待读取"), None)
    sector_text = (
        f"{first_sector.get('name')} {first_sector.get('realtime_change') or first_sector.get('heat')}"
        if first_sector
        else "板块待读取"
    )
    news_counts = _news_signal_counts(news)
    news_text = (
        f"利好{news_counts['positive']}条、利空{news_counts['risk']}条、中性{news_counts['neutral']}条"
        if news_counts["positive"] or news_counts["risk"] or news_counts["neutral"]
        else "资讯持续更新"
    )
    return _short_text(
        f"{trend_text} 同时看风险边界：年化波动率{volatility}，关联板块{sector_text}，最新资讯{news_text}。",
        238,
    )


def _build_growth_logic_reason(
    sectors: List[Dict[str, str]],
    news: List[Dict[str, str]],
    *,
    company_profile: Optional[Dict[str, Any]] = None,
    financials: Optional[Dict[str, Any]] = None,
    scores: Optional[List[Dict[str, Any]]] = None,
    stock: Optional[Dict[str, Any]] = None,
) -> str:
    score_by_label = {
        str(item.get("label") or ""): _safe_float(item.get("score"))
        for item in (scores or [])
        if isinstance(item, dict)
    }
    growth_score = score_by_label.get("成长")
    inputs = _growth_quality_inputs(company_profile, sectors, financials, news, stock=stock)
    industry_level = _evidence_strength_label(float(inputs.get("industry_space") or 0.0))
    moat_level = _evidence_strength_label(float(inputs.get("moat") or 0.0))
    execution_level = _evidence_strength_label(float(inputs.get("commercial_execution") or 0.0))
    growth_profiles = inputs.get("growth_profiles") or []
    if growth_profiles:
        themes = str(inputs.get("growth_profile_labels") or "") or "高成长赛道"
        roles = str(inputs.get("chain_roles") or "")
        moats = roles or "产业链卡位"
    else:
        themes = "主营业务景气度"
        moats = "盈利质量、现金流和竞争格局"
    validation = str(inputs.get("validation") or "")
    top_profile = (growth_profiles or [{}])[0]
    thesis = str(top_profile.get("thesis") or "")
    score_text = f"{growth_score:.1f}" if growth_score is not None else "待读取"
    if growth_score is not None and growth_score >= 7.2:
        stance = "成长分较高，主要来自高成长赛道和产业链卡位"
    elif growth_score is not None and growth_score >= 5.2:
        stance = "成长分中性偏上，方向有空间，但兑现质量仍要继续看"
    else:
        stance = "成长分偏保守，说明赛道、护城河或业绩兑现仍有短板"
    return _short_text(
        f"{stance}。当前成长评分{score_text}，赛道画像为{themes}，产业链位置看{moats}；"
        f"赛道证据{industry_level}、壁垒证据{moat_level}、兑现证据{execution_level}。"
        f"{thesis or '后续必须用收入增速、毛利率、现金流和订单/客户验证。'}"
        f"当前证据：{validation or '主营资料、板块与资讯交叉验证'}。",
        300,
    )


def _derive_dynamic_valuation(
    current_price: float,
    history: List[Dict[str, Any]],
    market: str,
    *,
    news: Optional[List[Dict[str, str]]] = None,
    sectors: Optional[List[Dict[str, str]]] = None,
    financials: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    closes = _history_values(history, "close")
    lows = _history_values(history, "low")
    highs = _history_values(history, "high")
    recent_lows = lows[-180:] or lows
    recent_highs = highs[-180:] or highs
    ma20 = _mean(closes[-20:])
    ma60 = _mean(closes[-60:])
    ma120 = _mean(closes[-120:])
    momentum_20 = _return_percent(current_price, closes[-21] if len(closes) >= 21 else None)
    momentum_60 = _return_percent(current_price, closes[-61] if len(closes) >= 61 else None)
    volatility = _annualized_volatility(closes)
    atr_percent = _average_true_range_percent(history)
    stable_anchor = _stable_price_anchor(current_price, closes)
    sector_change = _sector_average_change(sectors or [])
    news_balance = _news_balance_score(news or [])
    quality_tilt = _valuation_quality_tilt(
        momentum_20=momentum_20,
        momentum_60=momentum_60,
        sector_change=sector_change,
        news_balance=news_balance,
        financials=financials,
    )

    if recent_lows and recent_highs:
        support = _quantile(recent_lows, 0.22) or min(recent_lows)
        resistance = _quantile(recent_highs, 0.78) or max(recent_highs)
        trend_anchor = _weighted_average(
            [
                (stable_anchor, 0.34),
                (ma20, 0.22),
                (ma60, 0.26),
                (ma120, 0.18),
            ]
        ) or stable_anchor
        adjusted_anchor = trend_anchor * (1.0 + quality_tilt)
        volatility_component = _clamp((volatility or 48.0) / 520.0, 0.08, 0.24)
        atr_component = _clamp((atr_percent or 3.0) / 55.0, 0.03, 0.11)
        target_half_width = _clamp(0.09 + volatility_component + atr_component, 0.16, 0.34)
        support_floor = support
        resistance_ceiling = resistance
        model_low = adjusted_anchor * (1.0 - target_half_width * 0.72)
        model_high = adjusted_anchor * (1.0 + target_half_width * 0.86)
        low = _weighted_average([(support_floor, 0.28), (model_low, 0.72)]) or model_low
        high = _weighted_average([(resistance_ceiling, 0.30), (model_high, 0.70)]) or model_high
        low = _clamp(low, adjusted_anchor * 0.68, adjusted_anchor * 0.97)
        high = _clamp(high, adjusted_anchor * 1.08, adjusted_anchor * 1.48)
    else:
        adjusted_anchor = stable_anchor * (1.0 + quality_tilt)
        target_half_width = _clamp(0.16 + ((volatility or 45.0) / 500.0), 0.18, 0.34)
        low = adjusted_anchor * (1.0 - target_half_width)
        high = adjusted_anchor * (1.0 + target_half_width * 1.25)

    if high <= low:
        low = stable_anchor * 0.9
        high = stable_anchor * 1.18
    if high <= low * 1.12:
        center = _weighted_average([(stable_anchor, 0.55), (current_price, 0.2), (ma60, 0.25)]) or stable_anchor
        width = center * _clamp(0.12 + ((volatility or 45.0) / 700.0), 0.14, 0.28)
        low = center - width
        high = center + width * 1.16
    if high > low * 1.65:
        center = (high + low) / 2.0
        width = center * 0.245
        low = center - width
        high = center + width

    return {
        "label": "AI动态估值区间",
        "currency_label": "港元/股" if market == "H股" else "元/股",
        "low": _market_price_round(low),
        "high": _market_price_round(high),
        "current_price": _market_price_round(current_price),
        "price_position": _price_position(current_price, low, high),
        "source": "computed",
        "status": "computed_from_stable_quant_model",
        "inputs": [
            "current_price",
            "stable_price_anchor",
            "quantile_support_resistance",
            "ma20",
            "ma60",
            "ma120",
            "atr",
            "volatility",
            "news_sector_financial_tilt",
        ],
    }


def _news_signal_counts(news: List[Dict[str, str]]) -> Dict[str, int]:
    counts = {"positive": 0, "risk": 0, "neutral": 0, "pending": 0}
    for item in news:
        tone = str(item.get("tone") or "neutral")
        counts[tone if tone in counts else "neutral"] += 1
    return counts


def _build_news_summary(
    news_pool: List[Dict[str, str]],
    display_news: List[Dict[str, str]],
) -> Dict[str, Any]:
    if not news_pool or _has_pending_items(news_pool):
        return {
            "pool_count": 0,
            "display_count": 0,
            "positive_count": 0,
            "risk_count": 0,
            "neutral_count": 0,
            "latest_date": "待读取",
            "description": "资讯池待读取。",
        }

    counts = _news_signal_counts(news_pool)
    display_count = len([
        item for item in display_news
        if item.get("title") and item.get("title") != "待读取"
    ])
    pool_count = len(news_pool)
    latest_date = _first_valid_news_date(news_pool)
    description = (
        f"本次资讯池聚合{pool_count}条，精选展示最新{display_count}条；"
        f"利好{counts['positive']}条、利空{counts['risk']}条、中性{counts['neutral']}条，"
        "其余资讯进入情绪与结论计算。"
    )
    return {
        "pool_count": pool_count,
        "display_count": display_count,
        "positive_count": counts["positive"],
        "risk_count": counts["risk"],
        "neutral_count": counts["neutral"],
        "latest_date": latest_date,
        "description": description,
    }


def _sector_average_change(sectors: List[Dict[str, str]]) -> Optional[float]:
    values = [
        parsed
        for parsed in (
            _parse_change_percent_text(item.get("realtime_change") or item.get("heat"))
            for item in sectors
        )
        if parsed is not None
    ]
    return _mean(values) if values else None


def _news_balance_score(news: List[Dict[str, str]]) -> float:
    counts = _news_signal_counts(news)
    effective_total = counts["positive"] + counts["risk"] + counts["neutral"] * 0.35
    if effective_total <= 0:
        return 0.0
    return _clamp((counts["positive"] - counts["risk"] * 1.25) / effective_total, -1.0, 1.0)


def _trend_quality_score(
    current_price: float,
    closes: List[float],
    *,
    ma20: Optional[float],
    ma60: Optional[float],
    momentum_20: Optional[float],
    momentum_60: Optional[float],
    volatility: Optional[float],
    volume_ratio: Optional[float],
) -> float:
    """Return a conservative 0..1 trend score with volatility damping."""

    score = 0.45
    if ma20 and current_price >= ma20:
        score += 0.14
    elif ma20:
        score -= 0.12
    if ma20 and ma60 and ma20 >= ma60:
        score += 0.16
    elif ma20 and ma60:
        score -= 0.14
    score += _bounded_signal(momentum_20 or 0.0, 24.0, 0.16)
    score += _bounded_signal(momentum_60 or 0.0, 45.0, 0.18)
    score += _clamp(((volume_ratio or 1.0) - 1.0) * 0.16, -0.08, 0.12)
    if len(closes) >= 12:
        recent = closes[-12:]
        positive_days = sum(1 for index in range(1, len(recent)) if recent[index] >= recent[index - 1])
        score += (positive_days / max(len(recent) - 1, 1) - 0.5) * 0.12
    score -= _clamp(((volatility or 45.0) - 45.0) / 220.0, 0.0, 0.16)
    return _clamp(score, 0.0, 1.0)


def _valuation_quality_tilt(
    *,
    momentum_20: Optional[float],
    momentum_60: Optional[float],
    sector_change: Optional[float],
    news_balance: float,
    financials: Optional[Dict[str, Any]],
) -> float:
    """Small valuation tilt. Kept deliberately capped so daily news cannot whipsaw ranges."""

    tilt = 0.0
    tilt += _bounded_signal(momentum_60 or 0.0, 70.0, 0.035)
    tilt += _bounded_signal(momentum_20 or 0.0, 40.0, 0.018)
    tilt += _bounded_signal(sector_change or 0.0, 6.0, 0.018)
    tilt += news_balance * 0.018
    if financials:
        roe = financials.get("roe")
        net_margin = financials.get("net_margin")
        operating_cash_flow = financials.get("operating_cash_flow_per_share")
        if isinstance(roe, (int, float)):
            tilt += _bounded_signal(roe - 8.0, 20.0, 0.024)
        if isinstance(net_margin, (int, float)):
            tilt += _bounded_signal(net_margin - 6.0, 18.0, 0.018)
        if operating_cash_flow is not None:
            tilt += 0.014 if operating_cash_flow > 0 else -0.02
    return _clamp(tilt, -0.075, 0.085)


def _is_special_treatment_stock(stock: Optional[Dict[str, Any]]) -> bool:
    if not stock:
        return False
    name = str(stock.get("name") or "")
    compact_name = _compact_query(name)
    return compact_name.startswith("st") or compact_name.startswith("*st") or "退市" in compact_name


_KNOWN_DISTRESS_COMPANY_TERMS = (
    "中国恒大",
    "恒大",
    "碧桂园",
    "华夏幸福",
    "海航控股",
)

_SEVERE_DISTRESS_TERMS = (
    "清盘",
    "清算",
    "停牌",
    "退市",
    "终止上市",
    "破产",
    "资不抵债",
    "债务违约",
    "交叉违约",
    "重整失败",
)

_LIQUIDITY_DISTRESS_TERMS = (
    "债务重组",
    "债务逾期",
    "流动性风险",
    "偿债压力",
    "被执行",
    "冻结",
    "诉讼",
    "保交楼",
)

_GOING_CONCERN_TERMS = (
    "持续经营",
    "审计意见",
    "无法表示意见",
    "保留意见",
    "累计亏损",
    "连续亏损",
    "净资产为负",
)


def _company_identity_terms(stock: Optional[Dict[str, Any]]) -> List[str]:
    """Return compact identifiers that make a news item company-specific."""

    if not stock:
        return []
    raw_terms: List[str] = [
        str(stock.get("name") or ""),
        str(stock.get("code") or ""),
        str(stock.get("canonical_code") or ""),
        str(stock.get("display_code") or ""),
    ]
    code = str(stock.get("code") or "")
    digits = re.sub(r"\D", "", code)
    if digits:
        raw_terms.extend([digits, digits.lstrip("0") or digits])
    terms: List[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        compact = _compact_query(term)
        if not compact or compact in seen:
            continue
        # Very short numeric snippets are too noisy for company identity.
        if compact.isdigit() and len(compact) < 4:
            continue
        terms.append(compact)
        seen.add(compact)
    return terms


def _has_severe_distress_context(compact_text: str) -> bool:
    """Separate routine suspension from true delisting/liquidation distress."""

    severe_terms = [
        _compact_query(term)
        for term in _SEVERE_DISTRESS_TERMS
        if _compact_query(term) and _compact_query(term) != _compact_query("停牌")
    ]
    if any(term in compact_text for term in severe_terms):
        return True
    suspension = _compact_query("停牌")
    if suspension not in compact_text:
        return False
    distress_context = (
        "退市",
        "终止上市",
        "清盘",
        "清算",
        "破产",
        "重大违法",
        "风险警示",
        "披星戴帽",
        "无法表示意见",
        "资不抵债",
        "债务违约",
    )
    return any(_compact_query(term) in compact_text for term in distress_context)


def _has_going_concern_context(compact_text: str) -> bool:
    """Avoid treating benign phrases like '持续经营时间最久' as audit risk."""

    hard_terms = (
        "审计意见",
        "无法表示意见",
        "保留意见",
        "累计亏损",
        "连续亏损",
        "净资产为负",
    )
    if any(_compact_query(term) in compact_text for term in hard_terms):
        return True
    going = _compact_query("持续经营")
    if going not in compact_text:
        return False
    risk_context = (
        "重大不确定",
        "存在不确定",
        "能力存在",
        "疑虑",
        "风险",
        "承压",
        "恶化",
        "无法",
        "困难",
    )
    return any(_compact_query(term) in compact_text for term in risk_context)


def _distress_risk_flags(
    stock: Optional[Dict[str, Any]],
    company_profile: Optional[Dict[str, Any]],
    news: List[Dict[str, str]],
) -> List[str]:
    """Detect distress risks that raw valuation ratios can mistake for value."""

    profile = company_profile or {}
    identity_terms = _company_identity_terms(stock)
    profile_text_parts: List[str] = [
        str((stock or {}).get("name") or ""),
        str((stock or {}).get("code") or ""),
        str(profile.get("verified_position") or ""),
        str(profile.get("industry") or ""),
        str(profile.get("business") or ""),
        str(profile.get("business_model") or ""),
        str(profile.get("watch_points") or ""),
        str(profile.get("intro") or ""),
    ]
    company_compact = _compact_query(" ".join(profile_text_parts))
    linked_news_parts: List[str] = []
    for item in news[:16]:
        news_text = " ".join([str(item.get("title") or ""), str(item.get("summary") or "")])
        compact_news = _compact_query(news_text)
        if compact_news and identity_terms and any(term in compact_news for term in identity_terms):
            linked_news_parts.append(news_text)
    linked_news_compact = _compact_query(" ".join(linked_news_parts))
    compact = " ".join([company_compact, linked_news_compact])
    flags: List[str] = []

    if _is_special_treatment_stock(stock):
        flags.append("ST/退市风险")
    if any(_compact_query(term) in compact for term in _KNOWN_DISTRESS_COMPANY_TERMS):
        flags.append("高风险困境资产")
    if _has_severe_distress_context(compact):
        flags.append("清盘/退市/停牌风险")
    if any(_compact_query(term) in compact for term in _LIQUIDITY_DISTRESS_TERMS):
        flags.append("债务/流动性风险")
    if _has_going_concern_context(compact):
        flags.append("持续经营/审计风险")

    return _dedupe_growth_strings(flags)


def _fundamental_risk_adjustment(
    financials: Optional[Dict[str, Any]],
    *,
    is_st_stock: bool,
    is_financial_institution: bool = False,
    distress_flags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Convert financial distress into score penalties and hard caps."""

    adjustments: Dict[str, Any] = {
        "flags": [],
        "value_penalty": 0.0,
        "growth_penalty": 0.0,
        "profitability_penalty": 0.0,
        "finance_penalty": 0.0,
        "value_cap": None,
        "growth_cap": None,
        "profitability_cap": None,
        "finance_cap": None,
    }

    def add_flag(
        label: str,
        *,
        value: float = 0.0,
        growth: float = 0.0,
        profitability: float = 0.0,
        finance: float = 0.0,
        value_cap: Optional[float] = None,
        growth_cap: Optional[float] = None,
        profitability_cap: Optional[float] = None,
        finance_cap: Optional[float] = None,
    ) -> None:
        if label not in adjustments["flags"]:
            adjustments["flags"].append(label)
        adjustments["value_penalty"] += value
        adjustments["growth_penalty"] += growth
        adjustments["profitability_penalty"] += profitability
        adjustments["finance_penalty"] += finance
        for key, cap in (
            ("value_cap", value_cap),
            ("growth_cap", growth_cap),
            ("profitability_cap", profitability_cap),
            ("finance_cap", finance_cap),
        ):
            if cap is None:
                continue
            current_cap = adjustments[key]
            adjustments[key] = cap if current_cap is None else min(current_cap, cap)

    distress_flags = distress_flags or []
    for flag in distress_flags:
        if flag == "清盘/退市/停牌风险":
            add_flag(
                flag,
                value=2.2,
                growth=1.8,
                profitability=1.5,
                finance=1.8,
                value_cap=2.8,
                growth_cap=2.8,
                profitability_cap=3.0,
                finance_cap=2.8,
            )
        elif flag == "ST/退市风险":
            add_flag(
                flag,
                value=1.55,
                growth=1.15,
                profitability=1.05,
                finance=1.0,
                value_cap=3.8,
                growth_cap=3.8,
                profitability_cap=3.8,
                finance_cap=3.6,
            )
        elif flag == "高风险困境资产":
            add_flag(
                flag,
                value=1.15,
                growth=0.75,
                profitability=0.55,
                finance=0.95,
                value_cap=4.2,
                growth_cap=4.6,
                profitability_cap=5.0,
                finance_cap=4.1,
            )
        elif flag == "债务/流动性风险":
            add_flag(
                flag,
                value=0.9,
                growth=0.55,
                profitability=0.45,
                finance=1.0,
                value_cap=4.6,
                growth_cap=5.0,
                profitability_cap=5.0,
                finance_cap=4.0,
            )
        elif flag == "持续经营/审计风险":
            add_flag(
                flag,
                value=0.75,
                growth=0.45,
                profitability=0.55,
                finance=0.65,
                value_cap=4.4,
                growth_cap=5.0,
                profitability_cap=4.4,
                finance_cap=4.2,
            )

    if is_st_stock:
        add_flag(
            "ST风险",
            value=1.2,
            growth=0.95,
            profitability=0.8,
            finance=0.65,
            value_cap=4.8,
            growth_cap=4.2,
            profitability_cap=4.6,
            finance_cap=4.4,
        )

    if not financials:
        return adjustments

    eps = _safe_float(financials.get("eps"))
    book_value = _safe_float(financials.get("book_value_per_share"))
    operating_cash_flow = _safe_float(financials.get("operating_cash_flow_per_share"))
    net_profit = _safe_float(financials.get("net_profit"))
    deducted_net_profit = _safe_float(financials.get("deducted_net_profit"))
    net_margin = _safe_float(financials.get("net_margin"))
    roe = _safe_float(financials.get("roe"))
    debt_ratio = _safe_float(financials.get("debt_ratio"))
    revenue_growth = _safe_float(financials.get("revenue_growth_qoq"))
    net_profit_growth = _safe_float(financials.get("net_profit_growth_qoq"))

    if net_profit is not None and net_profit < 0:
        add_flag("归母净利润亏损", value=0.85, growth=0.18, profitability=0.9, finance=0.25, value_cap=4.6, profitability_cap=4.2)
    if deducted_net_profit is not None and deducted_net_profit < 0:
        add_flag("扣非亏损", value=0.35, growth=0.12, profitability=0.45, value_cap=4.4)
    if eps is not None and eps < 0:
        add_flag("EPS亏损", value=0.45, profitability=0.55, value_cap=4.4)
    if book_value is not None and book_value <= 0:
        add_flag("每股净资产偏弱", value=0.75, finance=0.8, value_cap=3.9, finance_cap=3.6)
    if operating_cash_flow is not None and operating_cash_flow < 0:
        add_flag("经营现金流为负", value=0.35, finance=0.55, finance_cap=4.0)
    if net_margin is not None and net_margin < 0:
        add_flag("销售净利率为负", value=0.35, profitability=0.6, profitability_cap=4.2)
    if roe is not None and roe < 0:
        add_flag("ROE为负", value=0.35, profitability=0.55, value_cap=4.2)
    if debt_ratio is not None and not is_financial_institution:
        if debt_ratio >= 90:
            add_flag("资产负债率高", value=0.55, finance=0.85, value_cap=3.8, finance_cap=3.5)
        elif debt_ratio >= 75:
            add_flag("资产负债率偏高", value=0.25, finance=0.45, value_cap=5.4, finance_cap=4.8)
    if revenue_growth is not None and revenue_growth < -10:
        add_flag("收入下滑", growth=0.22, profitability=0.25, growth_cap=6.8)
    if net_profit_growth is not None and net_profit_growth < -20:
        add_flag("利润下滑", growth=0.1, profitability=0.35)
    if (
        revenue_growth is not None
        and revenue_growth < -10
        and net_profit_growth is not None
        and net_profit_growth < -20
    ):
        add_flag("收入利润双降", growth=0.1, profitability=0.2, growth_cap=6.4)

    return adjustments


def _apply_score_cap(score: float, cap: Optional[float]) -> float:
    return min(score, cap) if cap is not None else score


_HIGH_GROWTH_INDUSTRY_KEYWORDS = (
    "物理ai",
    "人工智能",
    "大模型",
    "算力",
    "ai算力",
    "ai芯片",
    "ai服务器",
    "服务器",
    "数据中心",
    "液冷",
    "半导体",
    "半导体设备",
    "薄膜沉积",
    "刻蚀设备",
    "第三代半导体",
    "碳化硅",
    "sic",
    "宽禁带半导体",
    "功率半导体",
    "氮化镓",
    "衬底",
    "晶圆",
    "先进封装",
    "存储芯片",
    "cpo",
    "光电共封装",
    "光模块",
    "高速光模块",
    "光通信",
    "通信设备",
    "pcb",
    "高速pcb",
    "光刻胶",
    "电子专用材料",
    "机器人",
    "人形机器人",
    "ai办公",
    "办公软件",
    "端侧ai",
    "边缘ai",
    "智能操作系统",
    "智能座舱",
    "智能汽车",
    "智能驾驶",
    "无人驾驶",
    "数字孪生",
    "空间智能",
    "合成数据",
    "创新药",
    "生物医药",
    "医疗器械",
    "新能源",
    "储能",
    "固态电池",
    "低空经济",
    "商业航天",
    "高端装备",
    "国产替代",
)
_MOAT_KEYWORDS = (
    "核心基础设施",
    "核心",
    "平台",
    "龙头",
    "首家",
    "稀缺",
    "壁垒",
    "护城河",
    "专利",
    "认证",
    "客户",
    "头部客户",
    "客户认证",
    "生态",
    "牌照",
    "国产替代",
    "技术",
    "工艺",
    "制程",
    "良率",
    "产能",
    "份额",
    "全球",
    "领先",
    "供应链",
    "衬底",
    "晶体",
    "晶圆",
    "高纯",
    "高品质",
    "量产",
    "数据",
)

_HIGH_GROWTH_INDUSTRY_CLUSTERS: tuple[Dict[str, Any], ...] = (
    {
        "label": "具身智能与机器人",
        "base_space": 0.94,
        "base_moat": 0.66,
        "thesis": "AI进入物理世界，核心看整机放量、核心零部件壁垒和运动控制算法兑现。",
        "aliases": (
            "具身智能",
            "人形机器人",
            "机器人",
            "四足机器人",
            "服务机器人",
            "工业机器人",
            "谐波减速器",
            "rv减速器",
            "行星减速器",
            "行星滚柱丝杠",
            "无框力矩电机",
            "空心杯电机",
            "六维力矩传感器",
            "触觉传感器",
            "电子皮肤",
            "slam",
            "运动控制",
            "强化学习",
        ),
        "roles": (
            ("整机平台", ("人形机器人", "服务机器人", "四足机器人", "工业机器人")),
            ("核心零部件", ("谐波减速器", "rv减速器", "行星滚柱丝杠", "无框力矩电机", "空心杯电机", "六维力矩传感器")),
            ("控制算法", ("运动控制", "强化学习", "slam", "任务规划", "具身语义")),
        ),
        "moat_terms": ("量产", "客户认证", "精密制造", "控制算法", "核心零部件", "供应链", "专利"),
    },
    {
        "label": "AI算力与先进半导体",
        "base_space": 0.96,
        "base_moat": 0.68,
        "thesis": "数字经济的算力底座，核心看芯片、先进封装、材料、光互联和数据中心配套。",
        "aliases": (
            "ai算力",
            "算力",
            "gpu",
            "asic",
            "ai芯片",
            "类脑芯片",
            "神经形态",
            "量子计算",
            "hbm",
            "chiplet",
            "先进封装",
            "2.5d",
            "3d封装",
            "半导体",
            "第三代半导体",
            "第四代半导体",
            "碳化硅",
            "sic",
            "氮化镓",
            "gan",
            "氧化镓",
            "ga2o3",
            "衬底",
            "晶圆",
            "功率半导体",
            "ai服务器",
            "服务器",
            "液冷",
            "数据中心",
            "光模块",
            "硅光",
            "cpo",
            "1.6t",
            "pcb",
            "高速pcb",
            "高频高速",
            "光刻胶",
            "电子专用材料",
            "半导体设备",
            "薄膜沉积",
            "刻蚀设备",
            "清洗设备",
            "cmp设备",
            "量测设备",
            "检测设备",
            "cvd",
            "ald",
            "pecvd",
            "ai办公",
            "办公软件",
            "saas",
            "企业服务",
            "端侧ai",
            "边缘ai",
            "智能操作系统",
            "智能座舱",
            "智能汽车",
        ),
        "roles": (
            ("核心芯片", ("gpu", "asic", "ai芯片", "类脑芯片", "量子计算")),
            ("先进封装/存储", ("hbm", "chiplet", "先进封装", "2.5d", "3d封装")),
            ("半导体材料", ("碳化硅", "sic", "氮化镓", "gan", "氧化镓", "衬底", "晶圆", "光刻胶", "电子专用材料")),
            ("半导体设备", ("半导体设备", "薄膜沉积", "刻蚀设备", "清洗设备", "cmp设备", "量测设备", "检测设备", "cvd", "ald", "pecvd")),
            ("算力基础设施", ("ai服务器", "服务器", "液冷", "数据中心", "光模块", "硅光", "cpo", "pcb", "高速pcb")),
            ("AI软件/端侧智能", ("ai办公", "办公软件", "saas", "企业服务", "端侧ai", "边缘ai", "智能操作系统", "智能座舱", "智能汽车")),
        ),
        "moat_terms": ("国产替代", "客户认证", "良率", "制程", "产能", "衬底", "高纯", "专利", "龙头", "全球"),
    },
    {
        "label": "AI智能体与数字原生应用",
        "base_space": 0.88,
        "base_moat": 0.58,
        "thesis": "软件原生生产力重构，核心看场景闭环、数据资产和商业化付费能力。",
        "aliases": (
            "ai智能体",
            "智能体",
            "agent",
            "aigc",
            "生成式ai",
            "智能语音",
            "语音识别",
            "认知智能",
            "多模态",
            "讯飞星火",
            "教育ai",
            "ai应用",
            "ai办公",
            "办公软件",
            "saas",
            "企业服务",
            "端侧ai",
            "边缘ai",
            "智能操作系统",
            "智能座舱",
            "智能汽车",
            "数字娱乐",
            "游戏",
            "社交",
            "空间计算",
            "虚拟世界",
            "生成式游戏",
            "3d资产",
            "depin",
            "web3",
            "zkp",
            "隐私计算",
        ),
        "roles": (
            ("垂直Agent", ("ai智能体", "智能体", "agent", "ai应用", "智能语音", "认知智能", "多模态", "教育ai", "ai办公", "企业服务", "saas")),
            ("端侧/行业AI应用", ("端侧ai", "边缘ai", "智能操作系统", "智能座舱", "智能汽车", "办公软件", "数字娱乐", "游戏", "社交")),
            ("数字空间底座", ("数字孪生", "空间计算", "空间智能", "合成数据", "数字地球", "物理ai")),
            ("去中心化基础设施", ("depin", "web3", "zkp", "隐私计算")),
        ),
        "moat_terms": ("数据", "平台", "生态", "客户", "工作流", "闭环", "核心基础设施", "首家"),
    },
    {
        "label": "物理AI与空间智能基础设施",
        "base_space": 0.86,
        "base_moat": 0.64,
        "growth_floor_bonus": 0.28,
        "thesis": "物理AI是AI进入真实世界的基础设施，核心看空间数据、仿真训练、世界模型和行业场景复制。",
        "aliases": (
            "物理ai",
            "physicalai",
            "数字孪生",
            "空间智能",
            "空间数据",
            "仿真",
            "仿真训练",
            "合成数据",
            "世界模型",
            "三维仿真",
            "智驾仿真",
            "数字地球",
            "场景资产",
            "aperdata",
            "51aes",
            "51sim",
            "51earth",
            "51world",
        ),
        "roles": (
            ("物理AI底座", ("物理ai", "核心基础设施", "世界模型", "空间数据")),
            ("仿真训练平台", ("仿真", "仿真训练", "51sim", "合成数据", "智驾仿真")),
            ("空间智能平台", ("空间智能", "数字孪生", "数字地球", "51aes", "51earth", "场景资产")),
        ),
        "moat_terms": ("核心基础设施", "平台", "数据", "仿真", "客户", "场景", "首家", "空间", "训练"),
    },
    {
        "label": "前沿生命科学与精准医疗",
        "base_space": 0.90,
        "base_moat": 0.62,
        "thesis": "医疗从经验试错走向计算驱动，核心看管线、临床进展、审批和商业化兑现。",
        "aliases": (
            "ai制药",
            "计算生物",
            "数字病人",
            "mrna",
            "sirna",
            "adc",
            "双抗",
            "三抗",
            "car-t",
            "car-nk",
            "crispr",
            "基因编辑",
            "细胞治疗",
            "脑机接口",
            "手术机器人",
            "微纳机器人",
            "高端医疗器械",
        ),
        "roles": (
            ("AI制药平台", ("ai制药", "计算生物", "数字病人")),
            ("创新药管线", ("mrna", "sirna", "adc", "双抗", "三抗", "car-t", "car-nk", "crispr", "细胞治疗")),
            ("高端器械", ("脑机接口", "手术机器人", "微纳机器人", "高端医疗器械")),
        ),
        "moat_terms": ("临床", "管线", "审批", "适应症", "专利", "平台", "商业化", "注册"),
    },
    {
        "label": "新能源与未来材料",
        "base_space": 0.86,
        "base_moat": 0.56,
        "thesis": "能源和材料体系重构，核心看技术路线确定性、成本曲线和量产良率。",
        "aliases": (
            "新能源",
            "新能源汽车",
            "动力电池",
            "锂电池",
            "固态电池",
            "半固态",
            "锂金属",
            "钠离子电池",
            "储能",
            "氢能",
            "绿氢",
            "燃料电池",
            "可控核聚变",
            "高温气冷堆",
            "熔盐堆",
            "hjt",
            "钙钛矿",
            "石墨烯",
            "碳纳米管",
            "自修复材料",
            "超级合金",
            "生物基",
            "可降解",
        ),
        "roles": (
            ("下一代储能", ("新能源", "新能源汽车", "动力电池", "锂电池", "固态电池", "半固态", "锂金属", "钠离子电池", "储能")),
            ("清洁能源", ("氢能", "绿氢", "燃料电池", "可控核聚变", "高温气冷堆", "熔盐堆", "hjt", "钙钛矿")),
            ("未来材料", ("石墨烯", "碳纳米管", "自修复材料", "超级合金", "生物基", "可降解")),
        ),
        "moat_terms": ("良率", "成本", "量产", "技术路线", "客户认证", "产能", "专利", "资源"),
    },
    {
        "label": "智能交通与低空经济",
        "base_space": 0.89,
        "base_moat": 0.58,
        "thesis": "交通从二维走向三维，核心看安全认证、场景运营和核心感知/动力链条。",
        "aliases": (
            "自动驾驶",
            "智能驾驶",
            "无人驾驶",
            "l4",
            "l5",
            "车路云",
            "v2x",
            "智能座舱",
            "智能汽车",
            "端侧ai",
            "边缘ai",
            "智能操作系统",
            "激光雷达",
            "4d毫米波雷达",
            "车载芯片",
            "低空经济",
            "evtol",
            "垂直起降",
            "低空空域",
            "低空管制",
            "分布式电推进",
            "航空电池",
        ),
        "roles": (
            ("自动驾驶系统", ("自动驾驶", "智能驾驶", "无人驾驶", "l4", "l5", "车路云", "v2x", "智能座舱", "智能汽车", "端侧ai", "智能操作系统")),
            ("感知与计算硬件", ("激光雷达", "4d毫米波雷达", "车载芯片")),
            ("低空飞行器/基建", ("低空经济", "evtol", "垂直起降", "低空空域", "低空管制", "分布式电推进", "航空电池")),
        ),
        "moat_terms": ("安全认证", "量产", "车规", "客户定点", "运营牌照", "空域", "算法", "传感器"),
    },
)


_STRATEGIC_GROWTH_COMPANY_HINTS: tuple[Dict[str, Any], ...] = (
    {
        "matches": ("6651", "HK6651", "五一视界", "51WORLD"),
        "terms": "物理AI 核心基础设施 空间智能 数字孪生 仿真训练 合成数据 世界模型 51Aes 51Sim 51Earth",
    },
    {
        "matches": ("688234", "天岳先进"),
        "terms": "碳化硅 SiC 第三代半导体 宽禁带半导体 衬底 晶圆 半导体材料 国产替代",
    },
    {
        "matches": ("300308", "中际旭创"),
        "terms": "AI算力 光模块 CPO 1.6T 数据中心 光通信 硅光 全球领先",
    },
    {
        "matches": ("688981", "中芯国际"),
        "terms": "半导体 晶圆 制造 制程 国产替代 先进工艺 产能",
    },
    {
        "matches": ("002230", "科大讯飞"),
        "terms": "人工智能 大模型 智能语音 语音识别 认知智能 多模态 讯飞星火 AI应用",
    },
    {
        "matches": ("688111", "金山办公"),
        "terms": "AI办公 办公软件 SaaS 企业服务 大模型 AI应用 智能体 文档协同 订阅生态",
    },
    {
        "matches": ("300496", "中科创达"),
        "terms": "端侧AI 智能操作系统 智能座舱 智能汽车 边缘AI 机器人 操作系统 智能驾驶",
    },
    {
        "matches": ("300418", "昆仑万维"),
        "terms": "AIGC AI智能体 大模型 AI应用 数字娱乐 游戏 社交 Agent 多模态",
    },
    {
        "matches": ("688072", "拓荆科技"),
        "terms": "半导体设备 薄膜沉积 CVD ALD PECVD 先进制程 半导体 国产替代 晶圆制造",
    },
    {
        "matches": ("300750", "宁德时代"),
        "terms": "新能源 动力电池 储能 固态电池 锂电池 全球龙头 产业链核心",
    },
    {
        "matches": ("002594", "比亚迪"),
        "terms": "新能源汽车 动力电池 储能 智能驾驶 垂直整合 全球化",
    },
    {
        "matches": ("002415", "海康威视"),
        "terms": "人工智能 机器视觉 智能物联 工业视觉 边缘AI 算法平台",
    },
    {
        "matches": ("000063", "中兴通讯"),
        "terms": "AI算力 通信设备 数据中心 服务器 光通信 5G 国产替代",
    },
)


def _strategic_growth_hint_terms(stock: Optional[Dict[str, Any]]) -> str:
    if not stock:
        return ""
    compact_identity = _compact_query(
        " ".join(
            [
                str(stock.get("name") or ""),
                str(stock.get("code") or ""),
            ]
        )
    )
    hints: List[str] = []
    for item in _STRATEGIC_GROWTH_COMPANY_HINTS:
        if any(_compact_query(term) in compact_identity for term in item.get("matches") or ()):
            hints.append(str(item.get("terms") or ""))
    return " ".join(hints)


def _growth_context_terms(
    profile: Optional[Dict[str, Any]],
    sectors: List[Dict[str, str]],
    stock: Optional[Dict[str, Any]] = None,
) -> str:
    profile = profile or {}
    parts: List[str] = [
        _strategic_growth_hint_terms(stock),
        str(profile.get("verified_position") or ""),
        str(profile.get("industry") or ""),
        str(profile.get("business") or ""),
        str(profile.get("business_model") or ""),
        str(profile.get("industry_logic") or ""),
        str(profile.get("watch_points") or ""),
        str(profile.get("intro") or ""),
        " ".join(str(item) for item in profile.get("products") or []),
    ]
    for sector in sectors[:6]:
        parts.extend(
            [
                str(sector.get("name") or ""),
                str(sector.get("relevance") or ""),
                str(sector.get("reason") or ""),
            ]
        )
    return _compact_query(" ".join(parts))


def _growth_source_terms(
    profile: Optional[Dict[str, Any]],
    sectors: List[Dict[str, str]],
    news: List[Dict[str, str]],
    stock: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    profile = profile or {}
    profile_parts = [
        _strategic_growth_hint_terms(stock),
        str(profile.get("verified_position") or ""),
        str(profile.get("industry") or ""),
        str(profile.get("business") or ""),
        str(profile.get("business_model") or ""),
        str(profile.get("industry_logic") or ""),
        str(profile.get("watch_points") or ""),
        str(profile.get("intro") or ""),
        " ".join(str(item) for item in profile.get("products") or []),
    ]
    sector_parts: List[str] = []
    for sector in sectors[:8]:
        sector_parts.extend(
            [
                str(sector.get("name") or ""),
                str(sector.get("relevance") or ""),
                str(sector.get("reason") or ""),
                str(sector.get("realtime_board") or ""),
            ]
        )
    news_parts = [
        str(item.get("title") or "")
        for item in news[:12]
    ]
    return {
        "profile": _compact_query(" ".join(profile_parts)),
        "sector": _compact_query(" ".join(sector_parts)),
        "news": _compact_query(" ".join(news_parts)),
    }


def _matches_any(text: str, keywords: tuple[str, ...] | List[str]) -> List[str]:
    hits: List[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        compact = _compact_query(keyword)
        if compact and compact in text and compact not in seen:
            hits.append(str(keyword))
            seen.add(compact)
    return hits


def _dedupe_growth_strings(values: List[str]) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = _compact_query(text)
        if not key or key in seen:
            continue
        deduped.append(text)
        seen.add(key)
    return deduped


def _growth_industry_profiles(
    profile: Optional[Dict[str, Any]],
    sectors: List[Dict[str, str]],
    news: List[Dict[str, str]],
    stock: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Map a company to high-growth industry clusters with evidence quality.

    The goal is not to award points for every fashionable keyword. A cluster
    only gets a strong score when company profile, sector linkage and/or latest
    news point to the same direction, and when we can identify the chain role.
    """

    sources = _growth_source_terms(profile, sectors, news, stock=stock)
    combined = " ".join(sources.values())
    profile_locked = bool((profile or {}).get("identity_locked"))
    sector_relevance = max(
        (
            {"核心": 1.0, "高": 0.9, "中高": 0.72, "中": 0.5}.get(str(item.get("relevance") or ""), 0.0)
            for item in sectors[:8]
        ),
        default=0.0,
    )
    profiles: List[Dict[str, Any]] = []
    for cluster in _HIGH_GROWTH_INDUSTRY_CLUSTERS:
        aliases = tuple(str(item) for item in cluster.get("aliases") or ())
        profile_hits = _matches_any(sources["profile"], aliases)
        sector_hits = _matches_any(sources["sector"], aliases)
        news_hits = _matches_any(sources["news"], aliases)
        all_hits = _dedupe_growth_strings(profile_hits + sector_hits + news_hits)

        chain_roles: List[str] = []
        for role, role_keywords in cluster.get("roles") or ():
            if _matches_any(combined, tuple(str(item) for item in role_keywords)):
                chain_roles.append(str(role))

        moat_terms = tuple(str(item) for item in cluster.get("moat_terms") or ())
        moat_hits = _matches_any(combined, moat_terms)
        if not all_hits and not chain_roles:
            continue
        # News is useful for validation, but it is too noisy to define a
        # company's industry identity by itself. Require at least company
        # profile or related-board evidence before assigning a high-growth
        # cluster; this prevents generic headlines from pulling consumer or
        # value stocks into AI/semiconductor themes.
        if not profile_hits and not sector_hits:
            continue

        confidence = 0.24
        if profile_hits:
            confidence += 0.3
        if sector_hits:
            confidence += 0.2
        if news_hits:
            confidence += 0.1
        if chain_roles:
            confidence += 0.08
        if moat_hits:
            confidence += 0.06
        if sector_relevance >= 0.72 and sector_hits:
            confidence += 0.06
        if profile_locked and profile_hits:
            confidence += 0.08
        confidence = _clamp(confidence, 0.25, 1.0)

        moat_quality = _clamp(
            float(cluster.get("base_moat") or 0.55)
            + min(len(moat_hits), 4) * 0.035
            + min(len(chain_roles), 2) * 0.035
            + (0.06 if profile_locked and profile_hits else 0.0),
            0.25,
            1.0,
        )
        profiles.append(
            {
                "label": str(cluster.get("label") or ""),
                "thesis": str(cluster.get("thesis") or ""),
                "base_space": float(cluster.get("base_space") or 0.72),
                "growth_floor_bonus": float(cluster.get("growth_floor_bonus") or 0.0),
                "moat_quality": moat_quality,
                "confidence": confidence,
                "matched_keywords": all_hits[:6],
                "chain_roles": chain_roles[:3],
                "moat_hits": moat_hits[:5],
                "source_hits": {
                    "profile": profile_hits[:4],
                    "sector": sector_hits[:4],
                    "news": news_hits[:4],
                },
            }
        )
    profiles.sort(
        key=lambda item: (
            float(item.get("base_space") or 0.0) * float(item.get("confidence") or 0.0)
            + float(item.get("moat_quality") or 0.0) * 0.18
        ),
        reverse=True,
    )
    return profiles[:3]


def _growth_profile_labels(growth_inputs: Dict[str, Any]) -> str:
    profiles = growth_inputs.get("growth_profiles") or []
    labels = [str(item.get("label") or "") for item in profiles[:2] if item.get("label")]
    return "、".join(labels)


def _growth_chain_roles(growth_inputs: Dict[str, Any]) -> str:
    roles: List[str] = []
    for profile in growth_inputs.get("growth_profiles") or []:
        roles.extend(str(item) for item in profile.get("chain_roles") or [])
    roles = _dedupe_growth_strings([item for item in roles if item])
    return "、".join(roles[:3])


def _growth_validation_text(growth_inputs: Dict[str, Any]) -> str:
    profiles = growth_inputs.get("growth_profiles") or []
    if not profiles:
        return "暂按主营资料、板块联动和资讯交叉验证"
    top = profiles[0]
    source_hits = top.get("source_hits") or {}
    sources: List[str] = []
    if source_hits.get("profile"):
        sources.append("公司资料")
    if source_hits.get("sector"):
        sources.append("关联板块")
    if source_hits.get("news"):
        sources.append("最新资讯")
    confidence = float(top.get("confidence") or 0.0)
    if confidence >= 0.78:
        level = "证据较强"
    elif confidence >= 0.58:
        level = "证据中等"
    else:
        level = "仍需验证"
    source_text = "、".join(sources) if sources else "公开资料"
    return f"{source_text}{level}"


def _growth_quality_inputs(
    profile: Optional[Dict[str, Any]],
    sectors: List[Dict[str, str]],
    financials: Optional[Dict[str, Any]],
    news: List[Dict[str, str]],
    stock: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score long-term growth from industry space, moat and execution.

    Growth is intentionally not a pure momentum score. Commercial users care
    most about whether the company sits in a large future market and whether it
    has a durable way to capture that market. Short-term price/volume signals
    only validate timing after this foundation is scored.
    """

    terms = _growth_context_terms(profile, sectors, stock=stock)
    growth_profiles = _growth_industry_profiles(profile, sectors, news, stock=stock)
    high_growth_hits = [keyword for keyword in _HIGH_GROWTH_INDUSTRY_KEYWORDS if _compact_query(keyword) in terms]
    moat_hits = [keyword for keyword in _MOAT_KEYWORDS if _compact_query(keyword) in terms]
    relevance_hits = [
        str(item.get("relevance") or "")
        for item in sectors[:6]
        if str(item.get("relevance") or "") in {"核心", "高", "中高"}
    ]

    industry_space = 0.42 + min(len(high_growth_hits), 4) * 0.105
    if growth_profiles:
        top_profile = growth_profiles[0]
        mapped_space = (
            0.36
            + float(top_profile.get("base_space") or 0.0) * float(top_profile.get("confidence") or 0.0) * 0.52
            + min(len(growth_profiles) - 1, 2) * 0.035
        )
        industry_space = max(industry_space, mapped_space)
    if profile and profile.get("identity_locked"):
        industry_space += 0.045
    moat_score = 0.34 + min(len(moat_hits), 5) * 0.07 + len(relevance_hits[:3]) * 0.045
    if growth_profiles:
        top_profile = growth_profiles[0]
        mapped_moat = (
            0.31
            + float(top_profile.get("moat_quality") or 0.0) * float(top_profile.get("confidence") or 0.0) * 0.48
            + min(len(top_profile.get("chain_roles") or []), 2) * 0.045
        )
        moat_score = max(moat_score, mapped_moat)
    if profile and profile.get("identity_locked"):
        moat_score += 0.045

    commercial_execution = 0.46
    if financials:
        revenue_growth = _safe_float(financials.get("revenue_growth_qoq"))
        net_profit_growth = _safe_float(financials.get("net_profit_growth_qoq"))
        roe = _safe_float(financials.get("roe"))
        net_margin = _safe_float(financials.get("net_margin"))
        operating_cash_flow = _safe_float(financials.get("operating_cash_flow_per_share"))
        commercial_execution += _bounded_signal((revenue_growth or 0.0) - 5.0, 55.0, 0.16)
        commercial_execution += _bounded_signal((net_profit_growth or 0.0) - 5.0, 70.0, 0.14)
        commercial_execution += _bounded_signal((roe or 0.0) - 8.0, 28.0, 0.08)
        commercial_execution += _bounded_signal((net_margin or 0.0) - 6.0, 24.0, 0.06)
        if operating_cash_flow is not None:
            commercial_execution += 0.06 if operating_cash_flow > 0 else -0.09

    news_balance = _news_balance_score(news)
    commercial_execution += news_balance * 0.08

    # These are evidence-strength scores, not literal claims that a company has
    # "100% industry space" or "100% moat". Even strong strategic themes keep
    # headroom for uncertainty, competition, and commercial execution risk.
    industry_space = _clamp(industry_space, 0.2, 0.88)
    moat_score = _clamp(moat_score, 0.15, 0.82)
    commercial_execution = _clamp(commercial_execution, 0.1, 0.86)
    growth_floor_bonus = 0.0
    for growth_profile in growth_profiles:
        growth_floor_bonus = max(
            growth_floor_bonus,
            float(growth_profile.get("growth_floor_bonus") or 0.0) * float(growth_profile.get("confidence") or 0.0),
        )
    growth_floor_bonus = _clamp(growth_floor_bonus, 0.0, 0.32)
    composite = _clamp(
        industry_space * 0.42 + moat_score * 0.34 + commercial_execution * 0.24,
        0.0,
        1.0,
    )
    return {
        "industry_space": industry_space,
        "moat": moat_score,
        "commercial_execution": commercial_execution,
        "composite": composite,
        "high_growth_hits": high_growth_hits[:4],
        "moat_hits": moat_hits[:4],
        "growth_profiles": growth_profiles,
        "growth_profile_labels": _growth_profile_labels({"growth_profiles": growth_profiles}),
        "chain_roles": _growth_chain_roles({"growth_profiles": growth_profiles}),
        "validation": _growth_validation_text({"growth_profiles": growth_profiles}),
        "growth_floor_bonus": growth_floor_bonus,
    }


def _growth_quality_description(growth_inputs: Dict[str, Any], risk_flags: List[str]) -> str:
    industry_level = _evidence_strength_label(float(growth_inputs.get("industry_space") or 0.0))
    moat_level = _evidence_strength_label(float(growth_inputs.get("moat") or 0.0))
    execution_level = _evidence_strength_label(float(growth_inputs.get("commercial_execution") or 0.0))
    profile_text = str(growth_inputs.get("growth_profile_labels") or "")
    role_text = str(growth_inputs.get("chain_roles") or "")
    validation_text = str(growth_inputs.get("validation") or "")
    if growth_inputs.get("growth_profiles"):
        theme_text = profile_text or "高成长赛道"
        moat_text = role_text or "产业链卡位"
    else:
        theme_text = "主营业务景气度"
        moat_text = "盈利质量、现金流和竞争格局"
    risk_text = f"；已扣除{ '、'.join(risk_flags[:3]) }等风险" if risk_flags else ""
    return (
        f"成长分优先看长期画像：赛道{theme_text}，产业链位置{moat_text}；"
        f"赛道证据{industry_level}、壁垒证据{moat_level}、兑现证据{execution_level}，"
        f"{validation_text or '再用趋势和资讯验证'}{risk_text}。"
    )


def _evidence_strength_label(value: float) -> str:
    if value >= 0.78:
        return "较强"
    if value >= 0.62:
        return "中上"
    if value >= 0.46:
        return "中等"
    if value >= 0.3:
        return "偏弱"
    return "较弱"


def _is_financial_institution(
    stock: Optional[Dict[str, Any]],
    sectors: List[Dict[str, str]],
    company_profile: Optional[Dict[str, Any]],
) -> bool:
    """Identify banks/insurers/brokers where industrial balance-sheet rules mislead."""

    profile = company_profile or {}
    text_parts = [
        str((stock or {}).get("name") or ""),
        str((stock or {}).get("code") or ""),
        str(profile.get("industry") or ""),
        str(profile.get("business") or ""),
        str(profile.get("business_model") or ""),
        str(profile.get("intro") or ""),
    ]
    for sector in sectors[:8]:
        text_parts.extend(
            [
                str(sector.get("name") or ""),
                str(sector.get("reason") or ""),
                str(sector.get("realtime_board") or ""),
            ]
        )
    compact = _compact_query(" ".join(text_parts))
    return any(
        keyword in compact
        for keyword in (
            "银行",
            "中资银行",
            "城商行",
            "农商行",
            "保险",
            "证券",
            "券商",
            "信托",
            "金融控股",
        )
    )


def _build_dynamic_scores(
    current_price: float,
    history: List[Dict[str, Any]],
    valuation: Dict[str, Any],
    news: List[Dict[str, str]],
    sectors: List[Dict[str, str]],
    financials: Optional[Dict[str, Any]] = None,
    stock: Optional[Dict[str, Any]] = None,
    company_profile: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    closes = _history_values(history, "close")
    volumes = _history_values(history, "volume")
    momentum_20 = _return_percent(current_price, closes[-21] if len(closes) >= 21 else None)
    momentum_60 = _return_percent(current_price, closes[-61] if len(closes) >= 61 else None)
    volatility = _annualized_volatility(closes)
    atr_percent = _average_true_range_percent(history)
    avg_5 = _mean(volumes[-5:])
    avg_20 = _mean(volumes[-20:])
    volume_ratio = avg_5 / avg_20 if avg_5 and avg_20 else None
    ma20 = _mean(closes[-20:])
    ma60 = _mean(closes[-60:])
    trend_quality = _trend_quality_score(
        current_price,
        closes,
        ma20=ma20,
        ma60=ma60,
        momentum_20=momentum_20,
        momentum_60=momentum_60,
        volatility=volatility,
        volume_ratio=volume_ratio,
    )
    low = float(valuation["low"])
    high = float(valuation["high"])
    span = high - low
    if span > 0:
        position_ratio = (current_price - low) / span
        if current_price < low:
            discount_bonus = min((low - current_price) / max(low, 0.01) * 8, 1.15)
            value_score = 6.9 + discount_bonus + trend_quality * 0.75
        elif current_price <= high:
            value_score = 7.35 - _clamp(position_ratio, 0.0, 1.0) * 2.05 + trend_quality * 0.55
        else:
            value_score = 4.55 - min((current_price - high) / max(high, 0.01) * 7, 2.2) + trend_quality * 0.35
    else:
        value_score = 5.0

    news_counts = _news_signal_counts(news)
    sector_change = _sector_average_change(sectors)
    positive_news = news_counts["positive"]
    risk_news = news_counts["risk"]
    sector_bonus = _clamp((sector_change or 0.0) / 3.0, -1.2, 1.2)
    momentum_20_value = momentum_20 if momentum_20 is not None else 0.0
    momentum_60_value = momentum_60 if momentum_60 is not None else 0.0
    volume_bonus = _clamp(((volume_ratio or 1.0) - 1.0) * 1.8, -0.8, 1.0)
    volatility_penalty = _clamp((volatility or 45.0) / 60.0 + (atr_percent or 3.0) / 12.0, 0.45, 2.6)
    normalized_news_balance = _news_balance_score(news)
    shareholder_hits = 0
    for item in news:
        compact_title = _compact_query(item.get("title", ""))
        if any(keyword in compact_title for keyword in ("分红", "派息", "回购", "回購", "增持")):
            shareholder_hits += 1
    is_st_stock = _is_special_treatment_stock(stock)
    is_financial_institution = _is_financial_institution(stock, sectors, company_profile)
    distress_flags = _distress_risk_flags(stock, company_profile, news)
    risk_adjustment = _fundamental_risk_adjustment(
        financials,
        is_st_stock=is_st_stock,
        is_financial_institution=is_financial_institution,
        distress_flags=distress_flags,
    )
    risk_flags = [str(item) for item in risk_adjustment.get("flags", [])]
    growth_inputs = _growth_quality_inputs(company_profile, sectors, financials, news, stock=stock)
    growth_floor_bonus = float(growth_inputs.get("growth_floor_bonus") or 0.0)

    growth_score = (
        2.35
        + float(growth_inputs["industry_space"]) * 2.35
        + float(growth_inputs["moat"]) * 1.75
        + float(growth_inputs["commercial_execution"]) * 1.45
        + trend_quality * 0.78
        + _bounded_signal(momentum_60_value, 85.0, 0.42)
        + _bounded_signal(momentum_20_value, 55.0, 0.28)
        + normalized_news_balance * 0.35
        + sector_bonus * 0.28
        - _clamp((volatility or 45.0) / 260.0, 0.0, 0.36)
    )
    industry_space = float(growth_inputs["industry_space"])
    moat_score = float(growth_inputs["moat"])
    commercial_execution = float(growth_inputs["commercial_execution"])
    strategic_growth_conviction = (
        industry_space * 0.5
        + moat_score * 0.34
        + commercial_execution * 0.16
    )
    if float(growth_inputs["industry_space"]) >= 0.72 and float(growth_inputs["moat"]) >= 0.68:
        long_term_growth_floor = (
            5.05
            + float(growth_inputs["industry_space"]) * 1.45
            + float(growth_inputs["moat"]) * 1.1
            + float(growth_inputs["commercial_execution"]) * 0.35
            + growth_floor_bonus
        )
        growth_score = max(growth_score, long_term_growth_floor)
    # Strategic growth should not be erased simply because an early-cycle
    # company is currently expensive or loss-making. We still keep profit and
    # finance scores low, but the growth score itself must reflect large future
    # markets, chain position, and moat evidence.
    if industry_space >= 0.8 and moat_score >= 0.58 and not is_st_stock:
        strategic_floor = (
            5.85
            + strategic_growth_conviction * 1.85
            + min(growth_floor_bonus, 0.45)
        )
        if growth_inputs.get("growth_profiles"):
            top_profile = (growth_inputs.get("growth_profiles") or [{}])[0]
            confidence = float(top_profile.get("confidence") or 0.0)
            strategic_floor += _clamp((confidence - 0.55) * 0.8, 0.0, 0.35)
        if distress_flags:
            strategic_floor -= 0.45
        growth_score = max(growth_score, min(strategic_floor, 8.7))
    profitability_score = (
        4.45
        + normalized_news_balance * 0.9
        + volume_bonus * 0.72
        + trend_quality * 0.65
        + (0.35 if ma20 and ma60 and ma20 >= ma60 else -0.25)
    )
    finance_score = (
        5.35
        + (0.35 if current_price >= (ma60 or current_price) else -0.28)
        + volume_bonus * 0.35
        + trend_quality * 0.35
        - volatility_penalty * 0.58
        - risk_news * 0.18
    )
    dividend_score = 0.8 + shareholder_hits * 2.0 + (0.4 if shareholder_hits and positive_news >= risk_news else 0.0)
    score_status = {
        "profitability": "computed_proxy_until_financials_ready",
        "finance": "computed_proxy_until_balance_sheet_ready",
        "dividend": "computed_from_shareholder_return_news",
    }
    score_descriptions = {
        "profitability": "用资讯情绪、量价参与度和趋势结构生成代理评分，财报细项未读取时不过度拔高。",
        "finance": "用波动率、均线结构和风险资讯生成财务安全代理评分。",
        "dividend": "根据最新资讯中分红、派息、回购、增持信号动态计算；未读取到则保持低分。",
    }
    if financials:
        net_margin = _safe_float(financials.get("net_margin"))
        net_profit = _safe_float(financials.get("net_profit"))
        roe = _safe_float(financials.get("roe"))
        roa = _safe_float(financials.get("roa"))
        net_profit_growth = _safe_float(financials.get("net_profit_growth_qoq"))
        operating_cash_flow = _safe_float(financials.get("operating_cash_flow_per_share"))
        book_value_per_share = _safe_float(financials.get("book_value_per_share"))
        eps = _safe_float(financials.get("eps"))
        pb = _safe_float(financials.get("pb"))
        pe = _safe_float(financials.get("pe"))
        debt_ratio = _safe_float(financials.get("debt_ratio"))
        dividend_yield = _safe_float(financials.get("dividend_yield_ttm"))
        dividend_payout = _safe_float(financials.get("dividend_payout_ratio"))
        dividend_per_share = _safe_float(financials.get("dividend_per_share_ttm"))
        dividend_events = int(_safe_float(financials.get("dividend_events_ttm")) or 0)
        if pb is None and book_value_per_share and book_value_per_share > 0:
            pb = current_price / book_value_per_share
        derived_dividend_yield = dividend_yield
        if derived_dividend_yield is None and dividend_per_share and current_price > 0:
            derived_dividend_yield = dividend_per_share / current_price * 100.0
        derived_dividend_payout = dividend_payout
        if derived_dividend_payout is None and dividend_per_share and eps and eps > 0:
            derived_dividend_payout = _clamp(dividend_per_share / eps * 100.0, 0.0, 180.0)

        profitability_score = (
            5.0
            + (net_margin or 0.0) / 18.0
            + (roe or 0.0) / 25.0
            + (roa or 0.0) / 22.0
            + (net_profit_growth or 0.0) / 120.0
        )
        debt_penalty = 0.0 if is_financial_institution else _clamp(((debt_ratio or 45.0) - 60.0) / 35.0, 0.0, 1.2)
        finance_score = (
            5.0
            + (1.0 if operating_cash_flow and operating_cash_flow > 0 else -1.0)
            + (0.6 if (pe and pe > 0) or (eps and eps > 0) else -0.6)
            - _clamp(((pb or 0.0) - 8.0) / 18.0, 0.0, 2.5)
            + (0.4 if book_value_per_share and book_value_per_share > 0 else -0.4)
            - debt_penalty
        )
        if is_financial_institution:
            # 银行、保险、券商的资产负债率和ROA天然不同于制造业。
            # 用盈利为正、净利率、低PB、正每股净资产和分红能力综合衡量，
            # 避免把行业会计结构误判成财务风险。
            bank_profitability = (
                4.65
                + _bounded_signal((net_margin or 0.0) - 18.0, 30.0, 1.2)
                + _bounded_signal((roe or 0.0) * (4.0 if (roe or 0.0) < 5.0 else 1.0) - 8.0, 18.0, 0.9)
                + (0.65 if net_profit is not None and net_profit > 0 else -1.2)
                + (0.35 if eps is not None and eps > 0 else -0.45)
            )
            profitability_score = max(profitability_score, bank_profitability)
            finance_score = (
                5.15
                + (0.75 if book_value_per_share and book_value_per_share > 0 else -0.7)
                + _bounded_signal(1.1 - (pb or 1.1), 0.8, 0.85)
                + (0.45 if derived_dividend_yield is not None and derived_dividend_yield >= 2.0 else 0.0)
                + (0.35 if eps is not None and eps > 0 else -0.5)
                - (0.55 if risk_news >= 3 else 0.0)
            )
        dividend_score = (
            0.6
            + _clamp((derived_dividend_yield or 0.0) * 1.45, 0.0, 5.2)
            + _clamp((derived_dividend_payout or 0.0) / 38.0, 0.0, 2.2)
            + _clamp(dividend_events * 0.35, 0.0, 0.8)
        )
        if operating_cash_flow is not None and operating_cash_flow > 0:
            dividend_score += 0.45
        if debt_ratio is not None and debt_ratio < 35:
            dividend_score += 0.35
        if net_margin is not None and net_margin > 20:
            dividend_score += 0.35
        if derived_dividend_yield is not None:
            dividend_floor_allowed = (
                not is_st_stock
                and not (net_profit is not None and net_profit < 0)
                and not (eps is not None and eps < 0)
                and not (operating_cash_flow is not None and operating_cash_flow < 0)
            )
            if dividend_floor_allowed and derived_dividend_yield >= 3.0:
                dividend_score = max(dividend_score, 7.2)
            elif dividend_floor_allowed and derived_dividend_yield >= 2.0:
                dividend_score = max(dividend_score, 6.4)
            elif dividend_floor_allowed and derived_dividend_yield >= 1.2:
                dividend_score = max(dividend_score, 5.2)
        if is_st_stock:
            dividend_score = min(dividend_score, 2.8)
        elif (net_profit is not None and net_profit < 0) or (eps is not None and eps < 0):
            dividend_score = min(dividend_score, 3.6)
        elif operating_cash_flow is not None and operating_cash_flow < 0:
            dividend_score = min(dividend_score, 4.2)
        score_status = {
            "profitability": "computed_from_financial_indicators",
            "finance": "computed_from_financial_indicators",
            "dividend": "computed_from_dividend_and_shareholder_return_indicators",
        }
        score_descriptions = {
            "profitability": (
                f"销售净利率{net_margin:.1f}%、ROE{roe:.1f}%、ROA{roa:.1f}%，"
                "由财务指标直接计算。"
                if net_margin is not None and roe is not None and roa is not None
                else "基于已读取财务指标计算。"
            ),
            "finance": (
                f"经营现金流/股{operating_cash_flow:.3f}，PB{pb:.2f}，资产负债率{debt_ratio:.1f}%，由财务指标直接计算。"
                if operating_cash_flow is not None and pb is not None and debt_ratio is not None
                else "基于已读取财务指标计算。"
            ),
            "dividend": (
                f"近一年现金分红约每股{dividend_per_share:.3f}元，按当前价折算股息率约{derived_dividend_yield:.2f}%，并结合现金流、负债率和盈利质量计算。"
                if dividend_per_share is not None and derived_dividend_yield is not None
                else (
                    f"股息率TTM{derived_dividend_yield:.2f}%、派息比率{derived_dividend_payout:.1f}%，由分红指标计算。"
                    if derived_dividend_yield is not None and derived_dividend_payout is not None
                    else "未读取到现金分红或TTM股息率，分红评分保持低位。"
                )
            ),
        }

    value_score -= float(risk_adjustment.get("value_penalty") or 0.0)
    growth_score -= float(risk_adjustment.get("growth_penalty") or 0.0)
    profitability_score -= float(risk_adjustment.get("profitability_penalty") or 0.0)
    finance_score -= float(risk_adjustment.get("finance_penalty") or 0.0)
    if (
        not is_st_stock
        and industry_space >= 0.85
        and moat_score >= 0.82
        and not distress_flags
    ):
        growth_score = max(
            growth_score,
            7.2 + min(growth_floor_bonus, 0.35),
        )
    value_score = _apply_score_cap(value_score, risk_adjustment.get("value_cap"))
    growth_score = _apply_score_cap(growth_score, risk_adjustment.get("growth_cap"))
    profitability_score = _apply_score_cap(profitability_score, risk_adjustment.get("profitability_cap"))
    finance_score = _apply_score_cap(finance_score, risk_adjustment.get("finance_cap"))
    has_strategic_growth_profile = bool(growth_inputs.get("growth_profiles")) and industry_space >= 0.78 and moat_score >= 0.55
    severe_risk = any(
        flag in {"清盘/退市/停牌风险", "ST/退市风险", "持续经营/审计风险"}
        for flag in risk_flags
    )
    if has_strategic_growth_profile and not is_st_stock and not severe_risk:
        post_risk_growth_floor = 6.45 + strategic_growth_conviction * 0.82 + min(growth_floor_bonus, 0.35)
        if profitability_score <= 1.2:
            post_risk_growth_floor -= 0.28
        if finance_score < 4.0:
            post_risk_growth_floor -= 0.18
        growth_score = max(growth_score, min(post_risk_growth_floor, 7.8))

    stock_market = str((stock or {}).get("market") or "")
    low_price_distress = (
        stock_market == "A股"
        and current_price < 2.0
        and not has_strategic_growth_profile
        and not is_st_stock
    )
    if low_price_distress:
        if "低价股流动性风险" not in risk_flags:
            risk_flags.append("低价股流动性风险")
        if "低价股流动性风险" not in distress_flags:
            distress_flags.append("低价股流动性风险")
        value_score = min(value_score, 5.4)
        growth_score = min(growth_score, 5.0)
        finance_score = min(finance_score, 5.5)
    if risk_flags:
        risk_text = "、".join(risk_flags[:4])
        value_description = f"先看当前价相对{valuation['label']}的位置，再叠加{risk_text}等基本面风险折扣；低价不直接等于高价值。"
        value_status = "computed_from_valuation_and_fundamental_risk"
        value_inputs = ["current_price", "dynamic_valuation_range", "financial_risk_flags", "st_risk"]
    else:
        value_description = f"根据当前价相对{valuation['label']}的位置，并结合趋势质量和基本面安全边际动态计算。"
        value_status = "computed_from_valuation_position_and_quality"
        value_inputs = ["current_price", "dynamic_valuation_range", "trend_quality"]
    valuation_cost = _valuation_cost_effectiveness(
        str((stock or {}).get("code") or ""),
        current_price,
        valuation,
        financials,
        is_st_stock=is_st_stock,
        distress_flags=distress_flags,
    )

    return [
        {
            "label": "价值",
            "score": _clamp_score(value_score),
            "description": value_description,
            "source": "computed",
            "status": value_status,
            "inputs": value_inputs,
        },
        {
            "label": "估值性价比",
            "score": valuation_cost["score"],
            "description": valuation_cost["description"],
            "source": "computed",
            "status": valuation_cost["status"],
            "inputs": valuation_cost["inputs"],
        },
        {
            "label": "成长",
            "score": _clamp_score(growth_score),
            "description": _growth_quality_description(growth_inputs, risk_flags),
            "source": "computed",
            "status": "computed_from_industry_moat_execution_trend",
            "inputs": ["high_growth_industry_mapping", "chain_position", "moat", "commercial_execution", "trend_quality", "news_tone"],
        },
        {
            "label": "盈利能力",
            "score": _clamp_score(profitability_score),
            "description": score_descriptions["profitability"],
            "source": "computed",
            "status": score_status["profitability"],
            "inputs": ["net_margin", "roe", "roa", "net_profit_growth"] if financials else ["news_tone", "volume_ratio", "ma20", "ma60"],
        },
        {
            "label": "财务",
            "score": _clamp_score(finance_score),
            "description": score_descriptions["finance"],
            "source": "computed",
            "status": score_status["finance"],
            "inputs": ["operating_cash_flow_per_share", "pe", "pb", "book_value_per_share"] if financials else ["volatility", "volume_ratio", "ma60", "risk_news"],
        },
        {
            "label": "分红",
            "score": _clamp_score(dividend_score),
            "description": score_descriptions["dividend"],
            "source": "computed",
            "status": score_status["dividend"],
            "inputs": ["dividend_per_share_ttm", "dividend_yield_ttm", "dividend_payout_ratio", "cash_flow", "debt_ratio"] if financials else ["dividend_news", "buyback_news", "holding_increase_news"],
        },
    ]


def _build_dynamic_decision_reasons(
    current_price: float,
    valuation: Dict[str, Any],
    quant_metrics: List[Dict[str, str]],
    sectors: List[Dict[str, str]],
    news: List[Dict[str, str]],
    stock: Optional[Dict[str, Any]] = None,
    company_profile: Optional[Dict[str, Any]] = None,
    sniper_points: Optional[List[Dict[str, Any]]] = None,
    financials: Optional[Dict[str, Any]] = None,
    scores: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    profile = company_profile or {}
    stock_info = stock or {}
    points = sniper_points or []
    business_reason = _company_basic_reason(stock_info, profile, sectors)
    return [
        {
            "title": "推荐建议",
            "description": _short_text(
                _build_recommendation_reason(current_price, valuation, quant_metrics, points),
                190,
            ),
        },
        {
            "title": "公司基本面",
            "description": business_reason,
        },
        {
            "title": "长期成长逻辑",
            "description": _build_growth_logic_reason(
                sectors,
                news,
                company_profile=profile,
                financials=financials,
                scores=scores,
                stock=stock_info,
            ),
        },
        {
            "title": "动态估值区间",
            "description": _short_text(_build_valuation_reason(current_price, valuation), 190),
        },
        {
            "title": "狙击点位",
            "description": _short_text(_build_sniper_reason(points, valuation), 190),
        },
        {
            "title": "趋势与风险",
            "description": _build_trend_risk_reason(current_price, valuation, quant_metrics, sectors, news),
        },
    ]


def _first_valid_news_date(news: List[Dict[str, str]]) -> str:
    for item in sorted(news, key=lambda value: value.get("date", ""), reverse=True):
        date = str(item.get("date") or "").strip()
        if date and date != "待读取":
            return date
    return "待读取"


def _has_pending_items(items: List[Dict[str, Any]], key: str = "data_status") -> bool:
    if not items:
        return True
    return any(str(item.get(key) or "").strip() == "pending" for item in items)


def _score_data_depth(scores: List[Dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in scores}
    if any("financial_indicators" in status or "dividend_indicators" in status for status in statuses):
        return "已读取财务指标"
    if any("proxy" in status for status in statuses):
        return "部分为代理评分"
    return "量化计算"


def _build_data_audit(pack: Dict[str, Any], recommendation: CommercialAiRecommendation) -> List[Dict[str, str]]:
    """Create user-facing provenance rows for the analysis page.

    These rows deliberately distinguish realtime data, calculated model output,
    AI-generated copy, and pending fields. They are not investment advice; they
    are the guardrails that keep the UI honest about where each number came from.
    """

    valuation = pack.get("valuation") or {}
    quote_status = str(pack.get("_quote_status") or "")
    quote_source = str(pack.get("_quote_source") or "待读取")
    quote_updated_at = str(pack.get("_quote_updated_at") or "待读取")
    sector_status = str(pack.get("_sector_status") or "pending")
    news_status = str(pack.get("_news_status") or "pending")
    sector_source = str(pack.get("_sector_source") or "待读取")
    news_source = str(pack.get("_news_source") or "待读取")
    news_summary = pack.get("_news_summary") or {}
    history_count = len(pack.get("_history") or [])
    financials = pack.get("_financials")

    return [
        {
            "section": "当前价",
            "classification": "realtime" if quote_status == "realtime_or_latest" else "pending",
            "status": "ok" if quote_status == "realtime_or_latest" else "pending",
            "evidence": f"{quote_source} · {quote_updated_at}",
            "action": "价格不可读时阻断强结论和点位计划。",
        },
        {
            "section": "估值区间",
            "classification": "computed",
            "status": "ok" if valuation.get("status") else "partial",
            "evidence": f"由当前价、近{history_count}条日线、稳定价格锚、分位数支撑阻力、MA20/MA60/MA120、ATR和波动率计算。",
            "action": "这是算法估值区间，不等同于券商目标价。",
        },
        {
            "section": "点位计划",
            "classification": "computed",
            "status": "ok" if pack.get("sniper_points") else "pending",
            "evidence": "由AI估值区间、ATR波动、近30日支撑、MA20/MA60和阶段高点生成。",
            "action": "先看失效位，再看确认位，避免无纪律追高。",
        },
        {
            "section": "六维评分",
            "classification": "computed" if financials else "model_assumption",
            "status": "ok" if pack.get("scores") else "pending",
            "evidence": _score_data_depth(pack.get("scores") or []),
            "action": "财报指标不足时保持代理评分口径，不包装成完整基本面评分。",
        },
        {
            "section": "关联板块",
            "classification": "realtime" if sector_status == "latest_public_source" else "pending",
            "status": "ok" if sector_status == "latest_public_source" else "pending",
            "evidence": sector_source,
            "action": "需要同时看业务相关度和实时板块涨跌。",
        },
        {
            "section": "最新相关资讯",
            "classification": "realtime" if news_status == "latest_public_source" else "pending",
            "status": "ok" if news_status == "latest_public_source" else "pending",
            "evidence": (
                f"{news_source} · 资讯池{news_summary.get('pool_count', 0)}条 · "
                f"最新日期 {news_summary.get('latest_date') or _first_valid_news_date(pack.get('news') or [])}"
            ),
            "action": f"页面精选展示最新{news_summary.get('display_count', len(pack.get('news') or []))}条，并标记利好、利空或中性。",
        },
        {
            "section": "AI结论",
            "classification": "ai_generated" if recommendation.source == "deepseek" else "computed",
            "status": recommendation.status,
            "evidence": "AI决策引擎接收结构化量化决策包；不接收聊天上下文。",
            "action": "AI只输出短结论，必须服从行情、量化、板块、资讯证据。",
        },
    ]


def _hypothesis_status_from_value(
    value: Optional[str],
    *,
    positive_values: set[str],
    risk_values: set[str],
    pending_values: set[str] | None = None,
) -> str:
    text = str(value or "").strip()
    pending_values = pending_values or {"待读取"}
    if not text or text in pending_values:
        return "待读取"
    if text in positive_values:
        return "成立"
    if text in risk_values:
        return "风险"
    return "观察中"


def _build_investment_hypotheses(pack: Dict[str, Any]) -> List[Dict[str, str]]:
    valuation = pack.get("valuation") or {}
    quant_metrics = pack.get("quant_metrics") or []
    sniper_points = pack.get("sniper_points") or []
    sectors = pack.get("related_sectors") or []
    news = pack.get("_news_pool") or pack.get("news") or []
    unit = str(valuation.get("currency_label") or "元/股").split("/")[0]
    price_position = str(valuation.get("price_position") or "待读取")
    current_price = _safe_float(valuation.get("current_price"))
    low = _safe_float(valuation.get("low"))
    high = _safe_float(valuation.get("high"))
    ma_structure = (_metric_by_label(quant_metrics, "均线结构") or {}).get("value", "待读取")
    volume_state = (_metric_by_label(quant_metrics, "量价确认") or {}).get("percentile", "待读取")
    news_counts = _news_signal_counts(news)
    sector_change = _sector_average_change(sectors)
    invalid_point = next((item for item in sniper_points if item.get("label") == "失效位"), None)
    confirm_point = next((item for item in sniper_points if item.get("label") == "确认位"), None)

    if current_price is None or low is None or high is None:
        valuation_status = "待读取"
    elif current_price <= high:
        valuation_status = "成立" if current_price <= low or "下沿" in price_position else "观察中"
    else:
        valuation_status = "风险"

    trend_status = _hypothesis_status_from_value(
        ma_structure,
        positive_values={"多头", "修复"},
        risk_values={"承压"},
    )
    if trend_status == "成立" and volume_state == "缩量":
        trend_status = "观察中"

    if news_counts["risk"] > news_counts["positive"] and news_counts["risk"] > 0:
        event_status = "风险"
    elif news_counts["positive"] > 0 or (sector_change is not None and sector_change >= 0):
        event_status = "成立"
    elif _has_pending_items(news):
        event_status = "待读取"
    else:
        event_status = "观察中"

    invalid_text = (
        f"{float(invalid_point['price']):.3f}{unit}"
        if invalid_point and isinstance(invalid_point.get("price"), (int, float))
        else "待读取"
    )
    confirm_text = (
        f"{float(confirm_point['price']):.3f}{unit}"
        if confirm_point and isinstance(confirm_point.get("price"), (int, float))
        else "待读取"
    )

    return [
        {
            "title": "估值赔率假设",
            "status": valuation_status,
            "evidence": (
                f"当前价{current_price:.3f}{unit}，AI估值区间{low:.3f}-{high:.3f}{unit}，{price_position}。"
                if current_price is not None and low is not None and high is not None
                else "待读取"
            ),
            "check_next": "观察价格是否继续停留在合理区间下沿，且没有基本面恶化资讯。",
            "invalidated_by": f"持续高于{high:.3f}{unit}后仍无量价确认，或估值区间输入数据失效。" if high else "待读取",
        },
        {
            "title": "趋势确认假设",
            "status": trend_status,
            "evidence": f"均线结构{ma_structure}，量价状态{volume_state}。",
            "check_next": f"若放量站上确认位{confirm_text}，趋势修复可信度提升。",
            "invalidated_by": "缩量反弹或均线结构重新转弱。",
        },
        {
            "title": "最新行业资讯假设",
            "status": event_status,
            "evidence": (
                f"资讯情绪：利好{news_counts['positive']}条、利空{news_counts['risk']}条、中性{news_counts['neutral']}条；"
                f"板块平均变化{sector_change:+.2f}%。"
                if sector_change is not None
                else f"资讯情绪：利好{news_counts['positive']}条、利空{news_counts['risk']}条、中性{news_counts['neutral']}条；板块待读取。"
            ),
            "check_next": "继续跟踪最新公告、行业催化和相关板块涨跌是否同向。",
            "invalidated_by": "出现连续利空资讯，或核心相关板块明显跑输。",
        },
        {
            "title": "风险纪律假设",
            "status": "观察中" if invalid_text != "待读取" else "待读取",
            "evidence": f"当前失效位设为{invalid_text}，用于控制短线承接失败风险。",
            "check_next": "优先观察失效位是否被有效跌破，再决定是否降级。",
            "invalidated_by": f"跌破{invalid_text}后无法快速收复，先降低关注优先级。" if invalid_text != "待读取" else "待读取",
        },
    ]


def _refresh_dynamic_sections(pack: Dict[str, Any]) -> None:
    valuation = pack["valuation"]
    current_price = float(valuation["current_price"])
    history = pack.get("_history") or []
    decision_news = pack.get("_news_pool") or pack.get("news") or []
    if history:
        stock = pack.get("stock") or {}
        valuation.update(
            _derive_dynamic_valuation(
                current_price,
                history,
                str(stock.get("market") or "A股"),
                news=decision_news,
                sectors=pack.get("related_sectors") or [],
                financials=pack.get("_financials"),
            )
        )
        valuation["marker_percent"] = round(
            _marker_percent(
                float(valuation["current_price"]),
                float(valuation["low"]),
                float(valuation["high"]),
            ),
            2,
        )
        current_price = float(valuation["current_price"])
        pack["sniper_points"] = _derive_sniper_points(current_price, history, valuation)
    pack["scores"] = _build_dynamic_scores(
        current_price,
        history,
        valuation,
        decision_news,
        pack.get("related_sectors") or [],
        pack.get("_financials"),
        stock=pack.get("stock") or {},
        company_profile=pack.get("_company_profile") or {},
    )
    pack["decision_reasons"] = _build_dynamic_decision_reasons(
        current_price,
        valuation,
        pack.get("quant_metrics") or [],
        pack.get("related_sectors") or [],
        decision_news,
        pack.get("stock") or {},
        pack.get("_company_profile") or {},
        pack.get("sniper_points") or [],
        pack.get("_financials"),
        pack.get("scores") or [],
    )


def _build_live_quant_metrics(current_price: float, history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    closes = _history_values(history, "close")
    lows = _history_values(history, "low")
    highs = _history_values(history, "high")
    volumes = _history_values(history, "volume")
    momentum_20 = _return_percent(current_price, closes[-21] if len(closes) >= 21 else None)
    momentum_60 = _return_percent(current_price, closes[-61] if len(closes) >= 61 else None)
    volatility = _annualized_volatility(closes)
    avg_5 = _mean(volumes[-5:])
    avg_20 = _mean(volumes[-20:])
    volume_ratio = avg_5 / avg_20 if avg_5 and avg_20 else None
    position = _range_position(current_price, lows, highs)
    ma20 = _mean(closes[-20:])
    ma60 = _mean(closes[-60:])

    if momentum_20 is None:
        momentum_label = "待读取"
    elif momentum_20 >= 10:
        momentum_label = "强势"
    elif momentum_20 >= 0:
        momentum_label = "改善"
    else:
        momentum_label = "转弱"

    if volatility is None:
        volatility_label = "待读取"
    elif volatility >= 80:
        volatility_label = "很高"
    elif volatility >= 45:
        volatility_label = "偏高"
    else:
        volatility_label = "适中"

    if volume_ratio is None:
        volume_label = "待读取"
    elif volume_ratio >= 1.35:
        volume_label = "放量"
    elif volume_ratio >= 0.9:
        volume_label = "正常"
    else:
        volume_label = "缩量"

    if position is None:
        position_label = "待读取"
    elif position <= 30:
        position_label = "低位"
    elif position >= 70:
        position_label = "高位"
    else:
        position_label = "中位"

    trend_label = "待读取"
    if ma20 and ma60:
        if current_price >= ma20 >= ma60:
            trend_label = "多头"
        elif current_price >= ma20:
            trend_label = "修复"
        else:
            trend_label = "承压"

    metrics = [
        {
            "label": "20日动量",
            "value": _format_signed_percent(momentum_20),
            "percentile": momentum_label,
            "interpretation": "根据最近日线收盘价计算，衡量短线趋势强弱。",
        },
        {
            "label": "60日动量",
            "value": _format_signed_percent(momentum_60),
            "percentile": "中期",
            "interpretation": "用于观察中期趋势是否仍在修复轨道内。",
        },
        {
            "label": "年化波动率",
            "value": f"{volatility:.1f}%" if volatility is not None else "待读取",
            "percentile": volatility_label,
            "interpretation": "基于近60个交易日对数收益率估算，波动越高越需要点位纪律。",
        },
        {
            "label": "量价确认",
            "value": _format_ratio(volume_ratio),
            "percentile": volume_label,
            "interpretation": "近5日成交量相对近20日均量，判断资金参与度。",
        },
        {
            "label": "120日位置",
            "value": f"{position:.0f}%" if position is not None else "待读取",
            "percentile": position_label,
            "interpretation": "当前价在近120日高低区间中的相对位置。",
        },
        {
            "label": "均线结构",
            "value": trend_label,
            "percentile": "趋势",
            "interpretation": "比较当前价、20日均线与60日均线，识别多头、承压或转弱状态。",
        },
    ]
    for metric in metrics:
        metric["source"] = "computed"
        metric["status"] = "pending" if metric.get("value") == "待读取" else "computed_from_history"
    return metrics


def _derive_sniper_points(
    current_price: float,
    history: List[Dict[str, Any]],
    valuation: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    lows = _history_values(history, "low")
    highs = _history_values(history, "high")
    closes = _history_values(history, "close")
    stable_anchor = _stable_price_anchor(current_price, closes)
    recent_low = (_quantile(lows[-30:], 0.18) if lows else None) or current_price * 0.88
    recent_high = (_quantile(highs[-30:], 0.82) if highs else None) or current_price * 1.12
    swing_high = (_quantile(highs[-90:], 0.86) if highs else None) or recent_high
    ma20 = _mean(closes[-20:])
    ma60 = _mean(closes[-60:])
    volatility = _annualized_volatility(closes)
    atr_percent = _average_true_range_percent(history)
    atr_price = current_price * _clamp((atr_percent or 3.0) / 100.0, 0.012, 0.095)
    valuation_low = _safe_float((valuation or {}).get("low")) or stable_anchor * 0.9
    valuation_high = _safe_float((valuation or {}).get("high")) or stable_anchor * 1.16

    if current_price < valuation_low:
        focus_low = max(current_price - atr_price * 0.45, recent_low * 0.98)
        focus_high = min(valuation_low * 1.015, current_price + atr_price * 1.15)
    elif current_price <= valuation_high:
        range_span = max(valuation_high - valuation_low, current_price * 0.08)
        range_position = (current_price - valuation_low) / range_span
        if range_position <= 0.35:
            focus_low = max(valuation_low * 0.985, current_price - atr_price * 0.85, recent_low * 0.98)
            focus_high = min(current_price + atr_price * 0.75, valuation_low + range_span * 0.42)
        else:
            pullback_anchor = _weighted_average([(valuation_low, 0.55), (stable_anchor, 0.45)]) or valuation_low
            focus_low = max(pullback_anchor - atr_price * 0.45, valuation_low * 0.98)
            focus_high = min(pullback_anchor + atr_price * 0.9, current_price * 0.995)
    else:
        focus_low = max(valuation_high - atr_price * 1.4, valuation_low)
        focus_high = min(valuation_high * 1.015, current_price - atr_price * 0.35)
    if focus_high <= focus_low:
        focus_low = min(current_price, focus_low)
        focus_high = max(current_price + atr_price * 0.8, focus_low * 1.018)
    confirm = max(
        valuation_low * 1.02,
        current_price + atr_price * 1.8,
        ma20 * 1.01 if ma20 else 0,
        ma60 * 1.04 if ma60 else 0,
        min(swing_high, valuation_high * 1.02, stable_anchor * 1.18),
    )
    if volatility and volatility >= 80:
        invalid_buffer = 1.9
    elif volatility and volatility >= 45:
        invalid_buffer = 1.55
    else:
        invalid_buffer = 1.25
    invalid = min(current_price - atr_price * invalid_buffer, recent_low * 0.985, valuation_low * 0.92)
    invalid = max(invalid, current_price * 0.72)

    return [
        {
            "label": "关注区",
            "price": _market_price_round((focus_low + focus_high) / 2.0),
            "description": f"{focus_low:.3f}-{focus_high:.3f}附近观察承接，不追高。",
            "source": "computed",
            "status": "computed_from_atr_valuation_support",
        },
        {
            "label": "确认位",
            "price": _market_price_round(confirm),
            "description": "放量站上确认位后，趋势修复可信度提升。",
            "source": "computed",
            "status": "computed_from_atr_ma_valuation",
        },
        {
            "label": "失效位",
            "price": _market_price_round(invalid),
            "description": "跌破失效位说明短线承接失败，需要降级观察。",
            "source": "computed",
            "status": "computed_from_atr_support_risk_rule",
        },
    ]


def _merge_live_market_data(pack: Dict[str, Any], market_data: Dict[str, Any]) -> None:
    quote = market_data.get("quote") or {}
    history = market_data.get("history") or []
    current_price = quote.get("price")
    if not isinstance(current_price, (int, float)) or current_price <= 0:
        return

    stock = pack["stock"]
    if quote.get("name"):
        quote_name = _clean_realtime_stock_name(quote["name"]) or str(quote["name"]).strip()
        current_name = str(stock.get("name") or "").strip()
        code_name = str(stock.get("code") or "").strip()
        if quote_name and (not current_name or current_name == code_name or len(quote_name) > len(current_name)):
            stock["name"] = quote_name
    if quote.get("currency"):
        stock["currency"] = "HKD" if quote["currency"] == "HKD" else stock.get("currency", quote["currency"])
    valuation = pack["valuation"]
    valuation.update(_derive_dynamic_valuation(float(current_price), history, stock["market"]))

    pack["quant_metrics"] = _build_live_quant_metrics(float(current_price), history)
    pack["sniper_points"] = _derive_sniper_points(float(current_price), history, valuation)
    pack["_quote_source"] = market_data.get("provider", "行情引擎")
    pack["_quote_status"] = "realtime_or_latest"
    pack["_quote_updated_at"] = str(quote.get("update_time") or _now_iso())
    pack["_provider_symbol"] = market_data.get("provider_symbol")
    pack["_history"] = history
    _refresh_dynamic_sections(pack)


def _catalog_identity(code: str) -> Optional[Dict[str, Any]]:
    normalized = _compact_query(code)
    if not normalized:
        return None
    try:
        normalized_code = _normalize_code(code)
    except HTTPException:
        normalized_code = code
    keys = {
        normalized,
        _compact_query(normalized_code),
        _compact_query(normalized_code.replace(".SH", ".SS")),
    }
    if normalized_code.startswith("HK"):
        digits = normalized_code[2:].lstrip("0") or normalized_code[2:]
        keys.update({_compact_query(digits), _compact_query(f"{digits}.hk"), _compact_query(f"hk{digits}")})

    for item in _load_stock_catalog():
        item_keys = {
            _compact_query(item.get("code", "")),
            _compact_query(item.get("canonical_code", "")),
            _compact_query(item.get("display_code", "")),
        }
        if item.get("market") == "H股":
            digits = str(item.get("display_code", "")).lstrip("0") or str(item.get("display_code", ""))
            item_keys.update({_compact_query(digits), _compact_query(f"hk{digits}"), _compact_query(f"{digits}.hk")})
        if keys & item_keys:
            return item
    return None


def _generic_pack(code: str, market_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    quote = (market_data or {}).get("quote") if market_data else None
    current_price = (
        quote.get("price")
        if isinstance(quote, dict) and quote.get("price")
        else 100.0
    )
    market = "H股" if code.startswith("HK") or code.endswith(".HK") else "A股"
    currency = "HKD" if market == "H股" else "CNY"
    identity = _catalog_identity(code)
    quote_name = _clean_realtime_stock_name((quote or {}).get("name")) if isinstance(quote, dict) else None
    stock_name = (identity or {}).get("name") or quote_name
    history = (market_data or {}).get("history") or []
    valuation = _derive_dynamic_valuation(float(current_price), history, market)
    quant_metrics = _build_live_quant_metrics(float(current_price), history)
    sniper_points = _derive_sniper_points(float(current_price), history, valuation)
    return {
        "stock": {
            "name": stock_name or code,
            "code": code,
            "market": market,
            "currency": currency,
            "exchange": "HKEX" if market == "H股" else (identity or {}).get("exchange"),
        },
        "valuation": valuation,
        "scores": _build_dynamic_scores(
            float(current_price),
            history,
            valuation,
            [],
            [],
            stock={"name": stock_name or code, "code": code, "market": market},
            company_profile={},
        ),
        "quant_metrics": quant_metrics,
        "decision_reasons": _build_dynamic_decision_reasons(
            float(current_price),
            valuation,
            quant_metrics,
            [],
            [],
            {"name": stock_name or code, "code": code, "market": market},
            {},
            sniper_points,
            None,
            [],
        ),
        "sniper_points": sniper_points,
        "related_sectors": [
            {
                "name": "待读取",
                "heat": "待读取",
                "relevance": "待读取",
                "data_source": "待读取",
                "data_status": "pending",
                "reason": "待读取",
            },
        ],
        "news": [
            {
                "title": "待读取",
                "source": "待读取",
                "date": "待读取",
                "url": "#",
                "tone": "pending",
                "data_status": "pending",
            },
        ],
        "_history": history,
    }


def _fallback_recommendation(pack: Dict[str, Any]) -> CommercialAiRecommendation:
    stock = pack["stock"]
    valuation = pack["valuation"]
    current_price = float(valuation["current_price"])
    valuation_low = float(valuation["low"])
    valuation_high = float(valuation["high"])
    scores = pack.get("scores") or []
    score_by_label = {item.get("label"): _safe_float(item.get("score")) for item in scores if isinstance(item, dict)}
    value_score = score_by_label.get("价值") or 5.0
    growth_score = score_by_label.get("成长") or 5.0
    trend_label = (_metric_by_label(pack.get("quant_metrics") or [], "均线结构") or {}).get("value", "待读取")
    volume_state = (_metric_by_label(pack.get("quant_metrics") or [], "量价确认") or {}).get("percentile", "待读取")
    sniper_points = pack.get("sniper_points") or []
    focus = sniper_points[0]["description"] if sniper_points else "待读取"
    confirm = float(sniper_points[1]["price"]) if len(sniper_points) > 1 else current_price * 1.08
    invalid = float(sniper_points[2]["price"]) if len(sniper_points) > 2 else current_price * 0.88
    if current_price < valuation_low:
        if growth_score >= 6.2 and trend_label in {"多头", "修复"}:
            action = "逢低关注"
            summary = "估值有折价，成长和趋势仍可跟踪。"
        else:
            action = "观察"
            summary = "估值有折价，但趋势验证不足，先看承接。"
    elif current_price <= valuation_high:
        if value_score >= 6.5 and growth_score >= 6.6 and volume_state in {"放量", "正常"}:
            action = "逢低关注"
            summary = "估值仍有赔率，量价验证后更适合推进。"
        else:
            action = "观察"
            summary = "估值位于合理区间内，关注量价验证。"
    else:
        action = "谨慎"
        summary = "谨慎：价格高于动态估值区间上沿，先控制追高风险。"
    unit = valuation["currency_label"].split("/")[0]
    sector_names = [
        item.get("name", "")
        for item in (pack.get("related_sectors") or [])[:3]
        if item.get("name") and item.get("name") != "待读取"
    ]
    quant_preview = [
        f"{item.get('label')} {item.get('value')}"
        for item in (pack.get("quant_metrics") or [])[:3]
        if item.get("value")
    ]

    return CommercialAiRecommendation(
        source="fallback",
        model="local-rule",
        status="fallback",
        action=action,
        summary=summary,
        entry_plan=f"{focus} 若放量站稳{confirm:.3f}{unit}，趋势验证度提升。",
        risk_trigger=f"若跌破{invalid:.3f}{unit}，说明短线承接失败，应优先控制回撤。",
        evidence_summary=[
            f"当前价{current_price:.3f}{unit}，{valuation['price_position']}",
            "量化指标：" + ("；".join(quant_preview) if quant_preview else "待读取"),
            "关联板块：" + ("、".join(sector_names) if sector_names else "待读取"),
        ],
    )


def _polish_recommendation_text(value: Any) -> str:
    text = str(value or "")
    replacements = [
        ("需等待确认", "需观察验证"),
        ("需要等待确认", "需要观察验证"),
        ("等待趋势确认", "观察趋势验证"),
        ("等待量价确认", "关注量价验证"),
        ("等待确认", "等待验证"),
        ("待确认", "观察验证"),
        ("承接确认", "承接验证"),
        ("趋势确认度", "趋势验证度"),
    ]
    for source, target in replacements:
        text = text.replace(source, target)
    return text


def _polish_recommendation(recommendation: CommercialAiRecommendation) -> CommercialAiRecommendation:
    return CommercialAiRecommendation(
        source=recommendation.source,
        model=recommendation.model,
        status=recommendation.status,
        action=_polish_recommendation_text(recommendation.action),
        summary=_polish_recommendation_text(recommendation.summary),
        entry_plan=_polish_recommendation_text(recommendation.entry_plan),
        risk_trigger=_polish_recommendation_text(recommendation.risk_trigger),
        evidence_summary=[
            _polish_recommendation_text(item)
            for item in recommendation.evidence_summary
        ],
    )


def _build_deepseek_prompt(pack: Dict[str, Any]) -> str:
    decision_pack = {
        "stock": pack["stock"],
        "company_profile": pack.get("_company_profile") or {},
        "valuation": pack["valuation"],
        "scores": pack["scores"],
        "quant_metrics": pack["quant_metrics"],
        "decision_reasons": pack["decision_reasons"],
        "sniper_points": pack["sniper_points"],
        "industry_trend": pack.get("industry_trend"),
        "related_sectors": pack["related_sectors"],
        "news_summary": pack.get("_news_summary") or {},
        "news": pack["news"],
        "investment_hypotheses": pack.get("investment_hypotheses") or [],
        "data_status": {
            "quote": pack.get("_quote_status") or "pending",
            "sectors": pack.get("_sector_status") or "pending",
            "news": pack.get("_news_status") or "pending",
            "financials": "latest_public_source" if pack.get("_financials") else "pending",
        },
        "instruction": (
            "请基于以上数据，输出简短、专业、有推荐倾向但不过度承诺的股票分析结论。"
                "news_summary 是完整资讯池统计，news 是前端精选展示列表；必须把资讯池情绪分布纳入判断。"
                "news.tone 中 positive=利好，risk=利空，neutral=中性；必须把最新资讯纳入判断。"
            "必须尊重 investment_hypotheses 和 data_status；待读取的数据不能当成事实。"
            "成长评分是核心变量，它代表未来行业空间、公司护城河/卡位、商业化兑现和趋势验证，不能简化成短线涨幅。"
            "如果公司处在未来高增长行业且有强护城河，可以给更积极的跟踪建议；"
            "但不能只因为题材好或价格低于估值区间就直接推荐，必须同时看动态估值区间、确认位、失效位、财务风险和量价状态。"
            "面向用户的措辞不要出现“待确认”“等待确认”，改用趋势验证、量价验证或观察验证。"
            "不要写聊天式回答，不要展开长篇解释。"
        ),
    }
    return json.dumps(decision_pack, ensure_ascii=False)


def _pending_industry_trend(theme: str = "待读取") -> CommercialIndustryTrend:
    return CommercialIndustryTrend(
        theme=theme or "待读取",
        source="pending",
        status="pending",
        summary="待读取",
        items=[
            CommercialIndustryTrendItem(
                tone="pending",
                label="待读取",
                impact_score=0,
                title="待读取",
                description="待读取",
            )
        ],
    )


def _primary_industry_theme(pack: Dict[str, Any]) -> str:
    sectors = pack.get("related_sectors") or []
    for item in sectors:
        name = str(item.get("name") or "").strip()
        if name and name != "待读取":
            return name
    return "待读取"


def _build_industry_trend_prompt(pack: Dict[str, Any]) -> str:
    theme = _primary_industry_theme(pack)
    trend_pack = {
        "stock": pack["stock"],
        "company_profile": pack.get("_company_profile") or {},
        "core_theme": theme,
        "related_sectors": (pack.get("related_sectors") or [])[:6],
        "news": (pack.get("news") or [])[:5],
        "quant_metrics": (pack.get("quant_metrics") or [])[:6],
        "instruction": (
            "请围绕 core_theme 分析该股票所处行业趋势。"
            "输出必须实事求是，简单明了，有利好、有中性、有风险，不要夸张，不承诺收益。"
            "必须基于 related_sectors、news 和 quant_metrics 中已经给出的事实，不要补写未提供的公司事实。"
            "行业趋势要服务于成长判断：说明未来空间、竞争格局、商业化兑现和护城河是否增强或削弱。"
            "summary不超过34个中文字符；items必须恰好3条，tone分别为positive/neutral/risk。"
            "每条必须给impact_score，范围-100到100；100=非常利好，0=中性，-100=非常利空。"
        ),
    }
    return json.dumps(trend_pack, ensure_ascii=False)


def _trend_impact_score(value: Any, tone: str) -> int:
    """Clamp AI trend impact score to a conservative -100..100 range."""

    fallback = {"positive": 65, "neutral": 0, "risk": -60, "negative": -60, "pending": 0}.get(tone, 0)
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        score = fallback
    return max(-100, min(100, score))


def _try_deepseek_industry_trend(pack: Dict[str, Any]) -> CommercialIndustryTrend:
    theme = _primary_industry_theme(pack)
    if theme == "待读取":
        return _pending_industry_trend(theme)

    api_key = _get_deepseek_key()
    if not api_key:
        return _pending_industry_trend(theme)

    model = (
        os.getenv("COMMERCIAL_ANALYSIS_DEEPSEEK_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or "deepseek-chat"
    ).strip()
    payload = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 520,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是A股和港股产业趋势研究员，只输出JSON。"
                    "字段必须包含 theme, summary, items。"
                    "items必须是3个对象，字段包含 tone, label, impact_score, title, description。"
                    "tone只用positive/neutral/risk；label用利好/中性/风险。"
                    "impact_score用-100到100的整数，+100表示非常利好，-100表示非常利空。"
                ),
            },
            {"role": "user", "content": _build_industry_trend_prompt(pack)},
        ],
    }

    try:
        response = requests.post(
            _deepseek_url(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=18,
        )
        response.raise_for_status()
        data = response.json()
        parsed = json.loads(data["choices"][0]["message"]["content"])
        raw_items = parsed.get("items") or []
        if not isinstance(raw_items, list):
            return _pending_industry_trend(theme)
        items: List[CommercialIndustryTrendItem] = []
        expected = [("positive", "利好"), ("neutral", "中性"), ("risk", "风险")]
        for index, (tone, label) in enumerate(expected):
            raw = raw_items[index] if index < len(raw_items) and isinstance(raw_items[index], dict) else {}
            item_tone = str(raw.get("tone") or tone)
            if item_tone not in {"positive", "neutral", "risk"}:
                item_tone = tone
            items.append(
                CommercialIndustryTrendItem(
                    tone=item_tone,
                    label=str(raw.get("label") or label),
                    impact_score=_trend_impact_score(raw.get("impact_score"), item_tone),
                    title=str(raw.get("title") or "待读取")[:28],
                    description=str(raw.get("description") or "待读取")[:88],
                )
            )

        return CommercialIndustryTrend(
            theme=str(parsed.get("theme") or theme)[:18],
            source="deepseek",
            status="ok",
            summary=str(parsed.get("summary") or "待读取")[:42],
            items=items,
        )
    except Exception as exc:  # noqa: BLE001 - trend block should degrade to pending.
        logger.warning("[commercial-analysis] DeepSeek industry trend pending: %s", exc)
        return _pending_industry_trend(theme)


def _first_api_key(value: str) -> str:
    return next((item.strip() for item in value.split(",") if item.strip()), "")


def _get_deepseek_key() -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(_repo_root() / ".env", override=False)
    except Exception:  # noqa: BLE001 - dotenv is optional in production.
        pass

    try:
        from src.config import setup_env

        setup_env()
    except Exception:  # noqa: BLE001 - env bootstrap must be best-effort.
        pass

    keys_value = os.getenv("LLM_DEEPSEEK_API_KEYS") or os.getenv("DEEPSEEK_API_KEYS") or ""
    key = _first_api_key(keys_value)
    if key:
        return key
    return (
        os.getenv("LLM_DEEPSEEK_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or ""
    ).strip()


def _deepseek_url() -> str:
    base_url = (
        os.getenv("LLM_DEEPSEEK_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or "https://api.deepseek.com"
    ).strip().rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    return f"{base_url}/chat/completions"


def _build_company_basic_prompt(pack: Dict[str, Any]) -> str:
    company_profile = pack.get("_company_profile") or {}
    company_pack = {
        "stock": pack["stock"],
        "company_profile": company_profile,
        "financials": pack.get("_financials") or {},
        "scores": pack.get("scores") or [],
        "valuation": pack.get("valuation") or {},
        "related_sectors": (pack.get("related_sectors") or [])[:6],
        "news_summary": pack.get("_news_summary") or {},
        "news": (pack.get("news") or [])[:8],
        "accuracy_priority": [
            "company_profile.verified_position / official profile",
            "company_profile.business / intro / products",
            "financials",
            "related_sectors and news",
        ],
        "instruction": (
            "请只基于以上结构化资料，为普通投资者生成“公司基本面”说明。"
            "输出必须是JSON，字段严格为 company_position, business_model, industry_logic, watch_points, risk_boundary。"
            "准确性优先级必须是：官方或已核验公司资料 > 公开公司资料 > 财务数据 > 板块和资讯。"
            "如果company_profile.verified_position非空，company_position必须保留它的核心身份和关键词，不得改写为更泛化或更低级的概念；"
            "例如不能把“物理AI核心基础设施企业”改成“数字孪生平台型公司”。"
            "如果资料没有明确说明某项业务，不要用板块、新闻标题或概念词自行推断公司主业。"
            "company_position说明公司属于什么产业链/业务类型；business_model说明靠什么赚钱或兑现业绩；"
            "industry_logic说明行业景气、政策、需求或竞争如何影响公司；watch_points说明后续最该看哪些经营指标；"
            "risk_boundary说明什么情况会削弱基本面判断。"
            "语言要像专业投研，但必须直白，不能堆概念；每个字段25到70个中文字符。"
            "如果股票带ST、亏损、净资产为负、现金流为负或高负债，必须直接写入risk_boundary。"
            "不要承诺收益，不要写买入卖出，不要出现“待确认”“待读取”“作为AI”“不构成投资建议”。"
            "不要补写未提供的具体客户、订单、产品收入比例或不存在的事实。"
        ),
    }
    return json.dumps(company_pack, ensure_ascii=False)


def _clean_deepseek_clause(value: Any, limit: int = 86) -> str:
    text = _strip_html(str(value or ""))
    text = re.sub(r"\s+", "", text)
    text = text.replace("待确认", "观察验证").replace("待读取", "资料未充分覆盖")
    return _complete_clause_text(text, limit).rstrip("，,；;:：、。.!！?？ ")


def _financial_risk_note(financials: Optional[Dict[str, Any]]) -> str:
    if not financials:
        return ""
    flags: List[str] = []
    net_profit = _safe_float(financials.get("net_profit"))
    deducted_net_profit = _safe_float(financials.get("deducted_net_profit"))
    eps = _safe_float(financials.get("eps"))
    operating_cash_flow = _safe_float(financials.get("operating_cash_flow_per_share"))
    net_margin = _safe_float(financials.get("net_margin"))
    debt_ratio = _safe_float(financials.get("debt_ratio"))
    if net_profit is not None and net_profit < 0:
        flags.append("归母净利润亏损")
    if deducted_net_profit is not None and deducted_net_profit < 0:
        flags.append("扣非净利润亏损")
    if eps is not None and eps < 0:
        flags.append("EPS为负")
    if operating_cash_flow is not None and operating_cash_flow < 0:
        flags.append("经营现金流为负")
    if net_margin is not None and net_margin < 0:
        flags.append("销售净利率为负")
    if debt_ratio is not None and debt_ratio >= 75:
        flags.append(f"资产负债率{debt_ratio:.1f}%偏高")
    if not flags:
        return ""
    return "；当前" + "、".join(flags[:5]) + "，需要后续数据验证改善"


def _format_deepseek_company_basic(
    parsed: Dict[str, Any],
    stock: Dict[str, Any],
    company_profile: Optional[Dict[str, Any]] = None,
    financials: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    required_fields = [
        ("公司定位", "company_position", 82),
        ("业务主线", "business_model", 82),
        ("行业逻辑", "industry_logic", 82),
        ("观察重点", "watch_points", 78),
        ("风险边界", "risk_boundary", 86),
    ]
    clauses: List[str] = []
    for label, key, limit in required_fields:
        clause = _clean_deepseek_clause(parsed.get(key), limit)
        if len(clause) < 8:
            return None
        clauses.append(f"{label}：{clause}。")

    profile = company_profile or {}
    if profile.get("identity_locked"):
        verified_position = _profile_phrase(profile.get("verified_position"), 120)
        business_model = _complete_clause_text(profile.get("business_model"), 160)
        industry_logic = _complete_clause_text(profile.get("industry_logic"), 150)
        watch_points = _complete_clause_text(profile.get("watch_points"), 120)
        risk_boundary = _complete_clause_text(profile.get("risk_boundary"), 140)
        risk_note = _financial_risk_note(financials)
        locked_clauses = [
            f"公司定位：{stock.get('name') or profile.get('name')}，{verified_position}。",
        ]
        if business_model:
            locked_clauses.append(f"业务主线：{business_model}。")
        if industry_logic:
            locked_clauses.append(f"行业逻辑：{industry_logic}。")
        if watch_points:
            locked_clauses.append(f"观察重点：{watch_points}。")
        if risk_boundary or risk_note:
            locked_clauses.append(f"风险边界：{risk_boundary}{risk_note}。")
        return "".join(locked_clauses)

    text = "".join(clauses)
    forbidden = ("待确认", "待读取", "作为AI", "不构成投资建议", "保证收益", "一定上涨", "建议买入")
    if any(word in text for word in forbidden):
        return None
    compact = _compact_query(text)
    stock_name = _compact_query(str(stock.get("name") or ""))
    if stock_name and len(stock_name) >= 3 and stock_name not in compact:
        text = f"公司定位：{stock.get('name')}，{clauses[0].split('：', 1)[1]}" + "".join(clauses[1:])
    return text


def _replace_decision_reason(reasons: List[Dict[str, str]], title: str, description: str) -> None:
    for item in reasons:
        if item.get("title") == title:
            item["description"] = description
            return
    reasons.append({"title": title, "description": description})


def _try_deepseek_company_basic_reason(pack: Dict[str, Any]) -> Optional[str]:
    api_key = _get_deepseek_key()
    if not api_key:
        return None

    model = (
        os.getenv("COMMERCIAL_ANALYSIS_DEEPSEEK_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or "deepseek-chat"
    ).strip()
    payload = {
        "model": model,
        "temperature": 0.05,
        "max_tokens": 680,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是A股和港股基本面研究员，只输出JSON。"
                    "你必须根据输入资料做标准化基本面介绍，不能虚构未给出的事实。"
                    "公司主身份以company_profile中的官方或已核验资料为最高优先级，不得被板块、新闻或概念词覆盖。"
                    "字段只能包含 company_position, business_model, industry_logic, watch_points, risk_boundary。"
                ),
            },
            {"role": "user", "content": _build_company_basic_prompt(pack)},
        ],
    }

    try:
        response = requests.post(
            _deepseek_url(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=18,
        )
        response.raise_for_status()
        data = response.json()
        parsed = json.loads(data["choices"][0]["message"]["content"])
        if not isinstance(parsed, dict):
            return None
        return _format_deepseek_company_basic(
            parsed,
            pack.get("stock") or {},
            pack.get("_company_profile") or {},
            pack.get("_financials") or {},
        )
    except Exception as exc:  # noqa: BLE001 - company basic block can keep local fallback.
        logger.warning("[commercial-analysis] DeepSeek company basic fallback: %s", exc)
        return None


def _try_deepseek_recommendation(pack: Dict[str, Any]) -> Optional[CommercialAiRecommendation]:
    api_key = _get_deepseek_key()
    if not api_key:
        return None

    model = (
        os.getenv("COMMERCIAL_ANALYSIS_DEEPSEEK_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or "deepseek-chat"
    ).strip()
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 420,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是A股和港股量化投研助手，只输出JSON。"
                    "字段必须包含 action, summary, entry_plan, risk_trigger, evidence_summary。"
                    "action用逢低关注/观察/谨慎/回避之一；summary不超过36个中文字符。"
                ),
            },
            {"role": "user", "content": _build_deepseek_prompt(pack)},
        ],
    }

    try:
        response = requests.post(
            _deepseek_url(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=18,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        evidence_summary = parsed.get("evidence_summary") or []
        if isinstance(evidence_summary, str):
            evidence_summary = [evidence_summary]
        return CommercialAiRecommendation(
            source="deepseek",
            model=model,
            status="ok",
            action=str(parsed.get("action") or "观察"),
            summary=str(parsed.get("summary") or _fallback_recommendation(pack).summary),
            entry_plan=str(parsed.get("entry_plan") or _fallback_recommendation(pack).entry_plan),
            risk_trigger=str(parsed.get("risk_trigger") or _fallback_recommendation(pack).risk_trigger),
            evidence_summary=[str(item) for item in evidence_summary[:4]],
        )
    except Exception as exc:  # noqa: BLE001 - DeepSeek failure should not break the public page.
        logger.warning("[commercial-analysis] DeepSeek recommendation fallback: %s", exc)
        return None


def _build_response(stock_code: str) -> CommercialAnalysisResponse:
    code = _normalize_code(stock_code)
    market_data = _try_tencent_market_data(code) or _try_stock_api_market_data(code)
    if not market_data:
        raise HTTPException(
            status_code=503,
            detail="待读取",
        )

    pack = _generic_pack(code, market_data)
    _merge_live_market_data(pack, market_data)
    valuation = pack["valuation"]
    valuation["marker_percent"] = round(
        _marker_percent(
            float(valuation["current_price"]),
            float(valuation["low"]),
            float(valuation["high"]),
        ),
        2,
    )
    latest_news = _fetch_latest_news(
        code,
        pack["stock"]["name"],
        pack["stock"]["market"],
        limit=_NEWS_POOL_LIMIT,
    )
    if latest_news:
        for item in latest_news:
            item["data_status"] = "latest_public_source"
        display_news = latest_news[:_NEWS_DISPLAY_LIMIT]
        pack["_news_pool"] = latest_news
        pack["news"] = display_news
        pack["_news_summary"] = _build_news_summary(latest_news, display_news)
        pack["_news_status"] = "latest_public_source"
        pack["_news_source"] = "资讯情绪引擎"
    else:
        pack["news"] = [
            {
                "title": "待读取",
                "source": "待读取",
                "date": "待读取",
                "url": "#",
                "tone": "pending",
                "data_status": "pending",
            }
        ]
        pack["_news_pool"] = []
        pack["_news_summary"] = _build_news_summary([], pack["news"])
        pack["_news_status"] = "pending"
        pack["_news_source"] = "待读取"

    latest_sectors = _fetch_related_sectors(code, pack["stock"]["market"])
    if latest_sectors:
        pack["related_sectors"] = latest_sectors
        pack["_sector_status"] = "latest_public_source"
        has_profile_theme = any(item.get("heat") in {"公司行业", "公司资料"} for item in latest_sectors)
        pack["_sector_source"] = "公司资料关键词 + 实时所属板块" if has_profile_theme else "实时所属板块"
    else:
        pack["related_sectors"] = [
            {
                "name": "待读取",
                "heat": "待读取",
                "relevance": "待读取",
                "data_source": "待读取",
                "data_status": "pending",
                "reason": "待读取",
            }
        ]
        pack["_sector_status"] = "pending"
        pack["_sector_source"] = "待读取"
    pack["_company_profile"] = _fetch_company_profile(
        code,
        pack["stock"]["market"],
        pack["stock"]["name"],
    )
    pack["_financials"] = _fetch_financial_snapshot(code)
    _refresh_dynamic_sections(pack)
    deepseek_company_basic = _try_deepseek_company_basic_reason(pack)
    if deepseek_company_basic:
        _replace_decision_reason(pack["decision_reasons"], "公司基本面", deepseek_company_basic)
    industry_trend = _try_deepseek_industry_trend(pack)
    pack["industry_trend"] = industry_trend.model_dump()
    pack["investment_hypotheses"] = _build_investment_hypotheses(pack)
    recommendation = _polish_recommendation(
        _try_deepseek_recommendation(pack) or _fallback_recommendation(pack)
    )
    data_audit = _build_data_audit(pack, recommendation)
    data_quality = CommercialDataQuality(
        updated_at=str(pack.get("_quote_updated_at") or _now_iso()),
        quote_source=str(pack.get("_quote_source") or "行情引擎"),
        quote_status=str(pack.get("_quote_status") or "best_effort"),
        ai_status=recommendation.status,
        notes=[
            "决策引擎按当前价、日线趋势、波动率、量价确认、板块联动和资讯情绪生成交易决策包。",
            "算法先判断估值赔率，再等待趋势和量能确认，帮助用户识别更值得出手的位置。",
            "点位计划把关注区、确认位和失效位前置，核心目标是少追高、控回撤。",
            "AI决策引擎只接收结构化决策包并输出短结论，不作为聊天框展示。",
        ],
    )

    return CommercialAnalysisResponse(
        stock=CommercialStockIdentity(**pack["stock"]),
        recommendation=recommendation,
        valuation=CommercialValuationRange(**valuation),
        scores=[CommercialScore(**item) for item in pack["scores"]],
        quant_metrics=[CommercialQuantMetric(**item) for item in pack["quant_metrics"]],
        decision_reasons=[CommercialDecisionReason(**item) for item in pack["decision_reasons"]],
        sniper_points=[CommercialSniperPoint(**item) for item in pack["sniper_points"]],
        industry_trend=industry_trend,
        related_sectors=[CommercialRelatedSector(**item) for item in pack["related_sectors"]],
        news_summary=CommercialNewsSummary(**(pack.get("_news_summary") or _build_news_summary([], pack.get("news") or []))),
        news=[CommercialNewsItem(**item) for item in pack["news"]],
        data_quality=data_quality,
        data_audit=[CommercialDataAuditItem(**item) for item in data_audit],
        investment_hypotheses=[
            CommercialInvestmentHypothesis(**item)
            for item in pack["investment_hypotheses"]
        ],
    )


@router.get(
    "/search",
    response_model=CommercialSearchResponse,
    summary="公开股票搜索建议",
    description="基于全量A/H股索引返回首页搜索候选，支持代码、中文名、拼音和别名。",
)
async def search_commercial_stocks(
    q: str = Query("", max_length=40, description="搜索关键词"),
    limit: int = Query(8, ge=1, le=12, description="最多返回条数"),
) -> CommercialSearchResponse:
    return await run_in_threadpool(_search_catalog, q, limit)


@router.get(
    "/{stock_code}",
    response_model=CommercialAnalysisResponse,
    summary="公开商业分析页数据",
    description="返回每日股研AI商业化分析页所需的量化决策包。行情不可用时返回透明错误状态。",
)
async def get_commercial_analysis(stock_code: str) -> CommercialAnalysisResponse:
    return await run_in_threadpool(_build_response, stock_code)
