
/* ═══ Tevarn Mobile · real backend (no mock data) ═══ */
const AVCOLORS=[['#6d5df6','#b8b0ff'],['#00a8c0','#7ee7f5'],['#f6489b','#ffb3d9'],['#16a34a','#86efac'],['#d97706','#fcd34d']];
function drawAvt(cv,seed){
  const ctx=cv.getContext('2d'); let hash=0;
  for(let i=0;i<seed.length;i++) hash=(hash*31+seed.charCodeAt(i))>>>0;
  const [c1,c2]=AVCOLORS[hash%AVCOLORS.length];
  ctx.clearRect(0,0,10,10); ctx.fillStyle=c1; ctx.fillRect(0,0,10,10);
  ctx.fillStyle=c2;
  for(let y=1;y<9;y++) for(let x=1;x<5;x++){
    hash=(hash*1103515245+12345)>>>0;
    if(hash%5<2){ctx.fillRect(x,y,1,1); ctx.fillRect(8-x,y,1,1);}
  }
}

const $ = id => document.getElementById(id);
let MODE = 'local'; // PC link: remote=authenticated, local=not linked
let CHAT_MODE = 'local'; // chat surface: local=本机模型(default), remote=PC agent
let streaming = false;
let approvals = 0;
let STATE = null;
let SESSIONS = [];
let DEVICES = [];
let APPROVALS = { escalations: [], evolution: [] };
let ACTIVE_SESSION = null;
let ws = null;
let wsRetry = 0;
let streamBuf = { id: null, text: '' };
let notifyOn = true;

function toast(msg){
  const t=$('toast'); if(!t) return;
  t.textContent=msg; t.classList.add('show');
  clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('show'),2400);
}

async function api(path, opts={}){
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers||{}) },
    ...opts,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if(!res.ok){
    const err = (data && (data.error || data.detail)) || res.statusText;
    throw new Error(typeof err === 'string' ? err : JSON.stringify(err));
  }
  return data;
}

/* ═══ Theme ═══ */
function toggleTheme(){
  const ph=$('phone'); const dark=ph.getAttribute('data-theme')==='dark';
  ph.setAttribute('data-theme', dark?'light':'dark');
  syncThemeSeg(dark?'dark':'light');
}
function themeSeg(btn,mode){
  document.querySelectorAll('.tseg button').forEach(b=>b.classList.remove('act'));
  btn.classList.add('act');
  if(mode==='dark'&&$('phone').getAttribute('data-theme')!=='dark') toggleTheme();
  if(mode==='light'&&$('phone').getAttribute('data-theme')==='dark') toggleTheme();
  if(mode==='system'){
    const prefers = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    if(prefers && $('phone').getAttribute('data-theme')!=='dark') toggleTheme();
    if(!prefers && $('phone').getAttribute('data-theme')==='dark') toggleTheme();
    toast('已跟随系统主题');
  }
}
function syncThemeSeg(m){
  document.querySelectorAll('.tseg button').forEach(b=>{
    b.classList.toggle('act',(m==='dark'&&b.textContent==='深色')||(m==='light'&&b.textContent==='浅色'));
  });
}

/* ═══ Dynamic Island ═══ */
let islandTimer=null;
function islandBase(){
  if(!STATE) return;
  if(CHAT_MODE==='remote' && MODE==='remote'){
    const u = STATE.user?.username || STATE.user?.email || 'PC';
    const ms = STATE.runtime?.processes_live != null ? (STATE.runtime.processes_live+' 进程') : '在线';
    island('conn', `<b>远端</b>&nbsp;${esc(u)} · ${ms}`);
  } else {
    const lr = STATE?.local_llm_ready;
    const lm = STATE?.local_llm?.model || '';
    island('local', lr
      ? `<b>本机</b>&nbsp;${esc(lm).slice(0,16)}`
      : `<b>本机</b>&nbsp;未配置`);
  }
}
function island(kind, html, autoHide){
  const n=$('notch'), d=$('il-dot'), t=$('il-txt');
  if(!n) return;
  clearTimeout(islandTimer);
  n.classList.remove('live','big'); d.className='il-dot';
  if(!kind){ t.innerHTML=''; return; }
  n.classList.add(kind==='big'?'big':'live');
  if(kind==='stream') d.classList.add('cy');
  else if(kind==='alert') d.classList.add('pk');
  else if(kind==='local') d.classList.add('lo');
  else d.classList.add('on');
  t.innerHTML=html||'';
  if(autoHide) islandTimer=setTimeout(()=>islandBase(), autoHide);
}
function islandTap(){
  if($('notch').classList.contains('big')){ disconnect(); return; }
  const badge = approvals || 0;
  island('big', MODE==='remote'
    ? `<b>已连 PC</b>&nbsp;待办 ${badge}&nbsp;·&nbsp;再点断开`
    : `<b>本地模式</b>&nbsp;·&nbsp;再点尝试重连`, 4000);
}

function esc(s){
  return String(s??'').replace(/&/g,'&'+'amp;').replace(/</g,'&'+'lt;').replace(/>/g,'&'+'gt;').replace(/"/g,'&'+'quot;').replace(/'/g,'&'+'#39;');
}

/* ═══ Mode / connection chrome ═══ */
function applyModeUI(){
  const localModel = STATE?.local_llm?.model || '';
  const localReady = !!(STATE && STATE.local_llm_ready);
  const pcModel = STATE?.active_model || $('api-model')?.value || '—';

  // Force chat mode validity
  if(CHAT_MODE==='remote' && MODE!=='remote'){
    CHAT_MODE = 'local';
    localStorage.setItem('tevarn-chat-mode', 'local');
  }

  // Connection strip (PC link status, independent of chat surface)
  if(MODE==='remote'){
    $('conn-lamp').className='lamp on pulse';
    const name = STATE?.user?.username || STATE?.user?.email || 'PC';
    const live = STATE?.runtime?.processes_live;
    const lat = STATE?.backend_health?.status === 'ok' ? '在线' : '探测中';
    $('conn-name').innerHTML = 'PC 已连接 · ' + esc(name) +
      (CHAT_MODE==='remote'
        ? ' <span class="mode-pill rm">远端</span>'
        : ' <span class="mode-pill lo">本机</span>');
    $('conn-meta').textContent = (live!=null?`进程 ${live} · `:'') + lat;
    $('conn-go').textContent = '会话 →';
    $('rm-title').textContent = '已连接 · ' + (STATE?.base_url || '');
    $('rm-meta').textContent = `tevarn · ${STATE?.user?.email||''} · 设备 ${DEVICES.length}`;
    $('rm-state').textContent='ONLINE'; $('rm-state').className='badge';
    $('rm-disc').textContent='断开连接';
  } else {
    $('conn-lamp').className='lamp warn';
    $('conn-name').innerHTML = '未连 PC <span class="mode-pill lo">本机</span>';
    $('conn-meta').textContent = localReady ? ('模型 '+localModel) : '去「我的」配置 LLM';
    $('conn-go').textContent = '会话 →';
    $('rm-title').textContent='未连接';
    $('rm-meta').textContent='填写地址登录你的 PC';
    $('rm-state').textContent = localReady ? '本机就绪' : 'LOCAL';
    $('rm-state').className = 'badge am';
    $('rm-disc').textContent='连接 PC';
  }

  // Mode bar (primary dual-mode control)
  const ml = $('mode-local'), mr = $('mode-remote');
  if(ml && mr){
    ml.classList.toggle('act', CHAT_MODE==='local');
    mr.classList.toggle('act', CHAT_MODE==='remote');
    mr.disabled = MODE!=='remote';
    mr.style.opacity = MODE==='remote' ? '1' : '.45';
  }
  const snap = MODE_SNAP;
  if(CHAT_MODE==='remote'){
    $('chat-title').textContent = currentSessionTitle();
    $('chat-sub').textContent = snap?.subtitle || (ACTIVE_SESSION
      ? `远端 Agent · ${ACTIVE_SESSION.slice(0,8)} · ${pcModel}`
      : `远端 Agent · ${pcModel}`);
    $('inp').placeholder = snap?.placeholder || '给 PC Agent 发消息…';
  } else {
    $('chat-title').textContent = '本机对话';
    $('chat-sub').textContent = snap?.subtitle || (localReady
      ? (`本机模型 · ${localModel}`)
      : '我的 → LLM 设置（API Key）');
    $('inp').placeholder = snap?.placeholder || (localReady ? '本机模型对话…' : '配置本机模型，或切换远端…');
  }
  if($('mode-hint')){
    let h = snap?.reason || '';
    if(CHAT_MODE==='remote' && STATE && STATE.kernel_local===false && MODE==='remote'){
      h = (h?h+' · ':'') + '若无模型输出：请确认 PC 上 kernel-host 已启动';
    }
    $('mode-hint').textContent = h || (CHAT_MODE==='remote' ? '远端：使用 PC 工具链与会话' : '本机：直连 API 模型，不经 Agent');
  }
  // Media: camera/voice always; file attach prefers remote for PC workspace
  if($('attach-btn')){
    $('attach-btn').style.opacity = '1';
    $('attach-btn').title = '附件 / 相机 / 相册';
  }
  if($('cam-btn')) $('cam-btn').style.opacity = '1';
  if($('voice-btn')) $('voice-btn').style.opacity = '1';

  renderSugg();
  renderHist();
  if($('comp-model')){
    $('comp-model').textContent = (CHAT_MODE==='remote' ? pcModel : (localModel||'—')).toString().slice(0,28);
  }
  if($('comp-lamp')) $('comp-lamp').className = 'lamp ' + (MODE==='remote' ? 'on pulse' : 'warn');
  autogrow();
  if(!streaming) islandBase();
}

function currentSessionTitle(){
  const s = SESSIONS.find(x=>x.id===ACTIVE_SESSION);
  return s?.title || 'Tevarn 会话';
}

async function refreshState(){
  try {
    STATE = await api('/api/mobile/state');
    MODE = STATE.authenticated ? 'remote' : 'local';
    SESSIONS = STATE.sessions || [];
    DEVICES = STATE.devices || [];
    approvals = Number(STATE.approvals_pending||0);
    notifyOn = !!STATE.notify_approvals;
    if(STATE.active_session_id) ACTIVE_SESSION = STATE.active_session_id;
    // fill API form from real catalog if present
    if($('api-base') && STATE.base_url) $('api-base').value = STATE.base_url;
    if($('api-model') && STATE.active_model) $('api-model').value = STATE.active_model;
    syncBadge();
    applyModeUI();
    renderHist();
    renderDevs();
    if($('me-name')){
      $('me-name').textContent = STATE.user?.display_name || STATE.user?.username || STATE.user?.email || '未登录';
      $('me-meta').textContent = (STATE.authenticated?('已连接 · '+STATE.base_url):'未连接') + ' · 设备 ' + DEVICES.length;
    }
    if($('me-role')){
      if(STATE.authenticated){
        $('me-role').style.display='';
        $('me-role').textContent = STATE.user?.is_superuser ? 'ADMIN' : 'USER';
        $('me-role').className = 'badge' + (STATE.user?.is_superuser?' cy':'');
      } else { $('me-role').style.display='none'; }
    }
    if($('me-backend')){
      const bh = STATE.backend_health;
      $('me-backend').textContent = STATE.authenticated
        ? ((bh?.service||'tevarn') + (bh?.status?(' · '+bh.status):'') + ' · ' + (STATE.base_url||''))
        : '未连接';
    }
    if($('sw-notify')){
      $('sw-notify').classList.toggle('on', !!notifyOn);
    }
    updateCapabilities();
    await refreshModeSnap();
    await refreshApprovals();
    await refreshProcesses();
  } catch(e){
    console.error(e);
    toast('状态刷新失败: '+e.message);
  }
}

async function connectPC(auto){
  const base = ($('api-base')?.value || STATE?.base_url || 'http://127.0.0.1:8090').trim();
  const email = ($('api-email')?.value || '').trim();
  const password = ($('api-key')?.value || '').trim(); // reuse field: password or leave empty for auto
  try {
    toast('正在连接 '+base+' …');
    const body = { base_url: base, auto: !!auto || (!email && !password) };
    if(email) body.email = email;
    if(password) body.password = password;
    const r = await api('/api/mobile/connect', { method:'POST', body: JSON.stringify(body) });
    if(!r.ok) throw new Error(r.error||'connect failed');
    toast('已连接 · '+(r.user?.email||''));
    island('conn', '<b>已连接</b>&nbsp;PC', 2200);
    ensureWs();
    await refreshState();
    if(!ACTIVE_SESSION){
      await newChat(true);
    } else {
      await openSession(ACTIVE_SESSION);
      await loadMessages(ACTIVE_SESSION);
    }
  } catch(e){
    toast('连接失败: '+e.message);
    island('alert', '<b>连接失败</b>', 2400);
  }
}

async function disconnect(){
  if(MODE==='remote'){
    try { await api('/api/mobile/disconnect', { method:'POST', body:'{}' }); } catch{}
    if(ws){ try{ws.close();}catch{} ws=null; }
    MODE='local'; ACTIVE_SESSION=null;
    setChatMode('local');
    $('msgs').innerHTML = localWelcomeHtml();
    bindAvts();
    toast('已断开 · 降级为本地模式');
    island('alert','<b>已断连</b>&nbsp;本地模式',2600);
    await refreshState();
  } else {
    await connectPC(true);
  }
}

/* ═══ Navigation ═══ */
function goTab(name){
  document.querySelectorAll('.screen').forEach(s=>s.classList.remove('act'));
  $('s-'+name).classList.add('act');
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('act',t.dataset.tab===name));
  if(name==='approve'){ refreshApprovals(); refreshProcesses(); }
  if(name==='remote') refreshState();
  if(name==='me'){ refreshCatalogFields(); if(typeof loadLocalLlmForm==='function') loadLocalLlmForm(); }
}
function drawer(open){ $('drawer').classList.toggle('open', !!open); }

