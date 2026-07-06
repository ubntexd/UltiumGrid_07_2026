const API = "";
const fmt = (n, d = 2) => (n == null || Number.isNaN(n) ? "—" : Number(n).toFixed(d));
const fmtUsd = (n) => (n == null ? "—" : `${n >= 0 ? "+" : ""}${fmt(n)} $`);

let chartHistory, chartVpsCpu;
const vpsCpuHistory = { labels: [], btc: [], sol: [], xrp: [], total: [] };

async function api(path, opts) {
  const r = await fetch(API + path, opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

function tickClock() {
  document.getElementById("clock").textContent = new Date().toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

function bytesToGiB(b) {
  return (b / 1024 / 1024 / 1024).toFixed(2) + " GiB";
}

function renderVps(v) {
  const mem = v.memory || {};
  const disk = v.disk_root || {};
  const load = v.load || {};
  const stacks = v.docker?.cpu_pct_by_stack || {};
  document.getElementById("vps-cards").innerHTML = `
    <div class="vps-card"><div class="label">CPU load (1m)</div><div class="value">${fmt(load.load_1m, 2)}</div><div class="bar"><span style="width:${Math.min(100, (load.load_1m / (v.cpu_cores || 3)) * 100)}%"></span></div></div>
    <div class="vps-card"><div class="label">RAM utilisée</div><div class="value">${mem.used_pct || 0}%</div><div class="bar"><span style="width:${mem.used_pct || 0}%"></span></div></div>
    <div class="vps-card"><div class="label">RAM dispo</div><div class="value">${bytesToGiB(mem.available_bytes || 0)}</div></div>
    <div class="vps-card"><div class="label">Disque /</div><div class="value">${disk.used_pct || 0}%</div><div class="bar"><span style="width:${disk.used_pct || 0}%"></span></div></div>
    <div class="vps-card"><div class="label">CPU stacks</div><div class="value" style="font-size:0.75rem">BTC ${fmt(stacks.btc,1)}% · SOL ${fmt(stacks.sol,1)}% · XRP ${fmt(stacks.xrp,1)}%</div></div>
    <div class="vps-card"><div class="label">Containers</div><div class="value">${v.docker?.container_count || 0}</div></div>
  `;
  document.getElementById("docker-count").textContent = v.docker?.container_count || 0;
  const tbody = document.querySelector("#docker-table tbody");
  tbody.innerHTML = (v.docker?.containers || []).map((c) =>
    `<tr><td>${c.name}</td><td>${c.stack}</td><td>${fmt(c.cpu_pct,1)}%</td><td>${c.mem_usage}</td></tr>`
  ).join("");

  const t = new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (vpsCpuHistory.labels.length > 40) {
    vpsCpuHistory.labels.shift();
    ["btc", "sol", "xrp", "total"].forEach((k) => vpsCpuHistory[k].shift());
  }
  vpsCpuHistory.labels.push(t);
  vpsCpuHistory.btc.push(stacks.btc || 0);
  vpsCpuHistory.sol.push(stacks.sol || 0);
  vpsCpuHistory.xrp.push(stacks.xrp || 0);
  vpsCpuHistory.total.push((stacks.btc || 0) + (stacks.sol || 0) + (stacks.xrp || 0));
  if (chartVpsCpu) {
    chartVpsCpu.data.labels = vpsCpuHistory.labels;
    chartVpsCpu.data.datasets[0].data = vpsCpuHistory.btc;
    chartVpsCpu.data.datasets[1].data = vpsCpuHistory.sol;
    chartVpsCpu.data.datasets[2].data = vpsCpuHistory.xrp;
    chartVpsCpu.update("none");
  }
}

function renderKpis(summary) {
  if (!summary) return;
  const items = [
    { l: "Net réalisé (total)", n: summary.total_net_realized, cls: summary.total_net_realized >= 0 ? "pos" : "neg" },
    { l: "Net aujourd'hui", n: summary.total_net_today, cls: summary.total_net_today >= 0 ? "pos" : "neg" },
    { l: "Gross ouvert (3 inst.)", n: summary.total_gross_open, cls: summary.total_gross_open >= 0 ? "pos" : "neg" },
    { l: "Grid ouvert", n: summary.total_grid_open, cls: "pos" },
    { l: "Instances OK", n: summary.instances_ok, cls: "" },
  ];
  document.getElementById("kpi-row").innerHTML = items.map((i) =>
    `<div class="kpi ${i.cls}"><div class="n">${typeof i.n === "number" && i.l.includes("Instances") ? i.n : fmtUsd(i.n)}</div><div class="l">${i.l}</div></div>`
  ).join("");
}

function renderSummary(report) {
  const el = document.getElementById("summary-box");
  if (!report) { el.textContent = "Aucune collecte"; return; }
  el.innerHTML = `
    <p><strong>Dernière collecte :</strong> ${(report.ts_utc || "").slice(0, 19)} UTC</p>
    <p><strong>Heure :</strong> ${report.hour_key || "—"}</p>
    <p><strong>Net réalisé cumulé :</strong> ${fmtUsd(report.summary?.total_net_realized)}</p>
    <p><strong>Net jour :</strong> ${fmtUsd(report.summary?.total_net_today)}</p>
    <p style="margin-top:0.75rem;color:var(--muted)">Prochaine collecte automatique : n8n toutes les heures (:00 UTC).</p>
  `;
}

function renderInstances(instances) {
  const grid = document.getElementById("instances-grid");
  grid.innerHTML = (instances || []).map((inst) => {
    if (!inst.ok) {
      return `<div class="inst-card" data-id="${inst.instance_id}"><h3>${inst.label} <span class="badge err">OFFLINE</span></h3><p>${inst.error || "Erreur"}</p></div>`;
    }
    const p = inst.pnl_open || {};
    const r = inst.realized || {};
    const t = inst.trades || {};
    const run = inst.running || {};
    return `
    <div class="inst-card" data-id="${inst.instance_id}">
      <h3>${inst.label} <span class="badge ok">${run.symbol || ""}</span></h3>
      <dl class="inst-metrics">
        <dt>Cycle</dt><dd>#${run.cycle_id} · mark ${fmt(run.mark_price, run.mark_price > 10 ? 2 : 4)}</dd>
        <dt>Total Profit</dt><dd>${fmtUsd(p.gross_total)}</dd>
        <dt>Grid / Float</dt><dd>${fmtUsd(p.grid_profit)} / ${fmtUsd(p.floating_profit)}</dd>
        <dt>Net réalisé</dt><dd>${fmtUsd(r.sum_net)}</dd>
        <dt>Net aujourd'hui</dt><dd>${fmtUsd(r.net_today)}</dd>
        <dt>Trades / RT cycle</dt><dd>${t.total || 0} / ${t.roundtrips_cycle_open || 0}</dd>
        <dt>Frais cumulés</dt><dd>${fmtUsd(t.fees_usdt)}</dd>
        <dt>Daily PnL guard</dt><dd>${fmtUsd(p.daily_pnl_guard)}</dd>
      </dl>
      <div class="inst-chart"><canvas id="chart-pnl-${inst.instance_id}"></canvas></div>
    </div>`;
  }).join("");

  (instances || []).forEach((inst) => {
    if (!inst.ok || !inst.pnl_curve?.length) return;
    const ctx = document.getElementById(`chart-pnl-${inst.instance_id}`);
    if (!ctx) return;
    const pts = inst.pnl_curve.slice(-60);
    new Chart(ctx, {
      type: "line",
      data: {
        labels: pts.map((p) => (p.ts || "").slice(11, 16)),
        datasets: [{
          label: "Gross cycle",
          data: pts.map((p) => p.grid_pnl),
          borderColor: inst.instance_id === "sol" ? "#a855f7" : inst.instance_id === "xrp" ? "#0ea5e9" : "#3b82f6",
          tension: 0.3,
          fill: true,
          backgroundColor: "rgba(99,102,241,0.08)",
          pointRadius: 0,
        }],
      },
      options: chartOptsSmall(),
    });
  });
}

function chartOptsSmall() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: "#8b9cb8", maxTicksLimit: 6 }, grid: { color: "#2a3548" } },
      y: { ticks: { color: "#8b9cb8" }, grid: { color: "#2a3548" } },
    },
  };
}

