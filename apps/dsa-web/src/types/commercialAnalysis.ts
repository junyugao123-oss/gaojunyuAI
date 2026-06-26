export type CommercialStockIdentity = {
  name: string;
  code: string;
  market: string;
  currency: string;
  exchange?: string | null;
};

export type CommercialAiRecommendation = {
  source: 'deepseek' | 'fallback' | string;
  model: string;
  status: 'ok' | 'fallback' | 'error' | string;
  action: string;
  summary: string;
  entryPlan: string;
  riskTrigger: string;
  evidenceSummary: string[];
};

export type CommercialValuationRange = {
  label: string;
  currencyLabel: string;
  low: number;
  high: number;
  currentPrice: number;
  markerPercent: number;
  pricePosition: string;
  source?: string | null;
  status?: string | null;
  inputs?: string[];
};

export type CommercialScore = {
  label: '价值' | '成长' | '盈利能力' | '财务' | '分红' | string;
  score: number;
  description: string;
  source?: string | null;
  status?: string | null;
  inputs?: string[];
};

export type CommercialQuantMetric = {
  label: string;
  value: string;
  percentile: string;
  interpretation: string;
  source?: string | null;
  status?: string | null;
};

export type CommercialDecisionReason = {
  title: string;
  description: string;
};

export type CommercialSniperPoint = {
  label: string;
  price: number;
  description: string;
  source?: string | null;
  status?: string | null;
};

export type CommercialIndustryTrendItem = {
  tone: 'positive' | 'neutral' | 'risk' | 'pending' | string;
  label: string;
  impactScore?: number;
  title: string;
  description: string;
};

export type CommercialIndustryTrend = {
  theme: string;
  source: 'deepseek' | 'pending' | string;
  status: 'ok' | 'pending' | string;
  summary: string;
  items: CommercialIndustryTrendItem[];
};

export type CommercialRelatedSector = {
  name: string;
  heat: string;
  reason: string;
  relevance?: string | null;
  realtimeChange?: string | null;
  realtimeBoard?: string | null;
  dataSource?: string | null;
  dataStatus?: string | null;
};

export type CommercialNewsItem = {
  title: string;
  source: string;
  date: string;
  url: string;
  tone: 'positive' | 'neutral' | 'risk' | string;
  dataStatus?: string | null;
};

export type CommercialDataQuality = {
  updatedAt: string;
  quoteSource: string;
  quoteStatus: string;
  aiStatus: string;
  notes: string[];
};

export type CommercialDataAuditItem = {
  section: string;
  classification: 'realtime' | 'computed' | 'ai_generated' | 'model_assumption' | 'pending' | string;
  status: 'ok' | 'partial' | 'pending' | string;
  evidence: string;
  action: string;
};

export type CommercialInvestmentHypothesis = {
  title: string;
  status: '成立' | '待确认' | '风险' | '待读取' | string;
  evidence: string;
  checkNext: string;
  invalidatedBy: string;
};

export type CommercialSearchItem = {
  name: string;
  code: string;
  market: 'A股' | 'H股' | string;
  exchange?: string | null;
  aliases: string[];
  score: number;
};

export type CommercialSearchResponse = {
  query: string;
  results: CommercialSearchItem[];
  source: string;
  updatedAt: string;
};

export type CommercialAnalysis = {
  stock: CommercialStockIdentity;
  recommendation: CommercialAiRecommendation;
  valuation: CommercialValuationRange;
  scores: CommercialScore[];
  quantMetrics: CommercialQuantMetric[];
  decisionReasons: CommercialDecisionReason[];
  sniperPoints: CommercialSniperPoint[];
  industryTrend: CommercialIndustryTrend;
  relatedSectors: CommercialRelatedSector[];
  news: CommercialNewsItem[];
  dataQuality: CommercialDataQuality;
  dataAudit: CommercialDataAuditItem[];
  investmentHypotheses: CommercialInvestmentHypothesis[];
};
