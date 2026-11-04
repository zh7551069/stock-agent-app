/* 数据导入台 · stock-advice  (2026-10-28 重构)
   核心变化：① 覆盖度看板（缺什么包一眼看到）② 文件按类分组 ③ 导入后自动刷新覆盖度 */
(() => {
  const $ = (s) => document.querySelector(s);
  const el = {
    dbInfo: $('#dbInfo'), dateIn: $('#dateIn'),
    btnRefresh: $('#btnRefresh'), btnMeta: $('#btnMeta'),
    btnPurge: $('#btnPurge'), purgeN: $('#purgeN'),
    covPanel: $('#covPanel'),
    covPct: $('#covPct'), covRing: $('#covRing'), covTitle: $('#covTitle'),
    covSub: $('#covSub'), covCats: $('#covCats'),
    covMissing: $('#covMissing'), covMissingBody: $('#covMissingBody'),
    covStale: $('#covStale'), covStaleTitle: $('#covStaleTitle'),
    covStaleDesc: $('#covStaleDesc'),
    covWait: $('#covWait'), covWaitTitle: $('#covWaitTitle'),
    covWaitDesc: $('#covWaitDesc'), btnWaitReload: $('#btnWaitReload'),
    covTables: $('#covTables'), packList: $('#packList'),
    drop: $('#drop'), fileInput: $('#fileInput'), btnPick: $('#btnPick'),
    fileList: $('#fileList'), importBar: $('#importBar'),
    chkAll: $('#chkAll'), chkDup: $('#chkDup'), chkQueue: $('#chkQueue'),
    selInfo: $('#selInfo'), btnDry: $('#btnDry'), btnImport: $('#btnImport'),
    progress: $('#progress'), progText: $('#progText'), progPct: $('#progPct'), progBar: $('#progBar'),
    results: $('#results'), batches: $('#batches'), toast: $('#toast'),
  };

  let staged = [];       // 当前批次文件
  let batch = '';
  let targetDate = '';
  let polling = null;

  const CAT_ORDER = ['日线', '因子', '分钟', '资金流', '涨停池', '龙虎榜', '题材', '财务', '基础', '热度', '其他'];
  const CAT_ICON = { 日线: '📈', 因子: '🧮', 分钟: '⏱', 资金流: '💰', 涨停池: '🔥',
                     龙虎榜: '🐉', 题材: '🧩', 财务: '📊', 基础: '🗂', 热度: '⭐', 其他: '•' };

  const fmtSize = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + 'MB'
                    : n > 1024 ? (n / 1024).toFixed(0) + 'KB' : n + 'B';
  const normDate = (s) => { const m = /(\d{4})[-/]?(\d{2})[-/]?(\d{2})/.exec(s || ''); return m ? m[1] + m[2] + m[3] : ''; };

  function toast(msg, kind) {
    el.toast.textContent = msg;
    el.toast.className = 'toast show' + (kind ? ' ' + kind : '');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.toast.className = 'toast'; }, 2600);
  }

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  async function api(path, opt) {
    const o = Object.assign({}, opt || {});
    const ms = o.timeoutMs || 30000;
    delete o.timeoutMs;
    const ac = new AbortController();
    o.signal = ac.signal;
    const tid = setTimeout(() => ac.abort(), ms);
    let r;
    try {
      r = await fetch(path, o);
    } catch (e) {
      if (e && e.name === 'AbortError') {
        throw new Error(`请求超时（已等 ${Math.round(ms / 1000)} 秒，后端没响应）`);
      }
      throw new Error('连不上后端服务（8770 可能已退出）');
    } finally {
      clearTimeout(tid);
    }
    if (r.status === 401) {
      location.replace('/login?next=' + encodeURIComponent(location.pathname + location.search));
      throw new Error('需要登录');
    }
    const j = await r.json().catch(() => ({}));
    // 🆕 2026-11-03：后端带 hint（如「进程无外置盘写权限」）时一并透出，
    //   否则用户只看到裸 IOException 反复重试。
    if (!r.ok) throw new Error(j.hint ? (j.hint + '\n（' + (j.error || '') + '）')
                                      : (j.error || ('HTTP ' + r.status)));
    return j;
  }

  /* ───────── 覆盖度 ───────── */
  let covBusy = false;

  function setCovLoading(msg, sub) {
    covBusy = true;
    el.covPanel.classList.add('loading');
    el.covRing.className = 'cov-ring loading';
    el.covPct.textContent = '·';
    el.covTitle.innerHTML = '<span class="cov-spin"></span>' + esc(msg || '正在读取覆盖度…');
    el.covSub.textContent = sub || '正在读 table_meta（缓存 60 秒，一般 1 秒内出结果）';
    el.covCats.innerHTML = '';
    el.covMissing.hidden = true;
    el.covTables.innerHTML = '<div class="cov-skel"></div>'.repeat(6);
  }

  function endCovLoading() {
    covBusy = false;
    if (el.covPanel) el.covPanel.classList.remove('loading');
    if (el.covRing) el.covRing.classList.remove('loading');
  }

  function covError(msg, retryDate) {
    el.covPct.textContent = '--';
    el.covRing.className = 'cov-ring low';
    el.covSub.textContent = '服务恢复后点右边按钮重试，或检查 8770 进程是否还在';
    el.covCats.innerHTML = '';
    el.covTables.innerHTML = '';
    el.covMissing.hidden = true;
    el.covTitle.innerHTML = '⚠️ 覆盖度读取失败：' + esc(msg)
      + ' <button class="cov-retry" id="covRetry">重新读取</button>';
    const btn = document.getElementById('covRetry');
    if (btn) btn.onclick = (ev) => { ev.preventDefault(); loadCoverage(retryDate); };
  }

  async function loadCoverage(date) {
    if (covBusy) return null;                     // 防重复点
    const d = (date || '').trim();
    setCovLoading('正在读取覆盖度…');
    try {
      const q = d ? ('?date=' + encodeURIComponent(d)) : '';
      const cov = await api('/api/upload/coverage' + q, { timeoutMs: 45000 });
      endCovLoading();
      renderCoverage(cov);
      return cov;
    } catch (e) {
      endCovLoading();
      covError((e && e.message) || String(e), d);
      return null;
    }
  }

  function fmtD(v) {
    const t = String(v || '').replace(/\D/g, '');
    return t.length === 8 ? `${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)}` : (v || '');
  }

  function renderCoverage(cov) {
    const pct = cov.pct || 0;
    el.covPct.textContent = pct.toFixed(0);
    el.covRing.style.setProperty('--pct', pct);
    el.covRing.className = 'cov-ring ' + (pct >= 100 ? 'full' : pct >= 70 ? 'mid' : 'low');
    // 2026-10-28 修：绝不再自动填充日期（曾导致 20261028.csv 被当成 20261027 处理，误删 12 表）
    // dateIn 留空 = 每个文件用自己的文件名日期（正确做法）

    // 🔴 2026-10-31 修：数据滞后告警。原逻辑用「库里 daily 最新日」当基准
    //    → 数据停更时基准跟着停 → 拿 10-29 判 10-29 = 恒 100% 全绿，永不报警。
    //    现在基准 = 应到的最新已收盘交易日（后端 expect_date()），滞后即红字横幅。
    const stale = !!cov.stale;
    const lag = cov.lag_days || 0;
    if (el.covStale) {
      if (stale && lag > 0) {
        // 分级：「今天该导还没导」= 正常节奏（盘后导入）→ 弱提示
        //       其余 = 真滞后 → 橙色警告
        const today = (lag === 1 && cov.expect_is_today);
        el.covStale.hidden = false;
        el.covStale.className = 'cov-stale' + (today ? ' soft' : '');
        el.covStaleTitle.textContent = today
          ? `今天（${fmtD(cov.expect)}）的数据还没导入`
          : `数据滞后 ${lag} 个交易日 —— 应到 ${fmtD(cov.expect)}，库里只到 ${fmtD(cov.latest_daily)}`;
        el.covStaleDesc.textContent = today
          ? `这是正常节奏：盘后把当天的包传上来即可。下面先按 ${fmtD(cov.expect)} 判定。`
          : `下面按「应到日 ${fmtD(cov.expect)}」判定，所以显示大面积缺失。`
            + `把 ${fmtD(cov.expect)} 的包传上来即可；若包还没下载，先下载。`;
      } else {
        el.covStale.hidden = true;
      }
    }

    el.covTitle.textContent = stale
      ? `应到 ${fmtD(cov.expect)} · 必备 ${cov.total} 张表，已到位 ${cov.ok_count} 张`
      : `目标交易日 ${cov.date || '?'} · 必备 ${cov.total} 张表，已到位 ${cov.ok_count} 张`;
    const gap = cov.total - cov.ok_count;
    el.covSub.textContent = gap === 0
      ? '✅ 全部到位，可以做盘后分析了'
      : stale
        ? `还缺 ${gap} 张 —— 主因是 ${fmtD(cov.expect)} 的数据还没导入，下方红框写着该补哪个包`
        : `还缺 ${gap} 张 —— 下方红框里写着该补哪个包`;

    el.covCats.innerHTML = (cov.by_cat || []).map((c) => {
      const done = c.ok >= c.total;
      return `<span class="cat-pill ${done ? 'ok' : 'miss'}" title="${done ? '全部到位' : '缺：' + c.missing.join(', ')}">
        ${CAT_ICON[c.cat] || '•'} ${c.cat} ${c.ok}/${c.total}</span>`;
    }).join('');

    // 缺包清单：按 hint 聚合（同一个包能补多张表）
    const miss = cov.missing || [];
    if (!miss.length) {
      el.covMissing.hidden = true;
    } else {
      el.covMissing.hidden = false;
      const groups = {};
      miss.forEach((m) => {
        const k = m.hint;
        (groups[k] = groups[k] || { hint: m.hint, cat: m.cat, tables: [] }).tables.push(m.label);
      });
      el.covMissingBody.innerHTML = Object.values(groups).map((g) =>
        `<div class="miss-row">
           <span class="miss-cat">${CAT_ICON[g.cat] || '•'} ${g.cat}</span>
           <span class="miss-hint">${g.hint}</span>
           <span class="miss-tables">${g.tables.join(' / ')}</span>
         </div>`).join('');
    }

    // 侧栏：全表清单
    el.covTables.innerHTML = [...(cov.ok || []), ...miss]
      .sort((a, b) => (CAT_ORDER.indexOf(a.cat) - CAT_ORDER.indexOf(b.cat)) || a.table.localeCompare(b.table))
      .map((m) => `<div class="trow ${m.max ? 'has' : 'no'}">
          <span class="tname" title="${m.table}">${m.label}</span>
          <span class="tdate">${m.max || '—'}</span>
        </div>`).join('')
      + (cov.optional || []).map((m) => `<div class="trow opt ${m.max ? 'has' : 'no'}">
          <span class="tname" title="${m.table}（可选）">${m.label} <i>可选</i></span>
          <span class="tdate">${m.max || '—'}</span>
        </div>`).join('');
  }

  /* ───────── 本地已下载未导入（/api/packs） ─────────
     🔴 2026-10-31：覆盖度只说「库里缺什么」，不说「本地有什么没导」。
        10-30 的 37 个包躺在 PACK_DIR，页面却一片全绿 → 必须显式提示。 */
  async function loadPacksWaiting() {
    if (!el.covWait) return;
    try {
      const j = await api('/api/packs', { timeoutMs: 90000 });
      const dbMax = j.db_max || {};
      const base = String(dbMax.daily || '').replace(/\D/g, '');
      const dates = (j.dates || [])
        .filter((d) => /^\d{8}$/.test(String(d.date || '')) && (d.importable || 0) > 0)
        .filter((d) => !base || String(d.date) > base)
        .sort((a, b) => String(a.date).localeCompare(String(b.date)));
      if (!dates.length) { el.covWait.hidden = true; return; }
      el.covWait.hidden = false;
      const d0 = dates[dates.length - 1];
      const total = dates.reduce((n, d) => n + (d.importable || 0), 0);
      el.covWaitTitle.textContent = dates.length === 1
        ? `本地已下载 ${fmtD(d0.date)} 的 ${d0.importable} 个包，但还没导入`
        : `本地已下载 ${dates.length} 个交易日共 ${total} 个包，但还没导入（最新 ${fmtD(d0.date)} ${d0.importable} 个）`;
      el.covWaitDesc.textContent =
        `路径：${j.dir || '?'} 。把上面这些文件拖到下方「1 · 上传文件」区即可，`
        + `或点右侧「重扫」刷新。库里 daily 最新 ${fmtD(base) || '—'}。`;
    } catch (e) {
      el.covWait.hidden = true;
    }
  }

  /* ───────── 包清单 ───────── */
  async function loadPacks() {
    try {
      const j = await api('/api/upload/types');
      el.packList.innerHTML = j.packs.map((p) =>
        `<div class="help-row">
           <b>${CAT_ICON[p.cat] || '•'} ${p.cat}</b>
           <span>${p.hint}<br><i>→ ${p.labels.join(' / ')}</i></span>
         </div>`).join('');
    } catch (e) {
      el.packList.textContent = '读取失败：' + e.message;
    }
  }

  /* ───────── 上传 ───────── */
  function renderFiles() {
    if (!staged.length) { el.fileList.innerHTML = ''; el.importBar.hidden = true; return; }
    el.importBar.hidden = false;

    const groups = {};
    staged.forEach((f, i) => {
      f._i = i;
      const cat = f.cat_label || f.cat || '其他';
      (groups[cat] = groups[cat] || []).push(f);
    });

    el.fileList.innerHTML = Object.keys(groups)
      .sort((a, b) => (CAT_ORDER.indexOf(a) + 99) % 100 - (CAT_ORDER.indexOf(b) + 99) % 100)
      .map((cat) => {
        const rows = groups[cat].map((f) => {
          const sup = f.supported !== false;
          const badge = !sup ? '<i class="bg bad">不支持</i>'
            : f.duplicate ? '<i class="bg dup">已导过</i>'
            : (f.imported && Object.values(f.imported).every(Boolean)) ? '<i class="bg ok">库内已有</i>' : '';
          return `<div class="frow ${sup ? '' : 'off'}">
            <label class="fchk"><input type="checkbox" class="fbox" data-i="${f._i}"
              ${sup ? 'checked' : ''} ${sup ? '' : 'disabled'}></label>
            <span class="fname" title="${f.name}">${f.name}</span>
            <span class="fdate">${f.date || '—'}</span>
            <span class="flabel">${f.label}${badge}</span>
            <span class="fsize">${fmtSize(f.size || 0)}</span>
          </div>`;
        }).join('');
        return `<div class="fgroup">
            <div class="fgroup-head">${CAT_ICON[cat] || '•'} ${cat} <b>${groups[cat].length}</b></div>
            ${rows}</div>`;
      }).join('');

    updateSel();
  }

  function picked() {
    return [...document.querySelectorAll('.fbox:checked')].map((c) => Number(c.dataset.i));
  }

  function updateSel() {
    const n = picked().length;
    el.selInfo.textContent = `已选 ${n} / ${staged.length} 个`;
    const bad = staged.filter((f) => f.supported === false).length;
    el.btnImport.disabled = n === 0;
    el.btnDry.disabled = n === 0;
    if (bad) el.selInfo.textContent += `（${bad} 个不支持已置灰）`;
  }

  async function upload(files) {
    if (!files || !files.length) return;
    const fd = new FormData();
    [...files].forEach((f) => fd.append('files', f));
    el.progText.textContent = `上传 ${files.length} 个文件…`;
    el.progress.hidden = false; el.progBar.style.width = '5%';
    try {
      const j = await api('/api/upload/batch', { method: 'POST', body: fd });
      batch = j.batch;
      staged = j.files || [];
      renderFiles();
      toast(`已上传 ${j.saved} 个文件，已按类型分组`);
    } catch (e) {
      toast('上传失败：' + e.message, 'bad');
    } finally {
      el.progress.hidden = true; el.progBar.style.width = '0';
    }
    loadBatches();
  }

  /* ───────── 导入 ───────── */
  async function doImport(dry) {
    const idx = picked();
    if (!batch || !idx.length) return toast('先选文件', 'bad');
    const names = idx.map((i) => staged[i].name);
    el.btnImport.disabled = el.btnDry.disabled = true;
    el.progress.hidden = false; el.progBar.style.width = '2%';
    el.progText.textContent = dry ? '预演中…' : '导入中…';
    try {
      await api('/api/upload/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          batch, files: names, dry,
          date: el.dateIn.value || undefined,
          allow_duplicates: el.chkDup.checked,
          wait_lock: el.chkQueue.checked ? 1800 : 120,
        }),
      });
      pollStatus(dry);
    } catch (e) {
      toast('启动失败：' + e.message, 'bad');
      el.btnImport.disabled = el.btnDry.disabled = false;
      el.progress.hidden = true;
    }
  }

  function pollStatus(dry) {
    clearInterval(polling);
    polling = setInterval(async () => {
      let st;
      try { st = await api('/api/upload/status'); } catch { return; }
      const total = st.total || 0, done = st.done || 0;
      const pct = total ? Math.min(99, Math.round((done / total) * 100)) : 5;
      el.progBar.style.width = pct + '%';
      el.progPct.textContent = pct + '%';
      el.progText.textContent = st.current ? `${done}/${total} · ${st.current}` : '处理中…';

      if (st.running) return;
      clearInterval(polling);
      el.progBar.style.width = '100%'; el.progPct.textContent = '100%';
      el.progText.textContent = st.error ? ('出错：' + st.error) : (dry ? '预演完成' : '导入完成');
      const delN = (st.deleted || []).length;
      const delFail = st.delete_failed || [];
      renderResults(st.results || [], st.skipped || [], dry, st.deleted || [], delFail);
      el.btnImport.disabled = el.btnDry.disabled = false;
      if (!dry) {
        let msg = '导入完成';
        if (delN) msg += `，已自动删除 ${delN} 个数据包`;
        if (delFail.length) msg += ` · ⚠️ ${delFail.length} 个删除失败`;
        toast(msg, delFail.length ? 'bad' : '');
        setTimeout(async () => {
          await loadCoverage(el.dateIn.value);
          const fileList = await api('/api/upload/batch/' + batch).catch(() => null);
          if (fileList && fileList.files) { staged = fileList.files; renderFiles(); }
          loadBatches();
          if (el.btnPurge) refreshPurgeHint();
        }, 900);
      }
    }, 900);
  }

  function renderResults(rows, skipped, dry, deleted, deleteFail) {
    deleted = deleted || [];
    deleteFail = deleteFail || [];
    const ok = rows.filter((r) => !r.error);
    const bad = rows.filter((r) => r.error);
    const byTable = {};
    ok.forEach((r) => { byTable[r.table] = (byTable[r.table] || 0) + Number(r.rows || 0); });
    const tableRows = Object.entries(byTable)
      .map(([t, n]) => `<div class="rrow"><span>${t}</span><b>${n.toLocaleString()}</b></div>`).join('');

    el.results.innerHTML = `
      <div class="rsum ${bad.length ? 'warn' : 'good'}">
        ${dry ? '预演' : '导入'}结果：成功 ${ok.length} 项 · 失败 ${bad.length} 项${skipped.length ? ' · 跳过重复 ' + skipped.length + ' 个' : ''}${deleted.length ? ' · 🗑 已删数据包 ' + deleted.length + ' 个' : ''}
      </div>
      ${tableRows ? `<div class="rtables">${tableRows}</div>` : ''}
      ${bad.length ? `<div class="rerr">${bad.map((r) => `<div>🔴 <b>${r.file || ''}</b> → ${r.error}</div>`).join('')}</div>` : ''}
      ${skipped.length ? `<div class="rskip">${skipped.map((s) => `<div>⏭ ${s.file}（${s.reason}）</div>`).join('')}</div>` : ''}
      ${deleteFail.length ? `<div class="rerr">${deleteFail.map((d) => `<div>🗑 <b>${d.file}</b> 删除失败 → ${d.error}</div>`).join('')}</div>` : ''}`;
  }

  /* ───────── 批次 ───────── */
  async function loadBatches() {
    try {
      const j = await api('/api/upload/batches');
      if (!j.batches || !j.batches.length) { el.batches.textContent = '（暂无）'; return; }
      el.batches.innerHTML = j.batches.map((b) =>
        `<div class="brow">
           <span class="bname" title="${b.batch}">${b.batch}</span>
           <span class="bmeta">${b.count} 个 · ${fmtSize(b.size)}</span>
           <button class="bx" data-b="${b.batch}" title="删除该暂存批次（不动库）">×</button>
         </div>`).join('');
      el.batches.querySelectorAll('.bx').forEach((btn) => {
        btn.onclick = async () => {
          if (!confirm('删除暂存批次 ' + btn.dataset.b + '？\n（只删上传副本，不影响已导入数据）')) return;
          try { await api('/api/upload/batch/' + btn.dataset.b, { method: 'DELETE' }); loadBatches(); }
          catch (e) { toast('删除失败：' + e.message, 'bad'); }
        };
      });
    } catch (e) { el.batches.textContent = '读取失败：' + e.message; }
  }

  async function refreshMeta() {
    if (!confirm('重算所有表的真实最新日期？\nminute_1min 有 45 亿行，整体约需 1-2 分钟。')) return;
    el.btnMeta.disabled = true;
    el.btnMeta.textContent = '重算中…';
    try {
      await api('/api/upload/refresh_meta', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      const t = setInterval(async () => {
        const st = await api('/api/upload/status').catch(() => ({}));
        if (!st.meta_running) {
          clearInterval(t);
          el.btnMeta.disabled = false;
          el.btnMeta.textContent = '重算元数据';
          await loadCoverage(el.dateIn.value);
          toast('元数据已重算');
        }
      }, 1500);
    } catch (e) {
      el.btnMeta.disabled = false; el.btnMeta.textContent = '重算元数据';
      toast('启动失败：' + e.message, 'bad');
    }
  }

  /* ───────── 绑定 ───────── */
  el.btnPick.onclick = () => el.fileInput.click();
  el.fileInput.onchange = (e) => upload(e.target.files);
  el.drop.onclick = (e) => { if (e.target === el.drop || e.target.classList.contains('drop-icon') || e.target.classList.contains('drop-title')) el.fileInput.click(); };
  ['dragenter', 'dragover'].forEach((ev) => el.drop.addEventListener(ev, (e) => { e.preventDefault(); el.drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => el.drop.addEventListener(ev, (e) => { e.preventDefault(); el.drop.classList.remove('over'); }));
  el.drop.addEventListener('drop', (e) => upload(e.dataTransfer.files));

  el.chkAll.onchange = () => {
    document.querySelectorAll('.fbox:not(:disabled)').forEach((c) => { c.checked = el.chkAll.checked; });
    updateSel();
  };
  el.fileList.addEventListener('change', (e) => { if (e.target.classList.contains('fbox')) updateSel(); });
  el.btnDry.onclick = () => doImport(true);
  el.btnImport.onclick = () => doImport(false);

  /* ───────── 清理已导入数据包 ───────── */
  async function refreshPurgeHint() {
    if (!el.btnPurge) return;
    try {
      const r = await api('/api/upload/purge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry: true }),
      });
      const mb = ((r.bytes || 0) / 1048576).toFixed(1);
      el.purgeN.textContent = r.count ? `(${r.count} 个 / ${mb} MB)` : '';
      el.btnPurge.disabled = !r.count;
      el.btnPurge.title = r.count
        ? `可清理 ${r.count} 个已导入成功的数据包，释放 ${mb} MB（未导入的文件不动）`
        : '没有可清理的已导入数据包';
    } catch { /* 静默 */ }
  }

  if (el.btnPurge) {
    el.btnPurge.onclick = async () => {
      const hint = el.purgeN.textContent || '';
      if (!confirm('清理已导入成功的数据包副本？' + hint
        + '\n\n只删除「内容已在库里」的上传副本，未导入的文件不动，不影响库内数据。')) return;
      el.btnPurge.disabled = true;
      el.purgeN.textContent = '(清理中…)';
      try {
        const r = await api('/api/upload/purge', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ dry: false }),
        });
        const okN = (r.deleted || []).length;
        const failN = (r.failed || []).length;
        const mb = ((r.bytes || 0) / 1048576).toFixed(1);
        toast(`已清理 ${okN} 个数据包，释放 ${mb} MB` + (failN ? ` · ⚠️ ${failN} 个失败` : ''),
              failN ? 'bad' : '');
        if (failN) console.warn('删除失败明细', r.failed);
        await loadBatches();
      } catch (e) {
        toast('清理失败：' + e.message, 'bad');
      }
      await refreshPurgeHint();
    };
  }

  el.btnRefresh.onclick = async () => {
    el.btnRefresh.disabled = true;
    await Promise.all([loadCoverage(el.dateIn.value), loadBatches(), refreshPurgeHint()]);
    el.btnRefresh.disabled = false;
    toast('已刷新');
  };
  if (el.btnWaitReload) {
    el.btnWaitReload.onclick = async () => {
      el.btnWaitReload.disabled = true;
      await loadPacksWaiting();
      el.btnWaitReload.disabled = false;
      toast('已重扫本地包目录');
    };
  }
  el.btnMeta.onclick = refreshMeta;
  el.dateIn.onchange = () => loadCoverage(normDate(el.dateIn.value));

  (async function init() {
    try {
      const j = await api('/api/upload/tables');
      el.dbInfo.textContent = 'DuckDB · ' + (j.duckdb || '').split('/').pop();
    } catch {}
    // 🔴 2026-10-28 修：绝不把「最新交易日」自动填进日期框
    // 曾导致 date_override 覆盖文件名日期 → 一次误删 12 张表的当日数据
    await loadCoverage('');
    loadPacksWaiting();
    loadPacks();
    loadBatches();
    refreshPurgeHint();
  })();
})();
