import apiClient from './index';

export type ExtractItem = {
  code?: string | null;
  name?: string | null;
  confidence: string;
};

export type ExtractFromImageResponse = {
  codes: string[];
  items?: ExtractItem[];
  rawText?: string;
};

export type HotStockItem = {
  rank?: number | null;
  code: string;
  name: string;
  price?: number | null;
  changePercent?: number | null;
  hotScore?: number | null;
  reason?: string | null;
};

export type HotStocksResponse = {
  stocks: HotStockItem[];
  generatedAt?: string | null;
};

function toNumberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export const stocksApi = {
  async extractFromImage(file: File): Promise<ExtractFromImageResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
    const response = await apiClient.post(
      '/api/v1/stocks/extract-from-image',
      formData,
      {
        headers,
        timeout: 60000, // Vision API can be slow; 60s
      },
    );

    const data = response.data as { codes?: string[]; items?: ExtractItem[]; raw_text?: string };
    return {
      codes: data.codes ?? [],
      items: data.items,
      rawText: data.raw_text,
    };
  },

  async parseImport(file?: File, text?: string): Promise<ExtractFromImageResponse> {
    if (file) {
      const formData = new FormData();
      formData.append('file', file);
      const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
      const response = await apiClient.post('/api/v1/stocks/parse-import', formData, { headers });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    if (text) {
      const response = await apiClient.post('/api/v1/stocks/parse-import', { text });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    throw new Error('请提供文件或粘贴文本');
  },

  async getHotRanking(limit = 12, timeoutMs = 60000): Promise<HotStocksResponse> {
    const response = await apiClient.get('/api/v1/stocks/hot-ranking', {
      params: { limit },
      timeout: timeoutMs,
    });
    const data = response.data as {
      stocks?: Array<Record<string, unknown>>;
      generated_at?: string | null;
      generatedAt?: string | null;
    };
    return {
      stocks: (data.stocks || []).map((item) => ({
        rank: toNumberOrNull(item.rank),
        code: String(item.code || ''),
        name: String(item.name || ''),
        price: toNumberOrNull(item.price),
        changePercent: toNumberOrNull(item.change_percent ?? item.changePercent),
        hotScore: toNumberOrNull(item.hot_score ?? item.hotScore),
        reason: typeof item.reason === 'string' ? item.reason : null,
      })).filter((item) => item.code && item.name),
      generatedAt: data.generated_at || data.generatedAt || null,
    };
  },
};
