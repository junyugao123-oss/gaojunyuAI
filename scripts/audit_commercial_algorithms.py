#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a 2000-stock A/H audit universe and audit commercial algorithms.

The script focuses on the three business-critical modules highlighted in the
product review:

1. 决策引擎: action tier, opportunity/risk score and operating discipline.
2. 六维健康评分: value, valuation cost, growth, profitability, finance, dividend.
3. 为什么是这个结论: whether the explanation is complete and consistent.

It intentionally calls the same internal functions used by the public API so
the audit result reflects production behavior. DeepSeek is not called here; the
goal is deterministic regression and anomaly detection.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.v1.endpoints import commercial_analysis as ca  # noqa: E402


ANCHOR_CODES = [
    # 白酒/消费/高股息/银行
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
    # 医药/制造/周期/新能源
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
    "300750.SZ",
    "002594.SZ",
    "000725.SZ",
    "002415.SZ",
    "000063.SZ",
    # 半导体/机器人/AI/风险股
    "688981.SH",
    "688111.SH",
    "688041.SH",
    "688008.SH",
    "688234.SH",
    "688072.SH",
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
    "HK9888",
    "HK2318",
    "HK1299",
    "HK0941",
]


def _score_by_label(scores: List[Dict[str, Any]]) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for item in scores:
        label = str(item.get("label") or "")
        score = ca._safe_float(item.get("score"))
        if label and score is not None:
            result[label] = round(score, 2)
    return result


def _catalog_by_code(catalog: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in catalog:
        keys = {
            str(item.get("code") or "").upper(),
            str(item.get("canonical_code") or "").upper(),
            str(item.get("display_code") or "").upper(),
        }
        code = str(item.get("code") or "")
        if code.startswith("HK"):
            digits = code[2:].zfill(5)
            keys.update({digits, f"{digits}.HK", f"HK{digits}"})
        for key in keys:
            if key:
                result[key] = item
    return result


def _universe_bucket(item: Dict[str, Any]) -> str:
    name = str(item.get("name") or "")
    code = str(item.get("code") or "")
    if "ST" in name.upper() or "*ST" in name.upper():
        return "risk_st"
    if item.get("market") == "H股":
        return "hk"
    if code.startswith(("688", "300")):
        return "growth_board"
    if code.startswith(("600", "601", "000")):
        return "main_board"
    return "other_a"


def build_universe(limit: int) -> List[Dict[str, Any]]:
    catalog = ca._load_stock_catalog()
    by_code = _catalog_by_code(catalog)
    selected: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: Optional[Dict[str, Any]], bucket: Optional[str] = None) -> None:
        if not item:
            return
        code = str(item.get("code") or "")
        if not code or code in seen:
            return
        row = dict(item)
        row["bucket"] = bucket or _universe_bucket(item)
        selected.append(row)
        seen.add(code)

    for raw_code in ANCHOR_CODES:
        keys = {raw_code.upper(), raw_code.replace(".", "").upper()}
        if raw_code.startswith("HK"):
            digits = raw_code[2:].zfill(5)
            keys.update({digits, f"{digits}.HK", f"HK{digits}"})
        for key in keys:
            if key in by_code:
                add(by_code[key], "anchor")
                break

    cn_items = [item for item in catalog if item.get("market") == "A股"]
    hk_items = [item for item in catalog if item.get("market") == "H股"]
    st_items = [item for item in cn_items if "ST" in str(item.get("name") or "").upper()]
    growth_items = [item for item in cn_items if str(item.get("code") or "").startswith(("688", "300", "002"))]
    main_items = [item for item in cn_items if str(item.get("code") or "").startswith(("600", "601", "000"))]

    for pool, target in (
        (st_items, max(24, int(limit * 0.12))),
        (growth_items, max(60, int(limit * 0.25))),
        (main_items, max(75, int(limit * 0.32))),
        (hk_items, max(45, int(limit * 0.18))),
        (cn_items, limit),
    ):
        for item in sorted(pool, key=lambda row: -float(row.get("popularity") or 0)):
            if len(selected) >= limit:
                break
            add(item)
            if sum(1 for row in selected if row["bucket"] == _universe_bucket(item)) >= target:
                break
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for item in sorted(catalog, key=lambda row: -float(row.get("popularity") or 0)):
            if len(selected) >= limit:
                break
            add(item)

    return selected[:limit]


