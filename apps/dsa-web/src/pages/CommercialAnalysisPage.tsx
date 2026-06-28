import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  BarChart3,
  Brain,
  CircleDollarSign,
  Database,
  ExternalLink,
  FileText,
  Gem,
  Gauge,
  LineChart,
  Scale,
  Search,
  ShieldCheck,
  WalletCards,
} from 'lucide-react';
import { commercialAnalysisApi } from '../api/commercialAnalysis';
import type {
  CommercialAnalysis,
  CommercialIndustryTrendItem,
  CommercialScore,
  CommercialSearchItem,
} from '../types/commercialAnalysis';
import './CommercialAnalysisPage.css';

function scoreDepth(score: number): 'very-high' | 'high' | 'medium' | 'low' {
  if (score >= 8) return 'very-high';
  if (score >= 6) return 'high';
  if (score >= 3) return 'medium';
  return 'low';
}

function scoreIcon(label: CommercialScore['label']) {
  if (label === '价值') return <Gem aria-hidden="true" />;
  if (label === '估值性价比') return <Scale aria-hidden="true" />;
  if (label === '成长') return <BarChart3 aria-hidden="true" />;
  if (label === '盈利能力') return <CircleDollarSign aria-hidden="true" />;
  if (label === '财务') return <ShieldCheck aria-hidden="true" />;
  return <WalletCards aria-hidden="true" />;
}

function scoreDefinition(label: CommercialScore['label']): string {
  if (label === '价值') return '看当前价格相对动态估值区间的安全边际。';
  if (label === '估值性价比') return '看历史估值分位、同业折溢价和隐含预期赔率。';
  if (label === '成长') return '看未来行业空间、公司护城河与业绩兑现，趋势只作验证。';
  if (label === '盈利能力') return '看净利率、ROE和利润质量能否持续兑现。';
  if (label === '财务') return '看现金流、负债率和资产质量是否稳健。';
  return '看分红、回购、派息等股东回报能力。';
}

function findSniperPoint(points: CommercialAnalysis['sniperPoints'], label: string) {
  return points.find((point) => point.label === label);
}

