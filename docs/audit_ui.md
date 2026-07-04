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
