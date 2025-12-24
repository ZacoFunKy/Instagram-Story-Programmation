# Fichier: bot.py
import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask
from instagrapi import Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

# Charger les variables d'environnement depuis .env (pour développement local)
load_dotenv()


def _env_required(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"La variable d'environnement {key} est requise")
    return value


# --- TES INFOS ---
TOKEN = _env_required("TOKEN")  # Dans Render: variable d'env
IG_USER = _env_required("IG_USER")
IG_PASS = _env_required("IG_PASS")
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "downloads")
SESSION_FILE = os.path.join(DOWNLOAD_DIR, "ig_session.json")
HTTP_PORT = int(os.environ.get("PORT", "8000"))

two_factor_code = None
ig_lock = threading.Lock()
scheduled_jobs: dict[str, dict] = {}  # {job_id: {chat_id, file_id, run_date, job}}

# Configuration
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
scheduler = BackgroundScheduler()
scheduler.start()

cl = Client()
web = Flask(__name__)


@web.route("/health")
def health() -> tuple[dict[str, str], int]:
    """Endpoint de santé pour UptimeRobot/Render keep-alive."""
    return {"status": "ok"}, 200


def load_instagram_session() -> None:
    """Charger une session Instagram existante depuis le disque."""
    if os.path.exists(SESSION_FILE):
        try:
            cl.load_settings(SESSION_FILE)
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


def parse_run_date(text: str, now: datetime) -> tuple[datetime | None, bool]:
    """
    Parse l'heure ou la date/heure fournie par l'utilisateur.
    
    Args:
        text: Chaîne au format HH:MM, JJ/MM HH:MM, JJ/MM/AAAA HH:MM ou AAAA-MM-JJ HH:MM
        now: Datetime actuelle pour référence
    
    Returns:
        Tuple (datetime parsée ou None, booléen indiquant si date explicite)
    """
    text = text.strip()
    formats_with_date = [
        ("%Y-%m-%d %H:%M", True),
        ("%d/%m/%Y %H:%M", True),
        ("%d/%m %H:%M", True),
    ]

    for fmt, explicit_date in formats_with_date:
        try:
            dt = datetime.strptime(text, fmt)
            if fmt == "%d/%m %H:%M":
                dt = dt.replace(year=now.year)
            return dt, explicit_date
        except ValueError:
            pass

    try:
        t = datetime.strptime(text, "%H:%M").time()
        return datetime.combine(now.date(), t), False
    except ValueError:
        return None, False


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
            if not force and cl.user_id:
                return True

            cl.login(IG_USER, IG_PASS, verification_code=two_factor_code)
            two_factor_code = None
            save_instagram_session()
            return True
        except Exception as exc:
            msg = str(exc)
            logging.error("Connexion Instagram échouée: %s", msg)

            if "Two-factor" in msg or "verification_code" in msg:
                if context and chat_id:
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "🔐 Instagram demande un code 2FA. "
                            "Envoie /code 123456 pour transmettre le code reçu par SMS/app."
                        ),
                    )
            else:
                if context and chat_id:
                    context.bot.send_message(chat_id=chat_id, text=f"❌ Login Instagram impossible: {msg}")
            return False

# Dossier pour stocker les images
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

load_instagram_session()

def post_story_job(file_id: str, chat_id: int, bot, job_id: str) -> None:
    """
    Tâche planifiée pour publier une story Instagram.
    
    Args:
        file_id: ID du fichier Telegram à télécharger
        chat_id: ID du chat Telegram pour notifications
        bot: Instance du bot Telegram
        job_id: ID unique du job pour le suivi
    """
    logging.info("⏰ Il est l'heure ! Téléchargement et publication de la photo...")
    
    # Supprimer de la liste des jobs programmés
    scheduled_jobs.pop(job_id, None)
    
    image_path = None
    try:
        # Télécharger l'image juste avant de poster
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def download_file():
            from telegram import Bot
            temp_bot = Bot(token=TOKEN)
            file = await temp_bot.get_file(file_id)
            path = os.path.join(DOWNLOAD_DIR, f"temp_story_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
            await file.download_to_drive(path)
            return path
        
        image_path = loop.run_until_complete(download_file())
        loop.close()
        
        if not instagram_login(chat_id, None):
            logging.warning("Publication annulée: connexion Instagram manquante")
            bot.send_message(chat_id=chat_id, text="❌ Publication annulée: connexion Instagram manquante")
            return

        cl.photo_upload_to_story(image_path)
        bot.send_message(chat_id=chat_id, text="✅ Story publiée avec succès !")
        logging.info("Story publiée")
        
    except Exception as exc:
        logging.exception("Erreur lors de la publication de la story")
        bot.send_message(chat_id=chat_id, text=f"❌ Erreur lors de la publication: {exc}")
    finally:
        # Nettoyer le fichier temporaire
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)
                logging.info("Fichier temporaire supprimé: %s", image_path)
            except Exception as e:
                logging.warning("Impossible de supprimer le fichier temporaire: %s", e)

