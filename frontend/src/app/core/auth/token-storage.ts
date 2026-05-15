import { Injectable } from '@angular/core';

const ACCESS = 'hf.access';
const REFRESH = 'hf.refresh';

function store(): Storage {
  return window.localStorage;
}

@Injectable({ providedIn: 'root' })
export class TokenStorage {
  getAccess(): string | null {
    return store().getItem(ACCESS);
  }
  getRefresh(): string | null {
    return store().getItem(REFRESH);
  }
  set(access: string, refresh: string): void {
    store().setItem(ACCESS, access);
    store().setItem(REFRESH, refresh);
  }
  clear(): void {
    store().removeItem(ACCESS);
    store().removeItem(REFRESH);
  }
}
