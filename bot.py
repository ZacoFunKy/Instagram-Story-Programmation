# Fichier: bot.py
import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask
import requests
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from db_manager import DBManager
from datetime_manager import (
    parse_datetime,
    get_quick_time_keyboard,
    process_quick_time_callback,
    validate_scheduled_time,
    create_confirmation_message,
    get_datetime_help_text
)
from instagram_manager import InstagramManager, set_pending_2fa_code
import config
import media_validator
import overlay_manager

# Charger les variables d'environnement depuis .env (pour développement local)
load_dotenv()


def _env_required(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"La variable d'environnement {key} est requise")
    return value


# --- CONFIGURATION ---
TOKEN = _env_required("TOKEN")
IG_USER = _env_required("IG_USER")
IG_PASS = _env_required("IG_PASS")
SUPABASE_URL = _env_required("SUPABASE_URL")
SUPABASE_KEY = _env_required("SUPABASE_KEY")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "downloads")
SESSION_FILE = os.path.join(DOWNLOAD_DIR, "ig_session.json")
HTTP_PORT = int(os.environ.get("PORT", "8000"))

# 2FA & réseau (optionnels)
IG_TOTP_SECRET = os.environ.get("IG_TOTP_SECRET")  # secret base32 pour Google Authenticator
IG_SESSIONID = os.environ.get("IG_SESSIONID")  # cookie sessionid Instagram (optionnel)
PROXY_URL = (
    os.environ.get("PROXY_URL")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
)

# Timezone - Utiliser la timezone de Paris pour éviter les décalages
TIMEZONE = ZoneInfo("Europe/Paris")

ig_lock = threading.Lock()

# Configuration du logging avec niveau du config
log_level = getattr(logging, config.LOG_LEVEL, logging.INFO)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=log_level
)
scheduler = BackgroundScheduler()
scheduler.start()

# Initialisation
web = Flask(__name__)
db = DBManager(SUPABASE_URL, SUPABASE_KEY)

# Initialiser le gestionnaire Instagram
ig_manager = InstagramManager(
    username=IG_USER,
    password=IG_PASS,
    session_file=SESSION_FILE,
    totp_secret=IG_TOTP_SECRET,
    proxy_url=PROXY_URL
)


@web.route("/health")
def health() -> tuple[dict[str, str], int]:
    """Endpoint de santé pour UptimeRobot/Render keep-alive."""
    return {"status": "ok"}, 200


def now_tz() -> datetime:
    """Retourne datetime actuel avec la timezone configurée."""
    return datetime.now(TIMEZONE)


