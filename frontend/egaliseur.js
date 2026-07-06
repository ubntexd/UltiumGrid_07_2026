const API = window.location.origin.includes("3000")
  ? "http://localhost:8000"
  : "";

const EGALISEUR_FIELDS = [
  ["trailing_delta_pct", "Trailing delta %"],
  ["limit_margin_pct", "Marge limite %"],
  ["activation_recovery_pct", "Activation reprise %"],
  ["hard_stop_pct", "Stop dur sac %"],
  ["max_hold_days", "Durée max (jours, défaut 1 = 24 h)"],
  ["daily_loss_cap_usd", "Plafond perte forcée USD/j"],
];

function pnlClass(v) {
  const n = Number(v);
  if (Number.isNaN(n)) return "";
  return n >= 0 ? "pos" : "neg";
}

function fmt(v, d = 2) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  return Number.isNaN(n) ? String(v) : n.toFixed(d);
}

function fmtDuration(sec) {
  if (sec == null) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
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

function renderModeBanner(status) {
  const el = document.getElementById("mode-banner");
  const mode = status.operation_mode || "test_only";
  const paused = status.paused;
  let cls = "mode-banner ";
  let title = "";
  let detail = "";
  if (paused) {
    cls += "mode-paused";
    title = "Bot Égaliseur : EN PAUSE";
    detail = "Aucune nouvelle action automatique.";
  } else if (mode === "continuous") {
    cls += "mode-continuous";
    title = "Bot Égaliseur : ACTIF EN CONTINU";
    detail =
      "Veille autonome permanente — agit sur tout sac open sans armement préalable.";
  } else {
    cls += "mode-test";
    const armed = status.test_armed_bag_ids || [];
    title = "Bot Égaliseur : MODE TEST UNIQUEMENT";
    detail = armed.length
      ? `Tests ponctuels autorisés sur sac(s) armé(s): ${armed.join(", ")}`
      : "Sacs Bot 1 journalisés uniquement — armer un sac pour un test réel isolé.";
  }
  el.className = cls;
  el.innerHTML = `<strong>${title}</strong><span>${detail}</span>`;
}

async function loadEgaliseur() {
  const [status, active, sold, actions, cfgRes] = await Promise.all([
    api("/api/egaliseur/status"),
    api("/api/egaliseur/bags?scope=active"),
    api("/api/egaliseur/bags?scope=sold"),
    api("/api/egaliseur/actions?limit=50"),
    api("/api/egaliseur/config"),
  ]);

  renderModeBanner(status);

  document.getElementById("egaliseur-meta").innerHTML = `
    <p>Sacs actifs: ${status.active_bags} | Vendus par égaliseur: ${status.sold_by_egaliseur}
    | Heartbeat: ${status.heartbeat?.ts || "—"}</p>
  `;

  document.querySelector("#egaliseur-active-table tbody").innerHTML = active
    .map(
      (b) => `<tr>
      <td>${b.id}</td><td>${b.status}</td><td>${fmt(b.quantity, 4)}</td>
      <td>${fmt(b.entry_price)}</td><td>${b.mark_price != null ? fmt(b.mark_price) : "—"}</td>
      <td class="${pnlClass(b.floating_pnl)}">${b.floating_pnl != null ? fmt(b.floating_pnl) : "—"}</td>
      <td>${b.mechanism ?? "—"}</td>
      <td>${fmtDuration(b.time_remaining_s)}</td>
      <td>${(status.test_armed_bag_ids || []).includes(b.id) ? "✓ armé" : "—"}</td>
    </tr>`
    )
    .join("") || `<tr><td colspan="9">Aucun sac géré</td></tr>`;

  document.querySelector("#egaliseur-sold-table tbody").innerHTML = sold
    .map(
      (b) => `<tr>
      <td>${b.id}</td><td>${b.exit_reason ?? b.status}</td>
      <td>${fmt(b.entry_price)}</td><td>${b.sold_price != null ? fmt(b.sold_price) : "—"}</td>
      <td class="${pnlClass(b.realized_pnl)}">${fmt(b.realized_pnl)}</td>
      <td>${b.sold_at ?? "—"}</td>
    </tr>`
    )
    .join("") || `<tr><td colspan="6">Aucune vente</td></tr>`;

  document.querySelector("#egaliseur-actions-table tbody").innerHTML = actions
    .map(
      (a) => `<tr>
      <td>${a.id}</td><td>${a.action}</td><td>${a.bag_id ?? "—"}</td>
      <td>${a.message}</td><td>${a.created_at ?? "—"}</td>
    </tr>`
    )
    .join("") || `<tr><td colspan="5">Aucune action</td></tr>`;

  const cfg = cfgRes.config || {};
  const form = document.getElementById("egaliseur-config-form");
  form.innerHTML = EGALISEUR_FIELDS.map(
    ([k, label]) =>
      `<label>${label} <input name="${k}" type="number" step="any" value="${cfg[k] ?? ""}" /></label>`
  ).join("");
  const bounds = cfgRes.trailing_delta_bounds_bips;
  document.getElementById("egaliseur-config-msg").textContent = bounds
    ? `Bornes trailingDelta Binance: ${bounds.min}–${bounds.max} bips`
    : "";
}

document.getElementById("btn-egaliseur-pause")?.addEventListener("click", async () => {
  await api("/api/egaliseur/pause", { method: "POST", body: "{}" });
  loadEgaliseur();
});

document.getElementById("btn-egaliseur-resume")?.addEventListener("click", async () => {
  await api("/api/egaliseur/resume", { method: "POST", body: "{}" });
  loadEgaliseur();
});

document.getElementById("btn-mode-test")?.addEventListener("click", async () => {
  await api("/api/egaliseur/mode", {
    method: "POST",
    body: JSON.stringify({ operation_mode: "test_only" }),
  });
  loadEgaliseur();
});

document.getElementById("btn-mode-continuous")?.addEventListener("click", async () => {
  const ok = window.confirm(
    "Passer en mode CONTINU ? Le Bot Égaliseur agira automatiquement sur tout nouveau sac. " +
      "Interdit pendant le run v2 du Bot 1 sauf feu vert explicite."
  );
  if (!ok) return;
  await api("/api/egaliseur/mode", {
    method: "POST",
    body: JSON.stringify({ operation_mode: "continuous" }),
  });
  loadEgaliseur();
});

document.getElementById("btn-test-arm")?.addEventListener("click", async () => {
  const bagId = Number(document.getElementById("test-bag-id").value);
  if (!bagId) return;
  await api("/api/egaliseur/test/arm", {
    method: "POST",
    body: JSON.stringify({ bag_id: bagId }),
  });
  loadEgaliseur();
});

document.getElementById("btn-test-disarm")?.addEventListener("click", async () => {
  const bagId = document.getElementById("test-bag-id").value;
  const q = bagId ? `?bag_id=${bagId}` : "";
  await api(`/api/egaliseur/test/disarm${q}`, { method: "POST", body: "{}" });
  loadEgaliseur();
});

document.getElementById("btn-egaliseur-save-config")?.addEventListener("click", async () => {
  const form = document.getElementById("egaliseur-config-form");
  const body = {};
  EGALISEUR_FIELDS.forEach(([k]) => {
    const el = form.querySelector(`[name="${k}"]`);
    if (el && el.value !== "") body[k] = Number(el.value);
  });
  try {
    await api("/api/egaliseur/config", { method: "POST", body: JSON.stringify(body) });
    document.getElementById("egaliseur-config-msg").textContent = "Config enregistrée";
    loadEgaliseur();
  } catch (e) {
    document.getElementById("egaliseur-config-msg").textContent = JSON.stringify(e);
  }
});

loadEgaliseur();
loadInstanceBranding();
setInterval(loadEgaliseur, 15000);

async function loadInstanceBranding() {
  try {
    const m = await api("/api/instance");
    const el = document.getElementById("instance-brand");
    if (el && m.instance_label) el.textContent = `${m.instance_label} · Égaliseur`;
    const symEl = document.getElementById("instance-symbol-badge");
    if (symEl && m.trading_symbol) {
      symEl.hidden = false;
      symEl.textContent = m.trading_symbol;
      if (m.symbol_disclaimer) symEl.title = m.symbol_disclaimer;
    }
    if (m.instance_label) {
      const sym = m.trading_symbol ? ` · ${m.trading_symbol}` : "";
      document.title = `${m.instance_label}${sym} — Bot Égaliseur`;
    }
    if (m.accent_color) {
      document.documentElement.style.setProperty("--accent", m.accent_color);
      document.body.classList.add(`instance-${m.instance_id || "default"}`);
    }
  } catch (_) {}
}
