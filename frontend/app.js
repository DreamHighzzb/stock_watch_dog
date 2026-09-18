/* ============================================================
 *  stock-radar 前端逻辑（多用户版）
 *  数据来源：WebSocket /ws?token=xxx  （每 1 秒推送当前用户行情）
 *  配置 CRUD：REST /api/*  （需携带登录 token）
 *  鉴权：登录/注册后拿到 token，存 localStorage，统一经 apiFetch 携带
 * ============================================================ */

const state = {
  token: localStorage.getItem('sr_token') || null,
  user: null,                // { username, user_id }
  tabs: {},                  // { tabName: [stock, ...] }  stock 含 quote 字段
  currentTab: null,
  sortMode: 'none',
  selectedCodes: new Set(),
  compact: localStorage.getItem('sr_compact') === '1',  // 简化显示
  ws: null,
  reconnectDelay: 1000,
  authMode: 'login',         // 'login' | 'register'
};

const $ = (id) => document.getElementById(id);

/* 简化显示时保留的列数（序号、名称、涨跌、现价、成本价、股数 = 6 列） */
const COMPACT_COLS = 6;

/* ---------------- 初始化 ---------------- */
document.addEventListener('DOMContentLoaded', () => {
  bindUI();
  startClock();
  applyCompactMode();
  checkAuth();
});

/* ============================================================
 *  鉴权
 * ============================================================ */
async function checkAuth() {
  if (!state.token) { showAuth(); return; }
  try {
    const res = await fetch('/api/auth/me', {
      headers: { 'Authorization': 'Bearer ' + state.token },
    });
    if (!res.ok) throw new Error('unauthorized');
    const data = await res.json();
    onLoggedIn(data);
  } catch {
    localStorage.removeItem('sr_token');
    state.token = null;
    state.user = null;
    showAuth();
  }
}

function showAuth() {
  $('authOverlay').classList.remove('hidden');
  $('userbar').classList.add('hidden');
  $('authUser').value = '';
  $('authPass').value = '';
  setAuthMode('login');
  setTimeout(() => $('authUser').focus(), 50);
}

function hideAuth() {
  $('authOverlay').classList.add('hidden');
  $('userbar').classList.remove('hidden');
}

function setAuthMode(mode) {
  state.authMode = mode;
  if (mode === 'login') {
    $('authModeText').textContent = '登录你的账户';
    $('authSubmit').textContent = '登 录';
    $('authSwitchText').textContent = '还没有账号？';
    $('authSwitch').textContent = '去注册';
    $('authPass').setAttribute('autocomplete', 'current-password');
  } else {
    $('authModeText').textContent = '注册新账户';
    $('authSubmit').textContent = '注 册';
    $('authSwitchText').textContent = '已有账号？';
    $('authSwitch').textContent = '去登录';
    $('authPass').setAttribute('autocomplete', 'new-password');
  }
}

function onLoggedIn(data) {
  state.user = { username: data.username, user_id: data.user_id };
  hideAuth();
  $('userName').textContent = '👤 ' + data.username;
  loadConfig().then(connectWS);
}

async function submitAuth() {
  const username = $('authUser').value.trim();
  const password = $('authPass').value;
  if (!username) return showAuthError('请输入用户名');
  if (password.length < 6) return showAuthError('密码至少 6 位');

  const url = state.authMode === 'login'
    ? '/api/auth/login' : '/api/auth/register';
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return showAuthError(data.detail || (state.authMode === 'login' ? '登录失败' : '注册失败'));
    }
    state.token = data.token;
    localStorage.setItem('sr_token', data.token);
    clearAuthError();
    onLoggedIn({ username: data.username, user_id: data.user_id });
  } catch (e) {
    showAuthError('网络错误: ' + e.message);
  }
}

function showAuthError(msg) {
  const el = $('authError');
  el.textContent = msg;
  el.classList.remove('hidden');
}
function clearAuthError() {
  const el = $('authError');
  el.textContent = '';
  el.classList.add('hidden');
}

