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
  ["idle_recenter_min", "Recentrage idle (min)"],
  ["stuck_sell_min", "SELL bloqué (min)"],
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

/** Flash visuel sur changement de valeur (vert hausse / rouge baisse). */
function flashEl(el, delta) {
  if (!el) return;
  el.classList.remove("flash-up", "flash-down");
  void el.offsetWidth;
  el.classList.add(delta >= 0 ? "flash-up" : "flash-down");
}

let _lastMark = null;
let _lastGross = null;
const charts = { price: null, pnl: null, cycles: null, latency: null };
let _gridOverlayRegistered = false;

function formatDuration(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return "—";
  const s = Math.floor(Number(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${r}s`;
  return `${r}s`;
}

function findNearestPointIndex(points, isoTs) {
  if (!isoTs || !points?.length) return 0;
  const t = new Date(isoTs).getTime();
  if (Number.isNaN(t)) return 0;
  let best = 0;
  let bestD = Infinity;
  points.forEach((p, i) => {
    const pt = new Date(p.ts).getTime();
    if (Number.isNaN(pt)) return;
    const d = Math.abs(pt - t);
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  });
  return best;
}

function registerGridOverlayPlugin() {
  if (_gridOverlayRegistered || typeof Chart === "undefined") return;
  _gridOverlayRegistered = true;
  Chart.register({
    id: "ultiumGridOverlay",
    afterDraw(chart, _args, opts) {
      const levels = opts.levels || [];
      const fills = opts.fills || [];
      const points = opts.points || [];
      const { ctx, chartArea, scales } = chart;
      if (!chartArea || !scales?.y) return;

      for (const lv of levels) {
        if (lv.price == null) continue;
        const y = scales.y.getPixelForValue(lv.price);
        if (y < chartArea.top - 4 || y > chartArea.bottom + 4) continue;
        const active = lv.visual === "active";
        const isSell = lv.side === "SELL";
        ctx.save();
        ctx.strokeStyle = active
          ? isSell
            ? "#ef4444"
            : "#22c55e"
          : "rgba(148, 163, 184, 0.5)";
        ctx.lineWidth = active ? 1.5 : 1;
        ctx.setLineDash(active ? [] : [5, 4]);
        ctx.beginPath();
        ctx.moveTo(chartArea.left, y);
        ctx.lineTo(chartArea.right, y);
        ctx.stroke();
        const qty =
          lv.quantity != null ? Number(lv.quantity).toFixed(5).replace(/\.?0+$/, "") : "—";
        const label = `${isSell ? "Limit" : "Buy"} ${Number(lv.price).toFixed(2)} — ${qty}`;
        ctx.font = `${active ? "600" : "400"} 10px ui-sans-serif, system-ui, sans-serif`;
        ctx.fillStyle = active ? (isSell ? "#fca5a5" : "#86efac") : "#94a3b8";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        ctx.fillText(label, chartArea.right - 6, y);
        ctx.restore();
      }

      for (const f of fills) {
        if (f.price == null) continue;
        const xi = findNearestPointIndex(points, f.ts);
        const x = scales.x.getPixelForValue(xi);
        const y = scales.y.getPixelForValue(f.price);
        if (x < chartArea.left - 12 || x > chartArea.right + 12) continue;
        const letter = f.side === "BUY" ? "B" : "S";
        const col = f.side === "BUY" ? "#22c55e" : "#ef4444";
        ctx.save();
        ctx.beginPath();
        ctx.arc(x, y, 9, 0, Math.PI * 2);
        ctx.fillStyle = col;
        ctx.fill();
        ctx.strokeStyle = "#0b1220";
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillStyle = "#fff";
        ctx.font = "bold 10px ui-sans-serif, system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(letter, x, y);
        ctx.restore();
      }
    },
  });
}

function ensureChart(key, canvasId, config) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return null;
  if (charts[key]) {
    charts[key].destroy();
  }
  charts[key] = new Chart(canvas, config);
  return charts[key];
}

function insufficientMsg(elId, msg) {
  const el = document.getElementById(elId);
  if (el) el.textContent = msg || "données insuffisantes pour l'instant";
}

async function refreshCharts(status) {
  const symbol = status?.symbol || "BTCUSDT";
  registerGridOverlayPlugin();
  // Prix + niveaux grille (20 lignes) + marqueurs fills
  try {
    const data = await api(`/api/charts/price?symbol=${symbol}&limit=120`);
    const msg = document.getElementById("price-chart-msg");
    const levels = data.levels || [];
    const fills = data.fills || [];
    if (data.insufficient_data && !levels.length) {
      insufficientMsg("price-chart-msg", data.message);
      if (charts.price) {
        charts.price.destroy();
        charts.price = null;
      }
    } else {
      if (msg) {
        msg.textContent =
          levels.length > 0
            ? `${levels.length} niveaux tracés · ${fills.length} fill(s) · survol = prix/qty DB`
            : data.message || "En attente de niveaux de grille actifs";
      }
      const points = data.points || [];
      const labels =
        points.length > 0
          ? points.map((p) => p.ts?.slice(11, 19) || String(p.id))
          : ["—"];
      const prices = points.length > 0 ? points.map((p) => p.price) : [data.mark || 0];
      const levelPrices = levels.map((l) => l.price).filter((p) => p != null);
      const allY = [...prices, ...levelPrices];
      let yMin = Math.min(...allY);
      let yMax = Math.max(...allY);
      const pad = (yMax - yMin) * 0.06 || yMax * 0.001 || 1;
      yMin -= pad;
      yMax += pad;

      ensureChart("price", "price-chart", {
        type: "line",
        data: {
          labels,
          datasets: [
            {
              label: "Prix",
              data: prices,
              borderColor: "#3b82f6",
              borderWidth: 2,
              pointRadius: points.length > 1 ? 0 : 2,
              tension: 0.15,
              rawPoints: points,
            },
          ],
        },
        options: {
          animation: false,
          responsive: true,
          maintainAspectRatio: false,
          layout: { padding: { right: 8, top: 8, bottom: 4, left: 4 } },
          plugins: {
            legend: { display: false },
            ultiumGridOverlay: {
              levels,
              fills,
              points,
            },
            tooltip: {
              callbacks: {
                label(item) {
                  return `Prix ${item.formattedValue}`;
                },
                afterBody(items) {
                  const i = items[0]?.dataIndex;
                  const pt = points[i];
                  return pt ? `id=${pt.id} ts=${pt.ts}` : "";
                },
              },
            },
          },
          scales: {
            x: { display: true, ticks: { maxTicksLimit: 10 } },
            y: {
              display: true,
              min: yMin,
              max: yMax,
              ticks: { callback: (v) => Number(v).toFixed(2) },
            },
          },
        },
      });
    }
  } catch (e) {
    insufficientMsg("price-chart-msg", "erreur chargement courbe prix");
  }

  // PnL
  try {
    const data = await api(`/api/charts/pnl?symbol=${symbol}&limit=120`);
    if (data.insufficient_data) {
      insufficientMsg("pnl-chart-msg", data.message);
      if (charts.pnl) {
        charts.pnl.destroy();
        charts.pnl = null;
      }
    } else {
      const msg = document.getElementById("pnl-chart-msg");
      if (msg) msg.textContent = data.formula || "";
      ensureChart("pnl", "pnl-chart", {
        type: "line",
        data: {
          labels: data.points.map((p) => p.ts?.slice(11, 19) || p.id),
          datasets: [
            {
              label: "PnL cumulé",
              data: data.points.map((p) => p.cumulative_pnl),
              borderColor: "#3b82f6",
              pointRadius: 2,
              rawPoints: data.points,
            },
          ],
        },
        options: {
          animation: false,
          plugins: {
            tooltip: {
              callbacks: {
                afterBody(items) {
                  const i = items[0]?.dataIndex;
                  const pt = data.points[i];
                  return pt
                    ? `id=${pt.id} cum=${pt.cumulative_pnl} grid=${pt.grid_pnl} bags=${pt.bags_pnl} closed=${pt.closed_cycles_pnl}`
                    : "";
                },
              },
            },
          },
        },
      });
    }
  } catch (e) {
    /* ignore */
  }

  // Cycles histogram
  try {
    const data = await api(`/api/charts/cycles?symbol=${symbol}&limit=40`);
    if (data.insufficient_data) {
      insufficientMsg("cycles-chart-msg", data.message);
      if (charts.cycles) {
        charts.cycles.destroy();
        charts.cycles = null;
      }
    } else {
      const msg = document.getElementById("cycles-chart-msg");
      if (msg) msg.textContent = "";
      ensureChart("cycles", "cycles-chart", {
        type: "bar",
        data: {
          labels: data.bars.map((b) => `#${b.id}`),
          datasets: [
            {
              label: "Net PnL cycle",
              data: data.bars.map((b) => b.net_pnl),
              backgroundColor: data.bars.map((b) =>
                b.net_pnl >= 0 ? "rgba(34,197,94,0.7)" : "rgba(239,68,68,0.7)"
              ),
              rawBars: data.bars,
            },
          ],
        },
        options: {
          animation: false,
          plugins: {
            tooltip: {
              callbacks: {
                afterBody(items) {
                  const i = items[0]?.dataIndex;
                  const b = data.bars[i];
                  return b
                    ? `id=${b.id} net=${b.net_pnl} gross=${b.gross_pnl} reason=${b.close_reason}`
                    : "";
                },
              },
            },
          },
        },
      });
    }
  } catch (e) {
    /* ignore */
  }
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
  if (name === "fees") loadFees();
  if (name === "bags") loadBags();
  if (name === "config") loadConfig();
  if (name === "market") loadMarket();
  if (name === "supervision") loadSupervision();
}

