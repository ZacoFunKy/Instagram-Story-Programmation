# Bot Instagram Story Scheduler - Architecture Stateless

## 🎯 Vue d'ensemble

Bot Telegram professionnel pour programmer des publications Instagram Stories avec persistance PostgreSQL via Supabase.

## 🏗️ Architecture

### Composants principaux

1. **bot.py** - Application Telegram principale
   - Gestion des commandes utilisateur
   - Interface conversationnelle
   - Validation des entrées

2. **db_manager.py** - Couche d'accès aux données
   - Abstraction complète de Supabase
   - Gestion CRUD des stories
   - Statistiques et nettoyage

3. **Worker** - Processus de publication
   - Vérification toutes les 60 secondes
   - Publication automatique des stories
   - Gestion des erreurs et notifications

### Flux de données

```
Utilisateur → Telegram → bot.py → db_manager.py → Supabase PostgreSQL
                                        ↓
                            Worker (APScheduler 60s)
                                        ↓
                                  Instagram API
```

## 📊 Base de données

### Table `stories`

```sql
- id (UUID, PK)
- chat_id (BIGINT) - ID Telegram
- file_id (TEXT) - ID fichier Telegram
- scheduled_time (TIMESTAMPTZ) - Date/heure programmée
- status (VARCHAR) - PENDING | PUBLISHED | ERROR | CANCELLED
- created_at (TIMESTAMPTZ)
- updated_at (TIMESTAMPTZ)
- error_message (TEXT, nullable)
```

### Index optimisés

- `idx_stories_status_scheduled` - Requêtes du worker
- `idx_stories_chat_id` - Requêtes par utilisateur

## 🚀 Déploiement

### Variables d'environnement requises

```env
TOKEN=              # Token Telegram Bot
IG_USER=           # Username Instagram
IG_PASS=           # Mot de passe Instagram
SUPABASE_URL=      # URL projet Supabase
SUPABASE_KEY=      # Clé API Supabase (anon ou service_role)
DOWNLOAD_DIR=      # Chemin stockage temporaire (défaut: downloads)
PORT=              # Port Flask (défaut: 8000)
```

### Sur Render

1. Créer un projet Supabase et exécuter `schema.sql`
2. Créer un Web Service Python sur Render
3. Ajouter toutes les variables d'environnement
4. Configurer un disque persistant sur `/data` pour DOWNLOAD_DIR
5. Build: `pip install -r requirements.txt`
6. Start: `python bot.py`

### Keep-alive

- Endpoint Flask `/health` sur le port configuré
- Configurer UptimeRobot pour ping toutes les 5 minutes

## 🔒 Sécurité

- ✅ Validation propriété des stories (chat_id)
- ✅ Pas de secrets en dur dans le code
- ✅ Session Instagram persistante avec 2FA
- ✅ Nettoyage automatique des fichiers temporaires
- ✅ Contraintes SQL sur les statuts

## 🧪 Tests recommandés

```python
# Test de création
story = db.create_story(123456, "file_id", datetime.now())

# Test de récupération
stories = db.get_pending_stories()

# Test de mise à jour
db.update_story_status(story_id, "PUBLISHED")

# Test d'annulation
db.cancel_story(story_id, chat_id)
```

## 📈 Monitoring

La commande `/status` affiche:
- État connexion Instagram
- Nombre de publications (pending, published, error)
- Présence session sauvegardée

## 🔄 Maintenance

### Nettoyage automatique

```python
# Supprimer les stories > 30 jours (PUBLISHED/ERROR/CANCELLED)
db.cleanup_old_stories(days=30)
```

Recommandation : ajouter un job APScheduler quotidien.

## 🐛 Logs

Tous les événements sont loggés:
- ✅ Création de story
- ✅ Publication réussie
- ❌ Erreurs de connexion
- ❌ Erreurs de publication
- 🗑️ Nettoyage de fichiers

## 📝 Commandes utilisateur

- `/start` - Accueil et instructions
- `/help` - Guide complet
- `/list` - Publications programmées
- `/cancel` - Annuler une saisie
- `/status` - Statistiques
- `/code` - Code 2FA Instagram

## 🎨 Fonctionnalités UI

- ✅ Boutons inline interactifs
- ✅ Messages formatés Markdown
- ✅ Notifications temps réel
- ✅ Validation taille photos (10 MB max)
- ✅ Support formats de date multiples

## 🔗 Dépendances

Voir `requirements.txt` pour la liste complète.

Principales:
- `python-telegram-bot==20.8`
- `instagrapi==2.1.2`
- `supabase==2.3.4`
- `apscheduler==3.10.4`
- `Flask==3.0.0`