async function logout() {
  try {
    if (state.token) {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + state.token },
      });
    }
  } catch {}
  if (state.ws) { try { state.ws.close(); } catch {} state.ws = null; }
  localStorage.removeItem('sr_token');
  state.token = null;
  state.user = null;
  state.tabs = {};
  state.currentTab = null;
  state.selectedCodes.clear();
  renderTabs();
  renderTable();
  syncTabSelect();
  showAuth();
}

/* ---------------- 修改密码 ---------------- */
function openChangePwDialog() {
  $('oldPw').value = '';
  $('newPw').value = '';
  clearChangePwError();
  $('changePwDialog').classList.remove('hidden');
  setTimeout(() => $('oldPw').focus(), 50);
}

function showChangePwError(msg) {
  const el = $('changePwError');
  el.textContent = msg;
  el.classList.remove('hidden');
}
function clearChangePwError() {
  const el = $('changePwError');
  el.textContent = '';
  el.classList.add('hidden');
}

async function submitChangePw() {
  const old_password = $('oldPw').value;
  const new_password = $('newPw').value;
  if (new_password.length < 6) return showChangePwError('新密码至少 6 位');

  try {
    const res = await apiFetch('/api/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_password, new_password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      return showChangePwError(err.detail || '修改失败');
    }
    $('changePwDialog').classList.add('hidden');
    toast('密码已修改，请重新登录', 'success', 4000);
    setTimeout(logout, 800);
  } catch (e) {
    showChangePwError('网络错误: ' + e.message);
  }
}

/* ============================================================
 *  带 token 的 fetch 封装
 * ============================================================ */
async function apiFetch(url, options = {}) {
  const headers = options.headers || {};
  if (state.token) headers['Authorization'] = 'Bearer ' + state.token;
  const opts = { ...options, headers };
  const res = await fetch(url, opts);
  if (res.status === 401) {
    localStorage.removeItem('sr_token');
    state.token = null;
    state.user = null;
    showAuth();
    throw new Error('登录已过期，请重新登录');
  }
  return res;
}

/* ---------------- 时钟 ---------------- */
function startClock() {
  const el = $('clock');
  const tick = () => {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    el.textContent =
      `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ` +
      `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  };
  tick();
  setInterval(tick, 1000);
}

/* ============================================================
 *  简化显示：JS 直接控制列的显示/隐藏
 * ============================================================ */
function applyCompactMode() {
  const compact = !!state.compact;

  // 1) 控制表头 th 的显示 / 隐藏
  const ths = document.querySelectorAll('#stockTable thead th');
  ths.forEach((th, idx) => {
    th.style.display = (!compact || idx < COMPACT_COLS) ? '' : 'none';
  });

  // 2) 让表格支持简化列宽（CSS 里已写好 #stockTable.compact 的列宽分配）
  const table = $('stockTable');
  if (table) table.classList.toggle('compact', compact);

  // 3) 同步复选框状态
  const cb = $('compactToggle');
  if (cb) cb.checked = compact;

  // 4) 重新渲染表格主体（td 的显示 / 隐藏由 renderTable 控制）
  renderTable();
}

/* ============================================================
 *  轻提示（toast）
 * ============================================================ */
function toast(msg, type = 'info', duration = 2500, actions = null) {
  const box = $('toastContainer');
  if (!box) { console.log(`[toast:${type}]`, msg); return () => {}; }

  const el = document.createElement('div');
  el.className = `toast ${type}`;

  const text = document.createElement('span');
  text.className = 'toast-text';
  text.textContent = msg;
  el.appendChild(text);

  let timer = null;
  function dismiss() {
    if (timer) clearTimeout(timer);
    el.style.transition = 'opacity .25s, transform .25s';
    el.style.opacity = '0';
    el.style.transform = 'translateY(-8px)';
    setTimeout(() => el.remove(), 260);
  }

  if (actions) {
    for (const a of actions) {
      const btn = document.createElement('button');
      btn.className = 'toast-action';
      btn.textContent = a.label;
      btn.onclick = () => {
        try { a.onClick && a.onClick(); } finally { dismiss(); }
      };
      el.appendChild(btn);
    }
  }

  box.appendChild(el);
  if (duration > 0) timer = setTimeout(dismiss, duration);
  return dismiss;
}

function toastSuccess(msg) {
  return toast(msg, 'success', 6000, [
    { label: '刷新页面', onClick: () => refreshTabs().catch(() => {}) },
  ]);
}

/* ============================================================
 *  WebSocket
 * ============================================================ */
function connectWS() {
  if (!state.token) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(state.token)}`);
  state.ws = ws;

  ws.onopen = () => {
    console.log('[ws] connected');
    state.reconnectDelay = 1000;
  };

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'quotes') {
        state.tabs = msg.data;
        ensureCurrentTab();
        renderTabs();
        renderTable();
        syncTabSelect();
      } else if (msg.type === 'alert') {
        showAlert(msg.data);
      }
    } catch (e) {
      console.error('[ws] 消息解析失败', e);
    }
  };

  ws.onclose = () => {
    if (!state.token) return;
    console.warn('[ws] disconnected, retrying...');
    setTimeout(connectWS, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 1.5, 10000);
  };

  ws.onerror = () => ws.close();
}

