# Cahier des charges — Grid Bot Spot BTC/USDT avec cycles de micro-gains

**Version 2.0 — Document de spécification fonctionnelle**

**Changement de v1.0 → v2.0 :** passage de Binance Futures Testnet à Binance **Spot pur, sans levier**, suite à une instabilité persistante et confirmée du matching engine Futures Demo Trading (écritures d'ordres systématiquement en échec malgré des lectures fonctionnelles, diagnostiqué avec preuves : `order/test` valide à 200, ordre réel en `-1007`, même résultat avec le SDK officiel et l'API WebSocket). Le Spot fonctionne normalement pour les écritures.

**Objectif produit :** automatiser une stratégie déjà pratiquée manuellement (cycles de grille avec sécurisation de gain), en y ajoutant une gestion de risque à variance réduite adaptée à l'accumulation de micro-gains journaliers.

---

## 1. Paramètres de la grille

| Paramètre | Valeur |
|---|---|
| Capital alloué | 5 000 USDT |
| Levier | Aucun (Spot pur) |
| Notionnel total | 5 000 USDT (= capital, pas de levier) |
| Nombre de paliers | 20 |
| Notionnel par palier | 250 USDT |
| Pas entre paliers | 0,25 % à 0,26 %, arithmétique — **à valider/élargir, voir section 1bis** |
| Largeur totale de la fourchette | ≈ 5 % |
| Type de grille | Spot, arithmétique |
| Frais de référence | Spot maker/taker Binance : 0,1 % par défaut, 0,075 % avec réduction BNB |

---

## 1bis. Point de vigilance économique : viabilité du pas face aux frais Spot

Le pas de 0,25 % a été initialement calibré sur des frais Futures maker (0,02-0,04 % l'aller-retour), beaucoup plus bas que les frais Spot (0,1-0,2 % l'aller-retour selon réduction BNB).

**Calcul avec les paramètres actuels (250 USDT/palier, pas 0,25 %) :**

- Profit brut par grille exécutée : 250 × 0,25 % = 0,625 USD
- Frais sans BNB (0,2 % aller-retour) : 0,50 USD → **net ≈ 0,125 USD/grille**
- Frais avec BNB (0,15 % aller-retour) : 0,375 USD → **net ≈ 0,25 USD/grille**

Le ratio gain/frais (1,25x à 1,67x) reste sous la règle prudente des 2x minimum. Conséquences pratiques : le cycle à +15 USD nécessite environ 60 grilles matchées sans BNB, ou environ 30 avec BNB, contre ~10 dans la configuration Futures initiale — la stratégie reste rentable en théorie mais beaucoup plus lente, et plus sensible aux arrondis de quantité et à la latence d'exécution.

**Recommandations à tester via le module de configuration paramétrable (section 7bis du prompt de développement) avant tout fonctionnement prolongé :**

- Activer la réduction de frais BNB (nécessite un solde BNB suffisant sur le compte Spot Testnet, et de configurer l'option `useBNBBurn` côté API si applicable).
- Élargir le pas entre paliers (ex. tester 0,4 % à 0,5 %) pour remonter le ratio gain/frais au-dessus de 2x.
- Comparer les deux leviers d'ajustement (pas plus large vs réduction BNB) via le mode simulation avant application en réel.

---

## 2. Cycle de sécurisation du gain (étage 1)

- **Ouverture du cycle — achat initial au marché (correction de conception importante) :** à l'ouverture d'un nouveau cycle (démarrage initial ou recentrage), le bot doit exécuter un **achat au marché** d'une quantité égale à la somme des quantités des 10 paliers SELL (moitié haute de la grille), **avant** de placer les ordres. Sans cette étape, les paliers SELL ne peuvent jamais être de vrais ordres (Binance Spot exige de détenir l'actif pour placer un ordre SELL réel) — ils resteraient à l'état `pending` sans `order_id`, structurellement inertes tant qu'aucun BUY n'a été rempli en premier. Cet achat initial permet de placer immédiatement de vrais ordres SELL limites dès l'ouverture, comme le fait le grid bot natif de Binance.
  - Coût de cette étape : frais de l'ordre au marché + slippage éventuel, à intégrer dans le calcul de viabilité économique (section 1bis) et dans le suivi des commissions réelles (Module 7quater du prompt de développement) — ce n'est pas gratuit et s'ajoute au coût du cycle.
  - Cet achat immobilise du capital en BTC dès l'ouverture (la quantité correspondant aux 10 paliers SELL), réduisant le capital USDT disponible pour les paliers BUY restants — à vérifier lors du dimensionnement (250 USDT/palier reste la référence, mais la disponibilité réelle de capital doit être revérifiée en tenant compte de cet achat initial).