function formatPrice(value: number, digits = 3): string {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatRangeValue(value: number): string {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function newsToneLabel(tone: string): string {
  if (tone === 'pending') return '待读取';
  if (tone === 'positive') return '利好';
  if (tone === 'risk' || tone === 'negative') return '利空';
  return '中性';
}

function trendToneLabel(tone: string): string {
  if (tone === 'positive') return '利好';
  if (tone === 'risk' || tone === 'negative') return '风险';
  if (tone === 'pending') return '待读取';
  return '中性';
}

function clampTrendImpact(score: number): number {
  return Math.max(-100, Math.min(100, Math.round(score)));
}

function trendImpactScore(item: CommercialIndustryTrendItem): number {
  if (typeof item.impactScore === 'number' && Number.isFinite(item.impactScore)) {
    return clampTrendImpact(item.impactScore);
  }
  if (item.tone === 'positive') return 65;
  if (item.tone === 'risk' || item.tone === 'negative') return -60;
  return 0;
}

function trendImpactLabel(score: number): string {
  if (score >= 80) return '非常利好';
  if (score >= 30) return '利好';
  if (score <= -80) return '非常利空';
  if (score <= -30) return '利空';
  return '中性';
}

function trendImpactClass(score: number): 'positive' | 'neutral' | 'negative' {
  if (score >= 30) return 'positive';
  if (score <= -30) return 'negative';
  return 'neutral';
}

function formatTrendImpact(score: number): string {
  if (score > 0) return `+${score}%`;
  if (score < 0) return `${score}%`;
  return '0%';
}

function sectorChangeTone(change?: string | null): 'up' | 'down' | 'flat' {
  if (change?.includes('涨')) return 'up';
  if (change?.includes('跌')) return 'down';
  return 'flat';
}

function hypothesisStatusClass(status: string): 'good' | 'watch' | 'risk' | 'pending' {
  if (status === '成立') return 'good';
  if (status === '风险') return 'risk';
  if (status === '待读取') return 'pending';
  return 'watch';
}

function hypothesisStatusLabel(status: string): string {
  if (status === '待确认') return '观察中';
  return status;
}

function moduleHeader(index: string, title: string, description: string) {
  return (
    <div className="gyaia-module-head">
      <span className="gyaia-module-index">{index}</span>
      <div>
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
    </div>
  );
}

function getScore(analysis: CommercialAnalysis, label: string): number | null {
  const item = analysis.scores.find((score) => score.label === label);
  return typeof item?.score === 'number' ? item.score : null;
}

function buildDecisionEngineSteps(
  analysis: CommercialAnalysis,
  invalidPoint?: CommercialAnalysis['sniperPoints'][number],
  confirmPoint?: CommercialAnalysis['sniperPoints'][number],
) {
  const { currentPrice, low, high, markerPercent } = analysis.valuation;
  const currentPriceText = formatPrice(currentPrice);
  const lowText = formatRangeValue(low);
  const highText = formatRangeValue(high);
  const confirmText = confirmPoint ? formatPrice(confirmPoint.price) : '确认位';
  const invalidText = invalidPoint ? formatPrice(invalidPoint.price) : '失效位';
  const range = Math.max(0.01, high - low);
  const relativePosition = (currentPrice - low) / range;
  const growthScore = getScore(analysis, '成长') ?? 5;
  const qualityScore = getScore(analysis, '盈利能力') ?? 5;
  const financeScore = getScore(analysis, '财务') ?? 5;
  const recommendationText = `${analysis.recommendation.action} ${analysis.recommendation.summary}`;
  const isCautious = /谨慎|观察|风险|承压|不追高/.test(recommendationText);

  let currentAction = '观察为主';
  let currentDescription = '先看价格是否继续稳定在合理区间附近，避免在趋势没有确认前主动追高。';

  if (currentPrice < low) {
    currentAction = growthScore >= 6 ? '低吸跟踪' : '等待修复';
    currentDescription = growthScore >= 6
      ? `当前价${currentPriceText}低于估值区间${lowText}-${highText}，若基本面没有恶化，可作为第一关注池，分批观察承接。`
      : `当前价${currentPriceText}虽低于估值区间${lowText}-${highText}，但成长或基本面弹性不足，先等止跌和资金回流。`;
  } else if (currentPrice <= high) {
    if (relativePosition <= 0.38 && growthScore >= 6) {
      currentAction = isCautious ? '轻仓试错' : '分批低吸';
      currentDescription = `当前价${currentPriceText}处在估值区间${lowText}-${highText}偏安全位置，适合小仓位观察，等量价确认再提高仓位。`;
    } else if (relativePosition >= 0.78 || isCautious) {
      currentAction = '持有观察';
      currentDescription = `当前价${currentPriceText}接近区间上沿${highText}，赔率下降，适合已有仓位观察，不适合新增追高。`;
    } else {
      currentAction = '轻仓跟踪';
      currentDescription = `当前价${currentPriceText}处在估值区间${lowText}-${highText}中部，先保留观察仓，等待趋势和成交量给方向。`;
    }
  } else if (currentPrice > high) {
    currentAction = growthScore >= 8 && qualityScore >= 6 ? '强股等回落' : '不追高';
    currentDescription = growthScore >= 8 && qualityScore >= 6
      ? `当前价${currentPriceText}高于区间上沿${highText}，说明市场已提前定价成长性，等回落或业绩继续兑现。`
      : `当前价${currentPriceText}高于区间上沿${highText}，安全边际不足，优先等待回撤或新的业绩证据。`;
  }

  const entryValue = currentPrice <= high && growthScore >= 6 ? '分批低吸' : '只等机会';
  const addValue = confirmPoint && markerPercent < 82 && growthScore >= 6 ? '确认后加' : '暂不加仓';
  const riskValue = invalidPoint || financeScore < 4 ? '跌破撤退' : '设线防守';

  return [
    {
      label: '今日动作',
      value: currentAction,
      description: currentDescription,
      tone: currentAction.includes('不追高') || currentAction.includes('等待') ? 'watch' : 'positive',
    },
    {
      label: '入场纪律',
      value: entryValue,
      description: entryValue === '分批低吸'
        ? `只在价格回落到${lowText}-${highText}区间内且有承接时分批参与，不一次打满仓位。`
        : `当前不急于开新仓，先等价格回到${lowText}-${highText}区间，或等趋势与消息面共振。`,
      tone: entryValue === '分批低吸' ? 'positive' : 'watch',
    },
    {
      label: '加仓节奏',
      value: addValue,
      description: addValue === '确认后加'
        ? `放量站上${confirmText}并维持强势后再提高仓位，让市场先证明趋势修复。`
        : `没有站上${confirmText}前不主动加仓，避免把普通反弹误判为趋势反转。`,
      tone: addValue === '确认后加' ? 'positive' : 'watch',
    },
    {
      label: '风险撤退',
      value: riskValue,
      description: financeScore < 4
        ? `财务或盈利质量偏弱时，若跌破${invalidText}应先撤退，避免亏损扩大。`
        : `若跌破${invalidText}，先降低仓位或离场，等重新站稳后再评估。`,
      tone: 'risk',
    },
  ];
}

const ROUTE_STOCK_NAME_HINTS: Record<string, string> = {
  HK6651: '五一视界',
  '06651.HK': '五一视界',
  '688234.SH': '天岳先进',
  '688795.SH': '摩尔线程-U',
  '300027.SZ': 'ST华谊',
};

function normalizeRouteCode(value: string): string {
  return value.trim().toUpperCase().replace('.SS', '.SH');
}

function compactRouteCode(value: string): string {
  return normalizeRouteCode(value).replace(/[^A-Z0-9]/g, '');
}

function getRouteStockNameHint(routeCode: string): string | null {
  const normalized = normalizeRouteCode(routeCode);
  if (ROUTE_STOCK_NAME_HINTS[normalized]) return ROUTE_STOCK_NAME_HINTS[normalized];

  const compact = compactRouteCode(routeCode);
  const matched = Object.entries(ROUTE_STOCK_NAME_HINTS).find(([code]) => compactRouteCode(code) === compact);
  return matched?.[1] ?? null;
}

function formatHeaderTitle(name: string | null | undefined, code: string): string {
  const normalizedCode = normalizeRouteCode(code);
  const cleanName = (name || '').trim();
  if (!cleanName) return normalizedCode;
  if (compactRouteCode(cleanName) === compactRouteCode(normalizedCode)) return normalizedCode;
  return `${cleanName} ${normalizedCode}`;
}

const CommercialAnalysisPage: React.FC = () => {
  const { stockCode } = useParams();
  const navigate = useNavigate();
  const routeCode = stockCode ? decodeURIComponent(stockCode) : 'HK6651';
  const [analysis, setAnalysis] = useState<CommercialAnalysis | null>(null);
  const [routeStockHint, setRouteStockHint] = useState<CommercialSearchItem | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadProgress, setLoadProgress] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setRouteStockHint(null);

    commercialAnalysisApi.search(routeCode, 1)
      .then((response) => {
        if (!mounted) return;
        setRouteStockHint(response.results[0] ?? null);
      })
      .catch(() => {
        if (!mounted) return;
        setRouteStockHint(null);
      });

    return () => {
      mounted = false;
    };
  }, [routeCode]);

  useEffect(() => {
    let mounted = true;
    let interval: number | undefined;
    setIsLoading(true);
    setLoadProgress(8);
    setLoadError(null);
    setAnalysis(null);

    interval = window.setInterval(() => {
      setLoadProgress((current) => {
        if (current >= 92) return current;
        if (current < 35) return current + 9;
        if (current < 68) return current + 6;
        return current + 3;
      });
    }, 420);

    commercialAnalysisApi.get(routeCode)
      .then((data) => {
        if (!mounted) return;
        setAnalysis(data);
        setLoadProgress(100);
      })
      .catch(() => {
        if (!mounted) return;
        setLoadError('待读取');
        setLoadProgress(0);
      })
      .finally(() => {
        if (!mounted) return;
        if (interval) window.clearInterval(interval);
        setIsLoading(false);
      });

    return () => {
      mounted = false;
      if (interval) window.clearInterval(interval);
    };
  }, [routeCode]);

  const routeHintName = routeStockHint?.name || getRouteStockNameHint(routeCode);
  const displayTitle = analysis
    ? formatHeaderTitle(analysis.stock.name, analysis.stock.code)
    : formatHeaderTitle(routeHintName, routeCode);
  const invalidPoint = analysis ? findSniperPoint(analysis.sniperPoints, '失效位') : undefined;
  const confirmPoint = analysis ? findSniperPoint(analysis.sniperPoints, '确认位') : undefined;
  const actionPoints = [invalidPoint, confirmPoint].filter(
    (point): point is CommercialAnalysis['sniperPoints'][number] => Boolean(point),
  );
  const newsSummary = analysis?.newsSummary ?? {
    poolCount: analysis?.news.length ?? 0,
    displayCount: analysis?.news.length ?? 0,
    positiveCount: 0,
    riskCount: 0,
    neutralCount: 0,
    latestDate: '待读取',
    description: '资讯池待读取。',
  };
  const selectedNewsCount = analysis?.news.length || newsSummary.displayCount || 0;
  const selectedNewsToneCounts = useMemo(() => {
    const counts = {
      positive: 0,
      risk: 0,
      neutral: 0,
    };

    (analysis?.news ?? []).forEach((item) => {
      if (item.tone === 'positive') {
        counts.positive += 1;
      } else if (item.tone === 'risk' || item.tone === 'negative') {
        counts.risk += 1;
      } else if (item.tone !== 'pending') {
        counts.neutral += 1;
      }
    });

    return counts;
  }, [analysis?.news]);
  const newsHeaderDescription = selectedNewsCount > 0
    ? `大数据精选${selectedNewsCount}条动态资讯，按时间倒序展示`
    : '大数据精选动态资讯，按时间倒序展示';
  const industryTrend = analysis?.industryTrend ?? {
    theme: '待读取',
    source: 'pending',
    status: 'pending',
    summary: '待读取',
    items: [
      {
        tone: 'pending',
        label: '待读取',
        impactScore: 0,
        title: '待读取',
        description: '待读取',
      },
    ],
  };
  const decisionEngineSteps = analysis
    ? buildDecisionEngineSteps(analysis, invalidPoint, confirmPoint)
    : [];

  return (
    <main className="gyai-analysis-page">
      <section className="gyaia-hero" aria-label="每日股研AI分析页顶部">
        <div className="gyaia-hero-bg" aria-hidden="true" />
        <header className="gyaia-nav">
          <Link to="/" className="gyaia-brand" aria-label="返回每日股研AI首页">
            <span className="gyaia-brand-name">每日股研AI</span>
            <span className="gyaia-brand-subtitle">AI量化算法实时评估A/H股</span>
          </Link>
          <nav className="gyaia-nav-right" aria-label="核心能力">
            <span><Database aria-hidden="true" />A/H股全域数据</span>
            <span><LineChart aria-hidden="true" />金融量化算法</span>
            <span><Brain aria-hidden="true" />AI深度推理</span>
          </nav>
        </header>

        <div className="gyaia-title-row">
          <button
            type="button"
            className="gyaia-back-button"
            onClick={() => navigate('/')}
            aria-label="返回首页"
          >
            <ArrowLeft aria-hidden="true" />
          </button>
          <div>
            <p>分析结果</p>
            <h1>{displayTitle}</h1>
          </div>
          <div className="gyaia-status-pill" aria-live="polite">
            <Brain aria-hidden="true" />
            {analysis
              ? '基于实时量化数据生成'
              : isLoading
                ? '实时数据加载中'
                : '实时数据不可用'}
          </div>
        </div>
      </section>

      {!analysis ? (
        <section className="gyaia-result" aria-label="实时分析数据状态">
          <div className="gyaia-live-state" role="status" aria-live="polite">
            <Search aria-hidden="true" />
            <h2>{isLoading ? '正在生成实时分析结果' : '待读取'}</h2>
            <p>
              {isLoading
                ? '正在连接行情、板块、资讯和量化模型，生成可执行的点位计划。'
                : loadError ?? '待读取'}
            </p>
            {isLoading ? (
              <div className="gyaia-loading-progress-wrap" aria-label="实时分析生成进度">
                <div
                  className="gyaia-loading-progress"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={loadProgress}
                  aria-valuetext={`生成进度 ${loadProgress}%`}
                  style={{ '--loading-progress': `${loadProgress}%` } as React.CSSProperties}
                >
                  <span />
                  <strong>{loadProgress}%</strong>
                </div>
                <div className="gyaia-loading-steps" aria-hidden="true">
                  <span>行情</span>
                  <span>板块</span>
                  <span>资讯</span>
                  <span>量化</span>
                  <span>AI结论</span>
                </div>
              </div>
            ) : null}
          </div>
        </section>
      ) : (
      <section className="gyaia-result" aria-label={`${analysis.stock.name}分析结果`}>
        <div className="gyaia-verdict-row">
          <div className="gyaia-verdict-copy">
            <span className="gyaia-kicker">
              <Gauge aria-hidden="true" />
              {analysis.recommendation.action}
            </span>
            <h2>{analysis.recommendation.summary}</h2>
            <p>{analysis.recommendation.entryPlan}</p>
          </div>

          <div className="gyaia-metric-strip" aria-label="核心行动指标">
            <div>
              <span>当前价</span>
              <strong>{formatPrice(analysis.valuation.currentPrice)}</strong>
              <em>{analysis.valuation.pricePosition}</em>
            </div>
            {actionPoints.map((point) => (
              <div key={point.label}>
                <span>{point.label}</span>
                <strong>{formatPrice(point.price)}</strong>
                <em>{point.description}</em>
              </div>
            ))}
          </div>
        </div>

        <div className="gyaia-valuation-row">
          <div className="gyaia-range-copy">
            <span>{analysis.valuation.label}</span>
            <strong>
              {formatRangeValue(analysis.valuation.low)}
              <small> - </small>
              {formatRangeValue(analysis.valuation.high)}
            </strong>
            <em>{analysis.valuation.currencyLabel}</em>
          </div>
          <div className="gyaia-range-rail" aria-label="估值区间">
            <span className="gyaia-range-segment gyaia-range-muted" />
            <span className="gyaia-range-segment gyaia-range-fair" />
            <span className="gyaia-range-segment gyaia-range-high" />
            <span
              className="gyaia-range-boundary gyaia-range-boundary-low"
              aria-label={`合理区间下限 ${formatRangeValue(analysis.valuation.low)}`}
            >
              <strong>{formatRangeValue(analysis.valuation.low)}</strong>
            </span>
            <span
              className="gyaia-range-boundary gyaia-range-boundary-high"
              aria-label={`合理区间上限 ${formatRangeValue(analysis.valuation.high)}`}
            >
              <strong>{formatRangeValue(analysis.valuation.high)}</strong>
            </span>
            <span className="gyaia-range-label gyaia-range-label-low">偏低</span>
            <span className="gyaia-range-label gyaia-range-label-fair">合理区间</span>
            <span className="gyaia-range-label gyaia-range-label-high">偏高</span>
            <span
              className="gyaia-range-marker"
              style={{ left: `${analysis.valuation.markerPercent}%` }}
            >
              <span>当前价格</span>
              <strong>{formatPrice(analysis.valuation.currentPrice)}</strong>
            </span>
          </div>
        </div>

        <section className="gyaia-module gyaia-decision-panel" aria-label="决策引擎">
          {moduleHeader('01', '决策引擎', '先给今天的操作结论，再看后面的数据拆解')}
          <div className="gyaia-decision-chain">
            {decisionEngineSteps.map((step) => (
              <div key={step.label} className={`gyaia-decision-step tone-${step.tone}`}>
                <span>{step.label}</span>
                <strong>{step.value}</strong>
                <em>{step.description}</em>
              </div>
            ))}
          </div>
        </section>

        <div className="gyaia-content-grid">
          <div className="gyaia-content-column gyaia-content-column-primary">
            <section className="gyaia-module gyaia-score-panel" aria-label="六维健康评分">
              {moduleHeader('02', '六维健康评分', '价值、估值性价比、成长、盈利、财务、分红六项拆解')}
              <div className="gyaia-score-grid">
                {analysis.scores.map((score) => (
                  <div
                    key={score.label}
                    className={`gyaia-score depth-${scoreDepth(score.score)}`}
                    aria-label={`${score.label}评分${score.score.toFixed(1)}，${scoreDefinition(score.label)}`}
                  >
                    <div className="gyaia-score-main">
                      <span className="gyaia-score-icon">{scoreIcon(score.label)}</span>
                      <span className="gyaia-score-label">{score.label}</span>
                      <strong style={{ '--score-angle': `${score.score * 36}deg` } as React.CSSProperties}>
                        {score.score.toFixed(1)}
                      </strong>
                    </div>
                    <p className="gyaia-score-definition">{scoreDefinition(score.label)}</p>
                  </div>
                ))}
              </div>
            </section>

            <section className="gyaia-module gyaia-sniper-panel" aria-label="狙击点位">
              {moduleHeader('03', '狙击点位', '先看失效位，再等确认位，减少盲目追高')}
              <div className="gyaia-sniper-list">
                {analysis.sniperPoints.map((point) => (
                  <div key={point.label} className="gyaia-sniper-item">
                    <span>{point.label}</span>
                    <strong>{formatPrice(point.price)}</strong>
                    <em>{point.description}</em>
                  </div>
                ))}
              </div>
            </section>

            <section className="gyaia-module gyaia-trend-panel" aria-label="行业趋势">
              {moduleHeader('07', '行业趋势', '用 -100% 到 +100% 表达行业方向的量化影响')}
              <div className="gyaia-trend-head">
                <span>{industryTrend.theme}</span>
                <strong>{industryTrend.summary}</strong>
              </div>
              <div className="gyaia-trend-list">
                {industryTrend.items.map((item) => {
                  const impactScore = trendImpactScore(item);
                  const impactClass = trendImpactClass(impactScore);

                  return (
                    <div
                      key={`${item.tone}-${item.title}`}
                      className={`gyaia-trend-item tone-${item.tone} impact-${impactClass}`}
                      style={{ '--trend-impact-width': `${Math.abs(impactScore) / 2}%` } as React.CSSProperties}
                    >
                      <span>{trendToneLabel(item.tone)}</span>
                      <strong>{item.title}</strong>
                      <b aria-label={`行业趋势量化影响 ${trendImpactLabel(impactScore)} ${formatTrendImpact(impactScore)}`}>
                        {formatTrendImpact(impactScore)}
                      </b>
                      <em>{item.description}</em>
                      <i aria-hidden="true">
                        <small />
                      </i>
                    </div>
                  );
                })}
              </div>
            </section>

            <section className="gyaia-module gyaia-quant-panel" aria-label="实时量化数据">
              {moduleHeader('05', '实时量化数据', '用趋势、波动、量价位置判断当前交易质量')}
              <div className="gyaia-quant-table" role="table" aria-label="实时量化数据">
                {analysis.quantMetrics.map((metric) => (
                  <div key={`${metric.label}-${metric.value}`} className="gyaia-quant-row" role="row">
                    <span role="cell">{metric.label}</span>
                    <strong role="cell">{metric.value}</strong>
                    <em role="cell">{metric.percentile}</em>
                    <small role="cell">{metric.interpretation}</small>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <div className="gyaia-content-column gyaia-content-column-secondary">
            <section className="gyaia-module gyaia-reason-panel" aria-label="结论依据">
              {moduleHeader('04', '为什么是这个结论', '把建议、估值区间、点位和风险讲清楚')}
              <div className="gyaia-reason-list">
                {analysis.decisionReasons.map((item) => (
                  <div key={item.title} className="gyaia-reason-item">
                    <strong>{item.title}</strong>
                    <span>{item.description}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="gyaia-module gyaia-sector-panel" aria-label="关联板块">
              {moduleHeader('06', '关联板块', '显示业务相关度，并同步实时板块涨跌')}
              <div className="gyaia-sector-list">
                {analysis.relatedSectors.map((sector) => (
                  <div key={sector.name} className="gyaia-sector-item">
                    <strong>{sector.name}</strong>
                    <div className="gyaia-sector-badges">
                      <span className="gyaia-sector-badge relevance">
                        相关度 {sector.relevance || sector.heat}
                      </span>
                      {sector.realtimeChange ? (
                        <span className={`gyaia-sector-badge change-${sectorChangeTone(sector.realtimeChange)}`}>
                          实时 {sector.realtimeBoard && sector.realtimeBoard !== sector.name ? `${sector.realtimeBoard} ` : ''}{sector.realtimeChange}
                        </span>
                      ) : null}
                    </div>
                    <em>{sector.reason}</em>
                  </div>
                ))}
              </div>
            </section>

            <section className="gyaia-module gyaia-thesis-panel" aria-label="投资假设追踪">
              {moduleHeader('08', '投资假设追踪', '把核心假设、当前证据和下一步观察项分开')}
              <div className="gyaia-thesis-list">
                {analysis.investmentHypotheses.map((item) => (
                  <div key={item.title} className={`gyaia-thesis-item status-${hypothesisStatusClass(item.status)}`}>
                    <div>
                      <strong>{item.title}</strong>
                      <span>{hypothesisStatusLabel(item.status)}</span>
                    </div>
                    <p>{item.evidence}</p>
                    <em>{item.checkNext}</em>
                  </div>
                ))}
              </div>
            </section>

            <section className="gyaia-module gyaia-news-panel" aria-label="最新相关资讯">
              {moduleHeader('09', '最新相关资讯', newsHeaderDescription)}
              <div className="gyaia-news-summary" aria-label="精选资讯概览">
                <div>
                  <span>精选{selectedNewsCount}条动态资讯</span>
                </div>
                <div className="tone-positive">
                  <strong>{selectedNewsToneCounts.positive}</strong>
                  <span>利好</span>
                </div>
                <div className="tone-risk">
                  <strong>{selectedNewsToneCounts.risk}</strong>
                  <span>利空</span>
                </div>
                <div className="tone-neutral">
                  <strong>{selectedNewsToneCounts.neutral}</strong>
                  <span>中性</span>
                </div>
              </div>
              <div className="gyaia-news-list">
                {analysis.news.map((item) => {
                  const isPending = item.tone === 'pending' || item.title === '待读取' || item.url === '#';
                  const content = (
                    <>
                      <FileText aria-hidden="true" />
                      <strong className={`gyaia-news-tone tone-${item.tone}`}>
                        {newsToneLabel(item.tone)}
                      </strong>
                      <span>{item.title}</span>
                      <em>{isPending ? '待读取' : `${item.source} · ${item.date}`}</em>
                      {!isPending ? <ExternalLink aria-hidden="true" /> : null}
                    </>
                  );

                  return isPending ? (
                    <div key={`${item.source}-${item.title}`} className="gyaia-news-item is-pending">
                      {content}
                    </div>
                  ) : (
                    <a
                      key={`${item.source}-${item.title}`}
                      href={item.url}
                      className="gyaia-news-item"
                      target="_blank"
                      rel="noreferrer"
                    >
                      {content}
                    </a>
                  );
                })}
              </div>
            </section>
          </div>
        </div>
      </section>
      )}
    </main>
  );
};

export default CommercialAnalysisPage;
