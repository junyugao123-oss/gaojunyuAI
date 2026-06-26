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
_NEWS_RESULT_LIMIT = 10
_SECTOR_CACHE: Dict[str, tuple[float, List[Dict[str, str]]]] = {}
_SECTOR_CACHE_SECONDS = 120
_FINANCIAL_CACHE: Dict[str, tuple[float, Optional[Dict[str, Any]]]] = {}
_FINANCIAL_CACHE_SECONDS = 1800

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
        "name": str(quote_fields[1]).strip() if len(quote_fields) > 1 and quote_fields[1] else None,
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
                "provider": "腾讯行情",
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
            "provider": f"stock-api:{payload.get('providerSource') or stock.get('source') or 'auto'}",
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
        "东方财富热股",
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
                "reason": f"东方财富实时所属板块，板块{heat}{leader_text}。",
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

    _FINANCIAL_CACHE[cache_key] = (now, snapshot)
    return snapshot


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html_unescape(text)
    return re.sub(r"\s+", " ", text).strip()


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
                "source": _strip_html(source_match.group(1)) if source_match else "AASTOCKS新闻",
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
                "source": f"东方财富公告 · {column_name}",
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
        source = _strip_html(str(row.get("文章来源") or "东方财富资讯"))
        date = _normalize_news_date(row.get("发布时间"))
        url = str(row.get("新闻链接") or "https://finance.eastmoney.com/").strip()
        items.append(
            {
                "title": display_title,
                "source": source,
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


def _derive_dynamic_valuation(current_price: float, history: List[Dict[str, Any]], market: str) -> Dict[str, Any]:
    closes = _history_values(history, "close")
    lows = _history_values(history, "low")
    highs = _history_values(history, "high")
    recent_lows = lows[-120:] or lows
    recent_highs = highs[-120:] or highs
    ma20 = _mean(closes[-20:])
    ma60 = _mean(closes[-60:])
    volatility = _annualized_volatility(closes)

    if recent_lows and recent_highs:
        range_low = min(recent_lows)
        range_high = max(recent_highs)
        anchors = [item for item in (current_price, ma20, ma60) if isinstance(item, (int, float)) and item > 0]
        anchor = _mean([float(item) for item in anchors]) or current_price
        low_buffer = 0.82 if volatility and volatility >= 80 else 0.88 if volatility and volatility >= 45 else 0.92
        high_buffer = 1.42 if volatility and volatility >= 80 else 1.30 if volatility and volatility >= 45 else 1.20
        low = max(min(range_low, current_price * 0.98), anchor * low_buffer)
        high = min(max(range_high, current_price * 1.08), anchor * high_buffer)
    else:
        low = current_price * 0.9
        high = current_price * 1.18

    if high <= low:
        low = current_price * 0.9
        high = current_price * 1.18
    if high <= low * 1.08:
        high = low * 1.12

    return {
        "label": "AI动态估值区间",
        "currency_label": "港元/股" if market == "H股" else "元/股",
        "low": _market_price_round(low),
        "high": _market_price_round(high),
        "current_price": _market_price_round(current_price),
        "price_position": _price_position(current_price, low, high),
        "source": "computed",
        "status": "computed_from_quote_history",
        "inputs": ["current_price", "historical_low_high", "ma20", "ma60", "volatility"],
    }


def _news_signal_counts(news: List[Dict[str, str]]) -> Dict[str, int]:
    counts = {"positive": 0, "risk": 0, "neutral": 0, "pending": 0}
    for item in news:
        tone = str(item.get("tone") or "neutral")
        counts[tone if tone in counts else "neutral"] += 1
    return counts


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


def _build_dynamic_scores(
    current_price: float,
    history: List[Dict[str, Any]],
    valuation: Dict[str, Any],
    news: List[Dict[str, str]],
    sectors: List[Dict[str, str]],
    financials: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    closes = _history_values(history, "close")
    volumes = _history_values(history, "volume")
    momentum_20 = _return_percent(current_price, closes[-21] if len(closes) >= 21 else None)
    momentum_60 = _return_percent(current_price, closes[-61] if len(closes) >= 61 else None)
    volatility = _annualized_volatility(closes)
    avg_5 = _mean(volumes[-5:])
    avg_20 = _mean(volumes[-20:])
    volume_ratio = avg_5 / avg_20 if avg_5 and avg_20 else None
    ma20 = _mean(closes[-20:])
    ma60 = _mean(closes[-60:])
    low = float(valuation["low"])
    high = float(valuation["high"])
    span = high - low
    if span > 0:
        position_ratio = (current_price - low) / span
        if current_price < low:
            value_score = 7.4 + min((low - current_price) / max(low, 0.01) * 10, 1.4)
        elif current_price <= high:
            value_score = 8.0 - _clamp(position_ratio, 0.0, 1.0) * 2.8
        else:
            value_score = 4.8 - min((current_price - high) / max(high, 0.01) * 8, 2.4)
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
    volatility_penalty = _clamp((volatility or 45.0) / 55.0, 0.4, 2.4)
    shareholder_hits = 0
    for item in news:
        compact_title = _compact_query(item.get("title", ""))
        if any(keyword in compact_title for keyword in ("分红", "派息", "回购", "回購", "增持")):
            shareholder_hits += 1

    growth_score = 5.2 + momentum_60_value / 18.0 + momentum_20_value / 24.0 + positive_news * 0.35 - risk_news * 0.45 + sector_bonus
    profitability_score = 4.6 + positive_news * 0.25 - risk_news * 0.4 + volume_bonus + (0.5 if ma20 and ma60 and ma20 >= ma60 else -0.2)
    finance_score = 5.4 + (0.4 if current_price >= (ma60 or current_price) else -0.3) + volume_bonus - volatility_penalty * 0.65 - risk_news * 0.25
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
        net_margin = financials.get("net_margin")
        roe = financials.get("roe")
        roa = financials.get("roa")
        net_profit_growth = financials.get("net_profit_growth_qoq")
        operating_cash_flow = financials.get("operating_cash_flow_per_share")
        pb = financials.get("pb")
        pe = financials.get("pe")
        dividend_yield = financials.get("dividend_yield_ttm")
        dividend_payout = financials.get("dividend_payout_ratio")

        profitability_score = (
            5.0
            + (net_margin or 0.0) / 18.0
            + (roe or 0.0) / 25.0
            + (roa or 0.0) / 22.0
            + (net_profit_growth or 0.0) / 120.0
        )
        finance_score = (
            5.0
            + (1.0 if operating_cash_flow and operating_cash_flow > 0 else -1.0)
            + (0.6 if pe and pe > 0 else -0.6)
            - _clamp(((pb or 0.0) - 8.0) / 18.0, 0.0, 2.5)
            + (0.4 if financials.get("book_value_per_share") and financials.get("book_value_per_share") > 0 else -0.4)
        )
        dividend_score = (
            0.6
            + _clamp((dividend_yield or 0.0) * 1.2, 0.0, 5.0)
            + _clamp((dividend_payout or 0.0) / 25.0, 0.0, 3.0)
        )
        if financials.get("eps") and financials["eps"] < 0:
            value_score -= 0.8
        score_status = {
            "profitability": "computed_from_financial_indicators",
            "finance": "computed_from_financial_indicators",
            "dividend": "computed_from_dividend_indicators",
        }
        score_descriptions = {
            "profitability": (
                f"销售净利率{net_margin:.1f}%、ROE{roe:.1f}%、ROA{roa:.1f}%，"
                "由财务指标直接计算。"
                if net_margin is not None and roe is not None and roa is not None
                else "基于已读取财务指标计算。"
            ),
            "finance": (
                f"经营现金流/股{operating_cash_flow:.3f}，PB{pb:.2f}，由财务指标直接计算。"
                if operating_cash_flow is not None and pb is not None
                else "基于已读取财务指标计算。"
            ),
            "dividend": (
                f"股息率TTM{dividend_yield:.2f}%、派息比率{dividend_payout:.1f}%，由分红指标计算。"
                if dividend_yield is not None and dividend_payout is not None
                else "未读取到TTM股息率或派息比率，分红评分保持低位。"
            ),
        }

    return [
        {
            "label": "价值",
            "score": _clamp_score(value_score),
            "description": f"根据当前价相对{valuation['label']}的位置动态计算。",
            "source": "computed",
            "status": "computed_from_valuation_position",
            "inputs": ["current_price", "dynamic_valuation_range"],
        },
        {
            "label": "成长",
            "score": _clamp_score(growth_score),
            "description": "由20/60日动量、板块变化和最新资讯情绪综合计算。",
            "source": "computed",
            "status": "computed_from_momentum_news_sector",
            "inputs": ["20d_momentum", "60d_momentum", "news_tone", "sector_change"],
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
            "inputs": ["dividend_yield_ttm", "dividend_payout_ratio"] if financials else ["dividend_news", "buyback_news", "holding_increase_news"],
        },
    ]


def _build_dynamic_decision_reasons(
    current_price: float,
    valuation: Dict[str, Any],
    quant_metrics: List[Dict[str, str]],
    sectors: List[Dict[str, str]],
    news: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    momentum_20 = (_metric_by_label(quant_metrics, "20日动量") or {}).get("value", "待读取")
    momentum_60 = (_metric_by_label(quant_metrics, "60日动量") or {}).get("value", "待读取")
    volatility = (_metric_by_label(quant_metrics, "年化波动率") or {}).get("value", "待读取")
    volume_ratio = (_metric_by_label(quant_metrics, "量价确认") or {}).get("value", "待读取")
    first_sector = next((item for item in sectors if item.get("name") and item.get("name") != "待读取"), None)
    sector_text = (
        f"{first_sector.get('name')} {first_sector.get('realtime_change') or first_sector.get('heat')}"
        if first_sector
        else "待读取"
    )
    news_counts = _news_signal_counts(news)
    news_text = (
        f"利好{news_counts['positive']}条、利空{news_counts['risk']}条、中性{news_counts['neutral']}条"
        if news_counts["positive"] or news_counts["risk"] or news_counts["neutral"]
        else "待读取"
    )
    unit = valuation["currency_label"].split("/")[0]
    return [
        {
            "title": "实时估值位置",
            "description": f"当前价{current_price:.3f}{unit}，{valuation['price_position']}。",
        },
        {
            "title": "趋势质量",
            "description": f"20日动量{momentum_20}，60日动量{momentum_60}，用来判断修复是否持续。",
        },
        {
            "title": "量化确认",
            "description": f"年化波动率{volatility}，量价确认{volume_ratio}，决定是否需要等待更好的确认点。",
        },
        {
            "title": "最新板块与资讯",
            "description": f"关联板块：{sector_text}；最新资讯情绪：{news_text}。",
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
            "evidence": f"由当前价、近{history_count}条日线、MA20/MA60和波动率计算。",
            "action": "这是算法估值区间，不等同于券商目标价。",
        },
        {
            "section": "点位计划",
            "classification": "computed",
            "status": "ok" if pack.get("sniper_points") else "pending",
            "evidence": "由近20日低点、近60日高点、MA20和波动率规则生成。",
            "action": "先看失效位，再看确认位，避免无纪律追高。",
        },
        {
            "section": "五维评分",
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
            "evidence": f"{news_source} · 最新日期 {_first_valid_news_date(pack.get('news') or [])}",
            "action": "资讯按时间倒序展示，并标记利好、利空或中性。",
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
    return "待确认"


def _build_investment_hypotheses(pack: Dict[str, Any]) -> List[Dict[str, str]]:
    valuation = pack.get("valuation") or {}
    quant_metrics = pack.get("quant_metrics") or []
    sniper_points = pack.get("sniper_points") or []
    sectors = pack.get("related_sectors") or []
    news = pack.get("news") or []
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
        valuation_status = "成立" if current_price <= low or "下沿" in price_position else "待确认"
    else:
        valuation_status = "风险"

    trend_status = _hypothesis_status_from_value(
        ma_structure,
        positive_values={"多头", "修复"},
        risk_values={"承压"},
    )
    if trend_status == "成立" and volume_state == "缩量":
        trend_status = "待确认"

    if news_counts["risk"] > news_counts["positive"] and news_counts["risk"] > 0:
        event_status = "风险"
    elif news_counts["positive"] > 0 or (sector_change is not None and sector_change >= 0):
        event_status = "成立"
    elif _has_pending_items(news):
        event_status = "待读取"
    else:
        event_status = "待确认"

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
            "status": "待确认" if invalid_text != "待读取" else "待读取",
            "evidence": f"当前失效位设为{invalid_text}，用于控制短线承接失败风险。",
            "check_next": "优先观察失效位是否被有效跌破，再决定是否降级。",
            "invalidated_by": f"跌破{invalid_text}后无法快速收复，先降低关注优先级。" if invalid_text != "待读取" else "待读取",
        },
    ]


def _refresh_dynamic_sections(pack: Dict[str, Any]) -> None:
    valuation = pack["valuation"]
    current_price = float(valuation["current_price"])
    history = pack.get("_history") or []
    pack["scores"] = _build_dynamic_scores(
        current_price,
        history,
        valuation,
        pack.get("news") or [],
        pack.get("related_sectors") or [],
        pack.get("_financials"),
    )
    pack["decision_reasons"] = _build_dynamic_decision_reasons(
        current_price,
        valuation,
        pack.get("quant_metrics") or [],
        pack.get("related_sectors") or [],
        pack.get("news") or [],
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
            "interpretation": "比较当前价、20日均线与60日均线，判断趋势质量。",
        },
    ]
    for metric in metrics:
        metric["source"] = "computed"
        metric["status"] = "pending" if metric.get("value") == "待读取" else "computed_from_history"
    return metrics


def _derive_sniper_points(current_price: float, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    lows = _history_values(history, "low")
    highs = _history_values(history, "high")
    closes = _history_values(history, "close")
    recent_low = min(lows[-20:]) if lows else current_price * 0.88
    recent_high = max(highs[-20:]) if highs else current_price * 1.12
    swing_high = max(highs[-60:]) if highs else recent_high
    ma20 = _mean(closes[-20:])
    volatility = _annualized_volatility(closes)
    focus_buffer = 0.9 if volatility and volatility >= 80 else 0.94

    focus_low = max(recent_low, current_price * focus_buffer)
    focus_high = min(max(current_price * 1.03, focus_low), recent_high)
    if focus_high <= focus_low:
        focus_high = max(focus_low, current_price * 1.02)
    confirm = max(
        current_price * 1.08,
        ma20 * 1.01 if ma20 else 0,
        min(swing_high, current_price * 1.25),
    )
    invalid = min(current_price * 0.88, recent_low * 0.98)

    return [
        {
            "label": "关注区",
            "price": _market_price_round(current_price),
            "description": f"{focus_low:.3f}-{focus_high:.3f}附近观察承接，不追高。",
            "source": "computed",
            "status": "computed_from_recent_support",
        },
        {
            "label": "确认位",
            "price": _market_price_round(confirm),
            "description": "放量站上确认位后，趋势修复可信度提升。",
            "source": "computed",
            "status": "computed_from_ma20_swing_high",
        },
        {
            "label": "失效位",
            "price": _market_price_round(invalid),
            "description": "跌破失效位说明短线承接失败，需要降级观察。",
            "source": "computed",
            "status": "computed_from_recent_low_risk_rule",
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
        stock["name"] = quote["name"]
    if quote.get("currency"):
        stock["currency"] = "HKD" if quote["currency"] == "HKD" else stock.get("currency", quote["currency"])
    valuation = pack["valuation"]
    valuation.update(_derive_dynamic_valuation(float(current_price), history, stock["market"]))

    pack["quant_metrics"] = _build_live_quant_metrics(float(current_price), history)
    pack["sniper_points"] = _derive_sniper_points(float(current_price), history)
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
    stock_name = (quote or {}).get("name") if isinstance(quote, dict) else None
    if not stock_name and identity:
        stock_name = identity.get("name")
    history = (market_data or {}).get("history") or []
    valuation = _derive_dynamic_valuation(float(current_price), history, market)
    quant_metrics = _build_live_quant_metrics(float(current_price), history)
    sniper_points = _derive_sniper_points(float(current_price), history)
    return {
        "stock": {
            "name": stock_name or code,
            "code": code,
            "market": market,
            "currency": currency,
            "exchange": "HKEX" if market == "H股" else (identity or {}).get("exchange"),
        },
        "valuation": valuation,
        "scores": _build_dynamic_scores(float(current_price), history, valuation, [], []),
        "quant_metrics": quant_metrics,
        "decision_reasons": _build_dynamic_decision_reasons(float(current_price), valuation, quant_metrics, [], []),
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
    sniper_points = pack.get("sniper_points") or []
    focus = sniper_points[0]["description"] if sniper_points else "待读取"
    confirm = float(sniper_points[1]["price"]) if len(sniper_points) > 1 else current_price * 1.08
    invalid = float(sniper_points[2]["price"]) if len(sniper_points) > 2 else current_price * 0.88
    if current_price < valuation_low:
        action = "逢低关注"
        summary = "逢低关注：价格低于动态估值区间下沿，但需要承接确认。"
    elif current_price <= valuation_high:
        action = "观察"
        summary = "观察：价格位于动态估值区间内，等待量价确认。"
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
        entry_plan=f"{focus} 若放量站稳{confirm:.3f}{unit}，趋势确认度提升。",
        risk_trigger=f"若跌破{invalid:.3f}{unit}，说明短线承接失败，应优先控制回撤。",
        evidence_summary=[
            f"当前价{current_price:.3f}{unit}，{valuation['price_position']}",
            "量化指标：" + ("；".join(quant_preview) if quant_preview else "待读取"),
            "关联板块：" + ("、".join(sector_names) if sector_names else "待读取"),
        ],
    )


def _build_deepseek_prompt(pack: Dict[str, Any]) -> str:
    decision_pack = {
        "stock": pack["stock"],
        "valuation": pack["valuation"],
        "scores": pack["scores"],
        "quant_metrics": pack["quant_metrics"],
        "decision_reasons": pack["decision_reasons"],
        "sniper_points": pack["sniper_points"],
        "industry_trend": pack.get("industry_trend"),
        "related_sectors": pack["related_sectors"],
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
            "news.tone 中 positive=利好，risk=利空，neutral=中性；必须把最新资讯纳入判断。"
            "必须尊重 investment_hypotheses 和 data_status；待读取的数据不能当成事实。"
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
        "core_theme": theme,
        "related_sectors": (pack.get("related_sectors") or [])[:6],
        "news": (pack.get("news") or [])[:5],
        "quant_metrics": (pack.get("quant_metrics") or [])[:6],
        "instruction": (
            "请围绕 core_theme 分析该股票所处行业趋势。"
            "输出必须实事求是，简单明了，有利好、有中性、有风险，不要夸张，不承诺收益。"
            "必须基于 related_sectors、news 和 quant_metrics 中已经给出的事实，不要补写未提供的公司事实。"
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
        "temperature": 0.2,
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
    latest_news = _fetch_latest_news(code, pack["stock"]["name"], pack["stock"]["market"])
    if latest_news:
        for item in latest_news:
            item["data_status"] = "latest_public_source"
        pack["news"] = latest_news
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
    pack["_financials"] = _fetch_financial_snapshot(code)
    _refresh_dynamic_sections(pack)
    industry_trend = _try_deepseek_industry_trend(pack)
    pack["industry_trend"] = industry_trend.model_dump()
    pack["investment_hypotheses"] = _build_investment_hypotheses(pack)
    recommendation = _try_deepseek_recommendation(pack) or _fallback_recommendation(pack)
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
