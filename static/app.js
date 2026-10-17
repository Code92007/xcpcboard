'use strict';
const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const read = (storage, key, fallback) => { try { return JSON.parse(storage.getItem(key)) ?? fallback; } catch { return fallback; } };
const save = (storage, key, value) => { try { storage.setItem(key, JSON.stringify(value)); return true; } catch { toast('浏览器存储不可用，本次修改仅在当前页面保留'); return false; } };
const S = {stations:[], active:'', data:null, local:null, token:read(sessionStorage,'xcpc-admin',''), query:'', view:'all', rankGroup:'official', queue:[], seen:new Map(), backlog:[], paused:false, queueOnly:false, video:true, mediaSelected:{}, busy:false, source:'', online:true,queueInitialized:false,receivedAt:0};
let toastTimer, pollTimer, animationTimer;
function toast(message) { $('toast').textContent=message; $('toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').classList.remove('visible'),3500); }
async function api(path, data, admin=false) {
  const headers = {};
  if (admin) headers.Authorization='Bearer '+S.token;
  if (data) headers['Content-Type']='application/json';
  const response = await fetch(path,{method:data?'PUT':'GET',headers,body:data?JSON.stringify(data):undefined,signal:AbortSignal.timeout(18000)});
  const result = await response.json();
  if (!response.ok) { if(response.status===401) { S.token=''; sessionStorage.removeItem('xcpc-admin'); } throw Error(result.error || '请求失败'); }
  return result;
}
function station() { return S.data?.station || S.stations.find(s=>s.id===S.active); }
function board() { return S.data?.board; }
function prefKey() { return 'xcpc-v2:'+S.source; }
function persist() { if(S.local) { S.local.view=S.view; S.local.rankGroup=S.rankGroup; S.local.queueOnly=S.queueOnly; save(localStorage,prefKey(),S.local); } }
function note(team) { return S.local?.notes?.[team.id] || station()?.notes?.[team.id] || ''; }
function stars() { return S.local?.stars || []; }
function rank(team) { return team.ranks[S.rankGroup] ?? null; }
function rankText(team) { const n=rank(team); return n==null?'*':String(n); }
function currentGroup() {
  if(S.view.startsWith('pub:')) return station()?.public_groups.find(g=>g.id===S.view.slice(4));
  if(S.view.startsWith('local:')) return S.local?.groups.find(g=>g.id===S.view.slice(6));
  return null;
}
function inView(team) { if(S.view==='all')return true; if(S.view==='stars')return stars().includes(team.id); return currentGroup()?.teams.includes(team.id) ?? false; }
function inRankGroup(team) { return S.rankGroup==='all' || team.groups.includes(S.rankGroup); }
function matches(team,query=S.query) { return [team.name,team.organization,team.id,team.location,note(team),...(team.members||[])].join(' ').toLowerCase().includes(query.toLowerCase()); }
function minute(n) { return Number.isInteger(n)?String(n):Number(n).toFixed(2).replace(/0+$/,'').replace(/\.$/,''); }
function newId() { return 'g'+[...crypto.getRandomValues(new Uint8Array(8))].map(n=>n.toString(16).padStart(2,'0')).join(''); }
function clock(sec) { sec=Math.max(0,Math.floor(sec));return [Math.floor(sec/3600),Math.floor(sec%3600/60),sec%60].map(n=>String(n).padStart(2,'0')).join(':'); }
function date(sec) { return new Date(sec*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); }
function timeOf(sec) { return new Date(sec*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}); }
function safeColor(value) { return /^#[0-9a-f]{3,8}$/i.test(value)?value:'#edf1e6'; }

async function refreshCatalog() {
  const result=await api('/api/contests'); S.stations=result.contests;
  if(!S.active || !S.stations.some(s=>s.id===S.active)) S.active=S.stations[0]?.id || '';
  renderStations();
}
function renderStations() {
  $('stationTabs').innerHTML=S.stations.map((s,i)=>`<button class="station-tab ${s.id===S.active?'active':''}" data-station="${esc(s.id)}" aria-current="${s.id===S.active?'page':'false'}"><span>${esc(s.title)}</span><small>${String(i+1).padStart(2,'0')}</small></button>`).join('');
}
async function switchStation(sid) {
  S.active=sid; S.data=null; S.source=''; S.video=read(localStorage,'xcpc-media-visibility',{})[sid]??true; S.query=''; $('search').value='';
  S.queue=[];S.seen.clear();S.backlog=[]; clearTimeout(animationTimer);
  history.replaceState(null,'','#'+sid); renderStations(); render(); await refresh();
}
async function refresh() {
  if(S.busy || !S.active)return;
  S.busy=true; const sid=S.active;
  try {
    const data=await api('/api/contests/'+sid);
    if(sid!==S.active)return;
    S.data=data; S.online=true;S.receivedAt=Date.now()/1000;
    const catalogIndex=S.stations.findIndex(s=>s.id===sid);if(catalogIndex>=0)S.stations[catalogIndex]=data.station;
    const source=data.station.source;
    if(source!==S.source) {
      S.source=source; S.local=read(localStorage,prefKey(),{stars:[],groups:[],notes:{}});
      if(!Array.isArray(S.local.stars)||!Array.isArray(S.local.groups)||typeof S.local.notes!=='object')S.local={stars:[],groups:[],notes:{}};
      S.view=S.local.view || (data.station.default_group!=='all'?'pub:'+data.station.default_group:'all');
      S.rankGroup=S.local.rankGroup || data.station.upstream_group || 'official';
      S.queueOnly=!!S.local.queueOnly; S.queue=[];S.seen.clear();S.backlog=[];S.queueInitialized=false;
    }
    if(S.view!=='all' && S.view!=='stars' && !currentGroup())S.view='all';
    if(data.board) {
      const events=data.board.events;
      if(!S.queueInitialized) { S.queue=events.slice(-80).reverse(); events.forEach(e=>S.seen.set(e.id,eventFingerprint(e)));S.queueInitialized=true; }
      else {
        for(const e of events) {
          if(S.seen.get(e.id)!==eventFingerprint(e)) { S.seen.set(e.id,eventFingerprint(e)); S.backlog=S.backlog.filter(x=>x.id!==e.id);S.backlog.push(e); }
        }
        // Bound a long-paused tab; the full latest standings remain available.
        if(S.backlog.length>800)S.backlog=S.backlog.slice(-800);
      }
      save(localStorage,'xcpc-snapshot:'+sid,data);
    }
    render();drainQueue();
  } catch(error) {
    if(sid!==S.active)return;
    S.online=false;
    if(!S.data) {
      const cached=read(localStorage,'xcpc-snapshot:'+sid,null);
      if(cached) {
        S.data=cached;S.source=cached.station.source;S.local=read(localStorage,prefKey(),{stars:[],groups:[],notes:{}});
        S.view=S.local.view||'all';S.rankGroup=S.local.rankGroup||cached.station.upstream_group||'official';
        S.queue=cached.board?.events.slice(-80).reverse() || [];
      }
    }
    render(); $('syncTop').textContent='连接中断 · 使用缓存'; $('syncTop').classList.add('offline');
  } finally { S.busy=false;if(sid!==S.active)refresh(); }
}
function eventFingerprint(e) { return e.status+'|'+e.problem; }
function render() {renderStations();renderBanner();renderViews();renderTable();renderQueue();renderStatus();}
function videoOpen() { return S.video && !!station()?.media?.length; }
function renderBanner() {
  const b=board(),s=station();
  if(!s) { $('contestBanner').innerHTML='<div class="loading">正在连接赛站…</div>';return; }
  if(!b) {
    $('contestBanner').innerHTML=`<div><div class="contest-head"><h2>${esc(s.title)}</h2><span class="status-badge">${s.board_url?'等待数据':'待公布榜单'}</span></div><div class="contest-meta">${s.board_url?'首次拉取中，成功后会自动显示。':'正赛链接公布后，在「管理赛站」填入链接即可开始同步。'}</div></div><div class="contest-right"><button class="video-toggle" data-action="video">直播 / 视频</button><button class="video-toggle" data-action="settings">配置赛站 ↗</button></div>`;
  } else {
    const warmup=/warmup|热身/i.test(b.title+s.board_url);
    let status=b.state==='pending'?'尚未开始':b.state==='finished'?'比赛已结束':b.frozen?'已封榜':'比赛进行中';
    const freezeHours=((b.freeze_at-b.start)/3600).toFixed(2).replace(/\.?0+$/,'');
    $('contestBanner').innerHTML=`<div><div class="contest-head"><h2>${esc(b.title)}</h2><span class="status-badge ${b.frozen?'frozen':''}">${status}${b.frozen&&b.state==='finished'?' · 封榜保留':''}</span>${warmup?'<span class="status-badge warmup">热身赛预览</span>':''}</div><div class="contest-meta"><span>${date(b.start)}（北京时间）</span><span>赛程 ${(b.end-b.start)/3600} 小时</span><span>${b.revealed?'已手动解封':`开赛 ${freezeHours}h 后封榜`}</span><span>${b.problem_total??b.problems.length} 道题目${b.problem_reveal?` · 已揭晓 ${b.problems.length} 题`:''}</span></div></div><div class="contest-right"><div class="contest-clock"><strong id="contestClock"></strong><small id="clockLabel"></small></div><button class="video-toggle ${videoOpen()?'active':''}" data-action="video">${videoOpen()?'收起':'▹'} 直播 / 视频</button></div>`;
    updateClock();
  }
  $('sourceLink').href=s.board_url || 'https://board.xcpcio.com/';
  renderVideo();
}
function updateClock() {
  const b=board();if(!b || !$('contestClock'))return;
  const now=S.online?S.data.server_time+(Date.now()/1000-S.receivedAt):Date.now()/1000;
  let label='已进行',sec=Math.min(now,b.end)-b.start;
  if(now<b.start){label='距离开赛';sec=b.start-now;}
  else if(now>=b.end){label='完整赛程';sec=b.end-b.start;}
  $('contestClock').textContent=clock(sec);$('clockLabel').textContent=label;
}
function renderVideo() {
  const s=station(),panel=$('videoPanel'),items=s?.media||[];
  panel.hidden=!S.video||!items.length;
  if(!S.video){panel.replaceChildren();delete panel.dataset.playlist;return;}
  if(!items.length){panel.innerHTML='<div class="empty"><h3>这个赛站还没有配置直播或视频</h3><p>管理员可在「管理赛站」添加多个 B 站直播间或视频。</p><button data-action="settings">添加播放内容 ↗</button></div>';return;}
  const signature=JSON.stringify([s.id,items]);
  if(panel.dataset.playlist===signature && panel.querySelector('iframe'))return;
  panel.dataset.playlist=signature;
  panel.innerHTML=`<div class="video-header"><div><span class="media-kind"></span><strong id="mediaTitle"></strong></div><a id="mediaOriginal" target="_blank" rel="noreferrer">在 B 站打开 ↗</a></div><div class="media-viewport"><div class="media-track">${items.map(()=>'<div class="media-slide"></div>').join('')}</div></div><div class="media-controls"><button type="button" data-media-step="-1" aria-label="上一个直播或视频">‹</button><div class="media-swipe" tabindex="0" role="group" aria-label="左右滑动切换直播或视频"><span id="mediaCount"></span><small>${items.length>1?'左右滑动切换节目':'直播与视频 · 按需播放'}</small></div><button type="button" data-media-step="1" aria-label="下一个直播或视频">›</button></div><div class="media-playlist" aria-label="播放列表">${items.map((item,i)=>`<button type="button" data-media-index="${i}" aria-pressed="false"><span>${item.type==='live'?'直播':'视频'}</span>${esc(item.title)}</button>`).join('')}</div>`;
  let start=null;
  const swipe=panel.querySelector('.media-swipe');
  swipe.onpointerdown=e=>{start={x:e.clientX,y:e.clientY};swipe.setPointerCapture(e.pointerId);};
  swipe.onpointerup=e=>{if(start&&Math.abs(e.clientX-start.x)>45&&Math.abs(e.clientX-start.x)>Math.abs(e.clientY-start.y))selectMedia(Number(panel.dataset.index)+(e.clientX<start.x?1:-1));start=null;};
  swipe.onpointercancel=()=>{start=null;};
  panel.onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();selectMedia(Number(panel.dataset.index)+(e.key==='ArrowRight'?1:-1));}};
  const selected=items.findIndex(item=>item.id===S.mediaSelected[S.active]);
  selectMedia(selected<0?0:selected);
}
function selectMedia(index) {
  const panel=$('videoPanel'),items=station()?.media||[];
  if(!items.length)return;
  index=(index+items.length)%items.length;
  const item=items[index];
  S.mediaSelected[S.active]=item.id;panel.dataset.index=index;
  // Remove the old player before creating another so background audio stops.
  for(const frame of panel.querySelectorAll('iframe'))frame.remove();
  const frame=document.createElement('iframe');frame.title=item.title;frame.src=item.embed_url;
  frame.allow='autoplay; fullscreen; picture-in-picture; encrypted-media';frame.allowFullscreen=true;
  frame.referrerPolicy='strict-origin-when-cross-origin';
  const slides=panel.querySelectorAll('.media-slide');
  slides.forEach((slide,i)=>slide.setAttribute('aria-hidden',String(i!==index)));
  slides[index].append(frame);
  panel.querySelector('.media-track').style.transform=`translateX(-${index*100}%)`;
  $('mediaTitle').textContent=item.title;$('mediaOriginal').href=item.url;
  panel.querySelector('.media-kind').textContent=item.type==='live'?'BILIBILI LIVE':'BILIBILI VIDEO';
  $('mediaCount').textContent=String(index+1).padStart(2,'0')+' / '+String(items.length).padStart(2,'0');
  panel.querySelectorAll('[data-media-index]').forEach((button,i)=>{button.classList.toggle('active',i===index);button.setAttribute('aria-pressed',String(i===index));});
  panel.querySelectorAll('[data-media-step]').forEach(button=>{button.disabled=items.length<2;});
}
function renderStatus() {
  const h=S.data?.health;
  $('syncTop').classList.toggle('offline',!S.online||!!h?.stale);
  $('syncTop').textContent=!S.online?'连接中断 · 使用缓存':h?.stale?'上游异常 · 使用缓存':h?.last_success?`${timeOf(h.last_success)} 已同步`:'等待首次同步';
  const notice=$('cacheNotice');
  notice.hidden=S.online&&!h?.stale&&!board()?.frozen;
  notice.textContent=!S.online?'当前无法连接服务器，正在展示此浏览器保存的最近榜单。连接恢复后自动同步。':h?.stale?`上游暂不可用，正在展示 ${date(h.last_success)} 的完整缓存；持续自动重试。` : board()?.frozen?'封榜保护中：封榜后的提交仅显示「封榜」，不会展示隐藏结果；缓存与提交队列继续更新。':'';
  $('manageButton').innerHTML=S.token?'赛站管理 <span>↗</span>':'管理赛站 <span>↗</span>';
}
function renderViews() {
  const s=station();
  const tabs=[['all','全部队伍'],['stars','我的关注 '+stars().length],...(s?.public_groups||[]).map(g=>['pub:'+g.id,`<span class="tag">公共</span>${esc(g.name)}`]),...(S.local?.groups||[]).map(g=>['local:'+g.id,esc(g.name)])];
  $('viewTabs').innerHTML=tabs.map(([id,name])=>`<button data-view="${esc(id)}" class="${S.view===id?'active':''}">${name}</button>`).join('');
  const groups=board()?.groups||{};
  const rankOptions=[['all','全部排名'],...Object.entries(groups)];
  for(const g of new Set((board()?.teams||[]).flatMap(t=>t.groups)))if(!rankOptions.some(([k])=>k===g))rankOptions.push([g,g]);
  if(!rankOptions.some(([id])=>id===S.rankGroup))S.rankGroup='all';
  $('rankGroup').innerHTML=rankOptions.map(([id,name])=>`<option value="${esc(id)}" ${S.rankGroup===id?'selected':''}>${esc(name)}</option>`).join('');
  $('queueOnlyView').checked=S.queueOnly;
}
function problemCell(p) {
  if(p.state==='none')return '<div class="problem none">·</div>';
  if(p.state==='ac')return `<div class="problem ac ${p.first?'first':''}" title="${p.first?'首杀 · ':''}${p.wrong} 次失败，${p.minute} 分钟通过"><span>${p.wrong?'+'+p.wrong:'+'}</span><small>${p.minute} min</small></div>`;
  if(p.state==='pending'||p.state==='frozen')return `<div class="problem ${p.state}" title="${p.wrong} 次失败，${p.pending} 次待揭晓"><span>? ${p.pending}</span><small>${p.wrong?'-'+p.wrong+' / ':''}${p.state==='frozen'?'封榜':'待判'}</small></div>`;
  return `<div class="problem wa" title="${p.wrong} 次失败"><span>−${p.wrong}</span></div>`;
}
function renderTable() {
  const b=board(),s=station(),wrap=$('tableWrap');
  if(!b) {
    $('teamCount').textContent='—';
    $('viewInfo').textContent='公开排名始终基于所选原榜组';
    wrap.innerHTML=`<div class="empty"><h3>${s?.board_url?'榜单准备中':'等待赛站的第一声键响'}</h3><p>${s?.board_url?'后台正在拉取数据，上游恢复后将自动加载。':'这里已为比赛留好位置。填入正式榜单后，队伍和提交会自动到场。'}</p>${S.data?.health?.error?`<p>${esc(S.data.health.error)}</p>`:''}<button data-action="settings">配置榜单链接 ↗</button></div>`;return;
  }
  const eligible=b.teams.filter(inRankGroup),rows=eligible.filter(t=>inView(t)&&matches(t));
  $('teamCount').textContent=rows.length+' / '+eligible.length;
  $('viewInfo').textContent=`${S.view==='all'?'全部队伍':S.view==='stars'?'我的关注':currentGroup()?.name || ''} · 排名基于${$('rankGroup').selectedOptions[0]?.textContent || '原榜'} · 罚时单位：分钟`;
  const hidden=!!b.problem_reveal?.hidden;
  const revealHint=hidden?` · 满 ${b.problem_reveal.threshold} 队通过后揭晓题号；右侧未揭晓题目仅在队内排列`:'';
  $('viewInfo').textContent+=revealHint;
  wrap.innerHTML=`<table><thead><tr><th></th><th>排名</th><th class="note-col">群友备注</th><th class="team-col">队伍 / 学校</th><th>AC</th><th>罚时</th>${b.problems.map(p=>`<th><span class="prob-label" style="background:${safeColor(p.color)}">${esc(p.label)}</span><small>${eligible.filter(t=>t.problems[b.problems.indexOf(p)].state==='ac').length} AC</small></th>`).join('')}${hidden?'<th class="hidden-heading">未揭晓题目<small>队内排列 · 无题号</small></th>':''}</tr></thead><tbody>${rows.map(t=>`<tr data-team="${esc(t.id)}" class="${stars().includes(t.id)?'followed':''}"><td class="star-cell"><button class="star ${stars().includes(t.id)?'on':''}" data-star="${esc(t.id)}" title="${stars().includes(t.id)?'取消关注':'加入我的关注'}" aria-label="${stars().includes(t.id)?'取消关注':'关注'} ${esc(t.name)}">${stars().includes(t.id)?'★':'☆'}</button></td><td class="rank ${rank(t)<=3?'top':''}">${rankText(t)}</td><td class="note-cell"><button class="note-button ${note(t)?'':'blank'}" data-note="${esc(t.id)}" title="编辑群友备注">${note(t)?esc(note(t)):'＋ 添加备注'}</button></td><td class="team-cell" title="${esc(t.id+' · '+t.name+' · '+t.members.join(' / '))}"><div class="team-name">${esc(t.name)}${t.groups.includes('unofficial')?'<span class="unofficial">打星</span>':''}</div><div class="organization">${esc(t.organization || t.location || t.id)}</div></td><td class="solved">${t.solved}</td><td class="penalty">${minute(t.penalty)}</td>${t.problems.map(p=>`<td class="prob-cell">${problemCell(p)}</td>`).join('')}${hidden?`<td class="hidden-cell"><div class="hidden-problems">${(t.hidden_problems||[]).map(p=>problemCell(p)).join('')||'<span class="help">尚无提交</span>'}</div></td>`:''}</tr>`).join('') || `<tr><td colspan="${6+b.problems.length+Number(hidden)}"><div class="empty"><h3>${S.query?'没有找到匹配队伍':'这个分组还没有队伍'}</h3><p>${S.query?'试试队名、学校或群友备注。':'在全部队伍中点 ☆，或使用「关注分组」挑选队伍。'}</p></div></td></tr>`}</tbody></table>`;
}
function statusClass(status) {return status==='AC'?'ac':['FROZEN','PENDING'].includes(status)?'pending':['WA','RE','TLE','MLE','OLE','RJ','JE'].includes(status)?'wa':'neutral';}
function renderQueue(animated=false,newId='') {
  const b=board(),container=$('queue');
  if(!b){container.innerHTML='<div class="empty">等待比赛数据</div>';$('queueTotal').textContent='—';$('queueHint').textContent='最近提交 · 新提交会依次入队';return;}
  const teamMap=new Map(b.teams.map(t=>[t.id,t]));
  const latest=new Map(b.events.map(e=>[e.id,e]));
  const events=S.queue.map(e=>latest.get(e.id)||e).filter(e=>{const t=teamMap.get(e.team_id);return t&&inRankGroup(t)&&(!S.queueOnly||inView(t));}).slice(0,80);
  const oldNodes=new Map([...container.querySelectorAll('[data-event]')].map(n=>[n.dataset.event,n]));
  const positions=animated?new Map([...oldNodes].map(([id,n])=>[id,n.getBoundingClientRect().top])):new Map();
  const nodes=events.map(e=>{
    const t=teamMap.get(e.team_id),n=oldNodes.get(e.id)||document.createElement('div');n.className='queue-row';n.dataset.event=e.id;
    n.innerHTML=`<span class="q-rank ${rank(t)!=null&&rank(t)<=3?'top':''}" title="当前排名">${rankText(t)}</span><div class="q-team"><strong title="${esc(t.name)}">${note(t)?`<span class="q-note">${esc(note(t))}</span>`:''}${esc(t.name)}</strong><small title="${esc(t.organization)}">${esc(t.organization || t.id)} · ${t.solved} AC · ${clock(e.second)}</small></div><span class="q-problem" title="${e.problem==='?'?'题号尚未揭晓':'题目 '+esc(e.problem)}">${esc(e.problem)}</span><span class="q-status ${statusClass(e.status)}" title="${esc(e.status)}">${e.status==='FROZEN'?'封榜':e.status==='PENDING'?'待判':esc(e.status)}</span>`;
    return n;
  });
  container.replaceChildren(...nodes);
  if(!nodes.length)container.innerHTML='<div class="empty">当前分组暂无公开提交</div>';
  if(animated&&!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    for(const n of nodes) {
      if(n.dataset.event===newId) {n.animate([{opacity:0,transform:'translateY(-15px)',background:'#e5f2dc'},{opacity:1,transform:'translateY(0)',background:'#f1f8e9'},{background:'transparent'}],{duration:950,easing:'ease-out'});}
      else if(positions.has(n.dataset.event)) { const dy=positions.get(n.dataset.event)-n.getBoundingClientRect().top;if(dy)n.animate([{transform:`translateY(${dy}px)`},{transform:'translateY(0)'}],{duration:420,easing:'cubic-bezier(.2,.7,.2,1)'}); }
    }
  }
  $('queueTotal').textContent=b.total_runs.toLocaleString()+' 次提交';
  $('queueHint').textContent=S.paused?`动画已暂停${S.backlog.length?' · '+S.backlog.length+' 条等待入队':''}`:S.backlog.length?`${S.backlog.length} 条正在依次入队`:'最近提交 · 新提交会依次入队';
  $('queuePause').textContent=S.paused?'▷':'Ⅱ';$('queuePause').setAttribute('aria-label',S.paused?'恢复队列动画':'暂停队列动画');
}
function drainQueue() {
  clearTimeout(animationTimer);
  if(S.paused||!S.backlog.length)return;
  const event=S.backlog.shift();S.queue=S.queue.filter(e=>e.id!==event.id);S.queue.unshift(event);S.queue=S.queue.slice(0,800);
  renderQueue(true,event.id);animationTimer=setTimeout(drainQueue,S.backlog.length>50?180:650);
}
function openModal(title,eyebrow,html) { $('modalTitle').textContent=title;$('modalEyebrow').textContent=eyebrow;$('modalBody').innerHTML=html;if(!$('modal').open)$('modal').showModal(); }
function closeModal() { $('modal').close(); }
function errorMessage(error) { const field=$('formError');if(field)field.textContent=error.message;else toast(error.message); }
async function adminSave(data,sid=S.active) {
  const result=await api('/api/admin/contests/'+sid,data,true);
  if(sid===S.active && S.data) S.data.station=result;
  await refreshCatalog();return result;
}
function login(after=settings) {
  if(S.token){after();return;}
  openModal('进入赛站管理','ADMIN ACCESS',`<form id="loginForm" class="form-stack"><div class="help">使用服务器上的管理密钥，配置所有访客可见的榜单、直播或视频、公共分组和备注。</div><label class="field">管理密钥<input type="password" id="adminToken" autocomplete="current-password" required autofocus></label><div id="formError" class="form-error"></div><div class="modal-actions"><button type="button" data-close>取消</button><button class="primary" type="submit">进入管理</button></div></form>`);
  $('loginForm').onsubmit=async e=>{e.preventDefault();S.token=$('adminToken').value.trim();try{await api('/api/admin/session',null,true);save(sessionStorage,'xcpc-admin',S.token);after();renderStatus();}catch(error){errorMessage(error);}};
}
function settings(newStation=false) {
  if(!S.token){login(()=>settings(newStation));return;}
  const s=newStation?{id:'',title:'',board_url:'',room:'',freeze_after:4,reveal:false}:station();
  openModal(newStation?'添加一个新赛站':'赛站设置','CONTEST SETTINGS',`<form id="settingsForm" class="form-stack"><div class="form-row">${newStation?'<label class="field">赛站 ID<input id="settingId" placeholder="例如 hangzhou-2027" pattern="[a-z0-9][a-z0-9-]{0,39}" required></label>':''}<label class="field">赛站名称<input id="settingTitle" value="${esc(s.title)}" required maxlength="80" placeholder="例如 ICPC 杭州"></label></div><label class="field">榜单链接（XCPCIO / CCPC）<input type="url" id="settingUrl" value="${esc(s.board_url)}" placeholder="https://board.xcpcio.com/icpc/51st/xian?group=official"><small>支持 board.xcpcio.com 和 rank.iccpc.cn/r/…；可随时替换链接。数据按榜单隔离缓存，分组和备注也按榜单保存。留空表示等待公布。</small></label><div class="field"><div class="group-heading"><h3>直播 / 视频播放列表</h3><button type="button" id="addMedia">＋ 添加内容</button></div><div id="mediaEditor"></div><small>选填，最多 20 项。支持直播间号、BV/av 号或 B 站完整链接；视频分 P 可用 ?p=2。访客可左右切换并自行打开或收起。</small></div><label class="field">开赛后几小时封榜<input type="number" id="settingFreeze" value="${s.freeze_after}" min="0" max="24" step="0.25" required><small>默认 4 小时；若原榜更早封榜，则跟随原榜。封榜后仍刷新公开提交。</small></label><label class="check-field"><input type="checkbox" id="settingReveal" ${s.reveal?'checked':''}><span>手动解封：显示上游已公开的最终结果<br><span class="help">官方滚榜完成后再开启；无法获取上游没有公开的数据。</span></span></label><div id="formError" class="form-error"></div><div class="modal-actions">${!newStation?'<button type="button" id="addStation" class="left">＋ 新增赛站</button>':''}<button type="button" id="adminLogout">退出管理</button><button class="primary" type="submit">保存设置</button></div></form>`);
  renderMediaEditor(s.media||[]);
  $('addMedia').onclick=()=>{const draft=readMediaEditor();if(draft.length>=20){toast('每站最多 20 项');return;}draft.push({title:'',url:''});renderMediaEditor(draft);$('mediaEditor').lastElementChild.querySelector('[data-media-url]').focus();};
  $('mediaEditor').onclick=e=>{const button=e.target.closest('[data-media-edit]');if(!button)return;const draft=readMediaEditor(),index=Number(button.dataset.index);if(button.dataset.mediaEdit==='delete')draft.splice(index,1);else{const next=index+Number(button.dataset.mediaEdit);[draft[index],draft[next]]=[draft[next],draft[index]];}renderMediaEditor(draft);};
  if($('addStation'))$('addStation').onclick=()=>settings(true);
  $('adminLogout').onclick=()=>{S.token='';sessionStorage.removeItem('xcpc-admin');closeModal();renderStatus();toast('已退出管理');};
  $('settingsForm').onsubmit=async e=>{
    e.preventDefault();const button=e.submitter;button.disabled=true;
    try {const sid=newStation?$('settingId').value.trim():S.active;await adminSave({title:$('settingTitle').value,board_url:$('settingUrl').value,media:readMediaEditor(),freeze_after:Number($('settingFreeze').value),reveal:$('settingReveal').checked},sid);closeModal();toast('已保存，后台开始同步数据');if(sid!==S.active)await switchStation(sid);else await refresh();}
    catch(error){errorMessage(error);}finally{button.disabled=false;}
  };
}
function readMediaEditor() {
  return [...$('mediaEditor').querySelectorAll('.media-edit-row')].map(row=>({title:row.querySelector('[data-media-title]').value,url:row.querySelector('[data-media-url]').value}));
}
function renderMediaEditor(items) {
  $('mediaEditor').innerHTML=items.map((item,i)=>`<div class="media-edit-row"><div class="media-edit-fields"><input data-media-title aria-label="节目 ${i+1} 名称" maxlength="100" placeholder="节目名称（可选）" value="${esc(item.title)}"><input data-media-url aria-label="节目 ${i+1} 链接" placeholder="直播间号 / BV号 / B站链接" value="${esc(item.url)}" required></div><div class="media-edit-actions"><button type="button" data-media-edit="-1" data-index="${i}" ${i===0?'disabled':''} aria-label="上移节目 ${i+1}">↑</button><button type="button" data-media-edit="1" data-index="${i}" ${i===items.length-1?'disabled':''} aria-label="下移节目 ${i+1}">↓</button><button type="button" data-media-edit="delete" data-index="${i}" aria-label="删除节目 ${i+1}">×</button></div></div>`).join('')||'<p class="help">尚未添加播放内容。</p>';
}
function editNote(tid) {
  const team=board()?.teams.find(t=>t.id===tid);if(!team)return;
  openModal('给群友留个名字','TEAM NOTE',`<form id="noteForm" class="form-stack"><div class="help">${esc(team.organization)} · ${esc(team.name)} · ${esc(team.id)}</div><label class="field">群员名称 / 备注<input id="noteValue" maxlength="100" value="${esc(note(team))}" placeholder="例如 小明 / 群昵称" autofocus></label><label class="field">生效范围<select id="noteScope"><option value="local">仅当前浏览器</option>${S.token?'<option value="public">公共备注（所有访客）</option>':''}</select></label><div class="help">个人备注优先显示；清空个人备注后恢复公共备注。</div><div id="formError" class="form-error"></div><div class="modal-actions"><button type="button" data-close>取消</button><button class="primary" type="submit">保存备注</button></div></form>`);
  if(S.token && !S.local.notes[tid])$('noteScope').value='public';
  $('noteForm').onsubmit=async e=>{e.preventDefault();try{const value=$('noteValue').value.trim();if($('noteScope').value==='public'){const notes={...station().notes};if(value)notes[tid]=value;else delete notes[tid];await adminSave({source:S.source,notes});delete S.local.notes[tid];}else{if(value)S.local.notes[tid]=value;else delete S.local.notes[tid];}persist();closeModal();renderTable();renderQueue();toast('备注已保存');}catch(error){errorMessage(error);}};
}
function groups() {
  if(!board()){toast('榜单加载后可创建关注分组');return;}
  const pub=station().public_groups||[],local=S.local.groups||[];
  const item=(g,scope)=>`<div class="group-item"><strong>${esc(g.name)}</strong><small>${g.teams.length} 支队伍${scope==='public'&&station().default_group===g.id?' · 公共默认':''}</small><button data-group-edit="${esc(g.id)}" data-scope="${scope}">编辑</button><button data-group-delete="${esc(g.id)}" data-scope="${scope}" class="danger">删除</button></div>`;
  openModal('把熟悉的队伍聚在一起','FOLLOW YOUR TEAMS',`<div class="help">个人分组只保存在当前浏览器；公共分组由管理员维护，所有访客都能选择。</div><div class="group-heading"><h3>我的分组 · 当前浏览器</h3><button data-group-add="local">＋ 新建分组</button></div><div class="group-list">${local.map(g=>item(g,'local')).join('')||'<p class="help">还没有个人分组。也可以直接在榜单点 ☆ 使用「我的关注」。</p>'}</div><div class="group-heading"><h3>公共视图 · 所有人可见</h3><button data-group-add="public">${S.token?'＋ 新建公共分组':'管理员登录'}</button></div><div class="group-list">${pub.map(g=>`<div class="group-item"><strong>${esc(g.name)}</strong><small>${g.teams.length} 支队伍${station().default_group===g.id?' · 默认视图':''}</small><button data-group-view="${esc(g.id)}">查看</button>${S.token?`<button data-group-edit="${esc(g.id)}" data-scope="public">编辑</button><button data-group-delete="${esc(g.id)}" data-scope="public" class="danger">删除</button>`:''}</div>`).join('')||'<p class="help">暂时没有公共分组。</p>'}</div>${S.token?'<div class="modal-actions"><button id="resetPublicDefault">恢复全部队伍为公共默认视图</button></div>':''}`);
  $('modalBody').onclick=async e=>{
    const el=e.target.closest('button');if(!el)return;
    if(el.dataset.groupAdd){if(el.dataset.groupAdd==='public'&&!S.token){login(()=>editGroup('public'));return;}editGroup(el.dataset.groupAdd);}
    if(el.dataset.groupEdit)editGroup(el.dataset.scope,el.dataset.groupEdit);
    if(el.dataset.groupView){S.view='pub:'+el.dataset.groupView;persist();closeModal();renderViews();renderTable();renderQueue();}
    if(el.dataset.groupDelete)deleteGroup(el.dataset.scope,el.dataset.groupDelete);
    if(el.id==='resetPublicDefault'){try{await adminSave({source:S.source,default_group:'all'});groups();toast('公共默认视图已恢复为全部队伍');}catch(error){toast(error.message);}}
  };
}
function editGroup(scope,id='') {
  $('modalBody').onclick=null;
  const list=scope==='public'?station().public_groups:S.local.groups;
  const original=list.find(g=>g.id===id),selected=new Set(original?.teams || stars());
  openModal(original?'编辑关注分组':'新建关注分组',scope==='public'?'PUBLIC VIEW':'BROWSER ONLY',`<form id="groupForm"><label class="field">分组名称<input id="groupName" value="${esc(original?.name||'')}" maxlength="60" placeholder="例如 群友 / 大连理工 / 老队友" required></label><div class="group-heading"><h3>选择队伍</h3><span class="choice-count" id="choiceCount"></span></div><input class="picker-search" id="pickerSearch" placeholder="搜索学校、队伍、备注…" aria-label="搜索可选队伍"><div class="group-picker" id="groupPicker"></div>${scope==='public'?`<label class="check-field" style="margin-top:14px"><input type="checkbox" id="groupDefault" ${station().default_group===id?'checked':''}>设为公共默认视图（新访客首次打开时显示）</label>`:''}<div id="formError" class="form-error"></div><div class="modal-actions"><button type="button" id="cancelGroup">返回</button><button class="primary" type="submit">保存分组</button></div></form>`);
  const picker=()=>{
    const q=$('pickerSearch').value;
    $('choiceCount').textContent='已选 '+selected.size+' 支队伍';
    $('groupPicker').innerHTML=board().teams.filter(t=>matches(t,q)).map(t=>`<label class="pick-row"><input type="checkbox" data-pick="${esc(t.id)}" ${selected.has(t.id)?'checked':''}><span>${esc(note(t)?note(t)+' · ':'')}${esc(t.name)}<small>${esc(t.organization)} · ${esc(t.id)}</small></span><b>${t.solved} AC</b></label>`).join('')||'<div class="empty">没有匹配队伍</div>';
  };
  picker();$('pickerSearch').oninput=picker;$('groupPicker').onchange=e=>{const id=e.target.dataset.pick;if(!id)return;e.target.checked?selected.add(id):selected.delete(id);$('choiceCount').textContent='已选 '+selected.size+' 支队伍';};
  $('cancelGroup').onclick=groups;
  $('groupForm').onsubmit=async e=>{
    e.preventDefault();try{const g={id:id||newId(),name:$('groupName').value.trim(),teams:[...selected]};if(!g.name)throw Error('请填写分组名称');const next=list.filter(x=>x.id!==g.id).concat(g);if(scope==='public'){let def=$('groupDefault').checked?g.id:station().default_group===g.id?'all':station().default_group;await adminSave({source:S.source,public_groups:next,default_group:def});S.view='pub:'+g.id;}else{S.local.groups=next;S.view='local:'+g.id;}persist();closeModal();renderViews();renderTable();renderQueue();toast('分组已保存');}catch(error){errorMessage(error);}
  };
}
function deleteGroup(scope,id) {
  const list=scope==='public'?station().public_groups:S.local.groups,g=list.find(x=>x.id===id);if(!g)return;
  $('modalBody').onclick=null;
  openModal('删除分组','REMOVE GROUP',`<p class="help">删除「${esc(g.name)}」${scope==='public'?'将对所有访客生效':''}。队伍和备注会保留。</p><div class="modal-actions"><button id="cancelDelete">取消</button><button id="confirmDelete" class="danger">删除分组</button></div>`);
  $('cancelDelete').onclick=groups;$('confirmDelete').onclick=async()=>{try{if(scope==='public')await adminSave({source:S.source,public_groups:list.filter(x=>x.id!==id)});else S.local.groups=list.filter(x=>x.id!==id);if(S.view===(scope==='public'?'pub:':'local:')+id)S.view='all';persist();groups();renderViews();renderTable();renderQueue();}catch(error){toast(error.message);}};
}
$('stationTabs').onclick=e=>{const b=e.target.closest('[data-station]');if(b&&b.dataset.station!==S.active)switchStation(b.dataset.station);};
$('viewTabs').onclick=e=>{const b=e.target.closest('[data-view]');if(!b)return;S.view=b.dataset.view;persist();renderViews();renderTable();renderQueue();};
$('search').oninput=e=>{S.query=e.target.value.trim();renderTable();};
$('rankGroup').onchange=e=>{S.rankGroup=e.target.value;persist();renderTable();renderQueue();};
$('queueOnlyView').onchange=e=>{S.queueOnly=e.target.checked;persist();renderQueue();};
$('queuePause').onclick=()=>{S.paused=!S.paused;renderQueue();if(!S.paused)drainQueue();};
$('manageButton').onclick=()=>login();$('groupButton').onclick=groups;
$('refreshButton').onclick=async()=>{await refresh();toast('已读取服务器最新缓存');};
$('closeModal').onclick=closeModal;
$('modal').onclick=e=>{if(e.target===$('modal')){const r=$('modal').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeModal();}};
$('modal').addEventListener('close',()=>{$('modalBody').onclick=null;});
document.addEventListener('click',e=>{
  if(e.target.closest('[data-close]'))closeModal();
  const action=e.target.closest('[data-action]')?.dataset.action;
  if(action==='settings')login();
  if(action==='video'){if(!station()?.media?.length){toast('当前赛站尚未添加播放内容，可在「管理赛站」配置');}else{S.video=!S.video;const visibility=read(localStorage,'xcpc-media-visibility',{});visibility[S.active]=S.video;save(localStorage,'xcpc-media-visibility',visibility);renderBanner();}}
  const media=e.target.closest('[data-media-index]');if(media)selectMedia(Number(media.dataset.mediaIndex));
  const step=e.target.closest('[data-media-step]');if(step)selectMedia(Number($('videoPanel').dataset.index)+Number(step.dataset.mediaStep));
  const star=e.target.closest('[data-star]');
  if(star&&S.local){const id=star.dataset.star;S.local.stars=stars().includes(id)?stars().filter(x=>x!==id):[...stars(),id];persist();renderViews();renderTable();if(S.queueOnly)renderQueue();}
  const n=e.target.closest('[data-note]');if(n)editNote(n.dataset.note);
});
document.addEventListener('keydown',e=>{if(e.key==='/'&&!$('modal').open&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)){e.preventDefault();$('search').focus();}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
window.addEventListener('online',refresh);
window.addEventListener('storage',e=>{if(e.key===prefKey()){S.local=read(localStorage,prefKey(),S.local);render();}});
async function boot() {
  try { S.active=decodeURIComponent(location.hash.slice(1));await refreshCatalog();S.video=read(localStorage,'xcpc-media-visibility',{})[S.active]??true;await refresh(); }
  catch(error) { $('contestBanner').innerHTML='<div class="empty">服务暂不可用，稍后将自动重试。</div>'; }
  pollTimer=setInterval(async()=>{if(document.hidden)return;try{await refreshCatalog();}catch{}await refresh();},15000);
  setInterval(updateClock,1000);
}
boot();
