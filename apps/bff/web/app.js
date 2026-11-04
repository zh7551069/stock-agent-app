/* ============================================================================
   stock-advice-agent · 移动端 App 交互层
   零依赖：hash 路由 + 字符串渲染 + 事件委托
   ========================================================================== */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const app = $("#app");
const view = $("#view");
const appbar = $("#appbar");
const tabbar = $("#tabbar");

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* 纯文本化（列表副标题用：去掉 markdown 记号与双链标记） */
function plain(t) {
  return String(t ?? "")
    .replace(/\[\[(?:知识点-)?([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, n, l) => l || n)
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1");
}

/* 短文本内联渲染：双链 → 可跳转 */
function mdInline(t) {
  return esc(t ?? "")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[\[(?:知识点-)?([^\]|]+)(?:\|([^\]]+))?\]\]/g,
      (_, n, l) => `<span class="link" data-nav="#/card/${encodeURIComponent(n)}">${esc(l || n)}</span>`);
}

/* 极简 Markdown（卡片正文 / 手册） */
function md(src) {
  if (!src) return "";
  const lines = String(src).replace(/\r/g, "").split("\n");
  const out = [];
  let inList = null, inTable = false, inQuote = false, code = false;

  const inline = (t) => esc(t)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[\[(?:知识点-)?([^\]|]+)(?:\|([^\]]+))?\]\]/g,
      (_, n, l) => `<span class="link" data-nav="#/card/${encodeURIComponent(n)}">${esc(l || n)}</span>`)
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[^*])\*([^*\n]{1,40})\*/g, "$1<em>$2</em>");

  const closeList = () => { if (inList) { out.push(`</${inList}>`); inList = null; } };
  const closeTable = () => { if (inTable) { out.push("</tbody></table></div>"); inTable = false; } };
  const closeQuote = () => { if (inQuote) { out.push("</blockquote>"); inQuote = false; } };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (/^```/.test(line)) { code = !code; out.push(code ? "<pre><code>" : "</code></pre>"); continue; }
    if (code) { out.push(esc(line)); continue; }
    if (!line.trim()) { closeList(); closeTable(); closeQuote(); continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { closeList(); closeTable(); closeQuote(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
    if (/^---+$/.test(line.trim())) { closeList(); closeTable(); closeQuote(); out.push("<hr>"); continue; }
    if (/^>\s?/.test(line)) {
      closeList(); closeTable();
      if (!inQuote) { out.push("<blockquote>"); inQuote = true; }
      out.push(`<p>${inline(line.replace(/^>\s?/, ""))}</p>`);
      continue;
    }
    closeQuote();
    if (/^\|/.test(line)) {
      const cells = line.split("|").slice(1, -1).map(c => c.trim());
      if (cells.every(c => /^:?-{2,}:?$/.test(c))) continue;
      if (!inTable) { out.push('<div class="table-wrap"><table><tbody>'); inTable = true; }
      out.push("<tr>" + cells.map(c => `<td>${inline(c)}</td>`).join("") + "</tr>");
      continue;
    }
    closeTable();
    const ul = line.match(/^\s*[-*]\s+(.*)$/), ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      const want = ul ? "ul" : "ol";
      if (inList !== want) { closeList(); out.push(`<${want}>`); inList = want; }
      out.push(`<li>${inline((ul || ol)[1])}</li>`);
      continue;
    }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList(); closeTable(); closeQuote();
  return out.join("\n");
}

/* ─────────── 数据层（带缓存 + 失效） ─────────── */
const api = (p, opts) => fetch(p, opts).then(async (r) => {
  // 会话过期 → 回登录页，避免界面卡在半截
  if (r.status === 401) {
    location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search));
    throw new Error("需要登录");
  }
  const d = await r.json().catch(() => null);
  if (!r.ok) throw new Error(d?.error || `接口返回 HTTP ${r.status}`);
  return d;
});

const postJSON = (p, body) => api(p, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const store = {
  overview: null,
  bucket: {},
  strategies: null,
  index: null,
  cards: { items: [], page: 1, total: 0, filters: { q: "", bucket: "", tag: "", exec: false } },
  version: 0,
};

function invalidate() {
  store.overview = null; store.bucket = {}; store.strategies = null;
  store.index = null; store.cards = { items: [], page: 1, total: 0, filters: store.cards.filters };
}

async function getOverview(force = false) {
  if (force) store.overview = null;
  if (!store.overview) store.overview = await api("/api/overview" + (force ? "?refresh=1" : ""));
  return store.overview;
}
async function getBucket(b, force = false) {
  const key = b === "盘中" ? `${b}|${state.intraFilter}` : b;
  const p = new URLSearchParams();
  if (b === "盘中") p.set("buyable", state.intraFilter === "all" ? "0" : "1");
  if (force) p.set("refresh", "1");
  const qs = p.toString() ? "?" + p.toString() : "";
  if (force || !store.bucket[key]) store.bucket[key] = await api(`/api/recommend/${encodeURIComponent(b)}${qs}`);
  return store.bucket[key];
}
async function getStrategies() {
  if (!store.strategies) store.strategies = await api("/api/strategies");
  return store.strategies;
}
async function getIndex() {
  if (!store.index) {
    try { store.index = await api("/api/index"); } catch { store.index = { ok: false }; }
  }
  return store.index;
}

/* ─────────── 格式化 ─────────── */
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(+v)) ? "—" : (+v).toFixed(d);
const signed = (v, d = 2) => (v === null || v === undefined || Number.isNaN(+v)) ? "—" : (v >= 0 ? "+" : "−") + Math.abs(+v).toFixed(d);
const dirClass = (v) => (v === null || v === undefined || Number.isNaN(+v)) ? "flat" : (v > 0 ? "rise" : v < 0 ? "fall" : "flat");
const dirText = (v) => (v > 0 ? "rise" : v < 0 ? "fall" : "flat");
const cmp = (a, b) => (a === null || b === null || a === undefined || b === undefined) ? "?" : (a > b ? ">" : a < b ? "<" : "=");
const yi = (v) => (v === null || v === undefined ? "—" : (+v >= 100 ? (+v).toFixed(0) : (+v).toFixed(1)) + " 亿");

/* ─────────── 顶栏 / 底部 tab ─────────── */
function setHeader({ title, sub = "", back = false, action = "" }) {
  appbar.className = "appbar" + (back ? "" : " align");
  appbar.innerHTML = `
    ${back ? `<button class="iconbtn back" data-back aria-label="返回"><svg class="ic"><use href="#i-back"/></svg></button>` : ""}
    <div class="appbar-titles">
      <div class="appbar-title">${esc(title)}</div>
      ${sub ? `<div class="appbar-sub">${esc(sub)}</div>` : ""}
    </div>
    <div class="appbar-actions">${action}</div>`;
}

function setTabbar(active) {
  if (!active) { tabbar.hidden = true; view.classList.remove("with-tabbar"); return; }
  tabbar.hidden = false;
  view.classList.add("with-tabbar");
  $$(".tab-btn").forEach(b => {
    const on = b.dataset.tab === active;
    b.classList.toggle("on", on);
    if (on) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
}

function appSubtitle(o) {
  if (!o) return "加载中…";
  const base = `数据 ${o.data_date} · 计算于 ${String(o.computed_at || "").slice(11, 16)}`;
  return (o.live && o.live.source === "realtime") ? `${base} · 盘中实时` : base;
}

/* ─────────── 图表 ─────────── */
function sparkSVG(values, opts = {}) {
  const W = 1000, H = 280;
  const val = values || [];
  const idx = [];
  val.forEach((v, i) => { if (v !== null && v !== undefined && isFinite(v)) idx.push(i); });
  if (idx.length < 2) return "";
  const vs = idx.map(i => +val[i]);

  // 取值范围要把「昨收」和 MA 也算进去，否则参考线可能画到框外
  const range = vs.slice();
  if (opts.prevClose !== null && opts.prevClose !== undefined && isFinite(opts.prevClose)) range.push(+opts.prevClose);
  (opts.ma20 || []).forEach(v => { if (v !== null && v !== undefined && isFinite(v)) range.push(+v); });
  (opts.ma5 || []).forEach(v => { if (v !== null && v !== undefined && isFinite(v)) range.push(+v); });

  const min = Math.min(...range), max = Math.max(...range);
  const span = (max - min) || Math.abs(max) * 0.02 || 1;
  const lo = min - span * 0.12, hi = max + span * 0.12;
  const x = i => (i / (val.length - 1)) * W;
  const y = v => H - ((v - lo) / (hi - lo)) * H;

  let d = "";
  idx.forEach((i, k) => { d += (k ? " L" : "M") + x(i).toFixed(1) + " " + y(val[i]).toFixed(1); });
  const area = `${d} L${W} ${H} L0 ${H} Z`;

  const line = (arr) => {
    let out = "", started = false;
    (arr || []).forEach((v, i) => {
      if (v === null || v === undefined || !isFinite(v)) return;
      out += (started ? " L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1);
      started = true;
    });
    return out;
  };

  const lastVal = vs[vs.length - 1];
  const leftPct = (idx[idx.length - 1] / (val.length - 1)) * 100;
  const topPct = (1 - (lastVal - lo) / (hi - lo)) * 100;
  const prev = opts.prevClose;
  const hasPrev = prev !== null && prev !== undefined && isFinite(prev);

  return `<div class="sparkbox2">
    <svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
      <path class="area" d="${area}"/>
      ${opts.ma20 ? `<path class="ma20" d="${line(opts.ma20)}" vector-effect="non-scaling-stroke"/>` : ""}
      ${opts.ma5 ? `<path class="ma5" d="${line(opts.ma5)}" vector-effect="non-scaling-stroke"/>` : ""}
      <path class="ln" d="${d}" stroke="${opts.lineColor || "var(--t2)"}" vector-effect="non-scaling-stroke"/>
      ${hasPrev ? `<line class="prevline" x1="0" y1="${y(prev).toFixed(1)}" x2="${W}" y2="${y(prev).toFixed(1)}"
          stroke="${opts.prevColor || "var(--line-strong)"}" stroke-width="2" stroke-dasharray="6 9" vector-effect="non-scaling-stroke"/>` : ""}
    </svg>
    <i class="sparkdot" style="left:${leftPct.toFixed(2)}%;top:${topPct.toFixed(2)}%;background:${opts.endColor || "var(--t2)"}"></i>
  </div>`;
}

/* ─────────── 通用状态 ─────────── */
let toastTimer = null;
function toast(msg, ms = 2200) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), ms);
}

function openSheet(html) {
  $("#sheet").innerHTML = `<div class="sheet-grip"></div>${html}`;
  $("#sheetWrap").hidden = false;
}
function closeSheet() { $("#sheetWrap").hidden = true; }

function loadingView() {
  view.innerHTML = `<div class="card" style="margin-top:calc(24 * var(--u))">
      <div class="skel skel-row"></div><div class="skel skel-row"></div><div class="skel skel-row"></div>
    </div>`;
}

function friendlyError(e) {
  const m = String(e?.message || e || "");
  if (/not found|404/i.test(m)) return "没有找到这条记录（数据可能已更新，返回列表试试）";
  if (/Failed to fetch|NetworkError|Load failed/i.test(m)) return "服务未启动或网络中断，确认 python3 server.py 仍在运行";
  return m;
}

function errorView(e, retryHash) {
  view.innerHTML = `<div class="empty">
      <svg class="ic"><use href="#i-warn"/></svg>
      读取失败<br><span class="hint">${esc(friendlyError(e))}</span>
    </div>
    <button class="btn btn--ghost" data-nav="${esc(retryHash || location.hash)}" style="margin-top:calc(24 * var(--u))">重试</button>`;
}

/* ─────────── 屏幕：今日推荐 ─────────── */
const BUCKET_META = {
  "盘前": { tone: "gold", text: "集合竞价 / 开盘前条件单", icon: "🌅" },
  "盘中": { tone: "cyan", text: "分时 / 涨速 / 打板", icon: "📊" },
  "盘后": { tone: "", text: "收盘选股 / 波段池", icon: "🌙" },
};

function envCard(o) {
  const e = o.env || {};
  const ok = e.ok !== false;
  // 主位置显示「今日涨跌」；20 日/5 日是另外两个数，放进下方键值区各自上色
  const todayPct = (e.index_pct === null || e.index_pct === undefined) ? e.ret20_pct : e.index_pct;
  const todayColor = dirText(todayPct);
  const gate = e.gate || "中性";
  return `
  <div class="card env-card" id="envCard" style="margin-top:calc(32 * var(--u))">
    <div class="env-top">
      <div class="gate" data-f="gate" data-gate="${esc(gate)}"><i class="dot" style="background:currentColor"></i>${esc(gate)}</div>
      <div class="env-date" data-f="date">${e.live
        ? `实时 ${esc(String(e.quote_time || "").slice(11, 16))}`
        : `截至 ${esc((o.data_date || "").slice(5))}`}</div>
    </div>
    <div class="hero">
      <div class="hero-num ${todayColor}" data-f="idx">${ok ? num(e.index_close) : "—"}</div>
      <div class="hero-side">
        <div class="hero-chg ${todayColor}" data-f="pct">${ok ? signed(todayPct) + "%" : "—"}</div>
        <div class="hero-cap" data-f="cap">上证指数 · ${e.live ? "实时" : "今日"}</div>
      </div>
    </div>
    <div class="chartbox sparkwrap" id="idxChart"></div>
    <div class="kv-grid" style="margin-top:calc(28 * var(--u))">
      <div class="kv-cell">
        <div class="kv-k">中期结构</div>
        <div class="kv-v sm ${e.structure === "空头排列" ? "fall" : e.structure === "多头排列" ? "rise" : ""}" data-f="structure">${esc(e.structure || e.trend || "—")}</div>
        <div class="kv-sub num" data-f="mastruct">收盘 ${num(e.index_close)} ${cmp(e.index_close, e.ma20)} MA20 ${num(e.ma20)} ${cmp(e.ma20, e.ma60)} MA60 ${num(e.ma60)}</div>
      </div>
      <div class="kv-cell">
        <div class="kv-k">短期 5 日</div>
        <div class="kv-v sm num ${dirText(e.ret5_pct)}" data-f="ret5">${signed(e.ret5_pct)}%</div>
        <div class="kv-sub num" data-f="ret5sub">20 日 ${signed(e.ret20_pct)}% · MA20 ${signed(e.ma20_slope_pct)}%/5日</div>
      </div>
      <div class="kv-cell">
        <div class="kv-k">涨停 / 炸板</div>
        <div class="kv-v sm num" data-f="zt"><span class="rise">${e.zt_count ?? "—"}</span><span class="muted"> / </span><span class="fall">${e.broken_count ?? "—"}</span></div>
        <div class="kv-sub num" data-f="ztshare">占全市场 ${num(e.zt_share, 1)}%（${e.limit_base ?? "—"} 只）</div>
      </div>
      <div class="kv-cell">
        <div class="kv-k">封板率</div>
        <div class="kv-v sm num" data-f="seal">${num(e.seal_rate, 1)}%</div>
        <div class="kv-sub num" data-f="sealsub">摸过涨停 ${e.touched_count ?? "—"} 只，封住 ${e.zt_count ?? "—"} 只</div>
      </div>
      <div class="kv-cell">
        <div class="kv-k">涨 / 跌家数</div>
        <div class="kv-v sm num" data-f="updown"><span class="rise">${e.up ?? "—"}</span><span class="muted"> / </span><span class="fall">${e.down ?? "—"}</span></div>
        <div class="kv-sub">全市场，不剔除</div>
      </div>
      <div class="kv-cell">
        <div class="kv-k">今日</div>
        <div class="kv-v sm num ${todayColor}" data-f="today">${signed(todayPct)}%</div>
        <div class="kv-sub" data-f="prev">前收 ${num(e.prev_close)}</div>
      </div>
    </div>
    ${(e.top_sectors || []).length ? `
      <div class="chiprow" data-f="sectors" style="margin-top:calc(12 * var(--u))">
        ${e.top_sectors.map(s => `<span class="chip">${esc(s.name)} <b class="${s.pct >= 0 ? "rise" : "fall"}">${signed(s.pct)}%</b></span>`).join("")}
      </div>` : ""}
    <div class="env-note" data-f="note">${esc(e.note || "")}</div>
    <button class="linkbtn" data-action="audit">数据口径与自检 ›</button>
  </div>`;
}

function bucketNote(o, b, data) {
  const meta = BUCKET_META[b];
  const live = (data && data.live) || o.live || null;
  let text = "";
  if (b === "盘前") text = `数据时点 ${o.data_date} 收盘 · 下一交易日开盘前的条件单候选；竞价类展示最近一次竞价结构，次日开盘对照触发条件。`;
  else if (b === "盘后") text = `数据时点 ${o.data_date} 收盘 · 收盘选股池，用于下一交易日盘中跟踪。`;
  else if (b === "盘中") {
    if (live && (live.source === "realtime" || live.source === "cache")) {
      const qt = (live.quote_time || "").slice(11, 19);
      const tail = live.source === "cache"
        ? `实时源暂不可用，展示 ${String(live.stale_since || "").slice(0, 16)} 拉取的快照`
        : (live.is_trading
            ? "实时行情 · 点击右上角刷新可拉取最新"
            : `当前${live.phase || "非交易时段"} · 展示当日快照（非盘中）`);
      const cnt = (data && data.buyable_count !== undefined)
        ? ` · 可买 <b style="color:var(--rise)">${data.buyable_count}</b> 只 / 已封板 ${data.sealed_count} 只`
        : "";
      const srcName = LIVE_SRC[live.code] || live.code || "实时源";
      const tr = live.trend || {};
      const trTxt = tr.ready
        ? ` · 走势样本 <b style="color:var(--rise)">${tr.samples}</b> 帧${tr.gap_min ? `（按 ${tr.gap_min} 分钟间隔折算）` : ""}`
        : " · 走势样本积累中：手动刷新至少 2 次（间隔 ≥1 分钟）后走势分生效";
      return `<div class="bucket-note">
        <svg class="ic"><use href="#i-sync"/></svg>
        <p><strong style="color:var(--rise)">● 实时行情 ${esc(qt)} · ${esc(live.phase || "")} · ${esc(srcName)}</strong><br>
        ${esc(tail)} · 现价/涨速/量比/分时均价均来自实时快照${live.bars ? `，${live.bars} 只涨停候选已拉当日 5min K 线` : ""}${cnt}${trTxt}</p>
      </div>`;
    }
    const why = live && live.reason ? `（${live.reason}）` : "";
    text = `实时源暂不可用${why}，已回退到 ${o.minute_date} 分时回放。`;
  }
  return `<div class="bucket-note">
    <svg class="ic"><use href="#i-info"/></svg>
    <p><strong style="color:var(--t2)">${esc(b)} · ${esc(meta.text)}</strong><br>${esc(text)}</p>
  </div>`;
}

/* ── 盘中走势标签（来自实时分时序列，见 core/realtime.py trend_score）── */
const LIVE_SRC = { "tencent.qt": "腾讯行情", "eastmoney.push2": "东财 push2" };

function trendBadge(s) {
  const t = s.trend;
  if (!t || !t.label) return "";
  const weak = /弱|回落/.test(t.label);
  const flat = t.label === "走平";
  const color = weak ? "var(--fall)" : flat ? "var(--t3)" : "var(--rise)";
  const mom = (t.mom5 === null || t.mom5 === undefined) ? ""
            : ` ${t.mom5 >= 0 ? "+" : ""}${num(t.mom5, 2)}%`;
  const tip = `走势分 ${num(t.score, 1)} · 5min ${num(t.mom5, 2)}% · 加速度 ${num(t.accel, 2)} · 均价线 ${num(t.vwap_slope, 2)}% · 量能脉冲 ${num(t.vol_pulse, 2)}`;
  return `<span class="badge" title="${esc(tip)}" style="color:${color};border-color:${color}">${esc(t.label)}${mom}</span>`;
}

function stockRowInner(s, i) {
  const pct = s.pct_chg;
  const rankNo = s._rank || (i + 1);
  const hits = (s.hits || []).slice(0, 2).map(h => `<span class="badge">${esc(h.strategy_name)}</span>`).join("");
  // 可买性（涨停封死 = 买不到）
  let tb = "";
  if (s.tradable && s.tradable !== "—") {
    const color = s.tradable_rank === 0 ? "var(--rise)" : s.tradable_rank === 1 ? "#d99a2b" : "var(--t3)";
    const gap = (s.limit_gap !== null && s.limit_gap !== undefined)
      ? (s.limit_gap <= 0.05 ? " · 已封死" : ` · 距涨停 ${num(s.limit_gap, 1)}%`) : "";
    tb = `<span class="badge" style="color:${color};border-color:${color}">${esc(s.tradable)}${gap}</span>`;
  }
  // 追高风险（≥7% 追高 / ≥9% 高危；知识卡：高于最佳买点 5~10% 不买）
  let chase = "";
  if (s.chase) {
    const c = s.chase === "高危" ? "var(--fall)" : "#d99a2b";
    chase = `<span class="badge" style="color:${c};border-color:${c}">${esc(s.chase)}</span>`;
  }
  return `<div class="stock-top">
      <span class="rank ${rankNo <= 3 ? "top" : ""}">${String(rankNo).padStart(2, "0")}</span>
      <div class="row-main">
        <div class="stock-name">${esc(s.name)}<span class="stock-code">${esc(s.code)}</span></div>
        <div class="row-s">${esc(s.industry || "—")} · 命中 ${s.hit_count} 条 · 评分 ${num(s.score, 1)}</div>
      </div>
      <div class="stock-right">
        <div class="stock-price ${dirText(pct)}">${num(s.close)}</div>
        <div class="stock-pct ${dirText(pct)}">${signed(pct)}%</div>
      </div>
    </div>
    ${(hits || tb || chase || s.trend) ? `<div class="hitline">${tb}${chase}${trendBadge(s)}${hits}</div>` : ""}`;
}

function stockRow(s, i) {
  return `<div class="stock-row" data-code="${esc(s.code)}" data-nav="#/stock/${esc(s.code)}">${stockRowInner(s, i)}</div>`;
}

function rowSig(s, i) {
  const hs = (s.hits || []).map(h => h.strategy_name).join(",");
  return [s._rank || (i + 1), s.close, s.pct_chg, s.score, s.hit_count, s.tradable,
          s.limit_gap, s.chase, s.kind, hs, s.trend ? s.trend.label : "",
          s.trend ? s.trend.mom5 : ""].join("|");
}

function chipsHtml(data) {
  return (data.strategies || []).map(s =>
    `<button class="chip" data-strategy="${esc(s.id)}">${esc(s.name)} <b>${s.count}${s.capped ? "+" : ""}</b></button>`).join("");
}

function eventBlockHtml(events) {
  const arr = events || [];
  if (!arr.length) return "";
  const rows = arr.slice(0, 8).map(x => `
    <div class="ev-row" data-nav="#/stock/${esc(x.code)}">
      <span class="ev-tag">⚡ ${esc(x.strategy_name)}</span>
      <span class="ev-main"><b>${esc(x.name)}</b> <span class="muted">${esc(x.code)}</span></span>
      <span class="ev-why">${esc(x.reason)}</span>
    </div>`).join("");
  return `<div class="card ev-card">
    <div class="ev-head">事件提醒 · 已封板<span>${arr.length} 条 · 供打板/次日跟踪，不是可买推荐</span></div>
    ${rows}
    ${arr.length > 8 ? `<div class="ev-more">… 另有 ${arr.length - 8} 条</div>` : ""}
  </div>`;
}

/* ── 蓄势观察池（未确认，不是买点）─────────────────────────
   回测口径见 reports/拉升前兆_事件研究_20261103.md：
   缩量横盘不提高「10 分钟后拉升」的概率（lift 0.70），但下行率只有基线 1/4。
   所以这里只做"低成本埋伏/不追高"的观察，不进推荐榜、不参与打分。 */
function watchRow(x) {
  const tone = x.state === "启动" ? "#d99a2b" : "var(--t2)";
  const tag = x.state === "启动" ? "刚启动" : "蓄势";
  return `<div class="ev-row" data-nav="#/stock/${esc(x.code)}">
    <span class="ev-tag" style="color:${tone}">${tag}</span>
    <span class="ev-main"><b>${esc(x.name)}</b> <span class="muted">${esc(x.code)}</span>
      <span class="muted" style="margin-left:calc(8 * var(--u))">${signed(x.pct_chg)}%</span></span>
    <span class="ev-why">${esc(x.reason)}</span>
  </div>`;
}

function watchBlockHtml(w) {
  if (!w) return "";
  const coil = (w.coil || []), go = (w.go || []);
  if (!coil.length && !go.length) return "";
  const n = (w.counts && w.counts.coil) || coil.length;
  return `<div class="card ev-card">
    <div class="ev-head">蓄势观察 · 未确认<span>${n} 只缩量横盘 · 回测不提高拉升概率，只用于避免追高，不是买点</span></div>
    ${coil.slice(0, 8).map(watchRow).join("")}
    ${go.length ? `<div class="ev-head" style="margin-top:calc(10 * var(--u))">刚启动（放量创新高）<span>仍未确认，只作"从埋伏转观察"的提示</span></div>
      ${go.slice(0, 4).map(watchRow).join("")}` : ""}
    <button class="linkbtn" data-action="watch-info" style="margin-top:calc(10 * var(--u))">为什么这些票不是买点 ›</button>
  </div>`;
}

/* ── 个股推荐位（每个时段只展示评分第一的那只） ── */
function featuredHtml(s) {
  if (!s) return "";
  const pct = s.pct_chg;
  const hits = (s.hits || []).slice(0, 3).map(h => `<span class="badge">${esc(h.strategy_name)}</span>`).join("");
  let tb = "";
  if (s.tradable && s.tradable !== "—") {
    const color = s.tradable_rank === 0 ? "var(--rise)" : s.tradable_rank === 1 ? "#d99a2b" : "var(--t3)";
    const gap = (s.limit_gap !== null && s.limit_gap !== undefined)
      ? (s.limit_gap <= 0.05 ? " · 已封死" : ` · 距涨停 ${num(s.limit_gap, 1)}%`) : "";
    tb = `<span class="badge" style="color:${color};border-color:${color}">${esc(s.tradable)}${gap}</span>`;
  }
  let chase = "";
  if (s.chase) {
    const c = s.chase === "高危" ? "var(--fall)" : "#d99a2b";
    chase = `<span class="badge" style="color:${c};border-color:${c}">${esc(s.chase)}</span>`;
  }
  return `<div class="feat-card" data-nav="#/stock/${esc(s.code)}">
    <div class="feat-top">
      <span class="feat-tag">TOP 1</span>
      <span class="feat-score">${num(s.score, 1)}<small>分</small></span>
    </div>
    <div class="feat-main">
      <div class="feat-name">${esc(s.name)}<span class="stock-code">${esc(s.code)}</span></div>
      <div class="feat-price ${dirText(pct)}">${num(s.close)}<span class="feat-pct">${signed(pct)}%</span></div>
    </div>
    <div class="feat-why">${esc((s.hits && s.hits[0] && s.hits[0].reason) || "")}</div>
    <div class="hitline">${tb}${chase}${trendBadge(s)}${hits}</div>
    <div class="feat-foot">${esc(s.industry || "—")} · 命中 ${s.hit_count} 条<span class="feat-more">查看 ›</span></div>
  </div>`;
}

function featSig(s) {
  return s ? [s.code, s.close, s.pct_chg, s.score, s.tradable, s.limit_gap, s.chase,
              s.hit_count, (s.hits || []).map(h => h.strategy_name).join(",")].join("|") : "none";
}

function patchFeatured(container, s) {
  if (!container) return;
  const sig = featSig(s);
  if (container.dataset.sig === sig) return;
  container.dataset.sig = sig;
  container.innerHTML = featuredHtml(s);
}

/* ── 大盘页 ───────────────────────────────────────────── */
function liveLineHtml(intra) {
  const live = (intra && intra.live) || {};
  if (live.source === "cache") {
    return `<span style="color:var(--gold)">● 快照缓存 ${esc(String(live.quote_time || live.stale_since || "").slice(11, 19))}</span>`
         + ` · 实时源暂不可用（限流/断网），展示最近一次成功拉取的快照`
         + (intra && intra.buyable_count !== undefined
            ? ` · 可买 <b style="color:var(--rise)">${intra.buyable_count}</b> 只 / 已封板 ${intra.sealed_count} 只` : "");
  }
  if (live.source !== "realtime") {
    return `<span style="color:var(--gold)">● 实时源不可用</span>${live.reason ? ` · ${esc(live.reason)}` : ""} · 已回退本地回放`;
  }
  const qt = String(live.quote_time || "").slice(11, 19);
  return `<span style="color:var(--rise)">● 实时行情 ${esc(qt)}</span> · ${esc(live.phase || "")}`
       + (live.is_trading ? " · 点击右上角刷新" : " · 非交易时段，展示当日快照")
       + (intra && intra.buyable_count !== undefined
          ? ` · 可买 <b style="color:var(--rise)">${intra.buyable_count}</b> 只 / 已封板 ${intra.sealed_count} 只` : "");
}

async function renderMarket() {
  setHeader({ title: "大盘", sub: "", action: `<button class="iconbtn" data-action="refresh" aria-label="刷新"><svg class="ic"><use href="#i-sync"/></svg></button>` });
  setTabbar("market");
  view.innerHTML = `<div class="card" style="margin-top:calc(32 * var(--u))">
      <div class="skel skel-hero"></div><div class="skel skel-row"></div>
    </div>`;
  const [o, intra] = await Promise.all([
    getOverview(), api("/api/recommend/%E7%9B%98%E4%B8%AD?buyable=1"),
  ]);
  setHeader({ title: "大盘", sub: appSubtitle(o), action: `<button class="iconbtn" data-action="refresh" aria-label="刷新"><svg class="ic"><use href="#i-sync"/></svg></button>` });
  view.innerHTML = `
    ${envCard(o)}
    <div class="hint" id="liveLine" style="margin-top:calc(28 * var(--u))">${liveLineHtml(intra)}</div>
    <div id="eventBlock" style="margin-top:calc(28 * var(--u))">${eventBlockHtml(intra.events)}</div>
    <div class="hint" style="margin-top:calc(48 * var(--u));text-align:center;color:var(--t4)">
      大盘环境用于判断「今天能不能做」；事件提醒为已封板个股，供打板/次日跟踪
    </div>`;
  paintIndex(o);
}

async function refreshMarket(force = false) {
  if (state.marketBusy) return;
  if (!$("#envCard")) return renderMarket();
  state.marketBusy = true;
  try {
    if (force) store.overview = null;
    const [o, intra] = await Promise.all([
      getOverview(force), api("/api/recommend/%E7%9B%98%E4%B8%AD?buyable=1" + (force ? "&refresh=1" : "")),
    ]);
    setHeader({ title: "大盘", sub: appSubtitle(o), action: `<button class="iconbtn" data-action="refresh" aria-label="刷新"><svg class="ic"><use href="#i-sync"/></svg></button>` });
    patchEnv(o);
    const ll = $("#liveLine");
    if (ll) ll.innerHTML = liveLineHtml(intra);
    const eb = $("#eventBlock");
    if (eb) eb.innerHTML = eventBlockHtml(intra.events);
  } finally {
    state.marketBusy = false;
  }
}

function splitByKind(stocks) {
  const A = [], B = [];
  (stocks || []).forEach(s => { (s.kind === "回踩" ? B : A).push(s); });
  return { A, B };
}

/* 推荐页数据拆分：TOP1 只出现在推荐位，列表里去掉它（名次保留真实序号） */
function homeParts(data) {
  const stocks = data.stocks || [];
  const top = stocks[0] || null;
  const rest = stocks.slice(1);
  const { A, B } = splitByKind(rest);
  // 名次按「页面展示顺序」连续编号（回踩低吸在前，突破在后），从 02 起（01 是推荐位）
  let n = 2;
  [...B, ...A].forEach(s => { s._rank = n++; });
  return { top, A, B, restCount: rest.length };
}

function paintIndex(o) {
  const box = $("#idxChart");
  if (!box || box.dataset.drawn === "1") return;
  getIndex().then(ix => {
    if (!box || !ix || !ix.closes || !ix.closes.length) return;
    const vals = ix.closes.filter(v => v !== null && v !== undefined && isFinite(v));
    const env = o.env || {};
    const dayPct = (env.index_pct === null || env.index_pct === undefined) ? env.ret20_pct : env.index_pct;
    const dayColor = (dayPct >= 0) ? "var(--rise)" : "var(--fall)";
    box.innerHTML = sparkSVG(ix.closes, {
      ma20: ix.ma20, prevClose: env.prev_close, prevColor: dayColor,
      endColor: dayColor, lineColor: "var(--t2)",
    }) + `<div class="chart-cap">近 ${vals.length} 个交易日
      <span class="${dirText(dayPct)}">今日 ${signed(dayPct)}%</span>
      <span class="muted">· 虚线为昨收 ${num(env.prev_close)} · 蓝线 MA20</span></div>`;
    box.dataset.drawn = "1";
  }).catch(() => {});
}

/* 原地更新大盘卡：只改文本节点，不重建 DOM（避免整块闪烁） */
function patchEnv(o) {
  if (!$("#envCard")) return;
  const e = o.env || {};
  const ok = e.ok !== false;
  const todayPct = (e.index_pct === null || e.index_pct === undefined) ? e.ret20_pct : e.index_pct;
  const tc = dirText(todayPct);
  const gate = e.gate || "中性";
  const el = (f) => document.querySelector(`[data-f="${f}"]`);
  const txt = (f, v, cls) => { const n = el(f); if (!n) return; if (cls) n.className = cls; n.textContent = v; };
  const html = (f, v) => { const n = el(f); if (n) n.innerHTML = v; };

  txt("date", e.live ? `实时 ${String(e.quote_time || "").slice(11, 16)}` : `截至 ${(o.data_date || "").slice(5)}`);
  txt("idx", ok ? num(e.index_close) : "—", `hero-num ${tc}`);
  txt("pct", ok ? signed(todayPct) + "%" : "—", `hero-chg ${tc}`);
  const g = el("gate");
  if (g) { g.dataset.gate = gate; g.innerHTML = `<i class="dot" style="background:currentColor"></i>${esc(gate)}`; }
  txt("cap", `上证指数 · ${e.live ? "实时" : "今日"}`, "hero-cap");
  txt("structure", e.structure || e.trend || "—",
      `kv-v sm ${e.structure === "空头排列" ? "fall" : e.structure === "多头排列" ? "rise" : ""}`);
  txt("mastruct", `收盘 ${num(e.index_close)} ${cmp(e.index_close, e.ma20)} MA20 ${num(e.ma20)} ${cmp(e.ma20, e.ma60)} MA60 ${num(e.ma60)}`, "kv-sub num");
  txt("ret5", `${signed(e.ret5_pct)}%`, `kv-v sm num ${dirText(e.ret5_pct)}`);
  txt("ret5sub", `20 日 ${signed(e.ret20_pct)}% · MA20 ${signed(e.ma20_slope_pct)}%/5日`, "kv-sub num");
  html("zt", `<span class="rise">${e.zt_count ?? "—"}</span><span class="muted"> / </span><span class="fall">${e.broken_count ?? "—"}</span>`);
  txt("ztshare", `占全市场 ${num(e.zt_share, 1)}%（${e.limit_base ?? "—"} 只）`, "kv-sub num");
  txt("seal", `${num(e.seal_rate, 1)}%`, "kv-v sm num");
  txt("sealsub", `摸过涨停 ${e.touched_count ?? "—"} 只，封住 ${e.zt_count ?? "—"} 只`, "kv-sub num");
  html("updown", `<span class="rise">${e.up ?? "—"}</span><span class="muted"> / </span><span class="fall">${e.down ?? "—"}</span>`);
  txt("today", `${signed(todayPct)}%`, `kv-v sm num ${tc}`);
  txt("prev", `前收 ${num(e.prev_close)}`, "kv-sub");
  const secs = el("sectors");
  if (secs) {
    const arr = e.top_sectors || [];
    secs.innerHTML = arr.map(s => `<span class="chip">${esc(s.name)} <b class="${s.pct >= 0 ? "rise" : "fall"}">${signed(s.pct)}%</b></span>`).join("");
    secs.style.display = arr.length ? "" : "none";
  }
  txt("note", e.note || "", "env-note");
}

/* 股票列表 keyed diff：按 code 复用 DOM，只更新变了的行 */
function patchList(container, stocks, showEmpty = true) {
  if (!container) return;
  const list = stocks || [];
  container.querySelector(".empty")?.remove();
  const existing = new Map([...container.querySelectorAll(".stock-row")].map(x => [x.dataset.code, x]));
  const used = new Set();
  list.forEach((s, i) => {
    let node = existing.get(s.code);
    const sig = rowSig(s, i);
    if (!node) {
      const wrap = document.createElement("div");
      wrap.className = "stock-row";
      wrap.dataset.code = s.code;
      wrap.dataset.nav = `#/stock/${s.code}`;
      wrap.innerHTML = stockRowInner(s, i);
      wrap.dataset.sig = sig;
      node = wrap;
    } else if (node.dataset.sig !== sig) {
      node.innerHTML = stockRowInner(s, i);
      node.dataset.sig = sig;
    }
    container.appendChild(node);
    used.add(s.code);
  });
  existing.forEach((node, code) => { if (!used.has(code)) node.remove(); });
  if (!list.length && showEmpty) {
    container.innerHTML = `<div class="empty"><svg class="ic"><use href="#i-trend"/></svg>该时段今日无命中</div>`;
  }
}

