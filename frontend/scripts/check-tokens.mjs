#!/usr/bin/env node
/**
 * Frontend lint gate: ban references to phantom design tokens.
 *
 * Rationale (HHF-04 / HHF-08 / HHF-11): several pages referenced CSS custom
 * properties that `tokens.css` never defines — `--text-1`, `--surface-1`,
 * `--accent`, `--pos`, `--neg` (and the `--accent-soft` / `--accent-fg`
 * variants). An undefined `var(--x)` silently falls through to `unset`/inherit
 * (or a hardcoded fallback), so these produced live rendering bugs: no link
 * colour, no tab underline, off-palette P&L greens/reds. The remediation pass
 * mapped each to its real token (see DESIGN_REFERENCE §6); this gate stops the
 * phantoms from sneaking back in.
 *
 * The denylist is the five phantom families. `--accent` also matches its
 * `--accent-soft` / `--accent-fg` variants, but a trailing boundary keeps it
 * from flagging some hypothetical future real token like `--accentuate`.
 *
 * For `.ts` files only matches inside backtick template literals are flagged
 * (mirrors check-inline-styles.mjs) — Angular component styles always live in
 * `styles: [`…`]`, so this skips false positives from token names appearing in
 * comments or plain string literals. `.css`/`.html` files are scanned whole.
 *
 * Run with: pnpm check:tokens
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(fileURLToPath(import.meta.url), '..', '..', 'src');

const SCAN_FILES = /\.(ts|css|html)$/;

// Each entry: a phantom token and the real token it should have been. All
// patterns are global so every occurrence on a line is reported.
const DENYLIST = [
  // The five phantom families that caused live rendering bugs.
  { pattern: /--text-1(?![a-z0-9-])/g, use: '--text (or --acc-info-fg for links)' },
  { pattern: /--surface-1(?![a-z0-9-])/g, use: '--surface' },
  { pattern: /--accent(?:-soft|-fg)?(?![a-z0-9-])/g, use: '--acc-info / --acc-info-soft / --acc-info-fg' },
  { pattern: /--pos(?![a-z0-9-])/g, use: '--acc-long-fg' },
  { pattern: /--neg(?![a-z0-9-])/g, use: '--acc-short-fg' },
  // Additional undefined tokens found during remediation (some had hardcoded
  // fallbacks, masking the drift) — guarded so they can't return.
  { pattern: /--border-1(?![a-z0-9-])/g, use: '--border-2 / --border-strong' },
  { pattern: /--r-3(?![a-z0-9-])/g, use: '--r-4 (radius scale: 2/4/6/8/12/full)' },
  { pattern: /--focus-ring-color(?![a-z0-9-])/g, use: '--acc-info (or the --focus-ring token)' },
];

function walk(dir, out) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      if (name === 'node_modules' || name.startsWith('.')) continue;
      walk(path, out);
    } else if (SCAN_FILES.test(name) && !name.endsWith('.spec.ts')) {
      out.push(path);
    }
  }
}

// True if the absolute index sits inside a backtick template literal (odd
// number of backticks precede it). Used to scope .ts matches to styles[].
function inTemplateLiteral(src, index) {
  let ticks = 0;
  for (let i = 0; i < index; i++) if (src[i] === '`') ticks++;
  return ticks % 2 === 1;
}

const files = [];
walk(root, files);

const offenders = [];
for (const f of files) {
  const src = readFileSync(f, 'utf8');
  const isTs = f.endsWith('.ts');
  const rel = f.replace(root, 'src');
  let offset = 0;
  for (const [i, line] of src.split('\n').entries()) {
    for (const { pattern, use } of DENYLIST) {
      for (const m of line.matchAll(pattern)) {
        if (isTs && !inTemplateLiteral(src, offset + m.index)) continue;
        offenders.push({ file: rel, line: i + 1, token: m[0], use });
      }
    }
    offset += line.length + 1; // +1 for the stripped newline
  }
}

if (offenders.length > 0) {
  console.error(`❌ Phantom design tokens found (${offenders.length}).\n`);
  for (const o of offenders) {
    console.error(`  ${o.file}:${o.line} — ${o.token}  →  use ${o.use}`);
  }
  console.error(`\nThese tokens are undefined in tokens.css. Map each to its real token (see DESIGN_REFERENCE §6).`);
  process.exit(1);
}

console.log(`✅ No phantom tokens found in ${files.length} files.`);
