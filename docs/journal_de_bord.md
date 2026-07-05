# Journal de bord — Projet UltiumGrid_07_2026

**Dernière mise à jour : 2026-07-05**  
**But de ce document :** permettre à n'importe qui (toi, une future instance de Claude, ou Cursor) de reprendre ce projet à zéro sans perdre le contexte. C'est la **source de vérité de suivi** — en cas de doute, ce document prime sur la mémoire d'une conversation.

Documents complémentaires (détail technique) : `docs/progression.md`, `docs/audit_modules.md`, `docs/spec.md`.

---

## 1. Objectif du projet

Développer un grid trading bot automatisé sur Binance, avec un mécanisme de sécurisation de gain par cycle (inspiré d'une pratique manuelle déjà éprouvée par l'utilisateur : fermer et rouvrir la grille dès qu'un seuil de profit est atteint). Objectif final déclaré : **accumulation de micro-gains fréquents, plusieurs fois par jour** — pas un objectif de gain unitaire élevé ni de faible fréquence.

Développement délégué à **Cursor** (agent de développement autonome), piloté par des prompts détaillés co-construits dans cette conversation. Claude (ici) joue le rôle d'analyste, de rédacteur de prompts, et d'auditeur des rapports produits par Cursor — Claude ne développe pas directement le code.

Dépôt : `UltiumGrid_07_2026`, sur VPS dédié.

---

## 2. Historique des grandes décisions (dans l'ordre chronologique)

1. **Config initiale envisagée : Binance Futures**, capital 2000 USD, levier x5, 20 paliers, pas 0,25%, cycle +15/+10 USD, coupe progressive à 10/14 paliers (variante 50%/reste), stop dur -8%, circuit breaker journalier, système de sacs (bag holding manuel, pas de vente automatique).
2. **Blocage Futures Demo Trading** : écritures d'ordres systématiquement en échec (`-1007`/`-1008`/`502`) malgré `order/test` valide (200) et lectures fonctionnelles. Diagnostic confirmé : Spot fonctionne, Futures non — probable instabilité/migration d'infrastructure Binance (COIN-M → USDS-M en cours à cette période).
3. **Migration décidée vers Binance Spot pur, sans levier.** Capital revu à **5000 USDT**. Conséquences : pas de funding, pas de liquidation exchange, notionnel = capital (pas de x5).
4. **Découverte économique majeure (section 1bis de la spec) :** le pas de 0,25% était calibré pour des frais Futures (0,02-0,04%), pas pour les frais Spot (0,1-0,2%). Ratio gain/frais initialement sous 2x — stratégie viable en théorie mais beaucoup plus lente que prévu.
5. **Ajout du superviseur indépendant ("Cowoker", Module 10bis)** : service séparé qui audite le bot, le marché et l'exchange en continu (heartbeat, réconciliation indépendante, latence, double vérification des garde-fous). `emergency_action` désactivé par défaut, activable uniquement par choix explicite de l'utilisateur.
6. **Ajout de la containerisation Docker obligatoire** (5 services : bot, backend, frontend, superviseur, DB).
7. **Ajout du module de configuration paramétrable (7bis)** avec indicateur de viabilité économique en direct, mode simulation, et comparaison de configurations testées.
8. **Ajout du module marché temps réel / sélection de coin (7ter)** — sélection unique, jamais de trading parallèle multi-coins.
9. **Ajout du suivi des commissions réelles avec paiement en BNB (7quater)** — recoupement frais théoriques vs réels, alerte de solde BNB bas.
10. **Correction de conception majeure : achat initial au marché.** Découverte que les ordres SELL (moitié haute de la grille) ne pouvaient jamais être de vrais ordres sans détenir l'actif au préalable (contrainte Spot). Ajout d'un achat au marché obligatoire à l'ouverture de tout cycle, couvrant la quantité des 10 paliers SELL. Coût réel : ~2,50 USDT par ouverture de cycle (sans BNB).
11. **Ajout du mécanisme de recentrage hors fourchette (section 2bis)** — deux cas : Cas A (aucune position, prix hors range >20 min → recentrage sans risque) et Cas B (SELL bloqué en pending malgré prix au-dessus, probable manque de liquidité testnet → vente forcée au marché après 15 min).
12. **Alignement visuel UI sur Binance natif** : niveaux de grille superposés sur le graphique de prix (couleur BUY/SELL, étiquettes prix/qté, marqueurs de fill), tableau récapitulatif avec les colonnes exactes (Pair, Time Created, Total Investment, Total Profit, Grid Profit, Floating Profit, Total Matched Trades, Price Range, Duration, Number of Grids, Action).
13. **Série d'audits critiques (2026-07-05)**, déclenchée par une contestation légitime de l'utilisateur sur un Grid Profit négatif observé en conditions réelles. Plusieurs bugs de fond trouvés et corrigés (détail section 4).
14. **Recalcul de l'objectif net réel : +12,50 USD/cycle**, pas +10 comme estimé initialement — après inclusion correcte du coût de l'achat initial dans le calcul de viabilité.
15. **Lancement d'un run organique de 48h** pour valider la logique de base en conditions réelles longue durée.
16. **Constat d'incompatibilité avec l'objectif de fréquence** : calcul de viabilité révèle qu'il faut ~120 grilles matchées pour un seul cycle avec la config actuelle (250 USDT/palier, pas 0,25%, sans BNB) — soit environ **1 cycle tous les 6-7 jours**, incompatible avec l'objectif de micro-gains plusieurs fois par jour.
17. **Décision (2026-07-05) : annulation du run de 48h**, repositionnement des paramètres avant de retenter un test longue durée.

---

## 3. Configuration actuelle de référence (au moment de l'annulation du run)

| Paramètre | Valeur |
|---|---|
| Mode | Binance Spot pur, sans levier |
| Capital | 5000 USDT |
| Nombre de paliers | 20 (10 BUY, 10 SELL) |
| Notionnel par palier | 250 USDT (capital / 2 / 10 — la moitié du capital part dans l'achat initial) |
| Pas entre paliers | 0,25% (arithmétique) — **remis en question, voir section 5** |
| Seuil de déclenchement de cycle | +15 USD brut |
| Objectif net réel par cycle | +12,50 USD (recalculé, coût achat initial inclus) |
| Coût de l'achat initial | ~2,50 USDT par ouverture de cycle (sans BNB) |
| BNB | Non activé — solde 0 sur le compte demo, **à rendre obligatoire dans la prochaine config** |
| Coupe progressive (étage 2) | 50% à 10 paliers, reste à 14 paliers, réarmement à +2 paliers ou 15-30 min |
| Stop dur (étage 3) | -8% sous prix d'entrée moyen réel |
| Circuit breaker journalier | -40 à -50 USD (non tranché définitivement) |
| Recentrage hors fourchette | Cas A : 20 min sans fill hors range → recentrage. Cas B : 15 min SELL bloqué → vente forcée marché |
| Infrastructure API réelle | `https://demo-api.binance.com` (pas `testnet.binance.vision` comme dans le prompt d'origine — écart documenté et assumé, endpoints `/api/v3/*` identiques) |

**État bot au 2026-07-05 18:15 UTC :** arrêté (`user_stop`), cycle 3 clos, position résiduelle ~0,051 BTC conservée. Preuve : `docs/proofs/m3_organic_long_run/cycle3_final_state.json`.

---

## 4. Bugs majeurs trouvés et corrigés pendant la série d'audits du 2026-07-05

| # | Bug | Cause racine | Correction | Preuve |
|---|---|---|---|---|
| 1 | Cycles dupliqués (deux `status=open` simultanés) | Aucune contrainte anti-doublon à l'ouverture de cycle | Contrainte DB unique partielle + garde applicative | `m_cycles_duplicate_binance.json` |
| 2 | Notionnel de viabilité doublé | `viability.py` utilisait `capital/10` au lieu de `(capital/2)/10` | Formule corrigée, alignée sur le moteur réel | `m7bis_viability_notional_fix.json` |
| 3 | Chiffre théorique codé en dur (`10 * cycles_total`) | Constante figée au lieu d'un calcul dynamique | Remplacé par `net_at_gross_threshold(config)` | corrections dans `viability.py` |
| 4 | **Grid Profit négatif structurellement impossible** (contesté par l'utilisateur) | Calcul basé sur `entry_avg` global (coût moyen contaminé par BUY orphelins) au lieu d'un appariement round-trip BUY/SELL par palier | Réécriture complète selon la formule officielle Binance : `profit = qty × (sell×(1-fee) − buy×(1+fee))`, appariement FIFO par palier | `m3_grid_profit_correction_cycle3.json` — cycle 3 : -5,37 → +1,53 USD |
| 5 | Cycles historiques non recalculés après la correction #4 | Les valeurs étaient figées en base à la clôture, pas recalculées rétroactivement | Script de correction rétroactive avec journal ancien/nouveau | `m3_grid_profit_retroactive_corrections.json` — cycle 2 corrigé (grid_profit 2,77→0, net_pnl inchangé à 14,55) |
| 6 | Symétrie manquante BUY initial / SELL d'inventaire initial | Les 3 SELL provenant de l'inventaire initial (pas d'un round-trip de grille) étaient comptés dans `Total Matched Trades` alors que le BUY initial correspondant en était exclu | `total_matched_trades` recalculé depuis `matched_ledger` (même source que Grid Profit) au lieu d'un simple `COUNT(level_index IS NOT NULL)` | `m3_matched_trades_symmetry_correction.json` — UI 27→12 round-trips |

**Point important :** dans tous les cas, le **PnL net économique réel n'a jamais été faux** — seul le *découpage* entre Grid Profit et Floating Profit, ou le *comptage* de trades, était erroné. Ces bugs auraient faussé l'analyse et la confiance dans les chiffres affichés, pas fait perdre de l'argent réel.

**Déclencheur de cette série d'audits :** l'utilisateur a contesté un Grid Profit négatif affiché en conditions réelles, en s'appuyant sur la logique Binance officielle (une somme de round-trips individuellement positifs ne peut mathématiquement pas être négative). Cette contestation a mené à la découverte du bug #4, le plus important de la série.

---

## 5. État du run organique de 48h (annulé)

- Lancé le 2026-07-04 ~16:57 UTC, prévu jusqu'au 2026-07-06 16:57 UTC.
- **Annulé le 2026-07-05 18:15 UTC** sur décision de l'utilisateur, avant son terme (~25h effectives).
- **Raison de l'annulation :** calcul de viabilité révélant ~120 grilles nécessaires par cycle avec la config testée, soit ~1 cycle tous les 6-7 jours — incompatible avec l'objectif de micro-gains fréquents.
- **Manifest :** `status: cancelled_by_user` — `docs/proofs/m3_organic_long_run/manifest.json`
- **Acquis malgré l'annulation :** les bugs #4, #5, #6 de la section 4 ont été découverts et corrigés *pendant* ce run, avec preuve réelle. Le connecteur, la DB, le superviseur ont tourné plus de 24h sans crash (303 snapshots conservés).
- **Non validé, reste ouvert :** déclenchement organique du recentrage Cas A/B avec les seuils réels de production (jamais observé en conditions réelles pendant ce run), déclenchement organique du seuil de cycle +15 (jamais atteint), comportement des garde-fous sur tendance de marché prolongée.
- **Statut du Module 3 :** validé en logique et en intégration courte, **pas** validé en run organique complet. Ne pas le considérer comme définitivement clos.

---

## 6. Analyse du désalignement objectif vs config (2026-07-05)

Le calcul (`net_at_gross_threshold`) a révélé que la config testée ne produit qu'environ 1 cycle tous les 6-7 jours — incompatible avec l'objectif de plusieurs cycles/jour.

**Quatre leviers identifiés pour corriger ce désalignement, avec tableau comparatif illustratif (hypothèses non encore vérifiées avec des données réelles) :**

| Combinaison | Pas | Seuil | Net/jour estimé (modèle illustratif) |
|---|---|---|---|
| Actuel + BNB | 0,25% | +15 | ≈ 1,97 USD |
| **Pas élargi + BNB** | 0,40% | +15 | **≈ 3,07 USD (meilleur du modèle)** |
| Seuil abaissé + BNB | 0,25% | +5 | ≈ 1,41 USD |
| Pas élargi + seuil abaissé + BNB | 0,40% | +5 | ≈ 2,20 USD |

**Important :** ce tableau repose sur une hypothèse générique de volatilité de marché (non mesurée sur le testnet réel) — à recalibrer dès que des données réelles de fréquence de fills seront disponibles.

**Décision prise :** tester en priorité la combinaison **pas 0,40% + BNB obligatoire**, seuil de cycle conservé à +15 pour l'instant (pas encore de décision définitive sur ce paramètre).

**Simulation Module 7bis (viabilité théorique, pas fréquence marché) :** `docs/proofs/m7bis_target_config_simulation.json` — cible 0,40%+BNB : 24 grilles/cycle vs 120 actuellement.

---

## 7. Prochaines étapes (au moment de la rédaction de ce document)

| # | Action | Statut | Preuve / note |
|---|---|---|---|
| 1 | Clore proprement le run 48h annulé (`cancelled_by_user`, cycle 3, preuves conservées) | **Fait** (2026-07-05) | `manifest.json`, `cycle3_final_state.json` |
| 2 | Rendre le BNB obligatoire au démarrage si `bnb_fee_discount=true` | **Fait** (code) | `backend/app/main.py` `/api/start`, `bot_runner.start()` |
| 3 | Obtenir solde BNB sur compte Spot Demo | **Bloquant** | `bnb_free=0` au 2026-07-05 — faucet demo.binance.com |
| 4 | Simulation 7bis ancien vs nouveau paramétrage | **Fait** (viabilité) | `m7bis_target_config_simulation.json` — à valider par l'utilisateur |
| 5 | Feu vert explicite avant nouveau run longue durée | **En attente** | Aucun run automatique |

**Gel actif :** pas de nouveau run longue durée ni d'application de la config cible (0,40%+BNB) sans feu vert utilisateur.

---

## 8. Fichiers de référence du projet

- `cahier_des_charges_grid_bot_spot.md` (v2.1) — spec fonctionnelle complète, source de vérité pour Cursor (`docs/spec.md` dans le repo).
- `prompt_cursor_ultiumgrid_07_2026.md` — prompt de développement principal, tous modules (1 à 10bis).
- `docs/journal_de_bord.md` — **ce document** (suivi projet).
- `docs/progression.md` — tableau modules + statuts techniques.
- `docs/audit_modules.md` — audits détaillés et corrections journalisées.
- Prompts d'audit ponctuels (tous donnés à Cursor au fil de la conversation, listés ici pour référence si besoin de les retrouver) :
  - Anti-doublon post-timeout (`-1007`)
  - Cycles dupliqués
  - Saut de prix pendant ban IP (résolu, non problématique)
  - Test des boutons de contrôle (Start/Stop/Panic)
  - Test panic close avec vente réelle
  - Achat initial au marché + recentrage unifié
  - PnL flottant recalculé à chaque tick
  - Alignement visuel UI Binance (grille superposée + tableau récapitulatif)
  - Correction chiffre théorique codé en dur
  - Audit propagation achat initial vs fills de grille
  - Audit formule Grid Profit (appariement Binance)
  - Vérification rétroactivité Grid Profit
  - Symétrie BUY/SELL inventaire initial + clôture de série
  - Annulation run 48h + nouveau paramétrage

---

## 9. Principe de méthode qui gouverne tout le projet (à ne jamais perdre)

Cursor travaille sous une règle unique et non négociable : **zéro imagination**. Aucune affirmation sur le comportement du système sans preuve produite dans le même passage de travail (appel API réel, requête DB réelle, test exécuté, capture réelle). Toute correction de données passées doit être journalisée (ancien/nouveau), jamais écrasée silencieusement. Cette règle a permis de détecter tous les bugs listés en section 4 — elle doit continuer à s'appliquer sur toute future évolution du projet.