/* 静默刷新：不重建页面，不闪白，不丢滚动位置 */
async function refreshHome(force = false) {
  if (state.homeBusy) return;
  if (!$("#bucketNote")) return renderHome();
  state.homeBusy = true;
  try {
    const b = state.bucket && BUCKET_META[state.bucket] ? state.bucket : "盘前";
    if (force) store.overview = null;
    const [o, data] = await Promise.all([getOverview(force), getBucket(b, force || b === "盘中")]);
    setHeader({ title: "选股复盘", sub: appSubtitle(o), action: `<button class="iconbtn" data-action="refresh" aria-label="刷新"><svg class="ic"><use href="#i-sync"/></svg></button>` });
    const bn = $("#bucketNote");
    if (bn) bn.innerHTML = bucketNote(o, b, data);
    const wb = $("#watchBlock");
    if (wb) wb.innerHTML = b === "盘中" ? watchBlockHtml(data.watch) : "";
    const ch = $("#chipRow");
    if (ch) ch.innerHTML = chipsHtml(data);
    const parts = homeParts(data);
    patchFeatured($("#featured"), parts.top);
    patchList($("#stockListA"), parts.A, parts.restCount > 0);
    patchList($("#stockListB"), parts.B, parts.restCount > 0);
    const ha = $("#headA"), hb = $("#headB");
    if (ha) { ha.hidden = !parts.A.length; const c = $("#cntA"); if (c) c.textContent = `${parts.A.length}`; }
    if (hb) { hb.hidden = !parts.B.length; const c = $("#cntB"); if (c) c.textContent = `${parts.B.length}`; }
  } finally {
    state.homeBusy = false;
  }
}


