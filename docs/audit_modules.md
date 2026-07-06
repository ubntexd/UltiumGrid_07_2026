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

## Surveillance position résiduelle après Stop + sécurisation Start (2026-07-05)

**Bloquant feu vert run longue durée** — corrigé et prouvé.

### Contexte

| Ancien comportement | Nouveau |
|---|---|
| `tick()` sort si `running=false` → garde-fous inactifs | Superviseur `check_orphan_position` tourne en continu |
| Stop silencieux avec BTC résiduel | `residual_position_warning` dans `/api/stop` + UI |
| Start skip buy → `entry_avg = center_price` | `prior_bot_state_entry_avg` ou `myTrades_fifo` ; sinon blocage |

### Fichiers

| Fichier | Rôle |
|---|---|
| `bot/ultiumgrid/engine/orphan_position.py` | Seuils, détection résiduel, `resolve_entry_avg_existing` |
| `supervisor/ultium_supervisor/watchdog.py` | `check_orphan_position` → alerte `orphan_position_unwatched` |
| `bot/ultiumgrid/bot_runner.py` | `stopped_at`, warning au `stop()`, blocage `untracked_inventory` au `start()` |
| `bot/ultiumgrid/engine/grid.py` | `open_grid(prior_entry_avg=…)` |
| `backend/app/main.py` | `residual_position_warning` synchrone dans `/api/stop` |
| `frontend/app.js` | Bandeau ⚠ après Stop |
| `scripts/m3_orphan_position_proof.py` | Script de preuve bout-en-bout |

### Tests obligatoires — résultats

| Test | Description | Statut | Preuve |
|---|---|---|---|
| **A1** | Alerte `orphan_position_unwatched` après Stop + délai | **conforme** | `m3_orphan_position_proof.json` → `tests.A1` + compte `base_total=0.00401` |
| **A2** | Pas d'alerte si position nulle / sous seuil | **conforme** | `tests.A2.active_orphan_alerts=0`, `notional_usdt=0` après Panic |
| **B1** | `residual_position_warning` dans réponse Stop | **conforme** | `tests.B1.api_stop_response` + `last_command.result` |
| **C1** | Start stock existant → `prior_bot_state_entry_avg` | **conforme** | `tests.C1.initial_buy_skipped=true`, `entry_avg=62776` ≠ `center_price` |
| **C2** | Start sans historique → blocage | **conforme** | `m3_orphan_C2_blocked.json` + `test_start_blocked_untracked_C2` |

Reproductible : `ORPHAN_STOPPED_MIN_S=0 PYTHONPATH=bot python3 scripts/m3_orphan_position_proof.py` (stack Docker requise).

Tests unitaires : `bot/tests/test_m3_orphan_position.py` (5 passed, 3 skipped si solde insuffisant hors Docker).

## Journal de trades filtrable (2026-07-05)

| Exigence | Statut | Preuve |
|---|---|---|
| Une ligne par trade DB | **conforme** | `GET /api/trades/journal` — `total` = `COUNT(trades)` |
| Catégories = matched_ledger | **conforme** | `trade_journal.py` + tests unitaires |
| Tri / filtres / pagination | **conforme** | UI onglet Journal + params API |
| Export CSV | **conforme** | `format=csv` |
| Lecture seule (aucun effet bot) | **conforme** | GET uniquement |

Preuves : `docs/proofs/m_journal_trades_filter.json`, `docs/proofs/m_journal_bags_live_proof.json`  
Tests : `bot/tests/test_m_journal_bags.py`  
Script : `scripts/m_journal_bags_proof.py`

## Traçabilité renforcée des sacs (2026-07-05)

| Champ | Statut | Preuve |
|---|---|---|
| `creation_reason` | **conforme** | `bags/manager.py` `create_bag()` |
| `cycle_id_origin` | **conforme** | passé depuis `bot_runner.tick()` |
| `incomplete_levels_at_creation` | **conforme** | JSON en DB |
| `market_price_at_creation` | **conforme** | mark au moment coupe |
| `bag_floating_snapshots` | **conforme** | snapshot création + horaire en tick |
| `sold_*` / status extensible | **conforme** | `sell_bag`, `close_bags_via_panic` |
| `GET /api/bags` champs complets | **conforme** | `bag_to_dict()` + `status=all` |

Preuve : `docs/proofs/m_bags_traceability.json`  
**Découplage** : Bot Égaliseur implémenté — voir § Bot Égaliseur ci-dessous.

