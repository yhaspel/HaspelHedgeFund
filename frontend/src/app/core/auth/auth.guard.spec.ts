import { provideHttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { Router, UrlTree } from '@angular/router';
import { authGuard } from './auth.guard';
import { TokenStorage } from './token-storage';

class FakeTokenStorage {
  private a: string | null = null;
  getAccess() { return this.a; }
  getRefresh() { return null; }
  set(a: string) { this.a = a; }
  clear() { this.a = null; }
}

function runGuard(): boolean | UrlTree {
  return TestBed.runInInjectionContext(
    () => authGuard({} as never, {} as never) as boolean | UrlTree,
  );
}

describe('authGuard', () => {
  let tokens: FakeTokenStorage;

  beforeEach(() => {
    tokens = new FakeTokenStorage();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        { provide: TokenStorage, useValue: tokens },
      ],
    });
  });

  it('redirects to /login when unauthenticated and no token', () => {
    const router = TestBed.inject(Router);
    const result = runGuard();
    expect(result).toEqual(router.createUrlTree(['/login']));
  });

  it('allows when a token is present', () => {
    tokens.set('fake');
    expect(runGuard()).toBe(true);
  });
});
