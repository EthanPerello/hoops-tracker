// Hoops Tracker remote MCP server (Cloudflare Worker).
//
// Speaks MCP over Streamable HTTP (stateless, JSON responses) at /mcp, so ChatGPT, Claude.ai,
// Claude Desktop/Code and any other MCP client can connect with just the URL. Data comes from the
// static JSON API the GitHub Pages site publishes (scripts/build_api.py); Sleeper leagues can also
// be read live from api.sleeper.app.
//
// Env: DATA_URL = base URL of the Pages site (set in wrangler.toml).

const PROTOCOLS = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'];
const SLEEPER = 'https://api.sleeper.app/v1';
const CACHE_TTL = 300; // seconds; the site rebuilds ~3x a day
const POSITIONS = ['PG', 'SG', 'SF', 'PF', 'C'];
const LOCK_NOTE = 'Sleeper public matchup data does not document individual NBA Lock-In selections. Public points are not confirmed locked game scores.';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS, DELETE',
  'Access-Control-Allow-Headers': 'Content-Type, Accept, Authorization, Mcp-Session-Id, MCP-Protocol-Version, Last-Event-ID',
  'Access-Control-Expose-Headers': 'Mcp-Session-Id, MCP-Protocol-Version',
};

// ---------------------------------------------------------------- data access
async function getJSON(url, ttl = CACHE_TTL) {
  const r = await fetch(url, { cf: { cacheTtl: ttl, cacheEverything: true }, headers: { 'User-Agent': 'hoops-tracker-mcp' } });
  if (!r.ok) throw new Error(`${url} returned HTTP ${r.status}`);
  return r.json();
}
const site = (env, path) => getJSON(`${env.DATA_URL.replace(/\/$/, '')}/${path}`);

const fold = s => String(s ?? '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[.'’]/g, '');

function row(p) {
  const r = p.rankings || {}, s = p.season || {}, l5 = p.last5 || {};
  return {
    id: p.id, name: p.name, team: p.team, positions: p.positions, age: p.age,
    ros_rank: r.ros?.consensus ?? null, dynasty_rank: r.dynasty?.consensus ?? null,
    fppg: s.fppg ?? null, last5_fppg: l5.fppg ?? null, mpg: s.mpg ?? null, gp: s.gp ?? null,
    injury: p.injury?.status ?? null, fantasy_team: p.fantasy_team?.team_name ?? null,
    roster_pct: p.roster_pct ?? null, roster_pct_source: p.roster_pct_source ?? null,
    sleeper_dynasty_candidate_pct: p.sleeper_dynasty_candidate_pct ?? null,
    espn_roster_pct: p.espn_roster_pct ?? null,
    trending: p.trending ?? null, daily_projections: p.daily_projections ?? [],
  };
}
const SORTS = {
  ros: r => r.ros_rank ?? 9999, dynasty: r => r.dynasty_rank ?? 9999,
  fppg: r => -(r.fppg ?? -999), last5_fppg: r => -(r.last5_fppg ?? -999), mpg: r => -(r.mpg ?? -999),
  roster_pct: r => -(r.roster_pct ?? -Infinity), sleeper_dynasty_candidate_pct: r => -(r.sleeper_dynasty_candidate_pct ?? -Infinity), espn_roster_pct: r => -(r.espn_roster_pct ?? -Infinity),
  trending_adds: r => -(r.trending?.['24']?.adds ?? -Infinity),
  trending_drops: r => -(r.trending?.['24']?.drops ?? -Infinity),
  trending_adds_7d: r => -(r.trending?.['168']?.adds ?? -Infinity),
  trending_drops_7d: r => -(r.trending?.['168']?.drops ?? -Infinity),
};
const clampLimit = (v, d, max = 200) => Math.max(1, Math.min(max, parseInt(v ?? d, 10) || d));

async function findPlayer(env, q) {
  const { players } = await site(env, 'api/players.json');
  const f = fold(q).trim();
  const exact = players.find(p => p.id === String(q) || p.sleeper_id === String(q) || fold(p.name) === f);
  if (exact) return { player: exact };
  const part = players.filter(p => fold(p.name).includes(f));
  return part.length === 1 ? { player: part[0] } : { candidates: part.slice(0, 10).map(p => p.name) };
}

