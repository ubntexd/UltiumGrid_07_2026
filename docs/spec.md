# Spécification fonctionnelle — UltiumGrid Grid Bot Spot (v2.0)

> **Source :** `docs/cahier_des_charges_grid_bot_spot.md` (v2.0, référence officielle).  
> Ancienne spec Futures archivée : `docs/spec_v1_futures_deprecated.md`.  
> Ambiguïtés : `docs/questions_ouvertes.md`. Migration : `docs/migration_futures_to_spot.md`.

## 1. Marché et connectivité

- Exchange : Binance **Spot** Testnet (`https://demo-api.binance.com`, WS `wss://demo-stream.binance.com/ws`).
- Connecteur : prix temps réel (WebSocket `bookTicker`), placement/annulation d’ordres, lecture soldes (`GET /api/v3/account` → `balances`).
- **Pas de** levier, marge, `positionRisk`, funding, liquidation.
- Filtres symbole via `GET /api/v3/exchangeInfo` (`tickSize`, `stepSize`, `minNotional`).

## 1bis. Viabilité économique (écran config)

Notionnel par palier = **(capital / 2) / (num_levels / 2)** — ex. 5 000 USDT, 20 paliers → **250 USDT/palier** (aligné sur `grid.qty_per_level`).

Calcul indicatif (pas 0,25 %, sans BNB) :
- Gain brut/grille : 250 × 0,25 % = **0,625 USD**
- Frais aller-retour (0,2 %) : **0,50 USD** → net ≈ **0,125 USD/grille**
- Frais achat initial (fixe/cycle) : (capital/2) × taker ≈ **2,50 USD** sur config défaut
- Grilles pour seuil **+15 USD brut** : ≈ **120** (sans BNB) ; net au seuil ≈ **+12,50 USD** après coût fixe d'ouverture
- Alerte si ratio gain brut / frais A/R < **2×**

Coût fixe d'ouverture inclus dans `fees_initial_inventory` et `net_at_gross_threshold` (Module 7bis).

## 2. Grille — étage 1 (cycle +15 / objectif net ≈ +12,50)

- 20 niveaux arithmétiques, pas **0,25 %** (bornes 0,05–2 %).
- Capital défaut **5 000 USDT** (Spot pur) : **moitié** inventaire SELL (achat marché), **moitié** limites BUY.
- **Séquence unique d’ouverture** (Start, post-+15, recentrage Cas A/B) :
  1. Calcul des niveaux autour du prix actuel
  2. BUY marché = somme des quantités SELL (confirmé `myTrades`)
  3. Placement **10 BUY + 10 SELL** limites (tous avec `order_id` — plus de SELL `pending` sans ordre)
  4. Cycle `open` + coût d’achat initial (`cycle_meta_*`, `fees_paid`)
