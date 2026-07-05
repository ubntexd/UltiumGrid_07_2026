# Audit de conformité par module

Référence : `docs/spec.md`.

## Module 1 — Connecteur Binance

| Exigence | Statut | Preuve |
|---|---|---|
| Prix temps réel WebSocket | conforme | `m1_websocket_reconnect.json` |
| Placement / annulation ordres | conforme | `m1_place_cancel_order.json` — LIMIT NEW puis cancel, openOrders avant/après |
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
| Placement initial / replacement fills | conforme | `m3_open_sequence.json` (4 paliers) + `m3_open_sequence_clarifications.json` T1 prod (20 paliers) |
| PnL Grid+Floating (pas funding) | conforme | formules dans `engine/grid.py` |
| Floating recalculé à chaque tick WS (sans fill) | conforme | `m3_floating_live_tick.json` |
| Trigger +15 fermeture/recentrage | **en cours** (run organique 48h) | `m3_organic_long_run/manifest.json` — pas de seuil réduit |
| §2bis idle_recenter_no_fill | conforme (impl + intégration) | `m3_recenter_cas_ab_module.json` + clarifications T2 ; timers prod 20 min = run organique |
| §2bis forced_sell_stuck_level | conforme (impl + intégration) | `m3_recenter_cas_ab_module.json` + clarifications T3 ; timers prod 15 min = run organique |

## Séquence d'ouverture de cycle (achat initial + recentrage)

### Preuve initiale (mini-grille rapide)

`docs/proofs/m3_open_sequence.json` — **grille test 4 paliers**, `capital_usdt=100`, `step_pct=0.3` (pas la taille production).

| Test | Résultat | Preuve clé |
|---|---|---|
| **T1** Start — achat marché + BUY/SELL limites | **conforme** | `initial_inventory_buy` orderId réel, 2 BUY + 2 SELL `open` avec `order_id`, `fees_paid` ≥ 1, 1 cycle `open` |
| **T2** Cas A idle recenter | **conforme** | cycle 14→15, `idle_recenter_no_fill`, **nouvel** `initial_buy`, SELL tous avec `order_id` (seuil temporaire 0.05 min) |
| **T3** Cas B stuck sell | **conforme** | `forced_sell_stuck_level` en `order_attempts`, palier `status=filled` |
| **T4** Anti-doublon interaction | **conforme** | Start pendant `_opening_cycle` → `already_running` ; Start actif → pas de 2ᵉ cycle ; 1 seul `open` |

### Clarifications validées (2026-07-04)

`docs/proofs/m3_open_sequence_clarifications.json` — `all_conforme: true`

| Point | Conclusion | Preuve |
|---|---|---|
| **T1/T4 taille grille** | Tests originaux = **4 paliers** ; production **20 paliers** revalidé séparément | T1 prod : 10 BUY + 10 SELL Binance, `num_levels=20`, `step_pct=0.25`, `capital_usdt=500`, `initial_buy` orderId `459658…` |
| **T2 condition prix** | Prix **hors fourchette** au déclenchement (`mark=66329` > `range_high=63171`) ; pas seulement le minuteur | `trigger.price_out_of_range=true`, `out_direction=above_high`, `db_verify_json` |
| **T2 négatif** | Minuteur expiré (30 min) **sans** prix hors fourchette → **pas** de recentrage | `t2_negative_in_range_no_recenter` : `recentered=false`, `_out_of_range_since` remis à `null` |
| **T3 WS vs SELL** | WS réel `62882` **<** SELL `62976` → pas de fill naturel ; vente via **MARKET** `forced_sell_stuck_level` | `comparison_ws_vs_sell`, `fill_type=MARKET`, `order_attempt_verify_json` |

Test unitaire négatif T2 : `test_idle_recenter_skips_when_in_range_despite_expired_timer` dans `test_m3_idle_recenter_unit.py`.

Script reproductible : `scripts/m3_open_sequence_clarifications.py`.

### Module recentrage — preuve consolidée (2026-07-04)

`docs/proofs/m3_recenter_cas_ab_module.json` — `conforme: true`

- Implémentation : `bot_runner._check_idle_recenter` (Cas A) + `_check_stuck_sells` (Cas B), appelées à chaque `tick()`
- Config production live : `idle_recenter_min=20`, `stuck_sell_min=15`
- Tests unitaires : `test_m3_idle_recenter_unit.py` (3 passed)
- Intégration (timers 0.05 min documentés) : `m3_open_sequence_clarifications.json`
- Déclenchement à 20/15 min sans accélération : **run organique** `scripts/m3_organic_long_run.py` (48h)

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
| Vente manuelle sac | conforme | `m5_bag_sell_spot.json` — MARKET FILLED, base avant/après |
| Règle capital immobilisé sacs | code présent | **non vérifié** live (seuil réduction grille) |

