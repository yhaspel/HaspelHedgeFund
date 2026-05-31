import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AppShellComponent } from '../shared/app-shell.component';
import { SettingsTabsComponent } from './settings-tabs.component';
import { ModelsStore } from '../../abstraction/models.store';

/**
 * Settings › Providers — BYO provider credentials.
 *
 * Split out of the former monolithic settings-models page (section "A").
 * Two cards (LLM keys + data keys) over a single Save handler. Every
 * binding, save flow and data-test hook is preserved verbatim.
 */
@Component({
  selector: 'hf-settings-providers',
  standalone: true,
  imports: [CommonModule, FormsModule, AppShellComponent, SettingsTabsComponent],
  template: `
    <hf-app-shell [crumbs]="[{ label: 'Settings', link: '/settings/models' }, { label: 'Providers' }]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Settings</div>
          <h1 class="mt-1.5">Providers</h1>
        </div>
      </div>

      <hf-settings-tabs />

      <div role="tabpanel" aria-label="Providers settings">
        <div class="grid grid-cols-2 gap-[18px] max-w-[1100px] items-start">
          <!-- LLM provider keys -->
          <section class="card">
            <div class="card-hd"><h2 class="title">LLM provider keys</h2></div>
            <div class="card-bd flex flex-col gap-3">
              <p class="text-[11.5px] text-text-3 m-0">
                Keys are stored Fernet-encrypted on your user row. P4a will replace this with a multi-tenant vault.
              </p>
              @for (p of providers; track p.field) {
                <div class="field">
                  <label class="lbl flex justify-between" [attr.for]="'llm-key-' + p.field">
                    <span>{{ p.label }}</span>
                    <span class="pill" [class.ok]="statusOf(p.field) === 'set'">
                      <span class="dot"></span>{{ statusOf(p.field) }}
                    </span>
                  </label>
                  <input [id]="'llm-key-' + p.field" class="input mono" type="password" autocomplete="new-password"
                    [(ngModel)]="keyEdits[p.field]" [name]="p.field"
                    [attr.data-test]="'llm-key-' + p.field"
                    placeholder="•••• paste to replace, blank to keep" />
                </div>
              }
              <div class="field">
                <label class="lbl" for="ollama-host">Ollama host (for local models)</label>
                <input id="ollama-host" class="input mono" type="text" [(ngModel)]="ollamaHost" name="ollama"
                  placeholder="http://localhost:11434" data-test="ollama-host" />
              </div>
            </div>
          </section>

          <!-- Data provider keys -->
          <section class="card">
            <div class="card-hd"><h2 class="title">Data provider keys</h2></div>
            <div class="card-bd flex flex-col gap-3">
              <p class="text-[11.5px] text-text-3 m-0">
                P2n BYOK: FMP and Tiingo require user-supplied keys (no platform fallback in prod). FRED is optional — free public data falls back to a shared platform key.
              </p>
              @for (p of dataProviders; track p.field) {
                <div class="field">
                  <label class="lbl flex justify-between" [attr.for]="'data-key-' + p.field">
                    <span>{{ p.label }}</span>
                    <span class="pill" [class.ok]="statusOf(p.field) === 'set'">
                      <span class="dot"></span>{{ statusOf(p.field) }}
                    </span>
                  </label>
                  <input [id]="'data-key-' + p.field" class="input mono" type="password" autocomplete="new-password"
                    [(ngModel)]="keyEdits[p.field]" [name]="p.field"
                    [attr.aria-describedby]="'data-key-note-' + p.field"
                    [attr.data-test]="'data-key-' + p.field"
                    placeholder="•••• paste to replace, blank to keep" />
                  <p [id]="'data-key-note-' + p.field" class="text-[11px] text-text-3 m-0 mt-0.5">{{ p.note }}</p>
                </div>
              }
            </div>
          </section>

          <!-- Notification provider keys -->
          <section class="card">
            <div class="card-hd"><h2 class="title">Notification keys</h2></div>
            <div class="card-bd flex flex-col gap-3">
              <p class="text-[11.5px] text-text-3 m-0">
                Used to deliver your email notifications. Falls back to the platform key when unset.
              </p>
              @for (p of emailProviders; track p.field) {
                <div class="field">
                  <label class="lbl flex justify-between" [attr.for]="'email-key-' + p.field">
                    <span>{{ p.label }}</span>
                    <span class="pill" [class.ok]="statusOf(p.field) === 'set'">
                      <span class="dot"></span>{{ statusOf(p.field) }}
                    </span>
                  </label>
                  <input [id]="'email-key-' + p.field" class="input mono" type="password" autocomplete="new-password"
                    [(ngModel)]="keyEdits[p.field]" [name]="p.field"
                    [attr.aria-describedby]="'email-key-note-' + p.field"
                    [attr.data-test]="'email-key-' + p.field"
                    placeholder="•••• paste to replace, blank to keep" />
                  <p [id]="'email-key-note-' + p.field" class="text-[11px] text-text-3 m-0 mt-0.5">{{ p.note }}</p>
                </div>
              }
            </div>
          </section>
        </div>

        <div class="save-row max-w-[1100px]">
          <button type="button" class="btn primary save-btn" (click)="saveKeys()" [disabled]="savingKeys()"
            data-test="save-keys">
            {{ savingKeys() ? 'Saving…' : 'Save provider keys' }}
          </button>
          @if (keysMsg()) {
            <p role="status" aria-live="polite" class="text-[11.5px] text-[var(--acc-long-fg)] m-0" data-test="keys-msg">{{ keysMsg() }}</p>
          }
        </div>
      </div>
    </hf-app-shell>
  `,
  styles: [
    `
      .save-row {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-top: 18px;
      }
      .save-btn {
        height: 32px;
        justify-content: center;
        min-width: 200px;
      }
    `,
  ],
})
export class SettingsProvidersPage implements OnInit {
  readonly store = inject(ModelsStore);