- **Déclencheur :** PnL total de la grille active (réalisé + flottant de la position du cycle en cours) ≥ **+15 USD brut**. *(Le funding rate n'existe pas en Spot — retiré du calcul.)*
- **Objectif net visé :** ≈ +12,50 USD après inclusion du coût réel de l'achat initial (2,50 USDT sur la config par défaut) et des frais de clôture/réouverture — chiffre recalculé (v2.1) à partir de la formule de viabilité réelle du Module 7bis, remplaçant l'estimation initiale de +10 qui ne tenait pas compte du coût fixe d'ouverture de cycle.
- **Action au déclenchement :**
  1. Annulation de tous les ordres ouverts de la grille active.
  2. Clôture de la position du cycle au marché.
  3. Enregistrement du cycle dans l'historique (durée, profit, trades matchés).
  4. Recentrage automatique de la grille sur le nouveau prix (incluant un nouvel achat initial au marché, selon la règle ci-dessus).
  5. Réouverture immédiate des 20 ordres limites, dont les 10 SELL désormais réellement placés.
- **Portée du calcul de PnL déclencheur :** exclusivement la grille active. Le flottant des sacs (voir section 4) n'entre jamais dans ce calcul.

---

## 2bis. Recentrage automatique en cas de dérive hors fourchette (sortie par le haut)

**Problème identifié :** le centre de la grille est fixé au moment de l'ouverture du cycle et ne suit pas le prix. Si le marché monte durablement sans jamais déclencher assez de fills pour atteindre le seuil de cycle (+15), le prix peut sortir entièrement de la fourchette par le haut et y rester — le bot devient inactif de fait, sans qu'aucun garde-fou existant ne le détecte (aucune perte, donc aucun garde-fou de risque ne se déclenche ; aucun gain, donc le cycle ne se ferme pas non plus).

**Deux cas distincts, avec des règles différentes :**

**Cas A — Aucune position ouverte (aucun BUY exécuté depuis l'ouverture du cycle).**

- Déclencheur : le prix reste continuellement en dehors de la fourchette complète (sous le palier BUY le plus bas, ou au-dessus du palier SELL le plus haut) pendant plus de **20 minutes** (seuil configurable), sans qu'aucun fill n'ait eu lieu depuis l'ouverture du cycle.
- Action : annuler les ordres restants, clôturer le cycle avec le motif `idle_recenter_no_fill` (ni gain ni perte réalisée, puisqu'aucune position n'a été prise), ouvrir un nouveau cycle recentré sur le prix actuel.
- Aucun risque financier dans ce cas : rien n'a été acheté, il n'y a donc rien à protéger — le recentrage est une pure question d'efficacité (éviter de perdre du temps d'accumulation), pas de gestion de risque.

**Cas B — Position ouverte (au moins un BUY exécuté), mais le(s) SELL correspondant(s) restent bloqués en `pending` malgré un prix de marché déjà passé au-dessus de leur niveau.**

- Ce cas signale probablement un manque de liquidité réelle en face sur le testnet à ce prix précis (l'ordre limite existe mais ne trouve pas de contrepartie), pas un bug du bot — à documenter comme telle, pas supposée.
- Déclencheur : un ordre SELL reste `pending` alors que le prix de marché réel (WebSocket) est resté au-dessus de son niveau pendant plus de **15 minutes** (seuil configurable).
- Action : forcer la vente de la quantité concernée au marché (pas au prix limite initial), enregistrer le profit réel obtenu sur ce palier, marquer l'événement `forced_sell_stuck_level` avec preuve (comparaison prix WebSocket vs statut de l'ordre au moment du déclenchement), puis poursuivre le cycle normalement (ou le clôturer si ce sell fait franchir le seuil de +15).

**Dans les deux cas :** le recentrage ne doit jamais toucher aux garde-fous existants (étage 2/3) — ce sont des mécanismes indépendants qui continuent de s'appliquer normalement si le prix venait à redescendre dans une zone de risque après un recentrage vers le haut.

---

## 3. Gestion du risque courant (étage 2)

Remplace un stop-loss unique par une coupe progressive, choisie pour minimiser la variance tout en gardant une espérance de perte comparable aux alternatives testées.

| Règle | Détail |
|---|---|
| Seuil de coupe partielle | Prix atteint le palier 10 (≈ -2,5 % depuis le centre) |
| Action à 10 paliers | Clôture de 50 % de la position du cycle → transfert en **sac** |
| Seuil de coupe totale | Prix atteint le palier 14 (≈ -3,5 %) |
| Action à 14 paliers | Clôture du solde restant → transfert en sac, grille recentrée |
| Règle de réarmement | Après toute coupe, aucun nouvel ordre d'achat n'est replacé sous le niveau de coupe avant que le prix remonte d'au moins 2 paliers (+0,5 %) **ou** qu'un délai de 15 à 30 minutes soit écoulé — au choix, le premier des deux conditions atteintes. |

**Pertes attendues par scénario (indicatif, recalculé proportionnellement à 250 USDT/palier — à valider avec les données réelles du bot) :**

- Rebond avant le palier 12 : ≈ -18 USD
- Rebond entre les paliers 12 et 14 : ≈ -18 USD (seule la moitié initiale est coupée)
- Poursuite au-delà du palier 14 : ≈ -49 USD

---

## 4. Système de sacs et Bot Égaliseur (v2.2 — changement de principe fondateur)

**Changement de principe (2026-07-06) :** la règle initiale ("vente manuelle uniquement") est remplacée. L'architecture passe à **deux bots indépendants** :

- **Bot 1 — Grille** (déjà spécifié dans ce document) : micro-gains par cycles de grille, logique de range.
- **Bot 2 — Égaliseur** : gère automatiquement la sortie des sacs créés par le Bot 1, sans confirmation humaine par transaction, avec pour objectif de récupérer la perte quand le marché le permet (logique de tendance, pas de range). Raison du changement : l'objectif du projet est l'accumulation **passive**, la confirmation manuelle par sac est incompatible avec cet objectif à l'échelle.

**Principe :** en Spot, chaque actif n'a qu'un seul solde agrégé par compte sur Binance, sans distinction native entre "capital utilisé par la grille", "sac en attente", ou "sac géré par l'Égaliseur". La séparation reste **virtuelle**, gérée dans la base de données — partagée en lecture par les deux bots, mais chaque bot n'écrit que dans son propre périmètre (le Bot 1 crée les sacs, le Bot 2 les vend et met à jour leur statut — jamais l'inverse).

