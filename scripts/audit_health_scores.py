#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit 每日股研AI six-dimensional health scores on a stock basket.

The script intentionally calls the same internal calculation functions used by
the commercial analysis API. It does not call DeepSeek. The goal is to make the
six scores explainable, repeatable and easy to spot-check before release.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.v1.endpoints import commercial_analysis as ca  # noqa: E402


DEFAULT_STOCKS = [
    # 大消费、金融、高股息
    "600519.SH",
    "000858.SZ",
    "600887.SH",
    "000333.SZ",
    "000651.SZ",
    "600036.SH",
    "601398.SH",
    "600900.SH",
    "601318.SH",
    "601899.SH",
    # 医药、制造、周期
    "600276.SH",
    "300760.SZ",
    "603259.SH",
    "600309.SH",
    "601088.SH",
    "600031.SH",
    "601012.SH",
    "600438.SH",
    "600150.SH",
    "600111.SH",
    # 新能源、半导体、AI硬件、高成长
    "300750.SZ",
    "002594.SZ",
    "000725.SZ",
    "002415.SZ",
    "000063.SZ",
    "688981.SH",
    "688111.SH",
    "688041.SH",
    "688008.SH",
    "688234.SH",
    # 热门/普通/风险样本
    "603650.SH",
    "002129.SZ",
    "300027.SZ",
    "600221.SH",
    "000503.SZ",
    "002475.SZ",
    "300274.SZ",
    "603986.SH",
    "688256.SH",
    "300124.SZ",
    # 港股龙头与成长样本
    "HK0700",
    "HK9988",
    "HK3690",
    "HK9618",
    "HK1810",
    "HK1024",
    "HK0388",
    "HK0939",
    "HK1211",
    "HK6651",
]


def _score_by_label(scores: List[Dict[str, Any]]) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for item in scores:
        label = str(item.get("label") or "")
        score = ca._safe_float(item.get("score"))
        if label and score is not None:
            result[label] = score
    return result


def _score_statuses(scores: List[Dict[str, Any]]) -> str:
    pairs = []
    for item in scores:
        label = str(item.get("label") or "")
        status = str(item.get("status") or "")
        if label:
            pairs.append(f"{label}:{status}")
    return " | ".join(pairs)


def _market_data(code: str) -> Optional[Dict[str, Any]]:
    return ca._try_tencent_market_data(code) or ca._try_stock_api_market_data(code)


def _data_coverage(financials: Optional[Dict[str, Any]], sectors: List[Dict[str, Any]], news: List[Dict[str, Any]]) -> str:
    parts = [
        "财务" if financials else "财务缺失",
        "板块" if sectors and not ca._has_pending_items(sectors) else "板块缺失",
        "资讯" if news and not ca._has_pending_items(news) else "资讯缺失",
    ]
    return "、".join(parts)


def _audit_findings(
    score_map: Dict[str, float],
    financials: Optional[Dict[str, Any]],
    valuation: Dict[str, Any],
    risk_flags: List[str],
    *,
    is_financial_institution: bool = False,
) -> List[str]:
    findings: List[str] = []
    current_price = ca._safe_float(valuation.get("current_price"))
    high = ca._safe_float(valuation.get("high"))
    net_profit = ca._safe_float((financials or {}).get("net_profit"))
    eps = ca._safe_float((financials or {}).get("eps"))
    book_value = ca._safe_float((financials or {}).get("book_value_per_share"))
    operating_cash_flow = ca._safe_float((financials or {}).get("operating_cash_flow_per_share"))
    debt_ratio = ca._safe_float((financials or {}).get("debt_ratio"))
    dividend_yield = ca._safe_float((financials or {}).get("dividend_yield_ttm"))
    dividend_per_share = ca._safe_float((financials or {}).get("dividend_per_share_ttm"))
    if dividend_yield is None and dividend_per_share and current_price:
        dividend_yield = dividend_per_share / current_price * 100.0

    severe_value_flags = {
        "ST/退市风险",
        "ST风险",
        "清盘/退市/停牌风险",
        "高风险困境资产",
        "债务/流动性风险",
        "持续经营/审计风险",
        "归母净利润亏损",
        "扣非亏损",
        "EPS亏损",
        "每股净资产偏弱",
        "销售净利率为负",
        "ROE为负",
        "低价股流动性风险",
    }
    if set(risk_flags) & severe_value_flags and score_map.get("价值", 0.0) >= 6.0:
        findings.append("风险股价值分偏高")
    if (net_profit is not None and net_profit < 0 or eps is not None and eps < 0) and score_map.get("盈利能力", 0.0) >= 4.5:
        findings.append("亏损股盈利分偏高")
    if (book_value is not None and book_value <= 0) and score_map.get("财务", 0.0) >= 4.0:
        findings.append("负净资产财务分偏高")
    if (operating_cash_flow is not None and operating_cash_flow < 0) and score_map.get("财务", 0.0) >= 5.2:
        findings.append("现金流为负财务分偏高")
    if (
        not is_financial_institution
        and debt_ratio is not None
        and debt_ratio >= 90
        and score_map.get("财务", 0.0) >= 4.8
    ):
        findings.append("高负债财务分偏高")
    dividend_should_score = (
        dividend_yield is not None
        and dividend_yield >= 3.0
        and not (net_profit is not None and net_profit < 0)
        and not (eps is not None and eps < 0)
        and not (book_value is not None and book_value <= 0)
    )
    if dividend_should_score and score_map.get("分红", 0.0) < 6.8:
        findings.append("高股息分红分偏低")
    if current_price is not None and high is not None and current_price > high and score_map.get("估值性价比", 0.0) >= 6.0:
        findings.append("高于估值区间但性价比分偏高")
    return findings


