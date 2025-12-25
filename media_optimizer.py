"""
Module de gestion et optimisation des médias pour Instagram Stories.

Ce module gère:
- Compression intelligente des images pour Instagram (1080x1920 optimal)
- Compression et conversion des vidéos
- Préservation de la qualité visuelle
- Formats optimaux pour Instagram
"""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image
import PIL.ExifTags

logger = logging.getLogger(__name__)

# Dimensions optimales pour Instagram Stories
STORY_WIDTH = 1080
STORY_HEIGHT = 1920
STORY_ASPECT_RATIO = STORY_HEIGHT / STORY_WIDTH  # 16:9 vertical

# Qualité de compression (95 = haute qualité, peu de compression)
JPEG_QUALITY = 95

# Taille maximale fichier pour Instagram (100MB)
MAX_FILE_SIZE_MB = 100


def get_optimal_dimensions(width: int, height: int) -> Tuple[int, int]:
    """
    Calcule les dimensions optimales pour une story Instagram.
    
    Préserve le ratio d'aspect original en remplissant le cadre 9:16.
    
    Args:
        width: Largeur originale
        height: Hauteur originale
    
    Returns:
        Tuple (nouvelle_largeur, nouvelle_hauteur)
    """
    aspect_ratio = height / width
    
    # Si l'image est déjà aux bonnes dimensions
    if width == STORY_WIDTH and height == STORY_HEIGHT:
        return width, height
    
    # Image plus large que le ratio story -> ajuster sur la hauteur
    if aspect_ratio > STORY_ASPECT_RATIO:
        new_height = STORY_HEIGHT
        new_width = int(STORY_HEIGHT / aspect_ratio)
    # Image plus carrée -> ajuster sur la largeur
    else:
        new_width = STORY_WIDTH
        new_height = int(STORY_WIDTH * aspect_ratio)
    
    return new_width, new_height


def add_story_bars(img: Image.Image, background_color=(0, 0, 0)) -> Image.Image:
    """
    Ajoute des bandes (letterbox/pillarbox) pour forcer le ratio 9:16.
    
    Args:
        img: Image PIL à traiter
        background_color: Couleur des bandes (noir par défaut)
    
    Returns:
        Image PIL avec ratio 9:16 parfait
    """
    width, height = img.size
    aspect_ratio = height / width
    
    # Si déjà au bon ratio, ne rien faire
    if abs(aspect_ratio - STORY_ASPECT_RATIO) < 0.01:
        return img
    
    # Créer un canvas 9:16
    if aspect_ratio > STORY_ASPECT_RATIO:
        # Image trop haute -> bandes sur les côtés (pillarbox)
        new_height = height
        new_width = int(height / STORY_ASPECT_RATIO)
    else:
        # Image trop large -> bandes en haut/bas (letterbox)
        new_width = width
        new_height = int(width * STORY_ASPECT_RATIO)
    
    # Créer le canvas avec fond noir
    canvas = Image.new('RGB', (new_width, new_height), background_color)
    
    # Centrer l'image originale
    offset_x = (new_width - width) // 2
    offset_y = (new_height - height) // 2
    canvas.paste(img, (offset_x, offset_y))
    
    logger.info(
        "Bandes ajoutées: %dx%d -> %dx%d (ratio %.2f -> %.2f)",
        width, height, new_width, new_height, aspect_ratio, STORY_ASPECT_RATIO
    )
    
    return canvas


def compress_image(
    input_path: str,
    output_path: Optional[str] = None,
    max_size_mb: float = MAX_FILE_SIZE_MB,
    add_bars: bool = True
) -> str:
    """
    Compresse une image pour Instagram Stories avec haute qualité.
    
    Args:
        input_path: Chemin de l'image source
        output_path: Chemin de sortie (optionnel, écrase l'original si None)
        max_size_mb: Taille max du fichier en MB
        add_bars: Ajouter des bandes noires pour forcer le ratio 9:16
    
    Returns:
        Chemin du fichier compressé
    
    Raises:
        ValueError: Si l'image ne peut pas être ouverte
    """
    if output_path is None:
        output_path = input_path
    
    try:
        # Ouvrir l'image
        with Image.open(input_path) as img:
            # Préserver les métadonnées EXIF (orientation, etc.)
            exif = img.info.get('exif', b'')
            
            # Convertir en RGB si nécessaire (PNG avec transparence, etc.)
            if img.mode in ('RGBA', 'LA', 'P'):
                # Créer un fond blanc pour les images avec transparence
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Ajouter des bandes si nécessaire pour forcer 9:16
            if add_bars:
                img = add_story_bars(img)
            
            # Redimensionner au format Instagram optimal (1080x1920)
            if (img.width, img.height) != (STORY_WIDTH, STORY_HEIGHT):
                logger.info(
                    "Redimensionnement final: %dx%d -> %dx%d",
                    img.width, img.height, STORY_WIDTH, STORY_HEIGHT
                )
                img = img.resize((STORY_WIDTH, STORY_HEIGHT), Image.Resampling.LANCZOS)
            
            # Sauvegarder avec qualité maximale
            save_kwargs = {
                'format': 'JPEG',
                'quality': JPEG_QUALITY,
                'optimize': True,
                'progressive': True,
            }
            
            if exif:
                save_kwargs['exif'] = exif
            
            img.save(output_path, **save_kwargs)
            
            # Vérifier la taille du fichier
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(
                "Image compressée: %s (%.2f MB, qualité %d%%)",
                output_path, file_size_mb, JPEG_QUALITY
            )
            
            # Si toujours trop gros, réduire la qualité progressivement
            quality = JPEG_QUALITY
            while file_size_mb > max_size_mb and quality > 70:
                quality -= 5
                logger.warning(
                    "Fichier trop gros (%.2f MB), recompression à %d%%",
                    file_size_mb, quality
                )
                img.save(output_path, format='JPEG', quality=quality, optimize=True)
                file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            
            return output_path
            
    except Exception as exc:
        logger.error("Erreur compression image %s: %s", input_path, exc)
        raise ValueError(f"Impossible de compresser l'image: {exc}")


