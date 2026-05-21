import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, of, tap, catchError, finalize, map } from 'rxjs';
import {
  INFO_GUIDES,
  InfoGuide,
  InfoGuideGroup,
} from '../core/models/info.model';

/** A search hit: either a guide-level match or a section-heading match. */
export interface InfoSearchResult {
  guide: InfoGuide;
  /** Present iff this hit is from a heading match. */
  heading?: { id: string; text: string };
  /** Coarse rank; lower is better. */
  rank: number;
}

interface IndexEntry {
  guide: InfoGuide;
  headings: { id: string; text: string }[];
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-');
}

@Injectable({ providedIn: 'root' })
export class InfoStore {
  private readonly http = inject(HttpClient);

  readonly guides: InfoGuide[] = INFO_GUIDES;

  private readonly _cache = new Map<string, string>();
  private readonly _loading = signal<Record<string, boolean>>({});
  private readonly _error = signal<Record<string, string | null>>({});
  readonly loading = this._loading.asReadonly();
  readonly error = this._error.asReadonly();

  // Search index
  private indexEntries: IndexEntry[] | null = null;
  private indexBuildingPromise: Promise<void> | null = null;
  readonly indexReady = signal(false);

  guidesByGroup(group: InfoGuideGroup): InfoGuide[] {
    return this.guides.filter((g) => g.group === group);
  }

  guideBySlug(slug: string): InfoGuide | undefined {
    return this.guides.find((g) => g.slug === slug);
  }

  adjacentGuides(slug: string): { prev: InfoGuide | null; next: InfoGuide | null } {
    const i = this.guides.findIndex((g) => g.slug === slug);
    if (i < 0) return { prev: null, next: null };
    return {
      prev: i > 0 ? this.guides[i - 1] : null,
      next: i < this.guides.length - 1 ? this.guides[i + 1] : null,
    };
  }

  loadGuide(slug: string): Observable<string> {
    const cached = this._cache.get(slug);
    if (cached !== undefined) {
      // Clear any prior error.
      this._error.set({ ...this._error(), [slug]: null });
      return of(cached);
    }
    this._loading.set({ ...this._loading(), [slug]: true });
    this._error.set({ ...this._error(), [slug]: null });
    return this.http
      .get(`/guides/${slug}.md`, { responseType: 'text' })
      .pipe(
        tap((md) => {
          this._cache.set(slug, md);
        }),
        catchError((e) => {
          const msg =
            e && e.status ? `Could not load guide (${e.status})` : 'Could not load guide';
          this._error.set({ ...this._error(), [slug]: msg });
          throw e;
        }),
        finalize(() => {
          this._loading.set({ ...this._loading(), [slug]: false });
        }),
      );
  }

  /** Build the search index by lazy-loading every guide once. */
  buildSearchIndex(): Promise<void> {
    if (this.indexEntries) return Promise.resolve();
    if (this.indexBuildingPromise) return this.indexBuildingPromise;
    this.indexBuildingPromise = Promise.all(
      this.guides.map((g) =>
        this.loadGuide(g.slug)
          .toPromise()
          .then((md): IndexEntry => ({
            guide: g,
            headings: extractHeadings(md || ''),
          }))
          .catch((): IndexEntry => ({ guide: g, headings: [] })),
      ),
    ).then((entries) => {
      this.indexEntries = entries;
      this.indexReady.set(true);
    });
    return this.indexBuildingPromise;
  }

  /** Case-insensitive search over title/summary/keywords and headings. */
  search(query: string): InfoSearchResult[] {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const results: InfoSearchResult[] = [];

    for (const guide of this.guides) {
      const titleHit = guide.title.toLowerCase().includes(q);
      const summaryHit = guide.summary.toLowerCase().includes(q);
      const kwHit = guide.keywords.some((k) => k.toLowerCase().includes(q));
      if (titleHit || summaryHit || kwHit) {
        results.push({
          guide,
          rank: titleHit ? 0 : summaryHit ? 1 : 2,
        });
      }
    }

    if (this.indexEntries) {
      for (const entry of this.indexEntries) {
        for (const h of entry.headings) {
          if (h.text.toLowerCase().includes(q)) {
            // Skip if this guide already matched at a higher tier through title.
            const dupGuideOnly = results.find(
              (r) => r.guide.slug === entry.guide.slug && !r.heading,
            );
            results.push({
              guide: entry.guide,
              heading: { id: h.id, text: h.text },
              // Rank below guide-level hits; demote further if the guide had no
              // top-level hit so heading-only matches still appear after guide
              // hits.
              rank: dupGuideOnly ? 3 : 4,
            });
          }
        }
      }
    }

    results.sort((a, b) => a.rank - b.rank);
    return results;
  }
}

/** Extract `## …` / `### …` headings with their slugified ids. */
export function extractHeadings(md: string): { id: string; text: string; level: 2 | 3 }[] {
  const out: { id: string; text: string; level: 2 | 3 }[] = [];
  const used = new Set<string>();
  const re = /^(#{2,3})\s+(.+?)\s*$/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(md))) {
    const level = m[1].length === 2 ? 2 : 3;
    const text = m[2].trim();
    let id = slugify(text);
    let candidate = id;
    let n = 2;
    while (used.has(candidate)) candidate = `${id}-${n++}`;
    used.add(candidate);
    out.push({ id: candidate, text, level: level as 2 | 3 });
  }
  return out;
}
