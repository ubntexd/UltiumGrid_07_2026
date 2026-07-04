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

À partir du notionnel par palier, du pas et des frais (avec/sans BNB) : profit net estimé par grille et nombre de grilles pour atteindre le seuil de cycle. Alerte si gain brut / frais < 2×.

## 2. Grille — étage 1 (cycle +15 / +10)

- 20 niveaux arithmétiques, pas **0,25 %** (bornes 0,05–2 %).
- Capital défaut **5 000 USDT** (Spot pur, sans levier).
- Placement initial, replacement après fill.
- PnL : **Grid Profit + Floating Profit** (pas de funding).
- Déclenchement cycle à **+15 USD brut**.
- Objectif UI : **+10 USD net** / cycle.

## 2bis. Recentrage hors fourchette

- **Cas A** (`idle_recenter_no_fill`, défaut 20 min) : prix hors fourchette complète, **aucun fill** depuis l’ouverture, solde base grille réel ≈ 0 (vérifié via `balances`) → annuler, clôturer le cycle, ouvrir un nouveau centré sur le prix actuel.
- **Cas B** (`forced_sell_stuck_level`, défaut 15 min) : SELL `open` avec prix marché déjà ≥ prix du palier sans fill → annuler la limite, vendre au marché la qty, journaliser la preuve.

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
