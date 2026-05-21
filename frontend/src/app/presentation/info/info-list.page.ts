import {
  Component,
  OnInit,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { InfoStore, InfoSearchResult } from '../../abstraction/info.store';
import {
  InfoGuide,
  InfoGuideGroup,
} from '../../core/models/info.model';
import { GuideShellComponent } from '../shared/guide-shell.component';

interface GroupSection {
  group: InfoGuideGroup;
  label: string;
  eyebrow: string;
  guides: InfoGuide[];
}

@Component({
  selector: 'hf-info-list',
  standalone: true,
  imports: [FormsModule, RouterLink, GuideShellComponent],
  template: `
    <hf-guide-shell [crumbs]="[{ label: 'Guides' }]">
      <ng-template #body>
        <a class="skip-link" href="#info-main">Skip to content</a>
        <div class="page-head">
          <div>
            <div class="eyebrow">Guides</div>
            <h1 style="margin-top:6px">Guides</h1>
            <p style="font-size:13.5px;color:var(--text-2);margin:8px 0 0;max-width:640px">
              Everything you need to operate the AI Hedge Fund — what it does, how to use it,
              what each strategy means, and a plain-language glossary of every term in the app.
            </p>
          </div>
        </div>

        <label class="info-search" for="info-search-input">
          <svg width="14" height="14" aria-hidden="true">
            <use href="/icons.svg#i-search" />
          </svg>
          <input
            id="info-search-input"
            type="search"
            placeholder="Search guides, headings, abbreviations…"
            autocomplete="off"
            [ngModel]="query()"
            (ngModelChange)="onQueryChange($event)"
            aria-label="Search guides"
          />
          @if (query()) {
            <button type="button" (click)="clear()" aria-label="Clear search">
              <svg width="12" height="12"><use href="/icons.svg#i-x" /></svg>
            </button>
          }
        </label>
        <span class="visually-hidden" aria-live="polite">{{ liveCount() }}</span>

        <main id="info-main">
          @if (effectiveQuery()) {
            @if (results().length === 0) {
              <div class="info-empty">
                <strong>No guides match "{{ effectiveQuery() }}"</strong>
                <p style="font-size:13px;color:var(--text-2);margin:0">
                  Try a shorter query, or skim the cards below for the topic you need.
                </p>
                <button type="button" class="btn sm" (click)="clear()">Clear search</button>
              </div>
            } @else {
              @if (!info.indexReady() && hasHeadingPotential()) {
                <p style="font-size:11.5px;color:var(--text-3);margin:8px 0 0">
                  Indexing additional sections…
                </p>
              }
              <div class="info-results" role="list">
                @for (r of results(); track trackResult($index, r)) {
                  <a
                    role="listitem"
                    class="info-result"
                    [routerLink]="['/info', r.guide.slug]"
                    [fragment]="r.heading?.id"
                  >
                    <span class="info-result-title">
                      <strong>{{ r.guide.title }}</strong>
                      @if (r.heading) {
                        <span style="color:var(--text-3)"> · {{ r.heading.text }}</span>
                      }
                    </span>
                    <span class="info-result-sub">{{ r.guide.summary }}</span>
                  </a>
                }
              </div>
            }
          } @else {
            @for (s of sections; track s.group) {
              <section class="info-section">
                <header class="info-section-head">
                  <div class="eyebrow">{{ s.eyebrow }}</div>
                </header>
                <div class="info-grid">
                  @for (g of s.guides; track g.slug) {
                    <a
                      class="info-card"
                      [routerLink]="['/info', g.slug]"
                      [attr.aria-label]="g.title + ': ' + g.summary"
                    >
                      @if (s.group !== 'strategies') {
                        <span class="info-card-icon" aria-hidden="true">
                          <svg width="14" height="14">
                            <use [attr.href]="'/icons.svg#' + g.icon" />
                          </svg>
                        </span>
                      }
                      <div>
                        <h3 class="info-card-title">
                          {{ g.title }}
                          @if (g.status === 'coming-soon') {
                            <span class="pill warn"><span class="dot"></span>Coming soon</span>
                          }
                        </h3>
                        <p class="info-card-summary">{{ g.summary }}</p>
                      </div>
                    </a>
                  }
                </div>
              </section>
            }
          }
        </main>
      </ng-template>
    </hf-guide-shell>
  `,
  styles: [`
    .visually-hidden {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      white-space: nowrap;
      border: 0;
    }
  `],
})
export class InfoListPage implements OnInit {
  readonly info = inject(InfoStore);

  readonly query = signal('');
  readonly debouncedQuery = signal('');
  private debounceTimer: number | null = null;

  readonly sections: GroupSection[] = [
    {
      group: 'getting-started',
      label: 'Getting Started',
      eyebrow: 'Getting Started',
      guides: this.info.guidesByGroup('getting-started'),
    },
    {
      group: 'strategies',
      label: 'Strategies',
      eyebrow: 'Strategies',
      guides: this.info.guidesByGroup('strategies'),
    },
    {
      group: 'reference',
      label: 'Reference',
      eyebrow: 'Reference',
      guides: this.info.guidesByGroup('reference'),
    },
  ];

  readonly effectiveQuery = computed(() => this.debouncedQuery().trim());

  readonly results = computed<InfoSearchResult[]>(() => {
    const q = this.effectiveQuery();
    if (!q) return [];
    // Re-read indexReady so this recomputes when the index finishes loading.
    void this.info.indexReady();
    return this.info.search(q);
  });

  readonly liveCount = computed(() => {
    if (!this.effectiveQuery()) return '';
    const n = this.results().length;
    return `${n} ${n === 1 ? 'result' : 'results'} for "${this.effectiveQuery()}"`;
  });

  readonly hasHeadingPotential = computed(() => {
    // While the index is still building, hint to users that more results
    // (heading-level) may appear shortly. Skip the hint if the query is so
    // unique it's already matched everywhere it could.
    return this.effectiveQuery().length >= 2;
  });

  ngOnInit() {
    // Kick off the search index lazy-load.
    this.info.buildSearchIndex();
  }

  onQueryChange(q: string) {
    this.query.set(q);
    if (this.debounceTimer !== null) window.clearTimeout(this.debounceTimer);
    this.debounceTimer = window.setTimeout(() => this.debouncedQuery.set(q), 150);
  }

  clear() {
    this.query.set('');
    this.debouncedQuery.set('');
  }

  trackResult(i: number, r: InfoSearchResult) {
    return `${r.guide.slug}#${r.heading?.id ?? ''}-${i}`;
  }
}