## Module 6 — Garde-fous

| Exigence | Statut | Preuve |
|---|---|---|
| Stop dur -8 % | conforme (unit) | `test_hard_stop_and_circuit_breaker` |
| Circuit breaker -40 USD | conforme (unit) | idem |
| Alertes (DB events) | code présent | table `alert_events` |
| Panic close | conforme | `m6_panic_spot.json`, `m_panic_real_sell.json`, `m_control_buttons.json` — vente MARKET réelle, openOrders vidés |

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
| 4 onglets + config/market/fees/supervision | conforme | `frontend/index.html` |
| Bandeau marge / contrôles | conforme | DOM + proxy `/api/running` |
| **Graphique 20 niveaux** (lignes H vert/rouge + labels + fills B/S) | **conforme** | `m8_binance_visual.json` + `m8_binance_visual_screenshot.png` |
| **Tableau récap Binance** (11 colonnes Running) | **conforme** | `grid_recap` API + capture |
| Audit valeur par valeur | conforme (grille+tableau) | `docs/audit_ui.md` § Conformité visuelle Binance |

## Module 9 — Reprise crash

| Exigence | Statut | Preuve |
|---|---|---|
| restore_state + reconcile orders | conforme | `m9_crash_recovery.json` |
| Un seul cycle `open` après restart | conforme | `audit_ui_bugfix.md` §6, `m_cycles_duplicate_fix.json` |

## Propagation `initial_inventory_buy` vs fills de grille

Règle : **exclure** l'achat initial des stats de performance grille (matched trades, win rate dérivé de trades) ; **inclure** son coût réel dans frais et viabilité économique.

Preuve reproductible : `scripts/m_initial_vs_grid_propagation_proof.py` → `docs/proofs/m_initial_vs_grid_propagation.json`

| Endroit | Inclus / exclu | Requête ou calcul réel | Justification |
|---|---|---|---|
| **Total Matched Trades** (`grid_recap`) | **Round-trips complets** | `total_matched_trades_from_trades()` = `roundtrip_count` (`grid_profit.py`) — `backend/app/main.py` | Appariement BUY@i+SELL@i+1 ; exclut achat initial **et** SELL d'inventaire initial |
| **Achat initial exposé à part** | Référence | `initial_inventory_buy` + `Trade WHERE level_index IS NULL` — L98-134 | Traçabilité sans fausser le compteur grille |
| **Marqueur B (graphique prix)** | **Inclus** (visuel) | `chart_price` : tous les `Trade` du cycle + fallback `cycle_meta.initial_buy` — L801-839 | Référence visuelle uniquement, pas une stat |
| **PnL Analysis** — `win_rate`, `avg_win`, `avg_loss`, `avg_cycle_duration_sec` | **Exclu** (indirect) | `Cycle WHERE status='closed'` — L489-502 ; formules L534-538 | Agrégats sur **cycles clôturés** (`net_pnl`, durées), pas sur `trades` |
| **Histogramme cycles** (onglet History) | **Exclu** | `GET /api/charts/cycles` → `Cycle WHERE status='closed'` — L924-950 ; UI `app.js` L304-347 | Une barre = `cycle.net_pnl`, pas un trade individuel |
| **Courbe PnL** (`/api/charts/pnl`) | **Exclu** (indirect) | `PnlSnapshot.cumulative_pnl` = closed_cycles + grid + bags — L876-912 | Pas de lecture table `trades` |
| **Frais réels** (`fees_paid` / onglet Fees) | **Inclus** | `_record_fees_for_order` appelé sur `initial_buy.orderId` — `bot_runner.py` L449 ; `FeePaid WHERE cycle_id` — L864-871 | Commission achat marché = coût réel du cycle |
| **net_pnl cycle à la clôture** | **Inclus** (frais) | `cycle.net_pnl = gross_pnl - SUM(fees_paid.cycle_id)` — `bot_runner.py` L864-871 | Tous frais cycle, dont achat initial |
| **Viabilité économique** (Module 7bis) | **Inclus** (fixe) | `fees_initial_inventory = (capital_usdt/2)*fee_rate` — `viability.py` ; `net_at_gross_threshold = grids*net_per_grid - fees_initial_inventory` | Coût fixe par ouverture/recentrage en plus du coût/grille |
| **Idle recenter** (pas de fill grille) | **Exclu** | `_last_fill_at is None` après ouverture — `bot_runner.py` L666-668 | L'inventaire initial ne compte pas comme fill grille |
| **Sync fills grille** | **Inclus** (grille) | `Trade(level_index=lv.index)` — `bot_runner.py` L629-637 | Seuls les fills LIMIT ont un `level_index` |
| **Test M2 DB** | N/A (fixture) | `INSERT Trade` sans `level_index` — `test_m2_database.py` | Test unitaire schéma, pas prod |
| **Script preuve UI** | **Exclu** | `COUNT(... level_index IS NOT NULL)` — `ui_binance_visual_proof.py` | Aligné sur sémantique matched trades |

