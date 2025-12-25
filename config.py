"""
Configuration centralisée pour le bot Instagram Story Scheduler.

Ce module contient toutes les constantes, limites et paramètres configurables.
"""

from datetime import timedelta

# =============================================================================
# LIMITES ET QUOTAS
# =============================================================================

# Limite de stories par utilisateur
MAX_PENDING_STORIES_PER_USER = 25  # Maximum de stories en attente par utilisateur
MAX_STORIES_PER_DAY = 50  # Maximum de stories publiées par jour par utilisateur

# Tailles de fichiers (en octets)
MAX_PHOTO_SIZE_MB = 20
MAX_VIDEO_SIZE_MB = 100
MAX_PHOTO_SIZE = MAX_PHOTO_SIZE_MB * 1024 * 1024  # 20 MB
MAX_VIDEO_SIZE = MAX_VIDEO_SIZE_MB * 1024 * 1024  # 100 MB

# Durées vidéo
MAX_VIDEO_DURATION_SECONDS = 60  # Instagram Stories max 60s
MIN_VIDEO_DURATION_SECONDS = 1


# =============================================================================
# RETRY ET ERREURS
# =============================================================================

# Système de retry automatique
RETRY_ENABLED = True
RETRY_MAX_ATTEMPTS = 3  # Nombre maximum de tentatives
RETRY_DELAYS_MINUTES = [5, 15, 60]  # Délais entre tentatives (exponentiel)

# Erreurs qui déclenchent un retry
RETRYABLE_ERRORS = [
    "timeout",
    "connection",
    "network",
    "temporarily unavailable",
    "rate limit",
    "429",  # Too Many Requests
    "500",  # Internal Server Error
    "502",  # Bad Gateway
    "503",  # Service Unavailable
]


# =============================================================================
# PROGRAMMATION ET VALIDATION
# =============================================================================

# Délais minimaux et maximaux
MIN_SCHEDULE_DELAY_MINUTES = 1  # Minimum 1 minutes dans le futur
MAX_SCHEDULE_DELAY_DAYS = 365  # Maximum 1 an dans le futur

# Validation temporelle
SCHEDULE_VALIDATION_ENABLED = True


# =============================================================================
# NETTOYAGE ET MAINTENANCE
# =============================================================================

# Nettoyage automatique
CLEANUP_ENABLED = True
CLEANUP_INTERVAL_HOURS = 24  # Nettoyer toutes les 24h
CLEANUP_PUBLISHED_AFTER_DAYS = 30  # Supprimer stories publiées > 30j
CLEANUP_ERROR_AFTER_DAYS = 7  # Supprimer stories en erreur > 7j
CLEANUP_CANCELLED_AFTER_DAYS = 7  # Supprimer stories annulées > 7j


# =============================================================================
# MÉDIAS ET OPTIMISATION
# =============================================================================

# Dimensions optimales pour Instagram Stories
STORY_WIDTH = 1080
STORY_HEIGHT = 1920
STORY_ASPECT_RATIO = 9 / 16  # Format vertical

# Validation des médias
MEDIA_VALIDATION_ENABLED = True
VALIDATE_ASPECT_RATIO = True
ASPECT_RATIO_TOLERANCE = 0.1  # Tolérance de 10%

# Formats acceptés
ALLOWED_IMAGE_FORMATS = ["JPEG", "JPG", "PNG", "WEBP"]
ALLOWED_VIDEO_FORMATS = ["MP4", "MOV", "AVI"]
ALLOWED_AUDIO_FORMATS = ["MP3", "M4A", "WAV", "OGG"]

# Compression
JPEG_QUALITY = 95
VIDEO_BITRATE_MBPS = 5
VIDEO_FPS = 30


# =============================================================================
# OVERLAYS (TEXTE, MUSIQUE, STICKERS)
# =============================================================================

# Support des overlays
OVERLAYS_ENABLED = True
TEXT_OVERLAY_ENABLED = True
MUSIC_OVERLAY_ENABLED = True
STICKERS_OVERLAY_ENABLED = False  # À implémenter plus tard

# Limites texte
MAX_TEXT_LENGTH = 200
DEFAULT_FONT_SIZE = 60
DEFAULT_TEXT_COLOR = "#FFFFFF"
DEFAULT_TEXT_POSITION = "center"  # top, center, bottom

# Limites musique
MAX_MUSIC_DURATION_SECONDS = 60
MUSIC_FADE_DURATION_SECONDS = 2


# =============================================================================
# MODE BROUILLON
# =============================================================================

DRAFT_MODE_ENABLED = True
MAX_DRAFTS_PER_USER = 5  # Maximum de brouillons par utilisateur


# =============================================================================
# LOGS ET MONITORING
# =============================================================================

# Niveau de logs
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
STRUCTURED_LOGGING = True  # Logs JSON structurés

# Logging des événements
LOG_STORY_EVENTS = True  # Logger dans story_events
LOG_FAILED_UPLOADS = True
LOG_RETRY_ATTEMPTS = True


# =============================================================================
# STATISTIQUES ET ANALYTICS
# =============================================================================

# Analytics avancés
ANALYTICS_ENABLED = True
TRACK_SUCCESS_RATE = True
TRACK_POPULAR_TIMES = True
TRACK_MEDIA_TYPE_PREFERENCES = True


# =============================================================================
# MESSAGES D'ERREUR DÉTAILLÉS
# =============================================================================