/* ─────────── 屏幕：连板股锐利档（候选池 / 盘中动量） ─────────── */
const lp = {
  mode: "pool",        // 进页面默认看候选池（缓存即可秒开）
  busy: false, pool: null, mom: null, err: "", openReview: false,
  poolFromCache: false, // true = 当前候选池来自 localStorage（非本次查询）
};

/* ── 候选池本地缓存（localStorage）─────────────────────────────
   🔴 为什么缓存候选池：它是 **T-1 收盘截面**，一天之内不会变，
      而 liupan_scan 每次要跑 4995 只票的因子窗口（≥1.5s）。每进一次页面重扫一次纯浪费。
   🔴 为什么不缓存动量：动量 = 先手分 = 实时快照，缓存就失去意义。
      但动量可以**复用缓存池的名单**，只重算分 —— 省掉全市场扫描，且保证
      「动量排序的那批票」与「你看到的候选池」是同一批，不会串。
   🔴 写缓存的唯一入口 = 真查询成功（不打 URL 参数就不写）。 */
const LP_LS_KEY = "saa.liupan.pool.v2";

function lpCacheGet() {
  try {
    const raw = localStorage.getItem(LP_LS_KEY);
    if (!raw) return null;
    const j = JSON.parse(raw);
    // 只认「完整成功」的池；结构变更（version 不符）也当无缓存，避免旧格式炸渲染
    if (!j || !j.pool || !j.pool.ok || !Array.isArray(j.pool.items)) return null;
    return j;
  } catch (e) {
    return null;      // 隐私模式 / 配额满 / JSON 脏 → 一律当作无缓存（不阻断）
  }
}

