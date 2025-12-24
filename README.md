# 🤖 Instagram Story Scheduler Bot

Bot Telegram professionnel pour programmer vos stories Instagram avec base de données PostgreSQL et architecture stateless.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![Telegram](https://img.shields.io/badge/Telegram-Bot-blue)
![Instagram](https://img.shields.io/badge/Instagram-Stories-purple)
![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-green)

## ✨ Fonctionnalités

- 📸 **Programmation de stories** - Envoie une photo et choisis l'heure
- 🕐 **Multi-formats de date** - `14:30`, `25/12 09:00`, `2025-12-31 23:59`
- 🗄️ **Base de données persistante** - Survie aux redémarrages avec Supabase PostgreSQL
- 🔐 **Support 2FA** - Google Authenticator intégré
- 🌍 **Timezone Europe/Paris** - Pas de décalage horaire
- 💾 **Stockage optimisé** - Téléchargement lazy des photos
- 📊 **Statistiques** - Vue d'ensemble de vos publications
- 🔄 **Worker automatique** - Vérification toutes les 60 secondes
- 🎨 **UI professionnelle** - Boutons inline et messages formatés

## 🚀 Démarrage rapide

### Prérequis

- Python 3.11+
- Compte Telegram Bot (via @BotFather)
- Compte Instagram
- Projet Supabase (PostgreSQL)

### Installation locale

```bash
# Cloner le repo
git clone https://github.com/ZacoFunKy/Instagram-Story-Programmation.git
cd Instagram-Story-Programmation

# Installer les dépendances
pip install -r requirements.txt

# Configurer les variables d'environnement
cp .env.example .env
# Éditer .env avec vos credentials

# Créer la base de données
# 1. Créer un projet sur https://supabase.com
# 2. Exécuter schema.sql dans SQL Editor

# Lancer le bot
python bot.py
```

### Déploiement sur Render

Voir le guide complet : **[DEPLOY.md](DEPLOY.md)**

## 📋 Commandes

| Commande | Description |
|----------|-------------|
| `/start` | Démarrer le bot et voir le menu |
| `/help` | Guide d'utilisation complet |
| `/list` | Voir les publications programmées |
| `/cancel` | Annuler une publication en cours |
| `/status` | État de la connexion Instagram |
| `/code 123456` | Entrer le code 2FA Google Authenticator |

## 🎯 Utilisation

1. **Envoie une photo** au bot Telegram
2. **Indique l'heure** de publication :
   - `14:30` → Aujourd'hui à 14h30
   - `25/12 09:00` → Le 25 décembre à 9h
   - `2025-12-31 23:59` → Format complet
3. **Confirmation** → Story programmée ✅
4. **Publication automatique** à l'heure exacte !

## 🏗️ Architecture

```
┌─────────────┐
│  Telegram   │
│    User     │
└──────┬──────┘
       │
       ↓
┌─────────────────────┐
│     bot.py          │
│  - Handlers         │
│  - Validation       │
│  - UI Logic         │
└──────┬──────────────┘
       │
       ↓
┌─────────────────────┐
│   db_manager.py     │
│  - CRUD ops         │
│  - Supabase client  │
└──────┬──────────────┘
       │
       ↓
┌─────────────────────┐
│  Supabase (Cloud)   │
│  - Stories table    │
│  - PostgreSQL       │
└─────────────────────┘

       ↑
       │
┌──────┴──────────────┐
│  Worker (60s loop)  │
│  - Check DB         │
│  - Publish stories  │
│  - Update status    │
└─────────────────────┘
```

Voir **[ARCHITECTURE.md](ARCHITECTURE.md)** pour plus de détails.

## 🗄️ Base de données

### Table `stories`

```sql
CREATE TABLE stories (
    id UUID PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    file_id TEXT NOT NULL,
    scheduled_time TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    error_message TEXT
);
```

**Statuts:**
- `PENDING` - En attente de publication
- `PUBLISHED` - Publiée avec succès
- `ERROR` - Erreur lors de la publication
- `CANCELLED` - Annulée par l'utilisateur

## 🔧 Configuration

### Variables d'environnement

```env
TOKEN=               # Token Telegram Bot (@BotFather)
IG_USER=            # Username Instagram
IG_PASS=            # Mot de passe Instagram
SUPABASE_URL=       # URL projet Supabase
SUPABASE_KEY=       # Clé API Supabase (anon key)
DOWNLOAD_DIR=       # Dossier stockage (défaut: downloads)
PORT=               # Port Flask (défaut: 8000)
```

### Timezone

Le bot utilise **Europe/Paris**. Pour changer :

```python
# Dans bot.py ligne 41
TIMEZONE = ZoneInfo("Europe/Paris")
```

## 🛡️ Sécurité

- ✅ Pas de secrets en dur dans le code
- ✅ Validation propriété des stories (chat_id)
- ✅ Session Instagram chiffrée et persistante
- ✅ Contraintes SQL sur les statuts
- ✅ Variables d'environnement pour tous les credentials

## 🐛 Problèmes connus

### Conflit httpx résolu

**Problème:** `python-telegram-bot` et `supabase` avaient des conflits sur `httpx`.

**Solution:** Utilisation de `supabase>=2.8.0` compatible avec `httpx>=0.26.0`.

Voir **[CORRECTIONS.md](CORRECTIONS.md)** pour l'historique complet des fixes.

## 📝 Fichiers importants

- `bot.py` - Application principale
- `db_manager.py` - Couche d'accès à la base de données
- `schema.sql` - Schéma PostgreSQL
- `requirements.txt` - Dépendances Python
- `.env` - Variables d'environnement (ne pas commit)

## 🧪 Tests

```python
# Test de connexion Telegram
/start

# Test de programmation
# 1. Envoie une photo
# 2. Envoie "14:30"
# 3. Vérifie dans Supabase

# Test de 2FA
/code 123456

# Test de liste
/list
```

## 📈 Monitoring

### Logs importants

```
✅ Connexion Instagram via session sauvegardée
🔄 Worker de publication démarré
🔍 X story(ies) à publier trouvée(s)
✅ Story publiée avec succès
```

### Commande /status

Affiche:
- État connexion Instagram
- Nombre de publications (pending, published, error)
- Présence session sauvegardée

## 🤝 Contribution

Les contributions sont les bienvenues ! N'hésite pas à :
1. Fork le projet
2. Créer une branche (`git checkout -b feature/amelioration`)
3. Commit tes changements (`git commit -m 'Add: nouvelle fonctionnalité'`)
4. Push (`git push origin feature/amelioration`)
5. Ouvrir une Pull Request

## 📄 Licence

Ce projet est sous licence MIT. Voir le fichier `LICENSE` pour plus de détails.

## 📞 Support

- 💬 Telegram: [@ZacoFunKy](https://t.me/ZacoFunKy)
- 🐛 Issues: [GitHub Issues](https://github.com/ZacoFunKy/Instagram-Story-Programmation/issues)
- 📧 Email: support@example.com

## 🙏 Remerciements

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - Framework Telegram Bot
- [instagrapi](https://github.com/adw0rd/instagrapi) - Client Instagram privé
- [Supabase](https://supabase.com) - Base de données PostgreSQL hébergée
- [Render](https://render.com) - Plateforme de déploiement

---

Fait avec ❤️ par [ZacoFunKy](https://github.com/ZacoFunKy)
