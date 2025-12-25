"""
Module pour ajouter des overlays (texte, musique) sur les stories Instagram.

Utilise Pillow pour le texte et moviepy pour la musique.
"""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
import config

logger = logging.getLogger(__name__)


def add_text_overlay(
    image_path: str,
    text: str,
    output_path: Optional[str] = None,
    position: str = "center",
    font_size: int = None,
    color: str = None,
    with_background: bool = True
) -> str:
    """
    Ajoute un overlay de texte sur une image.
    
    Args:
        image_path: Chemin de l'image source
        text: Texte à ajouter
        output_path: Chemin de sortie (optionnel)
        position: Position du texte ('top', 'center', 'bottom')
        font_size: Taille de la police (optionnel, utilise config par défaut)
        color: Couleur du texte (optionnel, utilise config par défaut)
        with_background: Ajouter un fond semi-transparent derrière le texte
    
    Returns:
        Chemin du fichier avec overlay
    """
    if output_path is None:
        base, ext = os.path.splitext(image_path)
        output_path = f"{base}_with_text{ext}"
    
    try:
        # Ouvrir l'image
        img = Image.open(image_path)
        draw = ImageDraw.Draw(img)
        
        # Paramètres par défaut
        if font_size is None:
            font_size = config.DEFAULT_FONT_SIZE
        if color is None:
            color = config.DEFAULT_TEXT_COLOR
        
        # Charger une police (utiliser la police système par défaut)
        try:
            # Essayer d'utiliser une police TrueType
            font = ImageFont.truetype("arial.ttf", font_size)
        except:
            # Fallback sur la police par défaut
            font = ImageFont.load_default()
            logger.warning("Police TrueType non trouvée, utilisation de la police par défaut")
        
        # Mesurer le texte
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        # Calculer la position
        img_width, img_height = img.size
        
        if position == "top":
            x = (img_width - text_width) / 2
            y = img_height * 0.1  # 10% du haut
        elif position == "bottom":
            x = (img_width - text_width) / 2
            y = img_height * 0.85 - text_height  # 85% du haut
        else:  # center
            x = (img_width - text_width) / 2
            y = (img_height - text_height) / 2
        
        # Ajouter un fond semi-transparent si demandé
        if with_background:
            padding = 20
            background_bbox = [
                x - padding,
                y - padding,
                x + text_width + padding,
                y + text_height + padding
            ]
            
            # Créer une couche semi-transparente
            overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rectangle(background_bbox, fill=(0, 0, 0, 128))  # Noir à 50%
            
            # Composer avec l'image originale
            img = img.convert('RGBA')
            img = Image.alpha_composite(img, overlay)
            draw = ImageDraw.Draw(img)
        
        # Dessiner le texte
        draw.text((x, y), text, font=font, fill=color)
        
        # Sauvegarder
        if img.mode == 'RGBA':
            img = img.convert('RGB')
        img.save(output_path, quality=config.JPEG_QUALITY)
        
        logger.info(f"Overlay texte ajouté: {output_path}")
        return output_path
        
    except Exception as exc:
        logger.error(f"Erreur lors de l'ajout d'overlay texte: {exc}")
        raise


