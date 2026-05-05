const state = {
  activePortfolioId: null,
  viewMode: "overview",
  assets: [],
  quoteAsset: "USDT",
  detailSymbol: null,
  txFixedSymbol: null,
  performanceCache: {},
  ignoreHashChange: false,
  loadingState: false,
};

const UTC7_TZ = "Asia/Ho_Chi_Minh";
const AUTO_REFRESH_MS = 5000;

const fmtNum = (n, maxDigits = 6) =>
  new Intl.NumberFormat("en-US", {
    maximumFractionDigits: maxDigits,
  }).format(Number(n || 0));

const fmtQuote = (n) => `${fmtNum(n, 2)} ${state.quoteAsset}`;
const fmtPct = (n) => `${Number(n || 0).toFixed(2)}%`;

function formatDateUTC7(isoText) {
  if (!isoText) return "-";
  let normalized = String(isoText).trim();
  // Backward compatibility for older rows/timestamps that were stored without timezone.
  if (!/[zZ]|[+-]\d{2}:\d{2}$/.test(normalized)) {
    normalized = `${normalized}Z`;
  }
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: UTC7_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(normalized));
}

function setDefaultTxTime() {
  const el = document.getElementById("txTime");
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  el.value = now.toISOString().slice(0, 16);
}

function pnlClass(value) {
  return Number(value) >= 0 ? "green" : "red";
}

function formatType(type) {
  if (type === "transfer_in") return "Transfer In";
  if (type === "transfer_out") return "Transfer Out";
  return `${type[0].toUpperCase()}${type.slice(1)}`;
}

function formatSignedQuantity(value) {
  const num = Number(value || 0);
  const prefix = num > 0 ? "+" : "";
  return `${prefix}${fmtNum(num)}`;
}

function normalizeText(value) {
  return String(value || "").trim();
}

function formatPairSymbol(symbol) {
  const sym = normalizeText(symbol).toUpperCase();
  return `${sym}/${state.quoteAsset}`;
}

function formatCoinInline(coinName, symbol) {
  const sym = normalizeText(symbol).toUpperCase();
  const name = normalizeText(coinName);
  const pair = formatPairSymbol(sym);
  if (!name) return pair;
  if (name.toUpperCase() === sym) return pair;
  return `${name} (${pair})`;
}

function formatCoinTitle(coinName, symbol) {
  const sym = normalizeText(symbol).toUpperCase();
  const name = normalizeText(coinName);
  const pair = formatPairSymbol(sym);
  if (!name) return pair;
  if (name.toUpperCase() === sym) return pair;
  return `${name} (${pair})`;
}

function getSelectedAssetSymbol() {
  const select = document.getElementById("assetSelect");
  if (!select || select.selectedIndex < 0) return state.quoteAsset;
  const option = select.options[select.selectedIndex];
  return option?.value || state.quoteAsset;
}

function getFeeCurrencyForForm() {
  const txType = document.getElementById("txType").value;
  const symbol = getSelectedAssetSymbol();
  if (txType === "buy" || txType === "transfer_in") return symbol;
  return state.quoteAsset;
}

function refreshFeeLabel() {
  const label = document.getElementById("feeLabel");
  if (!label) return;
  label.textContent = `Fee (${getFeeCurrencyForForm()})`;
}

function buildHomeHash() {
  return "#/home";
}

function buildDetailHash(portfolioId, symbol) {
  return `#/portfolio/${portfolioId}/coin/${encodeURIComponent(symbol)}`;
}

function setHash(hashValue) {
  if (window.location.hash === hashValue) return;
  state.ignoreHashChange = true;
  window.location.hash = hashValue;
  setTimeout(() => {
    state.ignoreHashChange = false;
  }, 0);
}

function showHomeView() {
  document.getElementById("homeView").classList.remove("hidden");
  document.getElementById("detailView").classList.add("hidden");
}

function showDetailView() {
  document.getElementById("homeView").classList.add("hidden");
  document.getElementById("detailView").classList.remove("hidden");
}

function getAssetBySymbol(symbol) {
  return state.assets.find((a) => a.symbol === symbol);
}

function setAssetBySymbol(symbol) {
  const normalized = String(symbol || "").toUpperCase().trim();
  if (!normalized) return;

  const select = document.getElementById("assetSelect");
  const existing = Array.from(select.options).find((o) => o.value === normalized);
  if (!existing) {
    const asset = getAssetBySymbol(normalized);
    const opt = document.createElement("option");
    opt.value = normalized;
    opt.textContent = `${normalized} / ${state.quoteAsset}`;
    opt.dataset.coinName = asset?.coin_name || normalized;
    select.appendChild(opt);
  }
  select.value = normalized;
}

