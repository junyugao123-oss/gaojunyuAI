import apiClient from './index';
import { toCamelCase } from './utils';
import type { CommercialAnalysis, CommercialSearchResponse } from '../types/commercialAnalysis';

export const commercialAnalysisApi = {
  search: async (query: string, limit = 8): Promise<CommercialSearchResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/commercial-analysis/search', {
      params: {
        q: query,
        limit,
      },
    });

    return toCamelCase<CommercialSearchResponse>(response.data);
  },

  get: async (stockCode: string): Promise<CommercialAnalysis> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/commercial-analysis/${encodeURIComponent(stockCode)}`
    );

    return toCamelCase<CommercialAnalysis>(response.data);
  },
};
