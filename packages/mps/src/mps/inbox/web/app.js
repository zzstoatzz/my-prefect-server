const $ = (selector) => document.querySelector(selector);
let state = {items: [], total: 0, connections: []};
let view = 'unreviewed';
let offset = 0;
let selected = null;
let busy = false;
let poll;
const text = (tag, value, className) => {const el = document.createElement(tag);el.textContent = value;if(className) el.className = className;return el;};
const date = (value) => new Date(value).toLocaleString(undefined, {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});
async function api(path, data) {
  const response = await fetch(path, data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {});
  if(response.status === 401) {location.assign('/login');throw new Error('Sign in required');}
  const result = await response.json();
  if(!response.ok) throw new Error(result.error || 'Request failed. Your previous queue is still saved.');
  return result;
}
function status(message) {$('#status').textContent = message;}
async function load() {
  try {
    state = await api(`/api/queue?view=${view}&offset=${offset}`);
    if(!state.items.some(item => item.id === selected)) selected = state.items[0]?.id ?? null;
    render();
    clearTimeout(poll);
    if(state.syncing) poll = setTimeout(load, 3000);
  } catch(error) {status(error.message);}
}
function render() {
  $('#progress').textContent = `${state.remaining} left in this batch`;
  $('#count').textContent = `${state.total ? offset + 1 : 0}–${offset + state.items.length} of ${state.total} in this queue`;
  $('#refresh').disabled = busy || state.syncing;
  $('#refresh').textContent = state.syncing ? 'Refreshing…' : 'Refresh mail';
  $('#previous').disabled = offset === 0 || busy;
  $('#next').disabled = offset + state.items.length >= state.total || busy;
  const sources = $('#sources');sources.replaceChildren();
  for(const provider of ['gmail','proton']) {
    const source = state.sources.find(s => s.provider === provider);
    const name = provider === 'gmail' ? 'Gmail' : 'Proton';
    const summary = source?.synced ? `${source.account} · Updated ${date(source.synced * 1000)}${source.total === null ? ' · Partial batch' : ''}` : 'Not connected';
    sources.append(text('span', `${name}: ${summary}${source?.error ? ` · ${source.error}` : ''}`, source?.error || !source?.synced ? 'source-error' : ''));
  }
  const list = $('#list');list.replaceChildren();
  const filter = $('#filter').value.toLowerCase();
  for(const item of state.items.filter(item => `${item.sender} ${item.subject}`.toLowerCase().includes(filter))) {
    const row = text('button','', 'row');row.type = 'button';row.setAttribute('aria-selected', String(selected === item.id));
    row.append(text('span', item.sender, 'sender'), text('span', item.subject || '(No subject)', 'subject'));
    const meta = text('span', '', 'meta');meta.append(text('span', `${item.provider === 'gmail' ? 'Gmail' : 'Proton'} · ${item.bucket}`),text('span',date(item.received_at).split(',')[0]));row.append(meta);
    row.onclick = () => {selected = item.id;document.body.classList.remove('show-list');$('#browse').setAttribute('aria-expanded','false');render();};list.append(row);
  }
  if(!state.items.length) list.append(text('p', 'No messages in this queue.', 'empty'));
  detail();
}
function detail() {
  const pane = $('#detail');pane.replaceChildren();
  const item = state.items.find(message => message.id === selected);
  if(!item) {pane.append(text('h2', view === 'unreviewed' ? 'You’re caught up on this batch.' : 'Nothing here yet.'),text('p','Refresh to check connected mailboxes. Account errors and partial batches are shown above.','support'));return;}
  pane.append(text('span',item.bucket,'badge'),text('h2',item.subject || '(No subject)'),text('div',item.sender,'envelope'),text('div',`${item.account} · ${date(item.received_at)} · ${item.unread ? 'Unread' : 'Read'}`,'envelope'),text('p',item.reason,'reason'),text('div',item.body || 'No plain-text content available. Open the original message to read it.','body'));
  if(item.content_complete === false) pane.append(text('p','Preview only. Open the original for the full message.','support'));
  if(item.note) pane.append(text('p',`Saved note: ${item.note}`,'support'));
  const actions = text('div','','actions');
  const connected = state.connections.includes(item.provider);
  for(const [action,label] of [['read','Mark read'],['keep','Keep actionable'],['defer','Later tomorrow'],['ignore','Archive'],['reply','Draft reply'],['reopen','Return to review']]) {
    if(action === 'reopen' && !item.decision) continue;
    const button = text('button',label);button.disabled = busy || (['read','keep','ignore'].includes(action) && !connected);
    button.onclick = () => action === 'reply' ? compose(item) : perform(action,[item.id]);actions.append(button);
  }
  pane.append(actions, text('p',connected ? 'Mark read and Archive finish review. Keep actionable leaves it unread in your Actionable queue. Later returns tomorrow.' : 'Mailbox actions need a live account connection. You can save a draft or defer this message now.','support'));
  const links = text('div','','links');const original = text('a','Open original');
  const url = new URL(item.url || 'https://mail.proton.me');if(url.protocol === 'https:') {original.href=url.href;original.target='_blank';original.rel='noopener noreferrer';links.append(original);}pane.append(links);
  const group = state.items.filter(other => other.group_key === item.group_key);
  if(group.length > 1) {
    const bulk = text('section','','bulk');bulk.append(text('strong',`${group.length} related messages in this batch`),text('p',item.group_key.startsWith('bulk:') ? 'Same mailing-list sender. Review the list before archiving them together.' : 'Matching message IDs across inboxes. Each copy keeps its own review history.'));
    for(const related of group) bulk.append(text('p',`${related.provider}: ${related.subject}`));
    const button = text('button',`Archive these ${group.length} messages`);button.disabled=busy || group.some(message => !state.connections.includes(message.provider));button.onclick=()=>perform('ignore',group.map(message=>message.id));bulk.append(button);pane.append(bulk);
  }
}
function confirmAction(title, message) {
  return new Promise(resolve => {const dialog=$('#confirm');$('#confirm-title').textContent=title;$('#confirm-text').textContent=message;dialog.onclose=()=>resolve(dialog.returnValue==='confirm');dialog.showModal();});
}
async function perform(action, ids, note='') {
  if(busy) return;
  if(action==='ignore' && !await confirmAction(`Archive ${ids.length === 1 ? 'this message' : `${ids.length} messages`}?`, 'This removes the selected messages from their inboxes and completes their review. It does not delete them.')) return;
  busy=true;render();status('Saving your decision…');
  const until=new Date();until.setDate(until.getDate()+1);until.setHours(9,0,0,0);
  try {const result=await api('/api/decide',{action,ids,note,request_id:crypto.randomUUID(),until:action==='defer'?until.getTime()/1000:null});status(result.completed===ids.length ? `${result.completed} ${result.completed===1?'decision':'decisions'} saved.` : `${result.completed} of ${ids.length} saved. Some mailbox changes were not confirmed; refresh and check those messages.`);await load();}
  catch(error) {status(error.message);}
  finally {busy=false;render();}
}
function compose(item) {
  const pane=$('#detail');const area=text('textarea','', 'draft');area.value=item.note || '';area.placeholder='Write a reply draft…';area.setAttribute('aria-label','Reply draft');
  const save=text('button','Save draft for later');save.onclick=()=>perform('reply',[item.id],area.value);
  const open=text('button','Open in email app');open.onclick=()=>{const address=item.sender.match(/<([^>]+)>/)?.[1] || item.sender.match(/[\w.+-]+@[\w.-]+/)?.[0];if(!address){status('Open the original message to reply.');return;}location.href=`mailto:${encodeURIComponent(address)}?subject=${encodeURIComponent('Re: '+item.subject)}&body=${encodeURIComponent(area.value)}`;};
  pane.append(text('h3','Reply draft'),area,save,open,text('p','Saved here until you send it from your email app. Nothing is sent automatically.','support'));area.focus();
}
for(const button of document.querySelectorAll('[data-view]')) button.onclick=()=>{view=button.dataset.view;offset=0;for(const other of document.querySelectorAll('[data-view]')) other.removeAttribute('aria-current');button.setAttribute('aria-current','page');load();};
$('#browse').onclick=()=>{const showing=document.body.classList.toggle('show-list');$('#browse').setAttribute('aria-expanded',String(showing));$('#browse').textContent=showing?'Back to message':'Browse messages';};
$('#filter').oninput=render;
$('#previous').onclick=()=>{offset=Math.max(0,offset-100);load();};
$('#next').onclick=()=>{offset+=100;load();};
$('#refresh').onclick=async()=>{try{await api('/api/refresh',{});status('Refreshing connected accounts. Existing decisions remain saved.');setTimeout(load,500);}catch(error){status(error.message);}};
load();