## Bot Égaliseur (Bot 2) — 2026-07-05

| Test | Statut | Preuve |
|---|---|---|
| T1 — Pose trailing stop testnet | **non validé** | `docs/proofs/m_egaliseur/` (à produire) |
| T2 — Déclenchement trailing | **non validé** | — |
| T3 — Stop dur | **non validé** | — |
| T4 — Sortie temporelle | **non validé** | test unitaire `test_time_exit_forces_market_sell` |
| T5 — Interdiction BUY | **conforme (code)** | `test_no_buy_in_engine_source`, `scripts/m_egaliseur_proof.py --test 5` |
| T6 — Pause + plafond perte | **non validé** | — |
| T7 — Réconciliation superviseur étendue | **conforme (code)** | `watchdog.py` + `m_egaliseur_proof.py --test 7` |
| T8 — Indépendance architecturale | **non validé (live)** | service Docker séparé ; preuve live après run v2 |

Code : `egaliseur/ultium_egaliseur/`, API `/api/egaliseur/*`, page UI `/egaliseur.html`.  
**Mode par défaut** : `operation_mode=test_only` (Q7) — tests ponctuels via `/api/egaliseur/test/arm`.  
**Ne pas marquer conforme** tant que T1–T4, T6, T8 live ne sont pas prouvés.

## Suivi run comparatif BTC/SOL — 2026-07-05 soir

Preuve consolidée : `docs/proofs/m_sol_cycle5_closure_audit.json`, `docs/proofs/m_sol_cycle5_trigger_investigation.json`, `docs/proofs/m_sol_cycle_trigger_5usd_applied.json`.

### Point 1 — Clôture cycle 5 SOL : trigger +5 ou rebuild ?

| Question | Réponse (preuve SQL / logs) |
|---|---|
| `close_reason` cycle 5 | **`config_change`** — pas `trigger_5`, pas `trigger_15`, pas `orphan_on_restart` |
| `closed_at` | **2026-07-05 22:40:35.361386 UTC** |
| Rebuild bot SOL | **2026-07-05 22:35:51 UTC** — log : `State restored cycle_id=5 running=True` → **cycle 5 conservé**, pas de cycle 6 |
| Création cycle 6 | **22:40:35.373662 UTC** — **12 ms** après `closed_at` cycle 5 ; log `Command received: config` à 22:40:35.312 → **`close_now` Module 7bis** (demande utilisateur) |
| `gross_pnl` à la clôture | **-0,72 USD** (pas 9,86 USD — pic flottant antérieur, non capturé à la clôture) |
| Séquence cycle 6 | Achat initial `#2409892928` à 22:40:35.645 ✅ |

**Trigger +5 USD organique : NON PROUVÉ.**

- Seuil **actif runtime = 15** jusqu'à 22:40 (`configurations` id=5 `is_active=true`).
- **195** snapshots `pnl_snapshots.grid_pnl ≥ 5` entre 22:02 et 22:40 ; **0** ≥ 15 ; max **12,01 USD** < 15 → pas de close attendue avec seuil 15.
- Si seuil 5 avait été actif dès 22:03, le trigger aurait dû fermer — **non testé** (wait_cycle puis rebuild sans appliquer le 5).
- **Cycle 6 ≠ preuve du mécanisme trigger** — effet de `close_now`, pas d'un `should_close_cycle()` organique.

**Statut trigger SOL seuil 5 :** en surveillance sur cycle 6+ (seuil 5 actif depuis 22:40).

### Point 2 — Bug d'affichage « Instance SOL » / données BTC

| Vérification | Résultat |
|---|---|
| `/api/instance` SOL | ✅ 200 — `{instance_id: sol, instance_label: …}` |
| `/api/instance` BTC (avant rebuild backend) | ❌ **404** — image backend **antérieure** au route handler (absent de `openapi.json`) |
| `/api/instance` BTC (après rebuild 22:46 UTC) | ✅ 200 — `{instance_id: btc, …}` |
| Usage frontend (`app.js` `loadInstanceBranding`) | **Label, couleur, title uniquement** — catch silencieux si 404 → reste « UltiumGrid » par défaut |
| Données symbole / capital / tableau | **`GET /api/running`** uniquement |

**Cause racine incident SOL/BTC confirmée (autre piste) :** premier lancement SOL — `POST /api/config` rejeté (BNB=0) → bot démarré en **BTCUSDT par défaut** → `/api/running` servait BTC. Preuve : `pre_launch_proof.json` `post_start.symbol=BTCUSDT`.

