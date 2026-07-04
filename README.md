# UltiumGrid_07_2026

Bot de grille Binance Futures Testnet — développement juillet 2026.

Compte GitHub : ubntexd

## État actuel (audit initial)

L’audit initial est documenté dans `docs/00_audit_initial.md`.

**Blocage actif :** les clés API présentes sont rejetées par Binance (`HTTP 401`, code `-2015`). Les modules métier ne démarrent pas tant que l’auth compte n’est pas prouvée et que `docs/spec.md` n’est pas fourni. Voir `docs/progression.md`.

## Prérequis vérifiés sur ce VPS

- Ubuntu 24.04, Python 3.12, Docker 29.x, Docker Compose v5.x
- Endpoints publics Binance Futures Testnet joignables depuis ce VPS

## Connexion testnet (étape exécutée avec succès partielle)

1. Copier `.env.example` vers `.env` et renseigner des clés **valides** :

```bash
cp .env.example .env
# éditer .env : BINANCE_FUTURES_TESTNET_API_KEY / BINANCE_FUTURES_TESTNET_API_SECRET
```

2. Installer les dépendances du kit de démarrage :

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

3. Tester (preuve attendue : `Auth : OK (canTrade=...)`) :

```bash
.venv/bin/python scripts/connect_binance_futures_testnet.py
```

Critère de succès auth (constaté en échec le 2026-07-04) :

- `GET /fapi/v1/ping` → `{}`
- `GET /fapi/v2/account` → HTTP 200 (actuellement HTTP 401 `-2015`)

### Endpoints

- Binance Futures Testnet : https://testnet.binancefuture.com

## Docker

Infrastructure projet (`Dockerfile` / `docker-compose.yml`) : **pas encore en place** (non vérifié). Ne pas lancer `docker compose up` pour l’instant.

## Documentation

| Fichier | Rôle |
|---|---|
| `docs/00_audit_initial.md` | État réel + preuves API |
| `docs/progression.md` | Avancement et blocages |
| `docs/questions_ouvertes.md` | Ambiguïtés et décisions prudentes |
| `docs/proofs/00_binance_audit_raw.json` | Réponses brutes Binance |
