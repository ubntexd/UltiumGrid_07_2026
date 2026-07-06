# Observatory + n8n — Guide débutant

Stack **Observatory** (dashboard pro) + **n8n** (automatisation horaire) pour UltiumGrid.

## URLs (sur le VPS, via tunnel SSH)

| Service | URL locale VPS | Description |
|---|---|---|
| **Dashboard Observatory** | http://127.0.0.1:18780 | UI trades + courbes + VPS temps réel |
| **n8n** | http://127.0.0.1:5678 | Éditeur workflows (login ci-dessous) |

Depuis votre PC :

```bash
ssh -L 18780:127.0.0.1:18780 -L 5678:127.0.0.1:5678 user@votre-vps
```

Puis ouvrir http://localhost:18780 et http://localhost:5678

### Identifiants n8n (par défaut)

- Utilisateur : `ultium`
- Mot de passe : `ultiumgrid2026` (modifiable dans `.env.observatory`)

---

## Installation (déjà préparée dans le repo)

```bash
cd /home/dev/dev/UltiumGrid_07_2026
docker compose -p ultiumgrid_obs -f docker-compose.observatory.yml --env-file .env.observatory.example up -d --build
```

Vérification :

```bash
curl -s http://127.0.0.1:18780/health
curl -s -X POST http://127.0.0.1:18780/api/collect | python3 -m json.tool
```

---

## Ce que fait la collecte horaire

Chaque heure (via n8n), le service appelle les **3 instances UltiumGrid** :

| Instance | API |
|---|---|
| BTC | :18000 |
| SOL | :18100 |
| XRP | :18200 |

Pour chaque instance :

- État running (cycle, mark, PnL ouvert grid/floating)
- Historique cycles (net réalisé, net du jour)
- Journal trades (frais, round-trips, catégories)
- Courbe PnL récente
- Métriques VPS + `docker stats`

Les rapports sont stockés dans le volume Docker `observatory_data` (`/data/hourly/`).

---

## Configurer n8n (5 minutes)

1. Ouvrir http://127.0.0.1:5678 (tunnel SSH)
2. Se connecter (ultium / mot de passe)
3. **Workflows** → **Import from File**
4. Choisir `n8n/workflows/ultiumgrid_hourly_analysis.json`
5. Ouvrir le workflow importé
6. Cliquer **Active** (toggle en haut à droite) pour activer la collecte chaque heure

Le workflow fait simplement :

```
[Toutes les heures] → [POST http://host.docker.internal:18780/api/collect] → [Log résultat]
```

Test manuel : bouton **Test workflow** dans n8n.

---

## Dashboard Observatory

- **VPS temps réel** : CPU, RAM, disque, load — refresh 15 s
- **KPIs** : net réalisé, net jour, gross ouvert
- **Courbe historique** : une point par collecte horaire
- **Cartes BTC / SOL / XRP** : détail trades comme l'analyse Cursor
- Bouton **Collecter maintenant** : déclenchement immédiat

---

## Ressources VPS

Avec 3 instances UltiumGrid déjà actives (~4 Go RAM), ajout estimé :

- Observatory : ~80–150 MiB
- n8n : ~200–400 MiB

Total encore confortable sur VPS 8 Go (voir `docs/proofs/m_vps_capacity_analysis.json`).

---

## Fichiers

| Fichier | Rôle |
|---|---|
| `docker-compose.observatory.yml` | Stack Docker |
| `observatory/` | API + UI dashboard |
| `n8n/workflows/ultiumgrid_hourly_analysis.json` | Workflow à importer |
| `scripts/m_hourly_trade_analysis.py` | Script CLI équivalent (sans n8n) |

### Collecte sans n8n (CLI)

```bash
python3 scripts/m_hourly_trade_analysis.py
```

---

## Dépannage

| Problème | Solution |
|---|---|
| Instance OFFLINE dans le dashboard | Vérifier que BTC/SOL/XRP tournent (`docker ps`) |
| n8n ne joint pas l'API | Les deux services sont sur le réseau `observatory` — utiliser l'URL `http://observatory:8000` |
| Pas d'historique | Activer le workflow n8n ou cliquer « Collecter maintenant » |
| Docker stats vides | Vérifier le montage `/var/run/docker.sock` dans le compose |
