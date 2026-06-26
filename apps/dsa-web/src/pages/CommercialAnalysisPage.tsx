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
  Search,
  ShieldCheck,
  Target,
  WalletCards,
} from 'lucide-react';
import { commercialAnalysisApi } from '../api/commercialAnalysis';
import type {
  CommercialAnalysis,
  CommercialIndustryTrendItem,
  CommercialScore,
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
  if (label === '成长') return <BarChart3 aria-hidden="true" />;
  if (label === '盈利能力') return <CircleDollarSign aria-hidden="true" />;
  if (label === '财务') return <ShieldCheck aria-hidden="true" />;
  return <WalletCards aria-hidden="true" />;
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

const CommercialAnalysisPage: React.FC = () => {
  const { stockCode } = useParams();
  const navigate = useNavigate();
  const routeCode = stockCode ? decodeURIComponent(stockCode) : 'HK6651';
  const [analysis, setAnalysis] = useState<CommercialAnalysis | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadProgress, setLoadProgress] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);

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

  const topEvidence = useMemo(
    () => analysis?.recommendation.evidenceSummary.slice(0, 3) ?? [],
    [analysis]
  );
  const displayTitle = analysis ? `${analysis.stock.name} ${analysis.stock.code}` : routeCode.toUpperCase();
  const invalidPoint = analysis ? findSniperPoint(analysis.sniperPoints, '失效位') : undefined;
  const confirmPoint = analysis ? findSniperPoint(analysis.sniperPoints, '确认位') : undefined;
  const actionPoints = [invalidPoint, confirmPoint].filter(
    (point): point is CommercialAnalysis['sniperPoints'][number] => Boolean(point),
  );
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

  return (
    <main className="gyai-analysis-page">
      <section className="gyaia-hero" aria-label="每日股研AI分析页顶部">
        <div className="gyaia-hero-bg" aria-hidden="true" />
        <header className="gyaia-nav">
          <Link to="/" className="gyaia-brand" aria-label="返回每日股研AI首页">
            <span className="gyaia-brand-name">每日股研AI</span>
            <span className="gyaia-brand-subtitle">A/H股智能分析助手</span>
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
          <footer className="gyaia-footer gyaia-footer-single">
            <p>本产品内容由高君宇个人学习研究开发，仅供参考，不构成投资建议。</p>
          </footer>
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
              <strong>{analysis.stock.currency === 'HKD' ? 'HK$' : '¥'}{formatPrice(analysis.valuation.currentPrice)}</strong>
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
              {analysis.stock.currency === 'HKD' ? 'HK$' : '¥'}{formatRangeValue(analysis.valuation.low)}
              <small> - </small>
              {analysis.stock.currency === 'HKD' ? 'HK$' : '¥'}{formatRangeValue(analysis.valuation.high)}
            </strong>
            <em>{analysis.valuation.currencyLabel}</em>
          </div>
          <div className="gyaia-range-rail" aria-label="估值区间">
            <span className="gyaia-range-segment gyaia-range-muted" />
            <span className="gyaia-range-segment gyaia-range-fair" />
            <span className="gyaia-range-segment gyaia-range-high" />
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

        <div className="gyaia-content-grid">
          <section className="gyaia-module gyaia-score-panel" aria-label="五维健康评分">
            {moduleHeader('01', '五维健康评分', '价值、成长、盈利、财务、分红五项拆解')}
            <div className="gyaia-score-grid">
              {analysis.scores.map((score) => (
                <div
                  key={score.label}
                  className={`gyaia-score depth-${scoreDepth(score.score)}`}
                  title={score.description}
                >
                  <span className="gyaia-score-icon">{scoreIcon(score.label)}</span>
                  <span>{score.label}</span>
                  <strong style={{ '--score-angle': `${score.score * 36}deg` } as React.CSSProperties}>
                    {score.score.toFixed(1)}
                  </strong>
                </div>
              ))}
            </div>
          </section>

          <section className="gyaia-module gyaia-sniper-panel" aria-label="狙击点位">
            {moduleHeader('02', '狙击点位', '先看失效位，再等确认位，减少盲目追高')}
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

          <section className="gyaia-module gyaia-reason-panel" aria-label="结论依据">
            {moduleHeader('03', '为什么是这个结论', '把估值、成长、量价和风险拆成可检查依据')}
            <div className="gyaia-reason-list">
              {analysis.decisionReasons.map((item) => (
                <div key={item.title} className="gyaia-reason-item">
                  <strong>{item.title}</strong>
                  <span>{item.description}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="gyaia-module gyaia-quant-panel" aria-label="实时量化数据">
            {moduleHeader('04', '实时量化数据', '用趋势、波动、量价位置判断当前交易质量')}
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

          <section className="gyaia-module gyaia-sector-panel" aria-label="关联板块">
            {moduleHeader('05', '关联板块', '显示业务相关度，并同步实时板块涨跌')}
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

          <section className="gyaia-module gyaia-trend-panel" aria-label="行业趋势">
            {moduleHeader('06', '行业趋势', '用 -100% 到 +100% 表达行业方向的量化影响')}
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

          <section className="gyaia-module gyaia-thesis-panel" aria-label="投资假设追踪">
            {moduleHeader('07', '投资假设追踪', '把核心假设、当前证据和下一步观察项分开')}
            <div className="gyaia-thesis-list">
              {analysis.investmentHypotheses.map((item) => (
                <div key={item.title} className={`gyaia-thesis-item status-${hypothesisStatusClass(item.status)}`}>
                  <div>
                    <strong>{item.title}</strong>
                    <span>{item.status}</span>
                  </div>
                  <p>{item.evidence}</p>
                  <em>{item.checkNext}</em>
                </div>
              ))}
            </div>
          </section>

          <section className="gyaia-module gyaia-news-panel" aria-label="最新相关资讯">
            {moduleHeader('08', '最新相关资讯', '多源抓取股票新闻、公告与市场资讯，按时间倒序展示')}
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

        <footer className="gyaia-footer">
          <div>
            <Target aria-hidden="true" />
            决策引擎：找低估机会 · 等趋势确认 · 设失效位控回撤
          </div>
          <div>
            {topEvidence.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
          <p>本产品内容由高君宇个人学习研究开发，仅供参考，不构成投资建议。</p>
        </footer>
      </section>
      )}
    </main>
  );
};

export default CommercialAnalysisPage;
