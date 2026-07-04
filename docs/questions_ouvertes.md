# Questions ouvertes

## Q1 — Cahier des charges introuvable (bloquant)

- **Constat :** `cahier_des_charges_grid_bot.md` et `docs/spec.md` absents.
- **Décision par défaut :** ne pas inventer les paramètres manquants ; bloquer les modules métier.
- **Pourquoi :** la règle impose `docs/spec.md` comme source de vérité ; combler le vide violerait « zéro imagination ».

## Q2 — Erreur Binance `-2015` (résolu)

- **Constat initial :** clés rejetées.
- **Résolution :** nouvelles clés fournies le 2026-07-04 → `GET /fapi/v2/account` HTTP 200, `canTrade=true`.
- **Preuve :** `docs/proofs/01_binance_auth_new_keys.json`.

## Q2b — Erreur Binance `-1007` sur écriture d’ordres (bloquant trading)

- **Constat :** `POST /fapi/v1/order` et `POST /fapi/v1/leverage` renvoient timeout backend / 502 / throttle.
- **Décision :** ne pas déclarer les modules trading live comme terminés ; code prêt, preuves lecture OK.
- **Pourquoi :** impossible de prouver place/cancel sans réponse Binance.

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

- **Constat :** variables `HL_TESTNET_*` vides ; hors scope du prompt principal (Binance Futures).
- **Décision par défaut :** ignorer Hyperliquid jusqu’à demande explicite et clés fournies.
