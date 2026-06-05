import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Subject, interval, takeUntil } from 'rxjs';

import { InvestorProfileStore } from '../../abstraction/investor-profile.store';
import { ModelsStore } from '../../abstraction/models.store';
import {
  QuestionnaireQuestion,
  QuestionnaireSection,
} from '../../core/models/investor-profile.model';
import { ModelEntry } from '../../core/models/model.types';
import { AppShellComponent } from '../shared/app-shell.component';

const DEFAULT_MODEL_ID = 'openrouter:meta-llama/llama-3.3-70b-instruct';
const POLL_INTERVAL_MS = 2000;
const POLL_BUDGET_MS = 90000;
const TICKER_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/;

@Component({
  selector: 'hf-questionnaire-page',
  standalone: true,
  imports: [CommonModule, FormsModule, AppShellComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <hf-app-shell [crumbs]="[{label:'Profile', link:'/profile'}, {label:'Questionnaire'}]">
      <div class="page-head">
        <div>
          <div class="eyebrow">Personalize</div>
          <h1 class="mt-1.5">Investor Questionnaire</h1>
          <p class="text-text-3 text-[12.5px] mt-1 max-w-[640px]">
            17 short questions about your goals, risk tolerance and
            temperament. The AI builds a profile that calibrates the council
            for every analysis you run.
          </p>
          <p class="privacy text-text-3 text-[11px] mt-1">
            Privacy: financial details are stored as bands, never exact
            figures. Only the derived profile brief reaches the LLM
            provider — never your raw answers.
          </p>
        </div>
      </div>

      @if (loading()) {
        <div class="card"><div class="card-bd">Loading questionnaire…</div></div>
      } @else if (schema(); as s) {
        <form class="card" (ngSubmit)="onSubmit($event)">
          <div class="card-bd">
            @for (section of s.sections; track section.id) {
              <fieldset class="section">
                <legend>{{ section.title }}</legend>
                @for (q of section.questions; track q.id) {
                  <div class="field">
                    <label class="lbl">
                      {{ q.label }}
                      @if (q.required) {
                        <span class="req" aria-hidden="true">*</span>
                      }
                    </label>
                    @if (q.type === 'single') {
                      <div class="opts" role="radiogroup" [attr.aria-label]="q.label">
                        @for (opt of q.options ?? []; track opt) {
                          <label class="opt">
                            <input type="radio"
                                   [name]="q.id"
                                   [value]="opt"
                                   [checked]="single(q.id) === opt"
                                   (change)="setSingle(q.id, opt)" />
                            <span>{{ opt }}</span>
                          </label>
                        }
                      </div>
                    } @else if (q.type === 'multi') {
                      <div class="opts">
                        @for (opt of q.options ?? []; track opt) {
                          <label class="chip"
                                 [class.on]="multiHas(q.id, opt)">
                            <input type="checkbox"
                                   [checked]="multiHas(q.id, opt)"
                                   (change)="toggleMulti(q.id, opt)" />
                            <span>{{ opt }}</span>
                          </label>
                        }
                      </div>
                    } @else if (q.type === 'text') {
                      <textarea class="input ta"
                                rows="2"
                                [attr.maxlength]="q.max_len ?? 500"
                                [value]="textValue(q.id)"
                                (input)="setText(q.id, $any($event.target).value)"></textarea>
                      <span class="cnt text-text-3 text-[11px]">
                        {{ textValue(q.id).length }} / {{ q.max_len ?? 500 }}
                      </span>
                    } @else if (q.type === 'tickers') {
                      <div class="ticker-input">
                        <div class="chips">
                          @for (t of tickersValue(q.id); track t) {
                            <span class="chip mono on">
                              {{ t }}
                              <button type="button"
                                      class="rm"
                                      aria-label="Remove ticker"
                                      (click)="removeTicker(q.id, t)">×</button>
                            </span>
                          }
                        </div>
                        <div class="add">
                          <input class="input mono"
                                 type="text"
                                 placeholder="ADD SYMBOL"
                                 [value]="tickerDraft()"
                                 (input)="setTickerDraft($any($event.target).value.toUpperCase())"
                                 (keydown.enter)="addTicker($event, q)" />
                          <button type="button"
                                  class="btn ghost sm"
                                  (click)="addTicker($event, q)">Add</button>
                        </div>
                        @if (q.hint) {
                          <span class="hint text-text-3 text-[11px]">{{ q.hint }}</span>
                        }
                      </div>
                    }
                  </div>
                }
              </fieldset>
            }
            <fieldset class="section">
              <legend>Model</legend>
              <div class="field">
                <label class="lbl" for="model-pick">Analysis model</label>
                <select id="model-pick"
                        class="input"
                        [(ngModel)]="modelId"
                        name="model_id">
                  @for (m of availableModels(); track m.id) {
                    <option [value]="m.id"
                            [disabled]="m.disabled">
                      {{ m.label }}
                    </option>
                  }
                </select>
                <span class="hint text-text-3 text-[11px]">
                  Default: Llama 3.3 70B (balanced tier).
                </span>
              </div>
            </fieldset>
            @if (error()) {
              <p class="alert" role="alert">{{ error() }}</p>
            }
            <div class="actions">
              <button type="button"
                      class="btn ghost"
                      (click)="onCancel()">Cancel</button>
              <button type="submit"
                      class="btn primary"
                      [disabled]="submitting() || analyzing()">
                {{ submitting() ? 'Submitting…' : analyzing() ? 'Analyzing your profile…' : 'Analyze' }}
              </button>
            </div>
          </div>
        </form>
      }
    </hf-app-shell>
  `,
  styles: [
    `
      .page-head {
        display: flex;
        justify-content: space-between;
        margin-bottom: 16px;
      }
      .section {
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        padding: 12px 16px;
        margin: 0 0 12px;
      }
      .section legend {
        padding: 0 6px;
        color: var(--text-2);
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.06em;
      }
      .field {
        display: flex;
        flex-direction: column;
        gap: 4px;
        margin: 12px 0;
      }
      .lbl { font-size: 13px; color: var(--text); }
      .req { color: var(--acc-short-fg); margin-left: 4px; }
      .opts {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }
      .opt {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        background: var(--surface-2);
        border: 1px solid var(--border);
        border-radius: var(--r-4);
        cursor: pointer;
        font-size: 12px;
      }
      .opt input { margin: 0; }
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        background: var(--surface-2);
        border: 1px solid var(--border);
        border-radius: var(--r-6);
        cursor: pointer;
        font-size: 12px;
      }
      .chip.on {
        background: var(--acc-info-soft);
        border-color: var(--acc-info);
      }
      .chip input { display: none; }
      .ta { width: 100%; font-family: inherit; }
      .input {
        padding: 6px 10px;
        background: var(--surface-2);
        color: var(--text);
        border: 1px solid var(--border);
        border-radius: var(--r-4);
      }
      .cnt { align-self: flex-end; }
      .ticker-input { display: flex; flex-direction: column; gap: 8px; }
      .chips { display: flex; flex-wrap: wrap; gap: 6px; }
      .chip .rm {
        background: transparent;
        border: 0;
        color: var(--text-3);
        cursor: pointer;
        font-size: 14px;
        padding: 0 0 0 4px;
      }
      .add { display: flex; gap: 6px; }
      .add .input { width: 200px; text-transform: uppercase; }
      .actions {
        display: flex;
        justify-content: flex-end;
        gap: 10px;
        margin-top: 12px;
      }
      .alert {
        padding: 10px 14px;
        background: var(--acc-short-soft);
        color: var(--acc-short-fg);
        border-radius: var(--r-6);
      }
      .privacy { font-style: italic; }
    `,
  ],
})
export class QuestionnairePage implements OnInit {
  private readonly store = inject(InvestorProfileStore);
  private readonly modelsStore = inject(ModelsStore);
  private readonly router = inject(Router);

  readonly schema = this.store.schema;
  readonly loading = signal(true);
  readonly submitting = signal(false);
  readonly analyzing = signal(false);
  readonly error = signal<string | null>(null);

  modelId = DEFAULT_MODEL_ID;

  // form state
  private readonly _single = signal<Record<string, string>>({});
  private readonly _multi = signal<Record<string, string[]>>({});
  private readonly _text = signal<Record<string, string>>({});
  private readonly _tickers = signal<Record<string, string[]>>({});
  readonly tickerDraft = signal('');

  private readonly destroy$ = new Subject<void>();

  readonly availableModels = computed(() => {
    const catalog = this.modelsStore.models();
    const keys = this.modelsStore.keys();
    if (catalog.length === 0) {
      return [
        { id: DEFAULT_MODEL_ID, label: 'Llama 3.3 70B (default)', disabled: false },
      ];
    }
    return catalog.map((m: ModelEntry) => {
      const provider = m.id.split(':')[0];
      const status = keys?.[provider as keyof typeof keys];
      const hasKey = status === 'set';
      const disabled = !hasKey && m.id !== DEFAULT_MODEL_ID && provider !== 'ollama';
      const suffix = disabled ? ' (no key)' : '';
      const reasoning = m.supports_reasoning ? '🧠 ' : '';
      return {
        id: m.id,
        label: `${reasoning}${m.display_name ?? m.id} · ${m.tier ?? 'tier?'}${suffix}`,
        disabled,
      };
    });
  });

  ngOnInit(): void {
    this.store.loadSchema().subscribe({
      next: () => this.loading.set(false),
      error: (e) => {
        this.error.set(e?.error?.detail ?? 'Could not load questionnaire.');
        this.loading.set(false);
      },
    });
    this.modelsStore.loadAll().subscribe();
  }

  single(qid: string): string {
    return this._single()[qid] ?? '';
  }

  setSingle(qid: string, value: string): void {
    this._single.set({ ...this._single(), [qid]: value });
  }

  multiHas(qid: string, value: string): boolean {
    return (this._multi()[qid] ?? []).includes(value);
  }

  toggleMulti(qid: string, value: string): void {
    const cur = this._multi()[qid] ?? [];
    const next = cur.includes(value)
      ? cur.filter((v) => v !== value)
      : [...cur, value];
    this._multi.set({ ...this._multi(), [qid]: next });
  }

  textValue(qid: string): string {
    return this._text()[qid] ?? '';
  }

  setText(qid: string, value: string): void {
    this._text.set({ ...this._text(), [qid]: value });
  }

  tickersValue(qid: string): string[] {
    return this._tickers()[qid] ?? [];
  }

  setTickerDraft(value: string): void {
    this.tickerDraft.set(value);
  }

  addTicker(event: Event, q: QuestionnaireQuestion): void {
    event.preventDefault();
    const t = this.tickerDraft().trim().toUpperCase();
    if (!t) return;
    if (!TICKER_RE.test(t)) {
      this.error.set(`Invalid ticker: ${t}`);
      return;
    }
    const cap = q.max_select ?? 15;
    const cur = this._tickers()[q.id] ?? [];
    if (cur.includes(t)) {
      this.tickerDraft.set('');
      return;
    }
    if (cur.length >= cap) {
      this.error.set(`Max ${cap} tickers.`);
      return;
    }
    this._tickers.set({ ...this._tickers(), [q.id]: [...cur, t] });
    this.tickerDraft.set('');
    this.error.set(null);
  }

  removeTicker(qid: string, ticker: string): void {
    const next = (this._tickers()[qid] ?? []).filter((t) => t !== ticker);
    this._tickers.set({ ...this._tickers(), [qid]: next });
  }

  onCancel(): void {
    this.router.navigate(['/profile']);
  }

  onSubmit(event: Event): void {
    event.preventDefault();
    this.error.set(null);

    const answers: Record<string, unknown> = {};
    const sections = this.schema()?.sections ?? [];
    let missingRequired: string | null = null;
    for (const section of sections as QuestionnaireSection[]) {
      for (const q of section.questions) {
        let v: unknown = undefined;
        if (q.type === 'single') v = this._single()[q.id] || undefined;
        else if (q.type === 'multi') v = this._multi()[q.id]?.length ? this._multi()[q.id] : undefined;
        else if (q.type === 'text') v = (this._text()[q.id] ?? '').trim() || undefined;
        else if (q.type === 'tickers') v = this._tickers()[q.id]?.length ? this._tickers()[q.id] : undefined;
        if (v === undefined) {
          if (q.required) missingRequired ??= q.label;
          continue;
        }
        answers[q.id] = v;
      }
    }
    if (missingRequired) {
      this.error.set(`Please answer: ${missingRequired}`);
      return;
    }

    this.submitting.set(true);
    this.store
      .submit({ answers, model_id: this.modelId })
      .subscribe({
        next: (response) => {
          this.submitting.set(false);
          if (response.analysis_status === 'done') {
            this.router.navigate(['/profile']);
            return;
          }
          if (response.analysis_status === 'failed') {
            this.error.set(response.error_message || 'Analysis failed.');
            return;
          }
          this.startPolling(response.id);
        },
        error: (err) => {
          this.submitting.set(false);
          this.error.set(
            err?.error?.detail ?? err?.message ?? 'Submission failed.',
          );
        },
      });
  }

  private startPolling(id: number): void {
    this.analyzing.set(true);
    const start = Date.now();
    interval(POLL_INTERVAL_MS)
      .pipe(takeUntil(this.destroy$))
      .subscribe(() => {
        if (Date.now() - start > POLL_BUDGET_MS) {
          this.analyzing.set(false);
          this.error.set(
            'Analysis is taking longer than expected. Try again in a moment.',
          );
          this.destroy$.next();
          return;
        }
        this.store.pollOne(id).subscribe({
          next: (resp) => {
            if (resp.analysis_status === 'done') {
              this.analyzing.set(false);
              this.destroy$.next();
              this.router.navigate(['/profile']);
            } else if (resp.analysis_status === 'failed') {
              this.analyzing.set(false);
              this.error.set(resp.error_message || 'Analysis failed.');
              this.destroy$.next();
            }
          },
          error: () => undefined,
        });
      });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }
}
