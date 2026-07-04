/**
 * P4-OFF WS-4.3 — thrown by ApiClient for a non-GET attempted at L2 / forced
 * offline, before any HTTP is issued. Writes are blocked, never queued (D6):
 * a replay queue is a sync engine and a trading hazard.
 */
export class OfflineWriteBlockedError extends Error {
  constructor(public readonly path: string) {
    super(`Unavailable offline: ${path}`);
    this.name = 'OfflineWriteBlockedError';
  }
}
