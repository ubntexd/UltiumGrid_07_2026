const API = window.location.origin.includes("3000")
  ? "http://localhost:8000"
  : "";

const CONFIG_FIELDS = [
  ["symbol", "Symbole"],
  ["capital_usdt", "Capital USDT"],
  ["num_levels", "Paliers"],
  ["step_pct", "Pas %"],
  ["cycle_trigger_usd", "Seuil cycle USD"],
  ["cut_level_1", "Palier coupe 1"],
  ["cut_pct_1", "Coupe 1 %"],
  ["cut_level_2", "Palier coupe 2"],
  ["cut_pct_2", "Coupe 2 %"],
  ["rearm_levels", "Réarmement paliers"],
  ["rearm_delay_min", "Réarmement min"],
  ["hard_stop_pct", "Stop dur %"],
  ["daily_circuit_breaker_usd", "Circuit breaker USD"],
  ["bags_capital_threshold_pct", "Seuil capital sacs %"],
  ["bnb_fee_discount", "Réduction frais BNB (0/1)"],
];

function pnlClass(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return "";
  return n >= 0 ? "pos" : "neg";
}

function fmt(v, d = 2) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toFixed(d);
}

async function api(path, opts = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw data;
  return data;
}

function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === name);
  });
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.id === `tab-${name}`);
  });
  if (name === "history") loadHistory();
  if (name === "pnl") loadPnl();
  if (name === "bags") loadBags();
  if (name === "config") loadConfig();
  if (name === "market") loadMarket();
}

document.querySelectorAll(".tabs button").forEach((b) => {
  b.addEventListener("click", () => switchTab(b.dataset.tab));
});

document.getElementById("btn-start").onclick = async () => {
  await api("/api/start", { method: "POST" });
  refreshRunning();
};
document.getElementById("btn-stop").onclick = async () => {
  await api("/api/stop", { method: "POST" });
  refreshRunning();
};
document.getElementById("btn-panic").onclick = async () => {
  if (!confirm("Panic close : clôturer grille + sacs ?")) return;
  await api("/api/panic", { method: "POST" });
  refreshRunning();
};

function renderRunning(s) {
  const g = s.grid || {};
  const m = s.capital || s.margin || {};
  document.getElementById("margin-banner").textContent =
    `Capital ${m.quote_asset || "USDT"} libre: ${fmt(m.quote_free ?? m.availableBalance)} | ` +
    `${m.base_asset || "BASE"} total: ${fmt(m.base_total, 6)} | canTrade: ${m.canTrade ?? "—"}`;

  document.getElementById("running-status").innerHTML = `
    <p>Running: <strong>${s.running ? "OUI" : "NON"}</strong></p>
    <p>Symbol: <strong>${s.symbol}</strong></p>
    <p>Mark: <strong>${fmt(s.mark_price, 2)}</strong></p>
    <p>Cycle: ${s.cycle_id ?? "—"}</p>
    <p>Daily PnL: <span class="${pnlClass(s.guards?.daily_pnl)}">${fmt(s.guards?.daily_pnl)}</span></p>
    <p>Guards: hard_stop=${s.guards?.hard_stop} breaker=${s.guards?.circuit_breaker} panic=${s.guards?.panic}</p>
  `;

  const incompleteCount = g.incomplete_count || (g.incomplete_levels || []).length;
  document.getElementById("grid-details").innerHTML = `
    <p>Active: ${g.active}</p>
    <p>Center: ${fmt(g.center_price)}</p>
    <p>Range: ${fmt(g.range_low)} — ${fmt(g.range_high)}</p>
    <p>Position qty: ${fmt(g.position_qty, 4)} @ ${fmt(g.entry_avg)}</p>
    <p>Grid Profit: <span class="${pnlClass(g.grid_profit)}">${fmt(g.grid_profit)}</span></p>
    <p>Floating: <span class="${pnlClass(g.floating_profit)}">${fmt(g.floating_profit)}</span></p>
    <p>Gross: <span class="${pnlClass(g.gross_pnl)}">${fmt(g.gross_pnl)}</span></p>
    <p>Paliers incomplets: <strong class="${incompleteCount ? "neg" : ""}">${incompleteCount}</strong></p>
  `;

  const low = Number(g.range_low);
  const high = Number(g.range_high);
  const mark = Number(s.mark_price);
  const marker = document.getElementById("grid-marker");
  if (low && high && mark && high > low) {
    const pct = Math.min(100, Math.max(0, ((mark - low) / (high - low)) * 100));
    marker.style.left = `${pct}%`;
  }

  const tbody = document.querySelector("#levels-table tbody");
  tbody.innerHTML = (g.levels || [])
    .map((lv) => {
      const incomplete = lv.status === "grid_level_incomplete";
      const statusCell = incomplete
        ? `<span class="badge-missing" title="${lv.incomplete_since || ""}">non placé</span>`
        : lv.status;
      return `<tr class="${incomplete ? "row-incomplete" : ""}">
      <td>${lv.index}</td><td>${lv.side}</td><td>${lv.price}</td>
      <td>${lv.quantity}</td><td>${statusCell}</td><td>${lv.order_id ?? "—"}</td>
    </tr>`;
    })
    .join("");
}