/* ═══ Approvals (live) ═══ */
async function refreshApprovals(){
  if(MODE!=='remote'){
    $('ap-list').innerHTML = '<div class="mut" style="text-align:center;padding:20px">连接 PC 后显示真实审批</div>';
    setSegBadge('ap-b1', 0); setSegBadge('ap-b2', 0);
    return;
  }
  try {
    APPROVALS = await api('/api/mobile/approvals');
    // only keep pending-like evolution items
    APPROVALS.evolution = (APPROVALS.evolution||[]).filter(p=>{
      const s = (p.status||'pending').toLowerCase();
      return !s || s==='pending' || s==='open' || s==='proposed';
    });
    APPROVALS.escalations = (APPROVALS.escalations||[]).filter(a=>{
      // reject non-item shapes (missing id and no capability)
      return !!(a && (a.id || a.confirm_id || a.request_id || a.capability || a.reason || a.command));
    });
    renderAppr();
    const n1 = (APPROVALS.escalations||[]).length;
    const n2 = (APPROVALS.evolution||[]).length;
    setSegBadge('ap-b1', n1);
    setSegBadge('ap-b2', n2);
    approvals = n1 + n2;
    syncBadge();
  } catch(e){
    $('ap-list').innerHTML = `<div class="mut" style="text-align:center;padding:20px">${esc(e.message)}</div>`;
  }
}
function setSegBadge(id, n){
  const el = $(id); if(!el) return;
  el.textContent = String(n);
  el.style.display = n>0 ? '' : 'none';
}
let aptab=1;
function apTab(n){ aptab=n; $('ap-t1').classList.toggle('act',n===1); $('ap-t2').classList.toggle('act',n===2); renderAppr(); }

function renderAppr(){
  const list = aptab===1 ? (APPROVALS.escalations||[]) : (APPROVALS.evolution||[]);
  if(!list.length){
    $('ap-list').innerHTML='<div class="mut" style="text-align:center;padding:20px">本类暂无待办</div>';
    return;
  }
  $('ap-list').innerHTML = list.map((a,i)=>{
    const id = a.id || a.confirm_id || a.request_id || String(i);
    const tag = aptab===1 ? (a.capability||a.kind||'权限') : '进化';
    const cls = aptab===1 ? 'am' : 'cy';
    const ag = a.agent_name || a.agent || a.source || '内核';
    const title = a.title || a.reason || a.summary || a.command || id;
    const desc = a.reason || a.detail || a.summary || a.description || '';
    const kind = aptab===1 ? 'escalation' : 'evolution';
    return `<div class="appr-card" id="ap-${esc(id)}">
      <div class="top"><span class="badge ${cls}">${esc(String(tag).toUpperCase())}</span><span class="ag">${esc(ag)}</span><span class="mut" style="margin-left:auto">${esc(String(title).slice(0,40))}</span></div>
      <div class="ds">${esc(desc)}</div>
      <div class="ops">
        <button class="btn sm pri" style="flex:1" onclick="decide('${esc(id)}',1,'${kind}')">通过</button>
        <button class="btn sm" style="flex:1" onclick="decide('${esc(id)}',0,'${kind}')">拒绝</button>
      </div>
    </div>`;
  }).join('');
}

async function decide(id, ok, kind){
  kind = kind || 'escalation';
  try {
    const r = await api('/api/mobile/approvals/'+encodeURIComponent(id)+'/decide', {
      method:'POST', body: JSON.stringify({ approved: !!ok, kind, scope: ok?'once':'deny' })
    });
    if(!r.ok) throw new Error(r.error||'failed');
    const c=$('ap-'+id);
    if(c){
      c.classList.add('done');
      c.querySelector('.ops').innerHTML=`<span class="badge ${ok?'gn':'rd'}">${ok?'PASSED':'REJECTED'}</span><span class="mut" style="align-self:center">已同步回 PC 内核</span>`;
    }
    approvals=Math.max(0,approvals-1); syncBadge();
    toast(ok?'已通过并下发':'已拒绝');
    island(ok?'conn':'alert', ok?'<b>已通过</b>':'<b>已拒绝</b>', 2200);
    await refreshApprovals();
  } catch(e){ toast('审批失败: '+e.message); }
}

async function approveAll(){
  const list = aptab===1 ? (APPROVALS.escalations||[]) : (APPROVALS.evolution||[]);
  for(const a of list){
    const id = a.id || a.confirm_id;
    if(id) await decide(id, 1, aptab===1?'escalation':'evolution');
  }
}
function syncBadge(){
  const b=$('tab-bdg'); if(!b) return;
  b.textContent=approvals;
  b.style.display=approvals>0?'block':'none';
}


/* ═══ Processes (live from kernel) ═══ */
let PROCESSES = [];
async function refreshProcesses(){
  const box = $('proc-list');
  if(!box) return;
  if(MODE!=='remote'){
    box.innerHTML = '<div class="mut" style="text-align:center;padding:14px">连接 PC 后显示真实进程</div>';
    if($('proc-count')) $('proc-count').textContent = '';
    return;
  }
  try {
    const r = await api('/api/mobile/processes');
    PROCESSES = Array.isArray(r.processes) ? r.processes : [];
    renderProcesses();
  } catch(e){
    box.innerHTML = `<div class="mut" style="text-align:center;padding:14px">${esc(e.message)}</div>`;
  }
}
function renderProcesses(){
  const box = $('proc-list'); if(!box) return;
  if($('proc-count')) $('proc-count').textContent = PROCESSES.length ? `(${PROCESSES.length})` : '';
  if(!PROCESSES.length){
    box.innerHTML = '<div class="mut" style="text-align:center;padding:14px">当前无运行中进程</div>';
    return;
  }
  box.innerHTML = PROCESSES.map(p=>{
    const id = p.id || p.process_id || '';
    const name = p.goal || p.title || p.identity || p.identity_key || id;
    const st = (p.state || p.status || 'unknown').toLowerCase();
    const used = Number(p.tokens_used||0), bud = Number(p.token_budget||0);
    const pct = bud>0 ? Math.min(100, Math.round(used/bud*100)) : 0;
    const lamp = st.includes('run')||st==='active' ? 'busy pulse' : st.includes('wait')||st.includes('suspend')||st.includes('block') ? 'warn' : st.includes('queue') ? 'on' : 'off';
    const barColor = pct>=80 ? 'background:var(--amber)' : '';
    const canStop = !['completed','failed','killed','interrupted','exited','done','error'].includes(st);
    const canResume = st.includes('suspend') || st.includes('pause');
    return `<div class="proc-row" data-pid="${esc(id)}">
      <span class="lamp ${lamp}"></span>
      <div class="pn"><div class="p1">${esc(String(name).slice(0,48))}</div>
        <div class="p2">${esc(id.slice(0,12))} · ${esc(st)}${bud?` · 预算 ${pct}%`:''}</div></div>
      <div class="pbar"><i style="width:${pct}%;${barColor}"></i></div>
      ${canResume?`<button class="btn sm cy" onclick="resumeProc('${esc(id)}')">恢复</button>`:''}
      ${canStop?`<button class="btn sm dan" onclick="stopProc('${esc(id)}')">停止</button>`:''}
    </div>`;
  }).join('');
}
async function stopProc(id){
  try {
    const r = await api('/api/mobile/processes/'+encodeURIComponent(id)+'/stop', { method:'POST', body:'{}' });
    if(!r.ok) throw new Error(r.error||'stop failed');
    toast('已停止 '+id.slice(0,8));
    island('alert','<b>已停止</b>',2000);
    await refreshProcesses();
  } catch(e){ toast('停止失败: '+e.message); }
}
async function resumeProc(id){
  try {
    const r = await api('/api/mobile/processes/'+encodeURIComponent(id)+'/resume', { method:'POST', body:'{}' });
    if(!r.ok) throw new Error(r.error||'resume failed');
    toast('已恢复 '+id.slice(0,8));
    await refreshProcesses();
  } catch(e){ toast('恢复失败: '+e.message); }
}

