import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  BarChart3,
  Brain,
  Database,
  FileText,
  LineChart,
  Search,
  ShieldCheck,
  Tag,
  Target,
} from 'lucide-react';
import { commercialAnalysisApi } from '../api/commercialAnalysis';
import type { CommercialSearchItem } from '../types/commercialAnalysis';
import { normalizeQuery } from '../utils/normalizeQuery';
import './CommercialLandingPage.css';

const LANDING_COPY_VERSION = 'example-preview-20260625';
const SEARCH_DEBOUNCE_MS = 180;
const SEARCH_RESULT_LIMIT = 8;

type StockProfile = {
  name: string;
  code: string;
  market: 'A股' | 'H股';
  aliases: string[];
  conclusion: string;
  valuation: string;
  growth: string;
  risk: string;
  currency: '人民币' | '港元';
  price: string;
  rangeStart: number;
  rangeMid: number;
  rangeEnd: number;
  rangeLabel: string;
  pricePosition: string;
  pointPlan: PointPlanItem[];
  evidence: EvidenceItem[];
};

type EvidenceItem = {
  text: string;
  source: string;
};

type PointPlanItem = {
  label: '关注区' | '确认位' | '失效位';
  price: string;
  description: string;
  tone: 'focus' | 'confirm' | 'risk';
};

type SearchSuggestion = Pick<CommercialSearchItem, 'name' | 'code' | 'market' | 'aliases'>;

const STOCKS: StockProfile[] = [
  {
    name: '五一视界',
    code: 'HK6651',
    market: 'H股',
    aliases: ['6651', '6651.HK', 'HK 6651', 'wuyishijie', 'wysj', '五一'],
    conclusion: 'AI判断：估值抬升至更高合理区间，当前价格处于合理区间下沿。',
    valuation: '合理偏低',
    growth: '积极',
    risk: '中等',
    currency: '港元',
    price: '137.950',
    rangeStart: 130,
    rangeMid: 137.95,
    rangeEnd: 181,
    rangeLabel: 'AI合理估值区间',
    pricePosition: '处于合理区间下沿',
    pointPlan: [
      { label: '关注区', price: '132-138', description: '观察承接，适合作为第一关注带。', tone: 'focus' },
      { label: '确认位', price: '146.000', description: '放量站稳后，趋势确认度提升。', tone: 'confirm' },
      { label: '失效位', price: '118.000', description: '跌破后降级观察，重新评估逻辑。', tone: 'risk' },
    ],
    evidence: [
      { text: '2025年收入同比增长21.0%，51Aes/51Sim继续扩张', source: '业绩公告' },
      { text: '51Aes与51Sim双产品线推进，空间智能场景持续落地', source: '业务进展' },
      { text: '数字孪生叠加物理AI，商业化应用边界继续打开', source: '行业趋势' },
    ],
  },
  {
    name: '腾讯控股',
    code: '0700.HK',
    market: 'H股',
    aliases: ['700', 'hk700', 'tengxun', 'tencent', 'txkg', '腾讯'],
    conclusion: 'AI判断：估值处于合理偏低区间，基本面稳健。',
    valuation: '偏低',
    growth: '稳定',
    risk: '中等',
    currency: '港元',
    price: '337.20',
    rangeStart: 260,
    rangeMid: 337.2,
    rangeEnd: 460,
    rangeLabel: 'AI合理估值区间',
    pricePosition: '处于区间中部',
    pointPlan: [
      { label: '关注区', price: '320-340', description: '回落不破区间可观察承接。', tone: 'focus' },
      { label: '确认位', price: '360.00', description: '放量站稳后，估值修复更清晰。', tone: 'confirm' },
      { label: '失效位', price: '298.00', description: '跌破后降低关注优先级。', tone: 'risk' },
    ],
    evidence: [
      { text: '游戏业务稳健增长，广告与视频号贡献增量', source: '公司财报' },
      { text: '现金流充足，回购力度延续，股东回报持续提升', source: '公司公告' },
      { text: '云与AI投入保持，长期增长弹性仍需经营兑现', source: '行业跟踪' },
    ],
  },
  {
    name: '贵州茅台',
    code: '600519',
    market: 'A股',
    aliases: ['600519.SH', 'maotai', 'gzmt', '茅台'],
    conclusion: 'AI判断：价格略高于合理区间，但盈利质量仍然很强。',
    valuation: '略贵',
    growth: '稳定',
    risk: '中低',
    currency: '人民币',
    price: '1728.00',
    rangeStart: 1530,
    rangeMid: 1728,
    rangeEnd: 1850,
    rangeLabel: 'AI合理估值区间',
    pricePosition: '接近区间上沿',
    pointPlan: [
      { label: '关注区', price: '1620-1680', description: '回落后再看安全边际。', tone: 'focus' },
      { label: '确认位', price: '1850.00', description: '突破上沿后再评估新估值。', tone: 'confirm' },
      { label: '失效位', price: '1530.00', description: '跌破下沿说明预期转弱。', tone: 'risk' },
    ],
    evidence: [
      { text: '品牌护城河稳定，盈利质量仍处行业高位', source: '公司财报' },
      { text: '渠道库存和批价波动影响短期市场预期', source: '市场跟踪' },
      { text: '现金流和分红能力较强，防御属性突出', source: '量化模型' },
    ],
  },
  {
    name: '宁德时代',
    code: '300750',
    market: 'A股',
    aliases: ['300750.SZ', 'ningdeshidai', 'catl', 'ndsd', '宁德'],
    conclusion: 'AI判断：估值接近合理区间，重点看盈利修复能否持续。',
    valuation: '合理',
    growth: '修复',
    risk: '中等',
    currency: '人民币',
    price: '246.80',
    rangeStart: 180,
    rangeMid: 246.8,
    rangeEnd: 320,
    rangeLabel: 'AI合理估值区间',
    pricePosition: '处于区间中部',
    pointPlan: [
      { label: '关注区', price: '232-248', description: '观察盈利修复和资金承接。', tone: 'focus' },
      { label: '确认位', price: '268.00', description: '站稳后趋势弹性增强。', tone: 'confirm' },
      { label: '失效位', price: '210.00', description: '跌破后先控制风险。', tone: 'risk' },
    ],
    evidence: [
      { text: '动力电池龙头地位稳固，海外业务贡献提升', source: '公司财报' },
      { text: '盈利修复受原材料价格和竞争格局共同影响', source: '行业跟踪' },
      { text: '技术迭代和储能增长提供中长期估值支撑', source: '量化模型' },
    ],
  },
];

