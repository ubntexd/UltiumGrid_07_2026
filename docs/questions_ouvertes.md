# Questions ouvertes

## Q1 — Cahier des charges (résolu)

- **Résolution :** `docs/cahier_des_charges_grid_bot_spot.md` v2.0 enregistré (2026-07-05).
- **Synthèse technique :** `docs/spec.md` (référence opérationnelle pour le code).
- **Écarts documentés :** voir Q10 ci-dessous si divergence implémentation vs CDC.

## Q2 — Erreur Binance `-2015` (résolu)

- **Constat initial :** clés rejetées.
- **Résolution :** nouvelles clés fournies le 2026-07-04 → `GET /fapi/v2/account` HTTP 200, `canTrade=true`.
- **Preuve :** `docs/proofs/01_binance_auth_new_keys.json`.

## Q2b — Erreur Binance `-1007` sur écriture d’ordres (bloquant trading live)

- **Constat :** `POST /fapi/v1/order` renvoie timeout backend / 502.
- **Décision anti-doublon (obligatoire) :** un `-1007` (ou 502) ne signifie pas échec — vérifier `openOrders` / `allOrders` / `origClientOrderId` avant tout renvoi ; chaque tentative a un `newClientOrderId` unique ; backoff 200ms→400ms→800ms (max 5) ; journal `order_attempts`.
- **`-1008` :** distinct — reduce-only / close / cancel censés exempts ; si reçus sur panic/coupe → `anomaly_1008_priority`.
- **Preuve :** `docs/proofs/m1_antiduze_post_1007.json`.

## Q3 — Seuil circuit breaker journalier (non tranché dans le prompt)

- **Plage indiquée :** entre -40 et -50 USD.
- **Décision par défaut (la plus prudente) :** **-40 USD**.
- **Pourquoi :** seuil plus proche de zéro = déclenchement plus tôt = moins de perte journalière potentielle.
- **Statut :** provisional ; à confirmer dès que `docs/spec.md` est disponible. Non appliqué (module 6 non démarré).

## Q4 — Canal d’alerte (non tranché)

- **Décision par défaut :** logs structurés fichier + endpoint API d’événements (pas de dépendance Telegram/Discord non configurée).
- **Pourquoi :** aucun credential de canal externe n’est présent dans `.env` ; inventer un webhook serait non vérifiable.
- **Statut :** provisional ; module 6 non démarré.

## Q5 — Hyperliquid Testnet

- **Constat :** variables `HL_TESTNET_*` vides ; hors scope du prompt principal.
- **Décision par défaut :** ignorer Hyperliquid jusqu’à demande explicite et clés fournies.

## Q6 — Superviseur emergency_action (Module 10bis)

- **Constat :** le prompt laisse le panic automatique à la discrétion de l’utilisateur.
- **Décision par défaut :** `SUPERVISOR_EMERGENCY_ACTION=false` — le superviseur **alerte uniquement**, n’appelle jamais panic close tout seul.
- **Pourquoi :** action destructive ; un faux positif pourrait liquider la grille sans consentement.

## Q7 — Bot Égaliseur — modes test_only vs continuous (run v2)

- **Contexte :** run organique Bot 1 v2 en cours jusqu'au **2026-07-06 19:23 UTC**.
- **Deux modes distincts (ne jamais confondre) :**
  - `test_only` (défaut) : journalise les sacs Bot 1 ; tests ponctuels réels via `POST /api/egaliseur/test/arm` sur un sac isolé.
  - `continuous` : veille autonome permanente sur tout sac `open` — **bloqué pendant run v2**.
- **Variable :** `EGALISEUR_OPERATION_MODE=test_only` (docker-compose).
- **Levée mode continu :** `POST /api/egaliseur/mode` avec `{"operation_mode":"continuous"}` — uniquement après fin run v2 + 8 tests validés.

## Q8 — Bot Égaliseur — comportement Pause

- **Décision :** à la pause, les trailing stops **déjà posés sur Binance restent actifs** (`cancel_orders_on_pause=false`).
- **Pourquoi :** éviter un sac sans protection pendant une investigation ; annulation configurable si besoin.

## Q9 — Bot Égaliseur — stop dur vs trailing

- **Décision :** pas d'OCO — **un ordre trailing** + **stop dur logiciel** (polling mark, MARKET si seuil).
- **Pourquoi :** Spot ne permet pas deux ordres SELL sur la même quantité.

## Q10 — Écarts CDC v2.0 / implémentation actuelle

| Sujet | CDC | Code actuel | Statut |
|---|---|---|---|
| Sortie temporelle sac | 24 h | `max_hold_days=1` | **aligné** (2026-07-05) |
| Trailing delta | 1–2 % suggéré | 1,5 % défaut | aligné provisoire |
| Réarmement coupe | 15–30 min **ou** 2 paliers | 20 min **ou** 2 paliers | défaut dev 20 min |
| Statut `journal_only` | non nommé dans CDC | mode `test_only` (journalisation) | temporaire, Q7 |
| Stop dur Égaliseur | « cohérent -8 % Bot 1 » | -8 % sur `entry_price` sac | aligné |
| §6.1 CDC original | mentionnait Futures | corrigé Spot à l'enregistrement | résolu |

## Q11 — Bot Égaliseur — sortie temporelle 24 h (vigilance empirique)

- **Décision CDC/prompt :** `max_hold_days=1` (24 h) par défaut — **pas 7 jours**.
- **Compromis assumé :** rotation rapide du capital > attente d'une récupération hypothétique.
- **Risque identifié :** ventes `sold_forced_time` fréquentes juste avant un rebond en tendance baissière prolongée.
- **Action requise (post-données réelles) :** analyser le ratio ventes forcées temporelles / rebonds manqués sur les premiers sacs réels ; si le délai s'avère systématiquement trop court, **signaler à l'utilisateur** avant de figer la config — ne pas ajuster silencieusement.
- **Statut :** en attente de données (run v2 + premiers sacs avec Égaliseur actif).
