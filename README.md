# UltiumGrid_07_2026

Bot de grille Binance USDT-M Futures **Testnet**, stack Docker complète (bot, backend API, frontend, PostgreSQL).

## Démarrage (exécuté avec succès sur ce VPS)

1. Copier les variables d’environnement :

```bash
cp .env.example .env
# Renseigner BINANCE_FUTURES_TESTNET_API_KEY et BINANCE_FUTURES_TESTNET_API_SECRET
```

2. Lancer toute la stack :

```bash
docker compose down -v   # état propre (efface le volume DB)
docker compose up --build -d
```

3. Vérifier :

```bash
docker compose ps
curl -s http://localhost:8000/health
curl -s http://localhost:8000/api/running
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/
```

- API : http://localhost:8000/docs  
- UI : http://localhost:8080/

Preuve d’un run réel : `docs/proofs/docker_api_stack.json`.

## Services

| Service | Rôle | Port |
|---|---|---|
| `db` | PostgreSQL 16 (volume `pgdata`) | interne |
| `bot` | Moteur grille / risque / sacs / garde-fous | — |
| `backend` | API FastAPI + WebSocket | 8000 |
| `frontend` | UI nginx (proxy `/api` et `/ws`) | 8080 |

## État connu (2026-07-04)

- Auth compte testnet : **OK** (`canTrade=true`, ~5000 USDT).
- Lecture marché / compte / WS : **OK**.
- Placement d’ordres testnet : **en échec côté Binance** (`-1007` timeout backend). Le bot démarre mais ne peut pas poser la grille tant que l’API d’écriture ne répond pas.
- Détail : `docs/progression.md`, `docs/audit_final.md`.

## Tests locaux (hors Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest bot/tests -v
```

## Documentation

| Fichier | Contenu |
|---|---|
| `docs/spec.md` | Spécification fonctionnelle |
| `docs/00_audit_initial.md` | Audit initial |
| `docs/progression.md` | Avancement / blocages |
| `docs/audit_modules.md` | Conformité par module |
| `docs/audit_ui.md` | Audit UI |
| `docs/audit_final.md` | Audit final |
| `docs/questions_ouvertes.md` | Ambiguïtés |
| `docs/proofs/` | Réponses brutes API / SQL / Docker |
