import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { OfflineState } from './offline-state.service';

function health(offlineMode: boolean) {
  return {
    ok: true,
    json: async () => ({ status: 'ok', offline_mode: offlineMode }),
  } as Response;
}

/** The ng-test env's global localStorage (Node --localstorage-file) is broken;
 *  install a fresh in-memory Storage on window (what our code reads). */
function useMemoryLocalStorage(): void {
  const store = new Map<string, string>();
  const mock = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(window, 'localStorage', { value: mock, configurable: true });
}

describe('OfflineState', () => {
  let svc: OfflineState;

  beforeEach(() => {
    useMemoryLocalStorage();
    svc = new OfflineState();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('probe → online when reachable + offline_mode:false', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(health(false)));
    await svc.probe();
    expect(svc.mode()).toBe('online');
  });

  it('probe → offline-l1 when reachable + offline_mode:true', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(health(true)));
    await svc.probe();
    expect(svc.mode()).toBe('offline-l1');
    expect(svc.backendInfo()?.offline_mode).toBe(true);
  });

  it('probe → offline-l2 when unreachable (fetch rejects)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network')));
    await svc.probe();
    expect(svc.mode()).toBe('offline-l2');
  });

  it('probe stays ONLINE on a non-2xx health (reachable — must not block writes)', async () => {
    // Finding-4 safety: a reachable-but-erroring health must NOT flip to L2.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 } as Response));
    await svc.probe();
    expect(svc.mode()).toBe('online');
  });

  it('forced short-circuits to L2 without touching the network', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    svc.setForced(true);
    await svc.probe();
    expect(svc.mode()).toBe('offline-l2');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(window.localStorage.getItem('hf.offline.forced')).toBe('1');
  });

  it('transitions L2 → online on recovery', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('down')));
    await svc.probe();
    expect(svc.mode()).toBe('offline-l2');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(health(false)));
    await svc.probe();
    expect(svc.mode()).toBe('online');
  });
});
