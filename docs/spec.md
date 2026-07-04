# Spécification fonctionnelle — UltiumGrid Grid Bot

> **Source :** exigences du prompt de développement end-to-end `UltiumGrid_07_2026` (2026-07-04).  
> Le fichier `cahier_des_charges_grid_bot.md` était introuvable dans le dépôt ; cette spec reprend uniquement les exigences explicites du prompt.  
> Ambiguïtés : `docs/questions_ouvertes.md`.

## 1. Marché et connectivité

- Exchange : Binance USDT-M Futures **Testnet** (`https://testnet.binancefuture.com`).
- Connecteur : prix temps réel (WebSocket), placement/annulation d’ordres, lecture position, solde/marge, funding rate.
- Filtres symbole (`tickSize`, `stepSize`, `MIN_NOTIONAL`) toujours lus via `GET /fapi/v1/exchangeInfo`, jamais inventés.

## 2. Grille — étage 1 (cycle +15 / +10)

- 20 niveaux arithmétiques, pas entre **0,25 % et 0,26 %** (défaut : **0,25 %**).
- Placement initial autour du prix de marché au démarrage du cycle.
- Après fill : achat rempli → vente placée au-dessus ; vente remplie → achat placé en-dessous.
- PnL grille active continu : **Grid Profit + Floating Profit + funding**.
- Déclenchement de cycle à **+15 USD brut** : fermeture complète, recentrage, réouverture.
- Objectif théorique de référence UI : rythme **+10 USD net** par cycle.

## 3. Risque — étage 2 (coupe progressive)

- Palier **10** atteint en baisse : coupe **50 %** de la position grille, transfert en sac.
- Palier **14** : coupe du solde restant de la grille, transfert en sac, recentrage.
- Réarmement : **2 paliers de remontée** **ou** délai **15–30 min** (défaut : 2 paliers **ou** 20 min).

## 4. Sacs (bags)

- Deux registres virtuels en DB : **grille active** / **sacs**.
- Réconciliation permanente : `position Binance = somme(sacs) + grille active`, à intervalle régulier et après chaque opération.
- Vente manuelle d’un sac (marché ou limite) via UI/API.
- Si marge occupée par les sacs trop élevée : refuser ou réduire le recentrage de grille pleine (seuil configurable).

## 5. Garde-fous — étage 3

- Stop dur à **≈ -8 %** sous le prix d’entrée moyen réel de la position totale.
- Circuit breaker journalier : défaut **−40 USD** (voir `docs/questions_ouvertes.md` Q3).
- Alertes : logs structurés + événements API (Q4).
- Panic close : clôture immédiate de tout (grille + sacs).

## 6. Persistance

Schéma minimal :

- `cycles` — cycles de grille (ouvert/fermé, PnL, timestamps, symbole, config).
- `trades` — trades individuels liés au cycle.
- `bags` — sacs (qty, entry, status, symbole).
- `bot_state` — état courant pour reprise après crash.
- `configurations` — configs testées (horodatage, paramètres, résultats).

## 7. Paramètres configurables (défauts)

| Paramètre | Défaut |
|---|---|
| Capital alloué | 1000 USDT |
| Levier | 5 (bornes 1–20) |
| Nombre de paliers | 20 |
| Pas entre paliers | 0,25 % (bornes 0,05–2 %) |
| Seuil cycle brut | +15 USD |
| Palier coupe 1 / % | 10 / 50 % |
| Palier coupe 2 / % | 14 / 100 % du solde grille |
| Réarmement paliers | 2 |
| Réarmement délai | 20 min |
| Stop dur | −8 % |
| Circuit breaker journalier | −40 USD |
| Seuil marge sacs (réduction grille) | 40 % de la marge disponible |
| Symbole | BTCUSDT |

Changement de paramètre / coin en cycle actif : confirmation UI obligatoire — attendre fin de cycle **ou** fermer proprement avant application.

## 8. Backend API

Endpoints : Running, History, PnL Analysis, Bags, marge occupée/disponible, configuration, marché, analytics.  
WebSocket serveur vers le frontend pour le temps réel.

## 9. Interface (4 onglets)

- **Running** : état live, prix, fourchette de grille, contrôles start/stop/seuil/panic.
- **History** : cycles passés.
- **PnL Analysis** : indicateurs et courbe PnL cumulé.
- **Bags** : sacs et vente manuelle.
- Bandeau de marge (occupée / disponible).
- Couleurs : gain vert, perte rouge. Aucun placeholder statique en production.

## 10. Reprise après crash

Au redémarrage : reconstruire l’état depuis la DB, réconcilier avec Binance, ne ni dupliquer ni perdre d’ordres réels.