### Preuves live (cycle ouvert id=2, 2026-07-04)

**Frais** — achat initial **inclus** dans `fees_paid` :

```sql
SELECT cycle_id, order_id, commission_usdt FROM fees_paid ORDER BY id;
-- 2|45968631089|0.005028488   ← initial_inventory_buy
-- 2|45968631100|0.2498396    ← fill grille SELL palier 10
```

**Trades** — distinction `level_index` :

```sql
SELECT id, side, order_id, level_index FROM trades ORDER BY id;
-- 2|BUY|45968631089|         ← achat initial (exclu matched)
-- 3|SELL|45968631100|10      ← fill grille (inclus matched)
```

**PnL** — `GET /api/pnl` : `cycles_total` depuis `Cycle.status='closed'` uniquement ; `win_rate = cycles_won/cycles_total` (pas de jointure `trades`).

**Histogramme** — `GET /api/charts/cycles` : `bars[].net_pnl` par cycle clos ; marqueur `B` uniquement dans `fills` du graphique prix (`level_index: null`).

**Viabilité** — notionnel aligné moteur `(capital/2)/(num_levels/2)` = **250 USDT** ; `grids_to_cycle=120` ; `net_at_gross_threshold=12.5` ; `fees_initial_inventory=2.5` (config 5000/20/0.25%).

### Symétrie BUY initial / SELL d'inventaire initial (2026-07-05)

**Preuve SQL cycle 2** :

```sql
SELECT id, side, level_index, order_id FROM trades WHERE cycle_id=2 ORDER BY id;
-- 2|BUY|       |45968631089  ← achat initial (exclu)
-- 3|SELL|10   |45968631100  ← inventaire initial, level_index renseigné
-- 4|SELL|11   |45968631101
-- 5|SELL|12   |45968631102
```

**Incohérence confirmée** : ancienne règle `COUNT(level_index IS NOT NULL)` comptait **3** SELL d'inventaire initial alors que le BUY initial était exclu.

**Correction** : `total_matched_trades_from_trades()` dans `grid_profit.py` — même `matched_ledger` que Grid Profit → **round-trip complet uniquement**.

| Cycle | Ancien compteur | Nouveau | Round-trips réels |
|---|---|---|---|
| 2 (clos) | 3 | **0** | 0 |
| 3 (ouvert, live) | 27 fills | **12** | 12 |

Preuve : `docs/proofs/m3_matched_trades_symmetry_correction.json`  
Tests : `test_total_matched_trades_excludes_initial_inventory_sells`, `test_total_matched_trades_counts_roundtrips_not_raw_fills`

**Propagation** : `grid_recap` / UI cycle ouvert uniquement (pas de colonne DB sur cycles clos). M7ter win_rate et histogramme M8 utilisent `cycle.net_pnl`, pas `total_matched_trades` → **non impactés**.

## Correction Grid Profit — appariement round-trip (2026-07-05)

### Cause racine confirmée

**Avant** (`grid.py` `on_fill`, supprimé) :

```python
# FAUX — comptabilité position globale (entry_avg incluant inventaire initial)
realized = direction * (fill_price - self.state.entry_avg) * closed
self.state.grid_profit += realized
```

Chaque SELL était valorisé contre le **coût moyen global** (~63 306 USDT, inventaire initial + BUYs), pas contre le BUY du palier `i` juste en dessous. En marché baissier, des SELL à 62 752 imputés à un entry_avg ~63 000 produisaient un Grid Profit **négatif** alors que chaque round-trip grille est structurellement positif.

**Ce n'était pas** `SUM(SELL) - SUM(BUY)` global, mais un effet équivalent sur les 4 BUY « orphelins » : leurs coûts contaminaient `entry_avg` et faussaient chaque SELL subséquent.

### Après (conforme Binance)