async function toggleNotify(el){
  el.classList.toggle('on');
  notifyOn = el.classList.contains('on');
  try { await api('/api/mobile/notify',{method:'POST',body:JSON.stringify({enabled:notifyOn})}); } catch{}
  toast('审批提醒已'+(notifyOn?'开启':'关闭'));
}

function updateCapabilities(){
  const on = MODE==='remote';
  const set = (id, cls) => { const e=$(id); if(e) e.className='lamp '+cls; };
  set('cap-chat', on?'on':'off');
  set('cap-appr', on?'on':'off');
  set('cap-proc', on?'on':'off');
  set('cap-dev', on?(DEVICES.length?'on':'warn'):'off');
  set('cap-file', on?'on':'off');
}

/* ═══ Devices ═══ */
function renderDevs(){
  if(!DEVICES.length){
    $('dev-list').innerHTML='<div class="mut" style="padding:12px">暂无设备 · 手动配对 L1 agent</div>';
    return;
  }
  const curBase = (STATE?.base_url||'').replace(/\/$/,'');
  $('dev-list').innerHTML = DEVICES.map(d=>{
    const on = (d.status||'').toLowerCase()!=='offline';
    const host = d.config?.host || d.config?.address || '';
    const meta = host ? `${host}${d.config?.port?':'+d.config.port:''} · ${d.status||''}` : (d.status||'');
    const isCur = curBase.includes(host) && host;
    return `<div class="dev">
      <span class="lamp ${on?(isCur?'on pulse':'busy'):'off'}"></span>
      <div class="di"><div class="dn">${esc(d.name)} ${isCur?'<span class="badge">当前</span>':''}</div><div class="dm">${esc(meta)}</div></div>
      ${isCur?'<button class="btn sm" disabled>已连接</button>'
        : on?`<button class="btn sm pri" onclick="pingDevice('${esc(d.id)}')">心跳</button>`
        :`<button class="btn sm" onclick="heartbeatDevice('${esc(d.id)}')">探测</button>`}
    </div>`;
  }).join('');
}
async function pingDevice(id){
  try {
    const r = await api('/api/mobile/devices/'+id+'/ping', { method:'POST', body:'{}' });
    toast(r.ok ? 'PONG · 设备可达' : ('失败: '+(r.error||'')));
    island('conn','<b>PONG</b>',2000);
    await refreshState();
  } catch(e){ toast('ping 失败: '+e.message); }
}
async function heartbeatDevice(id){
  try {
    await api('/api/mobile/devices/'+id+'/heartbeat', { method:'POST', body:'{}' });
    toast('已发送心跳');
    await refreshState();
  } catch(e){ toast(e.message); }
}
async function pairFromForm(){
  const name = prompt('设备名称', 'L1 Agent');
  if(!name) return;
  const host = prompt('L1 agent host', '127.0.0.1');
  if(!host) return;
  const port = Number(prompt('端口', '19876')||'19876');
  const token = prompt('配对 token（≥8 字符）');
  if(!token || token.length<8){ toast('token 太短'); return; }
  try {
    const r = await api('/api/mobile/devices/pair', {
      method:'POST', body: JSON.stringify({ name, host, port, token })
    });
    if(!r.ok) throw new Error(r.error||'pair failed');
    toast('配对成功：'+r.device?.name);
    await refreshState();
  } catch(e){ toast('配对失败: '+e.message); }
}

/* ═══ History drawer · 本机默认 / 远端在侧栏 ═══ */
function renderHist(){
  const localReady = !!(STATE && STATE.local_llm_ready);
  const localModel = STATE?.local_llm?.model || '未配置';
  let html = '';

  // 本机（默认）
  html += `<div class="dsect">本机对话
    <button type="button" class="dact" onclick="enterLocalChat(true)">新建</button>
  </div>`;
  html += `<div class="hitem ${CHAT_MODE==='local'?'act':''}" onclick="enterLocalChat(false)">
    <div class="t1">当前本机会话 <span class="mode-tag lo">本机</span></div>
    <div class="t2"><span class="lamp ${localReady?'on':'warn'}" style="width:6px;height:6px"></span>
      ${localReady ? esc(localModel) : '去「我的」配置 LLM'}
    </div>
  </div>`;

  // 远端
  html += `<div class="dsect">远端 Agent
    ${MODE==='remote' ? '<button type="button" class="dact" onclick="enterRemoteNew()">新建</button>' : ''}
  </div>`;
  if(MODE!=='remote'){
    html += `<div class="mut" style="padding:8px 10px 14px;line-height:1.6">连接 PC 后，在此打开远端会话（工具 / 审批 / 进程）。
      <div style="margin-top:8px"><button class="btn sm" onclick="drawer(0);goTab('remote')">去连接 PC</button></div>
    </div>`;
  } else if(!SESSIONS.length){
    html += `<div class="mut" style="padding:8px 10px">暂无远端会话 · 点「新建」开始</div>`;
  } else {
    html += SESSIONS.map(h=>`
      <div class="hitem ${CHAT_MODE==='remote'&&h.id===ACTIVE_SESSION?'act':''}" onclick="enterRemoteSession('${esc(h.id)}')">
        <div class="t1">${esc(h.title||h.id)} <span class="mode-tag rm">远端</span></div>
        <div class="t2"><span class="lamp on" style="width:6px;height:6px"></span>${esc((h.updated_at||h.created_at||'').toString().slice(0,19))}</div>
      </div>`).join('');
  }
  $('hist').innerHTML = html;
}

function setChatMode(mode){
  CHAT_MODE = mode;
  localStorage.setItem('tevarn-chat-mode', mode);
}

async function enterLocalChat(reset){
  setChatMode('local');
  drawer(0);
  if(reset){
    try { await api('/api/mobile/local/history', { method:'POST', body:'{}' }); } catch{}
  }
  if(reset || !$('msgs').children.length){
    $('msgs').innerHTML = localWelcomeHtml();
    bindAvts();
  } else {
    // reload local history
    await restoreLocalHistory();
  }
  applyModeUI();
  if(reset) toast('已新建本机对话');
  else toast('本机对话');
}

async function enterRemoteSession(id){
  if(MODE!=='remote'){ toast('请先连接 PC'); goTab('remote'); return; }
  setChatMode('remote');
  drawer(0);
  ACTIVE_SESSION = id;
  await openSession(id);
  await loadMessages(id);
  applyModeUI();
  toast('远端会话');
}

async function enterRemoteNew(){
  if(MODE!=='remote'){ toast('请先连接 PC'); goTab('remote'); return; }
  setChatMode('remote');
  drawer(0);
  try {
    const r = await api('/api/mobile/sessions', { method:'POST', body:'{}' });
    if(!r.ok) throw new Error(r.error||'create failed');
    ACTIVE_SESSION = r.session.id;
    ensureWs();
    wsSend({ type:'open_session', session_id: ACTIVE_SESSION });
    $('msgs').innerHTML = emptyRemoteWelcome();
    bindAvts();
    await refreshState();
    applyModeUI();
    toast('已新建远端会话');
  } catch(e){ toast('新建失败: '+e.message); }
}

async function switchSession(id){
  return enterRemoteSession(id);
}



async function restoreLocalHistory(){
  if(CHAT_MODE!=='local') return;
  try {
    const r = await api('/api/mobile/local/history');
    const msgs = r.messages || [];
    if(!msgs.length){
      $('msgs').innerHTML = localWelcomeHtml();
      bindAvts();
      return;
    }
    $('msgs').innerHTML = msgs.map(m=>{
      if(m.role==='user') return `<div class="mrow me"><div class="bub">${esc(m.content)}</div></div>`;
      return `<div class="mrow"><canvas class="avt" width="10" height="10" data-avt="本地"></canvas>
        <div class="bub"><div class="who">本机 · <span class="num">LLM</span></div>
        <div>${md(String(m.content||''))}</div></div></div>`;
    }).join('');
    bindAvts(); scrollBottom();
  } catch(e){ console.warn(e); }
}

/* ═══ 附件 / 相机 / 语音（真实设备 API，无 mock） ═══ */
let PENDING_FILES = [];
let PENDING_META = []; // {kind, localId?, pcFile?}

function openMediaSheet(){
  closeMediaSheet();
  const sh = $('media-sheet');
  if(sh) sh.classList.add('show');
  setTimeout(()=> document.addEventListener('click', sheetOutside, { once:true }), 0);
}
function sheetOutside(e){
  const sh = $('media-sheet');
  if(sh && !sh.contains(e.target) && e.target!==$('attach-btn')) closeMediaSheet();
}
function closeMediaSheet(){ $('media-sheet')?.classList.remove('show'); }

function sheetPickFile(){ closeMediaSheet(); pickAttach(); }
function sheetCamera(){ closeMediaSheet(); openCamera(); }
function sheetGallery(){
  closeMediaSheet();
  const gi = $('gallery-input');
  if(!gi) return;
  gi.value = '';
  gi.onchange = () => {
    const f = gi.files?.[0];
    if(f) addCapturedFile(f, 'image');
  };
  gi.click();
}

function pickAttach(){
  const fi = $('file-input');
  if(!fi) return;
  fi.value = '';
  fi.onchange = () => {
    const files = [...(fi.files || [])];
    if(!files.length) return;
    for(const f of files) PENDING_FILES.push(f);
    renderAttachChips();
    toast('已添加 '+files.length+' 个附件');
  };
  fi.click();
}

async function addCapturedFile(file, kind){
  PENDING_FILES.push(file);
  renderAttachChips();
  // Persist via pure-Rust media store (+ PC upload when connected)
  try {
    const fd = new FormData();
    fd.append('kind', kind || 'file');
    fd.append('file', file, file.name || ('capture.'+(kind==='audio'?'webm':'jpg')));
    const r = await fetch('/api/mobile/media', { method:'POST', body: fd });
    const j = await r.json().catch(()=>({}));
    if(j.ok && j.item){
      PENDING_META.push({ kind, localId: j.item.id, pcFile: j.pc_file || null });
      toast(kind==='audio' ? '语音已就绪' : (kind==='image' ? '照片已就绪' : '附件已就绪'));
    } else {
      toast('已加入待发送');
    }
  } catch {
    toast('已加入待发送');
  }
  autogrow();
}
function renderAttachChips(){
  const box = $('attach-chips');
  if(!box) return;
  if(!PENDING_FILES.length){ box.innerHTML=''; box.classList.remove('has'); return; }
  box.classList.add('has');
  box.innerHTML = PENDING_FILES.map((f,i)=>`
    <span class="chip-file" title="${esc(f.name)}">${esc(f.name.length>18?f.name.slice(0,16)+'…':f.name)}
      <button type="button" aria-label="移除" onclick="removeAttach(${i})">×</button>
    </span>`).join('');
}
function removeAttach(i){
  PENDING_FILES.splice(i,1);
  renderAttachChips();
}

