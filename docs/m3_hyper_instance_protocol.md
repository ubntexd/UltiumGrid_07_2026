# Protocole — Instance HYPER/USDT (comparaison parallèle BTC v2 + SOL)

> **⚠️ TERMINÉ — 2026-07-06** : convertie en **XRPUSDT** (`docs/m3_xrp_instance_protocol.md`). Archive de l'expérience HYPER ; PnL final : `docs/proofs/m3_hyper_instance_v1/hyper_final_pnl_summary.json`.

## Clarification symbole (obligatoire)

| Terme | Signification |
|---|---|
| **HYPE** / Hyperliquid | **Non disponible** sur `demo-api.binance.com` |
| **HYPERUSDT** | Actif **HYPER** (baseAsset=HYPER) sur Binance Demo Spot — **symbole réellement tradé** |
| Confusion à éviter | Ne jamais configurer `HYPEUSDT` ni parler de « HYPE » dans la config bot |

Preuve candidature : `docs/proofs/m_hype_candidate_check.json`.

## Objectif

Run organique **HYPERUSDT** sur compte Demo **séparé**, paramètres comparables au BTC v2 (seuil +15, pas 0,40 %), **aucune interférence** avec BTC ni SOL.

## Stack isolée

| Élément | Instance BTC | Instance SOL | Instance HYPER |
|---|---|---|---|
| Compose | `docker-compose.yml` | `docker-compose.sol.yml` | `docker-compose.hyper.yml` |
| Projet Docker | `ultiumgrid_07_2026` | `ultiumgrid_sol` | `ultiumgrid_hyper` |
| Env | `.env` | `.env.sol` | `.env.hyper` |
| DB | `ultiumgrid` / `pgdata` | `ultiumgrid_sol` / `pgdata_sol` | `ultiumgrid_hyper` / `pgdata_hyper` |
| Réseau | `ultiumnet` | `ultiumnet_sol` | `ultiumnet_hyper` |
| API docker | `127.0.0.1:18000` | `127.0.0.1:18100` | `127.0.0.1:18200` |
| UI docker | `127.0.0.1:18080` | `127.0.0.1:18180` | `127.0.0.1:18280` |
| Beacon Cursor | `:8080` / `:8000` | `:8081` / `:8001` | `:8082` / `:8002` |
| UI label | UltiumGrid — Instance BTC | UltiumGrid — Instance SOL | UltiumGrid — Instance HYPER |
| Badge symbole UI | — | — | **HYPERUSDT** (pas HYPE) |
| Accent | bleu `#3b82f6` | violet `#a855f7` | orange `#f97316` |
| Égaliseur | `test_only` | `test_only` | `test_only` |

## Configuration HYPER

| Paramètre | Valeur |
|---|---|
| Symbole | **HYPERUSDT** |
| Capital | 5000 USDT |
| Paliers | 20 |
| Pas | 0,40 % |
| BNB discount | true (BNB obligatoire sur le compte) |
| Seuil cycle | **+15 USD brut** (identique BTC — SOL reste à +5 pour test seuil) |

### Viabilité Module 7bis (recalculée avant lancement)

| Indicateur | Valeur |
|---|---|
| `notional_per_level` | 250 USDT |
| `net_per_grid` | 0,625 USDT |
| `grids_to_cycle` | 24 |
| `fees_initial_inventory` | 1,875 USDT |
| **`net_at_gross_threshold`** | **13,125 USDT** |

> Identique à la config BTC v2 (5000 / 20 / 0,40 % / seuil 15 / BNB). Preuve : `docs/proofs/m3_hyper_instance_v1/precheck.json`.

## UI — anti-confusion HYPE vs HYPER

- En-tête : `UltiumGrid — Instance HYPER` + badge **`HYPERUSDT`**
- Tooltip : « HYPERUSDT est l'actif HYPER sur Binance Demo — pas HYPE/Hyperliquid »
- Test obligatoire post-démarrage : `GET /api/running` doit retourner `symbol=HYPERUSDT` dès le premier chargement (anti-bug SOL documenté dans `m3_sol_instance_protocol.md`)

## Isolation (3 instances)

```bash
python3 scripts/m_hyper_isolation_proof.py
```

Preuve attendue : `docs/proofs/m3_hyper_instance_v1/isolation_check.json`

- Container IDs disjoints BTC / SOL / HYPER
- DB séparées
- Stop bot HYPER → BTC et SOL inchangés (et réciproquement)

## Déploiement (clés requises)

```bash
cp .env.hyper.example .env.hyper
# Éditer .env.hyper — clés Demo HYPER uniquement (compte séparé)

python3 scripts/m_hyper_instance_precheck.py

docker compose -p ultiumgrid_hyper -f docker-compose.hyper.yml --env-file .env.hyper up -d --build

# Beacon (optionnel)
nohup python3 scripts/port_beacon_hyper.py > /tmp/port_beacon_hyper.log 2>&1 &

python3 scripts/m_hyper_isolation_proof.py

python3 scripts/m3_hyper_instance_launch.py
```

## URLs UI

- Direct VPS : http://127.0.0.1:18280/
- Via beacon : http://localhost:8082/
- Égaliseur : http://127.0.0.1:18280/egaliseur.html

## Fenêtres de comparaison (rapport à 3)

| Instance | Seuil | Début | Fin cible |
|---|---|---|---|
| BTC v2 | +15 USD | 2026-07-05 19:23 UTC | 2026-07-06 19:23 UTC |
| SOL v1 | +5 USD | 2026-07-05 ~22:02 UTC | alignée si possible |
| HYPER v1 | +15 USD | *heure réelle au lancement* | alignée si possible |

**Règle :** ne jamais comparer des durées inégales sans le signaler.

## Rapport comparatif (métriques communes)

Fills, round-trips, Grid Profit réel, Floating, recentrages Cas A/B, alertes garde-fou — voir `docs/audit_modules.md` section « Suivi run comparatif BTC/SOL/HYPER ».

## Règle run

Aucune intervention manuelle pendant le run, sauf garde-fou ou alerte critique réelle.

## Statut déploiement

| Étape | Statut |
|---|---|
| Structure compose + scripts | ✅ |
| `.env.hyper` (clés utilisateur) | ✅ |
| Stack Docker démarrée | ✅ **2026-07-05 23:30 UTC** |
| Precheck compte (USDT/BNB) | ✅ 8266 USDT, 0,338 BNB |
| Isolation 3 instances | ✅ `isolation_check.json` |
| Lancement bot cycle 1 | ⚠️ **Arrêté** 23:35 UTC — bug WS prix BTC (voir audit_modules.md) |
| Test UI symbole | ✅ `post_start.symbol=HYPERUSDT` |
| Fix WS + non-régression mark | ✅ 2026-07-05 23:39 UTC — `ws_price_regression.json` ok |
| Relance trading | ✅ **2026-07-05 23:47 UTC** — cycle **59**, mark ~0,0756 (WS ok) |
| Cohérence position cycle 59 | ✅ `hyper_position_coherence_check.json` — 29847 + 3221.2 − 3.22 frais = 33065 |

**URLs :** http://127.0.0.1:18280/ · API http://127.0.0.1:18200/
