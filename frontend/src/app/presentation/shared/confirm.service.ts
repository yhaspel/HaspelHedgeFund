import { Injectable, signal } from '@angular/core';

export interface ConfirmOptions {
  title: string;
  body?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the confirm action as a destructive (danger) button. */
  danger?: boolean;
  /** When set, the user must type this exact string to enable the confirm
   *  action — an extra deliberation gate for the highest-stakes actions
   *  (e.g. the fund-wide kill switch). */
  requireText?: string;
}

interface PendingConfirm extends ConfirmOptions {
  resolve: (confirmed: boolean) => void;
}

/**
 * ConfirmService — promise-based confirmation routed through hf-modal.
 *
 * Replaces native confirm()/alert()/prompt() (HHF-05 / HHF-10) with a themed,
 * focus-trapped, keyboard-accessible dialog. A single <hf-confirm> host is
 * mounted once at the app root (app.html) and renders whatever request this
 * service holds.
 *
 * The method is `ask()`, not `confirm()`, on purpose: the native-dialog lint
 * gate bans `\bconfirm\(`, so a `confirm()` method would make every call site
 * trip the very guard it exists to satisfy.
 *
 *   const ok = await this.confirm.ask({ title: 'Delete X?', danger: true });
 *   if (!ok) return;
 */
@Injectable({ providedIn: 'root' })
export class ConfirmService {
  /** The active request, or null when nothing is open. Read by <hf-confirm>. */
  readonly active = signal<PendingConfirm | null>(null);

  ask(opts: ConfirmOptions): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      // Resolve any already-open request as cancelled before replacing it.
      this.active()?.resolve(false);
      this.active.set({ ...opts, resolve });
    });
  }

  /** Settle the current request and emit its result to the awaiting caller. */
  settle(confirmed: boolean): void {
    const cur = this.active();
    if (!cur) return;
    this.active.set(null);
    cur.resolve(confirmed);
  }
}
