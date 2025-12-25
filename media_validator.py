"""
Module de validation avancée des médias pour Instagram Stories.

Vérifie résolution, aspect ratio, format, durée, etc.
"""

import logging
from typing import Optional, Tuple
from io import BytesIO

from PIL import Image
import config

logger = logging.getLogger(__name__)


class MediaValidationError(Exception):
    """Exception levée lors d'une erreur de validation de média."""
    pass


def validate_image(file_bytes: bytes, file_size: int) -> Tuple[bool, Optional[str], Optional[dict]]:
    """
    Valide une image pour Instagram Stories.
    
    Args:
        file_bytes: Contenu binaire de l'image
        file_size: Taille du fichier en octets
    
    Returns:
        Tuple (valide, message_erreur, métadonnées)
    """
    try:
        # Vérifier la taille
        if file_size > config.MAX_PHOTO_SIZE:
            return False, config.get_error_message("media_too_large"), None
        
        # Ouvrir l'image
        img = Image.open(BytesIO(file_bytes))
        width, height = img.size
        format_name = img.format
        
        # Vérifier le format
        if format_name not in config.ALLOWED_IMAGE_FORMATS:
            return False, config.get_error_message(
                "invalid_format",
                current_format=format_name
            ), None
        
        # Calculer l'aspect ratio
        aspect_ratio = height / width if width > 0 else 0
        expected_ratio = config.STORY_ASPECT_RATIO
        
        # Vérifier l'aspect ratio (avec tolérance)
        if config.VALIDATE_ASPECT_RATIO:
            ratio_diff = abs(aspect_ratio - expected_ratio) / expected_ratio
            if ratio_diff > config.ASPECT_RATIO_TOLERANCE:
                warning = (
                    f"⚠️ *Aspect ratio non optimal*\n\n"
                    f"📐 Ratio actuel : {aspect_ratio:.2f}\n"
                    f"📐 Ratio optimal : {expected_ratio:.2f} (9:16)\n\n"
                    f"💡 L'image sera recadrée par Instagram.\n"
                    f"Format idéal : 1080x1920 pixels"
                )
                logger.warning(f"Aspect ratio non optimal: {aspect_ratio:.2f} vs {expected_ratio:.2f}")
        
        # Métadonnées
        metadata = {
            "width": width,
            "height": height,
            "format": format_name,
            "aspect_ratio": aspect_ratio,
            "size_mb": round(file_size / (1024 * 1024), 2)
        }
        
        logger.info(f"Image validée: {width}x{height}, {format_name}, {metadata['size_mb']} MB")
        return True, None, metadata
        
    except Exception as exc:
        logger.error(f"Erreur validation image: {exc}")
        return False, f"❌ Impossible de lire l'image: {exc}", None


def validate_video_metadata(duration: Optional[float], file_size: int) -> Tuple[bool, Optional[str]]:
    """
    Valide les métadonnées d'une vidéo (durée, taille).
    
    Args:
        duration: Durée en secondes (ou None si inconnue)
        file_size: Taille du fichier en octets
    
    Returns:
        Tuple (valide, message_erreur)
    """
    # Vérifier la taille
    if file_size > config.MAX_VIDEO_SIZE:
        return False, config.get_error_message("media_too_large")
    
    # Vérifier la durée si disponible
    if duration is not None:
        if duration > config.MAX_VIDEO_DURATION_SECONDS:
            return False, config.get_error_message(
                "video_too_long",
                current_duration=int(duration)
            )
        
        if duration < config.MIN_VIDEO_DURATION_SECONDS:
            return False, (
                f"⚠️ *Vidéo trop courte*\n\n"
                f"⏱️ Durée min : {config.MIN_VIDEO_DURATION_SECONDS}s\n"
                f"⏳ Ta vidéo : {duration:.1f}s\n\n"
                f"💡 Envoie une vidéo plus longue"
            )
    
    logger.info(f"Vidéo validée: {duration}s, {file_size / (1024*1024):.2f} MB")
    return True, None


def validate_audio(file_size: int, duration: Optional[float] = None) -> Tuple[bool, Optional[str]]:
    """
    Valide un fichier audio pour overlay musical.
    
    Args:
        file_size: Taille du fichier en octets
        duration: Durée en secondes (optionnel)
    
    Returns:
        Tuple (valide, message_erreur)
    """
    # Vérifier que les overlays musicaux sont activés
    if not config.MUSIC_OVERLAY_ENABLED:
        return False, "❌ Les overlays musicaux ne sont pas activés"
    
    # Vérifier la durée si disponible
    if duration is not None and duration > config.MAX_MUSIC_DURATION_SECONDS:
        return False, (
            f"🎵 *Musique trop longue*\n\n"
            f"⏱️ Durée max : {config.MAX_MUSIC_DURATION_SECONDS}s\n"
            f"⏳ Ta musique : {int(duration)}s\n\n"
            f"💡 Découpe ta musique et réessaye"
        )
    
    logger.info(f"Audio validé: {file_size / (1024*1024):.2f} MB")
    return True, None


def validate_text_overlay(text: str) -> Tuple[bool, Optional[str]]:
    """
    Valide un texte pour overlay.
    
    Args:
        text: Texte à valider
    
    Returns:
        Tuple (valide, message_erreur)
    """
    if not config.TEXT_OVERLAY_ENABLED:
        return False, "❌ Les overlays de texte ne sont pas activés"
    
    if len(text) > config.MAX_TEXT_LENGTH:
        return False, (
            f"✍️ *Texte trop long*\n\n"
            f"📝 Longueur max : {config.MAX_TEXT_LENGTH} caractères\n"
            f"📊 Ton texte : {len(text)} caractères\n\n"
            f"💡 Raccourcis ton texte"
        )
    
    if not text.strip():
        return False, "❌ Le texte ne peut pas être vide"
    
    return True, None


def get_validation_summary(metadata: dict) -> str:
    """
    Génère un résumé de validation pour l'utilisateur.
    
    Args:
        metadata: Métadonnées du média
    
    Returns:
        Message formaté
    """
    if not metadata:
        return ""
    
    lines = ["📊 *Informations du média*\n"]
    
    if "width" in metadata and "height" in metadata:
        lines.append(f"📐 Résolution : {metadata['width']}x{metadata['height']}")
        
        # Indiquer si c'est optimal
        is_optimal = (
            metadata['width'] == config.STORY_WIDTH and 
            metadata['height'] == config.STORY_HEIGHT
        )
        if is_optimal:
            lines.append("✅ Format optimal pour Stories")
        else:
            lines.append(f"💡 Format optimal : {config.STORY_WIDTH}x{config.STORY_HEIGHT}")
    
    if "format" in metadata:
        lines.append(f"🎨 Format : {metadata['format']}")
    
    if "size_mb" in metadata:
        lines.append(f"💾 Taille : {metadata['size_mb']} MB")
    
    if "aspect_ratio" in metadata:
        lines.append(f"📏 Ratio : {metadata['aspect_ratio']:.2f}")
    
    return "\n".join(lines)