### 4.1 Comptabilité à trois registres

- **Registre Grille active** (Bot 1) : quantité détenue par le cycle en cours.
- **Registre Sacs** (créé par Bot 1, géré en sortie par Bot 2) : quantité, prix d'entrée moyen, date de mise en sac, flottant en temps réel, statut (`open`, `trailing_active`, `sold_auto`, `sold_forced_time`, `sold_forced_stop`).
- **Réconciliation permanente** (vérifiée indépendamment par le Bot 1, le Bot 2, et le superviseur Module 10bis) : solde réel Binance de l'actif = somme des sacs (tous statuts non vendus) + quantité de la grille active. Écart détecté → alerte immédiate, peu importe lequel des deux bots aurait dû expliquer la différence.

### 4.2 Comportement du Bot Égaliseur (nouveau, remplace l'ancienne section 4.2)

Dès qu'un sac est créé par le Bot 1 (coupe palier 10 ou 14), le Bot Égaliseur en prend la responsabilité de sortie, selon la stratégie suivante, **sans confirmation humaine par transaction** :

1. **Mécanisme principal — Trailing Stop natif Binance Spot** (`trailingDelta`, ordre `STOP_LOSS`/`STOP_LOSS_LIMIT` avec paramètre trailing). Objectif : capturer une remontée du marché sans avoir à deviner un sommet fixe, cohérent avec l'objectif de récupérer la perte "si possible" plutôt que de la réaliser prématurément.
   - `trailingDelta` par défaut : à calibrer (point de départ suggéré 1-2%, à ajuster via simulation avec des données réelles avant activation en production).
   - Le prix limite associé doit inclure une marge de sécurité sous le prix d'activation théorique (pas pile au même niveau), pour limiter le risque de non-remplissage en cas de mouvement rapide.
   - `stopPrice` optionnel : le trailing ne commence à suivre le marché qu'une fois un premier seuil de reprise atteint (évite de suivre le marché depuis le pire moment, juste après la coupe).
