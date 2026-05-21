import {
  Component,
  ElementRef,
  ViewEncapsulation,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked } from 'marked';

export interface MarkdownHeading {
  id: string;
  text: string;
  level: 2 | 3;
}

const ADMONITION_RE = /^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/i;

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-');
}

function ensureUnique(id: string, used: Set<string>): string {
  let candidate = id || 'section';
  let n = 2;
  while (used.has(candidate)) {
    candidate = `${id}-${n++}`;
  }
  used.add(candidate);
  return candidate;
}

@Component({
  selector: 'hf-markdown',
  standalone: true,
  encapsulation: ViewEncapsulation.None,
  template: `<div class="guide-prose" [innerHTML]="rendered()"></div>`,
})
export class MarkdownComponent {
  source = input<string>('');
  dropLeadingHeading = input<boolean>(true);

  readonly headings = signal<MarkdownHeading[]>([]);
  readonly headingsChange = output<MarkdownHeading[]>();

  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly sanitizer = inject(DomSanitizer);

  readonly rendered = computed<SafeHtml>(() => {
    const md = this.source();
    if (!md) {
      this.headings.set([]);
      return this.sanitizer.bypassSecurityTrustHtml('');
    }
    const html = this.parse(md, this.dropLeadingHeading());
    return this.sanitizer.bypassSecurityTrustHtml(html);
  });

  constructor() {
    // After the rendered HTML is inserted, post-process: assign heading ids,
    // mark external links, rewrite admonition blockquotes. Runs every time
    // the rendered HTML changes.
    effect(() => {
      // Track the rendered signal.
      void this.rendered();
      // Defer until after Angular flushes the [innerHTML] update.
      queueMicrotask(() => this.postProcess());
    });
  }

  private parse(md: string, dropLeading: boolean): string {
    let text = md;
    if (dropLeading) {
      // Strip the first `# heading` line (and its blank line).
      text = text.replace(/^\s*#\s+.+\n+/, '');
    }
    // Parse synchronously.
    return marked.parse(text, { async: false }) as string;
  }

  private postProcess(): void {
    const root: HTMLElement | null = this.host.nativeElement.querySelector('.guide-prose');
    if (!root) return;

    // Assign heading ids and collect for the ToC.
    const used = new Set<string>();
    const collected: MarkdownHeading[] = [];
    const headingEls = root.querySelectorAll('h2, h3') as NodeListOf<HTMLHeadingElement>;
    headingEls.forEach((h: HTMLHeadingElement) => {
      const text = (h.textContent || '').trim();
      const baseId = h.id && h.id.length > 0 ? h.id : slugify(text);
      const id = ensureUnique(baseId, used);
      h.id = id;
      const level = h.tagName === 'H2' ? 2 : 3;
      collected.push({ id, text, level });
    });

    // External link policy.
    const anchors = root.querySelectorAll('a[href]') as NodeListOf<HTMLAnchorElement>;
    anchors.forEach((a: HTMLAnchorElement) => {
      const href = a.getAttribute('href') || '';
      if (/^https?:\/\//i.test(href)) {
        a.setAttribute('target', '_blank');
        a.setAttribute('rel', 'noopener noreferrer');
      }
    });

    // Admonition blockquotes: `> [!WARNING] ...`
    root.querySelectorAll('blockquote').forEach((bq: Element) => {
      const firstP = bq.querySelector('p');
      if (!firstP || !firstP.firstChild) return;
      const firstText =
        firstP.firstChild.nodeType === Node.TEXT_NODE
          ? (firstP.firstChild.textContent || '')
          : '';
      const m = firstText.match(ADMONITION_RE);
      if (!m) return;
      const kind = m[1].toUpperCase();
      // Strip the marker text from the first text node.
      firstP.firstChild.textContent = firstText.replace(ADMONITION_RE, '');
      bq.classList.add('admonition');
      bq.classList.add(`adm-${kind.toLowerCase()}`);
      // Add an icon header.
      const header = document.createElement('div');
      header.className = 'adm-head';
      const iconHref = kind === 'NOTE' || kind === 'TIP' ? '#i-info' : '#i-alert';
      header.innerHTML = `
        <svg width="14" height="14" aria-hidden="true"><use href="/icons.svg${iconHref}" /></svg>
        <span>${kind.charAt(0) + kind.slice(1).toLowerCase()}</span>
      `;
      bq.insertBefore(header, bq.firstChild);
    });

    // Update headings signal + emit only if it changed (avoid loops).
    const next = collected;
    const prev = this.headings();
    if (
      prev.length !== next.length ||
      prev.some((p, i) => p.id !== next[i].id || p.text !== next[i].text || p.level !== next[i].level)
    ) {
      this.headings.set(next);
      this.headingsChange.emit(next);
    }
  }
}