async function refreshRunning() {
  const s = await api("/api/running");
  renderRunning(s);
}

async function loadHistory() {
  const rows = await api("/api/history");
  document.querySelector("#history-table tbody").innerHTML = rows
    .map(
      (r) => `<tr>
      <td>${r.id}</td><td>${r.symbol}</td><td>${r.status}</td>
      <td class="${pnlClass(r.gross_pnl)}">${fmt(r.gross_pnl)}</td>
      <td class="${pnlClass(r.net_pnl)}">${fmt(r.net_pnl)}</td>
      <td>${r.opened_at ?? "—"}</td><td>${r.closed_at ?? "—"}</td><td>${r.close_reason ?? "—"}</td>
    </tr>`
    )
    .join("");
}

async function loadPnl() {
  const p = await api("/api/pnl");
  document.getElementById("pnl-kpis").innerHTML = `
    <h2>Indicateurs</h2>
    <p>Cycles: ${p.cycles_total} (W ${p.cycles_won} / L ${p.cycles_lost})</p>
    <p>Win rate: ${(p.win_rate * 100).toFixed(1)}%</p>
    <p>Gain moyen: <span class="pos">${fmt(p.avg_win)}</span></p>
    <p>Perte moyenne: <span class="neg">${fmt(p.avg_loss)}</span></p>
    <p>Durée moy. cycle: ${fmt(p.avg_cycle_duration_sec, 0)} s</p>
    <p>PnL net: <span class="${pnlClass(p.net_pnl)}">${fmt(p.net_pnl)}</span></p>
    <p>Théorique (+10/cycle): ${fmt(p.theoretical_pnl)}</p>
    <pre>${JSON.stringify(p.formulas, null, 2)}</pre>
  `;
  drawChart(p.curve || [], p.theoretical_pnl || 0);
}

