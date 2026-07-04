# Questions ouvertes

## Q1 — Cahier des charges introuvable (bloquant)

- **Constat :** `cahier_des_charges_grid_bot.md` et `docs/spec.md` absents.
- **Décision par défaut :** ne pas inventer les paramètres manquants ; bloquer les modules métier.
- **Pourquoi :** la règle impose `docs/spec.md` comme source de vérité ; combler le vide violerait « zéro imagination ».

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

- **Constat :** variables `HL_TESTNET_*` vides ; hors scope du prompt principal (Binance Futures).
- **Décision par défaut :** ignorer Hyperliquid jusqu’à demande explicite et clés fournies.
