# Audit de conformité par module

Référence : `docs/spec.md`.

## Module 1 — Connecteur Binance

| Exigence | Statut | Preuve |
|---|---|---|
| Prix temps réel WebSocket | conforme | `m1_websocket_reconnect.json` |
| Placement / annulation ordres | **non vérifié** | Binance `-1007` / `502` sur `POST /fapi/v1/order` |
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
| Placement initial / replacement fills | **non vérifié** live | dépend ordres |
| PnL Grid+Floating+funding | code présent | formules dans `engine/grid.py` |
| Trigger +15 fermeture/recentrage | code présent | **non vérifié** live |

## Module 4 — Coupe progressive

| Exigence | Statut | Preuve |
|---|---|---|
| Palier 10 → 50 % | conforme (unit) | `test_cut_at_level_10_and_14` |
| Palier 14 → 100 % + recentrage | conforme (unit) | idem |
| Réarmement 2 paliers / délai | conforme (unit) | réarmement délai testé |

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

## Module 8 — UI

| Exigence | Statut | Preuve |
|---|---|---|
| 4 onglets + config/market | conforme | `frontend/index.html` HTTP 200 |
| Bandeau marge / contrôles | conforme | DOM + proxy `/api/running` |
| Audit valeur par valeur | partiel | `docs/audit_ui.md` |

## Module 9 — Reprise crash

| Exigence | Statut | Preuve |
|---|---|---|
| restore_state + reconcile orders | code présent | **non vérifié** (pas d’ordres ouverts possibles) |