# Dossier pour stocker les images
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def publish_story_from_db(story: dict, bot_instance: Bot) -> None:
    """
    Publie une story depuis les données de la base de données.
    
    Args:
        story: Dictionnaire contenant les données de la story depuis Supabase
        bot_instance: Instance du bot Telegram pour les notifications
    """
    story_id = story["id"]
    file_id = story["file_id"]
    chat_id = story["chat_id"]
    media_type = story.get("media_type", "photo")
    
    logging.info("📤 Publication de la story %s (%s) pour le chat %s", story_id, media_type, chat_id)
    
    media_path = None

    def _extract_story_id(media_obj: object) -> str | None:
        """Récupère l'identifiant de story renvoyé par instagrapi."""
        try:
            if hasattr(media_obj, "pk"):
                return str(media_obj.pk)
            if isinstance(media_obj, dict):
                if media_obj.get("pk"):
                    return str(media_obj.get("pk"))
                if media_obj.get("id"):
                    return str(media_obj.get("id"))
        except Exception:
            return None
        return None
    try:
        # Télécharger le média depuis Telegram
        import asyncio
        
        async def download_file():
            file = await bot_instance.get_file(file_id)
            # Extension selon le type de média
            ext = "mp4" if media_type == "video" else "jpg"
            path = os.path.join(
                DOWNLOAD_DIR,
                f"temp_story_{now_tz().strftime('%Y%m%d_%H%M%S')}.{ext}"
            )
            await file.download_to_drive(path)
            return path
        
        # Gérer l'event loop de manière robuste
        try:
            # Essayer d'obtenir l'event loop existant
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                # Si le loop est fermé, en créer un nouveau
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            # Pas d'event loop, en créer un nouveau
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Exécuter le téléchargement sans fermer le loop
        media_path = loop.run_until_complete(download_file())
        
        # Connexion Instagram (synchrone car appelé depuis le scheduler)
        if not loop.run_until_complete(ig_manager.login(chat_id=None, context=None, force=False)):
            error_msg = "Connexion Instagram impossible"
            logging.warning(error_msg)
            db.update_story_status(
                story_id,
                "ERROR",
                error_msg,
                retry_count=(story.get("retry_count") or 0) + 1
            )
            db.log_story_event(
                story_id,
                "ERROR",
                {"stage": "login", "message": error_msg}
            )
            # Envoyer via API Telegram de manière synchrone
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": f"❌ Publication annulée: {error_msg}"
                    },
                    timeout=10
                )
            except Exception as notify_err:
                logging.error("Notification Telegram échouée: %s", notify_err)
            return

        # Publication sur Instagram
        to_close_friends = story.get("to_close_friends", False)
        
        # Note: Avec instagrapi 2.1.2, on utilise le paramètre audience directement
        # plutôt que de récupérer les IDs des amis proches
        logging.info("Publication story - Audience: %s", 
                    "Amis proches" if to_close_friends else "Public")
        
        # Publication selon le type de média (avec lock pour thread safety)
        media_response = None
        try:
            with ig_lock:
                client = ig_manager.get_client()
                if media_type == "video":
                    if to_close_friends:
                        logging.info("🎬 Publication vidéo pour amis proches...")
                        # Audience close friends via extra_data
                        extra_data = {"audience": "besties"}
                        media_response = client.video_upload_to_story(media_path, extra_data=extra_data)
                        logging.info("🎬 Vidéo publiée pour amis proches ✨")
                    else:
                        media_response = client.video_upload_to_story(media_path)
                        logging.info("🎬 Vidéo publiée sur Instagram")
                else:
                    if to_close_friends:
                        logging.info("📸 Publication photo pour amis proches...")
                        # Audience close friends via extra_data
                        extra_data = {"audience": "besties"}
                        media_response = client.photo_upload_to_story(media_path, extra_data=extra_data)
                        logging.info("📸 Photo publiée pour amis proches ✨")
                    else:
                        media_response = client.photo_upload_to_story(media_path)
                        logging.info("📸 Photo publiée sur Instagram")
        except Exception as upload_err:
            logging.error("❌ Erreur upload story: %s", upload_err)
            raise
        
        # Mise à jour du statut + métadonnées de publication
        published_at = datetime.now(ZoneInfo("UTC"))
        instagram_story_id = _extract_story_id(media_response)
        db.update_story_status(
            story_id,
            "PUBLISHED",
            published_at=published_at,
            instagram_story_id=instagram_story_id
        )
        db.log_story_event(
            story_id,
            "PUBLISHED",
            {
                "instagram_story_id": instagram_story_id,
                "media_type": media_type,
                "to_close_friends": to_close_friends,
            }
        )
        
        # Notification de succès (API Telegram synchrone)
        media_icon = "🎬" if media_type == "video" else "📸"
        media_name = "Vidéo" if media_type == "video" else "Photo"
        
        # Créer le message avec les détails appropriés
        if to_close_friends:
            success_text = f"✅ {media_name} publiée pour tes amis proches ! ✨ {media_icon}"
        else:
            success_text = f"✅ {media_name} publiée avec succès sur Instagram ! {media_icon}"
        
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": success_text
                },
                timeout=10
            )
        except Exception as notify_err:
            logging.error("Notification Telegram échouée: %s", notify_err)
        logging.info("✅ Story %s publiée avec succès", story_id)
        
    except Exception as exc:
        error_msg = str(exc)
        logging.exception("❌ Erreur lors de la publication de la story %s", story_id)
        db.update_story_status(
            story_id,
            "ERROR",
            error_msg,
            retry_count=(story.get("retry_count") or 0) + 1
        )
        db.log_story_event(
            story_id,
            "ERROR",
            {"stage": "publish", "message": error_msg}
        )
        
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": f"❌ Erreur lors de la publication: {error_msg}"
                },
                timeout=10
            )
        except Exception as notify_exc:
            logging.error("Impossible d'envoyer la notification d'erreur: %s", notify_exc)
    
    finally:
        # Nettoyer le fichier temporaire
        if media_path and os.path.exists(media_path):
            try:
                os.remove(media_path)
                logging.info("🗑️ Fichier temporaire supprimé: %s", media_path)
            except Exception as e:
                logging.warning("Impossible de supprimer le fichier temporaire: %s", e)


def check_and_publish_stories() -> None:
    """
    Worker qui vérifie périodiquement les stories à publier.
    Appelé toutes les 60 secondes par APScheduler.
    """
    try:
        pending_stories = db.get_pending_stories()
        
        if not pending_stories:
            logging.debug("Aucune story à publier pour le moment")
            return
        
        logging.info("🔍 %d story(ies) à publier trouvée(s)", len(pending_stories))
        
        # Créer une instance du bot pour les notifications
        bot_instance = Bot(token=TOKEN)
        
        for story in pending_stories:
            try:
                publish_story_from_db(story, bot_instance)
            except Exception as exc:
                logging.error(
                    "Erreur lors du traitement de la story %s: %s",
                    story.get("id"),
                    exc,
                    extra={
                        "story_id": story.get("id"),
                        "chat_id": story.get("chat_id"),
                        "error": str(exc)
                    }
                )
                
    except Exception as exc:
        logging.error("Erreur dans le worker de publication: %s", exc)