2. **Filet de sécurité — Stop dur fixe.** En parallèle du trailing, un stop de catastrophe fixe (cohérent avec le stop dur -8% du Bot 1, ou une valeur propre au Bot Égaliseur à définir) protège contre une poursuite de la baisse sans fin. Si ce stop se déclenche, vente immédiate, jamais de tentative de "sauver" davantage.
3. **Sortie basée sur le temps.** Si un sac reste ouvert au-delà d'une durée maximale configurable (par défaut **24 heures**, ajustable), vente forcée au marché même sans avoir atteint l'objectif de récupération — pour ne jamais immobiliser indéfiniment du capital qui devrait travailler pour l'accumulation passive. Ce délai court reflète directement l'objectif de rotation rapide du capital ; il implique d'accepter plus souvent une perte réalisée plutôt que d'attendre une hypothétique récupération.
4. **Aucun renforcement de position (interdit).** Le Bot Égaliseur ne rachète jamais pour moyenner à la baisse — il ne fait que gérer la sortie d'une quantité déjà figée à la création du sac.
5. **À la vente (quel que soit le mécanisme déclencheur) :** enregistrer le PnL réalisé, mettre à jour le statut du sac, retirer de la comptabilité active — sans impact sur la grille active du Bot 1.

### 4.3 Indépendance architecturale du Bot Égaliseur

- **Service séparé**, son propre process, son propre container Docker — même principe d'indépendance que le superviseur (Module 10bis). Un crash du Bot 1 ne doit jamais arrêter la gestion des sacs déjà en cours par le Bot 2, et inversement.
- Le Bot Égaliseur lit la table des sacs (déjà enrichie : `creation_reason`, `cycle_id_origin`, `market_price_at_creation`, historique de flottant) comme source de contexte, mais ne dépend jamais de l'état interne du Bot 1 pour décider — seulement des données de marché réelles et de l'état du sac en base.
- Le Bot Égaliseur a son propre superviseur/alerting (peut réutiliser l'infrastructure du Module 10bis, étendue pour couvrir ses propres actions) — chaque vente automatique doit être journalisée et notifiée (canal d'alerte), même si aucune confirmation n'est requise avant l'action.

### 4.4 Contrôle humain résiduel (pas de confirmation par transaction, mais pas zéro contrôle)

Même en mode automatique, certains contrôles restent nécessaires pour la sécurité globale :

- Bouton **Pause** du Bot Égaliseur dans l'UI : arrête toute nouvelle action automatique (les trailing stops déjà posés sur Binance restent actifs côté exchange sauf annulation explicite — à décider et documenter).
- **Plafond de perte quotidien propre au Bot Égaliseur** (distinct du circuit breaker du Bot 1) : si la somme des ventes forcées (stop dur ou temporelles) dépasse un seuil sur une journée, mettre le Bot Égaliseur en pause automatique et alerter — protection contre un enchaînement de mauvaises sorties en cascade sur une tendance de marché très défavorable.
- Toute vente automatique reste visible et auditable après coup (Journal de trades, Module Journal déjà spécifié) — le contrôle humain se déplace de "avant chaque vente" à "supervision continue et possibilité d'intervenir sur le système, pas sur chaque trade".

### 4.5 Règle de capital disponible (inchangée, étendue au Bot Égaliseur)

- Le Bot 1 vérifie le solde USDT disponible avant chaque recentrage de grille — le capital immobilisé en sacs (géré par le Bot 2) n'est pas disponible pour la grille tant que le sac n'est pas vendu.
- Si le capital immobilisé en sacs dépasse un seuil défini, le Bot 1 réduit proportionnellement le nombre de paliers actifs ou le notionnel par palier — inchangé.

---

## 5. Garde-fous de risque (étage 3 — catastrophe)