def select_audit_targets(universe: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Pick a stratified sample from the 2000-stock universe.

    The commercial score must work for more than the current hot names. This
    sample deliberately mixes leaders, ST/risk names, growth boards, H shares,
    large caps and ordinary A shares so the audit catches edge cases.
    """

    limit = max(0, min(limit, len(universe)))
    if limit == 0:
        return []

    buckets: Dict[str, List[Dict[str, Any]]] = {
        "anchor": [],
        "risk_st": [],
        "growth_board": [],
        "hk": [],
        "main_board": [],
        "other_a": [],
    }
    for item in universe:
        bucket = str(item.get("bucket") or _universe_bucket(item))
        buckets.setdefault(bucket, []).append(item)

    quota_plan = [
        ("anchor", max(18, limit // 5)),
        ("risk_st", max(18, limit // 7)),
        ("growth_board", max(24, limit // 4)),
        ("hk", max(20, limit // 5)),
        ("main_board", max(24, limit // 4)),
        ("other_a", max(12, limit // 10)),
    ]
    selected: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: Dict[str, Any]) -> None:
        code = str(item.get("code") or "")
        if code and code not in seen and len(selected) < limit:
            selected.append(item)
            seen.add(code)

    for bucket, quota in quota_plan:
        pool = sorted(buckets.get(bucket, []), key=lambda row: -float(row.get("popularity") or 0))
        for item in pool[:quota]:
            add(item)

    # Fill the remaining slots by interleaving buckets so no single market style
    # dominates the audit when a quota cannot be fully satisfied.
    ordered_buckets = [name for name, _ in quota_plan]
    cursor = 0
    while len(selected) < limit and cursor < len(universe):
        made_progress = False
        for bucket in ordered_buckets:
            pool = buckets.get(bucket, [])
            if cursor < len(pool):
                add(pool[cursor])
                made_progress = True
                if len(selected) >= limit:
                    break
        if not made_progress:
            break
        cursor += 1

    for item in universe:
        if len(selected) >= limit:
            break
        add(item)

    return selected


def _market_data(code: str) -> Optional[Dict[str, Any]]:
    return ca._try_tencent_market_data(code) or ca._try_stock_api_market_data(code)


def _data_coverage(pack: Dict[str, Any]) -> str:
    parts = []
    parts.append("行情" if pack.get("_quote_status") else "行情缺失")
    parts.append("财务" if pack.get("_financials") else "财务缺失")
    sectors = pack.get("related_sectors") or []
    news = pack.get("news") or []
    parts.append("板块" if sectors and not ca._has_pending_items(sectors) else "板块缺失")
    parts.append("资讯" if news and not ca._has_pending_items(news) else "资讯缺失")
    return "、".join(parts)


def _module_findings(pack: Dict[str, Any], recommendation: Any, quantified: Dict[str, Any]) -> List[str]:
    scores = pack.get("scores") or []
    score_map = _score_by_label(scores)
    valuation = pack.get("valuation") or {}
    current_price = ca._safe_float(valuation.get("current_price"))
    high = ca._safe_float(valuation.get("high"))
    stock = pack.get("stock") or {}
    financials = pack.get("_financials") or {}
    sectors = pack.get("related_sectors") or []
    news = pack.get("_news_pool") or pack.get("news") or []
    profile = pack.get("_company_profile") or {}
    reasons = pack.get("decision_reasons") or []
    findings: List[str] = []

    is_financial = ca._is_financial_institution(stock, sectors, profile)
    distress_flags = ca._distress_risk_flags(stock, profile, news)
    risk_adjustment = ca._fundamental_risk_adjustment(
        financials,
        is_st_stock=ca._is_special_treatment_stock(stock),
        is_financial_institution=is_financial,
        distress_flags=distress_flags,
    )
    risk_flags = list(dict.fromkeys([*distress_flags, *[str(item) for item in risk_adjustment.get("flags", [])]]))

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
    }
    if set(risk_flags) & severe_value_flags and score_map.get("价值", 0.0) >= 5.6:
        findings.append("六维评分:风险资产价值分偏高")
    if ca._is_special_treatment_stock(stock) and score_map.get("成长", 0.0) >= 4.6:
        findings.append("六维评分:ST股票成长分偏高")
    net_profit = ca._safe_float(financials.get("net_profit"))
    eps = ca._safe_float(financials.get("eps"))
    if (net_profit is not None and net_profit < 0 or eps is not None and eps < 0) and score_map.get("盈利能力", 0.0) >= 4.4:
        findings.append("六维评分:亏损股盈利分偏高")
    dividend_yield = ca._safe_float(financials.get("dividend_yield_ttm"))
    dividend_per_share = ca._safe_float(financials.get("dividend_per_share_ttm"))
    if dividend_yield is None and dividend_per_share and current_price:
        dividend_yield = dividend_per_share / current_price * 100.0
    if dividend_yield is not None and dividend_yield >= 3.0 and score_map.get("分红", 0.0) < 6.6 and not risk_flags:
        findings.append("六维评分:高股息分红分偏低")
    if current_price is not None and high is not None and current_price > high and score_map.get("估值性价比", 0.0) >= 6.2:
        findings.append("六维评分:高于估值上沿但性价比分偏高")

    action = str(quantified.get("action") or getattr(recommendation, "action", ""))
    risk_score = ca._safe_float(quantified.get("risk_score")) or 0.0
    opportunity_score = ca._safe_float(quantified.get("opportunity_score")) or 0.0
    if action == "积极买入" and risk_score > 48:
        findings.append("决策引擎:积极买入风险分过高")
    if action in {"积极买入", "逢低关注"} and opportunity_score < 60:
        findings.append("决策引擎:偏积极动作机会分不足")
    if action == "回避" and opportunity_score > 68 and risk_score < 70:
        findings.append("决策引擎:回避动作与机会风险分冲突")
    if ca._is_special_treatment_stock(stock) and action in {"积极买入", "逢低关注"}:
        findings.append("决策引擎:ST股票动作过积极")
    confirm_point = next((item for item in pack.get("sniper_points") or [] if item.get("label") == "确认位"), None)
    confirm_price = ca._safe_float((confirm_point or {}).get("price"))
    if current_price and confirm_price and confirm_price > current_price * 1.6:
        findings.append("决策引擎:确认位距离现价过远")

    reason_text = " ".join(str(item.get("description") or "") for item in reasons)
    required_titles = {"推荐建议", "公司基本面", "长期成长逻辑", "动态估值区间", "狙击点位", "趋势与风险"}
    actual_titles = {str(item.get("title") or "") for item in reasons}
    missing_titles = required_titles - actual_titles
    if missing_titles:
        findings.append("结论解释:缺少" + "、".join(sorted(missing_titles)))
    stock_name = str(stock.get("name") or "")
    if stock_name and len(stock_name) >= 3 and stock_name not in reason_text:
        findings.append("结论解释:未提及股票名称或公司主体")
    if "待读取" in reason_text or "待确认" in reason_text:
        findings.append("结论解释:包含商业展示不应出现的待读取/待确认")
    if "公司基本面" in actual_titles:
        basic_reason = next((str(item.get("description") or "") for item in reasons if item.get("title") == "公司基本面"), "")
        if len(basic_reason) < 45:
            findings.append("结论解释:公司基本面过短")
        if ca._company_basic_mismatch(basic_reason, profile, sectors, stock):
            findings.append("结论解释:公司基本面疑似行业错配")

    return findings or ["通过"]


def audit_one(code: str, *, include_news: bool) -> Dict[str, Any]:
    normalized_code = ca._normalize_code(code)
    market_data = _market_data(normalized_code)
    if not market_data:
        raise RuntimeError("行情不可读")

    pack = ca._generic_pack(normalized_code, market_data)
    ca._merge_live_market_data(pack, market_data)
    stock = pack.get("stock") or {}
    stock_market = str(stock.get("market") or "A股")
    stock_name = str(stock.get("name") or normalized_code)

    news: List[Dict[str, Any]] = []
    if include_news:
        news = ca._fetch_latest_news(normalized_code, stock_name, stock_market, limit=ca._NEWS_POOL_LIMIT)
        for item in news:
            item["data_status"] = "latest_public_source"
    pack["_news_pool"] = news
    pack["news"] = news[: ca._NEWS_DISPLAY_LIMIT] if news else []
    pack["_news_summary"] = ca._build_news_summary(news, pack.get("news") or [])
    pack["related_sectors"] = ca._fetch_related_sectors(normalized_code, stock_market) or []
    pack["_company_profile"] = ca._fetch_company_profile(normalized_code, stock_market, stock_name)
    pack["_financials"] = ca._fetch_financial_snapshot(normalized_code)
    pack["_valuation_context"] = ca._fetch_valuation_ratio_context(normalized_code)
    ca._refresh_dynamic_sections(pack)

    quantified = ca._classify_recommendation_action(pack)
    recommendation = ca._fallback_recommendation(pack)
    findings = _module_findings(pack, recommendation, quantified)
    scores = _score_by_label(pack.get("scores") or [])
    valuation = pack.get("valuation") or {}
    confirm_point = next((item for item in pack.get("sniper_points") or [] if item.get("label") == "确认位"), None)
    return {
        "code": normalized_code,
        "name": stock_name,
        "market": stock_market,
        "price": valuation.get("current_price"),
        "valuation_low": valuation.get("low"),
        "valuation_high": valuation.get("high"),
        "valuation_position": valuation.get("price_position"),
        "action": quantified.get("action"),
        "opportunity_score": quantified.get("opportunity_score"),
        "risk_score": quantified.get("risk_score"),
        "relative_position": quantified.get("relative_position"),
        "summary": quantified.get("summary"),
        "value_score": scores.get("价值"),
        "valuation_cost_score": scores.get("估值性价比"),
        "growth_score": scores.get("成长"),
        "profitability_score": scores.get("盈利能力"),
        "finance_score": scores.get("财务"),
        "dividend_score": scores.get("分红"),
        "confirm_price": (confirm_point or {}).get("price"),
        "data_coverage": _data_coverage(pack),
        "decision_reasons": json.dumps(pack.get("decision_reasons") or [], ensure_ascii=False),
        "score_details": json.dumps(pack.get("scores") or [], ensure_ascii=False),
        "findings": "、".join(findings),
    }


def _init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_universe (
            run_id TEXT,
            rank INTEGER,
            code TEXT,
            name TEXT,
            market TEXT,
            exchange TEXT,
            popularity REAL,
            bucket TEXT,
            PRIMARY KEY (run_id, code)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS algorithm_audit (
            run_id TEXT,
            code TEXT,
            name TEXT,
            market TEXT,
            price REAL,
            valuation_low REAL,
            valuation_high REAL,
            valuation_position TEXT,
            action TEXT,
            opportunity_score REAL,
            risk_score REAL,
            relative_position REAL,
            summary TEXT,
            value_score REAL,
            valuation_cost_score REAL,
            growth_score REAL,
            profitability_score REAL,
            finance_score REAL,
            dividend_score REAL,
            confirm_price REAL,
            data_coverage TEXT,
            findings TEXT,
            decision_reasons TEXT,
            score_details TEXT,
            elapsed_ms INTEGER,
            error TEXT,
            PRIMARY KEY (run_id, code)
        )
        """
    )
    return conn