/* ═══ Chat ═══ */
function localWelcomeHtml(){
  const ready = !!(STATE && STATE.local_llm_ready);
  const model = STATE?.local_llm?.model || '';
  if(ready){
    return `<div class="mrow"><canvas class="avt" width="10" height="10" data-avt="本地"></canvas>
      <div class="bub"><div class="who">本机 · <span class="num">${esc(model)}</span></div>
      <p>默认本机模型对话。打开左侧栏可进入<strong>远端 Agent</strong>会话。</p></div></div>`;
  }
  return `<div class="mrow"><canvas class="avt" width="10" height="10" data-avt="本地"></canvas>
    <div class="bub"><div class="who">本机 · <span class="num">未配置</span></div>
    <p>本机对话需要 <b>API Key 供应商</b>（OpenAI / Ollama 等）。ChatGPT OAuth 只能用于<strong>远端 Agent</strong>。</p>
    <p class="mut">左侧栏 → 远端 Agent 使用 PC 工具链；或在 LLM 设置里填 Key 做本机直连。</p></div></div>`;
}
function emptyRemoteWelcome(){
  return `<div class="mrow"><canvas class="avt" width="10" height="10" data-avt="sys"></canvas>
    <div class="bub"><div class="who">远端 Agent · <span class="num">ready</span></div>
    <p>当前为 PC 上的 agent 会话：支持工具、审批与附件。返回本机请打开左侧栏。</p></div></div>`;
}

function renderSugg(){
  const remote = ['今日审批待办','当前有哪些会话','运行时进程状态','帮我总结工作区'];
  const local = ['用一句话介绍你自己','帮我写一段 Rust hello world','解释什么是哈希链','如何连接办公室 PC'];
  const list = CHAT_MODE==='remote' ? remote : local;
  if($('sugg')) $('sugg').innerHTML = list.map(s=>`<div class="chip" onclick="quickAsk(${JSON.stringify(s)})">${esc(s)}</div>`).join('');
}

function bindAvts(){ document.querySelectorAll('canvas.avt').forEach(cv=>{ if(!cv._done){ drawAvt(cv, cv.dataset.avt||'x'); cv._done=true; } }); }

async function newChat(silent){
  if(CHAT_MODE==='remote'){
    await enterRemoteNew();
    return;
  }
  await enterLocalChat(true);
  if(silent){/* no toast already in enterLocalChat when reset */}
}

async function openSession(id){
  ACTIVE_SESSION = id;
  ensureWs();
  try {
    await api('/api/mobile/sessions/'+id+'/open', { method:'POST', body:'{}' });
  } catch(e){ console.warn(e); }
  wsSend({ type:'open_session', session_id: id });
}

async function loadMessages(id){
  try {
    const r = await api('/api/mobile/sessions/'+id+'/messages?limit=100');
    const msgs = r.messages || [];
    if(!msgs.length){ $('msgs').innerHTML = emptyRemoteWelcome(); bindAvts(); return; }
    $('msgs').innerHTML = msgs.map(m=>{
      const me = m.role==='user' || m.role==='human';
      if(me) return `<div class="mrow me"><div class="bub">${esc(m.content)}</div></div>`;
      return `<div class="mrow"><canvas class="avt" width="10" height="10" data-avt="${esc(m.role)}"></canvas>
        <div class="bub"><div class="who">${esc(m.role)} · <span class="num">${esc((m.created_at||'').toString().slice(11,19))}</span></div>
        <div>${md(String(m.content||''))}</div></div></div>`;
    }).join('');
    bindAvts();
    scrollBottom();
  } catch(e){ toast('加载消息失败: '+e.message); }
}

function quickAsk(s){ $('inp').value=s; autogrow(); onSend(); }
function syncSendIcon(){
  const send = $('sendico'), stop = $('stopico'), btn = $('sendbtn');
  if(!btn) return;
  if(streaming){
    btn.classList.add('stop');
    if(send) send.style.display='none';
    if(stop) stop.style.display='';
  } else {
    btn.classList.remove('stop');
    if(send) send.style.display='';
    if(stop) stop.style.display='none';
  }
}
function pcAgentReady(){
  return MODE==='remote' && !!(STATE && STATE.authenticated);
}
function localModelReady(){
  return !!(STATE && STATE.local_llm_ready);
}
let MODE_SNAP = null; // from pure-Rust /api/mobile/mode
async function refreshModeSnap(){
  try {
    const r = await api('/api/mobile/mode', {
      method:'POST',
      body: JSON.stringify({ surface: CHAT_MODE==='remote' ? 'remote' : 'local' }),
    });
    if(r.ok && r.mode) MODE_SNAP = r.mode;
  } catch(e){ console.warn('mode', e); }
  return MODE_SNAP;
}
/** 严格按当前 CHAT_MODE 选通道（与 Rust ModeSnapshot 一致） */
function resolveSendPath(){
  if(MODE_SNAP && MODE_SNAP.send_path) return MODE_SNAP.send_path;
  if(CHAT_MODE==='remote') return pcAgentReady() ? 'remote' : null;
  return localModelReady() ? 'local' : null;
}
function canChat(){
  if(MODE_SNAP) return !!MODE_SNAP.can_send;
  return resolveSendPath() !== null;
}
async function switchSurface(surface){
  if(surface==='remote'){
    if(!pcAgentReady()){
      toast('远端 Agent 需先连接 PC');
      goTab('remote');
      return;
    }
    setChatMode('remote');
    if(!ACTIVE_SESSION){
      await ensurePcAgentReady({ silent: true });
    } else {
      try { await openSession(ACTIVE_SESSION); await loadMessages(ACTIVE_SESSION); } catch{}
    }
  } else {
    setChatMode('local');
    await restoreLocalHistory();
    if(!$('msgs').children.length){
      $('msgs').innerHTML = localWelcomeHtml();
      bindAvts();
    }
  }
  await refreshModeSnap();
  applyModeUI();
  toast(surface==='remote' ? '已切换 · 远端 Agent' : '已切换 · 本机对话');
}
/** OAuth / 应用 PC 模型后：切到远端会话并确保有 session，发送键可点 */
async function ensurePcAgentReady(opts){
  const silent = !!(opts && opts.silent);
  if(!pcAgentReady()) return false;
  setChatMode('remote');
  try {
    if(!ACTIVE_SESSION){
      const r = await api('/api/mobile/sessions', { method:'POST', body:'{}' });
      if(r.ok && r.session?.id){
        ACTIVE_SESSION = r.session.id;
      } else if(SESSIONS[0]?.id){
        ACTIVE_SESSION = SESSIONS[0].id;
      }
    }
    if(ACTIVE_SESSION){
      ensureWs();
      try { await openSession(ACTIVE_SESSION); } catch{}
      // only rewrite msgs if empty / still welcome
      const hasUser = !!$('msgs')?.querySelector?.('.mrow.me');
      if(!hasUser){
        try { await loadMessages(ACTIVE_SESSION); } catch{
          $('msgs').innerHTML = emptyRemoteWelcome();
          bindAvts();
        }
      }
    } else {
      $('msgs').innerHTML = emptyRemoteWelcome();
      bindAvts();
    }
  } catch(e){
    console.warn(e);
  }
  await refreshModeSnap();
  applyModeUI();
  autogrow();
  if(!silent) toast('已进入远端 Agent · 可直接发送');
  return true;
}
function autogrow(){
  const t=$('inp'); if(!t) return;
  t.style.height='auto'; t.style.height=Math.min(120,t.scrollHeight)+'px';
  const can = !!t.value.trim() && canChat() && !streaming;
  if($('sendbtn')) $('sendbtn').disabled = !(can || streaming);
  syncSendIcon();
}

async function uploadPendingFiles(){
  const out = [];
  for(const f of PENDING_FILES){
    const fd = new FormData();
    fd.append('file', f, f.name);
    const r = await fetch('/api/mobile/upload', { method:'POST', body: fd });
    const j = await r.json().catch(()=>({}));
    if(!r.ok || !j.ok) throw new Error(j.error || ('上传失败: '+f.name));
    const file = j.file || j;
    out.push({
      filename: file.filename || f.name,
      url: file.url,
      type: file.type || f.type || '',
      text_content: file.text_content,
    });
  }
  return out;
}

let LOCAL_STREAM_CTRL = null;


/* ═══ 语音（MediaRecorder 真录音） ═══ */
let REC = null, REC_CHUNKS = [], REC_STREAM = null, REC_START = 0;
function voiceDown(e){
  e.preventDefault();
  startVoice();
}
function voiceUp(e){
  e.preventDefault();
  stopVoice(true);
}
function voiceCancel(e){
  if(REC) stopVoice(false);
}
async function startVoice(){
  if(REC) return;
  if(!navigator.mediaDevices?.getUserMedia){
    toast('当前环境不支持麦克风');
    return;
  }
  try {
    REC_STREAM = await navigator.mediaDevices.getUserMedia({ audio: true });
    REC_CHUNKS = [];
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : (MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '');
    REC = mime ? new MediaRecorder(REC_STREAM, { mimeType: mime }) : new MediaRecorder(REC_STREAM);
    REC.ondataavailable = (ev) => { if(ev.data && ev.data.size) REC_CHUNKS.push(ev.data); };
    REC.start(100);
    REC_START = Date.now();
    $('voice-btn')?.classList.add('rec');
    const w = $('rec-wave'); if(w){ w.classList.add('on'); w.textContent = '● 正在录音… 松开发送'; }
    island('stream', '<b>录音</b>&nbsp;中');
  } catch(err){
    toast('无法打开麦克风：'+(err.message||'权限被拒绝'));
    cleanupVoice();
  }
}
function cleanupVoice(){
  try { REC_STREAM?.getTracks()?.forEach(t=>t.stop()); } catch{}
  REC = null; REC_STREAM = null; REC_CHUNKS = [];
  $('voice-btn')?.classList.remove('rec');
  $('rec-wave')?.classList.remove('on');
}
async function stopVoice(keep){
  if(!REC) return;
  const rec = REC;
  const started = REC_START;
  await new Promise((resolve) => {
    rec.onstop = resolve;
    try { rec.stop(); } catch { resolve(); }
  });
  const chunks = REC_CHUNKS.slice();
  cleanupVoice();
  if(!keep) return;
  const ms = Date.now() - started;
  if(ms < 400){ toast('录音太短'); islandBase(); return; }
  const blob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
  if(blob.size < 200){ toast('未采到有效音频'); islandBase(); return; }
  const file = new File([blob], `voice_${Date.now()}.webm`, { type: blob.type || 'audio/webm' });
  await addCapturedFile(file, 'audio');
  // Also try browser STT into input when available (real Web Speech API)
  trySpeechToText(blob).catch(()=>{});
  islandBase();
}
async function trySpeechToText(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR) return; // optional enhancement; audio file already attached
  // Note: live STT needs mic stream; we already released. Skip if unavailable.
}