async function loadSupervision() {
  const d = await api("/api/supervision");
  const hb = d.states?.heartbeat?.value || {};
  const recon = d.states?.reconciliation?.value || {};
  const ex = d.states?.exchange?.value || {};
  document.getElementById("sup-heartbeat").innerHTML = `
    <h2>Heartbeat bot</h2>
    <p>HTTP ok: <strong>${hb.http_ok}</strong></p>
    <p>Âge heartbeat bot: ${hb.bot_heartbeat_age_s != null ? fmt(hb.bot_heartbeat_age_s, 1) + "s" : "—"}</p>
    <p>Dernier check: ${hb.checked_at || "—"}</p>
    <pre>${JSON.stringify(hb.bot_heartbeat || {}, null, 2)}</pre>
  `;
  document.getElementById("sup-recon").innerHTML = `
    <h2>Réconciliation indépendante</h2>
    <p>Δ USDT: <strong class="${(recon.delta_usdt || 0) > 1 ? "neg" : "pos"}">${fmt(recon.delta_usdt, 4)}</strong></p>
    <p>Binance qty: ${fmt(recon.binance_qty, 6)} | attendu: ${fmt(recon.expected, 6)}</p>
    <pre>${JSON.stringify(recon, null, 2)}</pre>
  `;
  document.getElementById("sup-exchange").innerHTML = `
    <h2>Exchange</h2>
    <p>Latence: ${fmt(ex.latency_ms, 1)} ms | status=${ex.status}</p>
  `;
  const alerts = d.alerts || [];
  document.querySelector("#sup-alerts tbody").innerHTML = alerts
    .map(
      (a) => `<tr>
      <td>${a.id}</td><td>${a.severity}</td><td>${a.kind}</td>
      <td>${a.message}</td><td>${a.status}</td><td>${a.created_at || "—"}</td>
    </tr>`
    )
    .join("") || `<tr><td colspan="6">Aucune alerte</td></tr>`;
  const lat = (d.metrics || [])
    .filter((m) => m.kind === "exchange_latency_ms")
    .slice(0, 40)
    .reverse();
  document.getElementById("sup-latency").textContent = lat
    .map((m) => `${m.created_at}  ${fmt(m.value, 1)} ms`)
    .join("\n") || "Pas encore de métriques";
  if (lat.length >= 2) {
    ensureChart("latency", "latency-chart", {
      type: "line",
      data: {
        labels: lat.map((m) => (m.created_at || "").slice(11, 19)),
        datasets: [
          {
            label: "Latence ms",
            data: lat.map((m) => m.value),
            borderColor: "#f59e0b",
            pointRadius: 2,
            rawPoints: lat,
          },
        ],
      },
      options: {
        animation: false,
        plugins: {
          tooltip: {
            callbacks: {
              afterBody(items) {
                const i = items[0]?.dataIndex;
                const p = lat[i];
                return p ? `value=${p.value} ts=${p.created_at}` : "";
              },
            },
          },
        },
      },
    });
  }
}