const DEFAULT_STOCK = STOCKS[0];
const MARKET_ONLY_QUERIES = new Set(['a', 'a股', 'h', 'h股', 'hk', 'sh', 'sz', 'cn', '沪', '深', '港股']);
const STOCK_CODE_TARGET_RE = /^(?:HK\d{1,5}|\d{1,5}\.HK|\d{1,5}|\d{6}|(?:SH|SZ|BJ)\d{6}|\d{6}\.(?:SH|SZ|SS|BJ))$/i;

function containsChineseText(value: string): boolean {
  return /[\u4e00-\u9fff]/.test(value);
}

function isMarketOnlyQuery(query: string): boolean {
  return MARKET_ONLY_QUERIES.has(query.replace(/[._\-/]/g, ''));
}

function scoreStock(query: string, stock: StockProfile): number {
  const normalized = normalizeQuery(query);
  if (!normalized || isMarketOnlyQuery(normalized)) {
    return 0;
  }

  const aliases = stock.aliases.map(normalizeQuery);
  const chineseTerms = [stock.name, ...stock.aliases].map(normalizeQuery).filter(containsChineseText);
  const codeTerms = [stock.code, ...stock.aliases].map(normalizeQuery).filter((term) => /\d/.test(term));
  const alphaTerms = aliases.filter((term) => /^[a-z]+$/.test(term));

  if (containsChineseText(normalized)) {
    if (chineseTerms.some((term) => term === normalized || normalized.includes(term))) {
      return 100;
    }
    if (chineseTerms.some((term) => term.startsWith(normalized))) {
      return 80;
    }
    if (chineseTerms.some((term) => term.includes(normalized))) {
      return 60;
    }
    return 0;
  }

  if (/\d/.test(normalized)) {
    if (codeTerms.some((term) => term === normalized)) {
      return 100;
    }
    if (codeTerms.some((term) => term.startsWith(normalized))) {
      return 80;
    }
    if (codeTerms.some((term) => term.includes(normalized))) {
      return 60;
    }
    return 0;
  }

  if (/^[a-z]+$/.test(normalized)) {
    if (normalized.length < 2) {
      return 0;
    }
    if (alphaTerms.some((term) => term === normalized)) {
      return 100;
    }
    if (alphaTerms.some((term) => term.startsWith(normalized))) {
      return 80;
    }
  }

  return 0;
}