function lpCacheSet(pool, trace) {
  try {
    localStorage.setItem(LP_LS_KEY, JSON.stringify({
      pool: pool, trace: trace || lp.traceSummary || null, _cachedAt: Date.now(),
    }));
  } catch (e) { /* 写不进就退化为每次都查，不影响功能 */ }
}

function lpCacheClear() {
  try { localStorage.removeItem(LP_LS_KEY); } catch (e) {}
}

/* 缓存是否已过自然日 —— 候选池是 T-1 收盘截面，隔夜后基本作废，必须显式告警。
   🔴 禁自证：这里只用「缓存写入的日历日 vs 今天」这个外部事实，
      绝不拿缓存的 asof 去和缓存自己比「是否最新」（那样永远是最新）。 */
function lpCacheStale(c) {
  const at = c && c._cachedAt ? new Date(c._cachedAt) : null;
  if (!at || isNaN(at.getTime())) return false;
  return at.toDateString() !== new Date().toDateString();
}

/* 缓存新鲜度：只如实报「缓存时间 + 池自身的 as-of」，不猜它是不是最新。 */
function lpCacheLabel(c) {
  const asof = (c && c.pool && c.pool.asof) || "—";
  const at = c && c._cachedAt ? new Date(c._cachedAt) : null;
  if (!at || isNaN(at.getTime())) return `池 as-of ${esc(asof)}`;
  const pad = (n) => String(n).padStart(2, "0");
  const hh = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  const sameDay = at.toDateString() === new Date().toDateString();
  const when = sameDay ? `今天 ${hh}`
    : `${at.getMonth() + 1}-${at.getDate()} ${hh}`;
  return `池 as-of ${esc(asof)} · 缓存于${when}`;
}

const LP_LEVEL = (v) => (v >= 0.60 ? "rise" : v >= 0.45 ? "warn" : "muted");
const LP_LEVEL_TXT = (v) => (v >= 0.60 ? "强" : v >= 0.45 ? "中性" : "弱");

function lpTags(it) {
  const t = [];
  if (it.zb) t.push(`<span class="badge" style="color:var(--fall);border-color:var(--fall)">炸板</span>`);
  if ((it.zt20 || 0) >= 2) t.push(`<span class="badge">20日${it.zt20}板</span>`);
  if (it.consist === "同向") t.push(`<span class="badge" style="color:var(--rise);border-color:var(--rise)">板块共振</span>`);
  else if (it.consist === "背离") t.push(`<span class="badge" style="color:#d99a2b;border-color:#d99a2b">板块背离</span>`);
  return t.join("");
}

function lpRow(it) {
  const pct = it.pct;
  return `<div class="stock-row" data-code="${esc(it.code)}" data-nav="#/stock/${esc(it.code)}">
    <div class="stock-top">
      <span class="rank ${it.rank <= 3 ? "top" : ""}">${String(it.rank).padStart(2, "0")}</span>
      <div class="row-main">
        <div class="stock-name">${esc(it.name || it.code)}<span class="stock-code">${esc(it.code6 || it.code)}</span></div>
        <div class="row-s">${esc(it.ind || "—")} · 换手 ${num(it.turn, 2)}% · 板块涨停 ${it.sect_zt ?? "—"} 家 · 板涨 ${signed(it.sect_med, 2)}%</div>
      </div>
      <div class="stock-right">
        <div class="stock-price ${dirText(pct)}">${signed(pct, 2)}%</div>
        <div class="stock-pct ${LP_LEVEL(it.score)}">先手 ${num(it.score, 3)}</div>
      </div>
    </div>
    <div class="hitline">
      <span class="badge ${LP_LEVEL(it.score)}" style="border-color:currentColor">${LP_LEVEL_TXT(it.score)}先手</span>
      <span class="badge">距涨停 ${num(it.room, 2)}%</span>
      ${lpTags(it)}
    </div>
  </div>`;
}

function lpPoolRow(it, i) {
  return `<div class="stock-row" data-code="${esc(it.code)}" data-nav="#/stock/${esc(it.code)}">
    <div class="stock-top">
      <span class="rank">${String(i + 1).padStart(2, "0")}</span>
      <div class="row-main">
        <div class="stock-name">${esc(it.name)}<span class="stock-code">${esc(it.code6)}</span></div>
        <div class="row-s">候选池成员 · 待先手分排序</div>
      </div>
    </div>
  </div>`;
}

function lpHead() {
  const d = lp.mode === "pool" ? lp.pool : lp.mom;
  const modeTxt = lp.mode === "pool" ? "候选池" : "盘中动量";
  const on = (m) => (lp.mode === m ? " on" : "");
  const cnt = d && d.ok
    ? (lp.mode === "pool"
        ? `带内 ${d.n} 只 · 展示前 ${d.n_show ?? d.n}`
        : `${d.n} 只可买`)
    : "";
  const isCache = lp.mode === "pool" && lp.poolFromCache;
  const sub = d && d.ok
    ? (isCache
        ? `${modeTxt} · 池 as-of ${esc(d.asof || "—")} · ${cnt} · 📦 本地缓存`
        : `${modeTxt} · 池 as-of ${esc(d.asof || "—")} · ${cnt} · 查询 ${esc(d.ts || "")}${d.cost_ms ? ` · ${(d.cost_ms / 1000).toFixed(1)}s` : ""}`)
    : `${modeTxt} · 点右上按钮查询`;
  setHeader({
    title: "连板股",
    sub,
    action: `<button class="lp-btn${on("pool")}" data-liupan="pool">候选池</button>
             <button class="lp-btn${on("momentum")}" data-liupan="momentum">盘中动量</button>`,
  });
}

function lpBody() {
  if (lp.busy) {
    return `<div class="card" style="margin-top:calc(28 * var(--u))">
      <div class="skel skel-row"></div><div class="skel skel-row"></div><div class="skel skel-row"></div>
      <div class="hint" style="text-align:center;padding:calc(12 * var(--u)) 0">查询中…盘中动量需要拉全市场实时快照，约 60–120 秒</div>
    </div>`;
  }
  if (lp.err) {
    return `<div class="card" style="margin-top:calc(28 * var(--u))">
      <div class="warnbox">⚠️ ${esc(lp.err)}</div>
      <div style="text-align:center;padding-top:calc(16 * var(--u))">
        <button class="linkbtn" data-liupan-refresh="${lp.mode}">重试</button>
      </div>
    </div>`;
  }
  const d = lp.mode === "pool" ? lp.pool : lp.mom;
  if (!d) return `<div class="hint" style="text-align:center;padding:calc(48 * var(--u)) 0">点右上「候选池」或「盘中动量」开始查询</div>`;
  if (!d.ok) return `<div class="card" style="margin-top:calc(28 * var(--u))"><div class="warnbox">⚠️ ${esc(d.error || "查询失败")}</div></div>`;

  let out = "";

  /* 📦 候选池来自本地缓存：明示「这不是刚查的」+ 给出唯一刷新入口 */
  if (lp.mode === "pool" && lp.poolFromCache) {
    const c = lpCacheGet();
    const stale = lpCacheStale(c);
    /* 🔴 隔夜 = 池子作废（T-1 截面已经翻篇）→ 换橙色、换文案、按钮加「建议」 */
    const tint = stale ? "rgba(217,122,43,0.13)" : "rgba(235,178,57,0.06)";
    const edge = stale ? "#d97a2b" : "var(--line)";
    const note = stale
      ? "⚠️ 这是<b>隔夜缓存</b>（池 as-of 已翻篇）· 请点右侧重新查询"
      : "进页面直接读缓存 · 点上方「候选池」或右侧刷新都会重新查询";
    out += `<div style="display:flex;align-items:center;justify-content:space-between;gap:calc(10 * var(--u));
      margin:calc(16 * var(--u)) 0 calc(2 * var(--u));padding:calc(10 * var(--u)) calc(12 * var(--u));
      border:1px solid ${edge};border-radius:var(--r-sm);background:${tint}">
      <span class="hint" style="flex:1">📦 ${lpCacheLabel(c)}<br>${note}</span>
      <button class="linkbtn" data-liupan-refresh="pool" style="flex:none;white-space:nowrap;padding:calc(12 * var(--u)) calc(16 * var(--u));border:1px solid ${stale ? "#d97a2b" : "var(--line)"};border-radius:var(--r-sm)${stale ? ";color:#d97a2b" : ""}">${stale ? "刷新（建议）" : "刷新"}</button>
    </div>`;
  }

  if (lp.mode === "momentum") {
    /* ⚡ 动量复用了缓存池：池名单来自缓存、先手分是刚算的 —— 两件事必须说清 */
    if (d.pool_source === "local_cache") {
      out += `<div class="hint" style="margin:calc(14 * var(--u)) 0 calc(2 * var(--u))">
        ⚡ 池复用本地缓存（${d.n_pool ?? "—"} 只 · as-of ${esc(d.pool_asof || "—")}）· <b>先手分实时计算</b>
      </div>`;
    }
    const items = d.items || [];
    const hot = (d.hot || []).filter(h => h.zt > 0).slice(0, 6);
    if (hot.length) {
      out += `<div class="group-head">板块异动 <span class="muted">同向共振优先</span></div>
        <div class="lp-hot">${hot.map(h => `<span class="lp-hotitem"><b>${esc(h.ind)}</b> ${h.zt}/${h.n}${h.names && h.names.length ? `<i>${esc(h.names.slice(0, 3).join(" / "))}</i>` : ""}</span>`).join("")}</div>`;
    }
    out += `<div class="group-head">锐利档 · 按先手分排序 <span class="muted">${items.length} 只</span></div>`;
    out += items.length ? items.map(lpRow).join("") : `<div class="hint" style="text-align:center;padding:calc(32 * var(--u)) 0">今日无可买候选</div>`;
    const lk = d.locked || [];
    if (lk.length) {
      out += `<div class="group-head">已封板（买不进·仅供跟踪） <span class="muted">${lk.length} 只</span></div>`;
      out += lk.map(x => `<div class="stock-row" data-code="${esc(x.code)}" data-nav="#/stock/${esc(x.code)}">
        <div class="stock-top">
          <span class="rank">封</span>
          <div class="row-main"><div class="stock-name">${esc(x.name)}<span class="stock-code">${esc(x.code6)}</span></div>
            <div class="row-s">${esc(x.ind || "—")} · 换手 ${num(x.turn, 2)}%</div></div>
          <div class="stock-right"><div class="stock-price rise">${signed(x.pct, 2)}%</div>
            <div class="stock-pct muted">先手 ${num(x.score, 3)}</div></div>
        </div></div>`).join("");
    }
    out += `<div class="hint" style="padding:calc(20 * var(--u)) 0 calc(8 * var(--u))">
      🔴 先手分 = 0.45×涨幅 + 0.30×换手 + 0.25×板块共振；涨幅 &gt;1.93% 即顶格（竞价锚点复用），此时分数不代表强度。
      先手 = 盯盘优先级，<b>非买入信号</b>。</div>`;
  } else {
    const items = d.items || [];
    out += `<div class="group-head">候选池 · 锐利档 <span class="muted">带内 ${items.length} 只 · 按蓄势分排序 · 待盘中重排</span></div>`;
    out += items.length ? items.map(lpPoolRow).join("") : `<div class="hint" style="text-align:center;padding:calc(32 * var(--u)) 0">今日候选池为空</div>`;
    out += `<div class="hint" style="padding:calc(20 * var(--u)) 0 calc(8 * var(--u))">
      候选池 = T-1 收盘截面筛出的连板潜力票（全市场 ${d.n_universe ?? "—"} 只里选 ${d.n ?? "—"} 只）。
      <b>是过滤器，不是买入信号</b>；点「盘中动量」才出先手分排序。</div>`;
  }
  return out;
}

