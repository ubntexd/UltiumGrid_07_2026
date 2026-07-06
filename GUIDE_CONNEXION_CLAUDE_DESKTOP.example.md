# Guide — Connecter Claude Desktop (modèle sans mot de passe)

Copiez `GUIDE_CONNEXION_CLAUDE_DESKTOP.md` depuis le VPS (généré avec les vraies valeurs)  
ou demandez à Cursor de vous le régénérer.

Les identifiants réels sont dans `docs/claude_readonly_access.md` sur le VPS (**ne pas committer**).

## Résumé

| Élément | Valeur |
|---|---|
| Utilisateur PostgreSQL | `claude_readonly` |
| Base BTC (principale) | `ultiumgrid` |
| Port VPS | `5432` (localhost uniquement) |
| Tunnel Windows | `ssh -N -L 5433:127.0.0.1:5432 dev@176.97.70.254` |
| URL MCP (remplacer MOT_DE_PASSE) | `postgresql://claude_readonly:MOT_DE_PASSE@localhost:5433/ultiumgrid` |

Preuve lecture seule : `docs/proofs/m_claude_readonly_permissions.json`