document.querySelectorAll(".tabs button").forEach((b) => {
  b.addEventListener("click", () => switchTab(b.dataset.tab));
});

async function controlAction(path, { confirmMsg } = {}) {
  if (confirmMsg && !confirm(confirmMsg)) return;
  const banner = document.getElementById("margin-banner");
  try {
    const res = await api(path, { method: "POST" });
    if (banner && res.message) {
      banner.dataset.prev = banner.innerHTML;
      banner.innerHTML = `<span class="${res.already_running ? "neg" : "pos"}">${res.message}</span>`;
    }
    // Attendre le traitement bot (poll ~5s) — ignorer un last_command antérieur
    const t0 = Date.now();
    for (let i = 0; i < 10; i++) {
      await new Promise((r) => setTimeout(r, 800));
      const last = await api("/api/last_command").catch(() => null);
      const ts = last?.ts ? Date.parse(last.ts) : 0;
      if (last?.name && path.endsWith(last.name) && last.result && ts >= t0 - 500) {
        if (banner && last.result.message) {
          banner.innerHTML = `<span class="${last.result.already_running || last.result.noop ? "neg" : "pos"}">${last.result.message}</span>`;
        }
        break;
      }
    }
  } catch (e) {
    if (banner) banner.innerHTML = `<span class="neg">Erreur: ${e.message || e.error || e}</span>`;
  }
  await refreshRunning();
}