def add_music_overlay(
    video_path: str,
    music_path: str,
    output_path: Optional[str] = None,
    volume: float = 0.5,
    fade_duration: float = None
) -> str:
    """
    Ajoute une musique de fond sur une vidéo.
    
    Args:
        video_path: Chemin de la vidéo source
        music_path: Chemin du fichier audio
        output_path: Chemin de sortie (optionnel)
        volume: Volume de la musique (0.0 à 1.0)
        fade_duration: Durée du fade in/out en secondes (optionnel)
    
    Returns:
        Chemin du fichier avec musique
    """
    if output_path is None:
        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_with_music{ext}"
    
    try:
        from moviepy.editor import VideoFileClip, AudioFileClip, CompositeAudioClip
        
        if fade_duration is None:
            fade_duration = config.MUSIC_FADE_DURATION_SECONDS
        
        logger.info(f"Ajout de musique: {music_path} sur {video_path}")
        
        # Charger la vidéo et la musique
        video = VideoFileClip(video_path)
        music = AudioFileClip(music_path)
        
        # Ajuster la durée de la musique à celle de la vidéo
        if music.duration > video.duration:
            music = music.subclip(0, video.duration)
        
        # Appliquer le volume
        music = music.volumex(volume)
        
        # Appliquer des fades
        if fade_duration > 0:
            music = music.audio_fadein(fade_duration).audio_fadeout(fade_duration)
        
        # Composer l'audio
        if video.audio:
            # Si la vidéo a déjà de l'audio, le mixer avec la musique
            final_audio = CompositeAudioClip([video.audio, music])
        else:
            # Sinon, utiliser juste la musique
            final_audio = music
        
        # Créer la vidéo finale
        final_video = video.set_audio(final_audio)
        
        # Exporter
        final_video.write_videofile(
            output_path,
            codec='libx264',
            audio_codec='aac',
            bitrate=f"{config.VIDEO_BITRATE_MBPS * 1_000_000}",
            fps=config.VIDEO_FPS,
            preset='medium',
            threads=4,
            logger=None  # Désactiver les logs verbeux
        )
        
        # Nettoyer
        video.close()
        music.close()
        final_video.close()
        
        logger.info(f"Musique ajoutée: {output_path}")
        return output_path
        
    except ImportError:
        logger.error("moviepy non installé, impossible d'ajouter la musique")
        raise Exception("Module moviepy requis pour les overlays musicaux")
    except Exception as exc:
        logger.error(f"Erreur lors de l'ajout de musique: {exc}")
        raise


def add_text_on_video(
    video_path: str,
    text: str,
    output_path: Optional[str] = None,
    position: str = "center",
    font_size: int = None,
    color: str = "white",
    duration: Optional[float] = None
) -> str:
    """
    Ajoute un overlay de texte sur une vidéo.
    
    Args:
        video_path: Chemin de la vidéo source
        text: Texte à ajouter
        output_path: Chemin de sortie (optionnel)
        position: Position du texte ('top', 'center', 'bottom')
        font_size: Taille de la police
        color: Couleur du texte
        duration: Durée d'affichage du texte (None = toute la vidéo)
    
    Returns:
        Chemin du fichier avec texte
    """
    if output_path is None:
        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_with_text{ext}"
    
    try:
        from moviepy.editor import VideoFileClip, TextClip, CompositeVideoClip
        
        if font_size is None:
            font_size = config.DEFAULT_FONT_SIZE
        
        logger.info(f"Ajout de texte sur vidéo: {text}")
        
        # Charger la vidéo
        video = VideoFileClip(video_path)
        
        # Créer le clip de texte
        txt_clip = TextClip(
            text,
            fontsize=font_size,
            color=color,
            font='Arial',
            method='caption',
            size=(video.w * 0.8, None)  # 80% de la largeur
        )
        
        # Positionner le texte
        if position == "top":
            txt_clip = txt_clip.set_position(('center', 'top'))
        elif position == "bottom":
            txt_clip = txt_clip.set_position(('center', 'bottom'))
        else:  # center
            txt_clip = txt_clip.set_position('center')
        
        # Définir la durée
        if duration is None:
            duration = video.duration
        txt_clip = txt_clip.set_duration(duration)
        
        # Composer
        final_video = CompositeVideoClip([video, txt_clip])
        
        # Exporter
        final_video.write_videofile(
            output_path,
            codec='libx264',
            audio_codec='aac' if video.audio else None,
            bitrate=f"{config.VIDEO_BITRATE_MBPS * 1_000_000}",
            fps=config.VIDEO_FPS,
            preset='medium',
            threads=4,
            logger=None
        )
        
        # Nettoyer
        video.close()
        txt_clip.close()
        final_video.close()
        
        logger.info(f"Texte ajouté sur vidéo: {output_path}")
        return output_path
        
    except ImportError:
        logger.error("moviepy non installé, impossible d'ajouter du texte sur vidéo")
        raise Exception("Module moviepy requis pour le texte sur vidéo")
    except Exception as exc:
        logger.error(f"Erreur lors de l'ajout de texte sur vidéo: {exc}")
        raise
