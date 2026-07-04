import { Injectable, signal } from '@angular/core';

import { environment } from '../../../environments/environment';
import { safeGet, safeRemove, safeSet } from './safe-storage';

export type OfflineMode = 'online' | 'offline-l1' | 'offline-l2';

export interface BackendHealth {
  status: string;
  offline_mode: boolean;
  llm?: { forced_preset: string | null; local_model: string; local_available: boolean };
}

const FORCED_KEY = 'hf.offline.forced';
// The backend health view does its own 2s Ollama probe on a cache miss, so a
// tight client abort would race it and false-flag L2 (which blocks all writes).
const PROBE_TIMEOUT_MS = 5000;
const DEGRADED_POLL_MS = 30_000;

/**
 * P4-OFF WS-4.1 — offline state, the single source of truth for the banner,
 * write-blocking, and disabled CTAs.
 *
 * Truth source is `GET /api/health/` (D4), not `navigator.onLine` (unreliable,
 * and can't tell L1 from L2 — localhost is reachable with Wi-Fi off):
 *   reachable + offline_mode:true  → offline-l1  (local stack up, WAN down)
 *   reachable + offline_mode:false → online
 *   unreachable                    → offline-l2  (backend down; read-only)
 * Re-probed on interceptor-reported failures, every 30s while degraded, and on
 * window online/offline events. A manual "simulate offline" toggle forces L2.
 */
@Injectable({ providedIn: 'root' })
export class OfflineState {
  readonly mode = signal<OfflineMode>('online');
  readonly forced = signal<boolean>(safeGet(FORCED_KEY) === '1');
  readonly lastProbeAt = signal<number | null>(null);
  readonly backendInfo = signal<BackendHealth | null>(null);

  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private probing = false;
  // A probe requested (reportFailure / online-offline event) while one is already
  // in flight is coalesced into a single re-probe once the current one settles —
  // so a recovery signal arriving mid-probe (up to a 5s timeout) is never dropped.
  private reprobeRequested = false;

  init(): void {
    void this.probe();
    window.addEventListener('online', () => void this.probe());
    window.addEventListener('offline', () => void this.probe());
  }

  /** Called by the interceptor when a request fails in a way that suggests the
   *  backend is unreachable — triggers an immediate re-probe. */
  reportFailure(): void {
    void this.probe();
  }

  /** Settings toggle: force offline (L2) for this browser without touching the
   *  network. The interceptor then serves cache for every GET. */
  setForced(on: boolean): void {
    this.forced.set(on);
    if (on) safeSet(FORCED_KEY, '1');
    else safeRemove(FORCED_KEY);
    void this.probe();
  }

  async probe(): Promise<void> {
    if (this.forced()) {
      this.setMode('offline-l2');
      this.backendInfo.set(null);
      return;
    }
    if (this.probing) {
      this.reprobeRequested = true;
      return;
    }
    this.probing = true;
    try {
      const res = await fetch(`${environment.apiBaseUrl}/health/`, {
        signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
        cache: 'no-store',
      });
      if (res.ok) {
        const body = (await res.json()) as BackendHealth;
        this.backendInfo.set(body);
        this.setMode(body.offline_mode ? 'offline-l1' : 'online');
      } else {
        // The backend RESPONDED (a 4xx/5xx health), so it is reachable — do NOT
        // flip to L2 (which would block every write). Treat as online; a real
        // write surfaces its own error. L2 is reserved for genuine unreachability.
        this.backendInfo.set(null);
        this.setMode('online');
      }
    } catch {
      // fetch rejected (network unreachable) or the probe timed out.
      this.setMode('offline-l2');
      this.backendInfo.set(null);
    } finally {
      this.lastProbeAt.set(Date.now());
      this.probing = false;
      if (this.reprobeRequested) {
        this.reprobeRequested = false;
        void this.probe();
      }
    }
  }

  private setMode(mode: OfflineMode): void {
    this.mode.set(mode);
    // Poll while degraded so recovery is picked up within ~30s; stop when online.
    if (mode === 'online') {
      if (this.pollTimer) {
        clearInterval(this.pollTimer);
        this.pollTimer = null;
      }
    } else if (!this.pollTimer && !this.forced()) {
      this.pollTimer = setInterval(() => void this.probe(), DEGRADED_POLL_MS);
    }
  }
}