function lpFoot() {
  const t = (lp.mom && lp.mom.trace) || lp.traceSummary || null;
  const last = lp.mom && lp.mom.ts ? lp.mom.ts : "—";
  return `<div class="card lp-review-card" data-nav="#/liupan/review" style="margin-top:calc(32 * var(--u));margin-bottom:calc(24 * var(--u))">
    <div class="lp-review-top">
      <div>
        <div class="lp-review-title">连板股复盘</div>
        <div class="row-s">基于决策轨迹日志 · 每次查询都已存档</div>
      </div>
      <svg class="ic"><use href="#i-chev"/></svg>
    </div>
    <div class="lp-review-meta">${t ? `累计 ${t.total} 次查询 · ${t.days} 个交易日 · 最近 ${esc(t.last || last)}` : "暂无轨迹"}</div>
  </div>`;
}

async function lpLoad(mode, force) {
  if (lp.busy) return;
  lp.mode = mode;
  lp.err = "";

  /* ① 候选池 + 非强制刷新 → 命中 localStorage 就直接渲染，**一个请求都不发**
     （force=true 只来自「刷新」按钮，那也是唯一会覆盖缓存的入口） */
  if (mode === "pool" && !force) {
    const c = lpCacheGet();
    if (c) {
      lp.pool = c.pool;
      lp.poolFromCache = true;
      if (c.trace) lp.traceSummary = c.trace;
      lpHead(); view.innerHTML = lpBody() + lpFoot();
      return;
    }
  }

  lp.busy = true;
  lpHead(); view.innerHTML = lpBody() + lpFoot();
  try {
    let qs = force ? "refresh=1" : "";

    /* ② 盘中动量：优先复用缓存池的名单 → 只实时算先手分，不重扫全市场
       🔴 先手分必须实时（refresh=1），缓存池只提供「算哪些票」 */
    if (mode === "momentum") {
      const c = lpCacheGet();
      const items = (c && c.pool && c.pool.items) || [];
      const codes = items.map((x) => x.code).filter(Boolean);
      if (codes.length) {
        const p = new URLSearchParams();
        p.set("codes", codes.join(","));
        if (c.pool.asof) p.set("pool_asof", c.pool.asof);
        if (c.pool.tier) p.set("tier", c.pool.tier);
        p.set("refresh", "1");
        qs = p.toString();
      } else if (!force) {
        qs = "refresh=1";       // 没有缓存池可复用 → 退回实时全量
      }
    }

    const d = await api(`/api/liupan/${mode}${qs ? "?" + qs : ""}`);
    if (mode === "pool") {
      lp.pool = d;
      lp.poolFromCache = false;
      if (d.ok) {
        lpCacheSet(d);                  // 🔴 真查询成功才写缓存
        /* 点按钮/刷新就有即时反馈：池子同一天内容本就不变，不提示等于「看着没刷新」 */
        if (force) toast(`已刷新 · 池 as-of ${d.asof || "—"} · ${d.n ?? 0} 只 · ${((d.cost_ms || 0) / 1000).toFixed(1)}s`);
      }
    } else {
      lp.mom = d;
    }
    if (!d.ok) lp.err = d.error || "查询失败";
  } catch (e) {
    lp.err = e.message || String(e);
  } finally {
    lp.busy = false;
    lpHead(); view.innerHTML = lpBody() + lpFoot();
  }
}

async function renderLiupan() {
  setTabbar("liupan");

  /* 首屏：候选池优先读 localStorage —— 命中则**零请求**直接渲染（含缓存时间提示）。
     🔴 不再自动打 /api/liupan/latest：那等于「每次进页面都偷偷重扫一次」，
        正是这次要治的问题。 */
  if (!lp.pool) {
    const c = lpCacheGet();
    if (c) {
      lp.pool = c.pool;
      lp.poolFromCache = true;
      if (c.trace) lp.traceSummary = c.trace;
    }
  }

  lpHead(); view.innerHTML = lpBody() + lpFoot();

  /* 唯一会自动发请求的情形：本机从没缓存过候选池（首次使用 / 清了浏览器数据） */
  if (!lp.pool && !lp.mom && !lp.busy) {
    await lpLoad("pool", false);
  }
}

/* ─────────── 屏幕：连板股复盘看板 ─────────── */
function lpRevMetric(label, v, d = 2) {
  if (!v) return `<div class="lp-metric"><div class="lp-metric-k">${label}</div><div class="lp-metric-v muted">—</div><div class="lp-metric-s">无样本</div></div>`;
  const c = v.avg >= 0 ? "rise" : "fall";
  return `<div class="lp-metric">
    <div class="lp-metric-k">${label}</div>
    <div class="lp-metric-v ${c}">${signed(v.avg, d)}%</div>
    <div class="lp-metric-s">中位 ${signed(v.median, d)}% · 胜率 ${num(v.win, 0)}% · n=${v.n}</div>
  </div>`;
}

async function renderLiupanReview() {
  setHeader({ title: "连板股复盘", sub: "决策轨迹 × 事后表现", back: true });
  setTabbar(null);
  loadingView();
  let d;
  try { d = await api("/api/liupan/review?limit=80&topn=5"); }
  catch (e) { return errorView(e, "#/liupan"); }

  const sm = d.summary || {};
  const rows = d.rows || [];
  if (!rows.length) {
    view.innerHTML = `<div class="hint" style="text-align:center;padding:calc(60 * var(--u)) 0">
      暂无轨迹。<br>去「连板股」页点一次「候选池」或「盘中动量」，每次查询都会自动存档。</div>`;
    return;
  }
  const body = rows.map(r => {
    const picks = (r.top || []).map(p => {
      const c1 = p.r1 === null || p.r1 === undefined ? "muted" : (p.r1 > 0 ? "rise" : p.r1 < 0 ? "fall" : "flat");
      const c3 = p.r3 === null || p.r3 === undefined ? "muted" : (p.r3 > 0 ? "rise" : p.r3 < 0 ? "fall" : "flat");
      return `<tr>
        <td>${esc(p.name || p.code)}<span class="muted"> ${esc((p.code || "").slice(0, 6))}</span></td>
        <td class="num">${num(p.score, 3)}</td>
        <td class="num ${p.pct > 0 ? "rise" : p.pct < 0 ? "fall" : "flat"}">${signed(p.pct, 2)}%</td>
        <td class="num ${c1}">${p.r1 === null || p.r1 === undefined ? "待" : signed(p.r1, 2) + "%"}</td>
        <td class="num ${c3}">${p.r3 === null || p.r3 === undefined ? "待" : signed(p.r3, 2) + "%"}</td>
      </tr>`;
    }).join("");
    return `<div class="lp-rev-block">
      <div class="lp-rev-head">
        <span class="lp-rev-ts">${esc((r.ts || "").slice(5, 16))}</span>
        <span class="lp-rev-kind">${r.kind === "pool" ? "候选池" : "盘中动量"}</span>
        <span class="muted">${r.n} 只 · 池 ${esc(r.asof || "—")}</span>
        ${r.avg_r1 === null || r.avg_r1 === undefined ? `<span class="lp-rev-sum muted">待验证</span>`
          : `<span class="lp-rev-sum ${r.avg_r1 > 0 ? "rise" : "fall"}">Top5 均 ${signed(r.avg_r1, 2)}%</span>`}
      </div>
      <table class="lp-table"><thead><tr>
        <th>票</th><th class="num">先手分</th><th class="num">当时涨幅</th><th class="num">T+1</th><th class="num">T+3</th>
      </tr></thead><tbody>${picks}</tbody></table>
    </div>`;
  }).join("");

  view.innerHTML = `
    <div class="lp-metrics">
      ${lpRevMetric("Top5 · T+1", sm.r1)}
      ${lpRevMetric("Top5 · T+3", sm.r3)}
      ${lpRevMetric("Top5 · T+5", sm.r5)}
    </div>
    <div class="hint" style="padding:calc(8 * var(--u)) 0 calc(20 * var(--u))">
      已存档 ${sm.queries || 0} 次查询 · 样本 ${sm.picks || 0} 票次 · 已验证 ${sm.verified || 0} 票次。
      基准 = 查询当日收盘（查询发生在盘中）；T+1 为 A 股最早可卖日。
    </div>
    <div class="group-head">逐次查询记录 <span class="muted">新 → 旧</span></div>
    ${body}
    <div class="hint" style="padding:calc(24 * var(--u)) calc(4 * var(--u))">
      ⚠️ 口径提示：轨迹只记录「当时选出来的是谁、几分」，不构成买卖建议；
      收益按 T 日收盘为基准、T+N 收盘结算，未扣手续费与滑点。
    </div>`;
}

async function renderHome() {
  setHeader({ title: "选股复盘", sub: "", action: `<button class="iconbtn" data-action="refresh" aria-label="刷新"><svg class="ic"><use href="#i-sync"/></svg></button>` });
  setTabbar("home");
  const b = state.bucket && BUCKET_META[state.bucket] ? state.bucket : "盘前";
  const first = !$("#envCard");
  if (first) {
    view.innerHTML = `<div class="card" style="margin-top:calc(32 * var(--u))">
        <div class="skel skel-hero"></div><div class="skel skel-row"></div><div class="skel skel-row"></div>
      </div>`;
  }
  const [o, data] = await Promise.all([getOverview(), getBucket(b, b === "盘中")]);
  setHeader({ title: "选股复盘", sub: appSubtitle(o), action: `<button class="iconbtn" data-action="refresh" aria-label="刷新"><svg class="ic"><use href="#i-sync"/></svg></button>` });

  const parts = homeParts(data);
  const isIntra = b === "盘中";
  view.innerHTML = `
    <div class="seg" style="margin-top:calc(40 * var(--u))">
      ${["盘前", "盘中", "盘后"].map(x => `<button data-bucket="${x}" class="${x === b ? "on" : ""}">${x}</button>`).join("")}
    </div>
    <div id="featured" style="margin-top:calc(24 * var(--u))">${featuredHtml(parts.top)}</div>
    <div id="bucketNote">${bucketNote(o, b, data)}</div>
    ${isIntra ? `<div class="seg" style="margin-top:calc(20 * var(--u))">
      <button data-buyable="1" class="${state.intraFilter !== "all" ? "on" : ""}">只看可买</button>
      <button data-buyable="0" class="${state.intraFilter === "all" ? "on" : ""}">全部（含已封板）</button>
    </div>` : ""}
    <div id="watchBlock" style="margin-top:calc(20 * var(--u))">${isIntra ? watchBlockHtml(data.watch) : ""}</div>
    <div class="chiprow" id="chipRow" style="margin-top:calc(32 * var(--u))">${chipsHtml(data)}</div>
    <div style="text-align:right;margin-top:calc(10 * var(--u))">
      <button class="linkbtn" data-action="scoring">评分规则 ›</button>
    </div>
    ${isIntra
      ? `<div class="group-head" id="headB" ${parts.B.length ? "" : "hidden"}>回踩低吸型 <span class="muted">优先 · <span id="cntB">${parts.B.length}</span> 只</span></div>
         <div id="stockListB"></div>
         <div class="group-head" id="headA" ${parts.A.length ? "" : "hidden"}>突破型 <span class="muted"><span id="cntA">${parts.A.length}</span> 只</span></div>
         <div id="stockListA"></div>`
      : `<div id="stockListA" style="margin-top:calc(16 * var(--u))"></div>`}
    <div class="hint" id="homeFoot" style="padding:calc(48 * var(--u)) 0 calc(24 * var(--u));text-align:center">
      知识库 ${o.kb.selection_cards} 张选股卡 · ${o.strategies.total} 条可执行策略
    </div>`;

  patchList($("#stockListA"), parts.A, parts.restCount > 0);
  patchList($("#stockListB"), parts.B, parts.restCount > 0);
  patchFeatured($("#featured"), parts.top);
}