/* ============================================================
 *  手动拉取最新配置
 * ============================================================ */
async function refreshTabs(extraQuotes = {}) {
  const res = await apiFetch('/api/config');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();

  const oldTabs = state.tabs;
  const newTabs = {};
  for (const [name, stocks] of Object.entries(data)) {
    newTabs[name] = stocks.map((s) => ({
      ...s,
      quote:
        extraQuotes[s.code] ||
        oldTabs[name]?.find((x) => x.code === s.code)?.quote ||
        {},
    }));
  }
  state.tabs = newTabs;

  const validCodes = new Set(
    Object.values(state.tabs).flatMap((arr) => arr.map((s) => s.code))
  );
  for (const c of [...state.selectedCodes]) {
    if (!validCodes.has(c)) state.selectedCodes.delete(c);
  }

  ensureCurrentTab();
  renderTabs();
  renderTable();
  syncTabSelect();
}

async function loadConfig() {
  try {
    await refreshTabs();
  } catch (e) {
    console.error('加载配置失败', e);
    toast('加载配置失败: ' + e.message, 'error', 6000);
  }
}

/* ---------------- 页签 ---------------- */
function ensureCurrentTab() {
  const names = Object.keys(state.tabs);
  if (!names.length) { state.currentTab = null; return; }
  if (!names.includes(state.currentTab)) state.currentTab = names[0];
}

function renderTabs() {
  const box = $('tabs');
  box.innerHTML = '';
  Object.keys(state.tabs).forEach((name) => {
    const div = document.createElement('div');
    div.className = 'tab' + (name === state.currentTab ? ' active' : '');
    div.textContent = name;
    div.onclick = () => {
      state.currentTab = name;
      state.selectedCodes.clear();
      renderTabs();
      renderTable();
      syncTabSelect();
    };
    box.appendChild(div);
  });
}

function syncTabSelect() {
  const sel = $('targetTabSelect');
  const names = Object.keys(state.tabs);
  sel.innerHTML = '';
  if (!names.length) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '（暂无页签）';
    sel.appendChild(opt);
    return;
  }
  names.forEach((n) => {
    const opt = document.createElement('option');
    opt.value = n; opt.textContent = n;
    sel.appendChild(opt);
  });
  if (state.currentTab) sel.value = state.currentTab;
}

