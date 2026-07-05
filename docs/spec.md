# Spécification fonctionnelle — UltiumGrid Grid Bot Spot (v2.0)

> **Source :** prompt migration Futures → Spot + `cahier_des_charges_grid_bot_spot.md` (v2.0, fourni dans le prompt).  
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

## 4. Sacs

- Registres grille active / sacs en DB.
- Réconciliation : `solde base réel = sacs + grille active`.
- Vente manuelle marché/limite.
- Seuil de capital immobilisé en sacs pour réduire la grille.

## 5. Garde-fous — étage 3

- Limites auto-imposées (pas de liquidation exchange).
- Stop dur **−8 %** sous entrée moyenne réelle (grille + sacs).
- Circuit breaker journalier défaut **−40 USD**.
- Panic close : vente marché de **100 %** du solde base libre réel.

## 6. Persistance

Tables : `cycles`, `trades`, `bags`, `bot_state`, `configurations`, `pnl_snapshots`, `alert_events`, `order_attempts`.

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
| Symbole | BTCUSDT |

## 7quater. Commissions réelles (BNB)

- Source unique : `GET /api/v3/myTrades` (`commission`, `commissionAsset`).
- Table `fees_paid` ; PnL net cycle = gross − somme `commission_usdt`.
- UI onglet Fees ; solde BNB affiché (activation paiement BNB = réglage compte Binance, pas un flag bot seul).

## 8–10. Backend, UI, reprise

Running / History / PnL / Bags / Config / Market / Fees / Supervision, libellés « capital disponible ».
