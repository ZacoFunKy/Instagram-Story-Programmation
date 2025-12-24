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
from instagrapi import Client
import requests
try:
    import pyotp  # Optional: for Google Authenticator TOTP
except Exception:  # pragma: no cover
    pyotp = None
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

two_factor_code = None
ig_lock = threading.Lock()

# Configuration
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
scheduler = BackgroundScheduler()
scheduler.start()

# Initialisation
cl = Client()
web = Flask(__name__)
db = DBManager(SUPABASE_URL, SUPABASE_KEY)

# Configurer un proxy si fourni
if PROXY_URL:
    try:
        cl.set_proxy(PROXY_URL)
        logging.info("Proxy configuré pour Instagram API")
    except Exception as e:
        logging.warning("Impossible de configurer le proxy: %s", e)

# Ralentir les requêtes pour paraître plus humain
try:
    cl.delay_range = [1, 3]
except Exception:
    pass


@web.route("/health")
def health() -> tuple[dict[str, str], int]:
    """Endpoint de santé pour UptimeRobot/Render keep-alive."""
    return {"status": "ok"}, 200


def now_tz() -> datetime:
    """Retourne datetime actuel avec la timezone configurée."""
    return datetime.now(TIMEZONE)


def load_instagram_session() -> None:
    """Charger une session Instagram existante depuis le disque."""
    if os.path.exists(SESSION_FILE):
        try:
            cl.load_settings(SESSION_FILE)
            if PROXY_URL:
                cl.set_proxy(PROXY_URL)
            logging.info("Session Instagram chargée depuis le disque")
        except Exception as exc:
            logging.warning("Impossible de charger la session Instagram: %s", exc)


def save_instagram_session() -> None:
    """Sauvegarder la session Instagram sur le disque."""
    try:
        cl.dump_settings(SESSION_FILE)
        logging.info("Session Instagram sauvegardée")
    except Exception as exc:
        logging.warning("Sauvegarde de la session Instagram impossible: %s", exc)


def instagram_login(
    chat_id: int | None = None,
    context: ContextTypes.DEFAULT_TYPE | None = None,
    force: bool = False
) -> bool:
    """
    Connexion Instagram avec gestion du 2FA et de la session.
    
    Args:
        chat_id: ID du chat Telegram pour notifications (optionnel)
        context: Contexte Telegram pour envoyer des messages (optionnel)
        force: Forcer la reconnexion même si déjà connecté
    
    Returns:
        True si connexion réussie, False sinon
    """
    global two_factor_code
    with ig_lock:
        try:
            # Si déjà connecté et pas de force, retourner True
            if not force and cl.user_id:
                return True

            # Option 1: login par sessionid (bypasse mot de passe/2FA)
            if IG_SESSIONID:
                try:
                    cl.login_by_sessionid(IG_SESSIONID)
                    save_instagram_session()
                    logging.info("✅ Connexion Instagram via sessionid")
                    return True
                except Exception as sessionid_exc:
                    logging.warning("Echec login sessionid: %s", sessionid_exc)

            # Essayer de charger la session d'abord
            if os.path.exists(SESSION_FILE) and not force:
                try:
                    cl.load_settings(SESSION_FILE)
                    cl.login(IG_USER, IG_PASS)
                    logging.info("✅ Connexion Instagram via session sauvegardée")
                    return True
                except Exception as session_exc:
                    logging.warning("Impossible de réutiliser la session: %s", session_exc)

            # Connexion normale avec 2FA si nécessaire
            # Générer un code TOTP si secret fourni et aucun code /code en attente
            code_to_use = two_factor_code
            used_totp = False
            if not code_to_use and IG_TOTP_SECRET and pyotp:
                try:
                    sanitized_secret = IG_TOTP_SECRET.replace(" ", "").strip().upper()
                    code_to_use = pyotp.TOTP(sanitized_secret).now()
                    used_totp = True
                    logging.info("Code TOTP généré automatiquement pour 2FA")
                except Exception as e:
                    logging.warning("Impossible de générer le TOTP: %s", e)

            try:
                cl.login(IG_USER, IG_PASS, verification_code=code_to_use)
            except Exception as first_exc:
                # Si le code 2FA semble invalide et qu'on utilise TOTP, retenter une seule fois
                msg1 = str(first_exc).lower()
                if used_totp and (
                    "code" in msg1 or "two-factor" in msg1 or "verification" in msg1
                ) and IG_TOTP_SECRET and pyotp:
                    try:
                        sanitized_secret = IG_TOTP_SECRET.replace(" ", "").strip().upper()
                        new_code = pyotp.TOTP(sanitized_secret).now()
                        logging.info("Retry login avec un nouveau TOTP")
                        cl.login(IG_USER, IG_PASS, verification_code=new_code)
                    except Exception:
                        raise first_exc
                else:
                    raise first_exc
            two_factor_code = None
            save_instagram_session()
            logging.info("✅ Connexion Instagram réussie")
            return True
            
        except Exception as exc:
            msg = str(exc)
            logging.error("Connexion Instagram échouée: %s", msg)

            if "Two-factor" in msg or "verification_code" in msg or "challenge_required" in msg:
                if context and chat_id:
                    # Programmer l'envoi sur la boucle de l'application Telegram
                    context.application.create_task(
                        context.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                "🔐 *Code 2FA requis*\n\n"
                                "Instagram demande un code d'authentification.\n"
                                "📱 Ouvre ton **Google Authenticator** et copie le code à 6 chiffres.\n\n"
                                "💡 Utilise : `/code 123456`\n"
                                "(Remplace par le code de ton app)"
                            ),
                            parse_mode="Markdown"
                        )
                    )
            elif "blacklist" in msg.lower() or "bad password" in msg.lower():
                # IP Render blacklistée ou mot de passe refusé côté serveur
                help_text = (
                    "❌ Connexion refusée par Instagram.\n\n"
                    "Causes probables :\n"
                    "• IP du serveur bloquée (blacklist)\n"
                    "• Mot de passe refusé\n\n"
                    "Solutions :\n"
                    "1) Faire une 1ère connexion en local (2FA) pour créer la session,\n"
                    "   puis copier `ig_session.json` vers `/data` sur Render.\n"
                    "2) Fournir `IG_SESSIONID` (cookie session) dans les variables d'environnement.\n"
                    "3) Configurer un proxy propre via `PROXY_URL`."
                )
                if context and chat_id:
                    context.application.create_task(
                        context.bot.send_message(chat_id=chat_id, text=help_text)
                    )
            else:
                if context and chat_id:
                    context.application.create_task(
                        context.bot.send_message(
                            chat_id=chat_id,
                            text=f"❌ Connexion Instagram impossible: {msg}"
                        )
                    )
            return False