function resetTxFormFields() {
  document.getElementById("txType").value = "buy";
  document.getElementById("assetSearch").value = "";
  document.getElementById("quantity").value = "";
  document.getElementById("priceUsdt").value = "";
  document.getElementById("feeUsdt").value = "0";
  document.getElementById("note").value = "";
  setDefaultTxTime();
}

function setTxAssetContext(symbol = null) {
  const pickerWrap = document.getElementById("assetPickerWrap");
  const fixedWrap = document.getElementById("fixedAssetWrap");
  const fixedName = document.getElementById("fixedAssetName");

  if (!symbol) {
    state.txFixedSymbol = null;
    pickerWrap.classList.remove("hidden");
    fixedWrap.classList.add("hidden");
    filterAssets();
    refreshFeeLabel();
    return;
  }

  const normalized = String(symbol).toUpperCase().trim();
  state.txFixedSymbol = normalized;
  setAssetBySymbol(normalized);
  const asset = getAssetBySymbol(normalized);
  fixedName.value = `${asset?.coin_name || normalized} (${normalized}/${state.quoteAsset})`;
  pickerWrap.classList.add("hidden");
  fixedWrap.classList.remove("hidden");
  refreshFeeLabel();
}

function openTxModal(symbol = null) {
  if (!state.activePortfolioId) {
    alert("Please create/select a portfolio first.");
    return;
  }

  resetTxFormFields();
  setTxAssetContext(symbol);
  document.getElementById("txModal").classList.remove("hidden");
  document.body.classList.add("modal-open");
  document.getElementById("quantity").focus();
}

function closeTxModal() {
  document.getElementById("txModal").classList.add("hidden");
  document.body.classList.remove("modal-open");
  state.txFixedSymbol = null;
}

function renderMainSectionMode() {
  const isOverview = state.viewMode === "overview";
  document.getElementById("overviewSection").classList.toggle("hidden", !isOverview);
  document.getElementById("portfolioSection").classList.toggle("hidden", isOverview);
}