/* ---------------- 排序 ---------------- */
function sortStocks(list) {
  const mode = state.sortMode;
  if (!mode || mode === 'none') return list.slice();
  const parts = mode.split('_');
  if (parts.length !== 2) return list.slice();
  const [col, dir] = parts;

  const keyOf = (s) => {
    const q = s.quote || {};
    switch (col) {
      case 'price':     return q.valid ? (q.price || 0) : 0;
      case 'change':    return q.valid ? (q.change_pct || 0) : 0;
      case 'buy':       return s.buy_price != null ? s.buy_price : 0;
      case 'low':       return q.valid ? (q.low || 0) : 0;
      case 'high':      return q.valid ? (q.high || 0) : 0;
      case 'pre_close': return q.valid ? (q.pre_close || 0) : 0;
      default:          return 0;
    }
  };
  const reverse = dir === 'desc';
  const sorted = list.slice();
  sorted.sort((a, b) => reverse ? keyOf(b) - keyOf(a) : keyOf(a) - keyOf(b));
  return sorted;
}

/* ---------------- 表格渲染 ----------------
 * 关键：根据 state.compact 决定是否追加后 5 列的 <td>
 * -------------------------------------------- */
function renderTable() {
  const tbody = document.querySelector('#stockTable tbody');
  tbody.innerHTML = '';

  const list = state.tabs[state.currentTab] || [];
  const sorted = sortStocks(list);
  const compact = !!state.compact;

  // 持仓当天总盈亏（仅统计已设置股数的持仓）
  let totalPnl = 0;
  let heldCount = 0;

  sorted.forEach((s, i) => {
    const q = s.quote || {};
    const tr = document.createElement('tr');
    tr.className = 'row';
    tr.dataset.code = s.code;
    if (state.selectedCodes.has(s.code)) tr.classList.add('selected');

    // 1) 序号
    tr.appendChild(td(String(i + 1)));

    // 2) 名称
    tr.appendChild(tdHtml(`${esc(s.name)}<div class="sub">[${esc(s.code)}]</div>`));

    // 3) 涨跌
    let changeHtml = '--', changeCls = '';
    if (q.valid) {
      const pct = q.change_pct, amt = q.change_amount;
      changeCls = pct > 0 ? 'up' : (pct < 0 ? 'down' : '');
      const sign = pct >= 0 ? '+' : '';
      changeHtml =
        `${sign}${pct.toFixed(2)}%<div class="sub">[${sign}${amt.toFixed(3)}]</div>`;
    } else {
      changeHtml = `--<div class="sub">[--]</div>`;
    }
    tr.appendChild(tdHtml(changeHtml, changeCls));

    // 4) 现价
    tr.appendChild(td(q.valid ? q.price.toFixed(3) : '--'));

    // 5) 成本价
    let buyHtml = '-';
    if (s.buy_price != null) {
      if (q.valid && s.buy_price > 0) {
        const profit = (q.price - s.buy_price) / s.buy_price * 100;
        const sign = profit >= 0 ? '+' : '';
        buyHtml = `${s.buy_price.toFixed(3)}<div class="sub">盈亏: ${sign}${profit.toFixed(2)}%</div>`;
      } else {
        buyHtml = `${s.buy_price.toFixed(3)}<div class="sub">盈亏: --</div>`;
      }
    }
    tr.appendChild(tdHtml(buyHtml));

    // 6) 股数（始终显示）
    const shares = (s.shares != null) ? parseFloat(s.shares) : 0;
    tr.appendChild(td(shares > 0 ? formatNum(shares) : (s.shares != null ? '0' : '--')));

    // 累计当天盈亏：(现价 - 昨收) * 股数
    if (q.valid && shares > 0) {
      totalPnl += (q.price - q.pre_close) * shares;
      heldCount++;
    }

    // 7~11) 简化显示时直接不渲染这几列（保证一定不显示）
    if (!compact) {
      tr.appendChild(td(q.valid ? q.low.toFixed(2) : '--'));       // 低价
      tr.appendChild(td(q.valid ? q.high.toFixed(2) : '--'));      // 高价
      tr.appendChild(td(q.valid ? q.pre_close.toFixed(2) : '--')); // 昨价
      tr.appendChild(td(s.up_warning != null ? s.up_warning : '未设'));   // 预警涨价
      tr.appendChild(td(s.down_warning != null ? s.down_warning : '未设')); // 预警跌价
    }

    tr.onclick = (e) => handleRowClick(s.code, e.ctrlKey || e.metaKey);
    tr.ondblclick = () => openEditDialog(s);

    tbody.appendChild(tr);
  });

  updatePnlSummary(totalPnl, heldCount);
}

