'use strict';
(function(root) {
  const object = value => value && typeof value === 'object' && !Array.isArray(value);
  const read = (storage, key) => { try { return JSON.parse(storage.getItem(key)); } catch { return null; } };
  const key = source => 'xcpc-v2:' + source;
  function normalize(value) {
    const data = object(value) ? value : {};
    return {...data,
      stars: Array.isArray(data.stars) ? data.stars.filter(t => typeof t === 'string') : [],
      groups: Array.isArray(data.groups) ? data.groups.filter(g => object(g) && typeof g.id === 'string' && typeof g.name === 'string' && Array.isArray(g.teams)).map(g => ({...g, teams:g.teams.filter(t => typeof t === 'string')})) : [],
      notes: object(data.notes) ? {...data.notes} : {},
      inherited_sources: Array.isArray(data.inherited_sources) ? [...data.inherited_sources] : []};
  }
  function load(storage, station) {
    const target = normalize(read(storage, key(station.source)));
    let changed = false;
    for (const link of station.preference_sources || []) {
      if (target.inherited_sources.includes(link.source)) continue;
      const saved = read(storage, key(link.source));
      if (!object(saved)) continue;
      const previous = normalize(saved), map = link.team_map || {};
      const mapped = teams => teams.filter(t => Object.hasOwn(map, t)).map(t => map[t]);
      target.stars = [...new Set([...target.stars, ...mapped(previous.stars)])];
      for (const [tid, note] of Object.entries(previous.notes)) {
        if (Object.hasOwn(map, tid) && !Object.hasOwn(target.notes, map[tid])) target.notes[map[tid]] = note;
      }
      const groupMap = {};
      for (const old of previous.groups) {
        const members = mapped(old.teams);
        if (old.teams.length && !members.length) continue;
        const names = target.groups.filter(g => g.name === old.name);
        let group = target.groups.find(g => g.id === old.id) || (names.length === 1 ? names[0] : null);
        if (!group) { group = {id:old.id, name:old.name, teams:[]}; target.groups.push(group); }
        group.teams = [...new Set([...group.teams, ...members])];
        groupMap[old.id] = group.id;
      }
      if (!target.view && previous.view) {
        if (['all','stars'].includes(previous.view)) target.view = previous.view;
        else if (previous.view.startsWith('local:') && groupMap[previous.view.slice(6)]) target.view = 'local:' + groupMap[previous.view.slice(6)];
        else if (previous.view.startsWith('pub:') && link.public_group_map?.[previous.view.slice(4)]) target.view = 'pub:' + link.public_group_map[previous.view.slice(4)];
      }
      for (const setting of ['rankGroup','queueOnly']) {
        if (!Object.hasOwn(target, setting) && Object.hasOwn(previous, setting)) target[setting] = previous[setting];
      }
      target.inherited_sources.push(link.source);
      changed = true;
    }
    // Keep the warmup key intact, and import once so later unfollows stay removed.
    if (changed) { try { storage.setItem(key(station.source), JSON.stringify(target)); } catch {} }
    return target;
  }
  function matchStars(starred, sourceTeams, targetTeams, followed=[]) {
    const sourceById = new Map(sourceTeams.map(team => [team.id, team]));
    const byName = teams => {
      const result = new Map();
      for (const team of teams) {
        const name = String(team.name || '').trim();
        if (name) result.set(name, [...(result.get(name) || []), team]);
      }
      return result;
    };
    const sourceNames = byName(sourceTeams), targetNames = byName(targetTeams);
    const existing = new Set(followed), matches = [];
    let unmatched = 0, ambiguous = 0, alreadyFollowed = 0;
    for (const id of new Set(starred)) {
      const source = sourceById.get(id);
      const name = String(source?.name || '').trim();
      if (!name || !targetNames.has(name)) { unmatched++; continue; }
      if (sourceNames.get(name).length !== 1 || targetNames.get(name).length !== 1) { ambiguous++; continue; }
      const target = targetNames.get(name)[0];
      if (existing.has(target.id)) { alreadyFollowed++; continue; }
      matches.push({source, target});
    }
    return {matches, unmatched, ambiguous, alreadyFollowed};
  }
  const api = {load, matchStars};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.XCPCPreferences = api;
})(globalThis);
