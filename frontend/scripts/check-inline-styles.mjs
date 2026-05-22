#!/usr/bin/env node
/**
 * Frontend lint gate: ban inline `style="…"` attributes in Angular templates.
 *
 * Rationale (DC-12 / WS-4.6): pages bypassed the design-system primitives with
 * thousands of one-off inline styles. The remediation pass moved every static
 * declaration into per-component styles[] / utility classes, and this gate
 * stops new inline styles from sneaking back in.
 *
 * Allowed: Angular property bindings `[style.x]="…"` for runtime-driven values
 * (e.g. `[style.width.%]="bt.progress_pct"`), because those legitimately need
 * to depend on component state.
 *
 * Banned: the static-string form `style="…"` inside @Component templates.
 *
 * Run with: pnpm check:inline-styles
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(fileURLToPath(import.meta.url), '..', '..', 'src');

const TEMPLATE_FILES = /\.(ts|html)$/;
const STYLE_ATTR = /\bstyle="[^"]*"/g;

function walk(dir, out) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      if (name === 'node_modules' || name.startsWith('.')) continue;
      walk(path, out);
    } else if (TEMPLATE_FILES.test(name) && !name.endsWith('.spec.ts')) {
      out.push(path);
    }
  }
}

function check(path) {
  const src = readFileSync(path, 'utf8');
  const matches = [...src.matchAll(STYLE_ATTR)];
  if (matches.length === 0) return [];
  // For .ts files, only flag occurrences inside backtick template literals
  // (skip styles in JS strings, e.g. inside computed signals or test stubs).
  if (path.endsWith('.ts')) {
    return matches.filter((m) => {
      const before = src.slice(0, m.index);
      const openTicks = (before.match(/`/g) || []).length;
      // Inside a template literal if an odd number of backticks precede the match.
      return openTicks % 2 === 1;
    });
  }
  return matches;
}

const files = [];
walk(root, files);

let total = 0;
const offenders = [];
for (const f of files) {
  const hits = check(f);
  if (hits.length > 0) {
    total += hits.length;
    offenders.push({ file: f, count: hits.length });
  }
}

if (total > 0) {
  console.error(
    `❌ Inline-style attributes found in ${offenders.length} file(s) (${total} total).\n`,
  );
  for (const o of offenders.sort((a, b) => b.count - a.count)) {
    const rel = o.file.replace(root, 'src');
    console.error(`  ${rel} — ${o.count}`);
  }
  console.error(
    `\nUse per-component styles[] or a utility class. Use [style.x]="value" only for runtime-driven values.`,
  );
  process.exit(1);
}

console.log(`✅ No inline style="…" attributes found in ${files.length} files.`);
