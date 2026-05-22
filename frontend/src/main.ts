import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';

// WS-6 / UR-10: restore persisted theme BEFORE first paint to avoid a flash
// of the wrong theme (an accessibility regression for anyone who needs light).
try {
  const stored = localStorage.getItem('hf.theme');
  if (stored === 'light' || stored === 'dark') {
    document.documentElement.dataset['theme'] = stored;
  }
} catch {
  // localStorage may be unavailable; default theme stays.
}

bootstrapApplication(App, appConfig)
  .catch((err) => console.error(err));