Nouveau module `bot/ultiumgrid/engine/grid_profit.py` — `MatchedGridLedger` :

```python
# FIFO par couple (buy_level, buy_level+1)
profit = matched_qty * (sell_price * (1 - fee) - buy_price * (1 + fee))
```

- `grid.py` `on_fill` → `matched_ledger.on_fill(side, level_index, ...)`
- `bot_runner._recompute_grid_profit_from_db()` après restore et après `_sync_fills`

### Recalcul manuel cycle 3

Preuve : `docs/proofs/m3_grid_profit_correction_cycle3.json`

| Métrique | Valeur |
|---|---|
| Fills grille | 27 (15 BUY / 12 SELL) |
| Round-trips complets appariés | **12** |
| BUY orphelins (qty en attente) | **0,01182 BTC** → Floating uniquement |
| Grid Profit **ancien** (UI) | **≈ −5,37 USDT** |
| Grid Profit **corrigé** | **+1,53 USDT** |
| Écart | **≈ 6,90 USDT** |

Chaque round-trip individuel : **≈ +0,125 USDT** (pas 0,25 % brut car fees inclus dans formule Binance).

### Test de non-régression

`bot/tests/test_m3_grid_profit_matched.py` — **4 passed** :
- `test_matched_pair_profit_excludes_orphan_buy` — 2 BUY, 1 SELL, 1 orphelin
- `test_sell_before_buy_does_not_realize_until_pair_complete`
- `test_compute_from_trades_list`
- `test_old_entry_avg_method_would_go_negative_on_drift`

### Propagation vérifiée

| Endroit | Impact | Décision |
|---|---|---|
| `grid.py` `on_fill` | Source corrigée | Appariement |
| `bot_runner` restore + `_sync_fills` | Recompute DB | Aligné |
| `build_status` / UI `grid_profit` | Lit état bot | Corrigé au prochain tick |
| `gross_pnl` | grid + floating | Floating absorbe orphelins |
| `PnlSnapshot` / courbe PnL | `grid_pnl` engine | Suit correction |
| Histogramme cycles | `cycle.net_pnl` à la clôture | Inchangé (utilise `grid_profit` moteur) |
| `fees_paid` / Module 7quater | Indépendant | Pas affecté |
| Module 7ter win_rate | Cycles clos | Pas affecté |
| `total_matched_trades` | **Corrigé** → roundtrip_count | `grid_profit.py` |

## Rétroactivité de la correction Grid Profit (2026-07-05)

### Constat SQL (preuve)

Requête exécutée sur `cycles` :

```sql
SELECT id, status, close_reason, grid_profit, floating_profit, gross_pnl, net_pnl, closed_at
FROM cycles ORDER BY id;
```

| Cycle | Statut | Clôture (UTC) | Avant fix ? | grid_profit DB (avant) | net_pnl |
|---|---|---|---|---|---|
| 1 | closed / open_failed | 2026-07-04 16:22 | oui | 0 | 0 |
| 2 | closed / trigger_15 | 2026-07-04 17:50 | **oui** | **2,77** (entry_avg) | 14,55 |
| 3 | open | — | en cours | live bot (+1,53) | — |

**Déploiement correction** : `grid_profit.py` mtime `2026-07-05T17:33:55Z` (rebuild bot).

**Réponse à la question** : la correction en direct (`matched_ledger`) **ne s'applique pas automatiquement** aux cycles déjà clôturés — leurs `grid_profit` / `floating_profit` étaient figés en base à la clôture.

### Action corrective

Script : `scripts/m3_grid_profit_retroactive_fix.py`  
Preuve : `docs/proofs/m3_grid_profit_retroactive_corrections.json`

| Cycle | Action | grid_profit | floating_profit | net_pnl | Écart net |
|---|---|---|---|---|---|
| 1 | non concerné (0 trade grille) | 0 → 0 | 0 → 0 | 0 | 0 |
| 2 | **recalculé** | 2,77 → **0** | 12,53 → **15,30** | **14,55** (inchangé) | 0 |

