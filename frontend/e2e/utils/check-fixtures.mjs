#!/usr/bin/env node
/**
 * e2e:routes — fail CI when a frontend `/api` endpoint has no mock registry
 * entry (and would therefore 599 under Lane A). Greps the Angular source for
 * ApiClient calls, normalises dynamic segments, and diffs against the specs
 * registered in `fixtures/api-mock.ts`.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = join(__dirname, '..', '..', 'src', 'app');
const MOCK = join(__dirname, '..', 'fixtures', 'api-mock.ts');

/** Replace `:param` and `${expr}` with a `*` wildcard; drop the query string. */
function normalize(p) {
  return p
    .replace(/\$\{environment\.apiBaseUrl\}/g, '')
    .replace(/\$\{[^}]*\}/g, '*')
    .replace(/:[A-Za-z0-9_]+/g, '*')
    .split('?')[0]
    .replace(/\/+$/, '/');
}

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    // Skip the generic ApiClient wrapper (its `${this.base}${path}` calls are
    // not real endpoints — they normalise to a useless `**`).
    else if (/\.ts$/.test(name) && !/\.spec\.ts$/.test(name) && name !== 'api-client.ts')
      out.push(full);
  }
  return out;
}

// 1) Registered specs from the mock registry.
const mockSrc = readFileSync(MOCK, 'utf-8');
const registered = new Set();
for (const m of mockSrc.matchAll(/\{\s*method:\s*'(\w+)',\s*spec:\s*'([^']+)'/g)) {
  registered.add(`${m[1]} ${normalize(m[2])}`);
}

// 2) ApiClient calls in the source. Matches `.get<...>('…')` / `.post<…>(`…`)` etc.
const callRe = /\.(get|post|put|patch|delete)\s*<[^>]*>\s*\(\s*([`'])([^`']*)\2/g;
const used = new Map(); // "METHOD path" -> example file
for (const file of walk(SRC)) {
  const text = readFileSync(file, 'utf-8');
  for (const m of text.matchAll(callRe)) {
    const method = m[1].toUpperCase();
    const path = normalize(m[3]);
    if (!path.startsWith('/') && !path.startsWith('*')) continue;
    used.set(`${method} ${path}`, file.replace(SRC, 'src/app'));
  }
}

/** A used route is covered if some registered spec matches segment-wise. */
function matches(used, reg) {
  const [um, up] = used.split(' ');
  const [rm, rp] = reg.split(' ');
  if (um !== rm) return false;
  const a = up.split('/');
  const b = rp.split('/');
  if (a.length !== b.length) return false;
  return a.every((seg, i) => seg === b[i] || seg === '*' || b[i] === '*');
}

const missing = [];
for (const [route, file] of used) {
  if (![...registered].some((reg) => matches(route, reg))) missing.push({ route, file });
}

if (missing.length) {
  console.error(`\n✗ ${missing.length} /api endpoint(s) have no mock registry entry:\n`);
  for (const { route, file } of missing) console.error(`  ${route}   (${file})`);
  console.error('\nAdd a route to e2e/fixtures/api-mock.ts (with a fixture if it returns data).\n');
  process.exit(1);
}
console.log(`✓ all ${used.size} source /api endpoints are covered by the mock registry`);
