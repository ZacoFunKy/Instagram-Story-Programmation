# Fichier: bot.py
import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from instagrapi import Client
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters


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

# Configuration
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
scheduler = BackgroundScheduler()
scheduler.start()

cl = Client()
web = Flask(__name__)


@web.route("/health")
def health():
    return {"status": "ok"}, 200

# Charger une session existante pour éviter de redemander le 2FA à chaque fois
def load_instagram_session():
    if os.path.exists(SESSION_FILE):
        try:
            cl.load_settings(SESSION_FILE)
            logging.info("Session Instagram chargée depuis le disque")
        except Exception as exc:
            logging.warning("Impossible de charger la session Instagram: %s", exc)


def save_instagram_session():
    try:
        cl.dump_settings(SESSION_FILE)
        logging.info("Session Instagram sauvegardée")
    except Exception as exc:
        logging.warning("Sauvegarde de la session Instagram impossible: %s", exc)


def parse_run_date(text: str, now: datetime):
    """Parse l'heure ou la date/heure fournie par l'utilisateur."""
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


def instagram_login(chat_id=None, context=None, force=False):
    """Connexion Instagram avec gestion du 2FA et de la session."""
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

def post_story_job(image_path, chat_id, context):
    logging.info("⏰ Il est l'heure ! Publication de %s", image_path)

    if not instagram_login(chat_id, context):
        logging.warning("Publication annulée: connexion Instagram manquante")
        return

    try:
        cl.photo_upload_to_story(image_path)
        context.bot.send_message(chat_id=chat_id, text="✅ Story publiée avec succès !")
        logging.info("Story publiée")
    except Exception as exc:
        logging.exception("Erreur lors de la publication de la story")
        context.bot.send_message(chat_id=chat_id, text=f"❌ Erreur lors de la publication: {exc}")

# --- GESTION TELEGRAM ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = await update.message.photo[-1].get_file()
    file_name = os.path.join(
        DOWNLOAD_DIR,
        f"story_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg",
    )
    await photo.download_to_drive(file_name)

    context.user_data['current_image'] = file_name
    await update.message.reply_text(
        "📸 Photo reçue !\nEnvoie maintenant l'heure de publication au format HH:MM (ex: 18:30)."
    )

async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    time_str = update.message.text.strip()
    image_path = context.user_data.get('current_image')

    if not image_path:
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

    scheduler.add_job(
        post_story_job,
        'date',
        run_date=run_date,
        args=[image_path, update.effective_chat.id, context],
    )

    context.user_data.pop('current_image', None)

    await update.message.reply_text(f"✅ C'est programmé pour {run_date.strftime('%Y-%m-%d %H:%M')}{day_info} !")


async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Envoie une photo, puis l'heure HH:MM ou une date+heure (ex: 25/12 09:30).\n"
        "Si Instagram demande un code 2FA, utilise /code 123456."
    )

def start_web_server():
    web.run(host="0.0.0.0", port=HTTP_PORT, use_reloader=False)


if __name__ == '__main__':
    # Lancer le petit serveur Flask pour UptimeRobot/Render keep-alive
    threading.Thread(target=start_web_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("code", handle_code))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_time))

    print("🤖 Bot démarré ! Envoie une photo sur Telegram.")
    app.run_polling()