  readonly providers = [
    { field: 'anthropic', label: 'Anthropic API key' },
    { field: 'openrouter', label: 'OpenRouter API key' },
    { field: 'openai', label: 'OpenAI API key' },
  ] as const;
  readonly dataProviders = [
    {
      field: 'fmp',
      label: 'FMP API key',
      note: 'Required for backtests and live agent runs. Sign up at financialmodelingprep.com.',
    },
    {
      field: 'tiingo',
      label: 'Tiingo API key',
      note: 'Required for news features. Free tier available at tiingo.com.',
    },
    {
      field: 'fred',
      label: 'FRED API key',
      note: 'Optional — defaults to a shared platform key. Set your own for isolation or higher rate limits.',
    },
  ] as const;
  readonly emailProviders = [
    {
      field: 'resend',
      label: 'Resend API key',
      note: 'Optional — overrides the platform key so notifications send from your own Resend account. Get one at resend.com.',
    },
  ] as const;

  keyEdits: Record<string, string> = {
    anthropic: '', openrouter: '', openai: '',
    fmp: '', tiingo: '', fred: '', resend: '',
  };
  ollamaHost = '';

  savingKeys = signal(false);
  keysMsg = signal<string | null>(null);

  ngOnInit(): void {
    this.store.loadAll().subscribe(() => {
      const keys = this.store.keys();
      if (keys) this.ollamaHost = keys.ollama_host ?? '';
    });
  }

  statusOf(field: string): string {
    return (this.store.keys() as Record<string, string> | null)?.[field] ?? 'unset';
  }

  saveKeys(): void {
    this.savingKeys.set(true);
    const body: Record<string, string> = { ollama_host: this.ollamaHost };
    const allFields = ['anthropic', 'openrouter', 'openai', 'fmp', 'tiingo', 'fred', 'resend'];
    for (const f of allFields) {
      if (this.keyEdits[f]) body[`${f}_api_key`] = this.keyEdits[f];
    }
    this.store.saveKeys(body).subscribe({
      next: () => {
        this.savingKeys.set(false);
        this.keysMsg.set('Saved. Reloading models…');
        this.keyEdits = {
          anthropic: '', openrouter: '', openai: '',
          fmp: '', tiingo: '', fred: '', resend: '',
        };
        this.store.loadModels().subscribe();
      },
      error: () => {
        this.savingKeys.set(false);
        this.keysMsg.set('Failed to save');
      },
    });
  }
}
