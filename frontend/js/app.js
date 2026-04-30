// ============================================================================
// 발권창구 — frontend logic
// Reads/writes config.json on a private GitHub repo via Contents API.
// State is intentionally simple: localStorage holds {repo, pat}; everything
// else is derived from config.json (writes) and state.json (reads).
// ============================================================================

const STORAGE_KEY = 'balgwon.config';
const FETCH_HEADERS = { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' };

// ----- rail data ------------------------------------------------------------
// Curated from Korean Wikipedia (KTX / 수서고속철도). Includes every station
// that KTX or SRT trains actually stop at as of 2026-04. When new lines open
// (e.g., 동해선 추가구간) add stations here — order within a provider is
// unimportant since dropdowns sort with localeCompare('ko').
const RAIL_DATA = {
  korail: {
    stations: [
      '가남', '감곡장호원', '강릉', '경산', '경주', '계룡', '곡성', '공주',
      '광명', '광주송정', '구례구', '구포', '기장', '김제', '김천(구미)',
      '나주', '남원', '남창', '논산', '단양', '대전', '동대구', '동해', '둔내',
      '마산', '만종', '목포', '묵호', '문경', '물금', '밀양',
      '부발', '부산', '부전', '북울산',
      '살미', '삼척', '상봉', '서대구', '서대전', '서울', '서원주', '센텀',
      '수안보온천', '수원', '순천', '신해운대',
      '안동', '앙성온천', '양평', '여수엑스포', '여천', '연풍', '영덕',
      '영등포', '영주', '영천', '오송', '용산', '울산', '울진', '원주', '의성',
      '익산', '장성', '전주', '정동진', '정읍', '제천', '진부(오대산)', '진영', '진주',
      '창원', '창원중앙', '천안아산', '청량리', '충주',
      '태화강', '판교', '평창', '포항', '풍기', '행신', '횡성',
    ],
    train_types: [
      { value: 'KTX', label: 'KTX', default: true },
      { value: 'KTX-산천', label: 'KTX-산천', default: true },
      { value: 'KTX-청룡', label: 'KTX-청룡', default: false },
      { value: 'KTX-이음', label: 'KTX-이음', default: false },
    ],
  },
  srt: {
    stations: [
      '경주', '곡성', '공주', '광주송정', '구례구',
      '김천(구미)', '나주', '남원', '남창', '대전', '동대구', '동탄',
      '마산', '목포', '밀양', '부산', '서대구', '수서', '순천',
      '여수엑스포', '여천', '오송', '울산', '익산', '전주', '정읍',
      '진영', '진주', '창원', '창원중앙', '천안아산', '평택지제', '포항',
    ],
    train_types: [
      { value: 'SRT', label: 'SRT', default: true },
    ],
  },
};

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
  async dispatchWorkflow(workflowFile, ref = 'main') {
    const res = await fetch(
      `https://api.github.com/repos/${this.repo}/actions/workflows/${workflowFile}/dispatches`,
      {
        method: 'POST',
        headers: { ...this._headers(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref }),
      },
    );
    if (res.status === 204) return;
    throw new Error(`dispatch ${workflowFile} → ${res.status} ${await res.text()}`);
  }
  async listRuns(workflowFile, limit = 8) {
    const res = await fetch(
      `https://api.github.com/repos/${this.repo}/actions/workflows/${workflowFile}/runs?per_page=${limit}`,
      { headers: this._headers(), cache: 'no-store' },
    );
    if (!res.ok) throw new Error(`list runs → ${res.status} ${await res.text()}`);
    const data = await res.json();
    return (data.workflow_runs || []).map(r => ({
      id: r.id,
      status: r.status,
      conclusion: r.conclusion,
      event: r.event,
      started: r.run_started_at || r.created_at,
      finished: r.updated_at,
      url: r.html_url,
    }));
  }
  async getRunJobs(runId) {
    const res = await fetch(
      `https://api.github.com/repos/${this.repo}/actions/runs/${runId}/jobs`,
      { headers: this._headers(), cache: 'no-store' },
    );
    if (!res.ok) throw new Error(`get jobs → ${res.status} ${await res.text()}`);
    const data = await res.json();
    return data.jobs || [];
  }
  async getJobLogs(jobId) {
    // GitHub returns 302 → signed Azure blob URL. fetch() follows redirects
    // automatically; the signed URL embeds auth so the redirected request
    // does not need our Authorization header (and would lose it on cross-
    // origin redirect anyway).
    const res = await fetch(
      `https://api.github.com/repos/${this.repo}/actions/jobs/${jobId}/logs`,
      { headers: this._headers() },
    );
    if (!res.ok) throw new Error(`get logs → ${res.status}`);
    return res.text();
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

function formToWatch(fd, formEl) {
  const train_types = Array.from(formEl.querySelectorAll('input[name="train_type"]:checked')).map(i => i.value);
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
    auto_reserve: !!formEl.querySelector('input[name="auto_reserve"]')?.checked,
    active: true,
  };
}

function fillStationDropdowns(provider, fromSelect, toSelect) {
  const sorted = RAIL_DATA[provider].stations.slice().sort((a, b) => a.localeCompare(b, 'ko'));
  const opts = (placeholder) => {
    const lines = [`<option value="" disabled selected>${placeholder}</option>`];
    sorted.forEach(s => lines.push(`<option value="${s}">${s}</option>`));
    return lines.join('');
  };
  fromSelect.innerHTML = opts('출발역 선택');
  toSelect.innerHTML = opts('도착역 선택');
}

function fillTrainTypeChips(provider, group) {
  group.className = `check-group check-group--${provider}`;
  const legend = '<legend class="field__label">열차 종류</legend>';
  const chips = RAIL_DATA[provider].train_types.map(t => `
    <label class="check-chip">
      <input type="checkbox" name="train_type" value="${t.value}"${t.default ? ' checked' : ''}>
      <span>${t.label}</span>
    </label>
  `).join('');
  group.innerHTML = legend + chips;
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

// Parse a basic "*/N * * * *" cron expr; returns { intervalMin } or null.
function parseSimpleCron(expr) {
  const m = String(expr || '').match(/^\*\/(\d+)\s+\*\s+\*\s+\*\s+\*$/);
  if (!m) return null;
  const intervalMin = parseInt(m[1], 10);
  if (!intervalMin || intervalMin < 1) return null;
  return { intervalMin };
}

function nextTickEpoch(intervalMin, now = Date.now()) {
  const intervalMs = intervalMin * 60 * 1000;
  return Math.ceil((now + 1) / intervalMs) * intervalMs;
}

function fmtCountdown(ms) {
  if (ms <= 0) return '확인 중…';
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `다음 ${min}:${String(sec).padStart(2, '0')} 후`;
}

function fmtRunTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const date = d.toLocaleDateString('ko-KR', { month: '2-digit', day: '2-digit' });
  const time = d.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });
  return `${date} ${time}`;
}