// Live Sleeper league joined with our player data (works for any public Sleeper league).
async function liveLeague(env, leagueId) {
  const id = String(leagueId).match(/(\d{6,})/)?.[1];
  if (!id) throw new Error('league_id should be the number in the Sleeper league URL');
  const base = `${env.SLEEPER_URL || SLEEPER}/league/${id}`; // SLEEPER_URL: test override only
  const [lg, rosters, users, all] = await Promise.all([
    getJSON(base, 60), getJSON(`${base}/rosters`, 60), getJSON(`${base}/users`, 60), site(env, 'api/players.json'),
  ]);
  if (!lg?.league_id) throw new Error(`Sleeper league ${id} not found`);
  const bySid = Object.fromEntries(all.players.filter(p => p.sleeper_id).map(p => [p.sleeper_id, p]));
  const umap = Object.fromEntries((users || []).map(u => [u.user_id, u]));
  const owned = new Set();
  const teams = (rosters || []).map(r => {
    const u = umap[r.owner_id] || {}, st = r.settings || {};
    const slot = pid => (r.starters || []).includes(pid) ? 'starter' : (r.reserve || []).includes(pid) ? 'injured_reserve'
      : (r.taxi || []).includes(pid) ? 'taxi' : 'bench';
    const players = (r.players || []).map(pid => {
      owned.add(pid);
      const p = bySid[pid];
      return p ? { ...row(p), sleeper_id: pid, slot: slot(pid), fantasy_team: undefined } : { sleeper_id: pid, name: null, slot: slot(pid) };
    }).sort((a, b) => (a.ros_rank ?? 9999) - (b.ros_rank ?? 9999));
    return {
      roster_id: r.roster_id, team_name: u.metadata?.team_name || u.display_name || `Team ${r.roster_id}`, owner: u.display_name ?? null,
      record: { wins: st.wins ?? 0, losses: st.losses ?? 0, ties: st.ties ?? 0 },
      points_for: Math.round(((st.fpts ?? 0) + (st.fpts_decimal ?? 0) / 100) * 100) / 100,
      players,
    };
  });
  const best_available = all.players.filter(p => p.sleeper_id && !owned.has(p.sleeper_id) && p.rankings?.ros)
    .slice(0, 60).map(p => ({ ...row(p), fantasy_team: undefined }));
  return {
    configured: true, live: true, league_id: id, name: lg.name, season: lg.season, status: lg.status,
    roster_positions: lg.roster_positions, scoring_settings_raw: lg.scoring_settings,
    current_week_hint: lg.settings?.leg ?? null,
    note: 'Fantasy points (fppg) use the site\'s published scoring, which may differ from this league\'s; scoring_settings_raw is this league\'s.',
    teams, best_available,
  };
}


// Get the current NBA matchup week from Sleeper state instead of guessing by date.
function requestedLeagueId(env, supplied) {
  const id = String(supplied || env.DEFAULT_LEAGUE_ID || '').match(/(\d{6,})/)?.[1];
  if (!id) throw new Error('Pass a Sleeper league_id, or configure DEFAULT_LEAGUE_ID on the Worker.');
  return id;
}
async function matchupWeek(env, lg, supplied) {
  if (supplied !== undefined && supplied !== null && String(supplied) !== '') {
    const n = Number(supplied);
    if (!Number.isInteger(n) || n < 1 || n > 60) throw new Error('week must be an integer between 1 and 60');
    return n;
  }
  try {
    const state = await getJSON((env.SLEEPER_URL || SLEEPER) + '/state/nba', 45);
    if ([state?.season, state?.league_season].some(s => String(s) === String(lg.season))) {
      const n = Number(state?.leg ?? state?.week);
      if (Number.isInteger(n) && n >= 1 && n <= 60) return n;
    }
  } catch (_) { /* fallback to the league's own current leg */ }
  const n = Number(lg.current_week_hint);
  if (Number.isInteger(n) && n >= 1 && n <= 60) return n;
  throw new Error('Could not determine the current NBA matchup week; pass week explicitly.');
}
const matchesTeam = (team, query) =>
  String(team.roster_id) === String(query) || fold(team.team_name).includes(fold(query)) || fold(team.owner).includes(fold(query));

