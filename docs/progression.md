# Progression — UltiumGrid_07_2026

Légende : `terminé` uniquement si développement + audit conformité + tests réels + vérification croisée Binance↔DB sont prouvés.

| Étape | Statut | Preuve / notes |
|---|---|---|
| Audit initial | **terminé** | `docs/00_audit_initial.md`, `docs/proofs/00_binance_audit_raw.json` |
| Module 1 — Connecteur Binance | **bloqué** | Auth `-2015` ; impossible de placer/annuler un ordre |
| Module 2 — Base de données | non démarré | dépend Module 1 pour vérif croisée |
| Module 3 — Moteur de grille | non démarré | |
| Module 4 — Gestion du risque | non démarré | |
| Module 5 — Système de sacs | non démarré | |
| Module 6 — Garde-fous | non démarré | |
| Module 7 — Backend API | non démarré | |
| Module 7bis — Configuration UI | non démarré | |
| Module 7ter — Marché / analytics | non démarré | |
| Module 8 — Interface web | non démarré | |
| Module 9 — Reprise après crash | non démarré | |
| Module 10 — Audit final | non démarré | |
| Infrastructure Docker complète | **non vérifié** | aucun `docker-compose.yml` au moment de l’audit |

---

## Blocage actif (2026-07-04)

### B1 — Clés API Binance Futures Testnet rejetées

- Appel : `GET https://testnet.binancefuture.com/fapi/v2/account` (signé)
- Réponse brute : `HTTP 401 {"code":-2015,"msg":"Invalid API-key, IP, or permissions for action"}`
- Même erreur sur `POST /fapi/v1/listenKey`
- IP VPS : `176.97.70.254`
- Preuve : `docs/proofs/00_binance_audit_raw.json`

**Action requise (humaine) :**

1. Régénérer les clés sur https://testnet.binancefuture.com (les anciennes ont été exposées dans l’historique git via `.env.example`).
2. Autoriser l’IP `176.97.70.254` si une whitelist IP est active.
3. Activer les permissions Futures + trading (lecture compte + ordres).
4. Mettre à jour `.env` local (non commité) :

```
BINANCE_FUTURES_TESTNET_API_KEY=...
BINANCE_FUTURES_TESTNET_API_SECRET=...
```

5. Relancer la vérification :

```bash
.venv/bin/python scripts/connect_binance_futures_testnet.py
```

Critère de déblocage : `GET /fapi/v2/account` renvoie HTTP 200 avec `canTrade` lisible, réponse brute archivée dans `docs/proofs/`.

### B2 — Cahier des charges manquant

- `cahier_des_charges_grid_bot.md` / `docs/spec.md` introuvable dans le dépôt et sous `/home/dev`.
- Sans ce fichier, l’audit de conformité ligne à ligne des modules est impossible.

**Action requise :** fournir le cahier des charges pour placement dans `docs/spec.md`.

---

## Décision de méthode

Conformément à la règle « zéro imagination » et « ne pas écrire de code dépendant d’une hypothèse non vérifiée » : **arrêt avant Module 1**. Aucun connecteur métier, schéma DB, moteur de grille ni UI n’a été développé dans ce passage.