/* ═══ 相机（getUserMedia 真摄像头） ═══ */
let CAM_STREAM = null;
async function openCamera(){
  if(!navigator.mediaDevices?.getUserMedia){
    toast('当前环境不支持相机');
    return;
  }
  const ov = $('cam-overlay');
  const vid = $('cam-video');
  if(!ov || !vid) return;
  try {
    CAM_STREAM = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    vid.srcObject = CAM_STREAM;
    await vid.play();
    ov.classList.add('show');
    island('stream', '<b>相机</b>&nbsp;预览');
  } catch(err){
    toast('无法打开相机：'+(err.message||'权限被拒绝'));
    closeCamera();
  }
}
function closeCamera(){
  try { CAM_STREAM?.getTracks()?.forEach(t=>t.stop()); } catch{}
  CAM_STREAM = null;
  const vid = $('cam-video');
  if(vid) vid.srcObject = null;
  $('cam-overlay')?.classList.remove('show');
  islandBase();
}
async function capturePhoto(){
  const vid = $('cam-video');
  const canvas = $('cam-canvas');
  if(!vid || !canvas || !CAM_STREAM) return;
  const w = vid.videoWidth || 1280;
  const h = vid.videoHeight || 720;
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(vid, 0, 0, w, h);
  const blob = await new Promise((res) => canvas.toBlob(res, 'image/jpeg', 0.92));
  if(!blob){ toast('拍照失败'); return; }
  const file = new File([blob], `photo_${Date.now()}.jpg`, { type: 'image/jpeg' });
  closeCamera();
  await addCapturedFile(file, 'image');
}

async function onSend(){
  const t=$('inp'); const txt=t.value.trim();
  if(!txt && !PENDING_FILES.length) return;
  if(streaming){ sendOrStop(); return; }

  let path = resolveSendPath();
  if(!path){
    if(CHAT_MODE==='local'){
      toast('本机模型未就绪：请在「我的 → LLM 设置」配置 API Key，或侧栏切换到远端 Agent');
      goTab('me');
    } else {
      toast('请先连接 PC');
      goTab('remote');
    }
    return;
  }
  if(path==='remote'){
    if(!ACTIVE_SESSION){
      await ensurePcAgentReady({ silent: true });
    }
    return onSendRemote(txt);
  }
  // 本机：图片/语音作为上下文说明附在消息中（真实文件已存 media store）
  if(PENDING_FILES.length && CHAT_MODE==='local'){
    const names = PENDING_FILES.map(f=>f.name).join('、');
    const note = txt
      ? (txt + '\n\n[附件: '+names+']')
      : ('请查看我上传的附件：'+names);
    // clear after composing
    const filesSnap = PENDING_FILES.slice();
    PENDING_FILES = []; renderAttachChips();
    // if only media, still send note to local LLM
    return onSendLocal(note);
  }
  return onSendLocal(txt);
}

async function onSendRemote(txt){
  const filesSnap = PENDING_FILES.slice();
  let attachments = [];
  if(filesSnap.length){
    island('stream', '<b>上传中</b>&nbsp;'+filesSnap.length+' 个文件');
    try {
      attachments = await uploadPendingFiles();
    } catch(e){
      toast(e.message);
      island('alert','<b>上传失败</b>',2200);
      return;
    }
  }
  const chipHtml = attachments.length
    ? `<div class="mut" style="margin-top:6px;font-size:11px">附件 ${attachments.map(a=>esc(a.filename)).join(' · ')}</div>`
    : '';
  $('msgs').insertAdjacentHTML('beforeend', `<div class="mrow me"><div class="bub">${esc(txt)||'(附件)'}${chipHtml}</div></div>`);
  $('inp').value=''; PENDING_FILES=[]; renderAttachChips(); autogrow(); scrollBottom();
  beginStreamUi('remote');
  ensureWs();
  const payload = { type:'user_input', content: txt, attachments, mode:'default' };
  try {
    if(!ACTIVE_SESSION){
      const r = await api('/api/mobile/sessions',{method:'POST',body:'{}'});
      if(!r.ok) throw new Error(r.error||'session create failed');
      ACTIVE_SESSION=r.session.id;
      wsSend({type:'open_session',session_id:ACTIVE_SESSION});
    }
    wsSend(payload);
  } catch(e){
    finishStream(true, e.message);
  }
}

function beginStreamUi(path){
  streaming=true; $('sendbtn').classList.add('stop'); $('sendbtn').disabled=false; syncSendIcon();
  const isRemote = path==='remote';
  island('stream', isRemote ? '<b>远端</b>&nbsp;回复中' : '<b>本机</b>&nbsp;回复中');
  streamBuf = { id: null, text: '', path: path, statusDetail: '' };
  const label = isRemote
    ? ('远端 Agent · '+(STATE?.active_model||'流式'))
    : ('本机 · '+(STATE?.local_llm?.model||'LLM'));
  $('msgs').insertAdjacentHTML('beforeend', `
    <div class="mrow" id="streaming"><canvas class="avt" width="10" height="10" data-avt="${isRemote?'agent':'本地'}"></canvas>
      <div class="bub"><div class="who">${esc(label)}</div>
      <div id="stream-body"></div>
      <div id="stream-tools"></div>
      <div class="mut" id="stream-status" style="margin-top:4px;font-size:11px"></div>
      <span class="caret" id="stream-caret"></span></div></div>`);
  bindAvts(); scrollBottom();
}

async function onSendLocal(txt){
  $('msgs').insertAdjacentHTML('beforeend', `<div class="mrow me"><div class="bub">${esc(txt)}</div></div>`);
  $('inp').value=''; autogrow(); scrollBottom();
  beginStreamUi('local');
  const ctrl = new AbortController();
  LOCAL_STREAM_CTRL = ctrl;
  try {
    const res = await fetch('/api/mobile/local/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify({ content: txt }),
      signal: ctrl.signal,
    });
    if(!res.ok){
      const j = await res.json().catch(()=>({}));
      throw new Error(j.error || ('HTTP '+res.status));
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while(true){
      const { done, value } = await reader.read();
      if(done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n');
      buf = parts.pop() || '';
      let eventName = 'message';
      for(const raw of parts){
        const line = raw.replace(/\r$/, '');
        if(!line){ eventName = 'message'; continue; }
        if(line.startsWith('event:')){ eventName = line.slice(6).trim(); continue; }
        if(!line.startsWith('data:')) continue;
        const data = line.slice(5).trim();
        let msg = {};
        try { msg = JSON.parse(data); } catch { msg = { text: data }; }
        if(eventName==='delta' && msg.text){
          streamBuf.text += msg.text;
          const sb = $('stream-body'); if(sb) sb.innerHTML = md(streamBuf.text);
          scrollBottom();
        } else if(eventName==='done'){
          if(msg.text && !streamBuf.text) streamBuf.text = msg.text;
          const sb = $('stream-body'); if(sb) sb.innerHTML = md(streamBuf.text);
          finishStream(!!msg.stopped);
          return;
        } else if(eventName==='error'){
          throw new Error(msg.error || 'local stream error');
        } else if(eventName==='start'){
          island('stream', '<b>回复中</b>&nbsp;'+esc(msg.model||'llm'));
        }
      }
    }
    finishStream(false);
  } catch(e){
    if(e.name==='AbortError'){
      finishStream(true);
    } else {
      finishStream(true, e.message);
    }
  } finally {
    LOCAL_STREAM_CTRL = null;
  }
}

function sendOrStop(){
  if(streaming){
    if(CHAT_MODE==='remote' || resolveSendPath()==='remote'){
      wsSend({ type:'stop' }); finishStream(true);
    } else {
      if(LOCAL_STREAM_CTRL) LOCAL_STREAM_CTRL.abort();
      api('/api/mobile/local/stop', { method:'POST', body:'{}' }).catch(()=>{});
      finishStream(true);
    }
  } else onSend();
}

function finishStream(stopped, err){
  streaming=false;
  const c=$('stream-caret'); if(c) c.remove();
  const b=$('sendbtn'); b.classList.remove('stop'); b.disabled=!($('inp').value.trim()&&canChat()); syncSendIcon();
  const wrap=$('streaming'); if(wrap){ wrap.removeAttribute('id'); }
  if(err){ const sb=$('stream-body'); if(sb) sb.innerHTML += ` <span class="mut">（${esc(err)}）</span>`; }
  else if(stopped){ const sb=$('stream-body'); if(sb) sb.innerHTML += ' <span class="mut">（已停止）</span>'; }
  island(stopped?'alert':'conn', stopped?'<b>已停止</b>':'<b>已完成</b>', 1600);
  streamBuf = { id:null, text:'' };
}

function appendToolEvent(ev){
  const box = $('stream-tools') || document.querySelector('#streaming #stream-tools');
  if(!box) return;
  const name = ev.name || ev.tool || 'tool';
  const phase = ev.phase || ev.status || '';
  const st = (phase==='end' || ev.status==='completed') ? 'OK' : (ev.status==='failed'?'FAIL':'…');
  const color = st==='OK'?'st': '';
  box.insertAdjacentHTML('beforeend', `<div class="trace open"><div class="th"><span class="px" style="font-size:9px;color:var(--purple)">TRACE</span> ${esc(name)} · ${esc(phase)} <span class="${color}" style="margin-left:auto">${st}</span></div>
    <div class="tb2">${esc(JSON.stringify(ev.arguments||ev.result||{}).slice(0,400))}</div></div>`);
  scrollBottom();
}

function onConfirmRequest(ev){
  approvals = Math.max(approvals, 1); syncBadge();
  if(notifyOn) island('alert', '<b>待审批</b>&nbsp;'+(ev.title||ev.tool||''), 4000);
  const cid = ev.confirm_id || ev.id;
  const html = `<div class="mrow"><canvas class="avt" width="10" height="10" data-avt="confirm"></canvas>
    <div class="bub"><div class="who">审批 · <span class="num">${esc(ev.agent_name||ev.tool||'')}</span></div>
    <p>${esc(ev.title||'需要确认')}</p>
    <div class="mut">${esc(ev.command||ev.reason||'')}</div>
    <div class="appr-inline" id="cf-${esc(cid)}">
      <div class="row">
        <button class="btn sm pri" style="flex:1" onclick="confirmLive('${esc(cid)}',true)">通过</button>
        <button class="btn sm" style="flex:1" onclick="confirmLive('${esc(cid)}',false)">拒绝</button>
      </div>
    </div></div></div>`;
  $('msgs').insertAdjacentHTML('beforeend', html);
  bindAvts(); scrollBottom();
  goTab('approve'); refreshApprovals();
}

function confirmLive(id, ok){
  wsSend({ type:'confirm_response', confirm_id:id, approved:!!ok, scope: ok?'once':'deny' });
  api('/api/mobile/approvals/'+encodeURIComponent(id)+'/decide', {
    method:'POST', body: JSON.stringify({ approved:!!ok, kind:'confirm', scope: ok?'once':'deny' })
  }).catch(()=>{});
  const el = $('cf-'+id);
  if(el) el.innerHTML = `<span class="badge ${ok?'gn':'rd'}">${ok?'PASSED':'REJECTED'}</span>`;
  toast(ok?'已在对话内批准':'已拒绝');
  if(approvals>0){ approvals--; syncBadge(); }
}

function md(s){
  // Safe-ish markdown: escape first, then allow limited formatting.
  let h = esc(s);
  h = h.replace(/```([\s\S]*?)```/g,(_,c)=>'<pre>'+c.trim()+'</pre>');
  h = h.replace(/`([^`]+)`/g,(_,c)=>'<code>'+c+'</code>');
  h = h.replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');
  h = h.replace(/\n/g,'<br>');
  return h;
}
function scrollBottom(){ const m=$('msgs'); m.scrollTop=m.scrollHeight; }

/* ═══ WebSocket to Rust host ═══ */
function ensureWs(){
  if(MODE!=='remote') return;
  if(ws && (ws.readyState===WebSocket.OPEN || ws.readyState===WebSocket.CONNECTING)) return;
  const proto = location.protocol==='https:'?'wss:':'ws:';
  ws = new WebSocket(`${proto}//${location.host}/api/mobile/ws`);
  ws.onopen = ()=>{ wsRetry=0; if(ACTIVE_SESSION) wsSend({type:'open_session', session_id:ACTIVE_SESSION}); };
  ws.onclose = ()=>{
    ws=null;
    const delay = Math.min(8000, 500*Math.pow(1.6, wsRetry++));
    setTimeout(ensureWs, delay);
  };
  ws.onerror = ()=>{};
  ws.onmessage = (ev)=>{
    let msg; try{ msg=JSON.parse(ev.data); }catch{ return; }
    handleWs(msg);
  };
}
function wsSend(obj){
  if(!ws || ws.readyState!==WebSocket.OPEN){ ensureWs(); setTimeout(()=>wsSend(obj), 200); return; }
  ws.send(JSON.stringify(obj));
}