def _write_universe(conn: sqlite3.Connection, run_id: str, universe: List[Dict[str, Any]]) -> None:
    rows = [
        (
            run_id,
            index,
            ca._normalize_code(str(item.get("code") or "")),
            item.get("name"),
            item.get("market"),
            item.get("exchange"),
            item.get("popularity"),
            item.get("bucket"),
        )
        for index, item in enumerate(universe, start=1)
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO stock_universe
        (run_id, rank, code, name, market, exchange, popularity, bucket)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()


def _write_audit_row(conn: sqlite3.Connection, run_id: str, row: Dict[str, Any]) -> None:
    fields = [
        "code",
        "name",
        "market",
        "price",
        "valuation_low",
        "valuation_high",
        "valuation_position",
        "action",
        "opportunity_score",
        "risk_score",
        "relative_position",
        "summary",
        "value_score",
        "valuation_cost_score",
        "growth_score",
        "profitability_score",
        "finance_score",
        "dividend_score",
        "confirm_price",
        "data_coverage",
        "findings",
        "decision_reasons",
        "score_details",
        "elapsed_ms",
        "error",
    ]
    conn.execute(
        f"""
        INSERT OR REPLACE INTO algorithm_audit
        (run_id, {", ".join(fields)})
        VALUES ({", ".join(["?"] * (len(fields) + 1))})
        """,
        [run_id, *[row.get(field) for field in fields]],
    )
    conn.commit()


def _export_csv(csv_path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "code",
        "name",
        "market",
        "price",
        "valuation_low",
        "valuation_high",
        "valuation_position",
        "action",
        "opportunity_score",
        "risk_score",
        "relative_position",
        "summary",
        "value_score",
        "valuation_cost_score",
        "growth_score",
        "profitability_score",
        "finance_score",
        "dividend_score",
        "confirm_price",
        "data_coverage",
        "decision_reasons",
        "score_details",
        "findings",
        "elapsed_ms",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fieldnames})


