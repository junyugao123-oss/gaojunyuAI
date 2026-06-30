import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  CommercialAnalysis,
  CommercialHotRecommendationResponse,
  CommercialSearchResponse,
} from '../types/commercialAnalysis';

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

  getHotRecommendation: async (limit = 18): Promise<CommercialHotRecommendationResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/commercial-analysis/hot-recommendation',
      {
        params: { limit },
        timeout: 12000,
      },
    );

    return toCamelCase<CommercialHotRecommendationResponse>(response.data);
  },
};