function renderHistory(hist) {
  const pts = hist.points || [];
  const labels = pts.map((p) => (p.hour_key || p.ts_utc || "").slice(0, 16).replace("T", " "));
  if (!chartHistory) {
    const ctx = document.getElementById("chart-history");
    chartHistory = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "BTC net réalisé", data: pts.map((p) => (p.instances?.find((i) => i.id === "btc") || {}).net_realized), borderColor: "#3b82f6", tension: 0.3, pointRadius: 3 },
          { label: "SOL net réalisé", data: pts.map((p) => (p.instances?.find((i) => i.id === "sol") || {}).net_realized), borderColor: "#a855f7", tension: 0.3, pointRadius: 3 },
          { label: "XRP net réalisé", data: pts.map((p) => (p.instances?.find((i) => i.id === "xrp") || {}).net_realized), borderColor: "#0ea5e9", tension: 0.3, pointRadius: 3 },
          { label: "Gross ouvert (total)", data: pts.map((p) => p.summary?.total_gross_open), borderColor: "#f59e0b", borderDash: [4, 4], tension: 0.3, pointRadius: 2 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#8b9cb8" } } },
        scales: {
          x: { ticks: { color: "#8b9cb8", maxRotation: 45 }, grid: { color: "#2a3548" } },
          y: { ticks: { color: "#8b9cb8" }, grid: { color: "#2a3548" } },
        },
      },
    });
  } else {
    chartHistory.data.labels = labels;
    chartHistory.data.datasets[0].data = pts.map((p) => (p.instances?.find((i) => i.id === "btc") || {}).net_realized);
    chartHistory.data.datasets[1].data = pts.map((p) => (p.instances?.find((i) => i.id === "sol") || {}).net_realized);
    chartHistory.data.datasets[2].data = pts.map((p) => (p.instances?.find((i) => i.id === "xrp") || {}).net_realized);
    chartHistory.data.datasets[3].data = pts.map((p) => p.summary?.total_gross_open);
    chartHistory.update();
  }
}