**`/api/instance` 404 sur BTC n'explique PAS** l'affichage de données BTC sur la page SOL (branding seulement). Piste **non confirmée** pour l'incident données ; **corrigée** pour cohérence multi-instance (backend BTC rebuildé).

### Point 3 — Réconciliation BTC (écart 0,0039 BTC @ 22:35)

| Instant | Binance BTC | `grid.position_qty` | Delta | Statut |
|---|---|---|---|---|
| ~22:35 UTC | — | — | ~0,0039 | fill récent, resync annoncé |
| **22:46 UTC** | **0,039330000000000004** | **0,039330000000000004** | **0** | ✅ `mismatches: 0` sur `GET /api/running` |

Écart **résorbé** — pas d'écart persistant.

### Point 4 — Égaliseur BTC absent

| Élément | État |
|---|---|
| Service dans `docker-compose.yml` | ✅ défini (`test_only`, `EGALISEUR_RESTRICTED_MODE=true`) |
| Container running | ❌ **non démarré** |
| Cohérence contrainte run v2 | ✅ **volontaire** — pas d'activation continue avant fin run v2 BTC (2026-07-06 19:23 UTC) ; pas un oubli bloquant |
| Action | **Aucune** sauf demande explicite démarrage test |

## Suivi run comparatif BTC / SOL / XRP — 2026-07-05/06

> **HYPER retiré de la comparaison en cours** (expérience terminée 2026-07-06). Archive : `docs/m3_hyper_instance_protocol.md`, preuves `docs/proofs/m3_hyper_instance_v1/`.  
> Instance 3 actuelle : **XRP** — protocole `docs/m3_xrp_instance_protocol.md`, preuves `docs/proofs/m3_xrp_instance_v1/`.

### Fenêtres temporelles (obligatoire pour comparaison)

| Instance | Symbole réel | Seuil cycle | Capital | Début run | Fin cible | Statut |
|---|---|---|---|---|---|---|
| BTC v2 | BTCUSDT | +15 USD | 5000 | 2026-07-05 19:23 UTC | 2026-07-06 19:23 UTC | En cours (cycle 10+) |
| SOL v1 | SOLUSDT | +5 USD | 4000 | 2026-07-05 ~22:02 UTC | alignée | En cours (cycle 10+) |
| **XRP v1** | **XRPUSDT** | **+15 USD** | **5000** | **2026-07-06 ~01:19 UTC** | alignée | **En cours (cycle 60+)** |
| ~~HYPER v1~~ | ~~HYPERUSDT~~ | ~~+15~~ | ~~5000~~ | ~~2026-07-05 23:47 UTC~~ | — | **Terminé** (−478 USDT compte) |

### Viabilité seuil +15 (BTC vs XRP, config alignée)

| | BTC v2 | XRP v1 (pré-lancement) |
|---|---|---|
| `net_at_gross_threshold` | 13,125 USD | **13,125 USD** (identique : 5000 / 20 / 0,40 % / BNB) |
| Preuve | `m3_organic_long_run_v2/pre_launch_proof.json` | `m_xrp_candidate_check.json` |

### Métriques comparatives (template — remplir après runs)

| Métrique | BTC v2 | SOL v1 | XRP v1 | Notes |
|---|---|---|---|---|
| Durée effective (h) | — | — | — | Fenêtres inégales → normaliser ou signaler |
| Fills (count) | — | — | — | |
| Round-trips | — | — | — | |
| Grid Profit réalisé (USD) | — | — | — | Cycles clos uniquement |
| Floating gross (USD) | — | — | — | Snapshot courant |
| Recentrages Cas A | — | — | — | |
| Recentrages Cas B | — | — | — | |
| Alertes garde-fou | — | — | — | |
| Cycles clos `trigger_*` | 8–9 (BTC) | 7–9 (SOL) | — | |

### Isolation 3 instances

Script : `python3 scripts/m_hyper_isolation_proof.py` (labels compose inchangés)  
Preuve attendue : `docs/proofs/m3_hyper_instance_v1/isolation_check.json`

### UI — test symbole au premier chargement (XRP)

Après `m3_xrp_instance_launch.py`, vérifier `launch_proof.json` → `post_start.symbol = XRPUSDT`, `mark_ok = true`, `ws_xrpusdt_in_logs = true`.

### Transition HYPER → XRP — validation fix WS

