/**
 * P4-OFF — defensive localStorage access.
 *
 * localStorage can throw (Safari private mode) or be unavailable (some embedded
 * webviews, and the unit-test env's Node `--localstorage-file` stub). The offline
 * layer reads it at service-construction time, so a throw there would break DI —
 * degrade to "no persisted value" instead.
 */
export function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function safeSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

export function safeRemove(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}
