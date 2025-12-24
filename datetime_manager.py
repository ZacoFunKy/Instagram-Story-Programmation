"""
Module professionnel de parsing et gestion des dates/heures.

Fonctionnalités:
- Parsing intelligent de formats multiples
- Suggestions de dates/heures via boutons Telegram
- Validation complète
- Support timezone
- Interface utilisateur intuitive
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Formats de date supportés (ordre de priorité)
DATE_FORMATS = [
    # Format ISO complet
    ("%Y-%m-%d %H:%M:%S", "ISO avec secondes", "2025-12-25 14:30:00"),
    ("%Y-%m-%d %H:%M", "ISO", "2025-12-25 14:30"),
    
    # Format français complet
    ("%d/%m/%Y %H:%M:%S", "FR avec secondes", "25/12/2025 14:30:00"),
    ("%d/%m/%Y %H:%M", "FR complet", "25/12/2025 14:30"),
    ("%d/%m %H:%M", "FR court", "25/12 14:30"),
    
    # Format US
    ("%m/%d/%Y %H:%M", "US", "12/25/2025 14:30"),
    
    # Heure seule
    ("%H:%M:%S", "Heure+sec", "14:30:00"),
    ("%H:%M", "Heure", "14:30"),
    
    # Formats naturels
    ("%d %B %Y %H:%M", "Naturel FR", "25 décembre 2025 14:30"),
    ("%B %d %Y %H:%M", "Naturel US", "December 25 2025 14:30"),
]


def parse_datetime(
    text: str,
    reference_time: datetime,
    timezone: ZoneInfo
) -> Tuple[Optional[datetime], bool, Optional[str]]:
    """
    Parse une date/heure depuis un texte avec support de multiples formats.
    
    Args:
        text: Texte à parser (ex: "14:30", "25/12 09:00", etc.)
        reference_time: Datetime de référence (maintenant)
        timezone: Timezone à appliquer
    
    Returns:
        Tuple (datetime parsé ou None, date explicite?, format utilisé)
    """
    text = text.strip()
    
    # Essayer tous les formats
    for fmt, name, example in DATE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            
            # Si seulement l'heure, utiliser la date du jour
            if fmt in ("%H:%M", "%H:%M:%S"):
                dt = datetime.combine(reference_time.date(), dt.time())
                dt = dt.replace(tzinfo=timezone)
                return dt, False, name
            
            # Si jour/mois sans année, utiliser l'année actuelle
            elif fmt == "%d/%m %H:%M":
                dt = dt.replace(year=reference_time.year)
                dt = dt.replace(tzinfo=timezone)
                return dt, True, name
            
            # Date complète
            else:
                dt = dt.replace(tzinfo=timezone)
                return dt, True, name
                
        except ValueError:
            continue
    
    # Format naturel en français (demain, après-demain, etc.)
    text_lower = text.lower()
    
    if "demain" in text_lower or "tomorrow" in text_lower:
        # Extraire l'heure si présente
        time_part = text_lower.split()[-1] if " " in text_lower else "09:00"
        try:
            time_obj = datetime.strptime(time_part, "%H:%M").time()
            dt = datetime.combine(
                (reference_time + timedelta(days=1)).date(),
                time_obj
            ).replace(tzinfo=timezone)
            return dt, True, "Naturel (demain)"
        except:
            pass
    
    if "après-demain" in text_lower or "overmorrow" in text_lower:
        time_part = text_lower.split()[-1] if " " in text_lower else "09:00"
        try:
            time_obj = datetime.strptime(time_part, "%H:%M").time()
            dt = datetime.combine(
                (reference_time + timedelta(days=2)).date(),
                time_obj
            ).replace(tzinfo=timezone)
            return dt, True, "Naturel (après-demain)"
        except:
            pass
    
    # Aucun format reconnu
    return None, False, None


def get_quick_time_keyboard() -> InlineKeyboardMarkup:
    """
    Crée un clavier avec des heures rapides communes.
    
    Returns:
        Clavier inline avec boutons d'heures
    """
    keyboard = [
        [
            InlineKeyboardButton("🌅 08:00", callback_data="time_08:00"),
            InlineKeyboardButton("☕ 09:00", callback_data="time_09:00"),
            InlineKeyboardButton("🌞 12:00", callback_data="time_12:00"),
        ],
        [
            InlineKeyboardButton("🌆 14:00", callback_data="time_14:00"),
            InlineKeyboardButton("🌃 18:00", callback_data="time_18:00"),
            InlineKeyboardButton("🌙 20:00", callback_data="time_20:00"),
        ],
        [
            InlineKeyboardButton("🕐 Dans 1h", callback_data="time_+1h"),
            InlineKeyboardButton("🕑 Dans 2h", callback_data="time_+2h"),
            InlineKeyboardButton("🕒 Dans 3h", callback_data="time_+3h"),
        ],
        [
            InlineKeyboardButton("📅 Demain 09:00", callback_data="time_tomorrow_09:00"),
            InlineKeyboardButton("📅 Demain 18:00", callback_data="time_tomorrow_18:00"),
        ],
        [
            InlineKeyboardButton("✍️ Saisir manuellement", callback_data="time_manual"),
            InlineKeyboardButton("❌ Annuler", callback_data="cancel_media"),
        ]
    ]
    
    return InlineKeyboardMarkup(keyboard)


def process_quick_time_callback(
    callback_data: str,
    reference_time: datetime,
    timezone: ZoneInfo
) -> Optional[datetime]:
    """
    Traite un callback de sélection rapide d'heure.
    
    Args:
        callback_data: Données du callback (ex: "time_14:00")
        reference_time: Datetime de référence
        timezone: Timezone à appliquer
    
    Returns:
        Datetime calculé ou None si invalide
    """
    if not callback_data.startswith("time_"):
        return None
    
    time_value = callback_data.replace("time_", "")
    
    # Relatif: +1h, +2h, +3h
    if time_value.startswith("+"):
        try:
            hours = int(time_value[1:-1])  # Extraire le chiffre
            return reference_time + timedelta(hours=hours)
        except:
            return None
    
    # Demain
    elif time_value.startswith("tomorrow_"):
        time_str = time_value.replace("tomorrow_", "")
        try:
            time_obj = datetime.strptime(time_str, "%H:%M").time()
            dt = datetime.combine(
                (reference_time + timedelta(days=1)).date(),
                time_obj
            ).replace(tzinfo=timezone)
            return dt
        except:
            return None
    
    # Heure spécifique aujourd'hui
    else:
        try:
            time_obj = datetime.strptime(time_value, "%H:%M").time()
            dt = datetime.combine(reference_time.date(), time_obj).replace(tzinfo=timezone)
            
            # Si l'heure est déjà passée, programmer pour demain
            if dt <= reference_time:
                dt += timedelta(days=1)
            
            return dt
        except:
            return None


def validate_scheduled_time(
    scheduled_time: datetime,
    reference_time: datetime,
    min_delay_minutes: int = 2
) -> Tuple[bool, Optional[str]]:
    """
    Valide une heure de publication programmée.
    
    Args:
        scheduled_time: Heure programmée
        reference_time: Heure actuelle
        min_delay_minutes: Délai minimum en minutes
    
    Returns:
        Tuple (valide?, message d'erreur optionnel)
    """
    # Vérifier que c'est dans le futur
    if scheduled_time <= reference_time:
        return False, "⚠️ L'heure doit être dans le futur."
    
    # Vérifier délai minimum
    min_time = reference_time + timedelta(minutes=min_delay_minutes)
    if scheduled_time < min_time:
        return False, f"⚠️ Délai minimum: {min_delay_minutes} minutes."
    
    # Vérifier pas trop loin (1 an max)
    max_time = reference_time + timedelta(days=365)
    if scheduled_time > max_time:
        return False, "⚠️ Date trop éloignée (max 1 an)."
    
    return True, None


def format_time_until(target_time: datetime, reference_time: datetime) -> str:
    """
    Formate le temps restant de manière lisible.
    
    Args:
        target_time: Heure cible
        reference_time: Heure actuelle
    
    Returns:
        Chaîne formatée (ex: "dans 2h 30min")
    """
    delta = target_time - reference_time
    
    if delta.total_seconds() < 0:
        return "en cours..."
    
    days = delta.days
    hours = int(delta.total_seconds() // 3600) % 24
    minutes = int((delta.total_seconds() % 3600) // 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}j")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}min")
    
    return "dans " + " ".join(parts)


def get_datetime_help_text() -> str:
    """
    Retourne le texte d'aide pour les formats de date/heure.
    
    Returns:
        Texte d'aide formaté
    """
    return (
        "📅 *Formats de date/heure acceptés :*\n\n"
        
        "*🕐 Heure seule (aujourd'hui) :*\n"
        "• `14:30` - Aujourd'hui à 14h30\n"
        "• `09:00` - Aujourd'hui à 9h00\n"
        "_(Si l'heure est passée, ce sera demain)_\n\n"
        
        "*📆 Date + Heure :*\n"
        "• `25/12 14:30` - Le 25 décembre à 14h30\n"
        "• `25/12/2025 14:30` - Date complète\n"
        "• `2025-12-25 14:30` - Format ISO\n\n"
        
        "*🗣️ Formats naturels :*\n"
        "• `demain 09:00` - Demain matin\n"
        "• `après-demain 18:00` - Après-demain soir\n\n"
        
        "*⚡ Relatif :*\n"
        "• Utilise les boutons rapides !\n"
        "• Dans 1h, 2h, 3h disponibles\n\n"
        
        "💡 *Astuce* : Utilise les boutons pour aller plus vite !"
    )


def create_confirmation_message(
    scheduled_time: datetime,
    reference_time: datetime,
    media_type: str,
    to_close_friends: bool
) -> str:
    """
    Crée un message de confirmation professionnel.
    
    Args:
        scheduled_time: Heure programmée
        reference_time: Heure actuelle
        media_type: Type de média (photo/video)
        to_close_friends: Audience restreinte?
    
    Returns:
        Message formaté
    """
    time_until = format_time_until(scheduled_time, reference_time)
    
    media_icon = "🎬" if media_type == "video" else "📸"
    media_name = "Vidéo" if media_type == "video" else "Photo"
    
    audience_icon = "✨" if to_close_friends else "👥"
    audience_text = "Amis proches uniquement" if to_close_friends else "Tout le monde"
    
    day_name = scheduled_time.strftime("%A")
    day_names_fr = {
        "Monday": "Lundi",
        "Tuesday": "Mardi",
        "Wednesday": "Mercredi",
        "Thursday": "Jeudi",
        "Friday": "Vendredi",
        "Saturday": "Samedi",
        "Sunday": "Dimanche"
    }
    day_name = day_names_fr.get(day_name, day_name)
    
    return (
        f"✅ *{media_name} programmée avec succès !*\n\n"
        f"{media_icon} *Type* : {media_name}\n"
        f"{audience_icon} *Audience* : {audience_text}\n\n"
        f"📅 *Date* : {day_name} {scheduled_time.strftime('%d/%m/%Y')}\n"
        f"🕐 *Heure* : {scheduled_time.strftime('%H:%M')}\n"
        f"⏰ *Publication* : {time_until}\n\n"
        f"🔔 Tu recevras une notification quand la story sera publiée.\n"
        f"📋 Utilise /list pour voir toutes tes publications."
    )