/* ─────────── 屏幕：个股详情 ─────────── */
async function renderStock(code) {
  setHeader({ title: "个股", back: true });
  setTabbar(null);
  loadingView();
  let s;
  try { s = await api(`/api/stock/${encodeURIComponent(code)}`); }
  catch (e) { return errorView(e, `#/stock/${code}`); }

  const pct = s.pct_chg;
  setHeader({
    title: s.name || code,
    sub: `${code}${s.industry ? " · " + s.industry : ""}`,
    back: true,
  });

  const hits = (s.hits || []).map(h => `
    <div class="hit-item">
      <div class="hit-head">
        <span class="badge badge--rise">${esc(h.strategy_name)}</span>
        <span class="badge">${esc(h.bucket)}</span>
        <span class="muted num" style="margin-left:auto;font-size:var(--fs-caption)">评分 ${num(h.score, 1)}</span>
      </div>
      <div class="hit-why">${esc(h.reason)}</div>
      ${h.trigger ? `<div class="hit-trig">${esc(h.trigger)}</div>` : ""}
    </div>`).join("");

  const warns = (s.warnings || []).map(w => `
    <div class="hit-item hit-warn">
      <div class="hit-head"><span class="badge badge--fall">预警</span><span class="muted" style="font-size:var(--fs-caption)">${esc(w.strategy)}</span></div>
      <div class="hit-why">${esc(w.reason)}</div>
    </div>`).join("");

  view.innerHTML = `
    <div class="detail-hero">
      <div class="hero">
        <div class="hero-num ${dirText(pct)}">${num(s.close)}</div>
        <div class="hero-side">
          <div class="hero-chg ${dirText(pct)}">${signed(pct)}%</div>
          <div class="hero-cap">最新收盘</div>
        </div>
      </div>
      <div class="detail-tags">
        ${s.industry ? `<span class="badge badge--lg">${esc(s.industry)}</span>` : ""}
        <span class="badge badge--lg">命中 ${(s.hits || []).length} 条</span>
        ${s.score ? `<span class="badge badge--lg badge--gold">综合 ${num(s.score, 1)}</span>` : ""}
      </div>
    </div>

    <div class="card sparkwrap">
      ${sparkSVG(s.spark || [], {
        prevClose: (s.spark && s.spark.length > 1) ? s.spark[s.spark.length - 2] : null,
        prevColor: (s.pct_chg >= 0) ? "var(--rise)" : "var(--fall)",
        endColor: (s.pct_chg >= 0) ? "var(--rise)" : "var(--fall)",
        lineColor: "var(--t2)",
      })}
    </div>
    <div class="hint" style="margin-top:calc(12 * var(--u))">近 40 日收盘走势</div>

    <div class="sec-head"><div class="sec-title">命中依据</div><div class="sec-meta">${(s.hits || []).length} 条</div></div>
    <div class="card">${hits || `<div class="empty" style="padding:calc(40 * var(--u)) 0">今日无命中记录</div>`}</div>

    ${warns ? `<div class="sec-head"><div class="sec-title">风险预警</div><div class="sec-meta">${(s.warnings || []).length} 条</div></div><div class="card">${warns}</div>` : ""}

    <div class="sec-head"><div class="sec-title">数据</div></div>
    <div class="card">
      <div class="kv-grid">
        <div class="kv-cell"><div class="kv-k">流通市值</div><div class="kv-v sm num">${yi(s.circ_mv)}</div></div>
        <div class="kv-cell"><div class="kv-k">综合评分</div><div class="kv-v sm num">${s.score ? num(s.score, 1) : "—"}</div></div>
        <div class="kv-cell"><div class="kv-k">命中策略</div><div class="kv-v sm num">${(s.hits || []).length} 条</div></div>
        <div class="kv-cell"><div class="kv-k">代码</div><div class="kv-v sm num">${esc(s.ts_code || code)}</div></div>
      </div>
    </div>
    <div style="height:calc(80 * var(--u))"></div>`;
}

/* ─────────── 屏幕：策略库 ─────────── */
let stratFilter = "all";

function stratRow(s) {
  const tone = s.bucket === "盘前" ? "badge--gold" : s.bucket === "盘中" ? "badge--cyan" : "";
  return `<div class="row" data-nav="#/strategy/${encodeURIComponent(s.id)}">
    <div class="row-main">
      <div class="row-t">${esc(s.name)}<span class="badge ${tone}">${esc(s.bucket)}</span></div>
      <div class="row-s">${esc(plain(s.card_one || s.note || ""))}</div>
    </div>
    <div class="row-r">
      <div class="row-num">${s.hits_today}${s.hits_capped ? "+" : ""}</div>
      <div class="row-cap">今日命中</div>
    </div>
  </div>`;
}

async function renderStrategies() {
  setTabbar("strategies");
  const o = await getOverview();
  setHeader({ title: "策略库", sub: `${o.strategies.total} 条可执行策略`, action: `<button class="iconbtn" data-action="refresh" aria-label="刷新"><svg class="ic"><use href="#i-sync"/></svg></button>` });
  loadingView();
  const d = await getStrategies();
  const list = d.strategies.filter(s => stratFilter === "all" || s.bucket === stratFilter);
  view.innerHTML = `
    <div class="seg" style="margin-top:calc(32 * var(--u))">
      ${[["all", "全部"], ["盘前", "盘前"], ["盘中", "盘中"], ["盘后", "盘后"]]
        .map(([v, t]) => `<button data-sfilter="${v}" class="${stratFilter === v ? "on" : ""}">${t}</button>`).join("")}
    </div>
    <div class="divided" style="margin-top:calc(24 * var(--u))">${list.map(stratRow).join("")}</div>
    <div class="hint" style="padding:calc(40 * var(--u)) 0;text-align:center">点击任意策略查看参数与今日命中个股</div>`;
}

async function renderStrategy(id) {
  setTabbar(null);
  setHeader({ title: "策略", back: true });
  loadingView();
  let s;
  try { s = await api(`/api/strategy/${encodeURIComponent(id)}`); }
  catch (e) { return errorView(e, `#/strategy/${id}`); }

  const hitTotal = s.hits_total ?? (s.hits || []).length;
  const hitInReco = s.hits_in_reco ?? (s.hits || []).filter(h => h.in_reco).length;
  const hitMark = (n, capped) => `${n}${capped ? "+" : ""}`;
  setHeader({ title: s.name, sub: `${s.bucket} · 今日命中 ${hitMark(hitTotal, s.hits_capped)} 只`, back: true });
  const params = Object.entries(s.params || {})
    .map(([k, v]) => `<span class="chip">${esc(k)} <b>${esc(v)}</b></span>`).join("");
  const hits = (s.hits || []).map(h => `
    <div class="hit-item">
      <div class="hit-head">
        <span class="badge badge--exec num">${esc(h.code)}</span>
        <span class="row-t" style="font-size:var(--fs-h3)">${esc(h.name)}</span>
        ${h.in_reco ? `<span class="badge badge--rise">推荐榜</span>` : ""}
        ${h.risk ? `<span class="badge badge--fall">预警</span>` : ""}
        <span class="muted num" style="margin-left:auto;font-size:var(--fs-label);white-space:nowrap">
          ${num(h.close)} <span class="${dirText(h.pct_chg)}">${signed(h.pct_chg)}%</span>
        </span>
      </div>
      <div class="hit-why">${esc(h.reason)}</div>
      ${h.trigger ? `<div class="hit-trig">${esc(h.trigger)}</div>` : ""}
    </div>`).join("");

  view.innerHTML = `
    <div class="sec-head"><div class="sec-title">说明</div></div>
    <div class="card">
      <div class="kv-k">执行口径</div>
      <div style="font-size:var(--fs-body-lg);line-height:1.65;margin-top:calc(12 * var(--u))">${esc(s.note || "—")}</div>
      ${params ? `<div class="kv-k" style="margin-top:calc(40 * var(--u))">参数</div>
        <div class="chipwrap" style="margin-top:calc(16 * var(--u))">${params}</div>` : ""}
    </div>

    <div class="sec-head"><div class="sec-title">知识点卡</div></div>
    <div class="card">
      <div class="row" data-nav="#/card/${encodeURIComponent(s.card)}" style="min-height:auto;padding:0">
        <div class="row-main">
          <div class="row-t" style="font-size:var(--fs-body-lg)">${esc(s.card)}</div>
          <div class="row-s" style="white-space:normal;line-height:1.6">${esc(plain(s.card_one || s.card_source || ""))}</div>
        </div>
        <span class="row-chev"><svg class="ic"><use href="#i-chev"/></svg></span>
      </div>
      ${(s.card_thresholds || []).length ? `<div class="chipwrap" style="margin-top:calc(24 * var(--u))">
        ${s.card_thresholds.map(t => `<span class="chip">${esc(t)}</span>`).join("")}</div>` : ""}
    </div>

    <div class="sec-head">
      <div class="sec-title">今日命中</div>
      <div class="sec-meta">${hitMark(hitTotal, s.hits_capped)} 只 · ${hitInReco} 只进榜</div>
    </div>
    <div class="card">${hits || `<div class="empty" style="padding:calc(40 * var(--u)) 0">今日无命中</div>`}</div>
    ${hitTotal ? `<div class="hint" style="margin-top:calc(20 * var(--u));padding:0 calc(8 * var(--u))">
      「推荐榜」= 这只股同时进入了今日个股推荐（按时段 top 20）；未进榜的命中仍属于该策略的今日信号，只是综合评分未进前列。
      ${s.hits_capped ? `<br>该策略命中超过 ${hitTotal} 只，此处按策略评分展示前 ${hitTotal} 只。` : ""}
    </div>` : ""}
    <div style="height:calc(80 * var(--u))"></div>`;
}

/* ─────────── 屏幕：知识卡 ─────────── */
const cardFilters = store.cards.filters;
let cardTag = "";

async function fetchCards(reset) {
  const f = store.cards.filters;
  const p = new URLSearchParams({ size: 20 });
  if (reset) store.cards.items = [];
  const page = reset ? 1 : store.cards.page + 1;
  p.set("page", page);
  if (f.q) p.set("q", f.q);
  if (f.bucket) p.set("bucket", f.bucket);
  if (f.tag) p.set("tag", f.tag);
  if (f.exec) p.set("exec", "1");
  const d = await api("/api/cards?" + p);
  store.cards.page = d.page;
  store.cards.total = d.total;
  store.cards.items = store.cards.items.concat(d.items);
  return d;
}

function cardRow(c) {
  return `<div class="row" data-nav="#/card/${encodeURIComponent(c.name)}" style="align-items:flex-start">
    <div class="row-main">
      <div class="row-t" style="white-space:normal;line-height:1.4">${esc(c.name)}</div>
      <div class="row-s" style="white-space:normal;-webkit-line-clamp:2;display:-webkit-box;-webkit-box-orient:vertical;overflow:hidden;line-height:1.55">${esc(plain(c.one || "（无一句话摘要）"))}</div>
      <div class="hitline" style="margin-top:calc(12 * var(--u))">
        <span class="badge ${c.bucket === "盘前" ? "badge--gold" : c.bucket === "盘中" ? "badge--cyan" : ""}">${esc(c.bucket)}</span>
        ${c.executable ? `<span class="badge badge--exec">可执行</span>` : ""}
        ${(c.tags || []).slice(0, 2).map(t => `<span class="badge">${esc(t)}</span>`).join("")}
      </div>
    </div>
    <span class="row-chev" style="margin-top:calc(10 * var(--u))"><svg class="ic"><use href="#i-chev"/></svg></span>
  </div>`;
}

