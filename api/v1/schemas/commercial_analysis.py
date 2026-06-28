# -*- coding: utf-8 -*-
"""Public commercial analysis page schemas."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CommercialStockIdentity(BaseModel):
    """Basic stock identity shown on the public analysis page."""

    name: str = Field(..., description="股票名称")
    code: str = Field(..., description="展示代码")
    market: str = Field(..., description="市场：A股/H股")
    currency: str = Field(..., description="币种")
    exchange: Optional[str] = Field(None, description="交易所")


class CommercialAiRecommendation(BaseModel):
    """Short, recommendation-like conclusion generated from a decision pack."""

    source: str = Field(..., description="deepseek/fallback")
    model: str = Field(..., description="模型名称或fallback")
    status: str = Field(..., description="ok/fallback/error")
    action: str = Field(..., description="关注/观察/回避等动作")
    summary: str = Field(..., description="一行核心结论")
    entry_plan: str = Field(..., description="关注或入场计划")
    risk_trigger: str = Field(..., description="失效或风险触发条件")
    evidence_summary: List[str] = Field(default_factory=list, description="结论依据摘要")


class CommercialValuationRange(BaseModel):
    """Valuation rail and current price marker."""

    label: str = Field(..., description="估值区间名称")
    currency_label: str = Field(..., description="币种单位展示")
    low: float = Field(..., description="合理估值下沿")
    high: float = Field(..., description="合理估值上沿")
    current_price: float = Field(..., description="当前价")
    marker_percent: float = Field(..., description="页面估值条marker位置百分比")
    price_position: str = Field(..., description="当前价格所处位置")
    source: Optional[str] = Field(None, description="computed/realtime/pending")
    status: Optional[str] = Field(None, description="字段状态")
    inputs: List[str] = Field(default_factory=list, description="计算输入")


class CommercialScore(BaseModel):
    """Six-dimensional health score."""

    label: str
    score: float
    description: str
    source: Optional[str] = Field(None, description="computed/model_assumption/pending")
    status: Optional[str] = Field(None, description="字段状态")
    inputs: List[str] = Field(default_factory=list, description="计算输入")


class CommercialQuantMetric(BaseModel):
    """Professional quantitative metric displayed on the page."""

    label: str
    value: str
    percentile: str
    interpretation: str
    source: Optional[str] = Field(None, description="computed/pending")
    status: Optional[str] = Field(None, description="字段状态")


class CommercialDecisionReason(BaseModel):
    """Why the recommendation is produced."""

    title: str
    description: str


class CommercialSniperPoint(BaseModel):
    """Actionable price point."""

    label: str
    price: float
    description: str
    source: Optional[str] = Field(None, description="computed/pending")
    status: Optional[str] = Field(None, description="字段状态")


class CommercialIndustryTrendItem(BaseModel):
    """One concise industry trend signal."""

    tone: str = Field(..., description="positive/neutral/risk/pending")
    label: str = Field(..., description="利好/中性/风险/待读取")
    impact_score: int = Field(0, ge=-100, le=100, description="行业趋势量化影响分，-100=非常利空，100=非常利好")
    title: str = Field(..., description="趋势标题")
    description: str = Field(..., description="趋势说明")


class CommercialIndustryTrend(BaseModel):
    """Industry trend summary generated from sector/news context."""

    theme: str = Field(..., description="核心行业主题")
    source: str = Field(..., description="deepseek/pending")
    status: str = Field(..., description="ok/pending")
    summary: str = Field(..., description="简短行业趋势判断")
    items: List[CommercialIndustryTrendItem] = Field(default_factory=list, description="利好/中性/风险趋势项")


class CommercialRelatedSector(BaseModel):
    """Related sector or theme."""

    name: str
    heat: str
    reason: str
    relevance: Optional[str] = Field(None, description="业务相关度：核心/高/中高/中")
    realtime_change: Optional[str] = Field(None, description="实时参考板块涨跌幅")
    realtime_board: Optional[str] = Field(None, description="实时参考板块名称")
    data_source: Optional[str] = Field(None, description="公司资料/实时板块")
    data_status: Optional[str] = Field(None, description="字段状态")


class CommercialNewsItem(BaseModel):
    """Clickable source or news item."""

    title: str
    source: str
    date: str
    url: str
    tone: str = Field("positive", description="positive/neutral/risk")
    data_status: Optional[str] = Field(None, description="latest/pending")


class CommercialNewsSummary(BaseModel):
    """News-pool coverage summary shown above selected news items."""

    pool_count: int = Field(0, description="本次资讯池聚合条数")
    display_count: int = Field(0, description="页面当前展示条数")
    positive_count: int = Field(0, description="利好资讯条数")
    risk_count: int = Field(0, description="利空资讯条数")
    neutral_count: int = Field(0, description="中性资讯条数")
    latest_date: str = Field("待读取", description="资讯池最新日期")
    description: str = Field("待读取", description="资讯池说明")


class CommercialDataQuality(BaseModel):
    """Data freshness and decision-engine status."""

    updated_at: str
    quote_source: str
    quote_status: str
    ai_status: str
    notes: List[str] = Field(default_factory=list)


class CommercialDataAuditItem(BaseModel):
    """Field-level data provenance for high-risk finance UI blocks."""

    section: str = Field(..., description="页面模块")
    classification: str = Field(..., description="realtime/computed/ai_generated/model_assumption/pending")
    status: str = Field(..., description="ok/partial/pending")
    evidence: str = Field(..., description="数据来源或计算依据")
    action: str = Field(..., description="产品展示或风控动作")


class CommercialInvestmentHypothesis(BaseModel):
    """Trackable investment thesis item generated from the decision pack."""

    title: str = Field(..., description="假设名称")
    status: str = Field(..., description="成立/观察中/风险/待读取")
    evidence: str = Field(..., description="当前证据")
    check_next: str = Field(..., description="下一步要观察什么")
    invalidated_by: str = Field(..., description="什么情况会推翻该假设")


class CommercialSearchItem(BaseModel):
    """Public stock search suggestion for the landing page."""

    name: str = Field(..., description="股票名称")
    code: str = Field(..., description="可直接进入分析页的股票代码")
    market: str = Field(..., description="市场：A股/H股")
    exchange: Optional[str] = Field(None, description="交易所")
    aliases: List[str] = Field(default_factory=list, description="别名")
    score: float = Field(0, description="搜索排序分")


class CommercialSearchResponse(BaseModel):
    """Search suggestions returned by the public commercial endpoint."""

    query: str
    results: List[CommercialSearchItem]
    source: str = Field("stocks.index.json", description="搜索索引来源")
    updated_at: str


class CommercialAnalysisResponse(BaseModel):
    """Public analysis page response."""

    stock: CommercialStockIdentity
    recommendation: CommercialAiRecommendation
    valuation: CommercialValuationRange
    scores: List[CommercialScore]
    quant_metrics: List[CommercialQuantMetric]
    decision_reasons: List[CommercialDecisionReason]
    sniper_points: List[CommercialSniperPoint]
    industry_trend: CommercialIndustryTrend
    related_sectors: List[CommercialRelatedSector]
    news_summary: CommercialNewsSummary
    news: List[CommercialNewsItem]
    data_quality: CommercialDataQuality
    data_audit: List[CommercialDataAuditItem]
    investment_hypotheses: List[CommercialInvestmentHypothesis]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "stock": {
                    "name": "五一视界",
                    "code": "HK6651",
                    "market": "H股",
                    "currency": "HKD",
                    "exchange": "HKEX",
                },
                "recommendation": {
                    "source": "deepseek",
                    "model": "deepseek-chat",
                    "status": "ok",
                    "action": "逢低关注",
                    "summary": "价格处于合理区间下沿，成长确定性强于盈利确定性。",
                    "entry_plan": "关注132-138港元区间的承接和成交放大。",
                    "risk_trigger": "若跌破118港元或商业化进度放缓，降低关注优先级。",
                    "evidence_summary": ["收入增长", "空间智能商业化", "估值位置"],
                },
            }
        }
    )