async function liveTransactions(a, env) {
  const lg = await liveLeague(env, requestedLeagueId(env, a.league_id));
  const currentWeek = await matchupWeek(env, lg, a.week);
  const back = a.week == null ? clampLimit(a.weeks_back, 3, 12) : 1;
  const weeks = Array.from({ length: back }, (_, i) => currentWeek - i).filter(n => n >= 1);
  const base = (env.SLEEPER_URL || SLEEPER) + '/league/' + lg.league_id;
  const batches = await Promise.all(weeks.map(w => getJSON(base + '/transactions/' + w, 45)));
  const allPlayers = await site(env, 'api/players.json');
  const nameById = Object.fromEntries(allPlayers.players.filter(p => p.sleeper_id).map(p => [String(p.sleeper_id), p.name]));
  const teamById = Object.fromEntries(lg.teams.map(t => [String(t.roster_id), t]));
  const annotate = (p, rid) => ({
    player_id: String(p), name: nameById[String(p)] ?? null,
    roster_id: Number(rid), team_name: teamById[String(rid)]?.team_name ?? null,
  });
  const rows = weeks.flatMap((week, i) => (Array.isArray(batches[i]) ? batches[i] : []).map(tr => {
    const adds = Object.entries(tr.adds || {}).map(([p, rid]) => annotate(p, rid));
    const drops = Object.entries(tr.drops || {}).map(([p, rid]) => annotate(p, rid));
    const picks = (tr.draft_picks || []).map(p => ({
      ...p, new_owner_name: teamById[String(p.owner_id)]?.team_name ?? null,
      previous_owner_name: teamById[String(p.previous_owner_id)]?.team_name ?? null,
    }));
    const timestamp = Number(tr.status_updated);
    return {
      transaction_id: tr.transaction_id, week: tr.leg ?? week, type: tr.type, status: tr.status,
      updated_at: Number.isFinite(timestamp) && timestamp > 0 ? new Date(timestamp).toISOString() : null,
      roster_ids: tr.roster_ids || [], teams: (tr.roster_ids || []).map(id => teamById[String(id)]?.team_name ?? ('Roster ' + id)),
      adds, drops, draft_picks: picks, faab_bid: tr.settings?.waiver_bid ?? null,
      metadata: tr.metadata ?? null,
    };
  }));
  const filtered = a.team ? rows.filter(tr => {
    const teamIds = lg.teams.filter(t => matchesTeam(t, a.team)).map(t => t.roster_id);
    return teamIds.some(rid => tr.roster_ids.some(x => String(x) === String(rid))
      || [...tr.adds, ...tr.drops].some(p => String(p.roster_id) === String(rid))
      || tr.draft_picks.some(p => String(p.owner_id) === String(rid) || String(p.previous_owner_id) === String(rid)));
  }) : rows;
  filtered.sort((a, b) => (Date.parse(b.updated_at) || 0) - (Date.parse(a.updated_at) || 0));
  const limit = clampLimit(a.limit, 50, 200);
  return {
    league_id: lg.league_id, league: lg.name, weeks_checked: weeks,
    total: filtered.length, showing: Math.min(limit, filtered.length),
    note: 'Sleeper public transactions include free agents, waivers, trades and picks; pending claims may not be exposed.',
    transactions: filtered.slice(0, limit),
  };
}
async function liveMatchup(a, env) {
  const lg = await liveLeague(env, requestedLeagueId(env, a.league_id));
  const week = await matchupWeek(env, lg, a.week);
  const base = (env.SLEEPER_URL || SLEEPER) + '/league/' + lg.league_id;
  const [raw, all] = await Promise.all([
    getJSON(base + '/matchups/' + week, 45), site(env, 'api/players.json'),
  ]);
  if (!Array.isArray(raw)) throw new Error('Sleeper returned no matchups for week ' + week);
  const players = Object.fromEntries(all.players.filter(p => p.sleeper_id).map(p => [String(p.sleeper_id), p]));
  const teamById = Object.fromEntries(lg.teams.map(t => [String(t.roster_id), t]));
  const describe = (id, index = null, roster = null) => {
    const p = players[String(id)], onRoster = roster?.players?.find(x => String(x.sleeper_id) === String(id));
    return {
      player_id: String(id), name: p?.name ?? onRoster?.name ?? null,
      nba_team: p?.team ?? onRoster?.team ?? null,
      lineup_position: index == null ? null : lg.roster_positions?.[index] ?? null,
      current_roster_slot: onRoster?.slot ?? null,
    };
  };
  let rows = raw.map(m => {
    const t = teamById[String(m.roster_id)] ?? null;
    const starterIds = (m.starters || []).filter(id => id && id !== '0');
    const starts = new Set(starterIds.map(String));
    const benchIds = (m.players || []).filter(id => id && !starts.has(String(id)));
    return {
      roster_id: m.roster_id, team_name: t?.team_name ?? null, owner: t?.owner ?? null,
      matchup_id: m.matchup_id ?? null, public_points: m.points ?? null,
      custom_points: m.custom_points ?? null,
      starters: (m.starters || []).map((id, i) => (!id || id === '0' ? null : describe(id, i, t))).filter(Boolean),
      bench: benchIds.map(id => describe(id, null, t)),
      current_ir: (t?.players || []).filter(p => p.slot === 'injured_reserve').map(p => ({ player_id: p.sleeper_id, name: p.name })),
      current_taxi: (t?.players || []).filter(p => p.slot === 'taxi').map(p => ({ player_id: p.sleeper_id, name: p.name })),
    };
  });
  if (a.team) {
    const ids = new Set(rows.filter(r => matchesTeam(r, a.team)).map(r => r.matchup_id));
    rows = rows.filter(r => ids.has(r.matchup_id));
  }
  return {
    league_id: lg.league_id, league: lg.name, week, roster_positions: lg.roster_positions,
    lineups_source: 'Sleeper weekly matchup starters; current_roster_slot is the live roster state',
    lock_ins: { available: false, source: 'public_sleeper_api', note: LOCK_NOTE },
    matchups: rows,
  };
}