Preuve : `docs/proofs/m3_xrp_instance_v1/launch_proof.json` — mark **1,1593** = REST XRP ; WS **`xrpusdt@bookTicker`** ; pas de mark résiduel HYPER (~0,075).

---

## Archive — Suivi run comparatif BTC / SOL / HYPER — 2026-07-05/06

Preuves instance HYPER : `docs/proofs/m3_hyper_instance_v1/`, protocole `docs/m3_hyper_instance_protocol.md`.

### Fenêtres temporelles (obligatoire pour comparaison)

| Instance | Symbole réel | Seuil cycle | Capital | Début run | Fin cible | Statut |
|---|---|---|---|---|---|---|
| BTC v2 | BTCUSDT | +15 USD | 5000 | 2026-07-05 19:23 UTC | 2026-07-06 19:23 UTC | En cours (cycle 10+) |
| SOL v1 | SOLUSDT | +5 USD | 4000 | 2026-07-05 ~22:02 UTC | alignée | En cours (cycle 6+) |
| HYPER v1 | **HYPERUSDT** (pas HYPE) | +15 USD | 5000 | **2026-07-05 23:47 UTC** (relaunch post-fix WS) | alignée | **Terminé 2026-07-06** |

> **HYPERUSDT** = actif HYPER sur Binance Demo. **HYPE** (Hyperliquid) absent de Demo — voir `m_hype_candidate_check.json`.

### Viabilité seuil +15 (BTC vs HYPER, config alignée)

| | BTC v2 | HYPER v1 (pré-lancement) |
|---|---|---|
| `net_at_gross_threshold` | 13,125 USD | **13,125 USD** (identique : 5000 / 20 / 0,40 % / BNB) |
| Preuve | `m3_organic_long_run_v2/pre_launch_proof.json` | `m3_hyper_instance_v1/precheck.json` |

### Métriques comparatives (template — remplir après runs)

| Métrique | BTC v2 | SOL v1 | HYPER v1 | Notes |
|---|---|---|---|---|
| Durée effective (h) | — | — | — | Fenêtres inégales → normaliser ou signaler |
| Fills (count) | — | — | — | |
| Round-trips | — | — | — | |
| Grid Profit réalisé (USD) | — | — | — | Cycles clos uniquement |
| Floating gross (USD) | — | — | — | Snapshot courant |
| Recentrages Cas A | — | — | — | |
| Recentrages Cas B | — | — | — | |
| Alertes garde-fou | — | — | — | |
| Cycles clos `trigger_*` | 8–9 (BTC) | 0 organique (SOL) | — | |

### Isolation 3 instances

Script : `python3 scripts/m_hyper_isolation_proof.py`  
Preuve attendue : `docs/proofs/m3_hyper_instance_v1/isolation_check.json`

### UI — test symbole au premier chargement (HYPER)

Reproduire le test SOL : après `m3_hyper_instance_launch.py`, vérifier `pre_launch_proof.json` → `post_start.symbol = HYPERUSDT` et `ui_data_not_wrong_instance.ok = true`.

## Bug critique — prix BTC utilisé sur instance HYPER (2026-07-05 ~23:30 UTC)

Preuves : `docs/proofs/m3_hyper_instance_v1/ws_price_bug_investigation.json`, `ws_price_bug_fix.json`, `ws_price_regression.json`.

### Symptômes

| Observation | Valeur |
|---|---|
| Mark UI/API | ~63 690 (prix BTC) |
| Mark réel HYPERUSDT (REST) | ~0,075 |
| Total/Daily PnL affichés | milliards USD (impossible sur 5000 USDT) |
| Cycles en ~4 min | **58** dont **57** `close_reason=trigger_15` |

### Cause confirmée (distincte de l'incident SOL)

| | Incident SOL | Bug HYPER |
|---|---|---|
| Nature | Config rejetée → **symbole tradé** resté BTCUSDT | Symbole tradé **HYPERUSDT correct** |
| Mark erroné | Données BTC cohérentes avec symbole BTC | **WS bookTicker resté sur `btcusdt@bookTicker`** alors que `cfg.symbol=HYPERUSDT` |
| Mécanisme | `/api/running` servait BTC | `on_ws_price` écrivait mid BTC dans `_live_mark` / `_last_ticker[HYPERUSDT]` ; `tick()` préfère `_live_mark` → floating × qty HYPER → trigger_15 en boucle |

