'use strict';
const assert = require('node:assert/strict');
const {load, matchStars, cfLevel, displayNote} = require('../static/preferences.js');
const roster=['杜瑜皓','蒋凌宇','许淇文'];
const original='蒋凌宇/杜瑜皓/许淇文';
assert.equal(displayNote(original,roster), '');
assert.equal(displayNote(original,roster,true), original);
assert.equal(displayNote('杜瑜皓/EvenImage+蒋凌宇/jiangly',roster), 'EvenImage+jiangly');
assert.equal(displayNote('Zheng_iii/chennie',['陈聂','郑岩泽']), 'Zheng_iii/chennie');
assert.equal(displayNote('<小羊肖恩>', ['杨潇']), '<小羊肖恩>');
assert.equal(displayNote('/原样备注/', ['杨潇']), '/原样备注/');
assert.equal(displayNote('甲乙丙+甲乙',['甲乙','甲乙丙']), '');
for (const [rating, expected] of [[null,'unrated'],[undefined,'unrated'],[0,'newbie'],[1199,'newbie'],[1200,'pupil'],[1399,'pupil'],[1400,'specialist'],[1600,'expert'],[1900,'candidate-master'],[2100,'master'],[2399,'master'],[2400,'grandmaster'],[2999,'grandmaster'],[3000,'legendary']]) assert.equal(cfLevel(rating), expected);
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
// Waiting for an official roster must show the saved warmup without importing IDs.
const pendingValues = new Map([
  ['xcpc-v2:warmup', JSON.stringify(old)],
  ['xcpc-v2:formal', JSON.stringify({stars:['c'], notes:{b:'新备注'}})]
]);
const pendingStorage = {getItem:k=>pendingValues.get(k)??null,setItem:(k,v)=>pendingValues.set(k,v)};
const pendingBefore = [...pendingValues];
const preview = load(pendingStorage, {source:'formal',preference_preview:{source:'warmup'}});
assert.equal(preview.preview, true);
assert.deepEqual(preview.stars, old.stars);
assert.deepEqual(preview.groups, old.groups);
assert.deepEqual(preview.notes, old.notes);
assert.deepEqual([...pendingValues], pendingBefore);
const published = load(pendingStorage, station);
assert.deepEqual(published.stars, ['c','new-a','b']);
assert.equal(published.preview, undefined);
assert.equal(pendingStorage.getItem('xcpc-v2:warmup'), JSON.stringify(old));
const previousTeams = [
  {id:'a',name:'同名队',organization:'甲校'},
  {id:'b',name:'已关注',organization:'乙校'},
  {id:'c',name:'重名',organization:'丙校'},
  {id:'d',name:'重名',organization:'丁校'},
  {id:'e',name:'未参赛',organization:'戊校'}
];
const currentTeams = [
  {id:'new-a',name:'同名队',organization:'甲校'},
  {id:'new-b',name:'已关注',organization:'乙校'},
  {id:'new-c',name:'重名',organization:'丙校'},
  {id:'new-d',name:'重名',organization:'丁校'},
  {id:'other',name:'同名队 ',organization:'不同学校'}
];
let imported = matchStars(['a','b','c','e','missing','a'],previousTeams,currentTeams,['new-b']);
assert.deepEqual(imported.matches, []);
assert.equal(imported.ambiguous, 2);
assert.equal(imported.unmatched, 2);
assert.equal(imported.alreadyFollowed, 1);
currentTeams.pop();
imported = matchStars(['a','b','c','e','missing','a'],previousTeams,currentTeams,['new-b']);
assert.deepEqual(imported.matches.map(match => match.target.id), ['new-a']);
assert.equal(imported.ambiguous, 1);
console.log('Browser preference migration checks passed');