/* 千分位格式化（整数带逗号，小数保留原样） */
function formatNum(n) {
  if (!Number.isFinite(n)) return '--';
  const isInt = Math.floor(n) === n;
  if (isInt) return n.toLocaleString('zh-CN');
  return String(n);
}

/* 更新持仓当天总盈亏汇总条 */
function updatePnlSummary(total, count) {
  const el = $('pnlSummary');
  if (!el) return;
  if (count === 0) {
    el.innerHTML = '持仓当天总盈亏：<span class="pnl-zero">本页签暂无持仓（未设置股数）</span>';
    return;
  }
  const sign = total >= 0 ? '+' : '';
  const cls = total > 0 ? 'pnl-up' : (total < 0 ? 'pnl-down' : 'pnl-zero');
  el.innerHTML =
    `持仓当天总盈亏：<span class="${cls}">${sign}${total.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元</span>` +
    `<span class="pnl-count">（${count} 只持仓 · 计算公式：(现价-昨收)×股数）</span>`;
}

function td(text) {
  const cell = document.createElement('td');
  cell.textContent = text;
  return cell;
}
function tdHtml(html, cls) {
  const cell = document.createElement('td');
  if (cls) cell.className = cls;
  cell.innerHTML = html;
  return cell;
}
function esc(str) {
  return String(str ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function handleRowClick(code, multi) {
  if (multi) {
    state.selectedCodes.has(code)
      ? state.selectedCodes.delete(code)
      : state.selectedCodes.add(code);
  } else {
    if (state.selectedCodes.size === 1 && state.selectedCodes.has(code)) {
      state.selectedCodes.clear();
    } else {
      state.selectedCodes.clear();
      state.selectedCodes.add(code);
    }
  }
  renderTable();
}

/* ---------------- 表头排序 ---------------- */
const SORTABLE_COLS = ['price', 'change', 'buy', 'low', 'high', 'pre_close'];

document.addEventListener('click', (e) => {
  const th = e.target.closest('th[data-col]');
  if (!th) return;
  if (th.style.display === 'none') return;  // 隐藏的列不响应排序
  const col = th.dataset.col;
  if (!SORTABLE_COLS.includes(col)) return;

  if (state.sortMode === `${col}_desc`) {
    state.sortMode = `${col}_asc`;
  } else if (state.sortMode === `${col}_asc`) {
    state.sortMode = `${col}_desc`;
  } else {
    state.sortMode = `${col}_desc`;
  }
  syncSortRadio();
  renderTable();
});

function syncSortRadio() {
  const el = document.querySelector(`input[name="sort"][value="${state.sortMode}"]`);
  document.querySelectorAll('input[name="sort"]').forEach((r) => (r.checked = false));
  if (el) el.checked = true;
}

/* ---------------- UI 事件绑定 ---------------- */
function bindUI() {
  document.querySelectorAll('input[name="sort"]').forEach((r) => {
    r.onchange = () => {
      state.sortMode = r.value;
      renderTable();
    };
  });

  // 简化显示复选框
  const compactToggle = $('compactToggle');
  if (compactToggle) {
    compactToggle.checked = !!state.compact;
    compactToggle.onchange = (e) => {
      state.compact = e.target.checked;
      localStorage.setItem('sr_compact', state.compact ? '1' : '0');
      applyCompactMode();   // 同步表头 + 重渲染表格主体
    };
  }

  $('addStockBtn').onclick = addStock;
  $('delStockBtn').onclick = deleteSelectedStocks;
  $('addTabBtn').onclick   = () => openTabDialog();
  $('delTabBtn').onclick   = deleteCurrentTab;

  $('editCancel').onclick = () => $('editDialog').classList.add('hidden');
  $('editSave').onclick   = saveEdit;
  $('tabCancel').onclick  = () => $('tabDialog').classList.add('hidden');
  $('tabSave').onclick    = saveNewTab;

  $('authSubmit').onclick = submitAuth;
  $('authSwitch').onclick = () => setAuthMode(state.authMode === 'login' ? 'register' : 'login');
  $('logoutBtn').onclick  = logout;
  $('changePwBtn').onclick = openChangePwDialog;
  $('changePwCancel').onclick = () => $('changePwDialog').classList.add('hidden');
  $('changePwSave').onclick = submitChangePw;
  $('authPass').addEventListener('keydown', (e) => { if (e.key === 'Enter') submitAuth(); });
  $('authUser').addEventListener('keydown', (e) => { if (e.key === 'Enter') submitAuth(); });
}

/* ---------------- 校验辅助 ---------------- */
function parsePriceInput(id) {
  const v = $(id).value.trim();
  if (!v) return null;
  const n = parseFloat(v);
  return Number.isFinite(n) && n > 0 ? n : NaN;
}

function parseSharesInput(id) {
  const v = $(id).value.trim();
  if (!v) return null;          // 留空 = 未设置 / 不修改
  const n = parseFloat(v);
  return Number.isFinite(n) && n >= 0 ? n : NaN;
}

/* ---------------- 添加股票 ---------------- */
async function addStock() {
  const code = $('codeInput').value.trim();
  if (!code) return toast('请输入股票代码', 'error');
  const tab = $('targetTabSelect').value;
  if (!tab) return toast('请选择目标页签', 'error');

  const up = parsePriceInput('upInput');
  const down = parsePriceInput('downInput');
  const buy = parsePriceInput('buyInput');
  if ([up, down, buy].some((v) => Number.isNaN(v))) {
    return toast('价格必须为大于 0 的数字', 'error');
  }

  const shares = parseSharesInput('sharesInput');
  if (Number.isNaN(shares)) {
    return toast('股数必须为不小于 0 的数字', 'error');
  }

  try {
    const body = {
      code,
      up_warning: up,
      down_warning: down,
      buy_price: buy,
    };
    if (shares !== null) body.shares = shares;
    const res = await apiFetch(`/api/tabs/${encodeURIComponent(tab)}/stocks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      return toast('添加失败: ' + (err.detail || res.statusText), 'error', 4000);
    }

    const stock = await res.json();
    ['codeInput', 'upInput', 'downInput', 'buyInput', 'sharesInput'].forEach((id) => ($(id).value = ''));
    toastSuccess(`已添加 ${stock.name || stock.code} 到 [${tab}]`);
  } catch (e) {
    toast('网络错误: ' + e.message, 'error', 4000);
  }
}

/* ---------------- 删除选中股票 ---------------- */
async function deleteSelectedStocks() {
  const codes = [...state.selectedCodes];
  if (!codes.length) return toast('请先选中要删除的股票', 'error');
  if (!confirm(`确定删除 ${codes.length} 只股票？`)) return;

  const tab = state.currentTab;
  let okCount = 0, failCount = 0;
  for (const code of codes) {
    try {
      const res = await apiFetch(
        `/api/tabs/${encodeURIComponent(tab)}/stocks/${encodeURIComponent(code)}`,
        { method: 'DELETE' }
      );
      if (res.ok) okCount++; else failCount++;
    } catch {
      failCount++;
    }
  }

  if (failCount === 0) {
    state.selectedCodes.clear();
    toastSuccess(`已删除 ${okCount} 只股票`);
  } else if (okCount === 0) {
    toast(`删除失败：${failCount} 只均未成功`, 'error', 4000);
  } else {
    state.selectedCodes.clear();
    toastSuccess(`删除完成：成功 ${okCount}，失败 ${failCount}`);
  }
}

/* ---------------- 编辑股票 ---------------- */
let editingStock = null;
function openEditDialog(stock) {
  editingStock = stock;
  $('editTitle').textContent = `修改 - ${stock.name}(${stock.code})`;
  $('editBuy').value  = stock.buy_price   ?? '';
  $('editShares').value = stock.shares    ?? '';
  $('editUp').value   = stock.up_warning  ?? '';
  $('editDown').value = stock.down_warning?? '';
  $('editDialog').classList.remove('hidden');
}

async function saveEdit() {
  if (!editingStock) return;
  const buy  = parsePriceInput('editBuy');
  const up   = parsePriceInput('editUp');
  const down = parsePriceInput('editDown');
  if ([buy, up, down].some((v) => Number.isNaN(v))) {
    return toast('价格必须为大于 0 的数字', 'error');
  }
  const shares = parseSharesInput('editShares');
  if (Number.isNaN(shares)) {
    return toast('股数必须为不小于 0 的数字', 'error');
  }

  const tab = state.currentTab;
  const code = editingStock.code;
  const name = editingStock.name;

  try {
    const body = { up_warning: up, down_warning: down, buy_price: buy };
    if (shares !== null) body.shares = shares;
    const res = await apiFetch(
      `/api/tabs/${encodeURIComponent(tab)}/stocks/${encodeURIComponent(code)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      return toast('保存失败: ' + (err.detail || res.statusText), 'error', 4000);
    }
    $('editDialog').classList.add('hidden');
    editingStock = null;
    toastSuccess(`已保存 ${name}(${code})`);
  } catch (e) {
    toast('网络错误: ' + e.message, 'error', 4000);
  }
}

/* ---------------- 页签 CRUD ---------------- */
function openTabDialog() {
  $('newTabName').value = `页签${Object.keys(state.tabs).length + 1}`;
  $('tabDialog').classList.remove('hidden');
  $('newTabName').focus();
}

async function saveNewTab() {
  const name = $('newTabName').value.trim();
  if (!name) return toast('请输入页签名称', 'error');

  try {
    const res = await apiFetch('/api/tabs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      return toast('创建失败: ' + (err.detail || res.statusText), 'error', 4000);
    }
    $('tabDialog').classList.add('hidden');
    toastSuccess(`页签 [${name}] 已创建`);
  } catch (e) {
    toast('网络错误: ' + e.message, 'error', 4000);
  }
}

async function deleteCurrentTab() {
  const name = state.currentTab;
  if (!name) return toast('当前没有页签', 'error');
  if (['自选', '持仓'].includes(name)) return toast('默认页签不可删除', 'error');
  if (!confirm(`确定删除页签 [${name}] 及其所有股票？`)) return;

  try {
    const res = await apiFetch(`/api/tabs/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      return toast('删除失败: ' + (err.detail || res.statusText), 'error', 4000);
    }
    toastSuccess(`页签 [${name}] 已删除`);
  } catch (e) {
    toast('网络错误: ' + e.message, 'error', 4000);
  }
}

/* ---------------- 预警弹窗 ---------------- */
function showAlert(alertData) {
  const card = document.createElement('div');
  card.className = `alert-card ${alertData.type}`;
  card.innerHTML =
    `<div class="title">${esc(alertData.title)}</div>` +
    `<div>${esc(alertData.message)}</div>`;
  $('alertContainer').appendChild(card);

  setTimeout(() => {
    card.style.transition = 'opacity .3s';
    card.style.opacity = '0';
    setTimeout(() => card.remove(), 300);
  }, 4000);
}