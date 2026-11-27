import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker from './src/index.js';

test('market fields are present in search, ownership null is not replaced, and synthetic FPPG is absent', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    players: [{ id: '4777', name: 'Ryan Kalkbrenner', positions: ['C'],
      roster_pct: null, roster_pct_source: null, espn_roster_pct: 1.42,
      trending: { '24': { adds: 2, drops: null } }, projected_fppg: 16.9,
      season: { fppg: 19.1 }, last5: { fppg: 15.5 } }]
  }), {headers: {'Content-Type': 'application/json'}});
  try {
    const rpc = async (method, params) => {
      const req = new Request('https://example.test/mcp', { method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
      return (await (await worker.fetch(req, {DATA_URL: 'https://data.example.test'})).json()).result;
    };
    const tools = (await rpc('tools/list')).tools;
    const search = tools.find(t => t.name === 'search_players');
    assert.ok(search.inputSchema.properties.sort_by.enum.includes('espn_roster_pct'));
    assert.ok(!search.inputSchema.properties.sort_by.enum.includes('projected_fppg'));
    const result = await rpc('tools/call', {name: 'search_players', arguments: {query: 'Kalkbrenner'}});
    assert.equal(result.isError, false);
    const p = JSON.parse(result.content[0].text).players[0];
    assert.equal(p.roster_pct, null);
    assert.equal(p.espn_roster_pct, 1.42);
    assert.equal(p.trending['24'].adds, 2);
    assert.equal(p.fppg, 19.1);
    assert.equal(p.last5_fppg, 15.5);
    assert.ok(!Object.hasOwn(p, 'projected_fppg'));
  } finally { globalThis.fetch = originalFetch; }
});