function fmtDuration(startIso, endIso) {
  if (!startIso || !endIso) return '—';
  const ms = new Date(endIso).getTime() - new Date(startIso).getTime();
  if (!(ms > 0)) return '—';
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m ${sec % 60}s`;
}

const EVENT_LABELS = {
  schedule: 'cron',
  workflow_dispatch: '수동',
  push: 'push',
};

function renderWatchCard(watch, state) {
  const node = tpl('tpl-watch').firstElementChild;
  node.dataset.watchId = watch.id;
  node.dataset.provider = watch.provider;
  node.dataset.active = String(watch.active);
  $('.badge--provider', node).textContent = watch.provider === 'srt' ? 'SRT' : 'KORAIL';
  const autoBadge = $('.badge--auto-reserve', node);
  if (autoBadge) autoBadge.hidden = !watch.auto_reserve;
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

function renderRunRow(run, onOpen) {
  const node = tpl('tpl-run-row').firstElementChild;
  const concl = run.conclusion || (run.status === 'completed' ? '' : 'in_progress');
  node.querySelector('.run-row__status').dataset.conclusion = concl;
  node.querySelector('.run-row__time').textContent = fmtRunTime(run.started);
  const event = EVENT_LABELS[run.event] || run.event || '';
  const dur = run.status === 'completed' ? fmtDuration(run.started, run.finished) : '진행 중';
  const status = run.conclusion || run.status || '—';
  node.querySelector('.run-row__meta').textContent = `${event} · ${dur} · ${status}`;
  node.querySelector('.run-row__link').href = run.url;
  node.dataset.runId = run.id;
  node.addEventListener('click', e => {
    if (e.target.closest('.run-row__link')) return;
    onOpen(run);
  });
  return node;
}

// Trim a GitHub Actions raw log blob to just the *python stdout* of the
// "Run watcher" step. Skips the noisy `env:` / `shell:` setup block that
// GitHub injects at the start of every step group, so the modal shows
// the actual worker log lines (or the early-exit "skip run" line on
// throttled invocations) instead of a wall of masked secrets.
function extractWatcherStep(logsText) {
  if (!logsText) return '';
  const lines = logsText.split('\n');
  let start = -1;
  let end = lines.length;
  for (let i = 0; i < lines.length; i++) {
    if (start === -1 && /##\[group\].*python -m worker\.main/.test(lines[i])) {
      start = i + 1;
    } else if (start !== -1 && /##\[group\]/.test(lines[i])) {
      end = i;
      break;
    }
  }
  if (start === -1) {
    return lines.slice(-200).map(stripIsoTimestamp).filter(Boolean).join('\n');
  }
  // Skip command echo + env block: trim to after the first ##[endgroup]
  // that closes the step's setup header.
  let actualStart = start;
  for (let i = start; i < end; i++) {
    if (/##\[endgroup\]/.test(lines[i])) {
      actualStart = i + 1;
      break;
    }
  }
  return lines.slice(actualStart, end).map(stripIsoTimestamp).filter(Boolean).join('\n');
}

// True when the watcher exited early because the user-set poll_interval_min
// hadn't elapsed yet. Used to surface a banner in the log dialog so the
// user understands why no train data is shown.
function isSkippedByInterval(watcherText) {
  return /skip run: \d+s since last_run/.test(watcherText || '');
}

function stripIsoTimestamp(line) {
  return line.replace(/^\s*\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?/, '');
}

// ----- main -----------------------------------------------------------------

class App {
  constructor(creds) {
    this.gh = new GitHub(creds);
    this.config = emptyConfig();
    this.state = null;
    this.configSha = null;
    this.cronIntervalMin = 10;
    this._countdownTimer = null;
  }

  async start() {
    document.getElementById('app').innerHTML = '';
    document.getElementById('app').appendChild(tpl('tpl-shell'));

    this._wireFab();
    this._wireSheet();
    this._wireCheckNow();
    this._wireSettings();
    this._wireLogSheet();
    await this._loadAll();
    this._startCountdown();
  }

  async _loadAll() {
    try {
      const [{ sha, content }, stateFile, wranglerFile, workflowFile] = await Promise.all([
        this.gh.getFile('config.json'),
        this.gh.getFile('state.json').catch(() => ({ sha: null, content: null })),
        this.gh.getFile('cloudflare-worker/wrangler.toml').catch(() => ({ sha: null, content: null })),
        this.gh.getFile('.github/workflows/watch.yml').catch(() => ({ sha: null, content: null })),
      ]);
      this.configSha = sha;
      this.config = content ? JSON.parse(content) : emptyConfig();
      this.state = stateFile?.content ? JSON.parse(stateFile.content) : null;
      // Prefer the CF Worker cron over the GHA fallback when present.
      if (!this._parseCronFromWrangler(wranglerFile?.content)) {
        this._parseCronFromWorkflow(workflowFile?.content);
      }
    } catch (e) {
      if (this._isAuthError(e)) {
        this._handleAuthFailure();
        return;
      }
      this._toast(`config.json 로드 실패 — ${e.message}`);
      this.config = emptyConfig();
    }
    this._renderHeader();
    this._renderWatches();
    this._renderSettings();
    this._loadRuns();
  }

  _parseCronFromWrangler(tomlText) {
    if (!tomlText) return false;
    const m = tomlText.match(/crons\s*=\s*\[\s*"([^"]+)"/);
    if (!m) return false;
    const parsed = parseSimpleCron(m[1]);
    if (!parsed) return false;
    this.cronIntervalMin = parsed.intervalMin;
    this.cronSource = 'cf';
    return true;
  }
  _parseCronFromWorkflow(yamlText) {
    if (!yamlText) return;
    const m = yamlText.match(/cron:\s*['"]([^'"]+)['"]/);
    if (!m) return;
    const parsed = parseSimpleCron(m[1]);
    if (parsed) {
      this.cronIntervalMin = parsed.intervalMin;
      this.cronSource = 'gha';
    }
  }

  _isAuthError(e) {
    const msg = String(e?.message || '');
    return msg.includes('401') || msg.includes('Bad credentials') || msg.includes('403');
  }

  _handleAuthFailure() {
    if (this._countdownTimer) clearInterval(this._countdownTimer);
    clearCreds();
    renderSetup(c => new App(c).start());
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
      card.querySelector('.watch__edit').addEventListener('click', () => this._openSheet(w.id));
      card.querySelector('.watch__delete').addEventListener('click', () => this._delete(w.id));
      root.appendChild(card);
    });
  }

  _renderSettings() {
    const toggle = $('#notify-empty-toggle');
    if (toggle) {
      toggle.checked = !!(this.config.settings?.notify_empty_on_cron);
    }
    const select = $('#poll-interval-select');
    if (select) {
      const v = this.config.settings?.poll_interval_min ?? 0;
      select.value = String(v);
    }
  }

  async _loadRuns() {
    const list = $('#run-list');
    if (!list) return;
    try {
      const runs = await this.gh.listRuns('watch.yml', 6);
      list.innerHTML = '';
      if (!runs.length) {
        const li = document.createElement('li');
        li.className = 'run-list--empty';
        li.textContent = '실행 기록 없음';
        list.appendChild(li);
        return;
      }
      runs.forEach(r => list.appendChild(renderRunRow(r, run => this._showRunLogs(run))));
    } catch (e) {
      if (this._isAuthError(e)) {
        this._handleAuthFailure();
        return;
      }
      list.innerHTML = '';
      const li = document.createElement('li');
      li.className = 'run-list--empty';
      li.textContent = `실행 목록 로드 실패 — ${e.message}`;
      list.appendChild(li);
    }
  }

  _startCountdown() {
    const intervalEl = $('#poll-interval');
    const countdownEl = $('#poll-countdown');
    if (!intervalEl || !countdownEl) return;
    const tick = () => {
      const userInterval = Number(this.config.settings?.poll_interval_min) || 0;
      const effective = Math.max(this.cronIntervalMin, userInterval);
      const next = nextTickEpoch(effective);
      const remaining = next - Date.now();
      const sourcePrefix = this.cronSource === 'cf' ? 'CF · ' : '';
      const userTag = userInterval > this.cronIntervalMin ? ` (제한)` : '';
      intervalEl.textContent = `${sourcePrefix}매 ${effective}분 자동${userTag}`;
      countdownEl.textContent = fmtCountdown(remaining);
      if (remaining <= 0) {
        setTimeout(() => this._loadRuns(), 60000);
      }
    };
    tick();
    if (this._countdownTimer) clearInterval(this._countdownTimer);
    this._countdownTimer = setInterval(tick, 1000);
  }

  _wireSettings() {
    const toggle = $('#notify-empty-toggle');
    if (toggle) {
      toggle.addEventListener('change', async () => {
        const enabled = toggle.checked;
        if (!this.config.settings) this.config.settings = {};
        this.config.settings.notify_empty_on_cron = enabled;
        await this._save(`settings: notify_empty_on_cron = ${enabled}`);
      });
    }
    const select = $('#poll-interval-select');
    if (select) {
      select.addEventListener('change', async () => {
        const v = parseInt(select.value, 10) || 0;
        if (!this.config.settings) this.config.settings = {};
        this.config.settings.poll_interval_min = v;
        await this._save(`settings: poll_interval_min = ${v}`);
        this._startCountdown();
      });
    }
  }
  _wireLogSheet() {
    const dialog = $('#log-sheet');
    $('#log-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
  }
  async _showRunLogs(run) {
    const dialog = $('#log-sheet');
    const meta = $('#log-meta');
    const content = $('#log-content');
    const external = $('#log-external');
    external.href = run.url;
    const event = EVENT_LABELS[run.event] || run.event || '';
    const dur = run.status === 'completed' ? fmtDuration(run.started, run.finished) : '진행 중';
    const status = run.conclusion || run.status || '—';
    meta.textContent = `${fmtRunTime(run.started)} · ${event} · ${dur} · ${status}`;
    content.textContent = '로그 불러오는 중…';
    dialog.showModal();

    try {
      const jobs = await this.gh.getRunJobs(run.id);
      if (!jobs.length) {
        content.textContent = '실행 작업이 없습니다.';
        return;
      }
      const logs = await this.gh.getJobLogs(jobs[0].id);
      const watcher = extractWatcherStep(logs);
      const banner = isSkippedByInterval(watcher)
        ? '⏭️  폴링 간격 필터로 실제 조회를 건너뛴 실행입니다.\n     직전 실제 실행에서 좌석 결과를 확인하세요.\n\n'
        : '';
      content.textContent = banner + (watcher || '워커 로그가 비어 있습니다.');
      content.scrollTop = content.scrollHeight;
    } catch (e) {
      if (this._isAuthError(e)) {
        dialog.close();
        this._handleAuthFailure();
        return;
      }
      content.textContent = `로그 가져오기 실패 — ${e.message}\n\n우상단 ↗ 으로 GitHub Actions에서 직접 보세요.`;
    }
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

  async _update(originalId, updated) {
    const idx = this.config.watches.findIndex(w => w.id === originalId);
    if (idx < 0) {
      this.config.watches.push(updated);
    } else {
      const existing = this.config.watches[idx];
      updated.id = existing.id;
      updated.active = existing.active;
      this.config.watches[idx] = updated;
    }
    await this._save(`edit watch ${originalId}`);
    this._renderWatches();
  }

  async _save(message) {
    const body = JSON.stringify(this.config, null, 2) + '\n';
    try {
      const res = await this.gh.putFile('config.json', { content: body, sha: this.configSha, message });
      this.configSha = res.content?.sha ?? this.configSha;
    } catch (e) {
      if (this._isAuthError(e)) { this._handleAuthFailure(); return; }
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
  _wireCheckNow() {
    $('#check-now').addEventListener('click', () => this._checkNow());
  }
  async _checkNow() {
    const btn = $('#check-now');
    if (btn.disabled) return;
    const startedAt = Date.now();
    btn.disabled = true;
    btn.dataset.state = 'running';
    $('#poll-pulse').dataset.state = 'active';
    $('#last-run').textContent = '실행 중…';
    try {
      await this.gh.dispatchWorkflow('watch.yml');
      // workflow takes ~30–50s — poll state.json for last_run advancement
      let detected = false;
      for (let i = 0; i < 14 && !detected; i++) {
        await new Promise(r => setTimeout(r, 5000));
        try {
          const fresh = await this.gh.getFile('state.json');
          if (fresh?.content) {
            const ns = JSON.parse(fresh.content);
            if (ns.last_run && new Date(ns.last_run).getTime() >= startedAt - 5000) {
              this.state = ns;
              detected = true;
            }
          }
        } catch { /* keep polling */ }
      }
      if (!detected) this._toast('확인 요청은 보냈는데 결과 반영이 느립니다 — 잠시 후 새로고침해 주세요.');
    } catch (e) {
      if (String(e.message).includes('403')) {
        this._toast('PAT에 Actions: Read and write 권한이 필요합니다. 설정에서 토큰을 다시 발급해 주세요.');
      } else if (this._isAuthError(e)) {
        this._handleAuthFailure();
        return;
      } else {
        this._toast(`확인 실패 — ${e.message}`);
      }
    } finally {
      btn.disabled = false;
      btn.dataset.state = 'idle';
      this._renderHeader();
      this._renderWatches();
    }
  }
  _wireSheet() {
    const sheet = $('#sheet');
    const form = $('#watch-form');
    const fromSel = form.querySelector('select[name="from"]');
    const toSel = form.querySelector('select[name="to"]');
    const trainGroup = $('#train-type-group');

    const refresh = (provider) => {
      fillStationDropdowns(provider, fromSel, toSel);
      fillTrainTypeChips(provider, trainGroup);
    };

    form.querySelectorAll('input[name="provider"]').forEach(input => {
      input.addEventListener('change', e => refresh(e.target.value));
    });

    $('#sheet-close').addEventListener('click', () => sheet.close());
    $('#sheet-cancel').addEventListener('click', () => sheet.close());
    sheet.addEventListener('click', e => {
      if (e.target === sheet) sheet.close();
    });

    form.addEventListener('submit', async e => {
      e.preventDefault();
      const fd = new FormData(e.target);
      try {
        const watch = formToWatch(fd, e.target);
        if (!watch.from || !watch.to) throw new Error('출발역과 도착역을 선택하세요');
        if (watch.from === watch.to) throw new Error('출발역과 도착역이 같습니다');
        if (!watch.train_types.length) throw new Error('열차 종류를 하나 이상 선택하세요');
        if (watch.time_min > watch.time_max) throw new Error('최저 시간이 최대 시간보다 늦습니다');
        $('#sheet-error').hidden = true;
        const editingId = this._editingWatchId;
        sheet.close();
        if (editingId) {
          await this._update(editingId, watch);
        } else {
          await this._create(watch);
        }
      } catch (err) {
        const errEl = $('#sheet-error');
        errEl.textContent = err.message;
        errEl.hidden = false;
      }
    });

    refresh('korail');
  }
  _openSheet(watchId = null) {
    const sheet = $('#sheet');
    const form = $('#watch-form');
    form.reset();
    this._editingWatchId = watchId || null;
    const editing = watchId ? this.config.watches.find(w => w.id === watchId) : null;
    const provider = editing?.provider || 'korail';
    form.querySelector(`input[name="provider"][value="${provider}"]`).checked = true;
    fillStationDropdowns(provider, form.querySelector('select[name="from"]'), form.querySelector('select[name="to"]'));
    fillTrainTypeChips(provider, $('#train-type-group'));
    if (editing) {
      form.querySelector('select[name="from"]').value = editing.from;
      form.querySelector('select[name="to"]').value = editing.to;
      form.querySelector('input[name="date"]').value = editing.date;
      form.querySelector('input[name="time_min"]').value = editing.time_min;
      form.querySelector('input[name="time_max"]').value = editing.time_max;
      form.querySelector('input[name="adult"]').value = editing.passengers?.adult ?? 1;
      form.querySelector('input[name="child"]').value = editing.passengers?.child ?? 0;
      form.querySelector('input[name="senior"]').value = editing.passengers?.senior ?? 0;
      const seatRadio = form.querySelector(`input[name="seat_class"][value="${editing.seat_class}"]`);
      if (seatRadio) seatRadio.checked = true;
      form.querySelectorAll('input[name="train_type"]').forEach(c => {
        c.checked = (editing.train_types || []).includes(c.value);
      });
      const autoToggle = form.querySelector('input[name="auto_reserve"]');
      if (autoToggle) autoToggle.checked = !!editing.auto_reserve;
    }
    $('#sheet-title').textContent = editing ? '워치 수정' : '새 워치';
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
  try {
    await navigator.serviceWorker.register('./sw.js', { scope: './' });
    navigator.serviceWorker.addEventListener('message', e => {
      if (e.data?.type !== 'sw-updated') return;
      // Only reload if the user isn't mid-interaction with a dialog.
      const open = document.querySelector('dialog[open]');
      if (!open) location.reload();
    });
  } catch (e) {
    console.warn('SW registration failed', e);
  }
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
