# Guide — Connecter Claude Desktop (modèle sans mot de passe)

Copiez `GUIDE_CONNEXION_CLAUDE_DESKTOP.md` depuis le VPS (généré avec les vraies valeurs)  
ou demandez à Cursor de vous le régénérer.

Les identifiants réels sont dans `docs/claude_readonly_access.md` sur le VPS (**ne pas committer**).

## Résumé — connexion directe (sans tunnel SSH)

| Élément | Valeur |
|---|---|
| Utilisateur PostgreSQL | `claude_readonly` |
| Base BTC (principale) | `ultiumgrid` |
| Hôte:port BTC | `176.97.70.254:25432` |
| URL MCP (remplacer MOT_DE_PASSE) | `postgresql://claude_readonly:MOT_DE_PASSE@176.97.70.254:25432/ultiumgrid` |

| Instance | Port direct |
|---|---|
| SOL | `176.97.70.254:25433` → `ultiumgrid_sol` |
| XRP | `176.97.70.254:25434` → `ultiumgrid_hyper` |

Preuve lecture seule : `docs/proofs/m_claude_readonly_permissions.json`
