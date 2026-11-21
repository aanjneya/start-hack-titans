/**
 * API_CONTRACT.ts
 *
 * Single source of truth for shared API endpoints, request/response payloads,
 * and domain types across frontend and backend.
 *
 * Rule: Update this contract FIRST before implementing or changing integrations.
 */

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ApiResponse<T> {
  ok: boolean;
  data?: T;
  error?: ApiError;
  mocked?: boolean;
}

export interface HealthCheckResponse {
  status: 'ok' | 'degraded';
  mockMode: boolean;
  timestamp: string;
}

/**
 * Route registry — keep endpoint paths and HTTP methods in sync here.
 */
export const API_ROUTES = {
  HEALTH: '/api/health',
} as const;
