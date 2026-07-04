import { Injectable, inject } from '@angular/core';
import { SwUpdate, VersionReadyEvent } from '@angular/service-worker';
import { filter } from 'rxjs/operators';

import { ConfirmService } from '../../presentation/shared/confirm.service';

/** How often long-lived tabs poll for a new deployed shell. */
const UPDATE_POLL_MS = 6 * 60 * 60 * 1000; // 6h

/**
 * P4-OFF WS-2.3 — service-worker update flow.
 *
 * When a rebuilt shell is deployed (new `ngsw.json` hashes), ngsw downloads it in
 * the background and emits `VERSION_READY`. We prompt via the accessible
 * ConfirmService (the repo bans native dialogs — `check:no-native-dialogs`); on
 * accept we activate the new version and reload. `unrecoverable` (the cached
 * shell diverged from what the SW expects) prompts a hard reload to recover.
 *
 * No-op when the SW is disabled (`ng serve` dev, or a build with
 * `serviceWorker: false`), so day-to-day dev is unaffected.
 */
@Injectable({ providedIn: 'root' })
export class SwUpdateService {
  private readonly swUpdate = inject(SwUpdate);
  private readonly confirm = inject(ConfirmService);

  init(): void {
    if (!this.swUpdate.isEnabled) return;

    this.swUpdate.versionUpdates
      .pipe(filter((e): e is VersionReadyEvent => e.type === 'VERSION_READY'))
      .subscribe(() => void this.promptReload());

    this.swUpdate.unrecoverable.subscribe(() => {
      void this.confirm
        .notify({
          title: 'App needs a refresh',
          body: 'The offline app cache got into a bad state. Reload to recover.',
          confirmLabel: 'Reload',
        })
        .then(() => document.location.reload());
    });

    // Long-lived tabs won't navigate for hours; poll so a deploy still lands.
    setInterval(() => {
      void this.swUpdate.checkForUpdate().catch(() => undefined);
    }, UPDATE_POLL_MS);
  }

  private async promptReload(): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'New version available',
      body: 'A newer version of the app is ready. Reload to use it.',
      confirmLabel: 'Reload',
      cancelLabel: 'Later',
    });
    if (!ok) return;
    await this.swUpdate.activateUpdate();
    document.location.reload();
  }
}
