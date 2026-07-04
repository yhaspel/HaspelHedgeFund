import {
  HttpErrorResponse,
  HttpHeaders,
  HttpInterceptorFn,
  HttpResponse,
} from '@angular/common/http';
import { inject } from '@angular/core';
import { from, of, switchMap, throwError } from 'rxjs';
import { catchError, tap } from 'rxjs/operators';

import { environment } from '../../../environments/environment';
import { cacheKey, CacheRecord, get, put, SCHEMA_VERSION } from './api-cache';
import { OfflineState } from './offline-state.service';

/**
 * P4-OFF WS-3.2 — the last-known-value cache interceptor.
 *
 * Registered OUTERMOST (before authInterceptor) so it sees the FINAL outcome
 * after the auth interceptor's silent-refresh retry: it caches post-refresh 2xx
 * bodies, and a dead-refresh 401 propagates as a 401 (never a cache hit). It also
 * caches `/me/`, which is what lets AuthStore rehydrate the user at L2.
 *
 * Only cross-origin API GETs (not denylisted) are handled. `/auth/` and
 * `/health/` are never replayed (login must not serve stale tokens; the probe
 * must tell the truth). 401/403/404/other-4xx/500 are never served from cache —
 * real answers from a live backend stay visible.
 */

// Login / refresh / signup must never replay; the health probe must be truthful.
const DENYLIST = ['/auth/', '/health/'];

function isCacheableApiGet(url: string): boolean {
  if (!url.startsWith(environment.apiBaseUrl)) return false;
  return !DENYLIST.some((seg) => url.includes(seg));
}

function staleResponse(url: string, rec: CacheRecord): HttpResponse<unknown> {
  return new HttpResponse({
    url,
    status: 200,
    body: rec.body,
    // HttpResponse's `headers` init requires an HttpHeaders instance.
    headers: new HttpHeaders({
      'X-HF-Cache': 'stale',
      'X-HF-Cached-At': new Date(rec.savedAt).toISOString(),
    }),
  });
}

export const offlineCacheInterceptor: HttpInterceptorFn = (req, next) => {
  if (req.method !== 'GET' || !isCacheableApiGet(req.url)) {
    return next(req);
  }
  const offline = inject(OfflineState);
  const key = cacheKey(req.urlWithParams);

  // Forced-offline (Settings toggle): skip the network entirely — serve the
  // cache hit or throw a synthetic status-0 error. Without this branch the
  // toggle would keep hitting the live backend.
  if (offline.forced()) {
    return from(get(key)).pipe(
      switchMap((rec) =>
        rec
          ? of(staleResponse(req.urlWithParams, rec))
          : throwError(
              () => new HttpErrorResponse({ status: 0, url: req.urlWithParams }),
            ),
      ),
    );
  }

  return next(req).pipe(
    tap((event) => {
      if (event instanceof HttpResponse && event.status >= 200 && event.status < 300) {
        // Fire-and-forget: never block the response path; swallow quota errors.
        void put({
          key,
          url: req.urlWithParams,
          body: event.body,
          status: event.status,
          savedAt: Date.now(),
          schemaVersion: SCHEMA_VERSION,
        });
      }
    }),
    catchError((err: HttpErrorResponse) => {
      const status = err?.status ?? 0;
      // Only backend-unreachable shapes replay cache. The predicate is written
      // out explicitly — a bare `|| 503 || 504` is always-truthy and would
      // replay cache on 401s too.
      if (status === 0 || [502, 503, 504].includes(status)) {
        offline.reportFailure();
        return from(get(key)).pipe(
          switchMap((rec) =>
            rec ? of(staleResponse(req.urlWithParams, rec)) : throwError(() => err),
          ),
        );
      }
      return throwError(() => err);
    }),
  );
};
