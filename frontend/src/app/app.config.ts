import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  ApplicationConfig,
  inject,
  isDevMode,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
} from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideServiceWorker } from '@angular/service-worker';

import { AuthStore } from './abstraction/auth.store';
import { routes } from './app.routes';
import { authInterceptor } from './core/auth/auth.interceptor';
import { SwUpdateService } from './core/offline/sw-update.service';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    provideHttpClient(withInterceptors([authInterceptor])),
    // P4-OFF WS-2: register ngsw so a refresh with the WAN/backend down boots the
    // shell from cache. Disabled in dev (`ng serve` never builds the SW anyway).
    // `registerWhenStable` is zoneless-safe (stability comes from PendingTasks).
    provideServiceWorker('ngsw-worker.js', {
      enabled: !isDevMode(),
      registrationStrategy: 'registerWhenStable:30000',
    }),
    provideAppInitializer(() => {
      // If a token survived a reload, rehydrate the current user before the
      // first route resolves so guards don't bounce an authenticated user.
      inject(AuthStore).refreshMe();
      // Wire the SW update notifier (no-op when the SW is disabled).
      inject(SwUpdateService).init();
    }),
  ],
};
