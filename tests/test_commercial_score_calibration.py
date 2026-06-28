from api.v1.endpoints import commercial_analysis as ca


def test_routine_acquisition_suspension_is_not_delisting_distress():
    stock = {"name": "拓荆科技", "code": "688072.SH", "market": "A股"}
    news = [
        {
            "title": "拓荆科技筹划购买半导体资产 股票下周一起停牌",
            "summary": "",
        }
    ]

    assert "清盘/退市/停牌风险" not in ca._distress_risk_flags(stock, {}, news)


def test_benign_going_concern_phrase_is_not_audit_risk():
    stock = {"name": "中国银行", "code": "HK3988", "market": "H股"}
    profile = {"intro": "中国银行股份有限公司是中国持续经营时间最久的银行。"}

    assert "持续经营/审计风险" not in ca._distress_risk_flags(stock, profile, [])


def test_financial_institution_debt_ratio_is_not_industrial_penalty():
    financials = {
        "net_profit": 1_000_000_000,
        "eps": 0.8,
        "book_value_per_share": 8.0,
        "debt_ratio": 92.0,
    }

    industrial = ca._fundamental_risk_adjustment(
        financials,
        is_st_stock=False,
        is_financial_institution=False,
        distress_flags=[],
    )
    bank = ca._fundamental_risk_adjustment(
        financials,
        is_st_stock=False,
        is_financial_institution=True,
        distress_flags=[],
    )

    assert "资产负债率高" in industrial["flags"]
    assert "资产负债率高" not in bank["flags"]


def test_semiconductor_equipment_maps_to_growth_profile():
    stock = {"name": "拓荆科技", "code": "688072.SH", "market": "A股"}
    profile = {
        "business": "高端半导体专用设备的研发、生产、销售与技术服务，覆盖薄膜沉积CVD、ALD、PECVD设备。",
    }
    sectors = [{"name": "半导体设备", "relevance": "高", "reason": "薄膜沉积设备国产替代"}]

    inputs = ca._growth_quality_inputs(profile, sectors, {}, [], stock=stock)
    labels = [item["label"] for item in inputs["growth_profiles"]]

    assert "AI算力与先进半导体" in labels
    assert inputs["industry_space"] >= 0.7