function handleWs(msg){
  const t = msg.type;
  if(t==='stream_delta'){
    const delta = msg.content || msg.delta || '';
    if(delta){
      // backend may send incremental chunks
      streamBuf.text += delta;
      const sb=$('stream-body');
      if(sb) sb.innerHTML = md(streamBuf.text);
      scrollBottom();
    }
    if(msg.done) finishStream(false);
  } else if(t==='status'){
    const st = msg.state || '';
    const detail = msg.detail || '';
    streamBuf.statusDetail = detail || streamBuf.statusDetail;
    const ss = $('stream-status');
    if(ss && detail && st!=='idle') ss.textContent = detail;
    if(st==='idle' || st==='error'){
      if(streaming){
        // empty + Ready → 明确报错（常见：kernel 未起 / LLM 准入失败）
        if(!streamBuf.text.trim()){
          const why = detail && detail!=='Ready'
            ? detail
            : (detail==='Ready'
              ? 'Agent 结束但未产出文本（检查 PC：tevarn-kernel-host 是否运行、模型是否可用）'
              : '无模型输出');
          finishStream(true, why);
        } else {
          finishStream(st==='error', detail && detail!=='Ready' ? detail : '');
        }
      }
    } else if(st==='thinking' || st==='tool_executing'){
      island('stream', `<b>${esc(st)}</b>&nbsp;${esc((detail||'').slice(0,28))}`, 0);
    }
  } else if(t==='tool_event'){
    appendToolEvent(msg);
  } else if(t==='confirm_request'){
    onConfirmRequest(msg);
  } else if(t==='user_echo' || t==='user_message_ack' || t==='run_event' || t==='auth_ok' || t==='mobile_hello'){
    /* protocol noise */
  } else if(t==='error'){
    toast(msg.detail||'错误');
    if(streaming) finishStream(true, msg.detail);
  } else if(t==='session_open'){
    ACTIVE_SESSION = msg.session_id || ACTIVE_SESSION;
    applyModeUI();
  } else if(t==='slash_result'){
    streamBuf.text += (msg.reply||'');
    const sb=$('stream-body'); if(sb) sb.innerHTML=md(streamBuf.text);
  } else if(t==='sync_response'){
    if(msg.partial_content){
      streamBuf.text = msg.partial_content;
      const sb=$('stream-body'); if(sb) sb.innerHTML=md(streamBuf.text);
    }
  } else if(t==='chat_closed'){
    if(streaming) finishStream(true, '会话连接已关闭，请重试');
  } else if(t==='pong'){
    island('conn','<b>PONG</b>',1600);
  }
}


/* ═══ LLM 设置（对齐 PC ModelSettings：供应商 / 模型 / 应用） ═══ */
let CATALOG = null;
let PRESETS = [];
let PROVIDER_OPTS = []; // {id,name,source:'catalog'|'preset'|'local', raw}
let OAUTH_STATE = '';
let OAUTH_DEVICE = '';
let OAUTH_KIND = ''; // openai | xai | ''


async function loadLlmPanel(){
  // Always load local profile for offline fields
  let localCfg = null;
  try {
    const lr = await api('/api/mobile/local/config');
    localCfg = lr.config || null;
    if(STATE){ STATE.local_llm = localCfg; STATE.local_llm_ready = !!localCfg?.ready; }
  } catch{}

  if(MODE==='remote'){
    try {
      const [catR, preR] = await Promise.all([
        api('/api/mobile/catalog'),
        api('/api/mobile/presets'),
      ]);
      CATALOG = (catR.ok && catR.catalog) ? catR.catalog : (STATE?.catalog || {});
      PRESETS = (preR.ok && preR.presets) ? preR.presets : [];
    } catch(e){
      CATALOG = STATE?.catalog || {};
      PRESETS = [];
      if($('llm-hint')) $('llm-hint').textContent = '目录加载失败: '+e.message;
    }
  } else {
    CATALOG = null;
    PRESETS = [];
  }
  buildProviderOptions(localCfg);
  fillProviderSelect(localCfg);
  onProviderChange();
  const active = CATALOG?.active_model || localCfg?.model || '';
  if($('api-model')) $('api-model').value = active;
  updateLlmHint(localCfg);
}

function buildProviderOptions(localCfg){
  PROVIDER_OPTS = [];
  const seen = new Set();
  const providers = CATALOG?.providers || [];
  for(const p of providers){
    if(p.enabled===false) continue;
    const id = p.id || '';
    if(!id || seen.has(id)) continue;
    seen.add(id);
    const nm = p.name || id;
    PROVIDER_OPTS.push({ id, name: nm, source: 'catalog', raw: p });
  }
  for(const p of PRESETS){
    const id = p.id || '';
    if(!id || seen.has(id)) continue;
    // skip if already catalogued under preset_id
    if(providers.some(c => c.id===id || c.preset_id===id)) continue;
    seen.add(id);
    PROVIDER_OPTS.push({ id, name: (p.name || id) + ' · 预设', source: 'preset', raw: p });
  }
  // offline / custom option always available
  PROVIDER_OPTS.push({
    id: '__custom__',
    name: '自定义 / 本机直连',
    source: 'local',
    raw: {
      llm_base_url: localCfg?.base_url || '',
      llm_model: localCfg?.model || '',
      llm_provider: 'openai-compatible',
    }
  });
}

function fillProviderSelect(localCfg){
  const ps = $('llm-provider'); if(!ps) return;
  const activePid = CATALOG?.active_provider_id || (localCfg?.ready ? '__custom__' : (PROVIDER_OPTS[0]?.id || ''));
  ps.innerHTML = PROVIDER_OPTS.map(o =>
    `<option value="${esc(o.id)}" ${o.id===activePid?'selected':''}>${esc(o.name)}</option>`
  ).join('') || '<option value="__custom__">自定义 / 本机直连</option>';
}

function currentProviderOpt(){
  const id = $('llm-provider')?.value || '__custom__';
  return PROVIDER_OPTS.find(o => o.id===id) || { id:'__custom__', source:'local', raw:{} };
}

function modelIdsFromProvider(opt){
  const p = opt?.raw || {};
  let ids = [];
  if(Array.isArray(p.models) && p.models.length){
    ids = p.models.map(m => typeof m==='string' ? m : m.id).filter(Boolean);
    // filter disabled
    if(p.models[0] && typeof p.models[0]==='object'){
      ids = p.models.filter(m => !m.disabled).map(m => m.id);
    }
  } else if(Array.isArray(p.cached_models)){
    ids = p.cached_models.slice();
  } else if(p.llm?.llm_model){
    ids = [p.llm.llm_model];
  } else if(p.llm_model){
    ids = [p.llm_model];
  }
  return ids.filter(Boolean);
}


function isOauthProvider(opt){
  if(!opt) return false;
  const id = (opt.id || '').toLowerCase();
  const p = opt.raw || {};
  const preset = (opt.source==='preset') ? p : (PRESETS.find(x => x.id===id || x.id===p.preset_id) || {});
  if(id.includes('oauth') || id === 'openai-chatgpt-oauth' || id === 'xai-oauth') return true;
  if(preset.auth_mode === 'oauth_pkce' || preset.auth_mode === 'oauth_device_code') return true;
  if(preset.oauth_provider === 'openai' || preset.oauth_provider === 'xai') return true;
  if(preset.needs_api_key === false && (id.includes('chatgpt') || id.includes('xai'))) return true;
  return false;
}
function oauthKind(opt){
  const id = (opt?.id || '').toLowerCase();
  const p = opt?.raw || {};
  const preset = PRESETS.find(x => x.id===opt?.id || x.id===p.preset_id) || p;
  if(id.includes('xai') || preset.oauth_provider==='xai' || preset.auth_mode==='oauth_device_code') return 'xai';
  if(id.includes('chatgpt') || id.includes('openai') || preset.oauth_provider==='openai' || preset.auth_mode==='oauth_pkce') return 'openai';
  return 'openai';
}
function syncAuthFields(){
  const opt = currentProviderOpt();
  const oauth = isOauthProvider(opt);
  OAUTH_KIND = oauth ? oauthKind(opt) : '';
  const baseFg = $('fg-llm-base');
  const keyFg = $('fg-llm-key');
  const opanel = $('oauth-panel');
  const testBtn = $('llm-test-btn');
  if(oauth){
    if(baseFg) baseFg.style.display = 'none';
    if(keyFg) keyFg.style.display = 'none';
    if(opanel) opanel.style.display = '';
    if(testBtn) testBtn.style.display = 'none';
    const help = $('oauth-help');
    if(help){
      help.textContent = OAUTH_KIND==='xai'
        ? 'Grok OAuth：点登录获取设备码，在浏览器完成授权（无需 API Key）。需已连接 PC。'
        : 'ChatGPT 会员 OAuth：点「ChatGPT 登录」在浏览器授权（无需 API Key）。令牌保存在 PC。自动完成失败时，用下方备用项粘贴回调地址。';
    }
    if($('oauth-start-btn')) $('oauth-start-btn').textContent = OAUTH_KIND==='xai' ? 'Grok 登录' : 'ChatGPT 登录';
    const base = opt.raw?.llm_base_url || opt.raw?.llm?.llm_base_url || '';
    if($('llm-base') && base) $('llm-base').value = base;
    // 手动回调备用：ChatGPT 始终展示；Grok 用设备码，隐藏 URL 粘贴
    if($('fg-oauth-callback')){
      if(OAUTH_KIND==='xai'){
        $('fg-oauth-callback').style.display = 'none';
      } else {
        $('fg-oauth-callback').style.display = '';
        if($('oauth-callback-lb')) $('oauth-callback-lb').textContent = '备用 · 手动粘贴回调地址';
        if($('oauth-callback-hint')){
          $('oauth-callback-hint').textContent =
            '授权成功后若未自动完成：复制浏览器地址栏完整 URL（须含 code=），粘贴到下方并点完成。';
        }
      }
    }
  } else {
    if(baseFg) baseFg.style.display = '';
    if(keyFg) keyFg.style.display = '';
    if(opanel) opanel.style.display = 'none';
    if(testBtn) testBtn.style.display = '';
  }
}