**Preuve log bot au boot :** `WS connecting wss://demo-stream.binance.com/ws/btcusdt@bookTicker` puis `WS price stream thread started for BTCUSDT` — **avant** fix, alors que cycles DB = HYPERUSDT.

**Bug secondaire :** `request_config_change` (bot inactif) mettait à jour `cfg` sans relancer WS ni rebinder `BagManager` → réconciliation loggée avec `symbol=BTCUSDT`.

### Ampleur réelle des dégâts (Demo)

| Métrique | Valeur |
|---|---|
| USDT départ (compte) | ~8 266 |
| USDT après stop | ~5 646 |
| HYPER détenu | ~29 880 (~2 250 USDT @ 0,075) |
| Trades réels Binance | **114** — prix d'exécution **~0,075** (correct) |
| PnL DB corrigé (58 cycles) | gross/net remis à `grid_profit` (0) — anciennes valeurs journalisées dans `ws_price_bug_fix.json` |
| Préjudice économique réel | ~**370–2 620 USDT** (frais + churn cycles), **pas** les milliards affichés |

### Actions prises

1. **Stop API** (pas Panic) — 2026-07-05 ~23:35 UTC
2. **Fix code** `bot_runner.py` : `restart_price_stream()` au changement de symbole + validation `data['s']` dans `on_ws_price` + `_rebind_subsystems()`
3. **Correction DB** : `scripts/m_fix_hyper_ws_price_damage.py`
4. **Non-régression** : mark API **0,0755** vs REST **0,0756** ; WS log `hyperusdt@bookTicker`
5. **Instance HYPER reste arrêtée** — ne pas relancer sans validation utilisateur

### Non-régression multi-instance

Après fix (bot HYPER rebuildé seulement) : BTC et SOL marks inchangés (containers non rebuildés). Rebuild BTC/SOL recommandé avant prochain restart pour bénéficier du fix WS sur changement de config.

## Vérification croisée post-incident WS — BTC/SOL (2026-07-05 ~23:44 UTC)

Preuve : `docs/proofs/m_ws_crosscheck_btc_sol.json`

### Point 1 — Historique changement de symbole

| Instance | Changement symbole ? | Exposition bug WS |
|---|---|---|
| **BTC** | **Non** — toutes configs = BTCUSDT ; run v2 démarré directement sur cible (config id=7 active depuis 19:23 UTC) | **Jamais** — WS `btcusdt@bookTicker` = symbole configuré dès le boot |
| **SOL** | **Oui** — config id=1 **BTCUSDT** (21:55) puis bascule SOLUSDT ; premier lancement raté | **Historique limité** ~21:58–21:59 UTC : **17** `price_ticks` SOLUSDT à ~63 186 (prix BTC) — même mécanisme WS non relancé. Rebuild container **22:35 UTC** → WS `solusdt@bookTicker` |

**Incident SOL affichage BTC (hier) — revue :**

| Piste | Rôle |
|---|---|
| Config rejetée → bot **tradait BTCUSDT** | **Cause primaire** — `/api/running` servait symbole/capital BTC |
| WS figé sur `btcusdt` après changement cfg | **Contribue partiellement** au premier lancement (~90 s de ticks mark BTC sous label SOLUSDT) ; **n'explique pas** l'affichage BTC persistant après relance propre 22:02 (mark SOL ~81,9 dans `pre_launch_proof`) |

### Point 2 — Vérification directe (instant T)

| | BTC | SOL |
|---|---|---|
| Mark API | **63 724,915** | **81,695** |
| REST ticker | **63 727,39** | **81,70** |
| Écart relatif | **0,004 %** | **0,006 %** |
| `mark_source` | ws | ws |
| `live_pnl` DB | 63 703,485 (ws) | 81,675 (ws) |
| Log WS | `btcusdt@bookTicker` | `solusdt@bookTicker` |
| Cycles `|gross_pnl| > 1000` | **0** (max **16,13**) | **0** (max **5,83**) |
| Running | oui (cycle 10) | oui (cycle 7) |

### Point 3 / 4 — Action et feu vert HYPER

- **BTC** : saine — **aucun arrêt** requis.
- **SOL** : saine **actuellement** — contamination ticks historique sans dégât cycle organique — **aucun arrêt** requis.
- **HYPER** : **feu vert** pour relance avec code corrigé (`restart_price_stream`, validation `data['s']`, `_rebind_subsystems()`).
- **Recommandation** : rebuild images bot BTC/SOL lors d'une fenêtre de maintenance (non bloquant — pas de changement symbole prévu).