# --- GESTION TELEGRAM ---async def handle_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestionnaire de la commande /list - Affiche les publications programmées."""
    user_jobs = [
        (job_id, info) for job_id, info in scheduled_jobs.items()
        if info['chat_id'] == update.effective_chat.id
    ]
    
    if not user_jobs:
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
    
    for idx, (job_id, info) in enumerate(user_jobs, 1):
        run_date = info['run_date']
        time_until = run_date - datetime.now()
        
        if time_until.total_seconds() > 0:
            hours = int(time_until.total_seconds() // 3600)
            minutes = int((time_until.total_seconds() % 3600) // 60)
            time_str = f"dans {hours}h {minutes}min"
        else:
            time_str = "en cours..."
        
        message += f"{idx}. 📅 {run_date.strftime('%d/%m/%Y à %H:%M')}\n"
        message += f"   ⏰ {time_str}\n\n"
        
        keyboard.append([
            InlineKeyboardButton(f"❌ Annuler #{idx}", callback_data=f"cancel_{job_id}")
        ])
    
    keyboard.append([InlineKeyboardButton("🔄 Actualiser", callback_data="list_posts")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        message,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gestionnaire de réception de photos.
    
    Stocke l'ID du fichier photo sans téléchargement pour économiser l'espace disque.
    """
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    # Vérifier la taille de la photo
    if photo.file_size and photo.file_size > 10 * 1024 * 1024:  # 10 MB
        await update.message.reply_text(
            "⚠️ La photo est trop volumineuse (max 10 MB).\n"
            "Envoie une photo plus légère."
        )
        return
    
    # Stocker uniquement l'ID du fichier Telegram
    context.user_data['current_photo_file_id'] = file_id
    context.user_data['photo_timestamp'] = datetime.now()
    
    keyboard = [
        [InlineKeyboardButton("❌ Annuler", callback_data="cancel_photo")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📸 *Photo reçue avec succès !*\n\n"
        "📅 Maintenant, envoie l'heure ou la date de publication :\n\n"
        "• `14:30` - aujourd'hui à 14h30\n"
        "• `25/12 09:00` - le 25 décembre à 9h\n"
        "• `2025-12-31 23:59` - format complet\n\n"
        "💡 Si l'heure est déjà passée, la publication sera programmée pour demain.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestionnaire de la commande /cancel - Annule la saisie en cours."""
    if 'current_photo_file_id' in context.user_data:
        context.user_data.pop('current_photo_file_id', None)
        context.user_data.pop('photo_timestamp', None)
        await update.message.reply_text(
            "❌ *Programmation annulée*\n\n"
            "La photo en attente a été supprimée.\n"
            "Envoie une nouvelle photo pour recommencer.",
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
    file_id = context.user_data.get('current_photo_file_id')

    if not file_id:
        await update.message.reply_text("❌ Envoie d'abord une photo !")
        return

    now = datetime.now()
    run_date, explicit_date = parse_run_date(time_str, now)
    if not run_date:
        await update.message.reply_text(
            "Format invalide. Utilise HH:MM (ex: 14:05) ou une date+heure (ex: 25/12 09:30)."
        )
        return

    if run_date <= now:
        if explicit_date:
            await update.message.reply_text("⚠️ Cette date est déjà passée. Donne une date future.")
            return
        run_date += timedelta(days=1)
        day_info = " (demain)"
    else:
        day_info = ""

    # Créer un ID unique pour ce job
    job_id = f"{update.effective_chat.id}_{int(run_date.timestamp())}"
    
    job = scheduler.add_job(
        post_story_job,
        'date',
        run_date=run_date,
        args=[file_id, update.effective_chat.id, context.bot, job_id],
        id=job_id
    )
    
    # Sauvegarder dans le dictionnaire de tracking
    scheduled_jobs[job_id] = {
        'chat_id': update.effective_chat.id,
        'file_id': file_id,
        'run_date': run_date,
        'job': job
    }

    context.user_data.pop('current_photo_file_id', None)
    context.user_data.pop('photo_timestamp', None)
    
    time_until = run_date - datetime.now()
    hours = int(time_until.total_seconds() // 3600)
    minutes = int((time_until.total_seconds() % 3600) // 60)
    
    keyboard = [
        [InlineKeyboardButton("📋 Voir mes publications", callback_data="list_posts")],
        [InlineKeyboardButton("📸 Programmer une autre story", callback_data="new_post")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ *Publication programmée avec succès !*\n\n"
        f"📅 Date : {run_date.strftime('%d/%m/%Y à %H:%M')}{day_info}\n"
        f"⏰ Dans : {hours}h {minutes}min\n\n"
        f"🔔 Tu recevras une notification quand la story sera publiée.\n"
        f"📌 Utilise /list pour voir toutes tes publications programmées.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestionnaire de la commande /status - Affiche l'état de la connexion."""
    is_logged = bool(cl.user_id)
    session_exists = os.path.exists(SESSION_FILE)
    
    status_icon = "✅" if is_logged else "❌"
    status_text = "Connecté" if is_logged else "Non connecté"
    
    message = f"📊 *État du bot*\n\n"
    message += f"{status_icon} Instagram : {status_text}\n"
    message += f"💾 Session sauvegardée : {'✅ Oui' if session_exists else '❌ Non'}\n"
    message += f"📅 Publications programmées : {len([j for j in scheduled_jobs.values() if j['chat_id'] == update.effective_chat.id])}\n\n"
    
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
    
    if query.data == "new_post":
        await query.message.reply_text(
            "📸 Envoie-moi une photo pour programmer une nouvelle story !"
        )
    
    elif query.data == "list_posts":
        # Simuler la commande /list
        update.message = query.message
        await handle_list(update, context)
    
    elif query.data == "help":
        # Simuler la commande /help
        update.message = query.message
        await handle_help(update, context)
    
    elif query.data == "cancel_photo":
        context.user_data.pop('current_photo_file_id', None)
        context.user_data.pop('photo_timestamp', None)
        await query.message.edit_text(
            "❌ Photo annulée. Envoie une nouvelle photo pour recommencer."
        )
    
    elif query.data.startswith("cancel_"):
        job_id = query.data.replace("cancel_", "")
        if job_id in scheduled_jobs and scheduled_jobs[job_id]['chat_id'] == query.message.chat_id:
            try:
                scheduled_jobs[job_id]['job'].remove()
                scheduled_jobs.pop(job_id)
                await query.message.edit_text(
                    "✅ *Publication annulée avec succès !*\n\n"
                    "La story ne sera pas publiée.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                await query.message.edit_text(
                    f"❌ Erreur lors de l'annulation : {e}"
                )
        else:
            await query.message.edit_text(
                "⚠️ Cette publication n'existe plus ou a déjà été publiée."
            )

async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gestionnaire de la commande /help - Affiche l'aide détaillée."""
    await update.message.reply_text(
        "📖 *Guide d'utilisation*\n\n"
        "*📸 Programmer une story :*\n"
        "1. Envoie une photo (max 10 MB)\n"
        "2. Indique la date/heure de publication\n\n"
        "*⏰ Formats acceptés :*\n"
        "• `14:30` - Aujourd'hui à 14h30\n"
        "• `25/12 09:00` - Le 25 déc à 9h\n"
        "• `25/12/2025 09:00` - Format complet\n"
        "• `2025-12-25 09:00` - Format ISO\n\n"
        "*🔐 Authentification 2FA :*\n"
        "Si Instagram demande un code :\n"
        "1. Consulte ton app d'authentification\n"
        "2. Utilise `/code 123456` (remplace par ton code)\n\n"
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

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    app.add_handler(CommandHandler("list", handle_list))
    app.add_handler(CommandHandler("cancel", handle_cancel))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_handler(CommandHandler("code", handle_code))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_time))

    print("🤖 Bot démarré ! Envoie une photo sur Telegram.")
    app.run_polling()