async function startOauthLogin(btn){
  if(MODE!=='remote'){ toast('OAuth 需先连接 PC'); goTab('remote'); return; }
  const opt = currentProviderOpt();
  if(!isOauthProvider(opt)){ toast('当前供应商不是 OAuth'); return; }
  btn.disabled = true; const old = btn.textContent; btn.textContent = '发起中…';
  try {
    if(OAUTH_KIND==='xai'){
      const r = await api('/api/mobile/oauth/xai/start', { method:'POST', body:'{}' });
      if(r.ok === false && r.error) throw new Error(r.error);
      OAUTH_DEVICE = r.device_code || '';
      const url = r.verification_uri || r.verification_url || 'https://accounts.x.ai/device';
      const code = r.user_code || '';
      if($('oauth-status')) $('oauth-status').textContent = `设备码 ${code} · 打开 ${url} 授权`;
      if(code) toast('设备码: '+code);
      if(url) window.open(url, '_blank');
      if($('oauth-poll-btn')){ $('oauth-poll-btn').style.display=''; }
      // auto poll
      pollOauthLoop();
    } else {
      const r = await api('/api/mobile/oauth/openai/start', { method:'POST', body:'{}' });
      if(r.ok === false && r.error) throw new Error(r.error || r.message || 'start failed');
      OAUTH_STATE = r.state || '';
      const url = r.authorization_url || '';
      if($('oauth-status')) $('oauth-status').textContent = r.message || '已发起登录，请在浏览器完成授权';
      if(url){
        window.open(url, '_blank');
        toast('已打开 ChatGPT 登录页');
      } else {
        toast(r.message || '请按提示完成授权');
      }
      if($('oauth-poll-btn')) $('oauth-poll-btn').style.display='';
      if($('fg-oauth-callback')) $('fg-oauth-callback').style.display='';
      if(r.callback_listening){
        pollOauthLoop();
        toast('已打开登录页 · 将自动检测；也可手动粘贴回调地址');
      } else {
        toast('请授权后，把地址栏完整 URL 粘贴到「备用 · 手动回调」');
      }
    }
  } catch(e){
    toast('登录失败: '+e.message);
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

let OAUTH_POLL_TIMER = null;
async function pollOauthLoop(){
  clearTimeout(OAUTH_POLL_TIMER);
  const deadline = Date.now() + 10*60*1000;
  const tick = async () => {
    if(Date.now() > deadline){ toast('等待授权超时'); return; }
    try {
      const ok = await pollOauthLogin(null, true);
      if(ok) return;
    } catch{}
    OAUTH_POLL_TIMER = setTimeout(tick, 2500);
  };
  tick();
}

async function pollOauthLogin(btn, silent){
  if(btn){ btn.disabled=true; }
  try {
    if(OAUTH_KIND==='xai'){
      if(!OAUTH_DEVICE){ if(!silent) toast('请先点登录'); return false; }
      const r = await api('/api/mobile/oauth/xai/poll', { method:'POST', body: JSON.stringify({ device_code: OAUTH_DEVICE }) });
      if(r.status==='authorized' || (r.ok && r.active_provider_id)){
        toast(r.message || 'Grok 登录成功');
        clearTimeout(OAUTH_POLL_TIMER);
        await loadLlmPanel();
        await refreshState();
        await ensurePcAgentReady({ silent: false });
        return true;
      }
      if(r.status==='error' || r.ok===false){
        if(!silent) toast(r.message || r.error || '登录失败');
        return r.status==='pending' ? false : false;
      }
      if($('oauth-status')) $('oauth-status').textContent = r.message || '等待授权…';
      return false;
    } else {
      const r = await api('/api/mobile/oauth/openai/poll', { method:'POST', body: JSON.stringify({ state: OAUTH_STATE || undefined }) });
      if(r.status==='authorized' || (r.ok && r.active_provider_id)){
        toast(r.message || 'ChatGPT 登录成功 · 可直接发送');
        clearTimeout(OAUTH_POLL_TIMER);
        await loadLlmPanel();
        await refreshState();
        await ensurePcAgentReady({ silent: false });
        return true;
      }
      if(r.status==='error' || (r.ok===false && r.status && r.status!=='pending')){
        if(!silent) toast(r.message || r.error || '登录失败');
        return false;
      }
      if($('oauth-status')) $('oauth-status').textContent = r.message || '等待 ChatGPT 授权…';
      return false;
    }
  } catch(e){
    if(!silent) toast(e.message);
    return false;
  } finally {
    if(btn) btn.disabled=false;
  }
}

async function completeOauthLogin(btn){
  if(MODE!=='remote'){ toast('请先连接 PC'); return; }
  const url = ($('oauth-callback')?.value || '').trim();
  if(!url){ toast('请粘贴完整回调地址'); return; }
  if(!/code=/.test(url) && !/[?&]code[=%]/.test(url)){
    toast('URL 里应包含 code= 参数，请复制地址栏完整链接');
    return;
  }
  btn.disabled=true; const old=btn.textContent; btn.textContent='完成中…';
  if($('oauth-status')) $('oauth-status').textContent = '正在用回调地址换取令牌…';
  try {
    const r = await api('/api/mobile/oauth/openai/complete', {
      method:'POST',
      body: JSON.stringify({ callback_url: url, state: OAUTH_STATE || undefined }),
    });
    if(r.ok===false) throw new Error(r.message || r.error || r.detail || '失败');
    clearTimeout(OAUTH_POLL_TIMER);
    toast(r.message || 'ChatGPT 登录成功 · 可直接发送');
    if($('oauth-callback')) $('oauth-callback').value='';
    if($('oauth-status')) $('oauth-status').textContent = '登录成功 · 已进入远端对话，输入后即可发送';
    await loadLlmPanel();
    await refreshState();
    await ensurePcAgentReady({ silent: false });
    goTab('chat');
    autogrow();
  } catch(e){
    if($('oauth-status')) $('oauth-status').textContent = '失败: '+e.message;
    toast('完成失败: '+e.message);
  } finally {
    btn.disabled=false; btn.textContent=old;
  }
}

function onProviderChange(){
  const opt = currentProviderOpt();
  const p = opt.raw || {};
  const base = p.llm_base_url || p.llm?.llm_base_url || (opt.source==='local' ? (STATE?.local_llm?.base_url||'') : '');
  if($('llm-base') && document.activeElement !== $('llm-base')){
    // only autofill when not typing
    if(!$('llm-base').dataset.touched) $('llm-base').value = base || '';
  } else if($('llm-base') && !$('llm-base').dataset.touched){
    $('llm-base').value = base || '';
  }
  const ms = $('llm-model'); if(!ms) return;
  let models = modelIdsFromProvider(opt);
  const prefer = (opt.source==='catalog' ? (CATALOG?.active_model || p.active_model) : null)
    || p.llm_model || p.llm?.llm_model || STATE?.local_llm?.model || '';
  if(prefer && !models.includes(prefer)) models = [prefer, ...models];
  if(!models.length){
    ms.innerHTML = '<option value="">— 手写或探测 —</option>';
    if($('fg-custom-model')) $('fg-custom-model').style.display = '';
  } else {
    ms.innerHTML = models.map(id =>
      `<option value="${esc(id)}" ${id===prefer?'selected':''}>${esc(id)}</option>`
    ).join('');
    if($('fg-custom-model')) $('fg-custom-model').style.display = 'none';
  }
  syncAuthFields();
}

function selectedModelName(){
  const custom = ($('llm-model-custom')?.value || '').trim();
  if($('fg-custom-model')?.style.display !== 'none' && custom) return custom;
  return ($('llm-model')?.value || custom || '').trim();
}

function updateLlmHint(localCfg){
  const el = $('llm-hint'); if(!el) return;
  if(MODE==='remote'){
    const pid = CATALOG?.active_provider_id || '—';
    const mid = CATALOG?.active_model || '—';
    el.textContent = `PC 活动: ${pid} / ${mid}` + (localCfg?.ready ? ` · 本机亦可用 ${localCfg.model}` : '');
  } else if(localCfg?.ready){
    el.textContent = `本机就绪 · ${localCfg.model} · ${localCfg.api_key_masked||''}`;
  } else {
    el.textContent = '未就绪：选择供应商或填写 Base URL / 模型 / API Key 后点「应用」';
  }
}

async function applyLlm(btn){
  btn.disabled = true; const old = btn.textContent; btn.textContent = '应用中…';
  try {
    const opt = currentProviderOpt();
    const model = selectedModelName();
    const base = ($('llm-base')?.value || '').trim();
    const key = ($('llm-key')?.value || '').trim();
    if(!model){ toast('请选择或填写模型'); return; }

    const oauth = isOauthProvider(opt);
    if(oauth && MODE!=='remote'){
      toast('OAuth 供应商需先连接 PC，并在登录后用于远端 Agent');
      return;
    }
    if(oauth && MODE==='remote'){
      // only select model — no API key
      if(!model){ toast('请选择模型'); return; }
      const pid = opt.source==='catalog' ? opt.id : opt.id;
      // if only preset, register first is not needed when already in catalog after oauth
      if(opt.source==='preset'){
        toast('请先点「登录授权」完成 OAuth，再应用模型');
        return;
      }
      const body = { provider_id: pid, model };
      if(ACTIVE_SESSION) body.session_id = ACTIVE_SESSION;
      const r = await api('/api/mobile/catalog/select', { method:'POST', body: JSON.stringify(body) });
      if(!r.ok) throw new Error(r.error || '切换失败');
      toast('已应用 OAuth 模型 · '+model);
      await refreshState();
      await loadLlmPanel();
      await ensurePcAgentReady({ silent: false });
      goTab('chat');
      return;
    }
    if(MODE==='remote' && opt.source==='catalog'){
      // update key if provided (API providers only)
      if(key){
        const cr = await api('/api/mobile/settings/credentials', {
          method:'POST',
          body: JSON.stringify({
            provider_id: opt.id,
            credential_id: null,
            label: 'Mobile',
            api_key: key,
            set_active: true,
          }),
        });
        if(!cr.ok) throw new Error(cr.error || '凭证更新失败');
      }
      // base url change via register (same id)
      if(base){
        const p = opt.raw || {};
        await api('/api/mobile/catalog/register', {
          method:'POST',
          body: JSON.stringify({
            id: opt.id,
            name: p.name || opt.id,
            llm_provider: p.llm_provider || 'openai-compatible',
            llm_base_url: base,
            llm_model: model,
            set_active: true,
            preset_id: p.preset_id || undefined,
          }),
        }).catch(()=>{});
      }
      const body = { provider_id: opt.id, model };
      if(ACTIVE_SESSION) body.session_id = ACTIVE_SESSION;
      const r = await api('/api/mobile/catalog/select', { method:'POST', body: JSON.stringify(body) });
      if(!r.ok) throw new Error(r.error || '切换失败');
      toast('已应用到 PC · '+model);
      // mirror to local for offline
      await syncLocalFromFields(opt, model, base, key);
    } else if(MODE==='remote' && opt.source==='preset'){
      const p = opt.raw || {};
      const llm = p.llm || {};
      const reg = await api('/api/mobile/catalog/register', {
        method:'POST',
        body: JSON.stringify({
          id: opt.id,
          name: p.name || opt.id,
          llm_provider: llm.llm_provider || 'openai-compatible',
          llm_base_url: base || llm.llm_base_url || '',
          llm_api_key: key || undefined,
          llm_model: model,
          set_active: true,
          preset_id: opt.id,
          models: p.models || undefined,
        }),
      });
      if(!reg.ok) throw new Error(reg.error || '登记失败');
      const r = await api('/api/mobile/catalog/select', {
        method:'POST',
        body: JSON.stringify({ provider_id: opt.id, model, session_id: ACTIVE_SESSION || undefined }),
      });
      if(!r.ok) throw new Error(r.error || '切换失败');
      toast('已激活预设 · '+model);
      await syncLocalFromFields(opt, model, base || llm.llm_base_url, key);
    } else {
      // local / custom direct
      if(!base){ toast('请填写 Base URL'); return; }
      if(!key && !(STATE?.local_llm?.has_key)){ toast('请填写 API Key'); return; }
      await syncLocalFromFields(opt, model, base, key);
      toast('本机模型已就绪 · '+model);
    }
    if($('llm-key')) $('llm-key').value = '';
    if($('llm-base')) delete $('llm-base').dataset.touched;
    await refreshState();
    await loadLlmPanel();
    applyModeUI();
  } catch(e){
    toast('应用失败: '+e.message);
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

async function syncLocalFromFields(opt, model, base, key){
  const body = {
    base_url: base || '',
    model: model || '',
    provider_label: opt?.name || opt?.id || 'custom',
  };
  if(key) body.api_key = key;
  const r = await api('/api/mobile/local/config', { method:'POST', body: JSON.stringify(body) });
  if(!r.ok) throw new Error(r.error || '本机配置保存失败');
  if(STATE){ STATE.local_llm = r.config; STATE.local_llm_ready = !!r.config?.ready; }
}

async function testLlm(btn){
  btn.disabled = true; const old = btn.textContent; btn.textContent = '测试…';
  try {
    const opt = currentProviderOpt();
    const p = opt.raw || {};
    const model = selectedModelName();
    const base = ($('llm-base')?.value || '').trim();
    const key = ($('llm-key')?.value || '').trim();

    if(MODE==='remote' && opt.source !== 'local'){
      const body = {
        provider_id: opt.source==='catalog' ? opt.id : undefined,
        llm_provider: p.llm_provider || p.llm?.llm_provider || 'openai-compatible',
        llm_base_url: base || p.llm_base_url || p.llm?.llm_base_url,
        llm_model: model || p.llm_model || p.llm?.llm_model,
      };
      if(key) body.llm_api_key = key;
      const r = await api('/api/mobile/test-llm', { method:'POST', body: JSON.stringify(body) });
      if(!r.ok) throw new Error(r.error || '测试失败');
      const n = (r.result?.models || r.result?.available || []).length;
      toast(r.result?.message || ('连接成功'+(n?(' · '+n+' 模型'):'')));
      // if models returned and select empty, fill
      const models = r.result?.models || r.result?.available || [];
      if(models.length && $('llm-model')){
        const cur = selectedModelName();
        $('llm-model').innerHTML = models.map(id =>
          `<option value="${esc(id)}" ${id===cur?'selected':''}>${esc(id)}</option>`
        ).join('');
        if($('fg-custom-model')) $('fg-custom-model').style.display = 'none';
      }
    } else {
      // local test
      const body = { base_url: base, model };
      if(key) body.api_key = key;
      // save temp for test if key provided
      if(base) await api('/api/mobile/local/config', { method:'POST', body: JSON.stringify({
        base_url: base, model, api_key: key || undefined, provider_label: opt.name || 'custom'
      })});
      const r = await api('/api/mobile/local/test', { method:'POST', body: JSON.stringify(body) });
      if(!r.ok) throw new Error(r.error || '测试失败');
      toast(r.result?.message || '连接成功');
      const models = r.result?.models || [];
      if(models.length && $('llm-model')){
        $('llm-model').innerHTML = models.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join('');
        if($('fg-custom-model')) $('fg-custom-model').style.display = 'none';
      }
      await loadLlmPanel();
    }
  } catch(e){
    toast('测试失败: '+e.message);
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
}

// legacy names used elsewhere
async function refreshCatalogFields(){ return loadLlmPanel(); }
async function loadLocalLlmForm(){ return loadLlmPanel(); }
function applyModel(btn){ return applyLlm(btn); }
function probeLlm(btn){ return testLlm(btn); }

async function testApi(btn){
  btn.disabled=true; btn.textContent='连接中…';
  try {
    const base = ($('api-base')?.value||'').trim();
    if(!base) throw new Error('请填写 Base URL');
    await connectPC(false);
    await loadLlmPanel();
    toast('已连接 PC');
  } catch(e){
    toast('连接失败: '+e.message);
  } finally {
    btn.disabled=false; btn.textContent='连接';
  }
}

function clearApiForm(){
  if($('api-key')) $('api-key').value='';
  if($('api-email')) $('api-email').value='';
  toast('已清空登录输入');
}

function clearLocalUi(){
  $('msgs').innerHTML = MODE==='remote'?emptyRemoteWelcome():localWelcomeHtml();
  bindAvts();
  toast('界面会话区已清空（服务器历史保留）');
}

/* ═══ Static action wiring (real handlers only) ═══ */
function wireStaticActions(){
  const disc = $('rm-disc');
  if(disc) disc.onclick = () => {
    if(MODE==='remote') disconnect();
    else goTab('remote');
  };
  if($('conn-go')) $('conn-go').onclick = () => drawer(1);
}


async function connectFromPairForm(){
  if($('pair-base')) $('api-base').value = $('pair-base').value;
  if($('pair-email') && $('api-email')) $('api-email').value = $('pair-email').value;
  if($('pair-pass') && $('api-key')) $('api-key').value = $('pair-pass').value;
  await connectPC(false);
}
async function pairAgentFromForm(){
  const host = $('pair-agent-host')?.value || '127.0.0.1';
  const port = Number($('pair-agent-port')?.value || 19876);
  const token = $('pair-agent-token')?.value || '';
  if(token.length < 8){ toast('token ≥ 8 字符'); return; }
  try {
    const r = await api('/api/mobile/devices/pair', {
      method:'POST', body: JSON.stringify({ name: 'L1 '+host, host, port, token })
    });
    if(!r.ok) throw new Error(r.error||'fail');
    toast('配对成功：'+(r.device?.name||''));
    await refreshState();
  } catch(e){ toast('配对失败: '+e.message); }
}
async function runtimeHeartbeat(){
  try {
    const r = await api('/api/mobile/runtime');
    const live = r.runtime?.processes_live;
    toast('运行时 OK' + (live!=null ? ' · 进程 '+live : ''));
    island('conn','<b>PONG</b>',1800);
  } catch(e){ toast(e.message); }
}

/* ═══ Init ═══ */
$('inp').addEventListener('input', autogrow);
$('inp').addEventListener('keydown', e=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); onSend(); }});
$('msgs').innerHTML = localWelcomeHtml();
bindAvts();
renderSugg();
syncBadge();
wireStaticActions();
ensureWs();
CHAT_MODE = localStorage.getItem('tevarn-chat-mode') || 'local';
if(CHAT_MODE!=='remote') CHAT_MODE = 'local';
// Default surface is 本机; drawer switches to 远端
refreshState().then(async ()=>{
  await loadLlmPanel().catch(()=>{});
  // 严格按用户选择：默认本机；仅当记忆为 remote 且已连 PC 时进远端
  const wantRemote = (localStorage.getItem('tevarn-chat-mode') === 'remote') && pcAgentReady();
  if(wantRemote){
    await ensurePcAgentReady({ silent: true });
  } else {
    setChatMode('local');
    await restoreLocalHistory().catch(()=>{});
    if(!$('msgs').children.length){
      $('msgs').innerHTML = localWelcomeHtml();
      bindAvts();
    }
  }
  await refreshModeSnap();
  applyModeUI();
  autogrow();
});
function tickStatusbar(){
  const el = $('sb-time'); if(!el) return;
  const d = new Date();
  el.textContent = d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0');
}
(function initTheme(){
  const mode = localStorage.getItem('tevarn-theme-mode') || localStorage.getItem('tevarn-theme') || 'light';
  const map = { system: 0, light: 1, dark: 2 };
  const buttons = document.querySelectorAll('.tseg button');
  const idx = map[mode] ?? 1;
  if (buttons[idx]) {
    // set class without toast on boot
    document.querySelectorAll('.tseg button').forEach(b=>b.classList.remove('act'));
    buttons[idx].classList.add('act');
    if (mode === 'system') {
      const dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    } else {
      document.documentElement.dataset.theme = mode === 'dark' ? 'dark' : 'light';
    }
  }
})();
(function(){
  const b = document.getElementById('llm-base');
  if(b){ b.addEventListener('input', ()=>{ b.dataset.touched='1'; }); }
})();
tickStatusbar();
setInterval(tickStatusbar, 15000);
setInterval(()=>{ if(MODE==='remote') refreshState(); }, 15000);
document.querySelectorAll('canvas[data-avt="boss"]').forEach(cv=>drawAvt(cv,'boss'));
