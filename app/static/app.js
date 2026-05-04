const state = {
  activePortfolioId: null,
  assets: [],
};

const fmtUSD = (n) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(Number(n || 0));

const fmtNum = (n) =>
  new Intl.NumberFormat("en-US", {
    maximumFractionDigits: 6,
  }).format(Number(n || 0));

function setDefaultTxTime() {
  const el = document.getElementById("txTime");
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  el.value = now.toISOString().slice(0, 16);
}

function pnlClass(value) {
  return Number(value) >= 0 ? "green" : "red";
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
      loadState();
    };
    wrap.appendChild(btn);
  });
}

function renderSummary(summary) {
  document.getElementById("currentBalance").textContent = fmtUSD(summary.current_balance);

  const change24 = document.getElementById("change24h");
  change24.textContent = fmtUSD(summary.portfolio_change_24h);
  change24.className = `value ${pnlClass(summary.portfolio_change_24h)}`;

  const totalPnl = document.getElementById("totalPnl");
  const pnlTxt = `${fmtUSD(summary.total_profit_loss)} (${Number(summary.total_profit_loss_pct || 0).toFixed(2)}%)`;
  totalPnl.textContent = pnlTxt;
  totalPnl.className = `value ${pnlClass(summary.total_profit_loss)}`;

  const top = summary.top_performer;
  const topEl = document.getElementById("topPerformer");
  if (!top) {
    topEl.textContent = "-";
    return;
  }

  topEl.textContent = `${top.coin_name} (${top.symbol}) ${fmtUSD(top.total_pnl)}`;
  topEl.className = `value small ${pnlClass(top.total_pnl)}`;
}

function renderHoldings(holdings) {
  document.getElementById("holdingCount").textContent = `${holdings.length} assets`;
  const body = document.getElementById("holdingsBody");
  body.innerHTML = "";

  holdings.forEach((h) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${h.coin_name}</strong> ${h.symbol}</td>
      <td>${fmtNum(h.quantity)}</td>
      <td>${fmtUSD(h.current_price)}</td>
      <td class="${pnlClass(h.price_change_24h)}">${Number(h.price_change_24h).toFixed(2)}%</td>
      <td>${fmtUSD(h.market_value)}</td>
      <td class="${pnlClass(h.total_pnl)}">${fmtUSD(h.total_pnl)} (${Number(h.pnl_pct).toFixed(2)}%)</td>
    `;
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
    const price = tx.price_usd == null ? "-" : fmtUSD(tx.price_usd);
    li.innerHTML = `
      <strong>${tx.coin_name} (${tx.symbol})</strong><br>
      ${tx.tx_type} | qty ${fmtNum(tx.quantity)} | price ${price} | fee ${fmtUSD(tx.fee_usd)}<br>
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
    opt.textContent = `${asset.symbol} / USDT`;
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

async function loadAssets() {
  const res = await fetch("/api/assets");
  const data = await res.json();
  state.assets = data.assets || [];
  renderAssetOptions(state.assets);
}

async function loadState() {
  const query = state.activePortfolioId ? `?portfolio_id=${state.activePortfolioId}` : "";
  const res = await fetch(`/api/state${query}`);
  const data = await res.json();

  state.activePortfolioId = data.active_portfolio_id;
  renderTabs(data.portfolios, data.active_portfolio_id);
  renderSummary(data.summary);
  renderHoldings(data.holdings);
  renderTransactions(data.transactions);
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

function bindForms() {
  document.getElementById("portfolioForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const nameEl = document.getElementById("portfolioName");
    const name = nameEl.value.trim();
    if (!name) return;
    try {
      await createPortfolio(name);
      nameEl.value = "";
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
      price_usd: document.getElementById("priceUsd").value || null,
      fee_usd: document.getElementById("feeUsd").value || "0",
      note: document.getElementById("note").value.trim(),
      tx_time: txIso,
    };

    try {
      await createTransaction(payload);
      document.getElementById("quantity").value = "";
      document.getElementById("priceUsd").value = "";
      document.getElementById("note").value = "";
      setDefaultTxTime();
      await loadState();
    } catch (err) {
      alert(err.message);
    }
  });

  document.getElementById("refreshBtn").addEventListener("click", async () => {
    await loadAssets();
    await loadState();
  });
}

async function bootstrap() {
  setDefaultTxTime();
  bindForms();
  await loadAssets();
  await loadState();
}

bootstrap();
