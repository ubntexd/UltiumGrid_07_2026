# Audit final — UltiumGrid Spot Demo

Date : 2026-07-04  
Environnement : `https://demo-api.binance.com` / `wss://demo-stream.binance.com/ws`

## Conforme avec preuve

| Exigence | Preuve |
|---|---|
| Spot public ping/ticker/exchangeInfo | `spot_public_audit.json` (vision) + live demo-api |
| URL correcte demo-api (clés demo) | `spot_url_fix.json` |
| Place / cancel ordre limite | `m1_place_cancel_order.json` |
| Account balances | `m1_account_balances_spot.json` |
| WebSocket bookTicker + reconnect | `m1_websocket_reconnect.json` |
| Anti-doublon unit + integration | tests M1 antiduze |
| retry_exhausted / grid_level_incomplete | `m1_retry_exhausted.json` |
| DB SQL direct | `m2_database_sql.json` |
| Grille BUY-only, croisement Binance↔DB | `m3_grid_integration.json` |
| Coupe qty réelle + incomplete | `m4_cut_incomplete_spot.json` |
| Vente sac | `m5_bag_sell_spot.json` |
| Panic close réel | `m6_panic_spot.json` |
| API capital/running/pnl vs Binance | `m7_api_crosscheck.json` |
| Config 3 params + reject bornes | `m7bis_config_spot.json` |
| Viabilité économique formules | `test_viability_formula_manual` (notionnel 250 = moteur) + API viability |
| Reprise crash sans doublon ordres | `m9_crash_recovery.json` |
| Audit UI valeurs = sources | `m8_audit_ui.json` |
| Courbes prix/PnL/cycles/latence (points = DB) | `m8_charts.json` |
| Prix permanent header + flash WS | DOM `#live-price`, Chart.js |
| Docker compose 5 services (+ supervisor) | `docker compose ps` Up |
| Cold start `down -v && up --build` | `docker_cold_start.json` (2026-07-04) |
| Superviseur heartbeat / recon / exchange | `m10bis_normal_cycle.json`, `m10bis_bot_unresponsive.json`, `m10bis_recon_mismatch.json` |
| Onglet UI Supervision | `/api/supervision` + tab DOM |

## Écarts assumés

| Écart | Justification |
|---|---|
| Spec dérivée du prompt Spot v2 (fichier cahier séparé absent) | `docs/spec.md` |
| `GET /api/v3/order` parfois `-2013` sur demo alors que openOrders OK | Vérif via openOrders |
| Dust BTC résiduel après panic (commissions) | Notional sous min après frais |
| Cycle +15 live non déroulé jusqu’au trigger | ~120 grilles config défaut ; séquence ouverture + M3 prouvés |
| `duplicate_avoided` ordre accepté puis `-1007` réel | Non reproduit sur demo-api ; unit + timeout_not_found seulement |
| Notionnel viabilité 500 vs 250 (moteur) | **Corrigé** 2026-07-04 — `m7bis_viability_notional_fix.json` |

## Non vérifiable / hors scope

| Point | Note |
|---|---|
| Futures Demo écriture | Abandonné (migration Spot) — `spec_v1_futures_deprecated.md` |
| Latence réseau extrême | Non testé |
| Frais BNB réels avec solde BNB > 0 | Compte demo sans BNB ; rejet BNB discount prouvé si solde 0 |
| Superviseur sur plusieurs heures sans fausse alerte | Cycle normal prouvé (échantillons latence) ; pas de run multi-heures dans cette session |
| emergency_action panic auto | Désactivé par défaut (Q6) — non testé volontairement |

## Verdict

**Projet opérationnel en Spot Demo** pour connecteur, grille, risque, sacs, garde-fous, API, UI, config, reprise crash — avec preuves d’exécution jointes.

Ne pas utiliser en production mainnet sans nouvelles clés et revue de sécurité.