**Précision importante :** en Spot pur sans marge, il n'y a **aucun risque de liquidation exigée par l'exchange** — la pire issue possible est de détenir un actif qui a perdu de la valeur, jamais une fermeture forcée par Binance. Les garde-fous ci-dessous restent utiles comme discipline de gestion du capital, mais leur rôle change : ils protègent la vitesse d'accumulation du plan, pas contre une liquidation.

| Garde-fou | Seuil | Rôle |
|---|---|---|
| Stop dur | ≈ -8 % sous le prix d'entrée moyen réel de la position totale | Limite auto-imposée de perte maximale acceptée sur un cycle/sac, pas une protection contre une liquidation exchange |
| Circuit breaker journalier | PnL réalisé du jour ≤ -40 à -50 USD | Arrêt du bot jusqu'au lendemain ; plafonne la pire journée possible |
| Alerte de sortie de range | Prix hors fourchette ou flottant grille < seuil défini | Notification (Telegram ou autre) sans action automatique |
| Bouton panic close | Manuel, dans l'interface | Clôture immédiate de tout (grille + sacs) en un clic |

Ces garde-fous sont désactivés pour la coupe automatique de risque courant (gérée par l'étage 2), mais restent actifs comme filet de sécurité ultime.

---

## 6. Architecture technique

### 6.1 Backend (moteur, exécution continue sur VPS)

- Connexion **WebSocket** Binance **Spot** pour prix et exécutions d'ordres en temps réel (pas de polling pour le mark).
- Calcul et placement des 20 niveaux de grille arithmétique.
- Calcul continu du PnL : Grid Profit (réalisé) + Floating Profit (latent) — **pas de funding en Spot**.
- Logique de déclenchement du cycle (étage 1), de coupe progressive (étage 2) et des garde-fous (étage 3).
- Gestion des sacs : transfert (Bot 1), sortie automatique (Bot 2 Égaliseur), réconciliation avec la position réelle Binance.
- Reprise après crash : l'état complet (grille, sacs, ordres ouverts) doit être reconstruit depuis la base de données au redémarrage, jamais reparti "à l'aveugle".

### 6.2 Base de données

- Historique des cycles (début, fin, profit, durée, trades matchés, statut).
- Historique des trades individuels.
- Registre des sacs actifs et clôturés.
- État courant du bot (pour la reprise après crash).
- État et journal du Bot Égaliseur (`egaliseur_state`, `egaliseur_actions`).

### 6.3 Interface web (calquée sur l'UI Binance)

| Onglet | Contenu |
|---|---|
| **Running** | Pair, Total Investment, Total Profit, Grid Profit, Floating Profit, Total Matched Trades, Price Range, Duration, Number of Grids — rafraîchi en temps réel |
| **History** | Liste des cycles fermés : Time Ended, Total Investment, Total Profit, Duration, Grid Status |
| **PnL Analysis** | Profit cumulé par jour, durée moyenne des cycles, meilleur/pire cycle |
| **Journal** | Trades individuels, catégories, frais réels |
| **Bags** | Lecture seule — sacs actifs (traçabilité coupe) |
| **Égaliseur** | Sacs gérés, historique ventes auto, pause/reprise, configuration trailing/stop/temps |
| **Config / Market / Fees / Supervision** | Paramétrage, marché, commissions, watchdog |

**Bandeau permanent :** capital immobilisé en sacs (%) / capital disponible pour la grille (USD).

**Contrôles globaux :** démarrer / arrêter le bot grille, modifier le seuil de cycle (+15 par défaut), bouton panic close.

---

## 7. Points restant à calibrer en développement

1. Seuil exact de réduction de la taille des paliers en cas de marge réduite par les sacs.
2. Seuil précis du circuit breaker journalier (-40 ou -50 USD).
3. Canal d'alerte (Telegram, email, notification push).
4. Fréquence de réconciliation position réelle / registres virtuels.

---

## Avertissement

Ce document décrit une architecture technique et des règles de gestion de risque analysées pour cohérence interne avec l'objectif déclaré (accumulation de micro-gains). Il ne constitue pas un conseil en investissement. Les seuils chiffrés sont indicatifs et doivent être validés/ajustés avec les données réelles du bot en conditions de marché.
