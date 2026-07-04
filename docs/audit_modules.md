# Audit de conformité par module

Référence : `docs/spec.md`.

## Module 1 — Connecteur Binance

| Exigence | Statut | Preuve |
|---|---|---|
| Prix temps réel WebSocket | conforme | `m1_websocket_reconnect.json` |
| Placement / annulation ordres | **non vérifié** (live fill) | Binance `-1007`/`502` persistants |
| Anti-doublon `duplicate_avoided` (**unit**) | conforme | `test_1007_duplicate_avoided_does_not_resend` — `-1007` **injecté/mocké** |
| Anti-doublon vérif réelle (**integration**) | conforme (timeout_not_found) | `m1_antiduze_post_1007.json` — `-1007` **réel** testnet + openOrders/allOrders réels |
| `duplicate_avoided` avec ordre **réellement accepté** puis `-1007` | **non vérifié** | impossible tant que POST n'accepte aucun ordre ; démo hybrid (openOrders réel + entrée injectée) dans la même preuve, **non présentée comme integration pure** |
| `retry_exhausted` + `grid_level_incomplete` | conforme (forcé) | `m1_retry_exhausted.json` — POST forcé `-1007`, verify réel |
| Journal `order_attempts` | conforme | table DB + SQL dans les preuves |
| `-1008` sur fermeture = anomalie HP | conforme (**unit**) | `test_1008_on_priority_close_is_anomaly` |
| Badge UI « non placé » | conforme (source + API) | `app.js` `badge-missing` + `incomplete_levels` dans status |
| Lecture position | conforme (API répond) | `open_positions=0` dans auth proof |
| Solde / marge | conforme | `availableBalance=5000` |
| Funding rate | conforme | `m1_account_funding.json` |
| Reconnexion WS après kill | conforme | kill + reconnect + stream_helper_msgs=2 |

## Module 2 — Base de données

| Exigence | Statut | Preuve |
|---|---|---|
| Tables cycles, trades, bags, bot_state | conforme | modèles + SQL |
| Insert / read / update | conforme | `m2_database_sql.json` |
| Vérif SQL directe | conforme | `SELECT`/`UPDATE` hors ORM |

## Module 3 — Moteur de grille

| Exigence | Statut | Preuve |
|---|---|---|
| 20 niveaux arithmétiques pas 0,25–0,26 % | conforme (unit) | `test_compute_20_levels_arithmetic` |
| Placement initial / replacement fills | conforme | `m3_open_sequence.json` (BUY+SELL avec `order_id`) |
| PnL Grid+Floating (pas funding) | conforme | formules dans `engine/grid.py` |
| Floating recalculé à chaque tick WS (sans fill) | conforme | `m3_floating_live_tick.json` |
| Trigger +15 fermeture/recentrage | code présent | **non vérifié** live cycle complet organique |
| §2bis idle_recenter_no_fill | conforme | `m3_open_sequence.json` T2 (seuil test 0.05 min) |
| §2bis forced_sell_stuck_level | conforme | `m3_open_sequence.json` T3 |

## Séquence d'ouverture de cycle (achat initial + recentrage)

Preuve unique : `docs/proofs/m3_open_sequence.json` (grille test 4 paliers, `capital_usdt=100`).

| Test | Résultat | Preuve clé |
|---|---|---|
| **T1** Start — achat marché + BUY/SELL limites | **conforme** | `initial_inventory_buy` orderId réel, 2 BUY + 2 SELL `open` avec `order_id`, `fees_paid` ≥ 1, 1 cycle `open` |
| **T2** Cas A idle recenter | **conforme** | cycle 14→15, `idle_recenter_no_fill`, **nouvel** `initial_buy`, SELL tous avec `order_id` (seuil temporaire 0.05 min) |
| **T3** Cas B stuck sell | **conforme** | `forced_sell_stuck_level` en `order_attempts`, palier `status=filled` |
| **T4** Anti-doublon interaction | **conforme** | Start pendant `_opening_cycle` → `already_running` ; Start actif → pas de 2ᵉ cycle ; 1 seul `open` |