def check_and_retry_stories() -> None:
    """
    Worker qui retente les stories en erreur avec système de retry intelligent.
    Appelé toutes les 5 minutes par APScheduler.
    """
    if not config.RETRY_ENABLED:
        return
    
    try:
        stories_to_retry = db.get_stories_for_retry()
        
        if not stories_to_retry:
            logging.debug("Aucune story à retenter")
            return
        
        logging.info("🔄 %d story(ies) à retenter trouvée(s)", len(stories_to_retry))
        
        # Créer une instance du bot
        bot_instance = Bot(token=TOKEN)
        
        for story in stories_to_retry:
            story_id = story["id"]
            retry_count = story.get("retry_count", 0)
            
            logging.info(
                "Retry tentative %d/%d pour story %s",
                retry_count + 1,
                config.RETRY_MAX_ATTEMPTS,
                story_id,
                extra={
                    "story_id": story_id,
                    "retry_attempt": retry_count + 1,
                    "max_attempts": config.RETRY_MAX_ATTEMPTS
                }
            )
            
            try:
                # Réinitialiser le statut à PENDING pour la retry
                db.update_story_status(
                    story_id,
                    "PENDING",
                    retry_count=retry_count  # Ne pas incrémenter encore
                )
                
                # Tenter la publication
                publish_story_from_db(story, bot_instance)
                
                # Logger le succès du retry
                if config.LOG_RETRY_ATTEMPTS:
                    db.log_story_event(
                        story_id,
                        "RETRY_SUCCESS",
                        {"attempt": retry_count + 1}
                    )
                
            except Exception as exc:
                logging.error(
                    "Échec du retry pour story %s: %s",
                    story_id,
                    exc
                )
                
                # Incrémenter retry_count et remettre en ERROR
                new_retry_count = retry_count + 1
                db.update_story_status(
                    story_id,
                    "ERROR",
                    str(exc),
                    retry_count=new_retry_count
                )
                
                if config.LOG_RETRY_ATTEMPTS:
                    db.log_story_event(
                        story_id,
                        "RETRY_FAILED",
                        {
                            "attempt": new_retry_count,
                            "error": str(exc),
                            "will_retry": new_retry_count < config.RETRY_MAX_ATTEMPTS
                        }
                    )
                
                # Notifier l'utilisateur si c'était la dernière tentative
                if new_retry_count >= config.RETRY_MAX_ATTEMPTS:
                    chat_id = story.get("chat_id")
                    if chat_id:
                        try:
                            requests.post(
                                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                                json={
                                    "chat_id": chat_id,
                                    "text": (
                                        f"❌ *Échec définitif de publication*\n\n"
                                        f"La story n'a pas pu être publiée après {config.RETRY_MAX_ATTEMPTS} tentatives.\n\n"
                                        f"💬 Erreur : {exc}\n\n"
                                        f"💡 Vérifie /status et réessaye manuellement."
                                    ),
                                    "parse_mode": "Markdown"
                                },
                                timeout=10
                            )
                        except Exception:
                            pass
                
    except Exception as exc:
        logging.error("Erreur dans le worker de retry: %s", exc)


def cleanup_old_stories_job() -> None:
    """
    Worker qui nettoie les anciennes stories terminées.
    Appelé toutes les 24h par APScheduler.
    """
    if not config.CLEANUP_ENABLED:
        return
def cleanup_old_stories_job() -> None:
    """
    Worker qui nettoie les anciennes stories terminées.
    Appelé toutes les 24h par APScheduler.
    """
    if not config.CLEANUP_ENABLED:
        return
    
    try:
        logging.info("🧹 Démarrage du nettoyage automatique...")
        
        # Nettoyer les stories publiées
        published_count = db.cleanup_old_stories(days=config.CLEANUP_PUBLISHED_AFTER_DAYS)
        
        # TODO: Nettoyer aussi les ERROR et CANCELLED séparément
        # Pour l'instant, utiliser la fonction existante
        
        logging.info(
            "✅ Nettoyage terminé: %d stories supprimées",
            published_count,
            extra={"deleted_count": published_count}
        )
        
    except Exception as exc:
        logging.error("Erreur lors du nettoyage automatique: %s", exc)