def compress_video(
    input_path: str,
    output_path: Optional[str] = None,
    max_size_mb: float = MAX_FILE_SIZE_MB
) -> str:
    """
    Compresse une vidéo pour Instagram Stories.
    
    Instagram Stories accepte:
    - Format: MP4 (H.264)
    - Durée: max 60 secondes
    - Résolution: 1080x1920 optimal
    - Bitrate: 3-5 Mbps recommandé
    
    Args:
        input_path: Chemin de la vidéo source
        output_path: Chemin de sortie (optionnel)
        max_size_mb: Taille max du fichier en MB
    
    Returns:
        Chemin du fichier compressé
    
    Raises:
        ValueError: Si la vidéo ne peut pas être traitée
    """
    try:
        from moviepy.editor import VideoFileClip
        
        if output_path is None:
            base, ext = os.path.splitext(input_path)
            output_path = f"{base}_compressed.mp4"
        
        logger.info("Compression vidéo: %s", input_path)
        
        with VideoFileClip(input_path) as video:
            # Durée maximale Instagram
            if video.duration > 60:
                logger.warning("Vidéo trop longue (%.1fs), découpe à 60s", video.duration)
                video = video.subclip(0, 60)
            
            # Calculer les dimensions optimales
            new_width, new_height = get_optimal_dimensions(video.w, video.h)
            
            # Déterminer le bitrate optimal
            # Formule: bitrate = (taille_max_MB * 8) / durée_secondes (en Mbps)
            target_size_bits = max_size_mb * 8 * 1024 * 1024
            duration_seconds = video.duration
            max_bitrate_bps = int(target_size_bits / duration_seconds)
            
            # Limiter entre 2 Mbps (min pour qualité) et 5 Mbps (max Instagram)
            bitrate_bps = max(2_000_000, min(5_000_000, max_bitrate_bps))
            bitrate_mbps = bitrate_bps / 1_000_000
            
            logger.info(
                "Paramètres vidéo: %dx%d, bitrate %.1f Mbps, durée %.1fs",
                new_width, new_height, bitrate_mbps, video.duration
            )
            
            # Redimensionner et encoder
            video_resized = video.resize(height=new_height)
            
            video_resized.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                bitrate=f"{int(bitrate_bps)}",
                fps=30,  # 30 FPS standard Instagram
                preset='medium',  # Balance vitesse/qualité
                threads=4,
                logger=None  # Désactiver les logs verbeux de moviepy
            )
            
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(
                "Vidéo compressée: %s (%.2f MB)",
                output_path, file_size_mb
            )
            
            return output_path
            
    except ImportError:
        logger.error("moviepy non installé, impossible de compresser la vidéo")
        raise ValueError("Module moviepy requis pour la compression vidéo")
    except Exception as exc:
        logger.error("Erreur compression vidéo %s: %s", input_path, exc)
        raise ValueError(f"Impossible de compresser la vidéo: {exc}")


def optimize_media_for_instagram(
    input_path: str,
    media_type: str = "photo"
) -> str:
    """
    Optimise un média (photo ou vidéo) pour Instagram Stories.
    
    Args:
        input_path: Chemin du fichier source
        media_type: Type de média ('photo' ou 'video')
    
    Returns:
        Chemin du fichier optimisé
    
    Raises:
        ValueError: Si le type de média n'est pas supporté
    """
    if media_type == "photo":
        return compress_image(input_path)
    elif media_type == "video":
        return compress_video(input_path)
    else:
        raise ValueError(f"Type de média non supporté: {media_type}")
