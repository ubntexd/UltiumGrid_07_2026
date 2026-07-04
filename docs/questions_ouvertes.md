# Questions ouvertes

## Q1 — Cahier des charges introuvable (bloquant)

- **Constat :** `cahier_des_charges_grid_bot.md` et `docs/spec.md` absents.
- **Décision par défaut :** ne pas inventer les paramètres manquants ; bloquer les modules métier.
- **Pourquoi :** la règle impose `docs/spec.md` comme source de vérité ; combler le vide violerait « zéro imagination ».

## Q2 — Cause exacte de l’erreur Binance `-2015` (bloquant)

- **Constat :** clés présentes (longueur 64) mais rejetées pour toute action authentifiée.
- **Décision par défaut :** traiter les clés comme invalides / non autorisées pour cette IP, exiger régénération + whitelist IP `176.97.70.254`.
- **Pourquoi :** l’API ne distingue pas clé invalide / IP / permissions dans le message `-2015` ; la seule action sûre est de fournir des clés fraîches explicitement autorisées pour ce VPS.

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
