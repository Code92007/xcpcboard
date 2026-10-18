'use strict';
const assert = require('node:assert/strict');
const {load} = require('../static/preferences.js');
const values = new Map();
const storage = {getItem:k => values.get(k) ?? null, setItem:(k,v) => values.set(k,v)};
const old = {stars:['a','b','unmatched'], notes:{a:'原备注', b:'旧备注', unmatched:'不要迁移'}, groups:[{id:'old-g',name:'群友',teams:['a','b','unmatched']}], view:'local:old-g',queueOnly:true};
const saved = JSON.stringify(old);
storage.setItem('xcpc-v2:warmup', saved);
storage.setItem('xcpc-v2:formal', JSON.stringify({stars:['c'],notes:{b:'新备注'},groups:[{id:'new-g',name:'群友',teams:['c']}]}));
const station = {source:'formal', preference_sources:[{source:'warmup',team_map:{a:'new-a',b:'b'},public_group_map:{'public-old':'public-new'}}]};
let result = load(storage, station);
assert.deepEqual(result.stars, ['c','new-a','b']);
assert.deepEqual(result.notes, {'new-a':'原备注',b:'新备注'});
assert.deepEqual(result.groups, [{id:'new-g',name:'群友',teams:['c','new-a','b']}]);
assert.equal(result.view, 'local:new-g');
assert.equal(result.queueOnly, true);
assert.equal(storage.getItem('xcpc-v2:warmup'), saved);
// Intentional later removals must survive every poll/reload.
result.stars = []; result.notes = {}; result.groups = []; result.view = 'all'; result.queueOnly = false;
storage.setItem('xcpc-v2:formal', JSON.stringify(result));
assert.deepEqual(load(storage, station), result);
// A public default created again under a new ID still selects the restored group.
storage.setItem('xcpc-v2:warmup', JSON.stringify({...old,view:'pub:public-old'}));
values.delete('xcpc-v2:formal');
assert.equal(load(storage, station).view, 'pub:public-new');
// Other sources and corrupt storage must not leak/crash.
assert.deepEqual(load(storage, {source:'another'}).stars, []);
storage.setItem('xcpc-v2:formal', '{broken');
storage.setItem('xcpc-v2:warmup', JSON.stringify({stars:null,notes:null,groups:[null],view:'stars'}));
assert.deepEqual(load(storage, station).stars, []);
assert.deepEqual(load({getItem:()=>{throw Error('disabled');}}, station).groups, []);
console.log('Browser preference migration checks passed');