async function applyRouteFromHash() {
  const hash = window.location.hash || buildHomeHash();
  const match = hash.match(/^#\/portfolio\/(\d+)\/coin\/([^/]+)$/);

  if (match) {
    state.viewMode = "portfolio";
    const portfolioId = Number(match[1]);
    const symbol = decodeURIComponent(match[2]).toUpperCase();
    if (!Number.isNaN(portfolioId)) {
      state.activePortfolioId = portfolioId;
    }
    await loadState();
    await loadCoinDetail(symbol, true, false);
    return;
  }

  state.detailSymbol = null;
  showHomeView();
  renderMainSectionMode();
  await loadState();
}

function renderTabs(portfolios, activeId) {
  const wrap = document.getElementById("portfolioTabs");
  wrap.innerHTML = "";

  const overviewBtn = document.createElement("button");
  overviewBtn.className = `tab-btn ${state.viewMode === "overview" ? "active" : ""}`;
  overviewBtn.textContent = "Overview";
  overviewBtn.onclick = async () => {
    state.viewMode = "overview";
    state.detailSymbol = null;
    showHomeView();
    setHash(buildHomeHash());
    await loadState();
  };
  wrap.appendChild(overviewBtn);

  portfolios.forEach((p) => {
    const btn = document.createElement("button");
    btn.className = `tab-btn ${state.viewMode === "portfolio" && p.id === activeId ? "active" : ""}`;
    btn.textContent = p.name;
    btn.onclick = async () => {
      state.viewMode = "portfolio";
      state.activePortfolioId = p.id;
      state.detailSymbol = null;
      showHomeView();
      setHash(buildHomeHash());
      await loadState();
    };
    wrap.appendChild(btn);
  });
}

function donutSlicePath(cx, cy, rOuter, rInner, startAngle, endAngle) {
  const startOuter = {
    x: cx + rOuter * Math.cos(startAngle),
    y: cy + rOuter * Math.sin(startAngle),
  };
  const endOuter = {
    x: cx + rOuter * Math.cos(endAngle),
    y: cy + rOuter * Math.sin(endAngle),
  };
  const startInner = {
    x: cx + rInner * Math.cos(endAngle),
    y: cy + rInner * Math.sin(endAngle),
  };
  const endInner = {
    x: cx + rInner * Math.cos(startAngle),
    y: cy + rInner * Math.sin(startAngle),
  };
  const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
  return [
    `M ${startOuter.x} ${startOuter.y}`,
    `A ${rOuter} ${rOuter} 0 ${largeArc} 1 ${endOuter.x} ${endOuter.y}`,
    `L ${startInner.x} ${startInner.y}`,
    `A ${rInner} ${rInner} 0 ${largeArc} 0 ${endInner.x} ${endInner.y}`,
    "Z",
  ].join(" ");
}

function renderOverviewDonut(holdings) {
  const rows = (holdings || []).filter((h) => Number(h.market_value || 0) > 0);
  if (!rows.length) {
    return '<div class="muted">No holdings yet.</div>';
  }

  const total = rows.reduce((acc, h) => acc + Number(h.market_value || 0), 0);
  const palette = ["#f08a4b", "#f2d14a", "#e46b93", "#8d7cf7", "#56d6c2", "#f6a3bb", "#7da7ff", "#d79bf4"];

  const cx = 110;
  const cy = 110;
  const rOuter = 82;
  const rInner = 48;
  let start = -Math.PI / 2;

  if (rows.length === 1) {
    const oneLabel = formatCoinInline(rows[0].coin_name, rows[0].symbol);
    return `
      <div class="ov-donut-wrap">
        <svg viewBox="0 0 220 220" class="ov-donut-chart" aria-label="Holdings allocation">
          <circle cx="${cx}" cy="${cy}" r="${(rOuter + rInner) / 2}" fill="none" stroke="#f08a4b" stroke-width="${rOuter - rInner}"></circle>
          <circle cx="${cx}" cy="${cy}" r="${rInner - 2}" fill="#0c1a2c"></circle>
        </svg>
        <ul class="ov-legend">
          <li>
            <span class="dot" style="background:#f08a4b"></span>
            <span>${oneLabel}</span>
            <span>100.00%</span>
          </li>
        </ul>
      </div>
    `;
  }

  const slices = rows
    .slice(0, 8)
    .map((h, idx) => {
      const pct = total > 0 ? Number(h.market_value || 0) / total : 0;
      const end = start + pct * Math.PI * 2;
      const path = donutSlicePath(cx, cy, rOuter, rInner, start, end);
      const color = palette[idx % palette.length];
      start = end;
      return {
        path,
        color,
        label: formatCoinInline(h.coin_name, h.symbol),
        pct: pct * 100,
      };
    });

  const legend = slices
    .map(
      (s) => `
        <li>
          <span class="dot" style="background:${s.color}"></span>
          <span>${s.label}</span>
          <span>${fmtPct(s.pct)}</span>
        </li>
      `
    )
    .join("");

  const svgPaths = slices.map((s) => `<path d="${s.path}" fill="${s.color}"></path>`).join("");
  return `
    <div class="ov-donut-wrap">
      <svg viewBox="0 0 220 220" class="ov-donut-chart" aria-label="Holdings allocation">
        ${svgPaths}
        <circle cx="${cx}" cy="${cy}" r="${rInner - 2}" fill="#0c1a2c"></circle>
      </svg>
      <ul class="ov-legend">${legend}</ul>
    </div>
  `;
}

const PERF_PERIODS = {
  "24H": 24 * 60 * 60 * 1000,
  "7D": 7 * 24 * 60 * 60 * 1000,
  "1M": 30 * 24 * 60 * 60 * 1000,
  "3M": 90 * 24 * 60 * 60 * 1000,
  "1Y": 365 * 24 * 60 * 60 * 1000,
};

function parsePerfPoints(performancePoints) {
  return (performancePoints || [])
    .map((p) => ({
      ts: new Date(p.ts).getTime(),
      value: Number(p.value_usdt || 0),
    }))
    .filter((p) => Number.isFinite(p.ts) && Number.isFinite(p.value))
    .sort((a, b) => a.ts - b.ts);
}

function filterPerfByPeriod(points, periodKey) {
  if (!points.length) return [];
  const now = points[points.length - 1].ts;
  const from = now - (PERF_PERIODS[periodKey] || PERF_PERIODS["24H"]);
  const filtered = points.filter((p) => p.ts >= from);
  if (!filtered.length) {
    return [points[0], points[points.length - 1]];
  }
  const before = [...points].reverse().find((p) => p.ts < from);
  if (before) filtered.unshift(before);
  return filtered;
}

async function fetchPortfolioPerformance(portfolioId, periodKey) {
  if (!state.performanceCache[portfolioId]) {
    state.performanceCache[portfolioId] = {};
  }
  if (state.performanceCache[portfolioId][periodKey]) {
    return state.performanceCache[portfolioId][periodKey];
  }

  const res = await fetch(`/api/portfolios/${portfolioId}/performance?period=${encodeURIComponent(periodKey)}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to load performance");
  }
  const data = await res.json();
  const points = data.points || [];
  state.performanceCache[portfolioId][periodKey] = points;
  return points;
}

function renderOverviewPerformanceChart(performancePoints, periodKey = "24H") {
  const source = parsePerfPoints(performancePoints);
  const pills = Object.keys(PERF_PERIODS)
    .map((k) => `<button type="button" class="pill ${k === periodKey ? "active" : ""}" data-period="${k}">${k}</button>`)
    .join("");
  const leftLabelMap = {
    "24H": "24h ago",
    "7D": "7d ago",
    "1M": "1m ago",
    "3M": "3m ago",
    "1Y": "1y ago",
  };

  if (!source.length) {
    return `
      <div class="ov-perf-head">
        <div class="ov-pills">${pills}</div>
        <span class="muted">No data</span>
      </div>
      <div class="ov-line-empty muted">No performance data yet.</div>
      <div class="ov-axis">
        <span>${leftLabelMap[periodKey] || "Start"}</span>
        <span>Now</span>
      </div>
    `;
  }

  const picked = filterPerfByPeriod(source, periodKey);
  if (picked.length < 2) {
    const single = picked[0] || source[0];
    picked.push({ ts: Date.now(), value: single.value });
  }

  const values = picked.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min) * 0.12 || 1;
  const lo = min - pad;
  const hi = max + pad;
  const w = 620;
  const h = 210;

  const firstTs = picked[0].ts;
  const lastTs = picked[picked.length - 1].ts;
  const tsSpan = Math.max(1, lastTs - firstTs);
  const xy = picked.map((p) => {
    const x = ((p.ts - firstTs) / tsSpan) * (w - 24) + 12;
    const y = h - ((p.value - lo) / (hi - lo)) * (h - 24) - 12;
    return { x, y };
  });

  const line = xy.map((p, idx) => `${idx === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
  const area = `${line} L ${xy[xy.length - 1].x} ${h - 8} L ${xy[0].x} ${h - 8} Z`;
  const firstValue = values[0];
  const lastValue = values[values.length - 1];
  const pnl = lastValue - firstValue;
  const pnlPct = firstValue > 0 ? (pnl / firstValue) * 100 : 0;

  return `
    <div class="ov-perf-head">
      <div class="ov-pills">${pills}</div>
      <span class="muted ${pnlClass(pnl)}">${fmtQuote(pnl)} (${fmtPct(pnlPct)})</span>
    </div>
    <svg viewBox="0 0 ${w} ${h}" class="ov-line-chart" aria-label="Portfolio performance">
      <path d="${area}" class="ov-area"></path>
      <path d="${line}" class="ov-line"></path>
    </svg>
    <div class="ov-axis">
      <span>${leftLabelMap[periodKey] || "Start"}</span>
      <span>Now</span>
    </div>
  `;
}

function renderOverview(overview) {
  const summary = overview?.summary || {
    current_balance: 0,
    portfolio_change_24h: 0,
    total_profit_loss: 0,
    total_profit_loss_pct: 0,
    top_performer: null,
  };

  document.getElementById("ovCurrentBalance").textContent = fmtQuote(summary.current_balance);
  const changeEl = document.getElementById("ovChange24h");
  changeEl.textContent = fmtQuote(summary.portfolio_change_24h);
  changeEl.className = `value ${pnlClass(summary.portfolio_change_24h)}`;

  const pnlEl = document.getElementById("ovTotalPnl");
  pnlEl.textContent = `${fmtQuote(summary.total_profit_loss)} (${fmtPct(summary.total_profit_loss_pct)})`;
  pnlEl.className = `value ${pnlClass(summary.total_profit_loss)}`;

  const topEl = document.getElementById("ovTopPerformer");
  if (!summary.top_performer) {
    topEl.textContent = "-";
    topEl.className = "value small";
  } else {
    topEl.textContent = `${formatCoinTitle(summary.top_performer.coin_name, summary.top_performer.symbol)} ${fmtQuote(summary.top_performer.total_pnl)}`;
    topEl.className = `value small ${pnlClass(summary.top_performer.total_pnl)}`;
  }

  const list = document.getElementById("overviewPortfolioList");
  list.innerHTML = "";

  (overview?.portfolios || []).forEach((item) => {
    const panel = document.createElement("article");
    panel.className = "panel overview-portfolio";

    const head = document.createElement("div");
    head.className = "panel-head";
    head.innerHTML = `<h2>${item.portfolio_name}</h2>`;
    const openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.className = "btn ghost small";
    openBtn.textContent = "Open Portfolio";
    openBtn.onclick = async () => {
      state.viewMode = "portfolio";
      state.activePortfolioId = item.portfolio_id;
      await loadState();
    };
    head.appendChild(openBtn);
    panel.appendChild(head);

    const summaryGrid = document.createElement("div");
    summaryGrid.className = "summary-grid overview-grid";
    summaryGrid.innerHTML = `
      <article class="card">
        <h3>Balance</h3>
        <p class="value small">${fmtQuote(item.summary.current_balance)}</p>
      </article>
      <article class="card">
        <h3>24h Change</h3>
        <p class="value small ${pnlClass(item.summary.portfolio_change_24h)}">${fmtQuote(item.summary.portfolio_change_24h)}</p>
      </article>
      <article class="card">
        <h3>Total PnL</h3>
        <p class="value small ${pnlClass(item.summary.total_profit_loss)}">${fmtQuote(item.summary.total_profit_loss)} (${fmtPct(item.summary.total_profit_loss_pct)})</p>
      </article>
      <article class="card">
        <h3>Top Performer</h3>
        <p class="value small ${item.summary.top_performer ? pnlClass(item.summary.top_performer.total_pnl) : ""}">
          ${item.summary.top_performer ? `${formatCoinTitle(item.summary.top_performer.coin_name, item.summary.top_performer.symbol)} ${fmtQuote(item.summary.top_performer.total_pnl)}` : "-"}
        </p>
      </article>
    `;
    panel.appendChild(summaryGrid);

    const chartGrid = document.createElement("div");
    chartGrid.className = "overview-charts";
    chartGrid.innerHTML = `
      <article class="card chart-card">
        <h3>Total Holdings</h3>
        ${renderOverviewDonut(item.holdings)}
      </article>
      <article class="card chart-card perf-card">
        <h3>Total Performance</h3>
        <div class="perf-content">${renderOverviewPerformanceChart(item.performance_points, "24H")}</div>
      </article>
    `;
    panel.appendChild(chartGrid);

    const perfCard = chartGrid.querySelector(".perf-card");
    if (perfCard) {
      const content = perfCard.querySelector(".perf-content");
      const bindPillActions = () => {
        perfCard.querySelectorAll(".pill").forEach((btn) => {
          btn.addEventListener("click", async () => {
            const period = btn.dataset.period || "24H";
            if (!content) return;
            content.innerHTML = '<div class="muted">Loading performance...</div>';
            try {
              const points = await fetchPortfolioPerformance(item.portfolio_id, period);
              content.innerHTML = renderOverviewPerformanceChart(points, period);
            } catch (err) {
              content.innerHTML = renderOverviewPerformanceChart(item.performance_points || [], period);
            }
            bindPillActions();
          });
        });
      };

      bindPillActions();

      (async () => {
        if (!content) return;
        content.innerHTML = '<div class="muted">Loading performance...</div>';
        try {
          const points = await fetchPortfolioPerformance(item.portfolio_id, "24H");
          content.innerHTML = renderOverviewPerformanceChart(points, "24H");
        } catch (err) {
          content.innerHTML = renderOverviewPerformanceChart(item.performance_points || [], "24H");
        }
        bindPillActions();
      })();
    }

    const tableWrap = document.createElement("div");
    tableWrap.className = "table-wrap";
    const rows = (item.holdings || [])
      .slice(0, 8)
      .map(
        (h) => `
          <tr>
            <td>${formatCoinInline(h.coin_name, h.symbol)}</td>
            <td>${fmtNum(h.quantity)}</td>
            <td>${fmtQuote(h.current_price)}</td>
            <td class="${pnlClass(h.price_change_24h)}">${fmtPct(h.price_change_24h)}</td>
            <td>${fmtQuote(h.market_value)}</td>
            <td class="${pnlClass(h.total_pnl)}">${fmtQuote(h.total_pnl)} (${fmtPct(h.pnl_pct)})</td>
          </tr>
        `
      )
      .join("");
    tableWrap.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Coin</th>
            <th>Qty</th>
            <th>Price</th>
            <th>24h</th>
            <th>Holdings</th>
            <th>PnL</th>
          </tr>
        </thead>
        <tbody>${rows || '<tr><td colspan="6" class="muted">No holdings yet.</td></tr>'}</tbody>
      </table>
    `;
    panel.appendChild(tableWrap);
    list.appendChild(panel);
  });
}

function renderSummary(summary) {
  document.getElementById("currentBalance").textContent = fmtQuote(summary.current_balance);

  const change24 = document.getElementById("change24h");
  change24.textContent = fmtQuote(summary.portfolio_change_24h);
  change24.className = `value ${pnlClass(summary.portfolio_change_24h)}`;

  const totalPnl = document.getElementById("totalPnl");
  totalPnl.textContent = `${fmtQuote(summary.total_profit_loss)} (${fmtPct(summary.total_profit_loss_pct)})`;
  totalPnl.className = `value ${pnlClass(summary.total_profit_loss)}`;

  const top = summary.top_performer;
  const topEl = document.getElementById("topPerformer");
  if (!top) {
    topEl.textContent = "-";
    return;
  }

  topEl.textContent = `${formatCoinTitle(top.coin_name, top.symbol)} ${fmtQuote(top.total_pnl)}`;
  topEl.className = `value small ${pnlClass(top.total_pnl)}`;
}

function renderMarketUpdateInfo(data) {
  const el = document.getElementById("marketUpdateInfo");
  if (!el) return;

  const mode = data.price_sync_mode || "polling";
  if (!data.price_last_updated_at) {
    el.textContent = `Market price update: waiting for first ${mode} sync...`;
    return;
  }

  const dt = data.price_last_updated_at_utc7 || formatDateUTC7(data.price_last_updated_at);
  el.textContent = `Market price update: ${dt}`;
}

function renderHoldings(holdings) {
  document.getElementById("holdingCount").textContent = `${holdings.length} assets`;
  const body = document.getElementById("holdingsBody");
  body.innerHTML = "";

  if (!holdings.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = '<td colspan="7" class="muted">No holdings yet.</td>';
    body.appendChild(tr);
    return;
  }

  holdings.forEach((h) => {
    const tr = document.createElement("tr");

    const cells = [
      `<button type="button" class="coin-link">${formatCoinInline(h.coin_name, h.symbol)}</button>`,
      fmtNum(h.quantity),
      fmtQuote(h.current_price),
      `<span class="${pnlClass(h.price_change_24h)}">${fmtPct(h.price_change_24h)}</span>`,
      fmtQuote(h.market_value),
      `<span class="${pnlClass(h.total_pnl)}">${fmtQuote(h.total_pnl)} (${fmtPct(h.pnl_pct)})</span>`,
    ];

    cells.forEach((html) => {
      const td = document.createElement("td");
      td.innerHTML = html;
      tr.appendChild(td);
    });

    const actionsTd = document.createElement("td");
    actionsTd.className = "actions-cell";

    const addBtn = document.createElement("button");
    addBtn.className = "btn primary small";
    addBtn.textContent = "+ Tx";
    addBtn.onclick = () => openTxModal(h.symbol);

    const detailBtn = document.createElement("button");
    detailBtn.className = "btn ghost small";
    detailBtn.textContent = "Detail";
    detailBtn.onclick = () => loadCoinDetail(h.symbol);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn danger small";
    deleteBtn.textContent = "Delete";
    deleteBtn.onclick = async () => {
      const ok = confirm(`Delete ${h.symbol} from this portfolio? This removes all transactions for this coin.`);
      if (!ok) return;
      try {
        await deleteCoinFromPortfolio(h.symbol);
        if (state.detailSymbol === h.symbol) {
          hideCoinDetail();
        }
        await loadState();
      } catch (err) {
        alert(err.message);
      }
    };

    actionsTd.appendChild(addBtn);
    actionsTd.appendChild(detailBtn);
    actionsTd.appendChild(deleteBtn);
    tr.appendChild(actionsTd);

    const coinButton = tr.querySelector(".coin-link");
    if (coinButton) {
      coinButton.addEventListener("click", () => loadCoinDetail(h.symbol));
    }

    body.appendChild(tr);
  });
}

function renderTransactions(rows) {
  const ul = document.getElementById("recentTx");
  ul.innerHTML = "";

  if (!rows.length) {
    ul.innerHTML = "<li>No transactions yet.</li>";
    return;
  }

  rows.slice(0, 20).forEach((tx) => {
    const li = document.createElement("li");
    const dt = formatDateUTC7(tx.tx_time);
    const price = tx.price_usdt == null ? "-" : fmtQuote(tx.price_usdt);
    const feeCurrency = tx.fee_currency || state.quoteAsset;
    const feeText = `${fmtNum(tx.fee_usdt, 10)} ${feeCurrency}`;
    li.innerHTML = `
      <strong>${formatCoinTitle(tx.coin_name, tx.symbol)}</strong><br>
      ${formatType(tx.tx_type)} | qty ${fmtNum(tx.quantity)} | price ${price} | fee ${feeText}<br>
      <small>${dt} (UTC+7)${tx.note ? ` | ${tx.note}` : ""}</small>
    `;
    ul.appendChild(li);
  });
}

function renderAssetOptions(filtered) {
  const select = document.getElementById("assetSelect");
  const currentValue = select.value;
  select.innerHTML = "";

  filtered.forEach((asset) => {
    const opt = document.createElement("option");
    opt.value = asset.symbol;
    opt.textContent = `${asset.symbol} / ${state.quoteAsset}`;
    opt.dataset.coinName = asset.coin_name || asset.symbol;
    select.appendChild(opt);
  });

  if (currentValue && filtered.some((a) => a.symbol === currentValue)) {
    select.value = currentValue;
  }
}

function filterAssets() {
  if (state.txFixedSymbol) {
    return;
  }

  const keyword = document.getElementById("assetSearch").value.trim().toUpperCase();
  if (!keyword) {
    renderAssetOptions(state.assets);
    refreshFeeLabel();
    return;
  }

  const filtered = state.assets.filter((a) => a.symbol.includes(keyword));
  renderAssetOptions(filtered);
  refreshFeeLabel();
}

function hideCoinDetail(syncHash = true) {
  state.detailSymbol = null;
  showHomeView();
  if (syncHash) {
    setHash(buildHomeHash());
  }
}

function renderCoinDetail(detail) {
  showDetailView();

  const coinLabelTitle = formatCoinTitle(detail.coin_name, detail.symbol);
  document.getElementById("coinDetailBread").textContent = `Portfolio > ${detail.portfolio_name} > ${coinLabelTitle} Transaction Overview`;
  document.getElementById("coinDetailTitle").textContent = coinLabelTitle;

  const priceEl = document.getElementById("coinDetailPrice");
  priceEl.textContent = `${fmtQuote(detail.price_usdt)} ${fmtPct(detail.price_change_24h)}`;
  priceEl.className = `value small ${pnlClass(detail.price_change_24h)}`;

  document.getElementById("detailHoldingsValue").textContent = fmtQuote(detail.holdings_value);
  document.getElementById("detailHoldingsQty").textContent = `${fmtNum(detail.holdings_quantity)} ${detail.symbol}`;
  document.getElementById("detailTotalCost").textContent = fmtQuote(detail.total_cost);
  document.getElementById("detailAvgCost").textContent = fmtQuote(detail.average_net_cost);

  const totalPnl = document.getElementById("detailTotalPnl");
  totalPnl.textContent = fmtQuote(detail.total_profit_loss);
  totalPnl.className = `value small ${pnlClass(detail.total_profit_loss)}`;

  const body = document.getElementById("coinTxBody");
  body.innerHTML = "";

  detail.transactions.forEach((tx) => {
    const tr = document.createElement("tr");
    const price = tx.price_usdt == null ? "-" : fmtQuote(tx.price_usdt);
    const cost = tx.cost_usdt == null ? "-" : fmtQuote(tx.cost_usdt);
    const proceeds = tx.proceeds_usdt == null ? "-" : fmtQuote(tx.proceeds_usdt);
    const pnl = tx.pnl_usdt == null ? "-" : fmtQuote(tx.pnl_usdt);
    const dt = formatDateUTC7(tx.tx_time);
    const feeCurrency = tx.fee_currency || state.quoteAsset;
    const feeText = `${fmtNum(tx.fee_usdt, 10)} ${feeCurrency}`;

    tr.innerHTML = `
      <td class="${tx.tx_type === "buy" ? "green" : tx.tx_type === "sell" ? "red" : ""}">${formatType(tx.tx_type)}</td>
      <td>${price}</td>
      <td class="${pnlClass(tx.quantity_signed)}">${formatSignedQuantity(tx.quantity_signed)}</td>
      <td>${dt}</td>
      <td>${feeText}</td>
      <td>${cost}</td>
      <td>${proceeds}</td>
      <td class="${tx.pnl_usdt == null ? "" : pnlClass(tx.pnl_usdt)}">${pnl}</td>
      <td>${tx.note || "-"}</td>
    `;
    body.appendChild(tr);
  });
}

async function loadAssets() {
  const res = await fetch("/api/assets");
  const data = await res.json();
  state.quoteAsset = data.quote_asset || "USDT";
  state.assets = data.assets || [];
  renderAssetOptions(state.assets);
  refreshFeeLabel();
}

async function loadState() {
  if (state.loadingState) return;
  state.loadingState = true;
  const params = new URLSearchParams();
  if (state.activePortfolioId) params.set("portfolio_id", String(state.activePortfolioId));
  if (state.viewMode === "overview") params.set("include_overview", "1");
  const query = params.toString() ? `?${params.toString()}` : "";
  try {
    const res = await fetch(`/api/state${query}`);
    const data = await res.json();

    state.activePortfolioId = data.active_portfolio_id;
    state.quoteAsset = data.quote_asset || state.quoteAsset;

    renderTabs(data.portfolios, data.active_portfolio_id);
    renderSummary(data.summary);
    renderMarketUpdateInfo(data);
    renderHoldings(data.holdings);
    renderTransactions(data.transactions);
    renderOverview(data.overview);
    renderMainSectionMode();

    if (state.detailSymbol) {
      const stillExists = data.holdings.some((h) => h.symbol === state.detailSymbol);
      if (!stillExists) {
        hideCoinDetail();
      } else {
        await loadCoinDetail(state.detailSymbol, false, false);
      }
    }
  } finally {
    state.loadingState = false;
  }
}

async function createPortfolio(name) {
  const res = await fetch("/api/portfolios", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to create portfolio");
  }
  const row = await res.json();
  state.activePortfolioId = row.id;
}

async function createTransaction(payload) {
  const res = await fetch("/api/transactions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to add transaction");
  }
}

async function deleteCoinFromPortfolio(symbol) {
  const res = await fetch(`/api/coins/${encodeURIComponent(symbol)}?portfolio_id=${state.activePortfolioId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to delete coin");
  }
}

async function fetchCoinDetail(symbol) {
  const res = await fetch(`/api/coins/${encodeURIComponent(symbol)}/detail?portfolio_id=${state.activePortfolioId}`);
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to load coin detail");
  }
  return res.json();
}

async function loadCoinDetail(symbol, setSymbol = true, syncHash = true) {
  if (setSymbol) {
    state.detailSymbol = symbol;
  }
  try {
    const detail = await fetchCoinDetail(symbol);
    renderCoinDetail(detail);
    if (syncHash && state.activePortfolioId != null) {
      setHash(buildDetailHash(state.activePortfolioId, symbol));
    }
  } catch (err) {
    alert(err.message);
    hideCoinDetail();
  }
}

async function syncNow() {
  const res = await fetch("/api/prices/sync-now", { method: "POST" });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to sync prices now");
  }
  return res.json();
}

function bindForms() {
  document.getElementById("portfolioForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const nameEl = document.getElementById("portfolioName");
    const name = nameEl.value.trim();
    if (!name) return;
    try {
      await createPortfolio(name);
      nameEl.value = "";
      hideCoinDetail();
      await loadState();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("assetSearch").addEventListener("input", filterAssets);
  document.getElementById("assetSelect").addEventListener("change", refreshFeeLabel);
  document.getElementById("txType").addEventListener("change", refreshFeeLabel);

  document.getElementById("txForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.activePortfolioId) {
      alert("Please create/select a portfolio first.");
      return;
    }

    if (state.txFixedSymbol) {
      setAssetBySymbol(state.txFixedSymbol);
    }

    const select = document.getElementById("assetSelect");
    const selectedOption = select.options[select.selectedIndex];
    if (!selectedOption) {
      alert("Please select an asset from Binance list.");
      return;
    }

    const txType = document.getElementById("txType").value;
    const dtLocal = document.getElementById("txTime").value;
    const txIso = new Date(dtLocal).toISOString();

    const payload = {
      portfolio_id: state.activePortfolioId,
      tx_type: txType,
      symbol: selectedOption.value,
      coin_name: selectedOption.dataset.coinName || selectedOption.value,
      quantity: document.getElementById("quantity").value,
      price_usdt: document.getElementById("priceUsdt").value || null,
      fee_usdt: document.getElementById("feeUsdt").value || "0",
      fee_currency: getFeeCurrencyForForm(),
      note: document.getElementById("note").value.trim(),
      tx_time: txIso,
    };

    try {
      await createTransaction(payload);
      document.getElementById("quantity").value = "";
      document.getElementById("priceUsdt").value = "";
      document.getElementById("feeUsdt").value = "0";
      document.getElementById("note").value = "";
      setDefaultTxTime();
      closeTxModal();
      await loadState();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("refreshBtn").addEventListener("click", async () => {
    await loadAssets();
    await loadState();
  });

  document.getElementById("syncNowBtn").addEventListener("click", async () => {
    const btn = document.getElementById("syncNowBtn");
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Updating...";
    try {
      await syncNow();
      await loadState();
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  });

  document.getElementById("openTxModalBtn").addEventListener("click", () => openTxModal(null));

  document.getElementById("detailAddTxBtn").addEventListener("click", () => openTxModal(state.detailSymbol));

  document.getElementById("txModalClose").addEventListener("click", closeTxModal);
  document.getElementById("txModalBackdrop").addEventListener("click", closeTxModal);

  document.getElementById("detailBackBtn").addEventListener("click", hideCoinDetail);

  document.getElementById("detailDeleteBtn").addEventListener("click", async () => {
    if (!state.detailSymbol) return;
    const ok = confirm(`Delete ${state.detailSymbol} from this portfolio? This removes all transactions for this coin.`);
    if (!ok) return;
    try {
      await deleteCoinFromPortfolio(state.detailSymbol);
      hideCoinDetail();
      await loadState();
    } catch (err) {
      alert(err.message);
    }
  });

  window.addEventListener("hashchange", async () => {
    if (state.ignoreHashChange) return;
    await applyRouteFromHash();
  });

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeTxModal();
    }
  });

  setInterval(async () => {
    await loadState();
  }, AUTO_REFRESH_MS);
}

async function bootstrap() {
  setDefaultTxTime();
  bindForms();
  renderMainSectionMode();
  showHomeView();
  await loadAssets();
  await applyRouteFromHash();
}

bootstrap();