async function loadLatest() {
  try {
    const report = await api("/api/latest");
    renderKpis(report.summary);
    renderSummary(report);
    renderInstances(report.instances);
    document.getElementById("api-status").textContent = "OK · " + (report.ts_utc || "").slice(11, 19);
  } catch {
    document.getElementById("api-status").textContent = "en attente collecte";
  }
  try {
    const hist = await api("/api/history?limit=48");
    renderHistory(hist);
  } catch { /* ignore */ }
}

async function pollVps() {
  try {
    const v = await api("/api/vps");
    renderVps(v);
  } catch (e) {
    console.warn("vps", e);
  }
}

async function doCollect() {
  const btn = document.getElementById("btn-refresh");
  btn.disabled = true;
  btn.textContent = "Collecte…";
  try {
    await api("/api/collect", { method: "POST" });
    await loadLatest();
  } catch (e) {
    alert("Erreur collecte: " + e.message);
  }
  btn.disabled = false;
  btn.textContent = "Collecter maintenant";
}

function initCharts() {
  const ctx = document.getElementById("chart-vps-cpu");
  chartVpsCpu = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "BTC stack %", data: [], borderColor: "#3b82f6", tension: 0.3, pointRadius: 0 },
        { label: "SOL stack %", data: [], borderColor: "#a855f7", tension: 0.3, pointRadius: 0 },
        { label: "XRP stack %", data: [], borderColor: "#0ea5e9", tension: 0.3, pointRadius: 0 },
      ],
    },
    options: chartOptsSmall(),
  });
}

document.getElementById("btn-refresh").addEventListener("click", doCollect);
initCharts();
tickClock();
setInterval(tickClock, 1000);
pollVps();
setInterval(pollVps, 15000);
loadLatest();
setInterval(loadLatest, 60000);
api("/api/collect", { method: "POST" }).then(loadLatest).catch(() => loadLatest());