function searchMarketPriority(query: string, stock: StockProfile): number {
  const normalized = normalizeQuery(query);
  const explicitHk = normalized.startsWith('hk') || normalized.endsWith('hk');
  const explicitA = normalized.startsWith('sh') || normalized.startsWith('sz') || normalized.startsWith('bj')
    || normalized.endsWith('sh') || normalized.endsWith('sz') || normalized.endsWith('ss') || normalized.endsWith('bj');
  if (explicitHk) return stock.market === 'H股' ? 0 : 1;
  if (explicitA) return stock.market === 'A股' ? 0 : 1;
  return stock.market === 'A股' ? 0 : 1;
}

function getLocalSuggestions(query: string): SearchSuggestion[] {
  const normalized = normalizeQuery(query);
  if (!normalized) {
    return [];
  }

  const matches = STOCKS
    .map((stock) => ({ stock, score: scoreStock(normalized, stock) }))
    .filter(({ score }) => score > 0)
    .sort((a, b) => searchMarketPriority(normalized, a.stock) - searchMarketPriority(normalized, b.stock)
      || b.score - a.score
      || a.stock.code.localeCompare(b.stock.code))
    .map(({ stock }) => ({
      name: stock.name,
      code: stock.code,
      market: stock.market,
      aliases: stock.aliases,
    }));

  return matches.slice(0, 5);
}

function normalizeSearchTarget(value: string): string {
  return value.trim().replace(/\s+/g, '').toUpperCase();
}

function extractSearchTarget(value: string): string {
  const normalized = normalizeSearchTarget(value);
  const match = normalized.match(/(?:HK\d{1,5}|\d{1,5}\.HK|(?:SH|SZ|BJ)\d{6}|\d{6}\.(?:SH|SZ|SS|BJ)|\d{6})/i);
  return (match?.[0] || normalized).toUpperCase();
}

function formatRangePrice(value: number, currency: StockProfile['currency']): string {
  const prefix = currency === '港元' ? 'HK$' : '¥';
  return `${prefix}${value.toLocaleString('en-US', {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  })}`;
}

