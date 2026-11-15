/**
 * Centralized API client for NETfix Read-Only Web Dashboard.
 *
 * Guarantees:
 * 1. Strict read-only HTTP GET operations only.
 * 2. Proper error propagation from FastAPI standardized error responses.
 * 3. Configurable base URL via VITE_API_BASE_URL.
 */

import { API_BASE_URL } from '../utils/constants';
import type { ApiErrorResponse } from './types';

export class ApiError extends Error {
  code: string;
  statusCode: number;
  details?: unknown;

  constructor(message: string, code: string, statusCode: number, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.statusCode = statusCode;
    this.details = details;
  }
}

/**
 * Perform a strictly read-only GET request to the NETfix API.
 */
export async function getJson<T>(endpoint: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const url = new URL(`${API_BASE_URL}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`);

  if (params) {
    Object.entries(params).forEach(([key, val]) => {
      if (val !== undefined && val !== null) {
        url.searchParams.append(key, String(val));
      }
    });
  }

  try {
    const response = await fetch(url.toString(), {
      method: 'GET',
      headers: {
        Accept: 'application/json',
      },
    });

    if (!response.ok) {
      let errPayload: ApiErrorResponse | null = null;
      try {
        errPayload = await response.json();
      } catch {
        // Response was not JSON
      }

      if (errPayload && errPayload.error) {
        throw new ApiError(
          errPayload.error.message || `API error ${response.status}`,
          errPayload.error.code || `HTTP_${response.status}`,
          response.status,
          errPayload.error.details
        );
      }

      throw new ApiError(
        `Request failed with status ${response.status}: ${response.statusText}`,
        `HTTP_${response.status}`,
        response.status
      );
    }

    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    const msg = error instanceof Error ? error.message : 'Unknown network error';
    throw new ApiError(
      `Failed to connect to NETfix API (${url.origin}): ${msg}`,
      'NETWORK_ERROR',
      0
    );
  }
}

/**
 * Perform a strictly validated POST request to the NETfix API.
 * Used exclusively for explicit advisory operations (such as starting an M4 diagnosis).
 */
export async function postJson<T>(endpoint: string, body?: unknown): Promise<T> {
  const url = new URL(`${API_BASE_URL}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`);

  try {
    const response = await fetch(url.toString(), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    if (!response.ok) {
      let errPayload: ApiErrorResponse | null = null;
      try {
        errPayload = await response.json();
      } catch {
        // Not JSON
      }

      if (errPayload && errPayload.error) {
        throw new ApiError(
          errPayload.error.message || `API error ${response.status}`,
          errPayload.error.code || `HTTP_${response.status}`,
          response.status,
          errPayload.error.details
        );
      }

      throw new ApiError(
        `Request failed with status ${response.status}: ${response.statusText}`,
        `HTTP_${response.status}`,
        response.status
      );
    }

    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    const msg = error instanceof Error ? error.message : 'Unknown network error';
    throw new ApiError(
      `Failed to connect to NETfix API (${url.origin}): ${msg}`,
      'NETWORK_ERROR',
      0
    );
  }
}