document.getElementById("btn-start").onclick = () => controlAction("/api/start");
document.getElementById("btn-stop").onclick = () => controlAction("/api/stop");
document.getElementById("btn-panic").onclick = () =>
  controlAction("/api/panic", { confirmMsg: "Panic close : vendre tout le solde base + clôturer sacs ?" });

function renderGridRecap(s) {
  const tbody = document.querySelector("#grid-recap-table tbody");
  if (!tbody) return;
  const r = s.grid_recap;
  if (!r) {
    tbody.innerHTML =
      '<tr><td colspan="11">Aucun cycle actif — Start pour afficher le récapitulatif (une ligne par cycle actif).</td></tr>';
    return;
  }
  const range =
    r.price_range_low != null && r.price_range_high != null
      ? `${fmt(r.price_range_low)} — ${fmt(r.price_range_high)}`
      : "—";
  tbody.innerHTML = `<tr>
    <td>${r.pair}</td>
    <td>${r.time_created ? r.time_created.slice(0, 19).replace("T", " ") : "—"}</td>
    <td>${fmt(r.total_investment)}</td>
    <td class="${pnlClass(r.total_profit)}">${fmt(r.total_profit)}</td>
    <td class="${pnlClass(r.grid_profit)}">${fmt(r.grid_profit)}</td>
    <td class="${pnlClass(r.floating_profit)}">${fmt(r.floating_profit)}</td>
    <td>${r.total_matched_trades}</td>
    <td>${range}</td>
    <td>${formatDuration(r.duration_sec)}</td>
    <td>${r.number_of_grids}</td>
    <td class="recap-actions">
      <button type="button" class="icon-btn ok" title="Start / Reprise" aria-label="Start">▶</button>
      <button type="button" class="icon-btn" title="Stop / Pause" aria-label="Stop">⏸</button>
      <button type="button" class="icon-btn danger" title="Panic Close" aria-label="Panic">⏹</button>
    </td>
  </tr>`;
  const [btnStart, btnStop, btnPanic] = tbody.querySelectorAll(".icon-btn");
  if (btnStart) btnStart.onclick = () => document.getElementById("btn-start")?.click();
  if (btnStop) btnStop.onclick = () => document.getElementById("btn-stop")?.click();
  if (btnPanic) btnPanic.onclick = () => document.getElementById("btn-panic")?.click();
}

