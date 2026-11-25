import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/index.js';

const id = '1408140530877042688';
const env = {
  DATA_URL: 'https://data.example',
  SLEEPER_URL: 'https://sleeper.example/v1',
  DEFAULT_LEAGUE_ID: id,
};
const players = [
  { id: '100', sleeper_id: '100', name: 'Starter One', team: 'DET', positions: ['PG'], age: 25,
    rankings: { ros: { consensus: 5 }, dynasty: { consensus: 8 } }, season: { fppg: 50 }, last5: { fppg: 55 }, roster_pct: 95, roster_pct_source: 'Sleeper', projected_fppg: 53, trending: { '24': { adds: 12, drops: 3 } } },
  { id: '200', sleeper_id: '200', name: 'Bench Two', team: 'UTA', positions: ['C'], age: 23,
    rankings: { ros: { consensus: 100 }, dynasty: { consensus: 80 } }, season: { fppg: 22 }, last5: { fppg: 25 } },
  { id: '300', sleeper_id: '300', name: 'Dropped Three', team: 'NYK', positions: ['PF'], age: 24,
    rankings: { ros: { consensus: 200 }, dynasty: { consensus: 150 } }, season: { fppg: 15 }, last5: { fppg: 16 } },
];
const league = { league_id: id, name: 'Deadball Dynasty', season: '2026',
  status: 'in_season', settings: { leg: 5 }, scoring_settings: { pts: 1 },
  roster_positions: ['PG', 'C', 'BN'] };
const rosters = [
  { roster_id: 5, owner_id: 'u1', starters: ['100'], players: ['100','200'],
    reserve: [], taxi: [], settings: { wins: 2, losses: 2, fpts: 1200 } },
  { roster_id: 6, owner_id: 'u2', starters: ['300'], players: ['300'],
    reserve: [], taxi: [], settings: { wins: 3, losses: 1, fpts: 1250 } },
];
const users = [
  { user_id: 'u1', display_name: 'ethanperello', metadata: { team_name: 'ethanperello' } },
  { user_id: 'u2', display_name: 'opponent', metadata: { team_name: 'Opponent Team' } },
];
const matchups = [
  { roster_id: 5, matchup_id: 9, starters: ['100'], players: ['100','200'], points: 120.5 },
  { roster_id: 6, matchup_id: 9, starters: ['300'], players: ['300'], points: 105.5 },
];
const transactions = [
  { transaction_id: 't1', type: 'free_agent', leg: 5, roster_ids: [5],
    status: 'complete', status_updated: 1790000000000, adds: { '200': 5 },
    drops: { '300': 5 }, settings: { waiver_bid: 4 } },
  { transaction_id: 't2', type: 'trade', leg: 5, roster_ids: [6,5],
    status: 'complete', status_updated: 1789000000000, adds: null, drops: null,
    draft_picks: [{ season: '2027', round: 1, owner_id: 5, previous_owner_id: 6 }] },
];

const originalFetch = globalThis.fetch;
globalThis.fetch = async (input) => {
  const u = String(input);
  const path = new URL(u).pathname;
  let value;
  if (path === '/api/players.json') value = { players };
  else if (path === '/api/index.json') value = { description: 'Test', freshness: {}, scoring: {}, glossary: {} };
  else if (path === '/api/league.json') value = { configured: false };
  else if (path === '/v1/league/' + id) value = league;
  else if (path === '/v1/league/' + id + '/rosters') value = rosters;
  else if (path === '/v1/league/' + id + '/users') value = users;
  else if (path === '/v1/state/nba') value = { season: '2026', leg: 5 };
  else if (path === '/v1/league/' + id + '/matchups/5') value = matchups;
  else if (path === '/v1/league/' + id + '/transactions/5') value = transactions;
  else throw Error('Unexpected fetch: ' + u);
  return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
};
after(() => { globalThis.fetch = originalFetch; });

async function callTool(name, args = {}) {
  const req = new Request('https://worker.example/mcp', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } }),
  });
  const res = await worker.fetch(req, env);
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.result.isError, false, JSON.stringify(data.result));
  return JSON.parse(data.result.content[0].text);
}

test('get_league returns live starter/bench roster with default league', async () => {
  const d = await callTool('get_league', { team: '5' });
  assert.equal(d.teams[0].players.find(p => p.name === 'Starter One').slot, 'starter');
  assert.equal(d.teams[0].players.find(p => p.name === 'Bench Two').slot, 'bench');
});
test('get_transactions reports add/drop, waiver bid and draft pick trade', async () => {
  const d = await callTool('get_transactions', { team: 'ethanperello', weeks_back: 1 });
  assert.deepEqual(d.weeks_checked, [5]);
  assert.equal(d.total, 2);
  assert.equal(d.transactions[0].adds[0].name, 'Bench Two');
  assert.equal(d.transactions[0].drops[0].name, 'Dropped Three');
  assert.equal(d.transactions[0].faab_bid, 4);
  assert.equal(d.transactions[1].draft_picks[0].new_owner_name, 'ethanperello');
});
test('get_matchup includes opponent and labels lock status unavailable', async () => {
  const d = await callTool('get_matchup', { team: '5' });
  assert.equal(d.week, 5);
  assert.equal(d.matchups.length, 2);
  const own = d.matchups.find(m => m.roster_id === 5);
  assert.equal(own.starters[0].name, 'Starter One');
  assert.equal(own.starters[0].lineup_position, 'PG');
  assert.equal(own.bench[0].name, 'Bench Two');
  assert.equal(own.public_points, 120.5);
  assert.equal(d.lock_ins.available, false);
});
test('GET JSON API mirrors MCP transactions and matchups', async () => {
  for (const path of ['/api/matchup?team=5', '/api/transactions?team=5&weeks_back=1', '/api/league?team=5']) {
    const res = await worker.fetch(new Request('https://worker.example' + path), env);
    assert.equal(res.status, 200);
    const d = await res.json();
    assert.equal(d.league_id, id);
  }
});
test('rejects invalid matchup weeks', async () => {
  const r = await worker.fetch(new Request('https://worker.example/api/matchup?week=NaN'), env);
  assert.equal(r.status, 400);
  const d = await r.json();
  assert.match(d.error, /week must be/);
});

test('market metrics are returned and sortable', async () => {
  const d = await callTool('search_players', { sort_by: 'projected_fppg' });
  assert.equal(d.players[0].projected_fppg, 53);
  assert.equal(d.players[0].roster_pct, 95);
  assert.equal(d.players[0].trending['24'].adds, 12);
  assert.equal(d.players[1].roster_pct, null);
  const trends = await callTool('search_players', { sort_by: 'trending_adds' });
  assert.equal(trends.players[0].name, 'Starter One');
});
