import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../src/index.js';

const ID = '1408140530877042688';
const base = `https://api.sleeper.app/v1/league/${ID}`;
const fixtures = {
  [base]: { league_id: ID, name: 'Deadball Dynasty', season: '2026',
    settings: { leg: 5 }, roster_positions: ['PG', 'SG', 'BN', 'BN'], scoring_settings: { pts: 1 } },
  [base + '/users']: [
    { user_id: 'u5', display_name: 'ethanperello', metadata: { team_name: 'ethanperello' } },
    { user_id: 'u6', display_name: 'other', metadata: {} },
  ],
  [base + '/rosters']: [
    { roster_id: 5, owner_id: 'u5', players: ['1', '2', '3', '4'],
      starters: ['1', '2'], reserve: ['4'], taxi: [] },
    { roster_id: 6, owner_id: 'u6', players: ['9'], starters: ['9'], reserve: [], taxi: [] },
  ],
  'https://example.github.io/hoops/api/players.json': { players: [
    { id: '1', sleeper_id: '1', name: 'Star One', team: 'BOS', positions: ['PG'] },
    { id: '2', sleeper_id: '2', name: 'Star Two', team: 'NYK', positions: ['SG'] },
    { id: '3', sleeper_id: '3', name: 'Bench Three', team: 'CLE', positions: ['SF'] },
    { id: '4', sleeper_id: '4', name: 'IR Four', team: 'MIA', positions: ['C'] },
    { id: '9', sleeper_id: '9', name: 'Opponent', team: 'LAL', positions: ['PG'] },
  ] },
  [base + '/matchups/5']: [
    { roster_id: 5, matchup_id: 2, players: ['1', '2', '3', '4'], starters: ['1', '2'],
      starters_points: [41.5, 20], players_points: { '1': 41.5, '2': 20, '3': 35 }, points: 61.5 },
    { roster_id: 6, matchup_id: 2, players: ['9'], starters: ['9'], starters_points: [37], points: 37 },
  ],
  [base + '/transactions/5']: [
    { transaction_id: 'move1', leg: 5, type: 'waiver', status: 'complete',
      roster_ids: [5], adds: { '3': 5 }, drops: { '4': 5 }, settings: { waiver_bid: 4 },
      created: 1794900000000 },
    { transaction_id: 'pending1', type: 'waiver', status: 'pending',
      roster_ids: [5], adds: { '9': 5 }, drops: null },
  ],
  [base + '/transactions/4']: [
    { transaction_id: 'trade1', leg: 4, type: 'trade', status: 'complete',
      roster_ids: [5, 6], adds: { '2': 5, '9': 6 }, drops: null,
      draft_picks: [{ season: '2027', round: 1, owner_id: 5, previous_owner_id: 6 }],
      created: 1794300000000 },
  ],
};
globalThis.fetch = async url => {
  const data = fixtures[String(url)];
  return new Response(data === undefined ? 'missing' : JSON.stringify(data),
    { status: data === undefined ? 404 : 200 });
};
const env = { DATA_URL: 'https://example.github.io/hoops', DEFAULT_LEAGUE_ID: ID };
async function tool(name, args = {}) {
  const res = await worker.fetch(new Request('https://worker.test/mcp', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call',
      params: { name, arguments: args } }),
  }), env);
  const d = await res.json();
  assert.equal(d.result.isError, false, d.result.content?.[0]?.text);
  return JSON.parse(d.result.content[0].text);
}

test('get_matchup returns week starters, bench, positions and opponent without inventing locks', async () => {
  const d = await tool('get_matchup', { team: 'ethanperello' });
  assert.equal(d.week, 5);
  assert.equal(d.teams.length, 1);
  const t = d.teams[0];
  assert.equal(t.opponent, 'other');
  assert.deepEqual(t.starting_lineup.map(p => p.name), ['Star One', 'Star Two']);
  assert.deepEqual(t.starting_lineup.map(p => p.lineup_position), ['PG', 'SG']);
  assert.deepEqual(t.bench.map(p => p.name), ['Bench Three', 'IR Four']);
  assert.equal(t.bench[0].reported_points, 35);
  assert.equal(t.starting_lineup[0].reported_points, 41.5);
  assert.equal(t.starting_lineup[0].lock_in_status, 'unknown');
  assert.equal(d.lock_in_data_available, false);
});

test('get_transactions includes real adds, drops, FAAB and traded picks; excludes pending by default', async () => {
  const d = await tool('get_transactions', { team: '5' });
  assert.deepEqual(d.weeks_checked, [5, 4]);
  assert.equal(d.total_found, 2);
  const waiver = d.transactions.find(t => t.transaction_id === 'move1');
  assert.equal(waiver.adds[0].name, 'Bench Three');
  assert.equal(waiver.drops[0].name, 'IR Four');
  assert.equal(waiver.waiver_bid, 4);
  assert.equal(d.transactions.find(t => t.transaction_id === 'trade1').draft_picks[0].season, '2027');
  const all = await tool('get_transactions', { include_pending: true, lookback_weeks: 1 });
  assert.equal(all.total_found, 2);
});

test('public JSON route exposes the same weekly matchup data', async () => {
  const res = await worker.fetch(new Request('https://worker.test/api/matchup?team=5'), env);
  assert.equal(res.status, 200);
  const d = await res.json();
  assert.equal(d.teams[0].starting_lineup[0].name, 'Star One');
});
