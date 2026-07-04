/**
 * P4-OFF WS-3.1 — last-known-value API cache (IndexedDB).
 *
 * A ~1-store IndexedDB wrapper the offline interceptor uses to replay the last
 * 2xx GET body when the backend is unreachable (L2). Chosen over localStorage
 * (async, effectively unbounded for our payloads, structured, per-user-scoped;
 * see the plan's D2). No runtime dependency.
 *
 * Entries are scoped by the JWT's user id decoded straight from localStorage —
 * NOT from AuthStore, which hydrates asynchronously after the boot-time `/me/`
 * fetch. An AuthStore-derived scope would file boot GETs under `anon` forever,
 * so logout's clearScope(user) would miss them and the next account on this
 * browser would replay the previous user's data.
 */

import { safeGet } from './safe-storage';

const DB_NAME = 'hf-offline';
const DB_VERSION = 1;
const STORE = 'responses';
const ACCESS_TOKEN_KEY = 'hf.access'; // mirrors TokenStorage

/** Bump on a breaking change to the cached record/API shape → mismatched
 *  entries are dropped on read (see get()). */
export const SCHEMA_VERSION = 1;

export interface CacheRecord {
  key: string;
  url: string;
  body: unknown;
  status: number;
  savedAt: number; // epoch ms
  schemaVersion: number;
}

export const ANON_SCOPE = 'anon';

/** Decode the JWT payload without verifying (client-side scoping only). */
function decodeJwt(token: string): Record<string, unknown> | null {
  try {
    const payload = token.split('.')[1];
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** The cache scope: the access token's user id (SimpleJWT `user_id`), else `sub`,
 *  else `anon` when there is no token. Read from localStorage directly. */
export function currentScope(): string {
  const token = safeGet(ACCESS_TOKEN_KEY);
  if (!token) return ANON_SCOPE;
  const claims = decodeJwt(token);
  const id = claims?.['user_id'] ?? claims?.['sub'];
  return id != null ? String(id) : ANON_SCOPE;
}

export function cacheKey(url: string, scope = currentScope()): string {
  return `${scope}|GET|${url}`;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'key' });
        store.createIndex('savedAt', 'savedAt');
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(db: IDBDatabase, mode: IDBTransactionMode): IDBObjectStore {
  return db.transaction(STORE, mode).objectStore(STORE);
}

/** Read a cached record. A schemaVersion mismatch is treated as a miss (and the
 *  stale entry is best-effort deleted) so a shell upgrade can't replay a body of
 *  a shape the new code no longer understands. */
export async function get(key: string): Promise<CacheRecord | null> {
  try {
    const db = await openDb();
    const rec = await new Promise<CacheRecord | undefined>((resolve, reject) => {
      const r = tx(db, 'readonly').get(key);
      r.onsuccess = () => resolve(r.result as CacheRecord | undefined);
      r.onerror = () => reject(r.error);
    });
    db.close();
    if (!rec) return null;
    if (rec.schemaVersion !== SCHEMA_VERSION) {
      void del(key);
      return null;
    }
    return rec;
  } catch {
    return null;
  }
}

export async function put(record: CacheRecord): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve, reject) => {
      const r = tx(db, 'readwrite').put(record);
      r.onsuccess = () => resolve();
      r.onerror = () => reject(r.error);
    });
    db.close();
  } catch {
    // Swallow quota / private-mode errors — caching is best-effort.
  }
}

async function del(key: string): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve) => {
      const r = tx(db, 'readwrite').delete(key);
      r.onsuccess = () => resolve();
      r.onerror = () => resolve();
    });
    db.close();
  } catch {
    /* best-effort */
  }
}

/** Delete every entry for one scope (called on logout for both the user and
 *  `anon` scope — last-known portfolio values are exactly the residue logout
 *  must remove, and the anon scope must not survive a session boundary). */
export async function clearScope(scope: string): Promise<void> {
  try {
    const db = await openDb();
    const prefix = `${scope}|`;
    await new Promise<void>((resolve) => {
      const store = tx(db, 'readwrite');
      const cursorReq = store.openCursor();
      cursorReq.onsuccess = () => {
        const cursor = cursorReq.result;
        if (!cursor) return resolve();
        if (String(cursor.key).startsWith(prefix)) cursor.delete();
        cursor.continue();
      };
      cursorReq.onerror = () => resolve();
    });
    db.close();
  } catch {
    /* best-effort */
  }
}

/** LRU prune to at most `max` entries (oldest `savedAt` first). */
export async function prune(max: number): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve) => {
      const store = tx(db, 'readwrite');
      const countReq = store.count();
      countReq.onsuccess = () => {
        const excess = countReq.result - max;
        if (excess <= 0) return resolve();
        let removed = 0;
        const cursorReq = store.index('savedAt').openCursor(); // ascending = oldest first
        cursorReq.onsuccess = () => {
          const cursor = cursorReq.result;
          if (!cursor || removed >= excess) return resolve();
          cursor.delete();
          removed++;
          cursor.continue();
        };
        cursorReq.onerror = () => resolve();
      };
      countReq.onerror = () => resolve();
    });
    db.close();
  } catch {
    /* best-effort */
  }
}