Mécanisme unifié : `GridEngine.open_grid` (étapes 1–3) + `BotRunner._open_new_cycle` (réservation DB **avant** achat marché, lock `_opening_cycle`). Capital : moitié inventaire SELL / moitié limites BUY.

## Module 4 — Coupe progressive

| Exigence | Statut | Preuve |
|---|---|---|
| Palier 10 → 50 % | conforme (**unit**) | `test_cut_at_level_10_and_14` |
| Palier 14 → 100 % + recentrage | conforme (**unit**) | idem |
| Réarmement 2 paliers / délai | conforme (**unit**) | réarmement délai testé |
| Coupe sur qty **réelle** si paliers incomplets | conforme (**unit**) | `test_cut_uses_real_qty_with_incomplete` |
| `cut_with_incomplete_grid` + alerte écart >10 % | code présent | **non vérifié** live (dépend position réelle) |

## Module 5 — Sacs

| Exigence | Statut | Preuve |
|---|---|---|
| Registres DB | conforme | table `bags` |
| Réconciliation | code présent | `BagManager.reconcile` |
| Vente manuelle | API queue OK | exécution live **non vérifiée** |
| Règle marge sacs | code présent | **non vérifié** live |

## Module 6 — Garde-fous

| Exigence | Statut | Preuve |
|---|---|---|
| Stop dur -8 % | conforme (unit) | `test_hard_stop_and_circuit_breaker` |
| Circuit breaker -40 USD | conforme (unit) | idem |
| Alertes (DB events) | code présent | table `alert_events` |
| Panic close | commande API queue | exécution live **non vérifiée** |

## Module 7 — Backend

| Exigence | Statut | Preuve |
|---|---|---|
| Running / History / PnL / Bags / marge | conforme | `docker_api_stack.json` |
| WebSocket serveur | code présent | `/ws` monté ; broadcast loop active |

## Module 7bis — Config

| Exigence | Statut | Preuve |
|---|---|---|
| Params exposés + défauts spec | conforme | `/api/config` |
| Validation bornes | conforme | levier 99 → erreur |
| Persistance configs | conforme | SQL `configurations` id=3 active leverage=3 |
| Confirmation cycle actif | code présent | modes `wait_cycle` / `close_now` |
| Simulation historique | conforme (message insuffisant) | < 3 cycles → `insufficient_data` |

## Module 7ter — Marché / analytics

| Exigence | Statut | Preuve |
|---|---|---|
| Liste actifs + prix/var/vol | conforme | `/api/market` |
| tickSize/stepSize réels | conforme | BTC `0.10`/`0.0001`, ETH `0.01`/`0.001` |
| Indicateurs PnL formules | conformes (doc) | `/api/pnl` champ `formulas` |
| Prix UI = Binance même instant | conforme (session test) | BTC 62533.0 = 62533.00 |

## Module 7quater — Fees réels

| Exigence | Statut | Preuve |
|---|---|---|
| `GET /api/v3/myTrades` → `fees_paid` | conforme | `m7quater_fees_mytrades.json` |
| `/api/fees` + onglet Fees | conforme | API 5 rows, total USDT match |
| Commission UI = brut myTrades | conforme | trade `254232167` commission `9e-08` BTC → `0.005625` USDT |
| Activation paiement BNB côté compte | **non vérifié** | réglage compte Binance (BNB free=0) ; bot affiche solde seulement |
| Écart théorique/réel par cycle | partiel | `by_cycle` exposé ; cycles clos avec fees liés encore rares |

## Module 8 — UI

| Exigence | Statut | Preuve |
|---|---|---|
| 4 onglets + config/market | conforme | `frontend/index.html` HTTP 200 |
| Bandeau marge / contrôles | conforme | DOM + proxy `/api/running` |
| Audit valeur par valeur | partiel | `docs/audit_ui.md` |

## Module 9 — Reprise crash

| Exigence | Statut | Preuve |
|---|---|---|
| restore_state + reconcile orders | conforme | `m9_crash_recovery.json` |
| Un seul cycle `open` après restart | conforme | `audit_ui_bugfix.md` §6, `m_cycles_duplicate_fix.json` |
