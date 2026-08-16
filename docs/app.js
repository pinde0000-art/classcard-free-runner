/* CLJ 실행 UI.
   기존 실행/폴링 로직(sync, range, showProgress, watchProgress, resetRunner,
   RUN.onclick)은 그대로 두고, 계정 선택과 계정별 목록만 앞에 붙였다.
   기기에는 비밀번호를 저장하지 않는다. account_token만 보관한다. */
const API_BASE = 'https://classcard-free-runner.pinde0000.workers.dev';
const API_URL = `${API_BASE}/run`;
const ACCOUNTS_KEY = 'clj_accounts_v1';
const LAST_KEY = 'clj_last_account';
const catalogKey = (id) => `clj_catalog_${id}`;

// 이전 버전의 공용 실행키는 더 이상 휴대폰에 보관하지 않는다.
try { localStorage.removeItem('classcard_runner_key'); } catch (error) {}
if (location.hash || new URLSearchParams(location.search).has('key')) {
  const cleanUrl = new URL(location.href);
  cleanUrl.hash = '';
  cleanUrl.searchParams.delete('key');
  history.replaceState(null, '', cleanUrl.pathname + cleanUrl.search);
}

let classes = [];
let accounts = [];
let activeAccount = null;
let selectedAccountId = '';
let isRunning = false;
let syncTimer = 0;

const $ = (id) => document.getElementById(id);
const C = $('class'), S = $('set'), START = $('start'), END = $('end'), RUN = $('run'),
  RUN_LABEL = $('run-label'), STATUS = $('status'), ACCOUNT_STATUS = $('account-status');
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* ---------------- 저장소 (비밀번호 없음) ---------------- */
function readAccounts() {
  try { const raw = JSON.parse(localStorage.getItem(ACCOUNTS_KEY) || '[]'); return Array.isArray(raw) ? raw : []; }
  catch { return []; }
}
function writeAccounts() { try { localStorage.setItem(ACCOUNTS_KEY, JSON.stringify(accounts)); } catch (error) {} }
function readCatalog(id) { try { return JSON.parse(localStorage.getItem(catalogKey(id)) || 'null'); } catch { return null; } }
function writeCatalog(id, catalog) { try { localStorage.setItem(catalogKey(id), JSON.stringify(catalog)); } catch (error) {} }
function dropCatalog(id) { try { localStorage.removeItem(catalogKey(id)); } catch (error) {} }

async function api(path, body) {
  const response = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), cache: 'no-store',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