const CommercialLandingPage: React.FC = () => {
  const navigate = useNavigate();
  const previewStock = DEFAULT_STOCK;
  const [query, setQuery] = useState('');
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [suggestions, setSuggestions] = useState<SearchSuggestion[]>([]);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);

  useEffect(() => {
    const normalized = normalizeQuery(query);
    if (!normalized || isMarketOnlyQuery(normalized)) {
      setSuggestions([]);
      return;
    }

    const localSuggestions = getLocalSuggestions(query);
    setSuggestions(localSuggestions);

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      commercialAnalysisApi.search(query, SEARCH_RESULT_LIMIT)
        .then((response) => {
          if (cancelled) return;
          setSuggestions(response.results.length > 0 ? response.results : localSuggestions);
        })
        .catch(() => {
          if (cancelled) return;
          setSuggestions(localSuggestions);
        });
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [query]);

  useEffect(() => {
    setHighlightedIndex((current) => Math.min(current, Math.max(0, suggestions.length - 1)));
  }, [suggestions.length]);

  const markerPosition = useMemo(() => {
    const span = previewStock.rangeEnd - previewStock.rangeStart;
    if (span <= 0) {
      return 50;
    }
    const fairStart = 28;
    const fairWidth = 44;
    const fairEnd = fairStart + fairWidth;
    const raw = (previewStock.rangeMid - previewStock.rangeStart) / span;

    if (raw < 0) {
      return Math.max(8, fairStart + raw * fairStart);
    }
    if (raw > 1) {
      return Math.min(92, fairEnd + (raw - 1) * (100 - fairEnd));
    }
    return fairStart + raw * fairWidth;
  }, [previewStock]);

  const applyStock = (stock: SearchSuggestion) => {
    setQuery(`${stock.name} ${stock.code}`);
    setSuggestionsOpen(false);
    setHighlightedIndex(0);
  };

  const clearDefaultSearchValue = () => {
    if (query.trim() === `${DEFAULT_STOCK.name} ${DEFAULT_STOCK.code}`) {
      setQuery('');
      setSuggestions([]);
      setSuggestionsOpen(false);
      setHighlightedIndex(0);
    }
  };

  const submitSearch = () => {
    const [firstSuggestion] = suggestions;
    if (firstSuggestion) {
      setSuggestionsOpen(false);
      navigate(`/analysis/${encodeURIComponent(firstSuggestion.code)}`);
      return;
    }

    const normalizedTarget = extractSearchTarget(query);
    if (!normalizedTarget) {
      navigate(`/analysis/${encodeURIComponent(DEFAULT_STOCK.code)}`);
      return;
    }

    if (STOCK_CODE_TARGET_RE.test(normalizedTarget) && !isMarketOnlyQuery(normalizedTarget.toLowerCase())) {
      setSuggestionsOpen(false);
      navigate(`/analysis/${encodeURIComponent(normalizedTarget)}`);
      return;
    }

    setSuggestionsOpen(false);
  };

  const handleSearchKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setSuggestionsOpen(suggestions.length > 0);
      setHighlightedIndex((current) => Math.min(current + 1, Math.max(0, suggestions.length - 1)));
      return;
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlightedIndex((current) => Math.max(0, current - 1));
      return;
    }

    if (event.key === 'Enter') {
      event.preventDefault();
      const highlighted = suggestions[highlightedIndex];
      if (suggestionsOpen && highlighted) {
        applyStock(highlighted);
        return;
      }
      submitSearch();
    }

    if (event.key === 'Escape') {
      setSuggestionsOpen(false);
    }
  };

  return (
    <main className="gyai-landing">
      <section className="gyai-page gyai-intro-page" aria-label="每日股研AI首页">
        <div className="gyai-hero">
          <div className="gyai-hero-bg" aria-hidden="true" />
          <header className="gyai-nav">
            <div className="gyai-brand">
              <span className="gyai-brand-name">每日股研AI</span>
              <span className="gyai-brand-subtitle">每日AI新数据 · 实时评估A/H股</span>
            </div>

            <nav className="gyai-nav-right" aria-label="每日股研AI导航">
              <div className="gyai-edge-list" aria-label="核心优势">
                <span><Database aria-hidden="true" />A/H股全域数据</span>
                <span><LineChart aria-hidden="true" />金融量化算法</span>
                <span><Brain aria-hidden="true" />AI深度推理</span>
              </div>
            </nav>
          </header>

          <div className="gyai-hero-content">
            <h1>
              <span>输入一只股票名称或代码，</span>
              <span>每日给你最专业的分析</span>
            </h1>

            <form
              className="gyai-search"
              onSubmit={(event) => {
                event.preventDefault();
                submitSearch();
              }}
            >
              <div className="gyai-search-input-wrap">
                <Search className="gyai-search-icon" aria-hidden="true" />
                <label htmlFor="gyai-stock-search" className="sr-only">输入股票代码或公司名</label>
                <input
                  id="gyai-stock-search"
                  value={query}
                  onChange={(event) => {
                    const nextQuery = event.target.value;
                    setQuery(nextQuery);
                    setSuggestionsOpen(Boolean(nextQuery.trim()));
                    setHighlightedIndex(0);
                  }}
                  onMouseDown={() => setIsSearchFocused(true)}
                  onClick={clearDefaultSearchValue}
                  onFocus={() => {
                    setIsSearchFocused(true);
                    setSuggestionsOpen(Boolean(query.trim()));
                  }}
                  onDoubleClick={() => applyStock(DEFAULT_STOCK)}
                  onBlur={() => window.setTimeout(() => {
                    setIsSearchFocused(false);
                    setSuggestionsOpen(false);
                  }, 140)}
                  onKeyDown={handleSearchKeyDown}
                  placeholder={isSearchFocused ? '' : `${DEFAULT_STOCK.name} ${DEFAULT_STOCK.code}`}
                  autoComplete="off"
                />
                <span className="gyai-search-example">
                  例如：五一视界/HK6651，摩尔线程-U/688795
                </span>

              </div>

              {suggestionsOpen && suggestions.length > 0 ? (
                <div className="gyai-suggestions" role="listbox" aria-label="股票搜索建议">
                  {suggestions.map((stock, index) => (
                    <button
                      key={`${stock.code}-${stock.name}`}
                      type="button"
                      role="option"
                      aria-selected={index === highlightedIndex}
                      className={index === highlightedIndex ? 'is-active' : undefined}
                      onMouseEnter={() => setHighlightedIndex(index)}
                      onMouseDown={(event) => {
                        event.preventDefault();
                        applyStock(stock);
                      }}
                    >
                      <span>
                        <strong>{stock.name}</strong>
                        <small>{stock.code}</small>
                      </span>
                      <em>{stock.market}</em>
                    </button>
                  ))}
                </div>
              ) : null}

              <button type="submit" className="gyai-analyze-button">分析</button>
            </form>

            <p className="gyai-hero-support">支持A股 / H股 · 数据实时更新</p>
          </div>
        </div>

        <div className="gyai-preview" role="region" aria-label="股票分析预览">
          <div className="gyai-preview-inner">
            <p
              className="gyai-preview-kicker notranslate"
              data-copy-version={LANDING_COPY_VERSION}
              translate="no"
            >
              示例预览 · {previewStock.name} {previewStock.code}
            </p>
            <h2>{previewStock.conclusion.replace('AI判断：', '')}</h2>

            <div className="gyai-summary-tags" aria-label="核心判断">
              <span><Tag aria-hidden="true" />估值 <strong>{previewStock.valuation}</strong></span>
              <span><BarChart3 aria-hidden="true" />成长 <strong>{previewStock.growth}</strong></span>
              <span><ShieldCheck aria-hidden="true" />风险 <strong>{previewStock.risk}</strong></span>
            </div>

            <div className="gyai-range-block">
              <div className="gyai-range-copy">
                <span>{previewStock.rangeLabel}</span>
                <strong>
                  {formatRangePrice(previewStock.rangeStart, previewStock.currency)}
                  <small> - </small>
                  {formatRangePrice(previewStock.rangeEnd, previewStock.currency)}
                </strong>
                <em>{previewStock.currency}/股</em>
              </div>
              <div className="gyai-range-rail" aria-label={`${previewStock.rangeLabel} ${previewStock.currency}`}>
                <span className="gyai-range-segment gyai-range-segment-muted" />
                <span className="gyai-range-segment gyai-range-segment-copper" />
                <span className="gyai-range-segment gyai-range-segment-red" />
                <span className="gyai-range-legend gyai-range-legend-low">偏低</span>
                <span className="gyai-range-legend gyai-range-legend-fair">合理区间</span>
                <span className="gyai-range-legend gyai-range-legend-high">偏高</span>
                <span
                  className="gyai-range-marker"
                  style={{ left: `${markerPosition}%` }}
                >
                  <span className="gyai-range-marker-label">当前价格</span>
                  <em>{previewStock.price}</em>
                </span>
              </div>
              <div className="gyai-price-summary" aria-label={`当前价 ${previewStock.price}`}>
                <span>当前价</span>
                <strong>{previewStock.price}</strong>
                <em>{previewStock.pricePosition}</em>
              </div>
            </div>

            <div className="gyai-insight-grid">
              <section className="gyai-point-plan-panel" aria-label="点位计划">
                <h3><Target aria-hidden="true" />点位计划</h3>
                <div className="gyai-point-plan-list">
                  {previewStock.pointPlan.map((item) => (
                    <div className={`gyai-point-plan-item is-${item.tone}`} key={item.label}>
                      <span>{item.label}</span>
                      <strong>{item.price}</strong>
                      <em>{item.description}</em>
                    </div>
                  ))}
                </div>
              </section>

              <section className="gyai-evidence-panel" aria-label="关键依据">
                <h3>关键依据（部分）</h3>
                <div className="gyai-evidence-list">
                  {previewStock.evidence.map((item) => (
                    <div className="gyai-evidence-item" key={`${item.source}-${item.text}`}>
                      <FileText aria-hidden="true" />
                      <span>{item.text}</span>
                      <em>{item.source}</em>
                    </div>
                  ))}
                </div>
              </section>
            </div>
            <p className="gyai-disclaimer">本产品内容由高君宇个人学习研究开发，仅供参考，不构成投资建议。</p>
          </div>
        </div>
      </section>
    </main>
  );
};

export default CommercialLandingPage;
