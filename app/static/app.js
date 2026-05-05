const state = {
  activePortfolioId: null,
  assets: [],
  quoteAsset: "USDT",
  detailSymbol: null,
  txFormOpen: false,
};

const UTC7_TZ = "Asia/Ho_Chi_Minh";

const fmtNum = (n, maxDigits = 6) =>
  new Intl.NumberFormat("en-US", {
    maximumFractionDigits: maxDigits,
  }).format(Number(n || 0));

const fmtQuote = (n) => `${fmtNum(n, 6)} ${state.quoteAsset}`;
const fmtPct = (n) => `${Number(n || 0).toFixed(2)}%`;

function formatDateUTC7(isoText) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: UTC7_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(isoText));
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

function showHomeView() {
  document.getElementById("homeView").classList.remove("hidden");
  document.getElementById("detailView").classList.add("hidden");
}

function showDetailView() {
  document.getElementById("homeView").classList.add("hidden");
  document.getElementById("detailView").classList.remove("hidden");
}

function setTxFormOpen(open) {
  state.txFormOpen = open;
  document.getElementById("txFormWrap").classList.toggle("hidden", !open);
  document.getElementById("txToggleBtn").textContent = open ? "Hide Form" : "+ Add Transaction";
}

function renderTabs(portfolios, activeId) {
  const wrap = document.getElementById("portfolioTabs");
  wrap.innerHTML = "";

  portfolios.forEach((p) => {
    const btn = document.createElement("button");
    btn.className = `tab-btn ${p.id === activeId ? "active" : ""}`;
    btn.textContent = p.name;
    btn.onclick = () => {
      state.activePortfolioId = p.id;
      state.detailSymbol = null;
      showHomeView();
      loadState();
    };
    wrap.appendChild(btn);
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

  topEl.textContent = `${top.coin_name} (${top.symbol}) ${fmtQuote(top.total_pnl)}`;
  topEl.className = `value small ${pnlClass(top.total_pnl)}`;
}

function renderMarketUpdateInfo(data) {
  const el = document.getElementById("marketUpdateInfo");
  if (!el) return;

  const mode = data.price_sync_mode || "polling";
  if (!data.price_last_updated_at) {
    el.textContent = `Market price update: waiting for first ${mode} sync (UTC+7)...`;
    return;
  }

  const dt = formatDateUTC7(data.price_last_updated_at);
  if (mode === "realtime") {
    el.textContent = `Market price update: ${dt} (UTC+7, ${state.quoteAsset}, realtime stream)`;
    return;
  }

  const intervalMin = Math.max(1, Math.round((Number(data.price_sync_interval_seconds || 300) / 60)));
  el.textContent = `Market price update: ${dt} (UTC+7, ${state.quoteAsset}, auto refresh every ${intervalMin} min)`;
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
      `<strong>${h.coin_name}</strong> ${h.symbol}`,
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

    actionsTd.appendChild(detailBtn);
    actionsTd.appendChild(deleteBtn);
    tr.appendChild(actionsTd);

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
    const dt = new Date(tx.tx_time).toLocaleString();
    const price = tx.price_usdt == null ? "-" : fmtQuote(tx.price_usdt);
    li.innerHTML = `
      <strong>${tx.coin_name} (${tx.symbol})</strong><br>
      ${formatType(tx.tx_type)} | qty ${fmtNum(tx.quantity)} | price ${price} | fee ${fmtQuote(tx.fee_usdt)}<br>
      <small>${dt}${tx.note ? ` | ${tx.note}` : ""}</small>
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
  const keyword = document.getElementById("assetSearch").value.trim().toUpperCase();
  if (!keyword) {
    renderAssetOptions(state.assets);
    return;
  }

  const filtered = state.assets.filter((a) => a.symbol.includes(keyword));
  renderAssetOptions(filtered);
}

function hideCoinDetail() {
  state.detailSymbol = null;
  showHomeView();
}

function renderCoinDetail(detail) {
  showDetailView();

  document.getElementById("coinDetailBread").textContent = `Portfolio > ${detail.portfolio_name} > ${detail.coin_name} Transaction Overview`;
  document.getElementById("coinDetailTitle").textContent = `${detail.coin_name} (${detail.symbol})`;

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
    const dt = new Date(tx.tx_time).toLocaleString();

    tr.innerHTML = `
      <td class="${tx.tx_type === "buy" ? "green" : tx.tx_type === "sell" ? "red" : ""}">${formatType(tx.tx_type)}</td>
      <td>${price}</td>
      <td class="${pnlClass(tx.quantity_signed)}">${formatSignedQuantity(tx.quantity_signed)}</td>
      <td>${dt}</td>
      <td>${fmtQuote(tx.fee_usdt)}</td>
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
}

async function loadState() {
  const query = state.activePortfolioId ? `?portfolio_id=${state.activePortfolioId}` : "";
  const res = await fetch(`/api/state${query}`);
  const data = await res.json();

  state.activePortfolioId = data.active_portfolio_id;
  state.quoteAsset = data.quote_asset || state.quoteAsset;

  renderTabs(data.portfolios, data.active_portfolio_id);
  renderSummary(data.summary);
  renderMarketUpdateInfo(data);
  renderHoldings(data.holdings);
  renderTransactions(data.transactions);

  if (state.detailSymbol) {
    const stillExists = data.holdings.some((h) => h.symbol === state.detailSymbol);
    if (!stillExists) {
      hideCoinDetail();
    } else {
      await loadCoinDetail(state.detailSymbol, false);
    }
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

async function loadCoinDetail(symbol, setSymbol = true) {
  if (setSymbol) {
    state.detailSymbol = symbol;
  }
  try {
    const detail = await fetchCoinDetail(symbol);
    renderCoinDetail(detail);
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

  document.getElementById("txForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!state.activePortfolioId) {
      alert("Please create/select a portfolio first.");
      return;
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
      setTxFormOpen(false);
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

  document.getElementById("txToggleBtn").addEventListener("click", () => {
    setTxFormOpen(!state.txFormOpen);
  });

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
}

async function bootstrap() {
  setDefaultTxTime();
  bindForms();
  setTxFormOpen(false);
  showHomeView();
  await loadAssets();
  await loadState();
}

bootstrap();