# Dossier pour stocker les images
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

load_instagram_session()


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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
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
        
        media_path = loop.run_until_complete(download_file())
        loop.close()
        
        # Connexion Instagram
        if not instagram_login(chat_id, None):
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
        
        # Publication selon le type de média
        media_response = None
        try:
            if media_type == "video":
                if to_close_friends:
                    logging.info("🎬 Publication vidéo pour amis proches...")
                    # Audience close friends via extra_data
                    extra_data = {"audience": "besties"}
                    media_response = cl.video_upload_to_story(media_path, extra_data=extra_data)
                    logging.info("🎬 Vidéo publiée pour amis proches ✨")
                else:
                    media_response = cl.video_upload_to_story(media_path)
                    logging.info("🎬 Vidéo publiée sur Instagram")
            else:
                if to_close_friends:
                    logging.info("📸 Publication photo pour amis proches...")
                    # Audience close friends via extra_data
                    extra_data = {"audience": "besties"}
                    media_response = cl.photo_upload_to_story(media_path, extra_data=extra_data)
                    logging.info("📸 Photo publiée pour amis proches ✨")
                else:
                    media_response = cl.photo_upload_to_story(media_path)
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
                    exc
                )
                
    except Exception as exc:
        logging.error("Erreur dans le worker de publication: %s", exc)


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
    """Gestionnaire de la commande /status - Affiche l'état de la connexion."""
    is_logged = bool(cl.user_id)
    session_exists = os.path.exists(SESSION_FILE)
    
    # Récupérer les stats depuis la BDD
    stats = db.get_user_stats(update.effective_chat.id)
    
    status_icon = "✅" if is_logged else "❌"
    status_text = "Connecté" if is_logged else "Non connecté"
    
    message = f"📊 *État du bot*\n\n"
    message += f"{status_icon} Instagram : {status_text}\n"
    message += f"💾 Session sauvegardée : {'✅ Oui' if session_exists else '❌ Non'}\n"
    message += f"📅 Publications programmées : {stats.get('pending_count', 0)}\n"
    message += f"✅ Publications réussies : {stats.get('published_count', 0)}\n"
    message += f"❌ Publications échouées : {stats.get('error_count', 0)}\n\n"
    
    if is_logged:
        message += "🟢 Le bot est prêt à publier tes stories !"
    else:
        message += "🔴 Connexion Instagram requise.\n"
        message += "La première publication déclenchera la connexion.\n"
        message += "Si le 2FA est activé, utilise /code pour entrer le code."
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gestionnaire du code 2FA Instagram.
    
    Permet à l'utilisateur de transmettre le code d'authentification à deux facteurs.
    """
    global two_factor_code

    if not context.args:
        await update.message.reply_text("Envoie le code 2FA ainsi: /code 123456")
        return

    code = context.args[0].strip()
    if len(code) < 4:
        await update.message.reply_text("Code invalide.")
        return

    two_factor_code = code
    await update.message.reply_text("🔐 Code reçu, tentative de connexion...")

    success = await asyncio.to_thread(instagram_login, update.effective_chat.id, context, True)
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
        seconds=60,
        id='story_publisher_worker'
    )
    logging.info("🔄 Worker de publication démarré (vérification toutes les 60s)")

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
    app.run_polling()