// ---------------------------------------------------------------- tools
const str = (description, extra = {}) => ({ type: 'string', description, ...extra });
const TOOLS = {
  get_overview: {
    description: 'Start here. Data freshness, the fantasy scoring used, ranking sources and their status, and the configured Sleeper league\'s teams and records.',
    inputSchema: { type: 'object', properties: {} },
    async run(_, env) {
      const [idx, staticLeague] = await Promise.all([site(env, 'api/index.json'), site(env, 'api/league.json')]);
      let lg = staticLeague;
      if (env.DEFAULT_LEAGUE_ID) {
        try { lg = await liveLeague(env, env.DEFAULT_LEAGUE_ID); } catch (_) { /* static fallback */ }
      }
      return {
        description: idx.description, freshness: idx.freshness, scoring: idx.scoring, glossary: idx.glossary,
        league: lg.configured ? {
          league_id: lg.league_id, name: lg.name, season: lg.season,
          teams: lg.teams.map(t => ({ roster_id: t.roster_id, team_name: t.team_name, owner: t.owner, ...t.record, points_for: t.points_for })),
        } : { configured: false, tip: 'Pass a league_id to get_league to read any Sleeper league live.' },
      };
    },
  },
  search_players: {
    description: 'Find and filter NBA players. Returns compact rows: consensus ROS and dynasty ranks, fantasy points per game (season and last 5), minutes, injury status, Sleeper fantasy team, verified Sleeper dynasty roster percentage if available, explicitly unverified Sleeper dynasty candidate percentage, separately labeled ESPN ownership, 24h/7d add/drop trends and daily provider projections if available.',
    inputSchema: {
      type: 'object', properties: {
        query: str('Part of a player name'),
        position: str('Position', { enum: POSITIONS }),
        team: str('NBA team abbreviation, e.g. LAL'),
        available_only: { type: 'boolean', description: 'Only players not rostered in the configured league' },
        fantasy_team: str('Only players on this fantasy team (name or part of it)'),
        healthy_only: { type: 'boolean', description: 'Exclude players with an injury designation' },
        sort_by: str('Sort order (default ros)', { enum: Object.keys(SORTS) }),
        limit: { type: 'integer', description: 'Default 25, max 200' },
      },
    },
    async run(a, env) {
      let rows = (await site(env, 'api/players.json')).players.map(row);
      if (a.query) { const q = fold(a.query); rows = rows.filter(r => fold(r.name).includes(q)); }
      if (a.position) rows = rows.filter(r => r.positions?.includes(a.position.toUpperCase()));
      if (a.team) rows = rows.filter(r => (r.team || '').toUpperCase() === a.team.toUpperCase());
      if (a.available_only) rows = rows.filter(r => !r.fantasy_team);
      if (a.fantasy_team) { const q = fold(a.fantasy_team); rows = rows.filter(r => r.fantasy_team && fold(r.fantasy_team).includes(q)); }
      if (a.healthy_only) rows = rows.filter(r => !r.injury);
      const key = SORTS[a.sort_by] || SORTS.ros;
      rows.sort((x, y) => key(x) - key(y));
      const limit = clampLimit(a.limit, 25);
      return { count: rows.length, showing: Math.min(limit, rows.length), players: rows.slice(0, limit) };
    },
  },
  get_player: {
    description: 'Everything about one player: rankings by source, season and last-5 averages, injury details and outlook, fantasy team, recent game logs with fantasy points, verified Sleeper dynasty roster percentage if available, explicitly unverified Sleeper dynasty candidate percentage, separate ESPN ownership, trending adds/drops and provider projections when available. No synthetic FPPG.',
    inputSchema: {
      type: 'object', required: ['name_or_id'], properties: {
        name_or_id: str('Player name (accents optional), or id / Sleeper id'),
        games: { type: 'integer', description: 'Recent games to include (default 10, 0 = all)' },
      },
    },
    async run(a, env) {
      const { player, candidates } = await findPlayer(env, a.name_or_id);
      if (!player) return { error: `No single match for "${a.name_or_id}".`, did_you_mean: candidates };
      const d = await site(env, `api/players/${encodeURIComponent(player.id)}.json`);
      const n = parseInt(a.games ?? 10, 10);
      return { ...d, game_logs: n > 0 ? d.game_logs.slice(0, n) : d.game_logs, game_logs_note: `newest first; ${d.game_logs.length} games total` };
    },
  },
  get_rankings: {
    description: 'Consensus dynasty or rest-of-season (ROS) ranking, with each source\'s rank (FantasyPros, Hashtag Basketball), FPPG, injury and fantasy team.',
    inputSchema: {
      type: 'object', properties: {
        type: str('Ranking type (default ros)', { enum: ['ros', 'dynasty'] }),
        position: str('Position', { enum: POSITIONS }),
        available_only: { type: 'boolean', description: 'Only players not rostered in the configured league' },
        limit: { type: 'integer', description: 'Default 50, max 200' },
      },
    },
    async run(a, env) {
      const d = await site(env, `api/rankings/${a.type === 'dynasty' ? 'dynasty' : 'ros'}.json`);
      let rows = d.players;
      if (a.position) rows = rows.filter(r => r.positions?.includes(a.position.toUpperCase()));
      if (a.available_only) rows = rows.filter(r => !r.fantasy_team);
      return { type: d.type, method: d.method, sources: d.sources, players: rows.slice(0, clampLimit(a.limit, 50)) };
    },
  },
  get_league: {
    description: 'Sleeper league: scoring, teams with records and full rosters (ranks, FPPG, lineup slot), and the best available players. Without league_id it returns the site\'s configured league; with league_id it reads that Sleeper league live. Pass team for one roster.',
    inputSchema: {
      type: 'object', properties: {
        league_id: str('Optional Sleeper league id or league URL, read live'),
        team: str('Optional team name, owner name or roster id'),
      },
    },
    async run(a, env) {
      const lg = (a.league_id || env.DEFAULT_LEAGUE_ID) ? await liveLeague(env, a.league_id || env.DEFAULT_LEAGUE_ID) : await site(env, 'api/league.json');
      if (!lg.configured) return { ...lg, tip: 'Pass league_id (the number in the Sleeper league URL) to read a league live.' };
      if (!a.team) return lg;
      const q = fold(a.team);
      const teams = lg.teams.filter(t => String(t.roster_id) === String(a.team) || fold(t.team_name).includes(q) || fold(t.owner).includes(q));
      return teams.length ? { league_id: lg.league_id, name: lg.name, roster_positions: lg.roster_positions, teams } : { error: `No team matches "${a.team}"`, teams: lg.teams.map(t => t.team_name) };
    },
  },

  get_transactions: {
    description: 'Live Sleeper transaction history: add/drop, waiver, free-agent and trade moves (including traded picks and FAAB bids). Defaults to the last 3 matchup weeks in the configured league. Filter by team or week.',
    inputSchema: {
      type: 'object', properties: {
        league_id: str('Sleeper league ID or URL (optional when a default league is configured)'),
        team: str('Filter moves involving this team: roster id, owner or part of team name'),
        week: { type: 'integer', description: 'One exact Sleeper matchup week; omitted = recent weeks' },
        weeks_back: { type: 'integer', description: 'Weeks to scan backward, default 3, max 12; ignored if week given' },
        limit: { type: 'integer', description: 'Default 50, max 200' },
      },
    },
    run: liveTransactions,
  },
  get_matchup: {
    description: 'Live weekly Sleeper matchup and starting lineup, including bench, current IR/taxi players, opponent and public matchup score. Lock-In selections are NOT available from the documented public API; public points are not confirmed locked points.',
    inputSchema: {
      type: 'object', properties: {
        league_id: str('Sleeper league ID or URL (optional when a default league is configured)'),
        team: str('Optional roster id, owner or team name; returns opponent too'),
        week: { type: 'integer', description: 'Sleeper matchup week (defaults to current NBA week)' },
      },
    },
    run: liveMatchup,
  },
  // search + fetch follow OpenAI's connector convention (ChatGPT deep research / company knowledge).
  search: {
    description: 'Search Hoops Tracker for players by name, team or position (e.g. "Jokic", "LAL", "C"). Returns ids for fetch.',
    inputSchema: { type: 'object', required: ['query'], properties: { query: str('Search text') } },
    async run(a, env) {
      const { players } = await site(env, 'api/players.json');
      const base = env.DATA_URL.replace(/\/$/, '');
      const q = fold(a.query).trim(), up = String(a.query).trim().toUpperCase();
      const docs = [
        { id: 'rankings:ros', title: 'Rest-of-season consensus rankings', url: `${base}/api/rankings/ros.json`, k: ['ros', 'rest', 'season'] },
        { id: 'rankings:dynasty', title: 'Dynasty consensus rankings', url: `${base}/api/rankings/dynasty.json`, k: ['dynasty', 'keeper'] },
        { id: 'league', title: 'Sleeper league rosters and standings', url: `${base}/api/league.json`, k: ['league', 'sleeper', 'roster', 'rosters', 'standings', 'available'] },
      ];
      const words = q.split(/\s+/);
      let picked = docs.filter(d => d.k.some(w => words.includes(w)));
      if (!picked.length && /rank/.test(q)) picked = docs.slice(0, 2); // plain "rankings": both lists
      const hits = players.filter(p => fold(p.name).includes(q) || p.team === up || p.positions?.includes(up)).slice(0, 25);
      return {
        results: [...picked.map(({ k, ...d }) => d),
          ...hits.map(p => ({ id: `player:${p.id}`, title: `${p.name} (${p.team || 'FA'}, ${(p.positions || []).join('/')})`, url: `${base}/api/players/${p.id}.json` }))],
      };
    },
  },
  fetch: {
    description: 'Fetch a document returned by search (player:<id>, rankings:ros, rankings:dynasty, league).',
    inputSchema: { type: 'object', required: ['id'], properties: { id: str('Id from search') } },
    async run(a, env) {
      const base = env.DATA_URL.replace(/\/$/, '');
      const [kind, rest] = String(a.id).split(/:(.*)/s);
      let path, title;
      if (kind === 'player') { path = `api/players/${encodeURIComponent(rest)}.json`; }
      else if (kind === 'rankings') { path = `api/rankings/${rest === 'dynasty' ? 'dynasty' : 'ros'}.json`; title = `${rest} rankings`; }
      else if (kind === 'league') { path = 'api/league.json'; title = 'Sleeper league'; }
      else return { error: `Unknown id "${a.id}"` };
      const d = await site(env, path);
      return { id: a.id, title: title || d.name, text: JSON.stringify(d), url: `${base}/${path}`, metadata: { source: 'Hoops Tracker' } };
    },
  },
};

