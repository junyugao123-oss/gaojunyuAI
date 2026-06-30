import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  BarChart3,
  Brain,
  ChevronDown,
  Database,
  FileText,
  Flame,
  LineChart,
  Search,
  ShieldCheck,
  Tag,
  Target,
} from 'lucide-react';
import { commercialAnalysisApi } from '../api/commercialAnalysis';
import useStockIndex from '../hooks/useStockIndex';
import type { CommercialSearchItem } from '../types/commercialAnalysis';
import type { StockSuggestion as StockIndexSuggestion } from '../types/stockIndex';
import { normalizeQuery } from '../utils/normalizeQuery';
import { searchStocks } from '../utils/searchStocks';
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
  valuationNote: string;
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

type SearchSuggestion = Pick<CommercialSearchItem, 'name' | 'code' | 'market' | 'aliases'> & {
  score?: number;
  hotReason?: string;
  hotScore?: number;
  isHotStock?: boolean;
};

const STOCKS: StockProfile[] = [
  {
    name: '五一视界',
    code: 'HK6651',
    market: 'H股',
    aliases: ['6651', '6651.HK', 'HK 6651', 'wuyishijie', 'wysj', '五一'],
    conclusion: 'AI判断：物理AI龙头，短线承压，等待量价验证。',
    valuation: '合理上沿',
    growth: '较高',
    risk: '中等',
    currency: '港元',
    price: '98.900',
    rangeStart: 62.978,
    rangeMid: 98.9,
    rangeEnd: 103.851,
    rangeLabel: 'AI动态估值区间',
    valuationNote: '安全边际开始收窄',
    pricePosition: '处于合理区间上沿',
    pointPlan: [
      { label: '关注区', price: '80.752', description: '74.410-87.094附近观察承接，不追高。', tone: 'focus' },
      { label: '确认位', price: '115.812', description: '放量站上确认位后，趋势修复可信度提升。', tone: 'confirm' },
      { label: '失效位', price: '71.208', description: '跌破失效位说明短线承接失败，需要降级观察。', tone: 'risk' },
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
    valuationNote: '区间保护仍在',
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
    valuationNote: '赔率偏向稳健',
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
    valuationNote: '估值仍需业绩验证',
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
const EMPTY_SEARCH_LABEL = '输入股票名称或代码';
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

function suggestionMarketPriority(query: string, market: string): number {
  const normalized = normalizeQuery(query);
  const explicitHk = normalized.startsWith('hk') || normalized.endsWith('hk');
  const explicitA = normalized.startsWith('sh') || normalized.startsWith('sz') || normalized.startsWith('bj')
    || normalized.endsWith('sh') || normalized.endsWith('sz') || normalized.endsWith('ss') || normalized.endsWith('bj');
  if (explicitHk) return market === 'H股' ? 0 : 1;
  if (explicitA) return market === 'A股' ? 0 : 1;
  return market === 'A股' ? 0 : 1;
}

function normalizeSuggestionMarket(market: string): SearchSuggestion['market'] {
  if (market === 'HK') return 'H股';
  if (market === 'CN' || market === 'BSE') return 'A股';
  return market === 'H股' || market === 'A股' ? market : 'A股';
}

function toSearchSuggestion(suggestion: StockIndexSuggestion): SearchSuggestion {
  return {
    name: suggestion.nameZh,
    code: suggestion.canonicalCode,
    market: normalizeSuggestionMarket(suggestion.market),
    aliases: [suggestion.displayCode, suggestion.canonicalCode, suggestion.nameZh],
    score: suggestion.score,
  };
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

function getIndexSuggestions(query: string, stockIndex: Parameters<typeof searchStocks>[1]): SearchSuggestion[] {
  const normalized = normalizeQuery(query);
  if (!normalized || isMarketOnlyQuery(normalized) || stockIndex.length === 0) {
    return [];
  }

  return searchStocks(query, stockIndex, { limit: SEARCH_RESULT_LIMIT * 3 })
    .map(toSearchSuggestion)
    .sort((a, b) => suggestionMarketPriority(normalized, a.market) - suggestionMarketPriority(normalized, b.market)
      || (b.score ?? 0) - (a.score ?? 0)
      || a.code.localeCompare(b.code))
    .slice(0, SEARCH_RESULT_LIMIT);
}

function mergeSearchSuggestions(query: string, ...lists: SearchSuggestion[][]): SearchSuggestion[] {
  const normalized = normalizeQuery(query);
  const seen = new Set<string>();
  const merged: SearchSuggestion[] = [];

  lists.flat().forEach((suggestion) => {
    const key = suggestion.code.trim().toUpperCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    merged.push(suggestion);
  });

  return merged
    .sort((a, b) => suggestionMarketPriority(normalized, a.market) - suggestionMarketPriority(normalized, b.market)
      || (b.score ?? 0) - (a.score ?? 0)
      || a.code.localeCompare(b.code))
    .slice(0, SEARCH_RESULT_LIMIT);
}

function formatSearchDisplayCode(code: string): string {
  const normalized = code.trim().toUpperCase();
  const aShareMatch = normalized.match(/^(\d{6})\.(?:SH|SZ|SS|BJ)$/);
  return aShareMatch?.[1] || normalized;
}

function getSearchStockLabel(stock: Pick<SearchSuggestion, 'name' | 'code'>): string {
  return `${stock.name} ${formatSearchDisplayCode(stock.code)}`;
}

function inferAStockExchange(code: string): string {
  if (/^[036]/.test(code)) return code.startsWith('6') ? `${code}.SH` : `${code}.SZ`;
  if (/^[48]/.test(code)) return `${code}.BJ`;
  return code;
}

function normalizeSearchTarget(value: string): string {
  return value.trim().replace(/\s+/g, '').toUpperCase();
}

function extractSearchTarget(value: string): string {
  const normalized = normalizeSearchTarget(value);
  const match = normalized.match(/(?:HK\d{1,5}|\d{1,5}\.HK|(?:SH|SZ|BJ)\d{6}|\d{6}\.(?:SH|SZ|SS|BJ)|\d{6})/i);
  return (match?.[0] || normalized).toUpperCase();
}

function normalizeAnalysisTarget(value: string): string {
  const normalized = value.trim().toUpperCase();
  if (/^\d{6}$/.test(normalized)) {
    return inferAStockExchange(normalized);
  }
  if (/^\d{6}\.SS$/.test(normalized)) {
    return normalized.replace('.SS', '.SH');
  }
  return normalized;
}

function formatRangePrice(value: number): string {
  return value.toLocaleString('en-US', {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
}

function formatPointPrice(value: string): string {
  return value
    .split(/([–-])/)
    .map((part) => (/^\d/.test(part.trim()) ? part.trim() : part))
    .join('');
}

const CommercialLandingPage: React.FC = () => {
  const navigate = useNavigate();
  const landingRef = useRef<HTMLElement | null>(null);
  const hotRecommendationSeqRef = useRef(0);
  const { index: stockIndex } = useStockIndex();
  const previewStock = DEFAULT_STOCK;
  const [recommendedSearchStock, setRecommendedSearchStock] = useState<SearchSuggestion | null>(null);
  const [query, setQuery] = useState('');
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [suggestions, setSuggestions] = useState<SearchSuggestion[]>([]);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [selectedSearchStock, setSelectedSearchStock] = useState<SearchSuggestion | null>(null);
  const [showScrollCue, setShowScrollCue] = useState(true);

  const recommendedSearchLabel = recommendedSearchStock
    ? getSearchStockLabel(recommendedSearchStock)
    : EMPTY_SEARCH_LABEL;
  const showRecommendedHotHint = Boolean(recommendedSearchStock && !query.trim() && !isSearchFocused);

  useEffect(() => {
    const normalized = normalizeQuery(query);
    if (!normalized || isMarketOnlyQuery(normalized)) {
      setSuggestions([]);
      return;
    }

    const localSuggestions = mergeSearchSuggestions(
      query,
      getLocalSuggestions(query),
      getIndexSuggestions(query, stockIndex),
    );
    setSuggestions(localSuggestions);

    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      commercialAnalysisApi.search(query, SEARCH_RESULT_LIMIT)
        .then((response) => {
          if (cancelled) return;
          const remoteSuggestions = response.results.length > 0 ? response.results : [];
          setSuggestions(mergeSearchSuggestions(query, remoteSuggestions, localSuggestions));
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
  }, [query, stockIndex]);

  useEffect(() => {
    let cancelled = false;
    const sequence = hotRecommendationSeqRef.current + 1;
    hotRecommendationSeqRef.current = sequence;

    commercialAnalysisApi.getHotRecommendation(18)
      .then((response) => {
        if (cancelled || hotRecommendationSeqRef.current !== sequence) return;
        if (!response.stock || response.action === '待加载' || response.action === '无合适标的') {
          setRecommendedSearchStock(null);
          return;
        }
        setRecommendedSearchStock({
          ...response.stock,
          hotReason: response.reason || response.summary || `${response.action} · 今日热门推荐`,
          isHotStock: true,
        });
      })
      .catch(() => {
        if (cancelled || hotRecommendationSeqRef.current !== sequence) return;
        setRecommendedSearchStock(null);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const container = landingRef.current;
    if (!container) return undefined;

    const updateScrollCue = () => {
      const nextVisible = container.scrollTop < Math.max(72, window.innerHeight * 0.12);
      setShowScrollCue((current) => (current === nextVisible ? current : nextVisible));
    };

    const frameId = window.requestAnimationFrame(updateScrollCue);
    const timeoutId = window.setTimeout(updateScrollCue, 240);
    container.addEventListener('scroll', updateScrollCue, { passive: true });

    return () => {
      window.cancelAnimationFrame(frameId);
      window.clearTimeout(timeoutId);
      container.removeEventListener('scroll', updateScrollCue);
    };
  }, []);

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
    setQuery(getSearchStockLabel(stock));
    setSelectedSearchStock(stock);
    setSuggestionsOpen(false);
    setHighlightedIndex(0);
  };

  const clearDefaultSearchValue = () => {
    if (recommendedSearchStock && query.trim() === recommendedSearchLabel) {
      setQuery('');
      setSuggestions([]);
      setSuggestionsOpen(false);
      setHighlightedIndex(0);
      setSelectedSearchStock(null);
    }
  };

  const submitSearch = () => {
    if (selectedSearchStock && query.trim() === getSearchStockLabel(selectedSearchStock)) {
      setSuggestionsOpen(false);
      navigate(`/analysis/${encodeURIComponent(selectedSearchStock.code)}`);
      return;
    }

    if (recommendedSearchStock && query.trim() === recommendedSearchLabel) {
      setSuggestionsOpen(false);
      navigate(`/analysis/${encodeURIComponent(recommendedSearchStock.code)}`);
      return;
    }

    const [firstSuggestion] = suggestions;
    if (firstSuggestion) {
      setSuggestionsOpen(false);
      navigate(`/analysis/${encodeURIComponent(firstSuggestion.code)}`);
      return;
    }

    const normalizedTarget = extractSearchTarget(query);
    if (!normalizedTarget) {
      if (recommendedSearchStock) {
        navigate(`/analysis/${encodeURIComponent(recommendedSearchStock.code)}`);
      }
      return;
    }

    if (STOCK_CODE_TARGET_RE.test(normalizedTarget) && !isMarketOnlyQuery(normalizedTarget.toLowerCase())) {
      const analysisTarget = normalizeAnalysisTarget(normalizedTarget);
      setSuggestionsOpen(false);
      navigate(`/analysis/${encodeURIComponent(analysisTarget)}`);
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
    <main className="gyai-landing" ref={landingRef}>
      <section className="gyai-page gyai-intro-page" aria-label="每日股研AI首页">
        <div className="gyai-hero">
          <div className="gyai-hero-bg" aria-hidden="true" />
          <header className="gyai-nav">
            <div className="gyai-brand">
              <span className="gyai-brand-name">每日股研AI</span>
              <span className="gyai-brand-subtitle">AI量化算法实时评估A/H股</span>
            </div>

            <nav className="gyai-nav-right" aria-label="每日股研AI导航">
              <div className="gyai-edge-list" aria-label="核心优势">
                <span><Database aria-hidden="true" />A/H股全域数据</span>
                <span><LineChart aria-hidden="true" />金融量化算法</span>
                <span><Brain aria-hidden="true" />AI深度推理</span>
              </div>
            </nav>
          </header>

          <div className="gyai-hero-stage">
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
                <div className={`gyai-search-input-wrap${showRecommendedHotHint ? ' has-hot-badge' : ''}`}>
                  <Search className="gyai-search-icon" aria-hidden="true" />
                  <label htmlFor="gyai-stock-search" className="sr-only">输入股票代码或公司名</label>
                  {showRecommendedHotHint ? (
                    <span className="gyai-search-hot-badge" aria-label="今日热门推荐">
                      <Flame aria-hidden="true" />
                      今日热门推荐
                    </span>
                  ) : null}
                  <input
                    id="gyai-stock-search"
                    value={query}
                    onChange={(event) => {
                      const nextQuery = event.target.value;
                      setQuery(nextQuery);
                      setSelectedSearchStock(null);
                      setSuggestionsOpen(Boolean(nextQuery.trim()));
                      setHighlightedIndex(0);
                    }}
                    onMouseDown={() => setIsSearchFocused(true)}
                    onClick={clearDefaultSearchValue}
                    onFocus={() => {
                      setIsSearchFocused(true);
                      setSuggestionsOpen(Boolean(query.trim()));
                    }}
                    onDoubleClick={() => {
                      if (recommendedSearchStock) {
                        applyStock(recommendedSearchStock);
                      }
                    }}
                    onBlur={() => window.setTimeout(() => {
                      setIsSearchFocused(false);
                      setSuggestionsOpen(false);
                    }, 140)}
                    onKeyDown={handleSearchKeyDown}
                    placeholder={isSearchFocused ? '' : recommendedSearchLabel}
                    autoComplete="off"
                  />

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
              <div className="gyai-hero-proof" aria-label="每日股研AI输出内容">
                <article>
                  <span>01</span>
                  <strong>每日行情雷达</strong>
                  <small>价格、成交、波动实时刷新</small>
                </article>
                <article>
                  <span>02</span>
                  <strong>AI估值引擎</strong>
                  <small>区间定价 + 安全边际</small>
                </article>
                <article>
                  <span>03</span>
                  <strong>点位作战计划</strong>
                  <small>关注、确认、失效一步到位</small>
                </article>
                <article>
                  <span>04</span>
                  <strong>资讯证据链</strong>
                  <small>公告、板块、新闻联动验证</small>
                </article>
              </div>
            </div>

            <aside className="gyai-hero-digest" aria-label="五一视界示例摘要">
              <p className="gyai-digest-kicker">示例</p>
              <div className="gyai-digest-title">
                <strong>{previewStock.name}</strong>
                <span>{previewStock.code}</span>
              </div>
              <p className="gyai-digest-conclusion">{previewStock.conclusion.replace('AI判断：', '')}</p>
              <div className="gyai-digest-tags" aria-label="示例核心指标">
                <span>估值 <strong>{previewStock.valuation}</strong></span>
                <span>成长 <strong>{previewStock.growth}</strong></span>
                <span>风险 <strong>{previewStock.risk}</strong></span>
              </div>
              <div className="gyai-digest-range" aria-label="示例估值区间">
                <div>
                  <span>AI合理估值</span>
                  <strong>
                    {formatRangePrice(previewStock.rangeStart)}
                    <small> - </small>
                    {formatRangePrice(previewStock.rangeEnd)}
                  </strong>
                </div>
                <em>{previewStock.valuationNote}</em>
              </div>
              <div className="gyai-digest-bottom" aria-label="示例当前价格与确认位">
                <div className="gyai-digest-point">
                  <span>当前价</span>
                  <strong>{previewStock.price}</strong>
                  <em>{previewStock.pricePosition}</em>
                </div>
                <div className="gyai-digest-point">
                  <span>确认位</span>
                  <strong>{formatPointPrice(previewStock.pointPlan[1].price)}</strong>
                  <em>{previewStock.pointPlan[1].description}</em>
                </div>
              </div>
            </aside>
          </div>

          {showScrollCue ? (
            <a
              className="gyai-scroll-cue"
              href="#gyai-example-preview"
              aria-label="查看示例预览"
              onClick={() => setShowScrollCue(false)}
            >
              <span className="gyai-scroll-cue-stack" aria-hidden="true">
                <ChevronDown />
                <ChevronDown />
                <ChevronDown />
              </span>
            </a>
          ) : null}
        </div>

        <div id="gyai-example-preview" className="gyai-preview" role="region" aria-label="股票分析预览">
          <div className="gyai-preview-inner">
            <p
              className="gyai-preview-kicker notranslate"
              data-copy-version={LANDING_COPY_VERSION}
              translate="no"
            >
              示例 · {previewStock.name} {previewStock.code}
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
                  {formatRangePrice(previewStock.rangeStart)}
                  <small> - </small>
                  {formatRangePrice(previewStock.rangeEnd)}
                </strong>
                <em>{previewStock.currency}/股</em>
              </div>
              <div className="gyai-range-rail" aria-label={`${previewStock.rangeLabel} ${previewStock.currency}`}>
                <span className="gyai-range-segment gyai-range-segment-muted" />
                <span className="gyai-range-segment gyai-range-segment-copper" />
                <span className="gyai-range-segment gyai-range-segment-red" />
                <span
                  className="gyai-range-boundary gyai-range-boundary-low"
                  aria-label={`合理区间下限 ${formatRangePrice(previewStock.rangeStart)}`}
                >
                  <strong>{formatRangePrice(previewStock.rangeStart)}</strong>
                </span>
                <span
                  className="gyai-range-boundary gyai-range-boundary-high"
                  aria-label={`合理区间上限 ${formatRangePrice(previewStock.rangeEnd)}`}
                >
                  <strong>{formatRangePrice(previewStock.rangeEnd)}</strong>
                </span>
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
                      <strong>{formatPointPrice(item.price)}</strong>
                      <em>{item.description}</em>
                    </div>
                  ))}
                </div>
              </section>

              <section className="gyai-evidence-panel" aria-label="关键依据">
                <h3>关键依据（仅部分）</h3>
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
          </div>
        </div>
      </section>
    </main>
  );
};

export default CommercialLandingPage;
