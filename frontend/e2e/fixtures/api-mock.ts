/**
 * Lane A network backbone. Installs a single `page.route('**\/api/**')` that
 * resolves every request to a versioned fixture by `method + path`. Unmatched
 * routes FAIL LOUDLY (599 + console error) so a missing fixture is a visible
 * test failure, never a silent hang (guards §3.5 / S-04).
 *
 * Per-test overrides drive edge cases:
 *   apiMock.override('GET', '/runs/', { json: [] })       // empty state
 *   apiMock.override('GET', '/macro/snapshot/', { status: 500 })  // error state
 *   apiMock.override('GET', '/runs/', { json: [...], delayMs: 800 })  // skeleton
 */
import type { Page, Route } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';

const DATA_DIR = path.join(__dirname, 'data');

/** Load a versioned JSON fixture by bare name (no extension). */
export function fixture<T = unknown>(name: string): T {
  return JSON.parse(fs.readFileSync(path.join(DATA_DIR, `${name}.json`), 'utf-8')) as T;
}

export interface MockResponse {
  status?: number;
  json?: unknown;
  body?: string;
  contentType?: string;
  delayMs?: number;
}

interface MatchCtx {
  params: Record<string, string>;
  url: URL;
  method: string;
  body: unknown;
}
type Responder = (ctx: MatchCtx) => MockResponse | Promise<MockResponse>;

interface RouteDef {
  method: string;
  spec: string;
  re: RegExp;
  names: string[];
  specificity: number;
  respond: Responder;
}

/** Compile an express-style spec (`/runs/:id/`) to an anchored pathname regex. */
function compile(spec: string): { re: RegExp; names: string[]; specificity: number } {
  const names: string[] = [];
  let literals = 0;
  const segs = spec.split('/').map((seg) => {
    if (seg.startsWith(':')) {
      names.push(seg.slice(1));
      return '([^/]+)';
    }
    if (seg) literals += 1;
    return seg.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  });
  return { re: new RegExp('^' + segs.join('/') + '$'), names, specificity: literals };
}

/** Strip the `…/api` prefix from a full URL pathname, keeping the leading slash. */
function apiPath(rawUrl: string): { path: string; url: URL } {
  const url = new URL(rawUrl);
  const i = url.pathname.indexOf('/api/');
  const p = i >= 0 ? url.pathname.slice(i + 4) : url.pathname;
  return { path: p, url };
}

const json = (data: unknown, status = 200): MockResponse => ({ status, json: data });

/**
 * The route table. Resolution is specificity-first (more literal segments win),
 * so `/macro/regime/batch/` beats `/macro/regime/:ticker/` regardless of order.
 */
