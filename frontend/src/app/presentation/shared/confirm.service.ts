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

export interface NotifyOptions {
  title: string;
  body?: string;
  confirmLabel?: string;
  danger?: boolean;
}

export interface PromptOptions {
  title: string;
  body?: string;
  /** Label for the single text field. */
  label?: string;
  initialValue?: string;
  placeholder?: string;
  confirmLabel?: string;
}

/** A single in-flight request, rendered by <hf-confirm>. Kept as one flat shape
 *  (not a union) so the template can read fields without narrowing gymnastics. */
export interface PendingRequest {
  kind: 'confirm' | 'notify' | 'prompt';
  title: string;
  body?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  requireText?: string; // confirm only
  label?: string; // prompt only
  initialValue?: string; // prompt only
  placeholder?: string; // prompt only
  _resolve: (value: boolean | string | undefined) => void;
  _cancelValue: boolean | string | undefined | null;
}

/**
 * ConfirmService — promise-based dialogs routed through hf-modal.
 *
 * Replaces the native confirm / alert / prompt dialogs (HHF-05 / HHF-10) with
 * themed, focus-trapped, keyboard-accessible ones. A single <hf-confirm> host is
 * mounted once at the app root (app.html) and renders whatever request this
 * service holds. Three modes:
 *   - ask()     -> Promise<boolean>        (yes/no, optional danger + type-to-confirm)
 *   - notify()  -> Promise<void>           (single OK — replaces the native alert)
 *   - askText() -> Promise<string | null>  (one text field — replaces the native prompt)
 *
 * Methods are deliberately NOT named confirm/alert/prompt: the native-dialog
 * lint gate bans those identifiers followed by "(", so collision-free names keep
 * every call site clean.
 *
 *   const ok = await this.confirm.ask({ title: 'Delete X?', danger: true });
 *   if (!ok) return;
 */
@Injectable({ providedIn: 'root' })
export class ConfirmService {
  /** The active request, or null when nothing is open. Read by <hf-confirm>. */
  readonly active = signal<PendingRequest | null>(null);

  ask(opts: ConfirmOptions): Promise<boolean> {
    return new Promise<boolean>((resolve) =>
      this.open({ kind: 'confirm', ...opts, _resolve: resolve as (v: unknown) => void, _cancelValue: false }),
    );
  }

  notify(opts: NotifyOptions): Promise<void> {
    return new Promise<void>((resolve) =>
      this.open({ kind: 'notify', ...opts, _resolve: resolve as (v: unknown) => void, _cancelValue: undefined }),
    );
  }

  askText(opts: PromptOptions): Promise<string | null> {
    return new Promise<string | null>((resolve) =>
      this.open({ kind: 'prompt', ...opts, _resolve: resolve as (v: unknown) => void, _cancelValue: null }),
    );
  }

  /** Resolve the active request with the user's input (confirm: true, prompt: the text). */
  accept(value: boolean | string): void {
    const cur = this.active();
    if (!cur) return;
    this.active.set(null);
    cur._resolve(value);
  }

  /** Resolve the active request with its cancel value (confirm: false, prompt: null). */
  cancel(): void {
    const cur = this.active();
    if (!cur) return;
    this.active.set(null);
    cur._resolve(cur._cancelValue as boolean | string | undefined);
  }

  private open(req: PendingRequest): void {
    // Resolve any already-open request as cancelled before replacing it.
    const prev = this.active();
    if (prev) prev._resolve(prev._cancelValue as boolean | string | undefined);
    this.active.set(req);
  }
}