function cardsBody() {
  const f = store.cards.filters;
  const o = store.overview || { kb: { by_tag: {} } };
  const hasMore = store.cards.items.length < store.cards.total;
  return `
    <div class="seg" style="margin-top:calc(24 * var(--u))">
      ${[["", "全部"], ["盘前", "盘前"], ["盘中", "盘中"], ["盘后", "盘后"]]
        .map(([v, t]) => `<button data-cbucket="${v}" class="${f.bucket === v ? "on" : ""}">${t}</button>`).join("")}
    </div>
    <div class="chiprow" style="margin-top:calc(24 * var(--u))">
      <button class="chip ${f.exec ? "on" : ""}" data-cexec>仅看可执行</button>
      ${Object.entries(o.kb.by_tag || {}).sort((a, b) => b[1] - a[1]).map(([t, n]) =>
        `<button class="chip ${f.tag === t ? "on" : ""}" data-ctag="${esc(t)}">${esc(t)} <b>${n}</b></button>`).join("")}
    </div>
    <div class="divided" style="margin-top:calc(8 * var(--u))">${store.cards.items.map(cardRow).join("") || `<div class="empty">没有符合条件的卡片</div>`}</div>
    <div class="hint" style="text-align:center;padding:calc(32 * var(--u)) 0 calc(16 * var(--u))">
      已加载 ${store.cards.items.length} / ${store.cards.total} 张
    </div>
    ${hasMore ? `<button class="btn btn--ghost" data-action="more-cards">加载更多</button>` : ""}
    <div style="height:calc(80 * var(--u))"></div>`;
}

async function renderCards() {
  setTabbar("cards");
  const [o] = await Promise.all([getOverview(), store.cards.items.length ? null : fetchCards(true)]);
  setHeader({ title: "知识卡", sub: `${o.kb.selection_cards} 张选股卡`, action: `<button class="iconbtn" data-action="refresh" aria-label="刷新"><svg class="ic"><use href="#i-sync"/></svg></button>` });
  const f = store.cards.filters;

  view.innerHTML = `
    <label class="search" style="margin-top:calc(24 * var(--u))">
      <svg class="ic"><use href="#i-search"/></svg>
      <input id="cardSearch" type="search" placeholder="搜索知识点：竞价 / 缩量回踩 / 量价背离" value="${esc(f.q)}" enterkeyhint="search">
    </label>
    <div id="cardsBody">${cardsBody()}</div>`;

  if (!store.cards.items.length) {
    await fetchCards(true);
    const box = $("#cardsBody");
    if (box) box.innerHTML = cardsBody();
  }
}

async function renderCard(name) {
  setTabbar(null);
  setHeader({ title: "知识卡", back: true });
  loadingView();
  let c;
  try { c = await api(`/api/card/${encodeURIComponent(name)}`); }
  catch (e) { return errorView(e, `#/card/${encodeURIComponent(name)}`); }

  setHeader({ title: "知识卡", sub: c.bucket || "", back: true });
  const rel = (c.related || []).map(r => `<span class="link" data-nav="#/card/${encodeURIComponent(r)}">${esc(r)}</span>`).join("、");

  view.innerHTML = `
    <div class="detail-tags" style="margin-top:calc(8 * var(--u))">
      <span class="badge badge--lg ${c.bucket === "盘前" ? "badge--gold" : c.bucket === "盘中" ? "badge--cyan" : ""}">${esc(c.bucket)}</span>
      ${c.category ? `<span class="badge badge--lg">${esc(c.category)}</span>` : ""}
      ${c.executable ? `<span class="badge badge--lg badge--exec" data-nav="#/strategy/${encodeURIComponent(c.strategy_id || "")}">已实现策略 ›</span>` : ""}
    </div>
    <div class="md" style="margin-top:calc(24 * var(--u))">
      <h1>${esc(c.name)}</h1>
      ${c.one ? `<blockquote>${mdInline(c.one)}</blockquote>` : ""}
      ${c.body ? md(c.body) : ""}
      ${(c.thresholds || []).length ? `<h3>数值判据</h3>
        <div class="chipwrap">
          ${c.thresholds.map(t => `<span class="chip">${esc(t)}</span>`).join("")}</div>` : ""}
      ${rel ? `<h3>关联知识点</h3><p style="font-size:var(--fs-body)">${rel}</p>` : ""}
      ${c.source ? `<p class="hint" style="margin-top:calc(32 * var(--u))">来源：${esc(plain(c.source))}</p>` : ""}
    </div>
    <div style="height:calc(96 * var(--u))"></div>`;
  view.scrollTop = 0;
}