ERROR_MESSAGES = {
    "connection_failed": (
        "❌ *Connexion Instagram impossible*\n\n"
        "📡 Problème de réseau détecté.\n"
        "💡 Solutions :\n"
        "• Vérifie ta connexion Internet\n"
        "• Réessaye dans quelques minutes\n"
        "• Utilise /status pour vérifier l'état"
    ),
    "login_failed": (
        "❌ *Échec de connexion Instagram*\n\n"
        "🔐 Identifiants incorrects ou compte bloqué.\n"
        "💡 Solutions :\n"
        "• Vérifie tes identifiants\n"
        "• Active le 2FA avec /code si nécessaire\n"
        "• Attends 1h si Instagram a bloqué temporairement"
    ),
    "rate_limit": (
        "⏸️ *Limite de taux atteinte*\n\n"
        "🚦 Instagram a détecté trop de requêtes.\n"
        "💡 Solution :\n"
        "• Attends 15-30 minutes\n"
        "• Ta story sera automatiquement retentée"
    ),
    "2fa_required": (
        "🔐 *Code 2FA requis*\n\n"
        "📱 Ouvre ton **Google Authenticator** et copie le code à 6 chiffres.\n\n"
        "💡 Utilise : `/code 123456`\n"
        "(Remplace par le code de ton app)"
    ),
    "media_too_large": (
        "⚠️ *Fichier trop volumineux*\n\n"
        "📦 Taille max :\n"
        "• Photos : {max_photo} MB\n"
        "• Vidéos : {max_video} MB\n\n"
        "💡 Compresse ton fichier et réessaye"
    ),
    "video_too_long": (
        "⏱️ *Vidéo trop longue*\n\n"
        "🎬 Durée max : {max_duration}s\n"
        "⏳ Ta vidéo : {current_duration}s\n\n"
        "💡 Découpe ta vidéo et réessaye"
    ),
    "invalid_format": (
        "🚫 *Format non supporté*\n\n"
        "✅ Formats acceptés :\n"
        "• Photos : {image_formats}\n"
        "• Vidéos : {video_formats}\n\n"
        "💡 Convertis ton fichier et réessaye"
    ),
    "max_pending_reached": (
        "⚠️ *Limite de publications atteinte*\n\n"
        "📊 Tu as {count}/{max} stories en attente.\n\n"
        "💡 Annule ou attends qu'une story soit publiée\n"
        "Utilise /list pour voir tes publications"
    ),
    "schedule_too_soon": (
        "⏰ *Heure trop proche*\n\n"
        "⏱️ Délai minimum : {min_delay} minutes\n\n"
        "💡 Choisis une heure plus éloignée"
    ),
    "schedule_too_far": (
        "📅 *Date trop éloignée*\n\n"
        "📆 Maximum : {max_days} jours dans le futur\n\n"
        "💡 Choisis une date plus proche"
    ),
}


# =============================================================================
# WORKER ET SCHEDULER
# =============================================================================

# Intervalles de vérification (en secondes)
WORKER_CHECK_INTERVAL = 60  # Vérifier les stories toutes les 60s
RETRY_CHECK_INTERVAL = 300  # Vérifier les retry toutes les 5min
CLEANUP_CHECK_INTERVAL = CLEANUP_INTERVAL_HOURS * 3600  # En secondes


# =============================================================================
# FONCTIONNALITÉS EXPÉRIMENTALES
# =============================================================================

# Features flags
ENABLE_CAROUSEL = False  # Stories multiples (pas encore implémenté)
ENABLE_LOCATION = False  # Géolocalisation (pas encore implémenté)
ENABLE_HASHTAGS = False  # Hashtags automatiques (pas encore implémenté)
ENABLE_MENTION = False  # Mentions @ (pas encore implémenté)


# =============================================================================
# HELPERS
# =============================================================================

def get_retry_delay(attempt: int) -> int:
    """
    Retourne le délai en minutes pour une tentative donnée.
    
    Args:
        attempt: Numéro de la tentative (0-indexed)
    
    Returns:
        Délai en minutes
    """
    if attempt >= len(RETRY_DELAYS_MINUTES):
        return RETRY_DELAYS_MINUTES[-1]
    return RETRY_DELAYS_MINUTES[attempt]


def should_retry_error(error_message: str) -> bool:
    """
    Détermine si une erreur devrait déclencher un retry.
    
    Args:
        error_message: Message d'erreur
    
    Returns:
        True si l'erreur est "retryable"
    """
    if not RETRY_ENABLED:
        return False
    
    error_lower = error_message.lower()
    return any(keyword in error_lower for keyword in RETRYABLE_ERRORS)


def get_error_message(error_type: str, **kwargs) -> str:
    """
    Récupère un message d'erreur formaté.
    
    Args:
        error_type: Type d'erreur
        **kwargs: Variables pour le formatage
    
    Returns:
        Message d'erreur formaté
    """
    message = ERROR_MESSAGES.get(error_type, "❌ Une erreur s'est produite.")
    
    # Remplacer les placeholders
    if kwargs:
        # Valeurs par défaut
        defaults = {
            "max_photo": MAX_PHOTO_SIZE_MB,
            "max_video": MAX_VIDEO_SIZE_MB,
            "max_duration": MAX_VIDEO_DURATION_SECONDS,
            "max_days": MAX_SCHEDULE_DELAY_DAYS,
            "min_delay": MIN_SCHEDULE_DELAY_MINUTES,
            "image_formats": ", ".join(ALLOWED_IMAGE_FORMATS),
            "video_formats": ", ".join(ALLOWED_VIDEO_FORMATS),
        }
        defaults.update(kwargs)
        
        try:
            message = message.format(**defaults)
        except KeyError:
            pass
    
    return message
