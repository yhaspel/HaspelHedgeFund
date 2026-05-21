import {
  AfterViewInit,
  Component,
  DestroyRef,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  ActivatedRoute,
  Router,
  RouterLink,
} from '@angular/router';
import { InfoStore } from '../../abstraction/info.store';
import { InfoGuide } from '../../core/models/info.model';
import { GuideShellComponent } from '../shared/guide-shell.component';
import { MarkdownComponent, MarkdownHeading } from '../shared/markdown.component';

@Component({
  selector: 'hf-info-detail',
  standalone: true,
  imports: [RouterLink, GuideShellComponent, MarkdownComponent],
  template: `
    <hf-guide-shell [crumbs]="crumbs()">
      <ng-template #body>
        <a class="skip-link" href="#info-main">Skip to content</a>
        @if (guide(); as g) {
          <div class="page-head">
            <div>
              <div class="eyebrow">Guide</div>
              <h1 #pageHeading tabindex="-1" style="margin-top:6px">
                {{ g.title }}
                @if (g.status === 'coming-soon') {
                  <span class="pill warn" style="margin-left:8px;vertical-align:middle">
                    <span class="dot"></span>Coming soon
                  </span>
                }
              </h1>
              <p style="font-size:13.5px;color:var(--text-2);margin:8px 0 0;max-width:720px">
                {{ g.summary }}
              </p>
            </div>
          </div>

          <div class="info-detail-grid">
            <aside class="info-toc-pane" aria-label="On this page">
              <nav class="info-toc">
                <h3>On this page</h3>
                <ul>
                  @for (h of headings(); track h.id) {
                    <li>
                      <a
                        [href]="'#' + h.id"
                        [class.toc-l3]="h.level === 3"
                        [attr.aria-current]="activeHeading() === h.id ? 'true' : null"
                        (click)="onTocClick($event, h.id)"
                      >{{ h.text }}</a>
                    </li>
                  }
                </ul>
              </nav>
            </aside>

            <main id="info-main" #mainEl>
              @if (loading()) {
                <div class="stack" style="margin-bottom:16px">
                  <div class="skel" style="height:18px;width:60%"></div>
                  <div class="skel" style="height:12px;width:90%"></div>
                  <div class="skel" style="height:12px;width:85%"></div>
                  <div class="skel" style="height:12px;width:70%"></div>
                </div>
              } @else if (error()) {
                <div class="card" style="margin-bottom:16px">
                  <div class="card-bd" style="display:flex;flex-direction:column;gap:12px">
                    <strong>Could not load this guide.</strong>
                    <p style="font-size:13px;color:var(--text-2);margin:0">
                      {{ error() }}
                    </p>
                    <button class="btn sm" (click)="reload()">Try again</button>
                  </div>
                </div>
              } @else {
                <hf-markdown
                  [source]="source()"
                  (headingsChange)="onHeadingsChange($event)"
                ></hf-markdown>
              }

              @if (showToc1Col()) {
                <details class="info-toc-disclosure">
                  <summary style="font-size:12px;color:var(--text-2);cursor:pointer">
                    On this page
                  </summary>
                  <nav class="info-toc" style="position:static;margin-top:8px">
                    <ul>
                      @for (h of headings(); track h.id) {
                        <li>
                          <a [href]="'#' + h.id" [class.toc-l3]="h.level === 3"
                             (click)="onTocClick($event, h.id)">{{ h.text }}</a>
                        </li>
                      }
                    </ul>
                  </nav>
                </details>
              }

              <div class="info-detail-foot">
                @if (prevGuide(); as p) {
                  <a class="foot-link" [routerLink]="['/info', p.slug]">
                    <span class="foot-label">← Previous</span>
                    <span class="foot-title">{{ p.title }}</span>
                  </a>
                }
                @if (nextGuide(); as n) {
                  <a class="foot-link next" [routerLink]="['/info', n.slug]">
                    <span class="foot-label">Next →</span>
                    <span class="foot-title">{{ n.title }}</span>
                  </a>
                }
              </div>
              <p style="margin-top:24px">
                <a [routerLink]="['/info']" style="color:var(--acc-info-fg);font-size:13px">← Back to all guides</a>
              </p>
            </main>
          </div>
        }
      </ng-template>
    </hf-guide-shell>
  `,
})
export class InfoDetailPage implements OnInit, AfterViewInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly info = inject(InfoStore);
  private readonly destroyRef = inject(DestroyRef);

  @ViewChild('pageHeading') pageHeading?: ElementRef<HTMLElement>;
  @ViewChild('mainEl') mainEl?: ElementRef<HTMLElement>;

  readonly slug = signal<string>('');
  readonly source = signal<string>('');
  readonly headings = signal<MarkdownHeading[]>([]);
  readonly activeHeading = signal<string | null>(null);

  readonly guide = computed(() => this.info.guideBySlug(this.slug()));
  readonly loading = computed(() => !!this.info.loading()[this.slug()]);
  readonly error = computed(() => this.info.error()[this.slug()] || null);

  readonly prevGuide = computed(() => this.info.adjacentGuides(this.slug()).prev);
  readonly nextGuide = computed(() => this.info.adjacentGuides(this.slug()).next);

  readonly crumbs = computed(() => {
    const g = this.guide();
    return [
      { label: 'Guides', link: '/info' },
      { label: g ? g.title : 'Guide' },
    ];
  });

  readonly showToc1Col = computed(() => this.headings().length > 1);

  private intersectionObserver: IntersectionObserver | null = null;
  private pendingFragment: string | null = null;

  ngOnInit() {
    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((params) => {
      const slug = params.get('slug') || '';
      this.slug.set(slug);
      const guide = this.info.guideBySlug(slug);
      if (!guide) {
        this.router.navigateByUrl('/info', { replaceUrl: true });
        return;
      }
      // Capture fragment so we can scroll to it after markdown renders.
      this.pendingFragment = this.route.snapshot.fragment;
      this.source.set('');
      this.headings.set([]);
      this.activeHeading.set(null);
      this.loadGuide();
    });

    this.route.fragment.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((frag) => {
      // When in-page anchor changes after initial load, scroll smoothly.
      if (frag && this.source()) {
        this.pendingFragment = frag;
        queueMicrotask(() => this.tryScrollToFragment());
      }
    });
  }

  ngAfterViewInit() {
    // Move focus to the heading on initial entry.
    queueMicrotask(() => this.pageHeading?.nativeElement.focus({ preventScroll: true }));
  }

  ngOnDestroy() {
    this.intersectionObserver?.disconnect();
  }

  reload() {
    this.loadGuide();
  }

  private loadGuide() {
    const slug = this.slug();
    this.info.loadGuide(slug).subscribe({
      next: (md) => {
        if (this.slug() !== slug) return; // stale
        this.source.set(md);
      },
      error: () => {
        // Error is reflected via the store; the template renders the retry card.
      },
    });
  }

  onHeadingsChange(hs: MarkdownHeading[]) {
    this.headings.set(hs);
    this.setupScrollSpy(hs);
    queueMicrotask(() => this.tryScrollToFragment());
  }

  private tryScrollToFragment() {
    if (!this.pendingFragment) {
      // No fragment → reset scroll on a fresh guide so the user starts at the top.
      window.scrollTo({ top: 0, behavior: 'auto' });
      return;
    }
    const id = this.pendingFragment;
    const el = document.getElementById(id);
    if (!el) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
    this.activeHeading.set(id);
    this.pendingFragment = null;
    // For accessibility, also try to give focus to the heading.
    (el as HTMLElement).setAttribute('tabindex', '-1');
    (el as HTMLElement).focus({ preventScroll: true });
  }

  onTocClick(e: MouseEvent, id: string) {
    e.preventDefault();
    // Update the URL fragment without re-triggering a route load.
    this.router.navigate([], {
      relativeTo: this.route,
      fragment: id,
      preserveFragment: false,
      replaceUrl: false,
    });
    this.pendingFragment = id;
    this.tryScrollToFragment();
  }

  private setupScrollSpy(hs: MarkdownHeading[]) {
    this.intersectionObserver?.disconnect();
    if (typeof IntersectionObserver === 'undefined') return;
    const targets: HTMLElement[] = [];
    for (const h of hs) {
      const el = document.getElementById(h.id);
      if (el) targets.push(el as HTMLElement);
    }
    if (!targets.length) return;
    this.intersectionObserver = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) {
          this.activeHeading.set(visible[0].target.id);
        }
      },
      { rootMargin: '-80px 0px -60% 0px', threshold: [0, 1] },
    );
    targets.forEach((t) => this.intersectionObserver!.observe(t));
  }
}
