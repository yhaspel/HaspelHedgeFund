import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, throwError } from 'rxjs';
import { environment } from '../../../environments/environment';
import { OfflineState } from '../offline/offline-state.service';
import { OfflineWriteBlockedError } from '../offline/offline-write-blocked.error';
import { ConfirmService } from '../../presentation/shared/confirm.service';

@Injectable({ providedIn: 'root' })
export class ApiClient {
  private readonly http = inject(HttpClient);
  private readonly offline = inject(OfflineState);
  private readonly confirm = inject(ConfirmService);
  private readonly base = environment.apiBaseUrl;

  get<T>(path: string): Observable<T> {
    return this.http.get<T>(`${this.base}${path}`);
  }

  post<T>(path: string, body: unknown): Observable<T> {
    return this.blocked<T>(path) ?? this.http.post<T>(`${this.base}${path}`, body);
  }

  put<T>(path: string, body: unknown): Observable<T> {
    return this.blocked<T>(path) ?? this.http.put<T>(`${this.base}${path}`, body);
  }

  patch<T>(path: string, body: unknown): Observable<T> {
    return this.blocked<T>(path) ?? this.http.patch<T>(`${this.base}${path}`, body);
  }

  delete<T>(path: string): Observable<T> {
    return this.blocked<T>(path) ?? this.http.delete<T>(`${this.base}${path}`);
  }

  /**
   * P4-OFF WS-4.3: block every non-GET at L2 / forced offline BEFORE hitting the
   * network (writes are never queued, D6). L1 does NOT block — the backend is up.
   * Returns a throwing Observable (and toasts) when blocked, else null.
   */
  private blocked<T>(path: string): Observable<T> | null {
    if (this.offline.mode() === 'offline-l2' || this.offline.forced()) {
      void this.confirm.notify({
        title: 'Unavailable offline',
        body: 'This action needs the backend, which is unreachable. Nothing was sent.',
      });
      return throwError(() => new OfflineWriteBlockedError(path));
    }
    return null;
  }
}