def audit_one(code: str, *, include_news: bool = False) -> Dict[str, Any]:
    market_data = _market_data(code)
    if not market_data:
        raise RuntimeError("行情不可读")

    quote = market_data.get("quote") or {}
    history = market_data.get("history") or []
    normalized_code = ca._normalize_code(code)
    stock = ca._catalog_identity(normalized_code) or {
        "code": normalized_code,
        "name": quote.get("name") or normalized_code,
        "market": quote.get("market") or ("H股" if normalized_code.startswith("HK") else "A股"),
    }
    name = str(quote.get("name") or stock.get("name") or normalized_code)
    market = str(stock.get("market") or quote.get("market") or "")
    current_price = float(quote.get("price"))

    financials = ca._fetch_financial_snapshot(normalized_code)
    profile = ca._fetch_company_profile(normalized_code, market, name)
    sectors = ca._fetch_related_sectors(normalized_code, market)
    news: List[Dict[str, Any]] = []
    if include_news:
        news = ca._fetch_latest_news(normalized_code, name, market, limit=ca._NEWS_DISPLAY_LIMIT)
    valuation_context = ca._fetch_valuation_ratio_context(normalized_code)
    valuation = ca._derive_dynamic_valuation(
        current_price,
        history,
        market,
        news=news,
        sectors=sectors,
        financials=financials,
        valuation_context=valuation_context,
    )
    scores = ca._build_dynamic_scores(
        current_price,
        history,
        valuation,
        news,
        sectors,
        financials,
        stock,
        profile,
    )
    score_map = _score_by_label(scores)
    distress_flags = ca._distress_risk_flags(stock, profile, news)
    is_financial_institution = ca._is_financial_institution(stock, sectors, profile)
    risk_adjustment = ca._fundamental_risk_adjustment(
        financials,
        is_st_stock=ca._is_special_treatment_stock(stock),
        is_financial_institution=is_financial_institution,
        distress_flags=distress_flags,
    )
    risk_flags = list(dict.fromkeys([*distress_flags, *[str(item) for item in risk_adjustment.get("flags", [])]]))
    findings = _audit_findings(
        score_map,
        financials,
        valuation,
        risk_flags,
        is_financial_institution=is_financial_institution,
    )
    avg_score = round(sum(score_map.values()) / len(score_map), 2) if score_map else None
    return {
        "code": normalized_code,
        "name": name,
        "market": market,
        "price": current_price,
        "valuation_low": valuation.get("low"),
        "valuation_high": valuation.get("high"),
        "valuation_position": valuation.get("price_position"),
        "价值": score_map.get("价值"),
        "估值性价比": score_map.get("估值性价比"),
        "成长": score_map.get("成长"),
        "盈利能力": score_map.get("盈利能力"),
        "财务": score_map.get("财务"),
        "分红": score_map.get("分红"),
        "average_score": avg_score,
        "data_coverage": _data_coverage(financials, sectors, news),
        "financial_source": (financials or {}).get("source") or "",
        "risk_flags": "、".join(risk_flags),
        "audit_findings": "、".join(findings) if findings else "通过",
        "score_statuses": _score_statuses(scores),
    }


def _iter_codes(raw_codes: Iterable[str]) -> List[str]:
    codes: List[str] = []
    seen = set()
    for raw in raw_codes:
        code = raw.strip()
        if not code:
            continue
        key = ca._normalize_code(code)
        if key in seen:
            continue
        codes.append(code)
        seen.add(key)
    return codes


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit six-dimensional health scores.")
    parser.add_argument("--codes", nargs="*", default=None, help="Stock codes to audit. Defaults to a 50-stock basket.")
    parser.add_argument("--codes-file", type=Path, help="Optional newline-separated stock code file.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of stocks to audit.")
    parser.add_argument("--include-news", action="store_true", help="Also fetch latest news. Slower but closer to API output.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()

    raw_codes: List[str] = []
    if args.codes:
        raw_codes.extend(args.codes)
    if args.codes_file:
        raw_codes.extend(args.codes_file.read_text(encoding="utf-8").splitlines())
    if not raw_codes:
        raw_codes = DEFAULT_STOCKS
    codes = _iter_codes(raw_codes)[: max(1, args.limit)]

    rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for index, code in enumerate(codes, start=1):
        print(f"[{index:02d}/{len(codes):02d}] {code}", flush=True)
        try:
            rows.append(audit_one(code, include_news=args.include_news))
        except Exception as exc:  # noqa: BLE001 - audit must continue.
            errors.append({"code": code, "error": str(exc)})
            print(f"  ! {code}: {exc}", file=sys.stderr, flush=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = args.output_dir / f"health_score_audit_{stamp}.csv"
    json_path = args.output_dir / f"health_score_audit_{stamp}.json"
    fieldnames = [
        "code",
        "name",
        "market",
        "price",
        "valuation_low",
        "valuation_high",
        "valuation_position",
        "价值",
        "估值性价比",
        "成长",
        "盈利能力",
        "财务",
        "分红",
        "average_score",
        "data_coverage",
        "financial_source",
        "risk_flags",
        "audit_findings",
        "score_statuses",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(
        json.dumps({"rows": rows, "errors": errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    findings_counter = Counter(row["audit_findings"] for row in rows)
    print("\n=== Audit summary ===")
    print(f"audited={len(rows)} errors={len(errors)}")
    for label, count in findings_counter.most_common():
        print(f"{label}: {count}")
    print(f"csv={csv_path}")
    print(f"json={json_path}")
    if errors:
        print("errors:")
        for item in errors:
            print(f"- {item['code']}: {item['error']}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
