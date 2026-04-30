// ============================================================================
// 발권창구 — frontend logic
// Reads/writes config.json on a private GitHub repo via Contents API.
// State is intentionally simple: localStorage holds {repo, pat}; everything
// else is derived from config.json (writes) and state.json (reads).
// ============================================================================

const STORAGE_KEY = 'balgwon.config';
const FETCH_HEADERS = { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' };

// ----- storage --------------------------------------------------------------

function loadCreds() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw);
    if (!v?.repo || !v?.pat) return null;
    return v;
  } catch {
    return null;
  }
}
function saveCreds(creds) { localStorage.setItem(STORAGE_KEY, JSON.stringify(creds)); }
function clearCreds() { localStorage.removeItem(STORAGE_KEY); }

// ----- github api -----------------------------------------------------------

class GitHub {
  constructor({ repo, pat }) {
    this.repo = repo;
    this.pat = pat;
  }
  _headers() {
    return { ...FETCH_HEADERS, Authorization: `Bearer ${this.pat}` };
  }
  async getFile(path) {
    const res = await fetch(`https://api.github.com/repos/${this.repo}/contents/${path}`, {
      headers: this._headers(), cache: 'no-store',
    });
    if (res.status === 404) return { sha: null, content: null };
    if (!res.ok) throw new Error(`GET ${path} → ${res.status} ${await res.text()}`);
    const json = await res.json();
    const content = json.content ? decodeUtf8(atob(json.content.replace(/\n/g, ''))) : '';
    return { sha: json.sha, content };
  }
  async putFile(path, { content, sha, message }) {
    const body = {
      message,
      content: btoa(encodeUtf8(content)),
      ...(sha ? { sha } : {}),
    };
    const res = await fetch(`https://api.github.com/repos/${this.repo}/contents/${path}`, {
      method: 'PUT', headers: { ...this._headers(), 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`PUT ${path} → ${res.status} ${await res.text()}`);
    return res.json();
  }
  async pingAuth() {
    const res = await fetch(`https://api.github.com/repos/${this.repo}`, { headers: this._headers() });
    if (!res.ok) throw new Error(`auth check → ${res.status}`);
    return res.json();
  }
}

function encodeUtf8(s) { return new TextEncoder().encode(s).reduce((a, b) => a + String.fromCharCode(b), ''); }
function decodeUtf8(s) {
  const bytes = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) bytes[i] = s.charCodeAt(i);
  return new TextDecoder('utf-8').decode(bytes);
}

// ----- domain ---------------------------------------------------------------

function emptyConfig() { return { version: 1, watches: [] }; }

function newWatchId(form) {
  const slug = `${form.from}-${form.to}-${form.date.replaceAll('-', '')}`;
  const stamp = Math.random().toString(36).slice(2, 6);
  return `${slug}-${stamp}`;
}

function parseTrainTypes(s) {
  return s.split(',').map(t => t.trim()).filter(Boolean);
}

function formToWatch(fd) {
  const train_types = parseTrainTypes(String(fd.get('train_types')));
  return {
    id: newWatchId({
      from: String(fd.get('from')),
      to: String(fd.get('to')),
      date: String(fd.get('date')),
    }),
    provider: String(fd.get('provider')),
    from: String(fd.get('from')).trim(),
    to: String(fd.get('to')).trim(),
    date: String(fd.get('date')),
    time_min: String(fd.get('time_min')),
    time_max: String(fd.get('time_max')),
    train_types,
    passengers: {
      adult: Number(fd.get('adult')) || 0,
      child: Number(fd.get('child')) || 0,
      senior: Number(fd.get('senior')) || 0,
    },
    seat_class: String(fd.get('seat_class')),
    auto_reserve: false,
    active: true,
  };
}

// ----- rendering ------------------------------------------------------------

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const tpl = id => $(`#${id}`).content.cloneNode(true);

function fmtDateLong(iso) {
  // 2026-05-15 -> 2026.05.15
  return iso.replaceAll('-', '.');
}
function fmtRelative(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const sec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (sec < 60) return `방금 전`;
  if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}시간 전`;
  return `${Math.floor(sec / 86400)}일 전`;
}
function fmtPassengers(p) {
  const parts = [];
  if (p.adult) parts.push(`성인 ${p.adult}`);
  if (p.child) parts.push(`아동 ${p.child}`);
  if (p.senior) parts.push(`경로 ${p.senior}`);
  return parts.join(' / ') || '성인 1';
}

function renderWatchCard(watch, state) {
  const node = tpl('tpl-watch').firstElementChild;
  node.dataset.watchId = watch.id;
  node.dataset.provider = watch.provider;
  node.dataset.active = String(watch.active);
  $('.badge--provider', node).textContent = watch.provider === 'srt' ? 'SRT' : 'KORAIL';
  $('.watch__from', node).textContent = watch.from;
  $('.watch__to', node).textContent = watch.to;
  $('.watch__date', node).textContent = fmtDateLong(watch.date);
  $$('.watch__meta-row', node)[0].querySelector('.watch__meta-value').textContent =
    `${watch.time_min} – ${watch.time_max}`;
  const types = $$('.watch__meta-row', node)[1].querySelector('.watch__meta-types');
  watch.train_types.forEach(t => {
    const b = document.createElement('span');
    b.className = 'badge';
    b.textContent = t;
    types.appendChild(b);
  });
  $$('.watch__meta-row', node)[2].querySelector('.watch__meta-value').textContent = fmtPassengers(watch.passengers);
  const lastCheck = state?.watches?.[watch.id]?.last_check;
  $('.watch__last-check', node).textContent = `마지막 확인 · ${fmtRelative(lastCheck)}`;
  const toggle = $('.toggle__input', node);
  toggle.checked = watch.active;
  return node;
}

function renderEmpty(onAdd) {
  const node = tpl('tpl-empty');
  node.querySelector('#empty-add').addEventListener('click', onAdd);
  return node;
}

// ----- main -----------------------------------------------------------------

class App {
  constructor(creds) {
    this.gh = new GitHub(creds);
    this.config = emptyConfig();
    this.state = null;
    this.configSha = null;
  }

  async start() {
    document.getElementById('app').innerHTML = '';
    document.getElementById('app').appendChild(tpl('tpl-shell'));

    this._wireFab();
    this._wireSheet();
    await this._loadAll();
  }

  async _loadAll() {
    try {
      const [{ sha, content }, stateFile] = await Promise.all([
        this.gh.getFile('config.json'),
        this.gh.getFile('state.json').catch(() => ({ sha: null, content: null })),
      ]);
      this.configSha = sha;
      this.config = content ? JSON.parse(content) : emptyConfig();
      this.state = stateFile?.content ? JSON.parse(stateFile.content) : null;
    } catch (e) {
      this._toast(`config.json 로드 실패 — ${e.message}`);
      this.config = emptyConfig();
    }
    this._renderHeader();
    this._renderWatches();
  }

  _renderHeader() {
    const lastRun = this.state?.last_run;
    const sub = $('#header-sub');
    const pulse = $('#poll-pulse');
    const lr = $('#last-run');
    if (!lastRun) {
      sub.textContent = 'cancel-seat watcher';
      lr.textContent = '— · 아직 실행 없음';
      pulse.dataset.state = 'idle';
      return;
    }
    const ageMs = Date.now() - new Date(lastRun).getTime();
    const recent = ageMs < 15 * 60 * 1000;
    pulse.dataset.state = recent ? 'active' : 'idle';
    lr.textContent = `polling · ${fmtRelative(lastRun)}`;
    sub.textContent = `${this.gh.repo}`;
  }

  _renderWatches() {
    const root = $('#watches');
    root.innerHTML = '';
    if (!this.config.watches?.length) {
      root.appendChild(renderEmpty(() => this._openSheet()));
      return;
    }
    this.config.watches.forEach(w => {
      const card = renderWatchCard(w, this.state);
      card.querySelector('.toggle__input').addEventListener('change', e => this._toggle(w.id, e.target.checked));
      card.querySelector('.watch__delete').addEventListener('click', () => this._delete(w.id));
      root.appendChild(card);
    });
  }

  // ---- mutations ----

  async _toggle(id, active) {
    const w = this.config.watches.find(x => x.id === id);
    if (!w) return;
    w.active = active;
    await this._save(`toggle ${id} → ${active ? 'on' : 'off'}`);
    this._renderWatches();
  }

  async _delete(id) {
    if (!confirm(`이 워치를 삭제할까요?\n\n${id}`)) return;
    this.config.watches = this.config.watches.filter(w => w.id !== id);
    await this._save(`delete watch ${id}`);
    this._renderWatches();
  }

  async _create(watch) {
    this.config.watches.push(watch);
    await this._save(`add watch ${watch.id}`);
    this._renderWatches();
  }

  async _save(message) {
    const body = JSON.stringify(this.config, null, 2) + '\n';
    try {
      const res = await this.gh.putFile('config.json', { content: body, sha: this.configSha, message });
      this.configSha = res.content?.sha ?? this.configSha;
    } catch (e) {
      if (String(e.message).includes('409') || String(e.message).includes('422')) {
        const fresh = await this.gh.getFile('config.json');
        this.configSha = fresh.sha;
        this.config = JSON.parse(fresh.content || JSON.stringify(emptyConfig()));
        this._toast('충돌 발생 — 최신 상태를 다시 불러왔습니다. 변경 사항을 다시 적용해 주세요.');
      } else {
        this._toast(`저장 실패 — ${e.message}`);
      }
    }
  }

  // ---- sheet (add / edit) ----

  _wireFab() {
    $('#fab-add').addEventListener('click', () => this._openSheet());
  }
  _wireSheet() {
    const sheet = $('#sheet');
    $('#sheet-close').addEventListener('click', () => sheet.close());
    $('#sheet-cancel').addEventListener('click', () => sheet.close());
    sheet.addEventListener('click', e => {
      if (e.target === sheet) sheet.close();
    });
    $('#watch-form').addEventListener('submit', async e => {
      e.preventDefault();
      const fd = new FormData(e.target);
      try {
        const watch = formToWatch(fd);
        if (!watch.train_types.length) throw new Error('열차 종류를 하나 이상 입력하세요');
        if (watch.time_min > watch.time_max) throw new Error('최저 시간이 최대 시간보다 늦습니다');
        $('#sheet-error').hidden = true;
        sheet.close();
        await this._create(watch);
      } catch (err) {
        const errEl = $('#sheet-error');
        errEl.textContent = err.message;
        errEl.hidden = false;
      }
    });
  }
  _openSheet() {
    const sheet = $('#sheet');
    $('#watch-form').reset();
    $('#sheet-error').hidden = true;
    sheet.showModal();
  }

  _toast(msg) { console.warn(msg); alert(msg); }
}

// ----- setup screen ---------------------------------------------------------

function renderSetup(onSubmit) {
  const root = $('#app');
  root.innerHTML = '';
  root.appendChild(tpl('tpl-setup'));
  $('#setup-form').addEventListener('submit', async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const repo = String(fd.get('repo')).trim();
    const pat = String(fd.get('pat')).trim();
    const errEl = $('#setup-error');
    errEl.hidden = true;
    try {
      const probe = new GitHub({ repo, pat });
      await probe.pingAuth();
      saveCreds({ repo, pat });
      onSubmit({ repo, pat });
    } catch (err) {
      errEl.textContent = `연결 실패 — ${err.message}. PAT 권한과 repo 이름을 확인해 주세요.`;
      errEl.hidden = false;
    }
  });
}

// ----- service worker -------------------------------------------------------

async function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  try { await navigator.serviceWorker.register('./sw.js', { scope: './' }); }
  catch (e) { console.warn('SW registration failed', e); }
}

// ----- bootstrap ------------------------------------------------------------

async function boot() {
  registerSW();
  const creds = loadCreds();
  if (!creds) {
    renderSetup(creds => new App(creds).start());
    return;
  }
  await new App(creds).start();
}

document.addEventListener('DOMContentLoaded', boot);