/* ─────────── 屏幕：本地增量包 ─────────── */
function fmtSize(n) {
  if (!n && n !== 0) return "-";
  if (n > 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + "G";
  if (n > 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + "M";
  if (n > 1024) return (n / 1024).toFixed(0) + "K";
  return n + "B";
}

function packRowHtml(it) {
  const imported = Object.values(it.imported || {});
  const allOk = imported.length && imported.every(Boolean);
  const tables = (it.tables || []).join(", ") || "—";
  let tag = "";
  if (it.dup) tag = `<span class="pk-tag pk-dup">重复 ${esc(it.dup)}</span>`;
  else if (!it.supported) tag = `<span class="pk-tag pk-no">暂不支持</span>`;
  else if (allOk) tag = `<span class="pk-tag pk-ok">已入库</span>`;
  else tag = `<span class="pk-tag pk-pending">未入库</span>`;
  const sub = it.market ? `${it.content} · market=${it.market}` : (it.entries ? `${it.entries} 个 csv` : "");
  return `<div class="pk-row">
    <div class="pk-main">
      <div class="pk-name">${esc(it.name)}</div>
      <div class="pk-sub">${esc(sub)} · ${fmtSize(it.size)}</div>
      <div class="pk-tables num">→ ${esc(tables)}</div>
    </div>
    <div class="pk-state">${tag}</div>
  </div>`;
}

function packDateHtml(d) {
  const cats = (d.cats || []).map(c => `
    <div class="pk-cat">
      <div class="pk-cat-head">${esc(c.label)} <span class="muted">${c.items.length}</span></div>
      ${c.items.map(packRowHtml).join("")}
    </div>`).join("");
  return `<div class="card pk-card" data-date="${esc(d.date)}">
    <div class="pk-head">
      <div>
        <div class="pk-title">${esc(d.date)} <span class="muted">${d.count} 个文件 · ${fmtSize(d.size)}</span></div>
        <div class="pk-sub">可导入 ${d.importable} 项</div>
      </div>
      <div class="pk-actions">
        <button class="btn btn--ghost btn--sm" data-pack-dry="${esc(d.date)}">预演</button>
        <button class="btn btn--primary btn--sm" data-pack-run="${esc(d.date)}">导入</button>
      </div>
    </div>
    ${cats}
  </div>`;
}

async function renderPacks() {
  setHeader({ title: "数据增量", sub: "本地增量包 → DuckDB", action: `<button class="iconbtn" data-action="refresh" aria-label="刷新"><svg class="ic"><use href="#i-sync"/></svg></button>` });
  setTabbar("packs");
  loadingView();
  let d;
  try { d = await api("/api/packs"); }
  catch (e) { return errorView(e, "#/packs"); }
  const dated = (d.dates || []).filter(x => x.date !== "无日期");
  const base = (d.dates || []).find(x => x.date === "无日期");
  view.innerHTML = `
    <div class="card pk-summary">
      <div class="kv-grid">
        <div class="kv-cell"><div class="kv-k">包目录</div><div class="kv-v sm num">${esc(d.dir || "未配置")}</div></div>
        <div class="kv-cell"><div class="kv-k">日期数</div><div class="kv-v num">${dated.length}</div></div>
        <div class="kv-cell"><div class="kv-k">文件数</div><div class="kv-v num">${d.file_count || 0}</div></div>
      </div>
      <div class="hint" style="margin-top:calc(12 * var(--u))">点「预演」只统计不写库；点「导入」会先删当日旧数据再写入（写入前请确认本地库未被同步任务占用）。</div>
    </div>
    <div id="packList">${dated.map(packDateHtml).join("")}</div>
    ${base ? `<div class="sec-head" style="margin-top:calc(40 * var(--u))"><div class="sec-title" style="font-size:var(--fs-h3)">基础数据（无日期）</div></div>${packDateHtml(base)}` : ""}
    <div id="packProgress"></div>`;
}

async function packRun(date, dry) {
  if (!dry && !confirm(`确认把 ${date} 的增量包写入 DuckDB？
会先删除该日已有数据再写入。`)) return;
  const btn = document.querySelector(`[data-pack-${dry ? "dry" : "run"}="${date}"]`);
  if (btn) btn.disabled = true;
  try {
    await postJSON("/api/packs/import", { date, dry });
    packPoll(date, dry);
  } catch (e) {
    toast("启动失败：" + friendlyError(e));
    if (btn) btn.disabled = false;
  }
}

function packPoll(date, dry) {
  const box = $("#packProgress");
  let timer = null;
  const tick = async () => {
    try {
      const st = await api("/api/packs/import/status");
      if (box) {
        const pct = st.total ? Math.round(st.done / st.total * 100) : 0;
        box.innerHTML = `<div class="card pk-progress">
          <div class="pk-title">${dry ? "预演" : "导入"} ${esc(date)} · ${st.done}/${st.total}</div>
          <div class="progress" style="margin-top:calc(12 * var(--u))"><span style="width:${pct}%"></span></div>
          <div class="pk-sub" style="margin-top:calc(10 * var(--u))">${esc(st.current || "")}</div>
          ${st.error ? `<div class="hint" style="color:var(--fall)">${esc(st.error)}</div>` : ""}
        </div>`;
      }
      if (st.running) { timer = setTimeout(tick, 900); return; }
      const ok = (st.results || []).filter(r => r.rows > 0 || (!r.error && !r.dry));
      const errs = (st.results || []).filter(r => r.error);
      toast(`${dry ? "预演完成" : "导入完成"}：${(st.results || []).length} 项${errs.length ? `，失败 ${errs.length}` : ""}`);
      if (box && (st.results || []).length) {
        box.innerHTML = `<div class="card pk-progress">
          <div class="pk-title">${dry ? "预演结果" : "导入结果"}</div>
          ${(st.results || []).slice(0, 80).map(r => `<div class="pk-row"><div class="pk-main">
            <div class="pk-name">${esc(r.file || "")}</div>
            <div class="pk-sub num">${r.error ? "✗ " + esc(r.error) : `→ ${esc(r.table || "")} · ${r.rows ?? 0} 行`}</div>
          </div></div>`).join("")}
          ${errs.length ? `<div class="hint" style="color:var(--fall)">${errs.length} 项失败，见上</div>` : ""}
        </div>`;
      }
      renderPacks();
    } catch (e) {
      timer = setTimeout(tick, 1500);
    }
  };
  tick();
}

/* ─────────── 屏幕：作战手册 ─────────── */
async function renderPlaybook() {
  setTabbar("playbook");
  setHeader({ title: "作战手册", sub: "诉求-01 选股", action: `<button class="iconbtn" data-action="refresh" aria-label="刷新"><svg class="ic"><use href="#i-sync"/></svg></button>` });
  loadingView();
  const pb = await api("/api/playbook");
  view.innerHTML = `<div class="md" style="margin-top:calc(24 * var(--u))">
      ${pb.markdown ? md(pb.markdown) : "<p>未找到作战手册</p>"}
    </div><div style="height:calc(80 * var(--u))"></div>`;
}

/* ─────────── 同步 sheet ─────────── */
/* 手动刷新：打穿客户端 + 服务端缓存，按当前页面就地更新 */
let lastManualRefresh = 0;
async function manualRefresh() {
  const now = Date.now();
  if (now - lastManualRefresh < 4000) { toast("刚刷新过，稍等几秒"); return; }
  lastManualRefresh = now;
  const btn = document.querySelector('.iconbtn[data-action="refresh"]');
  if (btn) btn.classList.add("busy");
  try {
    const { name } = parseHash();
    if (name === "market") {
      await refreshMarket(true);
    } else if (name === "home") {
      await refreshHome(true);
    } else {
      invalidate();
      await route();
    }
    toast("已刷新");
  } catch (e) {
    toast("刷新失败：" + friendlyError(e));
  } finally {
    if (btn) btn.classList.remove("busy");
  }
}

async function openScoringSheet() {
  openSheet(`
    <h3>评分规则</h3>
    <div class="hint" id="scoreStatus">读取中…</div>
    <div id="scoreBody"></div>`);
  try {
    const d = await api("/api/scoring");
    $("#scoreStatus").textContent = d.example || "";
    $("#scoreBody").innerHTML = `
      <div class="card" style="padding:calc(28 * var(--u));margin-top:calc(20 * var(--u))">
        ${d.pipeline.map(x => `<div style="padding:calc(10 * var(--u)) 0;border-top:1px solid var(--line)">
          <div style="font-weight:600;color:var(--t2)">${esc(x.step)}</div>
          <div class="hint" style="margin-top:calc(4 * var(--u))">${esc(x.detail)}</div></div>`).join("")}
      </div>
      <div class="sec-head" style="margin-top:calc(48 * var(--u));margin-bottom:calc(16 * var(--u))">
        <div class="sec-title" style="font-size:var(--fs-h3)">盘中 8 条策略的打分公式</div>
      </div>
      <div class="card" style="padding:calc(24 * var(--u))">
        ${d.intraday.map(x => `<div style="padding:calc(10 * var(--u)) 0;border-top:1px solid var(--line)">
          <div style="font-weight:600;color:var(--t2)">${esc(x.name)}</div>
          <div class="num" style="color:var(--t3);font-size:var(--fs-caption);margin-top:calc(4 * var(--u))">${esc(x.formula)}</div>
        </div>`).join("")}
      </div>
      <div class="hint" style="margin-top:calc(24 * var(--u))">
        说明：分数只在「同一时段、同一批候选」内可比，不是收益率预测；涨停封板、追高只做标注，不直接扣分。
      </div>`;
  } catch (e) {
    $("#scoreStatus").textContent = "读取失败：" + friendlyError(e);
  }
}

function openWatchSheet() {
  openSheet(`
    <h3>蓄势观察 · 为什么不是买点</h3>
    <div class="hint">这组票只回答一个问题：<b>今天还没拉升、且抛压已经枯竭的票有哪些</b>。它不预测拉升。</div>
    <div class="card" style="padding:calc(24 * var(--u));margin-top:calc(20 * var(--u))">
      <div style="padding:calc(8 * var(--u)) 0"><b style="color:var(--t2)">回测结论（2026-06~10，103 天）</b>
        <div class="hint" style="margin-top:calc(4 * var(--u))">
        缩量横盘股未来 30 分钟涨 ≥1.5% 的概率是 2.54%，同波动率普通股是 3.63%（<b>lift 0.70</b>）——
        <b>它不提高拉升概率</b>；但下行（跌 ≥1.5%）概率只有 0.75%，是普通股的 1/4，
        次日（T+1）期望收益比全市场好约 8bp。</div></div>
      <div style="padding:calc(8 * var(--u)) 0;border-top:1px solid var(--line)"><b style="color:var(--t2)">所以怎么用</b>
        <div class="hint" style="margin-top:calc(4 * var(--u))">
        ① 先放进自选，等「放量 + 创日内新高」的启动确认再决定；<br>
        ② 它的价值是把成本从「已经涨 5%」压到「还在均价附近」，不是提高胜率；<br>
        ③ 榜单里的「刚启动」也只是提示，回测同口径 lift 0.96，仍不构成买点。</div></div>
    </div>
    <div class="hint" style="margin-top:calc(20 * var(--u))">数据：本地 5min 全历史事件研究，脚本 src/presurge_test2.py</div>`);
}

async function openSyncSheet() {
  openSheet(`
    <h3>数据与自检</h3>
    <div class="hint" id="syncStatus">读取状态…</div>
    <div class="card" id="syncMeta" style="padding:calc(32 * var(--u))"></div>
    <div class="btnrow">
      <button class="btn btn--ghost" data-action="recalc">重算</button>
      <button class="btn btn--primary" data-action="pull">更新数据</button>
    </div>
    <div class="hint" style="margin-top:calc(28 * var(--u));text-align:center">
      「重算」只重载本地缓存（秒级）；「更新数据」会从 DuckDB 重新抽取（约 25~40s）。
    </div>

    <div class="sec-head" style="margin-top:calc(56 * var(--u));margin-bottom:calc(20 * var(--u))">
      <div class="sec-title" style="font-size:var(--fs-h3)">数据自检</div>
      <div class="sec-meta">首页每个数的来源</div>
    </div>
    <div class="audit" id="auditBox"><div class="hint">读取中…</div></div>`);
  refreshSyncStatus();
  refreshAudit();
}

function syncStatusText(st) {
  if (!st || !st.enabled) return "本地数据看护未开启（--no-watch）";
  if (st.refreshing) return "正在同步源库…";
  const src = st.source_date || "—", cache = st.cache_date || "—";
  if (src !== "—" && cache !== "—" && src > cache) return `源库 ${src} 新于缓存 ${cache}`;
  return `本地数据看护 ${st.interval}s · 缓存 ${cache}（行情请用右上角刷新）`;
}

async function refreshSyncStatus() {
  try {
    const st = await api("/api/status");
    const el = $("#syncStatus");
    if (el) el.textContent = syncStatusText(st);
    const meta = $("#syncMeta");
    if (meta) meta.innerHTML = `
      <div class="kv-grid">
        <div class="kv-cell"><div class="kv-k">源库最新</div><div class="kv-v sm num">${esc(st.source_date || "—")}</div></div>
        <div class="kv-cell"><div class="kv-k">本地缓存</div><div class="kv-v sm num">${esc(st.cache_date || "—")}</div></div>
        <div class="kv-cell"><div class="kv-k">上次检查</div><div class="kv-v sm num">${esc((st.last_check || "—").slice(11))}</div></div>
        <div class="kv-cell"><div class="kv-k">自动 ETL</div><div class="kv-v sm">${st.auto_etl ? "开" : "关"}</div></div>
      </div>`;
  } catch (e) {
    const el = $("#syncStatus");
    if (el) el.textContent = "状态读取失败";
  }
}

async function refreshAudit() {
  const box = $("#auditBox");
  if (!box) return;
  try {
    const d = await api("/api/audit");
    box.innerHTML = (d.rows || []).map(r => `
      <div class="audit-row">
        <div class="audit-k">${esc(r.k)}</div>
        <div class="audit-r">
          <div class="audit-v num">${esc(r.v ?? "—")}</div>
          <div class="audit-n">${esc(r.src || "")}${r.note ? " · " + esc(r.note) : ""}</div>
        </div>
      </div>`).join("");
  } catch (e) {
    box.innerHTML = `<div class="hint">自检读取失败：${esc(e.message)}</div>`;
  }
}

async function doRefresh(full) {
  const btn = $(full ? '[data-action="pull"]' : '[data-action="recalc"]');
  if (btn) { btn.disabled = true; btn.textContent = full ? "拉取中…" : "重算中…"; }
  try {
    const r = await fetch(`/api/refresh${full ? "?full=1" : ""}`).then(x => x.json());
    invalidate();
    closeSheet();
    toast(r.ok ? `已更新 · ${r.elapsed}s` : (r.message || "更新失败"));
    route();
  } catch (e) {
    toast("请求失败：" + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ─────────── 路由 ─────────── */
const state = { bucket: "盘前", intraFilter: "buyable", homeBusy: false, marketBusy: false };

function parseHash() {
  const h = (location.hash || "#/home").replace(/^#\/?/, "");
  const parts = h.split("/");
  return { name: parts[0] || "home", arg: parts[1] ? decodeURIComponent(parts[1]) : "" };
}

let currentKey = "";

async function route() {
  const { name, arg } = parseHash();
  const key = location.hash || "#/home";
  if (key !== currentKey) { currentKey = key; }

  try {
    if (name === "liupan") {
      if (arg === "review") await renderLiupanReview(); else await renderLiupan();
    }
    else if (name === "home") await renderHome();
    else if (name === "market") await renderMarket();
    else if (name === "stock" && arg) await renderStock(arg);
    else if (name === "strategies") await renderStrategies();
    else if (name === "strategy" && arg) await renderStrategy(arg);
    else if (name === "cards") await renderCards();
    else if (name === "card" && arg) await renderCard(arg);
    else if (name === "packs") await renderPacks();
    else if (name === "playbook") await renderPlaybook();
    else { location.hash = "#/home"; return; }
  } catch (e) {
    console.error(e);
    errorView(e);
  }
  if (name !== "card") view.scrollTop = 0;
}

/* ─────────── 事件 ─────────── */
document.addEventListener("click", async (e) => {
  const nav = e.target.closest("[data-nav]");
  if (nav) {
    e.preventDefault();
    const to = nav.dataset.nav;
    if (to && to !== location.hash) location.hash = to;
    return;
  }
  /* 🔴 手动刷新 —— 全站唯一会覆盖 localStorage 的入口 */
  const lpRefresh = e.target.closest("[data-liupan-refresh]");
  if (lpRefresh) {
    await lpLoad(lpRefresh.dataset.liupanRefresh || "pool", true);
    return;
  }
  const lpBtn = e.target.closest("[data-liupan]");
  if (lpBtn) {
    const m = lpBtn.dataset.liupan;
    /* 🔴 点按钮 = 查询（2026-11-04 修·辉哥反馈「点了候选池没刷新数据」）：
       原来这里传 false → 命中 localStorage 就**一个请求都不发**，页面看着原样没变。
       现在：缓存只负责「进页面首屏秒开」，**点按钮一律真查**（pool 带 refresh=1）。 */
    await lpLoad(m, true);
    return;
  }
  const tabBtn = e.target.closest("[data-tab]");
  if (tabBtn) {
    const target = "#/" + tabBtn.dataset.tab;
    if (location.hash === target) view.scrollTop = 0;   // 已在该 tab：回到顶部
    else location.hash = target;
    return;
  }
  if (e.target.closest("[data-close-sheet]")) { closeSheet(); return; }
  if (e.target.closest("[data-back]")) {
    if (history.length > 1) history.back();
    else location.hash = "#/home";
    return;
  }

  const act = e.target.closest("[data-action]")?.dataset.action;
  if (act === "refresh") { await manualRefresh(); return; }
  if (act === "sync") { openSyncSheet(); return; }
  if (act === "scoring") { openScoringSheet(); return; }
  if (act === "watch-info") { openWatchSheet(); return; }
  if (act === "audit") { openSyncSheet(); return; }
  if (act === "recalc") { doRefresh(false); return; }
  if (act === "pull") { doRefresh(true); return; }
  if (act === "more-cards") {
    const btn = e.target.closest("[data-action]");
    btn.disabled = true; btn.textContent = "加载中…";
    await fetchCards(false);
    $("#cardsBody").innerHTML = cardsBody();
    return;
  }

  const buyBtn = e.target.closest("[data-buyable]");
  if (buyBtn) {
    state.intraFilter = buyBtn.dataset.buyable === "0" ? "all" : "buyable";
    await refreshHome();          // 原地切换，不重建页面、不丢滚动
    return;
  }

  const dryBtn = e.target.closest("[data-pack-dry]");
  if (dryBtn) { await packRun(dryBtn.dataset.packDry, true); return; }

  const runBtn = e.target.closest("[data-pack-run]");
  if (runBtn) { await packRun(runBtn.dataset.packRun, false); return; }

  const bucketBtn = e.target.closest("[data-bucket]");
  if (bucketBtn) {
    state.bucket = bucketBtn.dataset.bucket;
    await renderHome();
    view.scrollTop = 0;
    return;
  }

  const sfilter = e.target.closest("[data-sfilter]");
  if (sfilter) { stratFilter = sfilter.dataset.sfilter; await renderStrategies(); return; }

  const cbucket = e.target.closest("[data-cbucket]");
  if (cbucket) {
    store.cards.filters.bucket = cbucket.dataset.cbucket;
    await fetchCards(true);
    $("#cardsBody").innerHTML = cardsBody();
    return;
  }

  const ctag = e.target.closest("[data-ctag]");
  if (ctag) {
    const t = ctag.dataset.ctag;
    store.cards.filters.tag = store.cards.filters.tag === t ? "" : t;
    await fetchCards(true);
    await renderCards();
    return;
  }

  if (e.target.closest("[data-cexec]")) {
    store.cards.filters.exec = !store.cards.filters.exec;
    await fetchCards(true);
    await renderCards();
    return;
  }

  const chipStrat = e.target.closest("[data-strategy]");
  if (chipStrat) { location.hash = `#/strategy/${encodeURIComponent(chipStrat.dataset.strategy)}`; return; }
});

/* 搜索（防抖） */
let searchTimer = null;
document.addEventListener("input", (e) => {
  if (e.target.id !== "cardSearch") return;
  const v = e.target.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    store.cards.filters.q = v;
    await fetchCards(true);
    const box = $("#cardsBody");
    if (box) box.innerHTML = cardsBody();
    const inp = $("#cardSearch");
    if (inp) { inp.focus(); inp.setSelectionRange(v.length, v.length); }
  }, 320);
});

window.addEventListener("hashchange", route);

/* ─────────── 启动 ─────────── */
(async function boot() {
  if (!location.hash) location.hash = "#/home";
  await route();
  // 注册 service worker（本地开发端口也可用）
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
})();