// ---------------------------------------------------------------- JSON-RPC
async function handle(msg, env) {
  const { id, method, params } = msg || {};
  if (id === undefined || id === null) return null; // notification
  const ok = result => ({ jsonrpc: '2.0', id, result });
  const err = (code, message) => ({ jsonrpc: '2.0', id, error: { code, message } });
  switch (method) {
    case 'initialize': {
      const want = params?.protocolVersion;
      return ok({
        protocolVersion: PROTOCOLS.includes(want) ? want : PROTOCOLS[0],
        capabilities: { tools: {} },
        serverInfo: { name: 'hoops-tracker', title: 'Hoops Tracker', version: '1.1.0' },
        instructions: 'NBA fantasy basketball data for a Sleeper points league: consensus dynasty and rest-of-season rankings, '
          + 'per-game stats and fantasy points, injuries, and Sleeper league rosters. Start with get_overview; use search_players '
          + 'or get_rankings for lists, get_player for one player, get_league for rosters, '
          + 'get_transactions for adds/drops/trades, get_matchup for starters/bench and opponents. '
          + 'Public Sleeper data cannot confirm NBA Lock-In selections.',
      });
    }
    case 'ping': return ok({});
    case 'tools/list':
      return ok({ tools: Object.entries(TOOLS).map(([name, t]) => ({ name, description: t.description, inputSchema: t.inputSchema, annotations: { readOnlyHint: true, openWorldHint: false } })) });
    case 'tools/call': {
      const tool = TOOLS[params?.name];
      if (!tool) return err(-32602, `Unknown tool: ${params?.name}`);
      try {
        const out = await tool.run(params.arguments || {}, env);
        const isError = !!(out && out.error && Object.keys(out).length <= 3);
        return ok({ content: [{ type: 'text', text: JSON.stringify(out) }], isError });
      } catch (e) {
        return ok({ content: [{ type: 'text', text: `Error: ${e.message}` }], isError: true });
      }
    }
    default: return err(-32601, `Method not found: ${method}`);
  }
}