# --- GESTION TELEGRAM ---
async def handle_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestionnaire de la commande /list - Affiche les publications programmées."""
    user_stories = db.get_user_pending_stories(update.effective_chat.id)
    
    if not user_stories:
        keyboard = [
            [InlineKeyboardButton("📸 Programmer une story", callback_data="new_post")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "📭 *Aucune publication programmée*\n\n"
            "Tu n'as pas encore de story en attente de publication.\n"
            "Envoie-moi une photo pour commencer !",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        return
    
    message = "📋 *Publications programmées :*\n\n"
    keyboard = []
    
    for idx, story in enumerate(user_stories, 1):
        scheduled_time = datetime.fromisoformat(story["scheduled_time"].replace("Z", "+00:00"))
        # Convertir en timezone Paris pour affichage
        scheduled_local = scheduled_time.astimezone(TIMEZONE)
        time_until = scheduled_local - now_tz()
        
        if time_until.total_seconds() > 0:
            hours = int(time_until.total_seconds() // 3600)
            minutes = int((time_until.total_seconds() % 3600) // 60)
            time_str = f"dans {hours}h {minutes}min"
        else:
            time_str = "en cours..."
        
        # Icône selon le type de média
        media_icon = "🎬" if story.get("media_type") == "video" else "📸"
        
        message += f"{idx}. {media_icon} {scheduled_local.strftime('%d/%m/%Y à %H:%M')}\n"
        message += f"   ⏰ {time_str}\n"
        if story.get("to_close_friends"):
            message += f"   ✨ Amis proches\n"
        
        message += "\n"
        
        keyboard.append([
            InlineKeyboardButton(f"❌ Annuler #{idx}", callback_data=f"cancel_{story['id']}")
        ])
    
    keyboard.append([InlineKeyboardButton("🔄 Actualiser", callback_data="list_posts")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        message,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gestionnaire de réception de photos, vidéos et documents (pour qualité max).
    
    Stocke l'ID du fichier photo/vidéo/document sans téléchargement pour économiser l'espace disque.
    """
    media_type = None
    file_id = None
    file_size = None
    original_filename: str | None = None
    quality_warning = ""
    media_icon = ""
    
    # Support photo compressée
    if update.message.photo:
        photo = update.message.photo[-1]
        file_id = photo.file_id
        file_size = photo.file_size
        media_type = "photo"
        quality_warning = "⚠️ Photo compressée par Telegram. Envoie en tant que *document* pour qualité maximale."
        media_icon = "📸"
        max_size = 20 * 1024 * 1024  # 20 MB
    
    # Support vidéo compressée
    elif update.message.video:
        video = update.message.video
        file_id = video.file_id
        file_size = video.file_size
        duration = video.duration
        media_type = "video"
        media_icon = "🎬"
        max_size = 100 * 1024 * 1024  # 100 MB
        
        # Vérifier la durée (Instagram Stories max 60s)
        if duration and duration > 60:
            await update.message.reply_text(
                f"⚠️ Vidéo trop longue ({duration}s).\n"
                "Instagram Stories limite les vidéos à 60 secondes.\n"
                "Envoie une vidéo plus courte."
            )
            return
        
        quality_warning = f"✅ Vidéo reçue ({duration}s) - prête pour publication !"
    
    # Support document (image ou vidéo non compressée)
    elif update.message.document:
        doc = update.message.document
        file_id = doc.file_id
        file_size = doc.file_size
        original_filename = doc.file_name
        
        # Vérifier que c'est bien une image ou vidéo
        if not doc.mime_type:
            await update.message.reply_text(
                "⚠️ Type de fichier non reconnu. Envoie une photo ou vidéo."
            )
            return
        
        if doc.mime_type.startswith('image/'):
            media_type = "photo"
            quality_warning = "✅ Document reçu - qualité originale préservée !"
            media_icon = "📸"
            max_size = 20 * 1024 * 1024  # 20 MB
        elif doc.mime_type.startswith('video/'):
            media_type = "video"
            quality_warning = "✅ Vidéo document reçue - qualité maximale !"
            media_icon = "🎬"
            max_size = 100 * 1024 * 1024  # 100 MB
        else:
            await update.message.reply_text(
                "⚠️ Ce n'est pas une image ou vidéo. Envoie un média valide."
            )
            return
    else:
        return
    
    # Vérifier la taille du fichier
    if file_size and file_size > max_size:
        max_mb = max_size // (1024 * 1024)
        await update.message.reply_text(
            f"⚠️ Le fichier est trop volumineux (max {max_mb} MB).\n"
            "Envoie un fichier plus léger."
        )
        return
    
    # Stocker les informations du média
    context.user_data['current_media_file_id'] = file_id
    context.user_data['current_media_type'] = media_type
    context.user_data['current_media_file_size'] = file_size
    context.user_data['current_media_filename'] = original_filename
    context.user_data['media_timestamp'] = now_tz()
    
    keyboard = [
        [
            InlineKeyboardButton("👥 Tout le monde", callback_data="audience_everyone"),
            InlineKeyboardButton("✨ Amis proches", callback_data="audience_close_friends")
        ],
        [InlineKeyboardButton("❌ Annuler", callback_data="cancel_media")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    media_name = "Photo" if media_type == "photo" else "Vidéo"
    
    await update.message.reply_text(
        f"{media_icon} *{media_name} reçue avec succès !*\n\n"
        f"{quality_warning}\n\n"
        "👥 *Qui peut voir cette story ?*\n"
        "• Tout le monde - Visible par tous tes abonnés\n"
        "• Amis proches - Uniquement ta liste d'amis proches\n\n"
        "Choisis une option ci-dessous :",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestionnaire de la commande /cancel - Annule la saisie en cours."""
    if 'current_media_file_id' in context.user_data:
        context.user_data.pop('current_media_file_id', None)
        context.user_data.pop('current_media_type', None)
        context.user_data.pop('current_media_file_size', None)
        context.user_data.pop('current_media_filename', None)
        context.user_data.pop('media_timestamp', None)
        await update.message.reply_text(
            "❌ *Programmation annulée*\n\n"
            "Le média en attente a été supprimé.\n"
            "Envoie une nouvelle photo ou vidéo pour recommencer.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "ℹ️ Aucune programmation en cours.\n\n"
            "Pour annuler une publication déjà programmée, utilise /list"
        )

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gestionnaire de planification de publication.
    
    Parse l'heure ou date/heure fournie et planifie la publication de la story.
    """
    time_str = update.message.text.strip()
    file_id = context.user_data.get('current_media_file_id')
    media_type = context.user_data.get('current_media_type', 'photo')
    to_close_friends = context.user_data.get('to_close_friends', False)

    if not file_id:
        await update.message.reply_text(
            "❌ *Aucun média en attente*\n\n"
            "Envoie d'abord une photo ou vidéo pour commencer.",
            parse_mode="Markdown"
        )
        return

    now = now_tz()
    
    # Parser avec le nouveau module professionnel
    run_date, explicit_date, format_used = parse_datetime(time_str, now, TIMEZONE)
    
    if not run_date:
        keyboard = get_quick_time_keyboard()
        await update.message.reply_text(
            "❌ *Format non reconnu*\n\n"
            f"{get_datetime_help_text()}",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return
    
    # Valider la date programmée
    is_valid, error_message = validate_scheduled_time(run_date, now)
    if not is_valid:
        await update.message.reply_text(
            f"{error_message}\n\n"
            "💡 Astuce : Utilise les boutons rapides !",
            parse_mode="Markdown",
            reply_markup=get_quick_time_keyboard()
        )
        return

    # Créer la story dans la base de données
    story = db.create_story(
        chat_id=update.effective_chat.id,
        file_id=file_id,
        scheduled_time=run_date,
        to_close_friends=to_close_friends,
        media_type=media_type,
        file_size_bytes=context.user_data.get('current_media_file_size'),
        original_filename=context.user_data.get('current_media_filename')
    )
    
    if not story:
        await update.message.reply_text(
            "❌ *Erreur technique*\n\n"
            "Impossible de programmer la story. Réessaie ou contacte le support.",
            parse_mode="Markdown"
        )
        return

    # Nettoyer les données temporaires
    context.user_data.pop('current_media_file_id', None)
    context.user_data.pop('current_media_type', None)
    context.user_data.pop('current_media_file_size', None)
    context.user_data.pop('current_media_filename', None)
    context.user_data.pop('media_timestamp', None)
    context.user_data.pop('to_close_friends', None)
    
    # Message de confirmation professionnel
    confirmation_msg = create_confirmation_message(
        scheduled_time=run_date,
        reference_time=now,
        media_type=media_type,
        to_close_friends=to_close_friends
    )
    
    keyboard = [
        [InlineKeyboardButton("📋 Mes publications", callback_data="list_posts")],
        [InlineKeyboardButton("➕ Programmer une autre", callback_data="new_post")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        confirmation_msg,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestionnaire de la commande /status - Affiche l'état de la connexion et statistiques."""
    is_logged = ig_manager.is_logged_in()
    session_exists = os.path.exists(SESSION_FILE)
    
    # Récupérer les stats basiques
    stats = db.get_user_stats(update.effective_chat.id)
    
    # Récupérer les stats avancées
    advanced_stats = db.get_advanced_stats(update.effective_chat.id)
    
    status_icon = "✅" if is_logged else "❌"
    status_text = "Connecté" if is_logged else "Non connecté"
    
    message = f"📊 *État du bot*\n\n"
    message += f"{status_icon} Instagram : {status_text}\n"
    message += f"💾 Session sauvegardée : {'✅ Oui' if session_exists else '❌ Non'}\n\n"
    
    message += f"📈 *Statistiques*\n"
    message += f"📅 En attente : {stats.get('pending_count', 0)}\n"
    message += f"✅ Publiées : {stats.get('published_count', 0)}\n"
    message += f"❌ Erreurs : {stats.get('error_count', 0)}\n"
    
    if config.DRAFT_MODE_ENABLED:
        message += f"💾 Brouillons : {stats.get('draft_count', 0)}\n"
    
    # Taux de succès
    if advanced_stats.get('total', 0) > 0:
        success_rate = advanced_stats.get('success_rate', 0)
        message += f"\n🎯 Taux de succès : {success_rate}%\n"
        
        # Heures populaires
        if advanced_stats.get('popular_times'):
            popular = ", ".join(advanced_stats['popular_times'][:3])
            message += f"⏰ Heures favorites : {popular}\n"
        
        # Préférences médias
        prefs = advanced_stats.get('media_preferences', {})
        if prefs:
            message += f"📸 Photos : {prefs.get('photo', 0)} | 🎬 Vidéos : {prefs.get('video', 0)}\n"
    
    message += "\n"
    
    if is_logged:
        message += "🟢 Le bot est prêt à publier tes stories !"
    else:
        message += "🔴 Connexion Instagram requise.\n"
        message += "La première publication déclenchera la connexion.\n"
        message += "Si le 2FA est activé, utilise /code pour entrer le code."
    
    # Boutons d'action
    keyboard = [
        [InlineKeyboardButton("📋 Mes publications", callback_data="list_posts")],
        [InlineKeyboardButton("📊 Stats détaillées", callback_data="detailed_stats")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(message, parse_mode="Markdown", reply_markup=reply_markup)

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gestionnaire du code 2FA Instagram.
    
    Permet à l'utilisateur de transmettre le code d'authentification à deux facteurs.
    """
    if not context.args:
        await update.message.reply_text("Envoie le code 2FA ainsi: /code 123456")
        return

    code = context.args[0].strip()
    if len(code) < 4:
        await update.message.reply_text("Code invalide.")
        return

    # Enregistrer le code 2FA pour l'utiliser lors de la connexion
    set_pending_2fa_code(update.effective_chat.id, code)
    await update.message.reply_text("🔐 Code reçu, tentative de connexion...")

    # Tenter la connexion avec le code
    success = await ig_manager.login(update.effective_chat.id, context, force=True)
    if success:
        await update.message.reply_text("✅ Connexion Instagram validée. La publication programmée pourra se faire.")
    else:
        await update.message.reply_text("❌ Connexion toujours impossible. Vérifie le code ou réessaie.")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestionnaire des boutons inline."""
    query = update.callback_query
    await query.answer()
    
    # Gérer les boutons de temps rapides
    if query.data.startswith("time_"):
        file_id = context.user_data.get('current_media_file_id')
        media_type = context.user_data.get('current_media_type', 'photo')
        to_close_friends = context.user_data.get('to_close_friends', False)
        
        if not file_id:
            await query.message.edit_text(
                "❌ *Session expirée*\n\n"
                "Le média n'est plus disponible. Envoie-le à nouveau.",
                parse_mode="Markdown"
            )
            return
        
        # Traiter le callback de temps
        now = now_tz()
        result = process_quick_time_callback(query.data, now, TIMEZONE)
        
        if result["action"] == "cancel":
            context.user_data.pop('current_media_file_id', None)
            context.user_data.pop('current_media_type', None)
            context.user_data.pop('current_media_file_size', None)
            context.user_data.pop('current_media_filename', None)
            context.user_data.pop('to_close_friends', None)
            await query.message.edit_text(
                "❌ *Publication annulée*\n\n"
                "Envoie un nouveau média quand tu veux !",
                parse_mode="Markdown"
            )
            return
        
        if result["action"] == "manual":
            await query.message.edit_text(
                f"✍️ *Saisie manuelle*\n\n"
                f"{get_datetime_help_text()}",
                parse_mode="Markdown"
            )
            return
        
        # Récupérer la date programmée
        scheduled_time = result.get("scheduled_time")
        if not scheduled_time:
            await query.message.edit_text(
                "❌ *Erreur de traitement*\n\n"
                "Réessaie avec un autre bouton.",
                parse_mode="Markdown",
                reply_markup=get_quick_time_keyboard()
            )
            return
        
        # Valider
        is_valid, error_message = validate_scheduled_time(scheduled_time, now)
        if not is_valid:
            await query.message.edit_text(
                error_message,
                parse_mode="Markdown",
                reply_markup=get_quick_time_keyboard()
            )
            return
        
        # Créer la story
        story = db.create_story(
            chat_id=update.effective_chat.id,
            file_id=file_id,
            scheduled_time=scheduled_time,
            to_close_friends=to_close_friends,
            media_type=media_type,
            file_size_bytes=context.user_data.get('current_media_file_size'),
            original_filename=context.user_data.get('current_media_filename')
        )
        
        if not story:
            await query.message.edit_text(
                "❌ *Erreur technique*\n\n"
                "Impossible de programmer. Réessaie ou contacte le support.",
                parse_mode="Markdown"
            )
            return
        
        # Nettoyer
        context.user_data.pop('current_media_file_id', None)
        context.user_data.pop('current_media_type', None)
        context.user_data.pop('media_timestamp', None)
        context.user_data.pop('current_media_file_size', None)
        context.user_data.pop('current_media_filename', None)
        context.user_data.pop('to_close_friends', None)
        
        # Confirmation pro
        confirmation_msg = create_confirmation_message(
            scheduled_time=scheduled_time,
            reference_time=now,
            media_type=media_type,
            to_close_friends=to_close_friends
        )
        
        keyboard = [
            [InlineKeyboardButton("📋 Mes publications", callback_data="list_posts")],
            [InlineKeyboardButton("➕ Programmer une autre", callback_data="new_post")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.edit_text(
            confirmation_msg,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
        return
    
    if query.data == "audience_everyone":
        context.user_data['to_close_friends'] = False
        keyboard = get_quick_time_keyboard()
        await query.message.edit_text(
            "👥 *Audience sélectionnée : Tout le monde*\n\n"
            "⏰ Choisis l'heure de publication :\n\n"
            "🎯 Utilise les boutons rapides ou envoie un message personnalisé.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    elif query.data == "audience_close_friends":
        context.user_data['to_close_friends'] = True
        keyboard = get_quick_time_keyboard()
        await query.message.edit_text(
            "✨ *Audience sélectionnée : Amis proches*\n\n"
            "⏰ Choisis l'heure de publication :\n\n"
            "🎯 Utilise les boutons rapides ou envoie un message personnalisé.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    elif query.data == "new_post":
        await query.message.reply_text(
            "📸 Envoie-moi une photo pour programmer une nouvelle story !"
        )
    
    elif query.data == "list_posts":
        # Afficher les publications programmées
        user_stories = db.get_user_pending_stories(query.message.chat_id)
        
        if not user_stories:
            keyboard = [
                [InlineKeyboardButton("📸 Programmer une story", callback_data="new_post")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text(
                "📭 *Aucune publication programmée*\n\n"
                "Tu n'as pas encore de story en attente de publication.\n"
                "Envoie-moi une photo pour commencer !",
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            return
        
        message = "📋 *Publications programmées :*\n\n"
        keyboard = []
        
        for idx, story in enumerate(user_stories, 1):
            scheduled_time = datetime.fromisoformat(story["scheduled_time"].replace("Z", "+00:00"))
            scheduled_local = scheduled_time.astimezone(TIMEZONE)
            time_until = scheduled_local - now_tz()
            
            if time_until.total_seconds() > 0:
                hours = int(time_until.total_seconds() // 3600)
                minutes = int((time_until.total_seconds() % 3600) // 60)
                time_str = f"dans {hours}h {minutes}min"
            else:
                time_str = "en cours..."
            
            # Icône selon le type de média
            media_icon = "🎬" if story.get("media_type") == "video" else "📸"
            
            message += f"{idx}. {media_icon} {scheduled_local.strftime('%d/%m/%Y à %H:%M')}\n"
            message += f"   ⏰ {time_str}\n"
            
            # Afficher l'audience si amis proches
            if story.get("to_close_friends"):
                message += f"   ✨ Amis proches\n"
            
            message += "\n"
            
            keyboard.append([
                InlineKeyboardButton(f"❌ Annuler #{idx}", callback_data=f"cancel_{story['id']}")
            ])
        
        keyboard.append([InlineKeyboardButton("🔄 Actualiser", callback_data="list_posts")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    
    elif query.data == "help":
        # Afficher l'aide
        await query.message.reply_text(
            "📖 *Guide d'utilisation*\n\n"
            "*📸🎬 Programmer une story :*\n"
            "1. Envoie une photo (max 20 MB) ou vidéo (max 100 MB, 60s max)\n"
            "2. Choisis l'audience (tout le monde / amis proches)\n"
            "3. Indique la date/heure de publication\n\n"
            "*⏰ Formats acceptés :*\n"
            "• `14:30` - Aujourd'hui à 14h30\n"
            "• `25/12 09:00` - Le 25 déc à 9h\n"
            "• `25/12/2025 09:00` - Format complet\n"
            "• `2025-12-25 09:00` - Format ISO\n\n"
            "*🔐 Authentification 2FA :*\n"
            "Si Instagram demande un code :\n"
            "1. Ouvre ton app Google Authenticator\n"
            "2. Utilise `/code 123456` (ton code à 6 chiffres)\n\n"
            "*📋 Autres commandes :*\n"
            "/list - Liste des publications programmées\n"
            "/cancel - Annuler une publication\n"
            "/status - État de la connexion Instagram\n\n"
            "💬 Besoin d'aide ? Contacte @ZacoFunKy",
            parse_mode="Markdown"
        )
    
    elif query.data == "cancel_media":
        context.user_data.pop('current_media_file_id', None)
        context.user_data.pop('current_media_type', None)
        context.user_data.pop('media_timestamp', None)
        await query.message.edit_text(
            "❌ Média annulé. Envoie une nouvelle photo ou vidéo pour recommencer."
        )
    
    elif query.data.startswith("cancel_"):
        story_id = query.data.replace("cancel_", "")
        success = db.cancel_story(story_id, query.message.chat_id)
        
        if success:
            await query.message.edit_text(
                "✅ *Publication annulée avec succès !*\n\n"
                "La story ne sera pas publiée.",
                parse_mode="Markdown"
            )
        else:
            await query.message.edit_text(
                "⚠️ Cette publication n'existe plus, a déjà été publiée, ou ne t'appartient pas."
            )

async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestionnaire de la commande /help - Affiche l'aide détaillée."""
    datetime_help = get_datetime_help_text()
    
    await update.message.reply_text(
        "📖 *Guide d'utilisation*\n\n"
        "*📸🎬 Programmer une story :*\n"
        "1. Envoie une photo (max 20 MB) ou vidéo (max 100 MB, 60s max)\n"
        "2. Choisis l'audience (tout le monde / amis proches)\n"
        "3. Utilise les boutons rapides ou envoie une date/heure\n\n"
        f"{datetime_help}\n\n"
        "*🔐 Authentification 2FA :*\n"
        "Si Instagram demande un code :\n"
        "1. Ouvre ton app **Google Authenticator**\n"
        "2. Copie le code à 6 chiffres\n"
        "3. Utilise `/code 123456` (remplace par ton code)\n\n"
        "*📋 Autres commandes :*\n"
        "/list - Liste des publications programmées\n"
        "/cancel - Annuler une publication\n"
        "/status - État de la connexion Instagram\n\n"
        "💬 Besoin d'aide ? Contacte @ZacoFunKy",
        parse_mode="Markdown"
    )


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestionnaire de la commande /start - Affiche les instructions d'utilisation."""
    keyboard = [
        [InlineKeyboardButton("📸 Programmer une story", callback_data="new_post")],
        [InlineKeyboardButton("📋 Mes publications", callback_data="list_posts")],
        [InlineKeyboardButton("❓ Aide", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 *Bot Instagram Story Scheduler*\n\n"
        "Bienvenue ! Je t'aide à programmer tes stories Instagram.\n\n"
        "*Comment ça marche ?*\n"
        "1️⃣ Envoie-moi une photo\n"
        "2️⃣ Indique l'heure de publication\n"
        "3️⃣ Je publierai automatiquement ta story !\n\n"
        "*Commandes disponibles :*\n"
        "/list - Voir les publications programmées\n"
        "/cancel - Annuler une programmation\n"
        "/help - Obtenir de l'aide\n"
        "/code - Entrer le code 2FA Instagram\n\n"
        "💡 Astuce : Les photos sont stockées temporairement et supprimées après publication.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

def start_web_server() -> None:
    """Démarre le serveur Flask pour le endpoint de keep-alive."""
    web.run(host="0.0.0.0", port=HTTP_PORT, use_reloader=False)


if __name__ == '__main__':
    # Lancer le petit serveur Flask pour UptimeRobot/Render keep-alive
    threading.Thread(target=start_web_server, daemon=True).start()
    
    # Lancer le worker de vérification des stories (toutes les 60 secondes)
    scheduler.add_job(
        check_and_publish_stories,
        'interval',
        seconds=config.WORKER_CHECK_INTERVAL,
        id='story_publisher_worker'
    )
    logging.info("🔄 Worker de publication démarré (vérification toutes les %ds)", config.WORKER_CHECK_INTERVAL)
    
    # Lancer le worker de retry (toutes les 5 minutes)
    if config.RETRY_ENABLED:
        scheduler.add_job(
            check_and_retry_stories,
            'interval',
            seconds=config.RETRY_CHECK_INTERVAL,
            id='story_retry_worker'
        )
        logging.info("🔄 Worker de retry démarré (vérification toutes les %ds)", config.RETRY_CHECK_INTERVAL)
    
    # Lancer le worker de nettoyage (toutes les 24h)
    if config.CLEANUP_ENABLED:
        scheduler.add_job(
            cleanup_old_stories_job,
            'interval',
            seconds=config.CLEANUP_CHECK_INTERVAL,
            id='story_cleanup_worker'
        )
        logging.info("🧹 Worker de nettoyage démarré (exécution toutes les %dh)", config.CLEANUP_INTERVAL_HOURS)

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("list", handle_list))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("code", handle_code))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.IMAGE | filters.Document.VIDEO, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_time))

    print("🤖 Bot démarré ! Envoie une photo sur Telegram.")
    print(f"✨ Features activées:")
    print(f"   • Retry automatique: {config.RETRY_ENABLED}")
    print(f"   • Nettoyage auto: {config.CLEANUP_ENABLED}")
    print(f"   • Validation médias: {config.MEDIA_VALIDATION_ENABLED}")
    print(f"   • Overlays texte: {config.TEXT_OVERLAY_ENABLED}")
    print(f"   • Overlays musique: {config.MUSIC_OVERLAY_ENABLED}")
    app.run_polling()