def _print_action_distribution(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    counter = Counter(str(row.get("action") or "审计失败") for row in rows)
    total = len(rows)
    ordered_actions = ["积极买入", "逢低关注", "观察", "谨慎", "回避", "审计失败"]
    print("\n=== Action distribution ===")
    for action in ordered_actions:
        count = counter.get(action, 0)
        if count:
            print(f"{action}: {count}/{total} ({count / total:.1%})")
    for action, count in sorted(counter.items()):
        if action not in ordered_actions:
            print(f"{action}: {count}/{total} ({count / total:.1%})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit commercial decision/six-score/explanation algorithms.")
    parser.add_argument("--universe-limit", type=int, default=2000, help="Number of A/H stocks to collect into DB.")
    parser.add_argument("--deep-limit", type=int, default=300, help="Number of stocks to run deep algorithm audit.")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent deep audit workers.")
    parser.add_argument("--include-news", action="store_true", help="Fetch latest news for the audited rows. Slower.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"commercial_algorithm_audit_{stamp}"
    db_path = args.output_dir / f"{run_id}.sqlite"
    csv_path = args.output_dir / f"{run_id}.csv"
    json_path = args.output_dir / f"{run_id}.json"

    universe = build_universe(max(1, args.universe_limit))
    conn = _init_db(db_path)
    _write_universe(conn, run_id, universe)
    print(f"universe={len(universe)} db={db_path}", flush=True)

    deep_rows: List[Dict[str, Any]] = []
    audit_targets = select_audit_targets(universe, max(0, min(args.deep_limit, len(universe))))

    def run_target(item: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        code = str(item.get("code") or "")
        try:
            row = audit_one(code, include_news=args.include_news)
            row["elapsed_ms"] = int((time.time() - start) * 1000)
            row["error"] = ""
            return row
        except Exception as exc:  # noqa: BLE001 - keep the audit running.
            return {
                "code": code,
                "name": item.get("name"),
                "market": item.get("market"),
                "elapsed_ms": int((time.time() - start) * 1000),
                "error": str(exc),
                "findings": "审计失败",
            }

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(run_target, item): item for item in audit_targets}
        for index, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            deep_rows.append(row)
            _write_audit_row(conn, run_id, row)
            status = row.get("findings") or row.get("error") or "通过"
            print(f"[{index:04d}/{len(audit_targets):04d}] {row.get('code')} {row.get('name')} {status}", flush=True)

    _export_csv(csv_path, deep_rows)
    json_path.write_text(
        json.dumps({"run_id": run_id, "universe": universe, "audit_rows": deep_rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    conn.close()

    passed = sum(1 for row in deep_rows if row.get("findings") == "通过")
    errors = sum(1 for row in deep_rows if row.get("error"))
    print("\n=== Commercial algorithm audit ===")
    print(f"universe={len(universe)} deep_audited={len(deep_rows)} passed={passed} errors={errors}")
    _print_action_distribution(deep_rows)
    print(f"db={db_path}")
    print(f"csv={csv_path}")
    print(f"json={json_path}")
    return 0 if universe else 1


if __name__ == "__main__":
    raise SystemExit(main())