Cycle 2 : 3 SELL sans BUY grille → **0 round-trip** → Grid Profit Binance = **0** (les ventes utilisaient l'inventaire initial, pas des paires matchées). Le `gross_pnl` économique (15,30) et le `net_pnl` (14,55) restent corrects ; seul le découpage grid/floating était faux.

### Run organique 48h

- Manifest mis à jour : événement `grid_profit_retroactive_correction` + section `data_corrections`
- **Bot non interrompu**, chronomètre run inchangé (`started_at_utc` conservé)
- Le `summary.json` final devra lire les valeurs DB corrigées ; `net_pnl` cumulé homogène (même méthode fees ; cycle 2 net inchangé)

### Cycle 3 (ouvert)

Non stocké dans `cycles` tant qu'open — recalcul live via `_recompute_grid_profit_from_db()` après rebuild bot. À la clôture, `_close_cycle_db` utilisera la méthode corrigée.

---

## Rapport de synthèse — série d'audits M3 (2026-07-05)

Série clôturée. Run organique **annulé** le 2026-07-05 — gel jusqu'au feu vert pour nouveau run.

| # | Sujet | Statut | Preuve | Impact run 48h |
|---|---|---|---|---|
| 1 | **Achat initial vs fills grille** | Conforme (règle établie) | `m_initial_vs_grid_propagation.json` | Aucun |
| 2 | **Recentrage Cas A/B** | Conforme (déjà implémenté) | `m3_recenter_cas_ab_module.json` | Monitoring Cas A/B actif |
| 3 | **Notionnel viabilité 250 vs 500** | **Corrigé** | `m7bis_viability_notional_fix.json` | Objectif net théorique aligné (+12,50 USD) |
| 4 | **Chiffre théorique codé en dur** | **Corrigé** (via #3 + `spec.md`) | `viability.py`, `spec.md` | Comparaison PnL réel vs théo cohérente |
| 5 | **Grid Profit (entry_avg → appariement)** | **Corrigé** | `m3_grid_profit_correction_cycle3.json`, tests `test_m3_grid_profit_matched.py` | Cycle 3 live : +1,53 vs −5,37 UI |
| 6 | **Rétroactivité Grid Profit cycle 2** | **Corrigé** (DB) | `m3_grid_profit_retroactive_corrections.json` | `net_pnl` cycle 2 inchangé (14,55) ; manifest annoté |
| 7 | **Symétrie BUY initial / SELL inventaire** | **Corrigé** | `m3_matched_trades_symmetry_correction.json` | UI cycle 3 : 27→12 round-trips ; snapshots antérieurs = ancienne règle |

### Prochain point de contact

Feu vert utilisateur après approvisionnement BNB + validation du rapport `m7bis_target_config_simulation.json`.

---

## Annulation run organique 48h et repositionnement paramétrage (2026-07-05)

### Annulation

| Élément | Détail |
|---|---|
| Décision | Utilisateur — incompatibilité économique (fréquence ~1 cycle/6-7 jours) |
| Manifest | `status: cancelled_by_user`, `cancelled_at_utc: 2026-07-05T18:15:50Z` |
| Moniteur | PID 628782 arrêté ; `runner.pid` → `stopped` |
| Bot | **Stop** (pas Panic) — ordres annulés, position ~0,051 BTC conservée |
| Cycle 3 | `close_reason=user_stop`, `net_pnl≈-21,39` (frais cycle inclus) |
| Preuve finale | `docs/proofs/m3_organic_long_run/cycle3_final_state.json` |
| Snapshots/logs | **Conservés** (303 snapshots + corrections Grid Profit journalisées) |

**Panic non utilisé** : position significative mais Stop suffit (annulation ordres + clôture cycle DB sans vente forcée).

### Nouveau paramétrage cible (préparé, non appliqué)

| Param | Valeur cible |
|---|---|
| `step_pct` | **0,40 %** (vs 0,25 %) |
| `bnb_fee_discount` | **true** (bloquant au `/api/start` si `bnb_free=0`) |
| `cycle_trigger_usd` | 15 (inchangé) |

Simulation Module 7bis : `docs/proofs/m7bis_target_config_simulation.json`

| Métrique | Actuel 0,25% sans BNB | Cible 0,40% + BNB |
|---|---|---|
| `grids_to_cycle` | 120 | **24** |
| `net_per_grid` | 0,125 USD | **0,625 USD** |
| `net_at_gross_threshold` | 12,50 USD | **13,13 USD** |

### Étapes avant nouveau run (ordre)

1. **BNB sur compte Demo** — `bnb_free=0` aujourd'hui → **bloquant** (faucet portail demo.binance.com)
2. **Simulation** — faite (`m7bis_target_config_simulation.json`) ; rapport à valider par l'utilisateur
3. **Feu vert explicite** — puis application config + run court ; pas de run 48h automatique

### Code modifié (préparation BNB obligatoire)

- `backend/app/main.py` : `/api/start` refuse si `bnb_fee_discount` et `BNB≤0`
- `bot/ultiumgrid/bot_runner.py` : `start()` même garde côté moteur