function renderRunning(s) {
  const g = s.grid || {};
  const m = s.capital || s.margin || {};
  // Une seule source : s.mark_price (ticker Binance via /api/running).
  // Avant : l'en-tête gardait l'ancienne valeur si mark=null, l'État affichait "—".
  const mark = s.mark_price != null && s.mark_price !== "" ? Number(s.mark_price) : null;
  const markText = mark != null && !Number.isNaN(mark) ? fmt(mark, 2) : "—";
  const markSuffix = s.mark_error
    ? ` <span class="neg" title="${String(s.mark_error).replace(/"/g, "&quot;")}">${s.mark_stale ? "(stale)" : "(err)"}</span>`
    : s.mark_stale
      ? ' <span class="neg">(stale)</span>'
      : "";
  const live = document.getElementById("live-price");
  if (live) {
    const prev = _lastMark;
    live.innerHTML = `${s.symbol || ""} ${markText}${markSuffix}`;
    if (mark != null && prev != null && mark !== prev) flashEl(live, mark - prev);
    if (mark != null) _lastMark = mark;
  }

  const capErr = m.error ? String(m.error) : null;
  if (capErr && m.quote_free == null && m.base_total == null) {
    document.getElementById("margin-banner").innerHTML =
      `<span class="neg" title="${capErr.replace(/"/g, "&quot;")}">Capital indisponible: ${capErr.slice(0, 120)}</span>`;
  } else {
    const stale = m.stale || capErr ? ' <span class="neg">(stale)</span>' : "";
    document.getElementById("margin-banner").innerHTML =
      `Capital ${m.quote_asset || "USDT"} libre: ${fmt(m.quote_free ?? m.availableBalance)} | ` +
      `${m.base_asset || "BASE"} total: ${fmt(m.base_total, 6)} | canTrade: ${m.canTrade ?? "—"}${stale}`;
  }

  document.getElementById("running-status").innerHTML = `
    <p>Running: <strong>${s.running ? "OUI" : "NON"}</strong></p>
    <p>Symbol: <strong>${s.symbol}</strong></p>
    <p>Mark: <strong>${markText}</strong>${markSuffix}</p>
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
    <p>Gross: <span id="gross-pnl-val" class="${pnlClass(g.gross_pnl)}">${fmt(g.gross_pnl)}</span></p>
    <p>Paliers incomplets: <strong class="${incompleteCount ? "neg" : ""}">${incompleteCount}</strong></p>
  `;
  const grossEl = document.getElementById("gross-pnl-val");
  if (grossEl && _lastGross != null && g.gross_pnl !== _lastGross) {
    flashEl(grossEl, (g.gross_pnl || 0) - _lastGross);
  }
  _lastGross = g.gross_pnl;
  renderGridRecap(s);
  refreshCharts(s);

  const low = Number(g.range_low);
  const high = Number(g.range_high);
  const markNum = Number(mark);
  const marker = document.getElementById("grid-marker");
  if (low && high && markNum && high > low) {
    const pct = Math.min(100, Math.max(0, ((markNum - low) / (high - low)) * 100));
    marker.style.left = `${pct}%`;
  }

  const ex = s.exchange_orders || {};
  const syncById = {};
  for (const row of ex.levels_vs_openOrders || []) {
    if (row.order_id != null) syncById[String(row.order_id)] = row;
  }
  let levelsNote = document.getElementById("levels-sync-note");
  if (!levelsNote) {
    const card = document.querySelector("#levels-table")?.closest(".card");
    if (card) {
      levelsNote = document.createElement("p");
      levelsNote.id = "levels-sync-note";
      levelsNote.className = "chart-msg";
      card.insertBefore(levelsNote, card.querySelector("table"));
    }
  }
  if (levelsNote) {
    if (ex.openOrders_error) {
      levelsNote.innerHTML = `<span class="neg">openOrders indisponible: ${String(ex.openOrders_error).slice(0, 160)}</span>`;
    } else {
      const mm = (ex.mismatches || []).length;
      levelsNote.textContent =
        `Exchange openOrders=${ex.openOrders_count ?? 0} | écarts DB↔exchange=${mm}`;
      if (mm) levelsNote.classList.add("neg");
      else levelsNote.classList.remove("neg");
    }
  }

  const tbody = document.querySelector("#levels-table tbody");
  tbody.innerHTML = (g.levels || [])
    .map((lv) => {
      const incomplete = lv.status === "grid_level_incomplete";
      const sync = lv.order_id != null ? syncById[String(lv.order_id)] : null;
      let statusCell = incomplete
        ? `<span class="badge-missing" title="${lv.incomplete_since || ""}">non placé</span>`
        : lv.status;
      if (sync && lv.status === "open" && !sync.in_openOrders) {
        statusCell = `<span class="badge-missing" title="absent de openOrders">open (désync)</span>`;
      } else if (sync && sync.in_openOrders && lv.status !== "open") {
        statusCell = `${lv.status} <span class="neg">(encore openOrders)</span>`;
      }
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

async function loadFees() {
  const f = await api("/api/fees");
  document.getElementById("fees-summary").innerHTML = `
    <h2>Frais réels</h2>
    <p>BNB fee config: <strong>${f.bnb_fee_discount_config}</strong></p>
    <p>BNB libre: <strong>${f.bnb_free ?? "—"}</strong>${f.bnb_error ? ` <span class="neg">(${String(f.bnb_error).slice(0, 80)})</span>` : ""}</p>
    <p>Total fees listés (USDT): <strong>${fmt(f.total_fees_usdt_listed, 6)}</strong></p>
    <p class="chart-msg">${f.note || ""}</p>
  `;
  document.querySelector("#fees-table tbody").innerHTML = (f.rows || [])
    .map(
      (r) => `<tr>
      <td>${r.id}</td><td>${r.trade_id ?? "—"}</td><td>${r.order_id ?? "—"}</td>
      <td>${r.commission_asset}</td><td>${fmt(r.commission, 8)}</td>
      <td>${fmt(r.commission_usdt, 6)}</td><td>${r.cycle_id ?? "—"}</td>
      <td>${r.created_at ?? "—"}</td>
    </tr>`
    )
    .join("") || `<tr><td colspan="8">aucune commission enregistrée</td></tr>`;
  document.querySelector("#fees-cycles-table tbody").innerHTML = (f.by_cycle || [])
    .map(
      (c) => `<tr>
      <td>${c.cycle_id}</td>
      <td class="${pnlClass(c.gross_pnl)}">${fmt(c.gross_pnl)}</td>
      <td class="${pnlClass(c.net_pnl)}">${fmt(c.net_pnl)}</td>
      <td>${fmt(c.fees_real_usdt, 6)}</td>
      <td>${c.close_reason ?? "—"}</td>
    </tr>`
    )
    .join("") || `<tr><td colspan="5">aucun cycle clôturé</td></tr>`;
}

async function loadPnl() {
  const p = await api("/api/pnl");
  document.getElementById("pnl-kpis").innerHTML = `
    <h2>Indicateurs</h2>
    <p>Cycles clôturés : ${p.cycles_total} (W ${p.cycles_won} / L ${p.cycles_lost})</p>
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
    <p>Frais achat initial (fixe/cycle): ${fmt(v.fees_initial_inventory, 4)}</p>
    <p>Frais aller-retour/grille: ${fmt(v.fees_per_roundtrip, 4)} (taux ${fmt(v.fee_rate * 100, 4)}% — ${v.fee_source})</p>
    <p>Gain brut/grille: ${fmt(v.gross_per_grid, 4)}</p>
    <p>Net/grille: <span class="${pnlClass(v.net_per_grid)}">${fmt(v.net_per_grid, 4)}</span></p>
    <p>Net au seuil brut (${v.grids_to_cycle ?? "—"} grilles): <span class="${pnlClass(v.net_at_gross_threshold)}">${fmt(v.net_at_gross_threshold, 2)}</span>
      (frais totaux théor. ${fmt(v.total_fees_at_gross_threshold, 2)})</p>
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
