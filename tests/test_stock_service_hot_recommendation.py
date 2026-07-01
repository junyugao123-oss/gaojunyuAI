from datetime import datetime

from src.services import stock_service as stock_service_module
from src.services.stock_service import StockService


def _service_without_repo() -> StockService:
    return StockService.__new__(StockService)


def test_hot_stock_items_prioritize_intraday_gain_over_large_cap_rank():
    service = _service_without_repo()
    rows = [
        {
            "rank": 1,
            "code": "600519.SH",
            "name": "贵州茅台",
            "change_pct": 0.2,
            "amount": 1_000_000_000,
            "reason": "人气榜",
        },
        {
            "rank": 8,
            "code": "603650.SH",
            "name": "彤程新材",
            "change_pct": 6.3,
            "amount": 800_000_000,
            "reason": "飙升榜",
        },
        {
            "rank": 3,
            "code": "688234.SH",
            "name": "天岳先进",
            "change_pct": 3.1,
            "amount": 500_000_000,
            "reason": "飙升榜",
        },
    ]

    items = service._build_hot_stock_items(rows, 10)
    ranked = sorted(items, key=lambda item: (-(item.get("hot_score") or 0), item.get("rank") or 999))

    assert ranked[0]["name"] == "彤程新材"
    assert ranked[0]["hot_score"] > ranked[-1]["hot_score"]
    assert all(item["name"] != "贵州茅台" for item in ranked)


def test_hot_stocks_returns_empty_when_realtime_hot_source_unavailable(monkeypatch):
    service = _service_without_repo()
    stock_service_module._HOT_STOCKS_CACHE.clear()
    monkeypatch.setattr(service, "_fetch_hot_rows_with_timeout", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service, "_fetch_realtime_candidate_hot_rows", lambda *_args, **_kwargs: [])

    result = service.get_hot_stocks(10, timeout_seconds=0.4)

    assert result["stocks"] == []
    assert datetime.fromisoformat(result["generated_at"])


def test_hot_stocks_uses_realtime_candidate_gain_when_primary_source_unavailable(monkeypatch):
    service = _service_without_repo()
    stock_service_module._HOT_STOCKS_CACHE.clear()
    monkeypatch.setattr(service, "_fetch_hot_rows_with_timeout", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        service,
        "_fetch_realtime_candidate_hot_rows",
        lambda *_args, **_kwargs: [
            {
                "rank": 1,
                "code": "603650.SH",
                "name": "彤程新材",
                "price": 93.64,
                "change_pct": 6.3,
                "amount": 3_400_000_000,
                "reason": "实时涨幅候选",
            },
            {
                "rank": 2,
                "code": "600519.SH",
                "name": "贵州茅台",
                "price": 1190.96,
                "change_pct": 0.4,
                "amount": 2_900_000_000,
                "reason": "泛关注大盘股",
            },
        ],
    )

    result = service.get_hot_stocks(10, timeout_seconds=0.4)

    assert result["stocks"][0]["name"] == "彤程新材"
    assert result["stocks"][0]["change_percent"] == 6.3
    assert "涨幅+6.30%" in result["stocks"][0]["reason"]