/* ---------------- 계정 화면 ---------------- */
const STATUS_TEXT = { ready: '준비됨', syncing: '정보 불러오는 중', error: '오류', login: '로그인 필요' };
function statusOf(account) {
  if (account.status === 'error' && /아이디|비밀번호|로그인/.test(account.error || '')) return 'login';
  return account.status || 'syncing';
}
function agoText(time) {
  if (!time) return '사용 기록 없음';
  const minutes = Math.floor((Date.now() - time) / 60000);
  if (minutes < 1) return '방금 사용';
  if (minutes < 60) return `${minutes}분 전 사용`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전 사용`;
  return `${Math.floor(hours / 24)}일 전 사용`;
}
function avatarInto(element, account) {
  element.textContent = '';
  const name = (account.nickname || '').trim();
  if (account.avatar) {
    const image = document.createElement('img');
    image.src = account.avatar; image.alt = ''; image.loading = 'lazy';
    image.addEventListener('error', () => { image.remove(); element.textContent = name ? name[0] : '?'; });
    element.append(image);
  } else element.textContent = name ? name[0] : '?';
}
const CHECK_SVG = '<svg class="acct-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7"/></svg>';
const MORE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="5.2" r="1.9" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.9" fill="currentColor" stroke="none"/><circle cx="12" cy="18.8" r="1.9" fill="currentColor" stroke="none"/></svg>';

const EMPTY_ART = `<svg viewBox="0 0 96 96" aria-hidden="true">
  <rect x="18" y="30" width="48" height="34" rx="6" transform="rotate(-8 42 47)" opacity=".5"/>
  <rect x="28" y="34" width="50" height="36" rx="7"/>
  <path d="M38 48h26M38 57h16"/>
  <path d="m72 17 1.8 4.2 4.2 1.8-4.2 1.8L72 29l-1.8-4.2-4.2-1.8 4.2-1.8L72 17z"/></svg>`;
const PLUS_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>';

/* 비어 있어도 화면이 완성되어 보이도록. 동작은 기존 '계정 추가'와 같다. */
function buildEmptyState() {
  const empty = document.createElement('div');
  empty.className = 'empty';
  empty.innerHTML =
    `<span class="empty-art">${EMPTY_ART}</span>` +
    '<h2 class="empty-title">아직 연결된 계정이 없어요.</h2>' +
    '<p class="empty-sub">클래스카드 계정을 연결하면 클래스와 세트를 바로 불러옵니다.</p>' +
    `<button type="button" class="btn primary empty-cta">${PLUS_SVG}계정 연결하기</button>`;
  empty.querySelector('.empty-cta').addEventListener('click', () => openAccountForm(null));
  return empty;
}

function renderAccounts() {
  const list = $('account-list');
  list.textContent = '';
  if (!accounts.length) {
    list.append(buildEmptyState());
    $('account-continue').disabled = true;
    return;
  }
  for (const account of accounts) {
    const state = statusOf(account);
    const row = document.createElement('div');
    row.className = 'acct' + (account.account_id === selectedAccountId ? ' is-active' : '');
    row.dataset.id = account.account_id;

    const pick = document.createElement('button');
    pick.type = 'button';
    pick.className = 'acct';
    pick.style.cssText = 'all:unset;display:flex;align-items:center;gap:12px;flex:1;min-width:0;cursor:pointer';
    pick.setAttribute('aria-pressed', account.account_id === selectedAccountId);
    const avatar = document.createElement('span'); avatar.className = 'avatar'; avatarInto(avatar, account);
    const main = document.createElement('span'); main.className = 'acct-main';
    const name = document.createElement('span'); name.className = 'acct-name';
    name.textContent = account.nickname || '이름 확인 중';
    const sub = document.createElement('span'); sub.className = 'acct-sub';
    sub.innerHTML = `<i class="dot ${state === 'login' ? 'error' : state}"></i>`;
    const label = document.createElement('span');
    label.textContent = `${STATUS_TEXT[state]} · ${agoText(account.last_used_at)}`;
    sub.append(label);
    main.append(name, sub);
    pick.append(avatar, main);
    pick.addEventListener('click', () => selectAccount(account.account_id));

    const check = document.createElement('span');
    check.innerHTML = CHECK_SVG;
    const more = document.createElement('button');
    more.type = 'button'; more.className = 'acct-more'; more.innerHTML = MORE_SVG;
    more.setAttribute('aria-label', `${account.nickname || '계정'} 관리`);
    more.addEventListener('click', (event) => { event.stopPropagation(); openAccountMenu(account.account_id); });

    row.append(pick, check.firstChild, more);
    list.append(row);
  }
  const selected = accounts.find((account) => account.account_id === selectedAccountId);
  $('account-continue').disabled = !selected || statusOf(selected) !== 'ready';
}

function selectAccount(id) {
  if (isRunning) { ACCOUNT_STATUS.textContent = '학습이 실행 중이라 계정을 바꿀 수 없습니다.'; ACCOUNT_STATUS.classList.add('error'); return; }
  selectedAccountId = id;
  try { localStorage.setItem(LAST_KEY, id); } catch (error) {}
  ACCOUNT_STATUS.classList.remove('error');
  renderAccounts();
  // 이 계정으로 넘어갈 가능성이 높으니 목록을 미리 받아 둔다.
  prefetchCatalog(accounts.find((one) => one.account_id === id));
}

/* 동기화 중인 계정은 준비될 때까지만 짧게 확인한다. */
async function pollSyncing() {
  clearTimeout(syncTimer);
  const pending = accounts.filter((account) => account.status === 'syncing');
  if (!pending.length) return;
  for (const account of pending) {
    try {
      const data = await api('/account/status', { account_id: account.account_id, account_token: account.account_token });
      Object.assign(account, {
        nickname: data.account.nickname || account.nickname,
        avatar: data.account.avatar || account.avatar,
        status: data.account.status, error: data.account.error || '',
      });
    } catch (error) {
      // 일시적인 네트워크 오류는 다음 폴링에서 다시 확인한다. 인증 오류만 재로그인 상태로 전환한다.
      if (/접근 권한|등록되지 않은 계정/.test(error.message)) {
        account.status = 'error'; account.error = error.message;
      }
    }
  }
  writeAccounts(); renderAccounts();
  // 방금 준비된 계정은 미리 목록을 받아 두면 전환이 즉시 끝난다.
  for (const account of pending) if (statusOf(account) === 'ready') prefetchCatalog(account);
  if (accounts.some((account) => account.status === 'syncing')) syncTimer = setTimeout(pollSyncing, 4000);
}

/* ---------------- 계정 추가 / 관리 ---------------- */
const ACCOUNT_SHEET = $('account-sheet'), CONFIRM_SHEET = $('confirm-sheet');
let sheetCloseTimer = 0, relinkTarget = null, confirmAction = null;

function showSheet(element) {
  clearTimeout(sheetCloseTimer);
  element.classList.remove('closing');
  element.hidden = false;
  document.documentElement.classList.add('locked');
  void element.offsetWidth;
  element.classList.add('open');
}
function hideSheet(element) {
  if (element.hidden) return;
  element.classList.add('closing'); element.classList.remove('open');
  clearTimeout(sheetCloseTimer);
  sheetCloseTimer = setTimeout(() => {
    element.hidden = true; element.classList.remove('closing');
    if ([ACCOUNT_SHEET, CONFIRM_SHEET, $('sheet')].every((one) => one.hidden)) document.documentElement.classList.remove('locked');
  }, 290);
}
function openAccountForm(target) {
  relinkTarget = target || null;
  $('account-sheet-title').textContent = relinkTarget ? '다시 로그인' : '계정 추가';
  $('account-submit').textContent = relinkTarget ? '다시 연결' : '연결';
  $('login-id').value = ''; $('login-pw').value = ''; $('account-error').textContent = '';
  $('login-id').disabled = false; $('login-pw').disabled = false; $('account-submit').disabled = false;
  showSheet(ACCOUNT_SHEET);
  setTimeout(() => $('login-id').focus(), 120);
}
$('account-add').addEventListener('click', () => openAccountForm(null));
$('account-cancel').addEventListener('click', () => hideSheet(ACCOUNT_SHEET));
$('pw-toggle').addEventListener('click', () => {
  const field = $('login-pw'), shown = field.type === 'text';
  field.type = shown ? 'password' : 'text';
  $('pw-toggle').setAttribute('aria-pressed', String(!shown));
  $('pw-toggle').setAttribute('aria-label', shown ? '비밀번호 표시' : '비밀번호 숨김');
  field.focus();
});
$('account-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const loginId = $('login-id').value.trim();
  const loginPassword = $('login-pw').value;
  const error = $('account-error');
  error.textContent = '';
  if (!loginId || !loginPassword) { error.textContent = '아이디와 비밀번호를 입력해 주세요.'; return; }
  $('account-submit').disabled = true; $('login-id').disabled = true; $('login-pw').disabled = true;
  $('account-submit').textContent = '연결 중';
  try {
    const payload = { login_id: loginId, login_pwd: loginPassword };
    if (relinkTarget) Object.assign(payload, { account_id: relinkTarget.account_id, account_token: relinkTarget.account_token });
    const data = await api(relinkTarget ? '/account/relink' : '/account/link', payload);
    // 입력값은 여기서 즉시 버린다. 저장하지 않는다.
    $('login-pw').value = ''; $('login-id').value = '';
    if (relinkTarget) Object.assign(relinkTarget, { status: data.account.status, error: '' });
    else {
      const existing = accounts.find((account) => account.account_id === data.account.account_id);
      if (existing) Object.assign(existing, data.account, { account_token: data.account_token });
      else accounts.push({ ...data.account, account_token: data.account_token });
    }
    writeAccounts();
    if (!relinkTarget) {
      selectedAccountId = data.account.account_id;
      try { localStorage.setItem(LAST_KEY, selectedAccountId); } catch (storageError) {}
    }
    renderAccounts(); hideSheet(ACCOUNT_SHEET); pollSyncing();
    ACCOUNT_STATUS.textContent = '계정 정보를 불러오는 중입니다.'; ACCOUNT_STATUS.classList.remove('error');
  } catch (failure) {
    error.textContent = failure.message;
  } finally {
    $('account-submit').disabled = false; $('login-id').disabled = false; $('login-pw').disabled = false;
    $('account-submit').textContent = relinkTarget ? '다시 연결' : '연결';
  }
});

function openConfirm(title, text, action) {
  $('confirm-title').textContent = title; $('confirm-text').textContent = text;
  confirmAction = action; showSheet(CONFIRM_SHEET);
}
$('confirm-cancel').addEventListener('click', () => hideSheet(CONFIRM_SHEET));
$('confirm-ok').addEventListener('click', async () => { const action = confirmAction; hideSheet(CONFIRM_SHEET); if (action) await action(); });

function openAccountMenu(id) {
  const account = accounts.find((one) => one.account_id === id);
  if (!account) return;
  const index = accounts.indexOf(account);
  const actions = [
    { name: '목록 새로고침', run: async () => { await api('/account/refresh', { account_id: id, account_token: account.account_token }); account.status = 'syncing'; writeAccounts(); renderAccounts(); pollSyncing(); } },
    { name: '다시 로그인', run: async () => openAccountForm(account) },
    { name: '위로 이동', disabled: index === 0, run: async () => { accounts.splice(index - 1, 0, accounts.splice(index, 1)[0]); writeAccounts(); renderAccounts(); } },
    { name: '아래로 이동', disabled: index === accounts.length - 1, run: async () => { accounts.splice(index + 1, 0, accounts.splice(index, 1)[0]); writeAccounts(); renderAccounts(); } },
    { name: '이 기기에서 로그아웃', run: async () => { forgetAccount(id); } },
    { name: '계정 삭제', danger: true, run: async () => openConfirm('계정 삭제', `${account.nickname || '이 계정'}의 저장된 자격 증명과 목록을 모두 지웁니다.`, async () => {
      try { await api('/account/delete', { account_id: id, account_token: account.account_token }); } catch (error) {}
      forgetAccount(id);
    }) },
  ];
  openListSheet(actions.map((action, position) => ({
    index: position, name: action.name, sub: '', danger: action.danger, disabled: action.disabled,
  })), async (position) => {
    const action = actions[position];
    if (action.disabled) return;
    closeListSheet();
    if (isRunning && /삭제|로그아웃|다시 로그인/.test(action.name)) {
      ACCOUNT_STATUS.textContent = '학습이 실행 중이라 계정을 변경할 수 없습니다.';
      ACCOUNT_STATUS.classList.add('error');
      return;
    }
    try { await action.run(); } catch (error) { ACCOUNT_STATUS.textContent = error.message; ACCOUNT_STATUS.classList.add('error'); }
  });
}
function forgetAccount(id) {
  accounts = accounts.filter((one) => one.account_id !== id);
  dropCatalog(id);
  if (selectedAccountId === id) selectedAccountId = accounts.length ? accounts[0].account_id : '';
  writeAccounts(); renderAccounts();
}

/* ---------------- 선택 시트 (클래스 / 세트 / 메뉴) ---------------- */
const SHEET = $('sheet'), SHEET_LIST = $('sheet-list'), SHEET_SEARCH = $('sheet-search'), SHEET_SEARCH_WRAP = $('sheet-search-wrap');
let sheetKind = '', sheetPick = null, sheetRowsData = [], sheetOpener = null;
const ROW_CHECK = '<svg class="row-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7"/></svg>';

function renderSheetRows(query, selectedIndex) {
  const text = query.trim().toLowerCase();
  const rows = sheetRowsData.filter((row) => !text || row.name.toLowerCase().includes(text));
  SHEET_LIST.textContent = '';
  if (!rows.length) {
    const empty = document.createElement('p'); empty.className = 'sheet-empty'; empty.textContent = '결과 없음';
    SHEET_LIST.append(empty); return;
  }
  for (const row of rows) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'row' + (row.index === selectedIndex ? ' is-selected' : '') + (row.danger ? ' is-danger' : '');
    button.dataset.index = row.index;
    if (row.disabled) { button.disabled = true; button.style.opacity = '.4'; }
    button.innerHTML = `<span class="row-main"><span class="row-name"></span><span class="row-sub"></span></span>${row.sub === undefined ? '' : ROW_CHECK}`;
    button.querySelector('.row-name').textContent = row.name;
    button.querySelector('.row-sub').textContent = row.sub || '';
    button.addEventListener('click', () => sheetPick && sheetPick(row.index));
    SHEET_LIST.append(button);
  }
}
function openListSheet(rows, onPick, options = {}) {
  sheetRowsData = rows; sheetPick = onPick; sheetKind = options.kind || 'menu';
  sheetOpener = options.opener || null;
  SHEET_SEARCH.value = '';
  SHEET_SEARCH_WRAP.hidden = rows.length < 8;
  renderSheetRows('', options.selected === undefined ? -1 : options.selected);
  showSheet(SHEET);
  const active = SHEET_LIST.querySelector('.row.is-selected');
  if (active) active.scrollIntoView({ block: 'center' });
}
function closeListSheet() { hideSheet(SHEET); if (sheetOpener) setTimeout(() => sheetOpener.focus(), 300); }
SHEET.addEventListener('click', (event) => { if (event.target.hasAttribute('data-close')) closeListSheet(); });
ACCOUNT_SHEET.addEventListener('click', (event) => { if (event.target.hasAttribute('data-close')) hideSheet(ACCOUNT_SHEET); });
CONFIRM_SHEET.addEventListener('click', (event) => { if (event.target.hasAttribute('data-close')) hideSheet(CONFIRM_SHEET); });
SHEET_SEARCH.addEventListener('input', () => renderSheetRows(SHEET_SEARCH.value, sheetKind === 'class' ? +C.value : sheetKind === 'set' ? +S.value : -1));
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!SHEET.hidden) closeListSheet();
  else if (!ACCOUNT_SHEET.hidden) hideSheet(ACCOUNT_SHEET);
  else if (!CONFIRM_SHEET.hidden) hideSheet(CONFIRM_SHEET);
});

/* ---------------- 기존 실행 로직 (동작 변경 없음) ---------------- */
function sync() { S.innerHTML = ''; classes[C.value].sets.forEach((s, i) => S.add(new Option(`${s.name}${s.count ? ` · ${s.count}장` : ''}`, i))); range(); }
function range() {
  const n = classes[C.value]?.sets[S.value]?.count || 0;
  if (n) { START.max = END.max = n; START.value = 1; END.value = n; }
  else { START.removeAttribute('max'); END.removeAttribute('max'); START.value = 1; END.value = 1; }
  const testMode = $('m4').checked; START.disabled = testMode; END.disabled = testMode;
}
C.onchange = sync; S.onchange = range;
document.querySelectorAll('[name=mode]').forEach((input) => input.onchange = () => {
  range(); if (input.value === '4' && input.checked) STATUS.textContent = '테스트는 클래스카드에 출제된 전체 문제로 실행됩니다.';
});

const PROGRESS = $('progress'), PROGRESS_STATE = $('progress-state'), PROGRESS_COUNT = $('progress-count'),
  PROGRESS_BAR = $('progress-bar'), PROGRESS_GLOW = $('progress-glow'),
  PROGRESS_PCT = $('progress-pct').firstElementChild;
let shownDone = 0, shownTotal = 0, shownState = '';
const STATE_LABEL = { completed: '모두 완료', failed: '오류 발생', running: '학습 중', preparing: '학습 준비 중' };
function replay(element, className, duration) { element.classList.remove(className); void element.offsetWidth; element.classList.add(className); setTimeout(() => element.classList.remove(className), duration); }
function highlightStep() { replay(PROGRESS_GLOW, 'pulse', 760); replay(PROGRESS_COUNT, 'bump', 700); }
function renderProgress(done, total, state) {
  PROGRESS.classList.add('show');
  PROGRESS_COUNT.textContent = `${done}/${total}`;
  PROGRESS_PCT.textContent = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  PROGRESS_STATE.textContent = STATE_LABEL[state] || '대기 중';
  PROGRESS_STATE.dataset.state = STATE_LABEL[state] ? state : 'idle';
  PROGRESS_BAR.style.transform = `scaleX(${total ? Math.min(1, done / total) : 0})`;
}
async function showProgress(progress, state) {
  const parts = progress.split('/').map(Number), done = parts[0] || 0, total = parts[1] || 0;
  if (total !== shownTotal || done < shownDone) { shownDone = 0; shownTotal = total; }
  if (!shownTotal) shownTotal = total;
  const stepped = done > shownDone;
  const finished = state === 'completed' && shownState !== 'completed';
  shownDone = done; shownState = state;
  renderProgress(done, total, state);
  if (stepped || finished) highlightStep();
}
function resetRunner() {
  shownDone = 0; shownTotal = 0; shownState = ''; isRunning = false;
  PROGRESS.classList.remove('show'); PROGRESS_GLOW.classList.remove('pulse'); PROGRESS_COUNT.classList.remove('bump');
  PROGRESS_COUNT.textContent = '0/0'; PROGRESS_PCT.textContent = '0';
  PROGRESS_STATE.textContent = '대기 중'; PROGRESS_STATE.dataset.state = 'idle';
  PROGRESS_BAR.style.transform = 'scaleX(0)';
  $('m1').checked = true; $('a1').checked = true; range(); syncSegments(false); refreshPickers();
  RUN.disabled = false; RUN_LABEL.textContent = '실행';
  $('account-back').disabled = false; $('catalog-refresh').disabled = false;
  STATUS.textContent = activeAccount ? `${activeAccount.nickname || '계정'} · 클래스 ${classes.length}개` : '';
  STATUS.classList.remove('error');
}
async function watchProgress(id, total, initialIssue = 0) {
  let issue = initialIssue;
  for (let attempts = 0; attempts < 3600; attempts += 1) {
    await wait(1000);
    try {
      const issuePart = issue ? `&issue=${issue}` : '';
      const response = await fetch(`${API_BASE}/status?id=${id}${issuePart}`, { cache: 'no-store' });
      const data = await response.json();
      if (response.ok) {
        if (data.issue) issue = data.issue;
        await showProgress(data.progress === '0/0' ? `0/${total}` : data.progress, data.state);
        if (data.state === 'completed') { STATUS.textContent = '모두 완료되었습니다.'; await wait(4000); resetRunner(); return; }
        if (data.state === 'failed') { STATUS.textContent = '실행이 중단되어 초기화합니다.'; STATUS.classList.add('error'); await wait(2500); resetRunner(); return; }
      }
    } catch (error) {}
  }
  STATUS.textContent = '진행 상황 확인 시간이 초과되어 초기화합니다.'; STATUS.classList.add('error');
  await wait(2500); resetRunner();
}
RUN.onclick = async () => {
  const s = classes[C.value].sets[S.value];
  const payload = {
    class_id: classes[C.value].id, set_id: s.id, title: s.name,
    start: +START.value, end: +END.value, card_count: s.count,
    mode: +document.querySelector('[name=mode]:checked').value,
    amount: +document.querySelector('[name=amount]:checked').value,
    account_id: activeAccount.account_id, account_token: activeAccount.account_token,
  };
  if (payload.start < 1 || payload.end < payload.start || payload.end > payload.card_count) {
    STATUS.textContent = '카드 범위를 확인해 주세요.'; STATUS.classList.add('error'); return;
  }
  RUN.disabled = true; RUN_LABEL.textContent = '실행 중'; isRunning = true;
  $('account-back').disabled = true; $('catalog-refresh').disabled = true;
  STATUS.textContent = '학습을 빠르게 준비하고 있습니다.'; STATUS.classList.remove('error');
  const selectedTotal = payload.end - payload.start + 1;
  await showProgress(`0/${selectedTotal}`, 'preparing');
  try {
    const response = await fetch(API_URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    activeAccount.last_used_at = Date.now(); writeAccounts();
    STATUS.textContent = data.message || '학습을 시작했습니다.';
    await showProgress(`0/${selectedTotal}`, 'preparing');
    watchProgress(data.id, selectedTotal, data.issue || 0);
  } catch (error) {
    STATUS.textContent = `학습 실행에 실패했습니다: ${error.message}`; STATUS.classList.add('error');
    RUN.disabled = false; RUN_LABEL.textContent = '실행'; isRunning = false;
    $('account-back').disabled = false; $('catalog-refresh').disabled = false;
  }
};

/* ---------------- 세그먼트 · 트리거 ---------------- */
const MODE_IND = $('mode-ind'), AMOUNT_IND = $('amount-ind');
function slide(indicator, index, flash) { indicator.style.transform = `translateX(${index * 100}%)`; if (flash) replay(indicator, 'flash', 660); }
function syncSegments(flash) {
  const modes = [...document.querySelectorAll('[name=mode]')], amounts = [...document.querySelectorAll('[name=amount]')];
  const m = modes.findIndex((i) => i.checked), a = amounts.findIndex((i) => i.checked);
  if (m >= 0) slide(MODE_IND, m, flash); if (a >= 0) slide(AMOUNT_IND, a, flash);
}
document.querySelectorAll('[name=mode],[name=amount]').forEach((input) => input.addEventListener('change', () => syncSegments(true)));

const CLASS_PICKER = $('class-picker'), SET_PICKER = $('set-picker'), RUNNER_PANEL = $('runner-panel');
function refreshPickers() {
  const c = classes[C.value];
  CLASS_PICKER.disabled = !classes.length;
  $('class-value').textContent = c ? c.name : '클래스';
  $('class-badge').textContent = c && c.name.trim() ? c.name.trim()[0] : '–';
  $('class-meta').textContent = c ? `세트 ${c.sets.length}` : '';
  const s = c ? c.sets[S.value] : null;
  SET_PICKER.disabled = !c;
  $('set-value').textContent = s ? s.name : '세트';
  $('set-meta').textContent = s && s.count ? `${s.count}장` : '';
}
new MutationObserver(refreshPickers).observe(S, { childList: true });
CLASS_PICKER.addEventListener('click', () => {
  openListSheet(classes.map((c, i) => ({ index: i, name: c.name, sub: `세트 ${c.sets.length}개` })),
    (index) => { if (+C.value !== index) { C.value = String(index); C.dispatchEvent(new Event('change', { bubbles: true })); } refreshPickers(); setTimeout(closeListSheet, 150); },
    { kind: 'class', selected: +C.value, opener: CLASS_PICKER });
});
SET_PICKER.addEventListener('click', () => {
  const sets = classes[C.value]?.sets || [];
  openListSheet(sets.map((s, i) => ({ index: i, name: s.name, sub: s.count ? `${s.count}장` : '' })),
    (index) => { if (+S.value !== index) { S.value = String(index); S.dispatchEvent(new Event('change', { bubbles: true })); } refreshPickers(); setTimeout(closeListSheet, 150); },
    { kind: 'set', selected: +S.value, opener: SET_PICKER });
});

/* ---------------- 계정별 목록 ---------------- */
function applyCatalog(list) {
  classes = Array.isArray(list) ? list : [];
  C.innerHTML = '';
  classes.forEach((c, i) => C.add(new Option(c.name, i)));
  C.disabled = !classes.length; S.disabled = !classes.length;
  if (classes.length) sync(); else { S.innerHTML = ''; range(); }
  refreshPickers();
  RUN.disabled = !classes.length || isRunning;
}
/* 계정 화면에 머무는 동안 미리 목록을 받아 둔다.
   같은 계정에 대한 요청은 하나만 유지해 중복 호출을 막고, 실행 화면으로
   넘어갈 때는 이미 끝난(또는 진행 중인) 요청을 그대로 이어 쓴다. */
const catalogRequests = new Map();
function fetchCatalog(account, { force = false } = {}) {
  if (!account) return Promise.resolve(null);
  const id = account.account_id;
  if (force) catalogRequests.delete(id);
  if (catalogRequests.has(id)) return catalogRequests.get(id);
  const pending = api('/account/catalog', { account_id: id, account_token: account.account_token })
    .then((data) => {
      const fresh = (data.catalog && data.catalog.classes) || [];
      if (fresh.length) writeCatalog(id, { classes: fresh, synced_at: data.catalog.synced_at });
      Object.assign(account, {
        nickname: data.account.nickname || account.nickname,
        avatar: data.account.avatar || account.avatar,
        status: data.account.status,
        error: data.account.error || '',
      });
      writeAccounts();
      return data;
    })
    .catch((error) => { catalogRequests.delete(id); throw error; });
  catalogRequests.set(id, pending);
  return pending;
}
function prefetchCatalog(account) {
  if (!account || statusOf(account) !== 'ready') return;
  fetchCatalog(account).catch(() => {});
}

async function loadAccountCatalog(account, { background = false, force = false } = {}) {
  const cached = readCatalog(account.account_id);
  if (cached && cached.classes && cached.classes.length) applyCatalog(cached.classes);
  else if (!background) {
    applyCatalog([]);
    // 실제 구조를 유지한 스켈레톤을 보여준다. 표시만 바뀌고 흐름은 그대로다.
    RUNNER_PANEL.classList.add('is-loading');
    STATUS.textContent = '목록을 불러오는 중입니다.';
  }
  try {
    const data = await fetchCatalog(account, { force });
    const fresh = (data.catalog && data.catalog.classes) || [];
    // 저장과 계정 정보 갱신은 fetchCatalog가 이미 처리했다. 여기서는 화면만 맞춘다.
    if (fresh.length) applyCatalog(fresh);
    $('runner-nickname').textContent = account.nickname || '계정';
    avatarInto($('runner-avatar'), account);
    if (!fresh.length) {
      STATUS.textContent = account.status === 'syncing' ? '계정 정보를 불러오는 중입니다.' : '표시할 클래스가 없습니다.';
      STATUS.classList.toggle('error', account.status === 'error');
    } else { STATUS.textContent = `${account.nickname || '계정'} · 클래스 ${fresh.length}개`; STATUS.classList.remove('error'); }
  } catch (error) {
    if (!classes.length) { STATUS.textContent = `목록을 불러오지 못했습니다: ${error.message}`; STATUS.classList.add('error'); }
  } finally {
    RUNNER_PANEL.classList.remove('is-loading');
  }
}
$('catalog-refresh').addEventListener('click', async () => {
  if (isRunning || !activeAccount) return;
  STATUS.textContent = '목록을 새로 확인하고 있습니다.'; STATUS.classList.remove('error');
  try {
    await api('/account/refresh', { account_id: activeAccount.account_id, account_token: activeAccount.account_token });
    // 처음엔 짧게, 점점 길게 확인해서 빨리 끝나는 경우를 놓치지 않는다.
    let delay = 900;
    for (let attempt = 0; attempt < 32; attempt += 1) {
      await wait(delay);
      delay = Math.min(3000, Math.round(delay * 1.4));
      const data = await api('/account/status', { account_id: activeAccount.account_id, account_token: activeAccount.account_token });
      if (data.account.status !== 'syncing') break;
    }
    await loadAccountCatalog(activeAccount, { background: true, force: true });
  } catch (error) { STATUS.textContent = error.message; STATUS.classList.add('error'); }
});

/* ---------------- 화면 전환 ---------------- */
function showAccounts() {
  $('screen-runner').hidden = true; $('screen-accounts').hidden = false;
  renderAccounts(); pollSyncing();
}
$('account-back').addEventListener('click', () => { if (!isRunning) showAccounts(); });
$('account-continue').addEventListener('click', async () => {
  const account = accounts.find((one) => one.account_id === selectedAccountId);
  if (!account) return;
  activeAccount = account;
  account.last_used_at = account.last_used_at || 0;
  writeAccounts();
  $('screen-accounts').hidden = true; $('screen-runner').hidden = false;
  $('runner-nickname').textContent = account.nickname || '계정';
  avatarInto($('runner-avatar'), account);
  syncSegments(false);
  await loadAccountCatalog(account);
});

/* ---------------- 클래스 위의 용 ----------------
   실행 로직을 건드리지 않는다. 진행률 칩(data-state)과 진행률 표시 여부만
   지켜보다가 수면 → 브레스 → 비행 으로 포즈를 바꾼다. */
const DRAGON = $('dragon-stage');
let dragonTimer = 0;
function setDragon(pose) {
  clearTimeout(dragonTimer);
  DRAGON.dataset.state = pose;
}
function syncDragon() {
  const running = PROGRESS.classList.contains('show');
  const state = PROGRESS_STATE.dataset.state;
  if (!running || state === 'idle') { setDragon('sleep'); return; }
  const pose = DRAGON.dataset.state;
  if (state === 'preparing') {
    // 잠에서 깨는 순간에만 브레스를 쏜다. preparing 은 여러 번 들어올 수 있다.
    if (pose === 'sleep') {
      setDragon('breath');
      dragonTimer = setTimeout(() => setDragon('fly'), 2200);
    }
  } else if (pose !== 'breath') {
    setDragon('fly');   // 브레스 중이면 그게 끝난 뒤 타이머가 넘긴다
  }
}
const dragonWatch = new MutationObserver(syncDragon);
dragonWatch.observe(PROGRESS_STATE, { attributes: true, attributeFilter: ['data-state'] });
dragonWatch.observe(PROGRESS, { attributes: true, attributeFilter: ['class'] });

/* ---------------- 넓은 화면 레일 ----------------
   자체 상태를 갖지 않는다. 클릭은 기존 버튼에 그대로 위임하고,
   활성/비활성 표시는 그 버튼들의 상태를 그대로 비춘다. */
const RAIL_ACCOUNTS = $('rail-accounts'), RAIL_RUNNER = $('rail-runner'), RAIL_REFRESH = $('rail-refresh');
const SCREEN_RUNNER = $('screen-runner'), ACCOUNT_BACK = $('account-back'),
  ACCOUNT_CONTINUE = $('account-continue'), CATALOG_REFRESH = $('catalog-refresh');
RAIL_ACCOUNTS.addEventListener('click', () => { if (!SCREEN_RUNNER.hidden) ACCOUNT_BACK.click(); });
RAIL_RUNNER.addEventListener('click', () => { if (SCREEN_RUNNER.hidden) ACCOUNT_CONTINUE.click(); });
RAIL_REFRESH.addEventListener('click', () => CATALOG_REFRESH.click());
function syncRail() {
  const onRunner = !SCREEN_RUNNER.hidden;
  RAIL_ACCOUNTS.classList.toggle('is-active', !onRunner);
  RAIL_RUNNER.classList.toggle('is-active', onRunner);
  RAIL_ACCOUNTS.disabled = onRunner && ACCOUNT_BACK.disabled;
  RAIL_RUNNER.disabled = !onRunner && ACCOUNT_CONTINUE.disabled;
  RAIL_REFRESH.disabled = !onRunner || CATALOG_REFRESH.disabled;
}
const railWatch = new MutationObserver(syncRail);
[SCREEN_RUNNER, ACCOUNT_BACK, ACCOUNT_CONTINUE, CATALOG_REFRESH].forEach((element) =>
  railWatch.observe(element, { attributes: true, attributeFilter: ['hidden', 'disabled'] }));

/* ---------------- 시작 ---------------- */
(function start() {
  accounts = readAccounts();
  try { selectedAccountId = localStorage.getItem(LAST_KEY) || ''; } catch (error) {}
  if (!accounts.some((one) => one.account_id === selectedAccountId)) selectedAccountId = accounts.length ? accounts[0].account_id : '';
  renderAccounts();
  if (!accounts.length) ACCOUNT_STATUS.textContent = '계정을 추가해 주세요.';
  else { ACCOUNT_STATUS.textContent = `계정 ${accounts.length}개`; ACCOUNT_STATUS.classList.remove('error'); }
  $('account-add').disabled = false;
  syncRail();
  pollSyncing();
  // 첫 화면이 그려진 직후 백그라운드로 목록을 준비한다(입력을 막지 않는다).
  const preselected = accounts.find((one) => one.account_id === selectedAccountId);
  if (preselected) setTimeout(() => prefetchCatalog(preselected), 0);
})();