const REGISTRY: Array<{ method: string; spec: string; respond: Responder }> = [
  // ---- auth (interceptor skips bearer/refresh on these) -------------------
  { method: 'POST', spec: '/auth/login/', respond: () => json({ access: 'e2e.access.token', refresh: 'e2e.refresh.token' }) },
  { method: 'POST', spec: '/auth/refresh/', respond: () => json({ access: 'e2e.access.token.v2', refresh: 'e2e.refresh.token.v2' }) },
  { method: 'POST', spec: '/auth/signup/', respond: ({ body }) => json({ id: 2, email: (body as { email?: string })?.email ?? 'new@example.com', date_joined: '2026-06-06T00:00:00Z' }, 201) },
  { method: 'GET', spec: '/me/', respond: () => json(fixture('me')) },
  { method: 'GET', spec: '/me/model-preferences/', respond: () => json(fixture('model-preferences')) },
  { method: 'PUT', spec: '/me/model-preferences/', respond: ({ body }) => json({ ...(fixture('model-preferences') as object), ...(body as object) }) },
  { method: 'GET', spec: '/me/provider-keys/', respond: () => json(fixture('provider-keys')) },
  { method: 'PUT', spec: '/me/provider-keys/', respond: () => json(fixture('provider-keys')) },

  // ---- fund + autopilot ----------------------------------------------------
  { method: 'GET', spec: '/fund/', respond: () => json(fixture('fund')) },
  // P10 §C2/§B5: NAV history + validated composite. Cold-start shapes (the
  // graceful "accrues from the sweep / run a validation backtest" states) so
  // the fund-first landing page renders without chart fixtures.
  { method: 'GET', spec: '/fund/history/', respond: () => json({ available: false, reason: 'no snapshots yet — history accrues from the hourly sweep, or run backfill_portfolio_history', per_account: [], aggregate: { points: [], twr_pct: null }, benchmarks: [] }) },
  { method: 'GET', spec: '/fund/composite/', respond: () => json({ available: false, reason: 'no member strategy has a total-return-era validation backtest', missing: [] }) },
  { method: 'POST', spec: '/fund/halt/', respond: () => json({ ...(fixture('fund') as object), state: 'halted', is_live: false }) },
  { method: 'POST', spec: '/fund/resume/', respond: () => json({ ...(fixture('fund') as object), state: 'active', is_live: true }) },
  { method: 'GET', spec: '/strategies/:id/autopilot/', respond: () => json(fixture('autopilot')) },
  { method: 'PUT', spec: '/strategies/:id/autopilot/', respond: ({ body }) => json({ autopilot: { ...((fixture('autopilot') as { autopilot: object }).autopilot), ...(body as object) } }) },
  { method: 'POST', spec: '/strategies/:id/autopilot/enable/', respond: () => json(autopilotWith({ is_enabled: true, state: 'active' })) },
  { method: 'POST', spec: '/strategies/:id/autopilot/disable/', respond: () => json(autopilotWith({ is_enabled: false, state: 'paused' })) },
  { method: 'POST', spec: '/strategies/:id/autopilot/resume/', respond: () => json(autopilotWith({ is_enabled: true, state: 'active' })) },
  { method: 'POST', spec: '/strategies/:id/autopilot/run-now/', respond: () => json({ status: 'queued', cycle_id: 9001 }, 202) },
  { method: 'GET', spec: '/strategies/:id/autopilot/history/', respond: () => json(fixture('autopilot-history')) },
  { method: 'GET', spec: '/strategies/:id/executed/', respond: () => json(fixture('executed-book')) },

  // ---- runs ----------------------------------------------------------------
  { method: 'GET', spec: '/runs/', respond: () => json(fixture('runs-list')) },
  { method: 'POST', spec: '/runs/', respond: () => json({ ...(fixture('run-detail') as object), id: 9100, status: 'queued' }, 201) },
  { method: 'GET', spec: '/runs/:id/', respond: ({ params }) => json({ ...(fixture('run-detail') as object), id: Number(params['id']) || 371 }) },
  { method: 'POST', spec: '/runs/:id/cancel/', respond: ({ params }) => json({ id: Number(params['id']) || 371, status: 'cancelled' }) },
  { method: 'POST', spec: '/runs/:id/rerun/', respond: () => json({ ...(fixture('run-detail') as object), id: 9101, status: 'queued' }, 201) },

  // ---- backtests -----------------------------------------------------------
  { method: 'GET', spec: '/backtests/', respond: () => json(fixture('backtests-list')) },
  { method: 'POST', spec: '/backtests/', respond: () => json({ ...(fixture('backtest-detail') as object), id: 9200, status: 'queued', progress_pct: 0 }, 201) },
  { method: 'POST', spec: '/backtests/estimate/', respond: () => json({ est_cost_usd: '0.42', est_runtime_minutes: 6, n_candidates: 50, windows: 6 }) },
  { method: 'POST', spec: '/backtests/compare/', respond: () => json({ a: fixture('backtest-detail'), b: { ...(fixture('backtest-detail') as object), id: 25, name: '[seed] Walk-forward validation (compare)' } }) },
  { method: 'GET', spec: '/backtests/default-universe/', respond: () => json({ universe: ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA'] }) },
  { method: 'GET', spec: '/backtests/:id/', respond: ({ params }) => json({ ...(fixture('backtest-detail') as object), id: Number(params['id']) || 24 }) },
  { method: 'GET', spec: '/backtests/:id/equity-curve/', respond: () => json(fixture('backtest-equity')) },
  { method: 'GET', spec: '/backtests/:id/deflation/', respond: () => json({ deflated_sharpe: 0.61, prob_overfit: 0.18, n_trials: 50, available: true }) },
  { method: 'POST', spec: '/backtests/:id/cancel/', respond: ({ params }) => json({ id: Number(params['id']) || 24, status: 'cancelled' }) },
  // P10 §D4: soft archive/unarchive.
  { method: 'POST', spec: '/backtests/:id/archive/', respond: ({ params, body }) => json({ id: Number(params['id']) || 24, archived_at: (body as { archived?: boolean })?.archived === false ? null : '2026-06-06T00:00:00Z' }) },
  { method: 'DELETE', spec: '/backtests/:id/', respond: () => ({ status: 204 }) },

  // ---- strategies ----------------------------------------------------------
  { method: 'GET', spec: '/strategies/', respond: () => json(fixture('strategies-list')) },
  { method: 'POST', spec: '/strategies/', respond: () => json({ ...(fixture('strategy-detail') as object), id: 9300 }, 201) },
  { method: 'GET', spec: '/strategies/:id/', respond: ({ params }) => json({ ...(fixture('strategy-detail') as object), id: Number(params['id']) || 48 }) },
  { method: 'PUT', spec: '/strategies/:id/', respond: ({ params, body }) => json({ ...(fixture('strategy-detail') as object), id: Number(params['id']) || 48, ...(body as object) }) },
  { method: 'PATCH', spec: '/strategies/:id/', respond: ({ params, body }) => json({ ...(fixture('strategy-detail') as object), id: Number(params['id']) || 48, ...(body as object) }) },
  { method: 'DELETE', spec: '/strategies/:id/', respond: () => ({ status: 204 }) },
  { method: 'GET', spec: '/strategies/:id/cycles/', respond: () => json(fixture('strategy-cycles')) },
  { method: 'GET', spec: '/strategies/:id/cycles/:cycleId/', respond: ({ params }) => json({ ...(fixture('cycle-detail') as object), id: Number(params['cycleId']) || 109, status: 'done', enrolled_at: null }) },
  { method: 'POST', spec: '/strategies/:id/cycles/:cycleId/approve-council/', respond: () => json(fixture('cycle-detail')) },
  { method: 'POST', spec: '/strategies/:id/cycles/:cycleId/reject/', respond: () => json({ ...(fixture('cycle-detail') as object), status: 'rejected' }) },
  { method: 'POST', spec: '/strategies/:id/cycles/:cycleId/rerun/', respond: () => json({ status: 'queued', cycle_id: 9302 }, 202) },
  { method: 'POST', spec: '/strategies/:id/cycles/:cycleId/refresh-mark/', respond: () => json(fixture('cycle-detail')) },
  { method: 'GET', spec: '/strategies/:id/estimate/', respond: () => json(fixture('cycle-estimate')) },
  { method: 'POST', spec: '/strategies/:id/estimate/', respond: () => json(fixture('cycle-estimate')) },
  { method: 'POST', spec: '/strategies/:id/run-now/', respond: () => json({ status: 'queued', cycle_id: 9301 }, 202) },
  { method: 'GET', spec: '/strategies/:id/enroll/:targetId/', respond: ({ params }) => json({ ...(fixture('enrollment') as object), target_id: Number(params['targetId']) || 109 }) },
  { method: 'POST', spec: '/strategies/:id/enroll/:targetId/', respond: ({ params }) => json({ ok: true, enrolled: true, target_portfolio_id: Number(params['targetId']) || 1 }) },
  { method: 'GET', spec: '/strategies/:id/backtest-defaults/', respond: () => json({ universe: ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA'], start_date: '2024-06-06', end_date: '2026-06-05', rebalance_frequency: 'weekly' }) },
  // P10 §E4: news-lab name-level decision scoreboard (cold-start shape).
  { method: 'GET', spec: '/strategies/:id/news-decisions/', respond: ({ params }) => json({ strategy_id: Number(params['id']) || 56, n_cycles: 0, n_graded_decisions: 0, hit_rate: null, mean_excess_pct: null, sleeve_minus_ew_mean_pct: null, n_sleeve_vs_ew_cycles: 0, cycles: [] }) },

  // ---- graphs --------------------------------------------------------------
  { method: 'GET', spec: '/graphs/', respond: () => json(fixture('graphs-list')) },
  { method: 'POST', spec: '/graphs/', respond: ({ body }) => json({ ...(fixture('graph-detail') as object), id: 9400, name: (body as { name?: string })?.name ?? 'New graph' }, 201) },
  { method: 'GET', spec: '/graphs/registry/', respond: () => json(fixture('graphs-registry')) },
  { method: 'POST', spec: '/graphs/validate/', respond: () => json({ ok: true, valid: true, warnings: [], errors: [] }) },
  { method: 'POST', spec: '/graphs/from-template/:templateId/', respond: () => json({ ...(fixture('graph-detail') as object), id: 9401, owned: true, is_template: false }, 201) },
  { method: 'GET', spec: '/graphs/:id/', respond: ({ params }) => json({ ...(fixture('graph-detail') as object), id: Number(params['id']) || 7 }) },
  { method: 'PATCH', spec: '/graphs/:id/', respond: ({ params, body }) => json({ ...(fixture('graph-detail') as object), id: Number(params['id']) || 7, ...(body as object) }) },
  { method: 'DELETE', spec: '/graphs/:id/', respond: () => ({ status: 204 }) },
  { method: 'GET', spec: '/graphs/:id/versions/', respond: () => json(fixture('graph-versions')) },
  { method: 'POST', spec: '/graphs/:id/versions/', respond: () => json({ version: 'v3', created_at: '2026-06-06T00:00:00Z', spec: fixture('graph-detail') }, 201) },
  { method: 'GET', spec: '/graphs/:id/versions/:v/', respond: () => json({ version: 'v1', spec: fixture('graph-detail') }) },

  // ---- portfolio -----------------------------------------------------------
  { method: 'GET', spec: '/portfolios/', respond: () => json(fixture('portfolios')) },
  { method: 'POST', spec: '/portfolios/', respond: ({ body }) => json({ id: 9500, name: (body as { name?: string })?.name ?? 'New book', kind: 'manual', cash_balance: '0.00', created_at: '2026-06-06T00:00:00Z' }, 201) },
  { method: 'GET', spec: '/portfolios/hub/', respond: () => json(fixture('portfolios-hub')) },
  { method: 'GET', spec: '/portfolios/:id/positions/', respond: () => json(fixture('portfolio-positions')) },
  { method: 'GET', spec: '/portfolio/', respond: () => json(fixture('portfolio')) },
  { method: 'GET', spec: '/portfolio/ledger/', respond: () => json(fixture('portfolio-ledger')) },
  { method: 'GET', spec: '/portfolio/preferences/', respond: () => json(fixture('portfolio-preferences')) },
  { method: 'PUT', spec: '/portfolio/preferences/', respond: ({ body }) => json({ ...(fixture('portfolio-preferences') as object), ...(body as object) }) },
  { method: 'POST', spec: '/portfolio/positions/', respond: () => json({ ok: true, portfolio: fixture('portfolio') }, 201) },
  { method: 'PATCH', spec: '/portfolio/positions/:id/', respond: () => json({ ok: true, portfolio: fixture('portfolio') }) },
  { method: 'POST', spec: '/portfolio/cash/', respond: () => json({ ok: true, portfolio: fixture('portfolio') }) },
  { method: 'POST', spec: '/portfolio/refresh-marks/', respond: () => json(fixture('portfolio')) },
  { method: 'POST', spec: '/portfolio/positions/:id/close/', respond: () => json({ ok: true, portfolio: fixture('portfolio') }) },
  { method: 'GET', spec: '/portfolio/position-suggestion/', respond: () => json({ suggestion: { quantity: '10', est_notional: '4530.10' } }) },

  // ---- screener ------------------------------------------------------------
  { method: 'GET', spec: '/screener/presets/', respond: () => json(fixture('screener-presets')) },
  { method: 'GET', spec: '/screener/saved/', respond: () => json(fixture('screener-saved')) },
  { method: 'GET', spec: '/screener/fields/', respond: () => json(fixture('screener-fields')) },
  { method: 'POST', spec: '/screener/run/', respond: () => json(fixture('screener-run')) },
  { method: 'POST', spec: '/screener/saved/', respond: ({ body }) => json({ id: 9600, created_at: '2026-06-06T00:00:00Z', updated_at: '2026-06-06T00:00:00Z', ...(body as object) }, 201) },
  { method: 'DELETE', spec: '/screener/saved/:id/', respond: () => ({ status: 204 }) },

  // ---- news ----------------------------------------------------------------
  { method: 'GET', spec: '/news/feed/', respond: () => json(fixture('news-feed')) },
  { method: 'GET', spec: '/news/preferences/', respond: () => json(fixture('news-preferences')) },
  { method: 'PUT', spec: '/news/preferences/', respond: ({ body }) => json({ ...(fixture('news-preferences') as object), preferences: { ...((fixture('news-preferences') as { preferences?: object }).preferences ?? {}), ...(body as object) } }) },

  // ---- macro / regime ------------------------------------------------------
  { method: 'GET', spec: '/macro/snapshot/', respond: () => json(fixture('macro-snapshot')) },
  { method: 'GET', spec: '/macro/regime/batch/', respond: () => json({ snapshots: {}, as_of: '2026-06-06' }) },
  { method: 'GET', spec: '/macro/regime/:ticker/', respond: () => json(fixture('macro-regime')) },
  { method: 'GET', spec: '/macro/regime/:ticker/history/', respond: () => json({ ticker: 'SPY', history: [] }) },

  // ---- profile / questionnaire --------------------------------------------
  { method: 'GET', spec: '/profile/', respond: () => json(fixture('profile')) },
  { method: 'GET', spec: '/profile/questionnaire/schema/', respond: () => json(fixture('questionnaire-schema')) },
  { method: 'POST', spec: '/profile/questionnaire/', respond: () => json({ id: 9700, status: 'derived', profile: fixture('profile') }, 201) },
  { method: 'GET', spec: '/profile/questionnaire/:id/', respond: ({ params }) => json({ id: Number(params['id']) || 1, status: 'derived', answers: {}, profile: fixture('profile') }) },
  { method: 'POST', spec: '/profile/questionnaire/:id/tune/', respond: () => json({ ok: true, profile: fixture('profile') }) },
  { method: 'PATCH', spec: '/profile/state/', respond: ({ body }) => json({ ok: true, ...(body as object) }) },

  // ---- watchlist -----------------------------------------------------------
  { method: 'GET', spec: '/watchlists/', respond: () => json(fixture('watchlists-all')) },
  { method: 'POST', spec: '/watchlists/', respond: ({ body }) => json({ id: 2, is_default: false, items: [], ...(body as object) }, 201) },
  { method: 'GET', spec: '/watchlists/default/', respond: () => json(fixture('watchlist')) },
  // The /watchlist manager page addresses the default list by its numeric id.
  { method: 'GET', spec: '/watchlists/:id/', respond: () => json(fixture('watchlist')) },
  { method: 'POST', spec: '/watchlists/:id/tickers/', respond: ({ body }) => json({ id: 99, ticker: ((body as { ticker?: string })?.ticker ?? 'NEW').toUpperCase(), note: '', added_at: '2026-06-06T00:00:00Z' }, 201) },
  { method: 'DELETE', spec: '/watchlists/:id/tickers/:sym/', respond: () => ({ status: 204 }) },
  { method: 'POST', spec: '/watchlists/default/tickers/', respond: ({ body }) => json({ id: 99, ticker: ((body as { ticker?: string })?.ticker ?? 'NEW').toUpperCase(), note: '', added_at: '2026-06-06T00:00:00Z' }, 201) },
  { method: 'DELETE', spec: '/watchlists/default/tickers/:sym/', respond: () => ({ status: 204 }) },

  // ---- tickers -------------------------------------------------------------
  { method: 'GET', spec: '/tickers/profiles/', respond: () => json(fixture('ticker-profiles')) },
  { method: 'GET', spec: '/tickers/:sym/profile/', respond: () => json(fixture('ticker-profile')) },
  { method: 'GET', spec: '/tickers/:sym/sparkline/', respond: () => json(fixture('ticker-sparkline')) },
  { method: 'GET', spec: '/tickers/:sym/news/', respond: () => json(fixture('ticker-news')) },

  // ---- broker --------------------------------------------------------------
  { method: 'GET', spec: '/broker-accounts/', respond: () => json(fixture('broker-accounts')) },
  { method: 'POST', spec: '/broker-accounts/', respond: ({ body }) => json({ id: 9800, broker: 'demo', broker_display: 'Demo', mode: 'paper', status: 'active', ...(body as object) }, 201) },
  { method: 'GET', spec: '/broker-accounts/ibkr/runtime-config/', respond: () => json({ gateway_url: 'https://localhost:5000', connected: false }) },
  { method: 'GET', spec: '/broker-accounts/:id/overview/', respond: () => json(fixture('broker-overview')) },
  { method: 'POST', spec: '/broker-accounts/:id/disconnect/', respond: ({ params }) => json({ id: Number(params['id']) || 13, status: 'disconnected' }) },
  { method: 'DELETE', spec: '/broker-accounts/:id/', respond: () => ({ status: 204 }) },
  { method: 'GET', spec: '/brokers/', respond: () => json(fixture('brokers')) },
  { method: 'GET', spec: '/brokers/calendar/', respond: () => json(fixture('brokers-calendar')) },
  { method: 'GET', spec: '/broker/orders/', respond: () => json({ orders: [] }) },
  { method: 'POST', spec: '/broker/orders/', respond: ({ body }) => json({ id: 9810, status: 'pending', ...(body as object) }, 201) },
  { method: 'POST', spec: '/broker/orders/:id/confirm/', respond: ({ params }) => json({ id: Number(params['id']) || 9810, status: 'submitted' }) },
  { method: 'POST', spec: '/broker/orders/:id/cancel/', respond: ({ params }) => json({ id: Number(params['id']) || 9810, status: 'cancelled' }) },
  // broker maintenance + gateway/oauth connect flows (WS-12)
  { method: 'POST', spec: '/broker-accounts/:id/sync/', respond: ({ params }) => json({ id: Number(params['id']) || 13, status: 'active', synced: true }) },
  { method: 'POST', spec: '/broker-accounts/:id/acknowledge-drift/', respond: () => json({ ok: true }) },
  { method: 'PATCH', spec: '/broker-accounts/:id/settings/', respond: ({ body }) => json({ ok: true, ...(body as object) }) },
  { method: 'POST', spec: '/broker-accounts/:id/credentials/', respond: () => json({ ok: true, status: 'active' }) },
  { method: 'POST', spec: '/broker-accounts/:id/oauth/start/', respond: () => json({ authorize_url: 'https://example.com/oauth/authorize?state=e2e' }) },
  { method: 'POST', spec: '/broker-accounts/:id/gateway/probe/', respond: () => json({ reachable: true }) },
  { method: 'POST', spec: '/broker-accounts/:id/gateway/auth-status/', respond: () => json({ authenticated: false, competing_session: false }) },
  { method: 'POST', spec: '/broker-accounts/:id/gateway/discover-accounts/', respond: () => json({ accounts: [] }) },
  { method: 'POST', spec: '/broker-accounts/:id/gateway/activate/', respond: () => json({ ok: true, status: 'active' }) },
  { method: 'POST', spec: '/broker-accounts/:id/tradestation/discover-accounts/', respond: () => json({ accounts: [] }) },
  { method: 'POST', spec: '/broker-accounts/:id/tradestation/activate/', respond: () => json({ ok: true, status: 'active' }) },
  { method: 'GET', spec: '/broker-accounts/tradestation/runtime-config/', respond: () => json({ environment: 'sim', connected: false }) },
  { method: 'GET', spec: '/broker-accounts/tradestation/app-credentials/', respond: () => json({ client_id_set: false, redirect_uri: 'https://example.com/cb' }) },
  { method: 'PUT', spec: '/broker-accounts/tradestation/app-credentials/', respond: () => json({ client_id_set: true }) },

  // ---- models / tiers / disclaimers ---------------------------------------
  { method: 'GET', spec: '/models/', respond: () => json(fixture('models')) },
  { method: 'POST', spec: '/models/fetch/', respond: () => json(fixture('models')) },
  { method: 'POST', spec: '/models/verify-pricing/', respond: () => json({ ok: true, results: [] }) },
  { method: 'GET', spec: '/agents/', respond: () => json(fixture('agents')) },
  { method: 'GET', spec: '/universes/', respond: () => json(fixture('universes')) },
  { method: 'GET', spec: '/tiers/', respond: () => json(fixture('tiers')) },
  { method: 'PUT', spec: '/tiers/:tier/', respond: ({ body }) => json({ ...(body as object) }) },
  { method: 'GET', spec: '/presets/:name/', respond: ({ params }) => json({ name: params['name'], per_role: [] }) },
  { method: 'GET', spec: '/disclaimers/current/', respond: () => json(fixture('disclaimers-current')) },
  { method: 'POST', spec: '/disclaimers/accept/', respond: () => json({ version: 'v1', accepted_at: '2026-06-06T00:00:00Z' }) },

  // ---- persona evolution ---------------------------------------------------
  { method: 'GET', spec: '/persona-evolution/settings/', respond: () => json(fixture('persona-settings')) },
  { method: 'PATCH', spec: '/persona-evolution/settings/', respond: ({ body }) => json({ ...(fixture('persona-settings') as object), ...(body as object) }) },
  { method: 'GET', spec: '/persona-evolution/profiles/', respond: () => json(fixture('persona-profiles')) },
  { method: 'GET', spec: '/persona-evolution/profiles/:id/revisions/', respond: () => json({ revisions: [] }) },
  { method: 'POST', spec: '/persona-evolution/run/', respond: () => json({ ack: true, status: 'queued' }, 202) },

  // ---- leaderboard ---------------------------------------------------------
  { method: 'GET', spec: '/leaderboard/agents/', respond: () => json(fixture('leaderboard-agents')) },
  { method: 'GET', spec: '/leaderboard/models/', respond: () => json(fixture('leaderboard-models')) },
  { method: 'GET', spec: '/leaderboard/strategies/', respond: () => json(fixture('leaderboard-strategies')) },
  { method: 'GET', spec: '/leaderboard/strategies/by-flavor/', respond: () => json(fixture('leaderboard-flavor')) },
  { method: 'GET', spec: '/leaderboard/agents/:agent/decisions/', respond: () => json({ decisions: [] }) },
  { method: 'GET', spec: '/leaderboard/strategies/:s/council-alpha/', respond: () => json({ rows: [] }) },
  { method: 'POST', spec: '/leaderboard/recompute/', respond: () => json({ ok: true, status: 'queued' }, 202) },

  // ---- schedules + notifications ------------------------------------------
  { method: 'GET', spec: '/scheduled-runs/', respond: () => json(fixture('scheduled-runs')) },
  { method: 'POST', spec: '/scheduled-runs/', respond: ({ body }) => json({ id: 9900, enabled: true, ...(body as object) }, 201) },
  { method: 'PATCH', spec: '/scheduled-runs/:id/', respond: ({ params, body }) => json({ id: Number(params['id']) || 1, ...(body as object) }) },
  { method: 'DELETE', spec: '/scheduled-runs/:id/', respond: () => ({ status: 204 }) },
  { method: 'POST', spec: '/scheduled-runs/:id/run-now/', respond: () => json({ status: 'queued' }, 202) },
  { method: 'GET', spec: '/scheduled-runs/:id/history/', respond: () => json({ history: [] }) },
  { method: 'GET', spec: '/notification-channels/', respond: () => json(fixture('notification-channels')) },
  { method: 'POST', spec: '/notification-channels/:id/test/', respond: () => json({ ok: true, sent: true }) },
];

function autopilotWith(patch: Record<string, unknown>): unknown {
  const ap = fixture('autopilot') as { autopilot: object };
  return { autopilot: { ...ap.autopilot, ...patch } };
}

const COMPILED: RouteDef[] = REGISTRY.map((r) => {
  const c = compile(r.spec);
  return { ...r, ...c };
}).sort((a, b) => b.specificity - a.specificity || b.spec.length - a.spec.length);

interface Override {
  method: string;
  re: RegExp;
  names: string[];
  resp: MockResponse;
}

export class ApiMock {
  private readonly overrides: Override[] = [];
  /** Paths that were requested but had no fixture — surfaced by S-04 / assertNoMisses. */
  readonly misses: string[] = [];

  constructor(private readonly page: Page) {}

  async install(): Promise<void> {
    await this.page.route('**/api/**', (route) => this.handle(route));
  }

  /** Override one endpoint for the current test (most-recent override wins). */
  override(method: string, spec: string, resp: MockResponse): void {
    const c = compile(spec);
    this.overrides.unshift({ method: method.toUpperCase(), re: c.re, names: c.names, resp });
  }

  private async handle(route: Route): Promise<void> {
    const req = route.request();
    const method = req.method().toUpperCase();
    const { path: p, url } = apiPath(req.url());
    let body: unknown = undefined;
    if (method !== 'GET' && method !== 'DELETE') {
      try {
        body = req.postDataJSON();
      } catch {
        body = req.postData() ?? undefined;
      }
    }

    // 1) per-test overrides
    for (const o of this.overrides) {
      if (o.method !== method) continue;
      if (o.re.test(p)) return this.fulfill(route, o.resp);
    }

    // 2) registry
    for (const def of COMPILED) {
      if (def.method !== method) continue;
      const m = def.re.exec(p);
      if (!m) continue;
      const params: Record<string, string> = {};
      def.names.forEach((n, i) => (params[n] = decodeURIComponent(m[i + 1] ?? '')));
      const resp = await def.respond({ params, url, method, body });
      return this.fulfill(route, resp);
    }

    // 3) fail loudly
    this.misses.push(`${method} ${p}`);
    // eslint-disable-next-line no-console
    console.error(`[api-mock] UNMATCHED ${method} ${p} — add a fixture/registry entry`);
    await route.fulfill({
      status: 599,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'e2e-unmatched-route', method, path: p }),
    });
  }

  private async fulfill(route: Route, resp: MockResponse): Promise<void> {
    if (resp.delayMs) await new Promise((r) => setTimeout(r, resp.delayMs));
    if (resp.status === 204) return route.fulfill({ status: 204, body: '' });
    if (resp.body !== undefined) {
      return route.fulfill({
        status: resp.status ?? 200,
        contentType: resp.contentType ?? 'text/plain',
        body: resp.body,
      });
    }
    await route.fulfill({
      status: resp.status ?? 200,
      contentType: resp.contentType ?? 'application/json',
      body: JSON.stringify(resp.json ?? {}),
    });
  }
}
