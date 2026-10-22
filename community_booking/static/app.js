'use strict';
const $ = id => document.getElementById(id);
let config, receipt, qrBlob, pageTicket;
const display = (id, value) => { $(id).textContent = JSON.stringify(value, null, 2); };
const time = n => `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;
const data = form => Object.fromEntries(new FormData(form));
function message(text, error = false) { $('message').textContent = text; $('message').classList.toggle('error', error); }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {cache:'no-store'} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (!response.ok) {
    const value = await response.json().catch(() => ({}));
    throw new Error(value.detail || `请求失败 (${response.status})`);
  }
  return response.headers.get('content-type')?.includes('image/') ? response.blob() : response.json();
}
function action(fn) { return async event => {
  event?.preventDefault();
  const button = event?.submitter || (event?.currentTarget?.tagName === 'BUTTON' ? event.currentTarget : null);
  if (button) button.disabled = true;
  try { await fn(event); message('操作完成。'); } catch (e) { message(e.message, true); }
  finally { if (button) button.disabled = false; }
}; }
function tab(id) {
  for (const item of ['book','manage','staff','ticket-panel']) $(item).hidden = item !== id;
  document.querySelectorAll('[data-tab]').forEach(button => button.classList.toggle('active', button.dataset.tab === id));
}
document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', action(async () => { tab(button.dataset.tab); if (button.dataset.tab === 'book' && config) await slots(); })));
function option(select, label, value) { const o = document.createElement('option'); o.textContent = label; o.value = value; select.append(o); }
async function slots() {
  if (!$('duration').value || !$('date').value) return;
  const value = await api(`/api/availability?${new URLSearchParams({facility:$('facility').value, date:$('date').value, duration:$('duration').value})}`);
  $('slot').replaceChildren();
  for (const s of value.slots) {
    option($('slot'), `${time(s.start_minute)} · 剩余 ${s.remaining}`, s.start_minute);
    $('slot').lastChild.disabled = s.remaining === 0;
  }
  const first = [...$('slot').options].find(o => !o.disabled);
  if (first) $('slot').value = first.value;
}
async function facilityChanged() {
  const facility = config.facilities.find(f => f.id === $('facility').value);
  $('duration').replaceChildren();
  for (const d of facility.durations) option($('duration'), `${d} 分钟`, d);
  $('booking-form').elements.party.max = facility.max_party;
  await slots();
}
$('facility').addEventListener('change', action(facilityChanged));
$('duration').addEventListener('change', action(slots));
$('date').addEventListener('change', action(slots));
$('booking-form').addEventListener('submit', action(async () => {
  const value = data($('booking-form'));
  receipt = await api('/api/bookings', {facility:value.facility, date:value.date,
    start_minute:Number(value.start_minute), duration:Number(value.duration), party:Number(value.party), alias:value.alias,
    guardian_confirmed:value.guardian_confirmed === 'on', agreement_accepted:value.agreement_accepted === 'on', agreement_version:config.agreement_version});
  $('receipt').hidden = false;
  display('receipt-info', receipt.booking);
  // Offer the durable owner receipt before attempting the optional QR rendering.
  $('owner-form').elements.booking_id.value = receipt.booking.id;
  $('owner-form').elements.management_secret.value = receipt.management_secret;
  if (qrBlob) URL.revokeObjectURL(qrBlob);
  qrBlob = URL.createObjectURL(await api('/api/ticket/qr', {ticket:receipt.ticket}));
  $('qr').src = qrBlob;
  await slots();
}));
$('download').addEventListener('click', () => {
  if (!receipt) return;
  const blob = URL.createObjectURL(new Blob([JSON.stringify(receipt, null, 2)], {type:'application/json'}));
  const a = document.createElement('a'); a.href = blob; a.download = 'private-booking-receipt.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(blob), 5000);
  message('已请求下载；保管凭证可操作此预约，请勿分享。');
});
$('copy-ticket').addEventListener('click', action(async () => { if (receipt) await navigator.clipboard.writeText(receipt.ticket); }));
const owner = () => ({booking_id:$('owner-form').elements.booking_id.value, management_secret:$('owner-form').elements.management_secret.value});
$('owner-form').addEventListener('submit', action(async () => display('owner-result', await api('/api/bookings/lookup', owner()))));
$('cancel').addEventListener('click', action(async () => { display('owner-result', await api('/api/bookings/cancel', owner())); await slots(); }));
$('cancel-request').addEventListener('click', action(async () => display('owner-result', await api('/api/bookings/cancel-request', {...owner(), reason:$('owner-form').elements.reason.value}))));
function showStaff(value) { $('demo-import').hidden = true; $('staff-tools').hidden = false; $('login-form').hidden = true; $('staff-role').textContent = `${value.username} · ${value.role === 'admin' ? '管理员' : '核验员'}`; $('admin-tools').hidden = value.role !== 'admin'; }
$('demo-file').addEventListener('change', action(async () => {
  const file = $('demo-file').files[0];
  if (!file || file.size > 8192) throw new Error('请选择本机小型演示账户文件');
  const text = await file.text();
  if (!text.startsWith('仅供本机合成演示；不是生产账户。')) throw new Error('文件不是本候选版的演示账户文件');
  const name = $('demo-role').value;
  const lines = text.split('\n');
  const index = lines.findIndex(line => line.startsWith(name+' ('));
  if (index < 0 || !lines[index+1]) throw new Error('未找到所选合成角色');
  $('login-form').elements.username.value = name;
  $('login-form').elements.password.value = lines[index+1].trim();
  $('demo-file').value = '';
}));
$('login-form').addEventListener('submit', action(async () => { showStaff(await api('/api/auth/login', data($('login-form')))); $('login-form').elements.password.value = ''; }));
$('logout').addEventListener('click', action(async () => { await api('/api/auth/logout', {}); $('demo-import').hidden = false; $('staff-tools').hidden = true; $('login-form').hidden = false; $('staff-result').textContent = ''; $('admin-result').textContent = ''; }));
function token() { const value = $('checkin-form').elements.ticket.value.trim(); return value.includes('#') ? value.slice(value.indexOf('#')+1) : value; }
$('inspect').addEventListener('click', action(async () => display('staff-result', await api('/api/staff/inspect', {ticket:token()}))));
let pendingCheckin = null;
$('checkin-form').addEventListener('submit', action(async () => {
  const form = $('checkin-form');
  const value = {ticket:token(), arrived:Number(form.elements.arrived.value), guardian_present:form.elements.guardian_present.checked};
  const fingerprint = JSON.stringify(value);
  if (!pendingCheckin || pendingCheckin.fingerprint !== fingerprint) pendingCheckin = {fingerprint, id:crypto.randomUUID()};
  const result = await api('/api/staff/checkin', {...value, request_id:pendingCheckin.id});
  display('staff-result', result);
  $('undo-form').elements.checkin_id.value = result.checkin_id;
  // Keep this key for retry/double-click safety. Editing fields or pasting a new ticket starts the next batch.
}));
$('checkin-form').addEventListener('input', () => { pendingCheckin = null; });
$('next-batch').addEventListener('click', () => { pendingCheckin = null; $('staff-result').textContent = ''; message('已开始新批次。请核对本次人数，再确认到场。'); });
$('list-bookings').addEventListener('click', action(async () => display('admin-result', await api('/api/admin/bookings?date='+encodeURIComponent($('admin-date').value)))));
$('audit').addEventListener('click', action(async () => display('admin-result', await api('/api/admin/audit'))));
$('admin-booking-form').addEventListener('submit', action(async () => display('admin-result', await api('/api/admin/bookings/cancel', data($('admin-booking-form'))))));
$('rotate').addEventListener('click', action(async () => display('admin-result', await api('/api/admin/ticket/rotate', data($('admin-booking-form'))))));
$('undo-form').addEventListener('submit', action(async () => display('admin-result', await api('/api/admin/checkin/undo', data($('undo-form'))))));
$('capacity-form').addEventListener('submit', action(async () => {
  const d = data($('capacity-form')); const [h,m] = d.time.split(':').map(Number);
  display('admin-result', await api('/api/admin/capacity', {resource:d.resource, date:d.date, start_minute:h*60+m,
    duration:Number(d.duration), capacity:Number(d.capacity), reason:d.reason})); await slots();
}));
$('ticket-copy').addEventListener('click', action(async () => navigator.clipboard.writeText(pageTicket)));
(async () => {
  try {
    config = await api('/api/config');
    $('brand').textContent = config.brand; document.title = config.brand;
    $('agreement').textContent = config.agreement_text;
    $('hours').textContent = `${time(config.open_minute)}–${time(config.close_minute)} · ${config.timezone}`;
    for (const f of config.facilities) option($('facility'), f.name, f.id);
    for (const r of Object.keys(config.resources)) option($('resource'), r, r);
    for (const el of document.querySelectorAll('input[type=date]')) el.value = config.today;
    $('date').min = config.today;
    await facilityChanged();
    try { showStaff(await api('/api/auth/me')); } catch (_) { /* Anonymous is the expected initial state. */ }
    if (location.pathname === '/ticket') {
      pageTicket = location.hash.slice(1); history.replaceState(null, '', '/ticket'); tab('ticket-panel');
      display('ticket-result', await api('/api/ticket', {ticket:pageTicket}));
    }
  } catch(e) { message(e.message, true); }
})();