- Anti-doublon **avant** l’achat marché (réservation DB + lock `_opening_cycle`).
- PnL : Grid Profit + Floating Profit ; floating recalculé à **chaque tick WS**.
- Déclenchement **+15 USD brut** (Grid + Floating, hors sacs) puis **même séquence** pour le cycle suivant.
- Objectif net visé : **≈ +12,50 USD** / cycle (v2.1 — après frais achat initial ~2,50 USD et frais grilles ; remplace l'estimation +10).

## 2bis. Recentrage hors fourchette

- **Cas A** (`idle_recenter_no_fill`, défaut 20 min) : hors fourchette, aucun fill, solde grille ≈ 0 → fermer puis **rejouer la séquence complète** (nouvel achat inventaire).
- **Cas B** (`forced_sell_stuck_level`, défaut 15 min) : SELL `open`, mark ≥ prix palier trop longtemps → vente marché forcée ; si le cycle se clôture, **même séquence** de réouverture.

## 3. Risque — étage 2 (coupe progressive)

- Palier **10** → coupe **50 %** de la quantité réelle détenue (balances), transfert en sac.
- Palier **14** → coupe du solde grille, transfert en sac, recentrage.
- Réarmement : **2 paliers** ou **20 min**.
- Quantité coupée = solde réel (`balances`), jamais théorique.

## 4. Sacs — Bot Égaliseur (v2.2)

**Changement de principe (v2.2)** : la vente manuelle n'est plus le flux principal. **Bot 1 (grille)** crée les sacs ; **Bot 2 (Égaliseur)** les vend automatiquement sans confirmation par transaction, avec garde-fous globaux (pause, plafond perte quotidien, alerting).

- Registres grille active / sacs en DB.
- Réconciliation : `solde base réel = sacs non vendus + grille active` (statuts sac : `open`, `trailing_active`, `journal_only`).
- Seuil de capital immobilisé en sacs pour réduire la grille (Bot 1).

**Architecture Bot Égaliseur** (6e service Docker, process indépendant) :
- Lit `bags` en lecture ; écrit statut sac + tables `egaliseur_state`, `egaliseur_actions`.
- Ne modifie jamais `cycles` ni niveaux de grille.
- Mécanisme principal : `STOP_LOSS_LIMIT` SELL + `trailingDelta` (bornes via `exchangeInfo` `TRAILING_DELTA`).
- Stop dur : surveillance logicielle (annulation trailing + vente MARKET si seuil franchi).
- Sortie temporelle : vente MARKET forcée après `max_hold_days` (défaut **24 h** = 1 j, CDC §4.2).
- **Interdiction absolue** d'ordre BUY sur quantité de sac.
- Pause : nouvelles actions stoppées ; trailing existants **laissés actifs** (`cancel_orders_on_pause=false`).
- Activation trailing : `stopPrice` = entrée × (1 + 1 %) — suivi après reprise.
- Marge limite : 0,15 % sous prix d'activation.
- Mode restreint (`EGALISEUR_OPERATION_MODE=test_only` par défaut) : journalise les sacs Bot 1 ; ordres réels seulement sur sacs **armés** (`POST /api/egaliseur/test/arm`) ou en mode `continuous`.
- UI : page dédiée `/egaliseur.html` (séparée du Bot Grille).

**Point de vigilance — sortie 24 h :** délai nettement plus court que l'ancienne hypothèse (7 j). Privilégie la rotation du capital ; un sac en tendance baissière prolongée sera vendu à perte après 24 h même si un rebond survient plus tard. À valider empiriquement sur historique réel — voir `docs/questions_ouvertes.md` Q11.

Statuts sac étendus : `open`, `trailing_active`, `journal_only`, `sold_auto`, `sold_forced_stop`, `sold_forced_time`, `sold_manual` (legacy), `sold_panic`.

### 4bis. Traçabilité renforcée

Chaque sac enregistre dès la création :
- `creation_reason` (`cut_level_10`, `cut_level_14`, `manual`, …)
- `cycle_id_origin` — cycle source
- `incomplete_levels_at_creation` — paliers incomplets au moment de la coupe
- `market_price_at_creation` — mark réel à la création (≠ `entry_price`)
- `status` extensible : `open`, `trailing_active`, `journal_only`, `sold_auto`, `sold_forced_*`, `sold_manual`, `sold_panic`, `closed` (legacy)
- À la vente : `sold_price`, `sold_at`, `sold_by`
- Historique flottant : table `bag_floating_snapshots` (snapshot horaire max 1/h/sac)

**Découplage** : le Bot Égaliseur est un **processus indépendant** (service `egaliseur`, même principe que le superviseur Module 10bis). Il lit `bags` et agit sans passer par la boucle `tick()` du bot grille. Distinct de `emergency_action` superviseur (désactivé par défaut).

Champs egaliseur sur sac : `trailing_order_id`, `trailing_delta_bips`, `trailing_limit_price`, `activation_stop_price`, `hard_stop_price`, `max_exit_at`.

## 4ter. Journal de trades (UI)

- Onglet **Journal** : une ligne par enregistrement table `trades` (lecture seule).
- Catégories alignées sur `matched_ledger` : `grid_fill`, `initial_inventory_buy`, `forced_sell_stuck_level`.
- API `GET /api/trades/journal` : tri, filtres combinables, pagination, export CSV (`format=csv`).
- Frais depuis `fees_paid` ; round-trip / PnL depuis `MatchedGridLedger` (même moteur que Grid Profit).

## 5. Garde-fous — étage 3

- Limites auto-imposées (pas de liquidation exchange).
- Stop dur **−8 %** sous entrée moyenne réelle (grille + sacs).
- Circuit breaker journalier défaut **−40 USD**.
- Panic close : vente marché de **100 %** du solde base libre réel.

## 5bis. Surveillance position résiduelle après Stop

- **Problème** : après `Stop` (`running=false`), `tick()` ne s'exécute plus — `check_hard_stop` / `check_circuit_breaker` inactifs. Toute position BTC hors sacs et hors grille active devient non surveillée.
- **Superviseur (Module 10bis)** : check continu `orphan_position_unwatched` indépendant de `running` — lit `GET /api/v3/account`, compare `binance_qty − bags_qty` au seuil `ORPHAN_MIN_NOTIONAL_USDT` (défaut **10 USDT**). Alerte `alert` si bot arrêté depuis `ORPHAN_STOPPED_MIN_S` (défaut **600 s**). Payload : qty, notionnel, `entry_avg`, PnL flottant, durée arrêt.
- **Stop API** : `POST /api/stop` renvoie `residual_position_warning` (qty, notionnel, message, `entry_avg`, `floating_pnl`) sans bloquer l'arrêt. UI : bandeau d'avertissement immédiat (`frontend/app.js`).
- **Start avec stock préexistant** : si achat initial sauté (`free_base ≥ sell_qty`), `entry_avg` = dernier `entry_avg` bot_state **ou** coût FIFO `myTrades` — **jamais** `center_price` par défaut. Si coût non traçable → `untracked_inventory` bloquant (vente Panic ou confirmation explicite requise).
- **Champ bot_state** : `stopped_at` (ISO) enregistré au Stop, effacé au Start.

## 6. Persistance

Tables : `cycles`, `trades`, `bags`, `bot_state`, `configurations`, `pnl_snapshots`, `alert_events`, `order_attempts`, `egaliseur_state`, `egaliseur_actions`.

## 7. Paramètres configurables (défauts Spot)

| Paramètre | Défaut |
|---|---|
| Capital alloué | 5000 USDT |
| Nombre de paliers | 20 |
| Pas | 0,25 % |
| Seuil cycle | +15 USD |
| Coupe 10 / 50 %, coupe 14 / 100 % | |
| Réarmement | 2 paliers / 20 min |
| Stop dur | −8 % |
| Circuit breaker | −40 USD |
| Seuil capital sacs | 40 % |
| BNB fee discount | off |
| Idle recenter / stuck sell | 20 min / 15 min |
| Orphelin min notionnel USDT | 10 (`ORPHAN_MIN_NOTIONAL_USDT`) |
| Orphelin délai après Stop (s) | 600 (`ORPHAN_STOPPED_MIN_S`) |
| Symbole | BTCUSDT |
| Égaliseur — trailing delta | 1,5 % (calibrable 1–2 %) |
| Égaliseur — sortie temporelle | 24 h (`max_hold_days=1`) |
| Égaliseur — plafond perte forcée/j | −50 USD |

## 7quater. Commissions réelles (BNB)

- Source unique : `GET /api/v3/myTrades` (`commission`, `commissionAsset`).
- Table `fees_paid` ; PnL net cycle = gross − somme `commission_usdt`.
- UI onglet Fees ; solde BNB affiché (activation paiement BNB = réglage compte Binance, pas un flag bot seul).

## 8–10. Backend, UI, reprise

Running / History / PnL / Bags / Config / Market / Fees / Supervision, libellés « capital disponible ».
