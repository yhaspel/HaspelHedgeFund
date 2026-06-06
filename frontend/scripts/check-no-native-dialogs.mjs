#!/usr/bin/env node
/**
 * Frontend lint gate: ban native confirm() / alert() / prompt() dialogs.
 *
 * Rationale (HHF-05): native dialogs are unthemed, not keyboard/focus managed,
 * not screen-reader consistent, and bypass the design system. All call sites
 * were migrated to ConfirmService (ask/notify/askText, rendered via hf-modal);
 * this gate stops new native dialogs from sneaking back in.
 *
 * The ConfirmService methods are intentionally named ask/notify/askText (not
 * confirm/alert/prompt), so `this.confirm.ask(...)` etc. never match — only the
 * native globals (`confirm(`, `window.alert(`, `prompt(`, …) do. Comments that
 * contain the literal `confirm(`/`alert(`/`prompt(` will also trip it; reword
 * them (e.g. "native confirm dialog").
 *
 * Run with: pnpm check:no-native-dialogs
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(fileURLToPath(import.meta.url), '..', '..', 'src', 'app');

// Matches a bare or window-prefixed call to one of the native dialog globals.
// `\b` before the name means `confirmClose(` / `this.confirm.ask(` do NOT match
// (no `confirm` immediately followed by `(` there).
const NATIVE = /\b(?:window\.)?(confirm|alert|prompt)\s*\(/g;

function walk(dir, out) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      if (name === 'node_modules' || name.startsWith('.')) continue;
      walk(path, out);
    } else if (name.endsWith('.ts') && !name.endsWith('.spec.ts')) {
      out.push(path);
    }
  }
}

const files = [];
walk(root, files);

const offenders = [];
for (const f of files) {
  const rel = f.replace(root, 'src/app');
  readFileSync(f, 'utf8')
    .split('\n')
    .forEach((line, i) => {
      for (const m of line.matchAll(NATIVE)) {
        offenders.push({ file: rel, line: i + 1, hit: m[0].trim() });
      }
    });
}

if (offenders.length > 0) {
  console.error(`❌ Native dialog calls found (${offenders.length}).\n`);
  for (const o of offenders) {
    console.error(`  ${o.file}:${o.line} — ${o.hit}`);
  }
  console.error(`\nUse ConfirmService instead: this.confirm.ask() / .notify() / .askText() (rendered via hf-modal).`);
  process.exit(1);
}

console.log(`✅ No native confirm/alert/prompt dialogs found in ${files.length} files.`);
