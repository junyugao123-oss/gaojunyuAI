from api.v1.endpoints import commercial_analysis as ca


def test_realtime_quote_name_spaces_are_cleaned():
    assert ca._clean_realtime_stock_name("万  科A") == "万科A"
    assert ca._clean_realtime_stock_name("新 希 望") == "新希望"


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


def test_robotics_actuator_and_thermal_management_growth_is_not_understated():
    stock = {"name": "三花智控", "code": "002050.SZ", "market": "A股"}
    profile = {
        "business": "新能源汽车热管理、机器人执行器、伺服控制和工业自动化核心零部件。",
        "products": ["热管理系统", "机器人执行器", "伺服系统"],
    }
    sectors = [
        {"name": "机器人", "relevance": "高", "reason": "执行器与伺服控制产业链卡位"},
        {"name": "汽车热管理", "relevance": "高", "reason": "新能源汽车热管理核心部件"},
    ]

    inputs = ca._growth_quality_inputs(profile, sectors, {}, [], stock=stock)
    labels = [item["label"] for item in inputs["growth_profiles"]]

    assert "具身智能与机器人" in labels
    assert inputs["industry_space"] >= 0.74
    assert inputs["moat"] >= 0.6


def test_platform_ai_cloud_leader_growth_is_supported_by_industry_profile():
    stock = {"name": "阿里巴巴-W", "code": "HK9988", "market": "H股"}
    profile = {
        "business": "云计算、AI云服务、大模型应用、AI电商和平台型数字商业基础设施。",
        "products": ["云服务", "大模型应用", "AI电商"],
    }
    sectors = [
        {"name": "云计算", "relevance": "高", "reason": "AI基础设施和模型服务"},
        {"name": "AI应用", "relevance": "高", "reason": "平台型AI应用场景"},
    ]

    inputs = ca._growth_quality_inputs(profile, sectors, {}, [], stock=stock)
    labels = [item["label"] for item in inputs["growth_profiles"]]

    assert "AI算力与先进半导体" in labels
    assert "AI智能体与数字原生应用" in labels
    assert inputs["industry_space"] >= 0.74


def test_moutai_basic_reason_keeps_baijiu_identity_when_weak_media_terms_appear():
    stock = {"name": "贵州茅台", "code": "600519.SH", "market": "A股"}
    profile = {
        "business": "贵州茅台酒及系列酒生产销售，同时公开资料提到品牌授权、实景体验等延展场景。",
        "products": ["贵州茅台酒", "茅台系列酒"],
    }
    sectors = [
        {"name": "品牌授权", "relevance": "中", "reason": "弱相关延展词"},
        {"name": "实景娱乐", "relevance": "中", "reason": "弱相关延展词"},
    ]

    assert ca._infer_company_business_category(profile, sectors, stock) == "baijiu"
    reason = ca._company_basic_reason(stock, profile, sectors)

    assert "高端白酒/食品饮料" in reason
    assert "贵州茅台酒" in reason
    assert "内容IP" not in reason
    assert "影视娱乐" not in reason


def test_verified_moutai_profile_overrides_bad_deepseek_company_position():
    stock = {"name": "贵州茅台", "code": "600519.SH", "market": "A股"}
    profile = ca._verified_company_profile("600519.SH", "贵州茅台")
    parsed = {
        "company_position": "贵州茅台属于内容IP、影视娱乐与文旅消费链条。",
        "business_model": "依靠影视内容、票房和品牌授权赚钱。",
        "industry_logic": "影视院线景气影响公司估值。",
        "watch_points": "关注票房、剧集表现和IP授权。",
        "risk_boundary": "若内容储备不足则基本面变弱。",
    }

    reason = ca._format_deepseek_company_basic(parsed, stock, profile, {}, [])

    assert reason is not None
    assert "高端白酒" in reason
    assert "茅台酒" in reason
    assert "内容IP" not in reason
    assert "影视娱乐" not in reason