const json = (obj, status = 200, extra = {}) =>
  new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json', ...CORS, ...extra } });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });
    if (url.pathname === '/mcp' || url.pathname === '/mcp/') {
      if (request.method === 'GET') return new Response('This MCP server does not offer an SSE stream; POST JSON-RPC to /mcp.', { status: 405, headers: { Allow: 'POST', ...CORS } });
      if (request.method === 'DELETE') return new Response(null, { status: 405, headers: CORS }); // stateless: no sessions
      if (request.method !== 'POST') return new Response('Method not allowed', { status: 405, headers: CORS });
      let body;
      try { body = await request.json(); } catch { return json({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } }, 400); }
      if (Array.isArray(body)) {
        const out = (await Promise.all(body.map(m => handle(m, env)))).filter(Boolean);
        return out.length ? json(out) : new Response(null, { status: 202, headers: CORS });
      }
      const out = await handle(body, env);
      return out ? json(out) : new Response(null, { status: 202, headers: CORS });
    }
    if (['/api/league', '/api/matchup', '/api/transactions'].includes(url.pathname)) {
      if (request.method !== 'GET') return json({ error: 'Method not allowed' }, 405);
      const action = { '/api/league': 'get_league', '/api/matchup': 'get_matchup', '/api/transactions': 'get_transactions' }[url.pathname];
      try {
        const result = await TOOLS[action].run(Object.fromEntries(url.searchParams.entries()), env);
        return json(result, result.error ? 400 : 200, { 'Cache-Control': 'no-store' });
      } catch (e) {
        return json({ error: e.message }, 400);
      }
    }
    if (url.pathname === '/' || url.pathname === '/health') {
      return json({
        name: 'Hoops Tracker MCP server', version: '1.1.0', mcp_endpoint: `${url.origin}/mcp`, transport: 'streamable-http',
        data: env.DATA_URL, tools: Object.keys(TOOLS),
      });
    }
    return new Response('Not found', { status: 404, headers: CORS });
  },
};