function drawChart(curve, theoretical) {
  const canvas = document.getElementById("pnl-chart");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#243049";
  ctx.strokeRect(0, 0, canvas.width, canvas.height);
  if (!curve.length) {
    ctx.fillStyle = "#8fa3c8";
    ctx.fillText("Pas encore de snapshots PnL", 20, 30);
    return;
  }
  const vals = curve.map((c) => c.cumulative_pnl);
  const min = Math.min(...vals, 0, theoretical);
  const max = Math.max(...vals, 0, theoretical);
  const span = max - min || 1;
  const x = (i) => (i / (curve.length - 1 || 1)) * (canvas.width - 20) + 10;
  const y = (v) => canvas.height - 10 - ((v - min) / span) * (canvas.height - 20);
  ctx.beginPath();
  ctx.strokeStyle = "#3b82f6";
  curve.forEach((c, i) => {
    const px = x(i);
    const py = y(c.cumulative_pnl);
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.stroke();
  ctx.beginPath();
  ctx.strokeStyle = "#f59e0b";
  ctx.moveTo(10, y(theoretical));
  ctx.lineTo(canvas.width - 10, y(theoretical));
  ctx.stroke();
}

async function loadBags() {
  const bags = await api("/api/bags");
  document.querySelector("#bags-table tbody").innerHTML = bags
    .map(
      (b) => `<tr>
      <td>${b.id}</td><td>${fmt(b.quantity, 4)}</td><td>${fmt(b.entry_price)}</td>
      <td>${b.cut_level ?? "—"}</td>
      <td class="${pnlClass(b.realized_pnl)}">${fmt(b.realized_pnl)}</td>
      <td><button data-sell="${b.id}">Vendre marché</button></td>
    </tr>`
    )
    .join("") || `<tr><td colspan="6">Aucun sac ouvert</td></tr>`;
  document.querySelectorAll("[data-sell]").forEach((btn) => {
    btn.onclick = async () => {
      await api(`/api/bags/${btn.dataset.sell}/sell`, {
        method: "POST",
        body: JSON.stringify({ order_type: "MARKET" }),
      });
      loadBags();
    };
  });
}

function renderViability(v) {
  if (!v) return "";
  const alert = v.alert_ratio_below_2x;
  return `<div class="card" id="viability-box" style="border-color:${alert ? "var(--bad)" : "var(--ok)"}">
    <h3>Viabilité économique</h3>
    <p>Notionnel/palier: ${fmt(v.notional_per_level)}</p>
    <p>Frais aller-retour: ${fmt(v.fees_per_roundtrip, 4)} (taux ${fmt(v.fee_rate * 100, 4)}% — ${v.fee_source})</p>
    <p>Gain brut/grille: ${fmt(v.gross_per_grid, 4)}</p>
    <p>Net/grille: <span class="${pnlClass(v.net_per_grid)}">${fmt(v.net_per_grid, 4)}</span></p>
    <p>Ratio brut/frais: <strong class="${alert ? "neg" : "pos"}">${fmt(v.ratio_gross_to_fees, 2)}x</strong>
      ${alert ? "⚠ sous 2x" : "OK"}</p>
    <p>Grilles pour seuil cycle: ${v.grids_to_cycle ?? "∞ (net ≤ 0)"}</p>
    <p>BNB discount: ${v.bnb_fee_discount} (solde BNB=${fmt(v.bnb_balance, 4)}, suffisant=${v.bnb_sufficient})</p>
  </div>`;
}

async function loadConfig() {
  const data = await api("/api/config");
  const form = document.getElementById("config-form");
  const symbols = data.symbols || [];
  form.innerHTML = CONFIG_FIELDS.map(([k, label]) => {
    const v = data.active?.[k] ?? "";
    if (k === "symbol" && symbols.length) {
      const opts = symbols
        .map((s) => `<option value="${s}" ${s === v ? "selected" : ""}>${s}</option>`)
        .join("");
      return `<label>${label}<select name="${k}">${opts}</select></label>`;
    }
    if (k === "bnb_fee_discount") {
      return `<label>${label}<input name="${k}" type="number" min="0" max="1" step="1" value="${v ? 1 : 0}" /></label>`;
    }
    return `<label>${label}<input name="${k}" value="${v}" /></label>`;
  }).join("");
  const viabEl = document.getElementById("viability-panel");
  if (viabEl) viabEl.innerHTML = renderViability(data.viability);
  document.getElementById("config-history").innerHTML = (data.history || [])
    .map(
      (c) => `<div class="card" style="margin:0.4rem 0">
      #${c.id} ${c.symbol} active=${c.is_active}
      PnL=${fmt(c.net_pnl)} W/L=${c.cycles_won}/${c.cycles_lost}
      <pre>${JSON.stringify(c.params, null, 0)}</pre>
    </div>`
    )
    .join("");
}

function readConfigParams() {
  const form = document.getElementById("config-form");
  const params = {};
  CONFIG_FIELDS.forEach(([k]) => {
    const input = form.elements[k];
    let v = input.value;
    if (k === "bnb_fee_discount") v = Number(v) === 1;
    else if (k !== "symbol") v = Number(v);
    params[k] = v;
  });
  return params;
}

document.getElementById("btn-save-config").onclick = async () => {
  const params = readConfigParams();
  const mode = document.getElementById("config-mode").value;
  try {
    const res = await api("/api/config", {
      method: "POST",
      body: JSON.stringify({ params, mode }),
    });
    document.getElementById("config-msg").textContent = JSON.stringify(res, null, 2);
    loadConfig();
  } catch (e) {
    document.getElementById("config-msg").textContent = JSON.stringify(e, null, 2);
  }
};

document.getElementById("btn-simulate").onclick = async () => {
  const params = readConfigParams();
  try {
    const [sim, viab] = await Promise.all([
      api("/api/config/simulate", { method: "POST", body: JSON.stringify({ params }) }),
      api("/api/config/viability", { method: "POST", body: JSON.stringify({ params }) }),
    ]);
    document.getElementById("config-msg").textContent = JSON.stringify({ sim, viab }, null, 2);
    const viabEl = document.getElementById("viability-panel");
    if (viabEl) viabEl.innerHTML = renderViability(viab);
  } catch (e) {
    document.getElementById("config-msg").textContent = JSON.stringify(e, null, 2);
  }
};

async function loadMarket() {
  const rows = await api("/api/market");
  document.querySelector("#market-table tbody").innerHTML = rows
    .slice(0, 30)
    .map(
      (r) => `<tr data-sym="${r.symbol}" style="cursor:pointer">
      <td>${r.symbol}</td><td>${fmt(r.price, 4)}</td>
      <td class="${pnlClass(r.priceChangePercent)}">${fmt(r.priceChangePercent)}%</td>
      <td>${fmt(r.volume, 2)}</td>
    </tr>`
    )
    .join("");
  document.querySelectorAll("#market-table tbody tr").forEach((tr) => {
    tr.onclick = async () => {
      const d = await api(`/api/market/${tr.dataset.sym}`);
      document.getElementById("market-detail").innerHTML = `
        <h2>${d.symbol}</h2>
        <p>Price: ${fmt(d.price)}</p>
        <p>tickSize=${d.filters.tickSize} stepSize=${d.filters.stepSize} minNotional=${d.filters.minNotional}</p>
        <p>Vol stdev 1h: ${fmt(d.volatility_stdev_1h, 6)} ATR14: ${fmt(d.atr_14_1h, 4)}</p>
        <pre>bids=${JSON.stringify(d.orderbook.bids)}
asks=${JSON.stringify(d.orderbook.asks)}</pre>
      `;
    };
  });
}

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const host = API ? API.replace(/^http/, "ws") : `${proto}://${location.host}`;
  const ws = new WebSocket(`${host}/ws`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "status") renderRunning(msg.data);
  };
  ws.onclose = () => setTimeout(connectWs, 2000);
}

refreshRunning().catch(console.error);
connectWs();
setInterval(() => refreshRunning().catch(() => {}), 5000);
