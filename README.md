# UltiumGrid_07_2026 — Grid Bot Spot Testnet

Bot de grille **Binance Spot** (sans levier), stack Docker (bot, API, UI, PostgreSQL).

> Migration Futures → Spot : voir `docs/migration_futures_to_spot.md`.

## Prérequis

Clés API **Spot Testnet** (pas Futures Demo) :

1. https://testnet.binance.vision → API Management → Create API Key  
2. Copier dans `.env` :

```bash
cp .env.example .env
# BINANCE_SPOT_TESTNET_API_KEY=...
# BINANCE_SPOT_TESTNET_API_SECRET=...
```

Les clés Futures Demo **ne fonctionnent pas** sur Spot (`-2015` prouvé).

## Démarrage

```bash
docker compose up --build -d
```

Services : `db`, `bot`, `backend`, `frontend`, `supervisor` (watchdog indépendant).

- UI : http://localhost:8080/ (onglet Supervision inclus)  
- API : http://localhost:8000/  
- Health : http://localhost:8000/health (heartbeat bot inclus)health  

## Diagnostic ordres

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHONPATH=bot .venv/bin/python scripts/diagnose_binance_orders.py
```

## Documentation

| Fichier | Rôle |
|---|---|
| `docs/spec.md` | Spec Spot v2 |
| `docs/spec_v1_futures_deprecated.md` | Ancienne spec Futures |
| `docs/migration_futures_to_spot.md` | Table endpoints + fichiers |
| `docs/progression.md` | Avancement / blocages |
| `docs/proofs/spot_public_audit.json` | Preuves API publique Spot |
