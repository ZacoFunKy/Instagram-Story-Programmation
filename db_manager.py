"""
Gestionnaire de base de données pour le bot Instagram Story Scheduler.
Gère toutes les interactions avec Supabase PostgreSQL.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional
from uuid import UUID

from supabase import Client, create_client


class DBManager:
    """Gestionnaire centralisé pour toutes les opérations de base de données."""

    def __init__(self, supabase_url: str, supabase_key: str):
        """
        Initialise la connexion à Supabase.
        
        Args:
            supabase_url: URL du projet Supabase
            supabase_key: Clé API Supabase (anon ou service_role)
        """
        self.client: Client = create_client(supabase_url, supabase_key)
        self.logger = logging.getLogger(__name__)
        self.logger.info("Connexion à Supabase établie")

    def create_story(
        self,
        chat_id: int,
        file_id: str,
        scheduled_time: datetime,
        to_close_friends: bool = False,
        media_type: str = "photo"
    ) -> Optional[dict]:
        """
        Crée une nouvelle story programmée dans la base de données.
        
        Args:
            chat_id: ID du chat Telegram
            file_id: ID du fichier photo/vidéo sur Telegram
            scheduled_time: Date/heure de publication programmée
            to_close_friends: True si la story est uniquement pour les amis proches
            media_type: Type de média ('photo' ou 'video')
        
        Returns:
            Dictionnaire contenant les données de la story créée, ou None en cas d'erreur
        """
        try:
            # Convertir la date de publication en UTC pour une persistance cohérente
            scheduled_utc = scheduled_time.astimezone(ZoneInfo("UTC"))
            data = {
                "chat_id": chat_id,
                "file_id": file_id,
                "media_type": media_type,
                "scheduled_time": scheduled_utc.isoformat(),
                "status": "PENDING",
                "to_close_friends": to_close_friends
            }
            
            result = self.client.table("stories").insert(data).execute()
            
            if result.data:
                story = result.data[0]
                self.logger.info(
                    "Story créée - ID: %s, Chat: %s, Type: %s, Scheduled: %s",
                    story.get("id"),
                    chat_id,
                    media_type,
                    scheduled_time
                )
                return story
            return None
            
        except Exception as exc:
            self.logger.error("Erreur lors de la création de la story: %s", exc)
            return None

    def get_pending_stories(self) -> list[dict]:
        """
        Récupère toutes les stories en attente dont l'heure de publication est passée.
        
        Returns:
            Liste des stories à publier
        """
        try:
            # Utiliser l'heure actuelle en UTC pour la comparaison côté BDD
            now = datetime.now(ZoneInfo("UTC")).isoformat()
            
            result = self.client.table("stories")\
                .select("*")\
                .eq("status", "PENDING")\
                .lte("scheduled_time", now)\
                .order("scheduled_time")\
                .execute()
            
            return result.data if result.data else []
            
        except Exception as exc:
            self.logger.error("Erreur lors de la récupération des stories: %s", exc)
            return []

    def update_story_status(
        self,
        story_id: str,
        status: str,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Met à jour le statut d'une story.
        
        Args:
            story_id: UUID de la story
            status: Nouveau statut (PENDING, PUBLISHED, ERROR, CANCELLED)
            error_message: Message d'erreur optionnel si status=ERROR
        
        Returns:
            True si la mise à jour a réussi, False sinon
        """
        try:
            data = {"status": status}
            if error_message:
                data["error_message"] = error_message
            
            result = self.client.table("stories")\
                .update(data)\
                .eq("id", story_id)\
                .execute()
            
            self.logger.info("Story %s mise à jour: %s", story_id, status)
            return bool(result.data)
            
        except Exception as exc:
            self.logger.error(
                "Erreur lors de la mise à jour de la story %s: %s",
                story_id,
                exc
            )
            return False

    def get_user_pending_stories(self, chat_id: int) -> list[dict]:
        """
        Récupère toutes les stories en attente pour un utilisateur spécifique.
        
        Args:
            chat_id: ID du chat Telegram
        
        Returns:
            Liste des stories en attente pour cet utilisateur
        """
        try:
            result = self.client.table("stories")\
                .select("*")\
                .eq("chat_id", chat_id)\
                .eq("status", "PENDING")\
                .order("scheduled_time")\
                .execute()
            
            return result.data if result.data else []
            
        except Exception as exc:
            self.logger.error(
                "Erreur lors de la récupération des stories utilisateur %s: %s",
                chat_id,
                exc
            )
            return []

    def cancel_story(self, story_id: str, chat_id: int) -> bool:
        """
        Annule une story programmée.
        
        Args:
            story_id: UUID de la story
            chat_id: ID du chat (pour vérification de propriété)
        
        Returns:
            True si l'annulation a réussi, False sinon
        """
        try:
            # Vérifier que la story appartient bien à l'utilisateur
            result = self.client.table("stories")\
                .select("id")\
                .eq("id", story_id)\
                .eq("chat_id", chat_id)\
                .eq("status", "PENDING")\
                .execute()
            
            if not result.data:
                self.logger.warning(
                    "Tentative d'annulation d'une story inexistante ou non autorisée: %s",
                    story_id
                )
                return False
            
            # Mettre à jour le statut
            return self.update_story_status(story_id, "CANCELLED")
            
        except Exception as exc:
            self.logger.error("Erreur lors de l'annulation de la story %s: %s", story_id, exc)
            return False

    def get_user_stats(self, chat_id: int) -> dict:
        """
        Récupère les statistiques d'un utilisateur.
        
        Args:
            chat_id: ID du chat Telegram
        
        Returns:
            Dictionnaire avec les statistiques (pending, published, error counts)
        """
        try:
            result = self.client.table("stories_stats")\
                .select("*")\
                .eq("chat_id", chat_id)\
                .execute()
            
            if result.data and len(result.data) > 0:
                return result.data[0]
            
            return {
                "pending_count": 0,
                "published_count": 0,
                "error_count": 0,
                "cancelled_count": 0
            }
            
        except Exception as exc:
            self.logger.warning("Erreur lors de la récupération des stats: %s", exc)
            return {
                "pending_count": 0,
                "published_count": 0,
                "error_count": 0,
                "cancelled_count": 0
            }

    def cleanup_old_stories(self, days: int = 30) -> int:
        """
        Nettoie les anciennes stories terminées (PUBLISHED, ERROR, CANCELLED).
        
        Args:
            days: Nombre de jours à conserver
        
        Returns:
            Nombre de stories supprimées
        """
        try:
            from datetime import timedelta
            cutoff_date = (datetime.now(ZoneInfo("UTC")) - timedelta(days=days)).isoformat()
            
            result = self.client.table("stories")\
                .delete()\
                .in_("status", ["PUBLISHED", "ERROR", "CANCELLED"])\
                .lt("updated_at", cutoff_date)\
                .execute()
            
            count = len(result.data) if result.data else 0
            self.logger.info("Nettoyage: %d stories supprimées", count)
            return count
            
        except Exception as exc:
            self.logger.error("Erreur lors du nettoyage: %s", exc)
            return 0
