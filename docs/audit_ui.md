# Audit UI — Spot Demo (dynamique + graphiques)

Date : 2026-07-04  
UI : `http://localhost:8080/`  
Preuves : `docs/proofs/m8_audit_ui.json`, `docs/proofs/m8_charts.json`

## Valeurs live (proxy UI ↔ Binance)

| Champ | Match |
|---|---|
| mark_price / live-price header | oui (écart < 0,5 %) |
| capital.quote_free | oui exact |
| capital.base_total | oui exact |
| config active | oui |

## Graphiques (points = lignes DB)

| Graphique | Endpoint | Preuve |
|---|---|---|
| Prix + range grille | `/api/charts/price` | ids API ⊆ SQL `price_ticks` (`m8_charts.json`) |
| PnL cumulé | `/api/charts/pnl` | formule exposée, points depuis `pnl_snapshots` |
| Histogramme cycles | `/api/charts/cycles` | bars depuis `cycles` closed |
| Latence Exchange | Supervision metrics | Chart.js + liste brute |

Capture t1 → t2 : points prix passent de N à N+k ; nouveaux ids présents en SQL (`api_contains_sql_ids: true`).

## Dynamisme

- WebSocket `/ws` pousse le status (pas de bouton refresh obligatoire).
- Flash vert/rouge sur `#live-price` et PnL gross au changement.
- Prix actif toujours dans le header (`#live-price`).
- Tooltip graphique expose `id`, `price`, `ts` bruts.

## États insuffisants

Si < 2 points : message « données insuffisantes pour l'instant » (pas de courbe inventée).

---

## Conformité visuelle Binance (grille + tableau)

Date : 2026-07-04T16:26 UTC  
Preuves : `docs/proofs/m8_binance_visual.json`, captures `m8_binance_visual_screenshot.png`, `m8_binance_chart_screenshot.png`

### Élément 1 — Graphique prix + 20 niveaux

| Exigence | Implémentation | Preuve |
|---|---|---|
| Ligne horizontale par niveau (20) | Plugin Chart.js `ultiumGridOverlay` — trait pleine largeur | `chart_levels_count: 20` |
| Vert BUY / rouge SELL Limit | `side === 'BUY'` → `#22c55e` ; `SELL` → `#ef4444` | `level_compare[].color_rule` |
| Étiquette prix + qty | Label droite : `Buy 62774.76 — 0.00397` / `Limit 64346.09 — 0.00397` | capture chart + API `levels[].price/quantity` |
| Niveau inactif grisé | `visual: inactive` → trait pointillé `rgba(148,163,184,0.5)` | `all_levels_visual_match: true` |
| Marqueur fill sur courbe | Cercle `B`/`S` au prix+timestamp trade | 1 fill initial BUY (`m8_binance_chart_screenshot.png`) |
| Données = DB | `price_chart_api === price_db` pour les 20 niveaux | `all_levels_price_match: true` |

### Élément 2 — Tableau récapitulatif (onglet Running)

Colonnes dans l'ordre demandé : **Pair | Time Created | Total Investment | Total Profit | Grid Profit | Floating Profit | Total Matched Trades | Price Range | Duration | Number of Grids | Action**

| Colonne | Source (pas de calcul parallèle) | Vérification |
|---|---|---|
| Pair | `grid_recap.pair` = `config.symbol` | `recap_compare.pair` |
| Time Created | `cycles.opened_at` | cycle_id=2 |
| Total Investment | `config.capital_usdt` | 5000.00 |
| Total Profit | `grid.gross_pnl` | `recap_compare.total_profit` |
| Grid Profit | `grid.grid_profit` | match |
| Floating Profit | `grid.floating_profit` (WS tick) | match |
| Total Matched Trades | `COUNT(trades WHERE level_index IS NOT NULL)` — **exclut** achat initial | `m3_sell_openorders_proof.json` |
| Price Range | min/max prix tous niveaux DB | `61360.56 — 64346.09` |
| Duration | `now - opened_at` | affiché `1m 13s` |
| Number of Grids | `config.num_levels` | 20 |
| Action | boutons ▶ ⏸ ⏹ → Start/Stop/Panic existants | pas de 2ᵉ mécanisme |

Script reproductible : `scripts/ui_binance_visual_proof.py`  
Résultat : `all_conforme: true` dans `m8_binance_visual.json`.

**Capture pleine page incluant le graphique (Élément 1)** : `docs/proofs/m3_element1_full_page_screenshot.png` (3400px, section « Graphique prix + niveaux de grille » visible avec 20 lignes + marqueur B).

**10 SELL dans openOrders (Module 3)** : `docs/proofs/m3_sell_openorders_proof.json` — chaque `order_id_db` présent dans `GET openOrders` avec `status=NEW`.

**Sémantique Total Matched Trades** : compte uniquement les fills de **paliers grille** (`level_index` non null). L'achat initial (`orderId=45968631089`, `level_index` vide) est tracé à part dans `grid_recap.initial_inventory_buy` et sur le graphique (marqueur B), mais **n'incrémente pas** Total Matched Trades.
