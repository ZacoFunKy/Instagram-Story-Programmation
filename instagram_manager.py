"""
Gestionnaire professionnel de connexion Instagram avec intégration Telegram.

Ce module gère:
- Connexion Instagram avec session persistante
- 2FA via Telegram (pas de scripts séparés)
- Gestion automatique des erreurs et retry
- Notifications utilisateur via Telegram
"""

import logging
import os
import threading
from pathlib import Path
from typing import Optional

import pyotp
from instagrapi import Client
from telegram import Bot
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Lock pour thread-safety de la connexion Instagram
_ig_lock = threading.Lock()

# Code 2FA en attente (stockage temporaire par chat_id)
_pending_2fa_codes: dict[int, str] = {}


class InstagramManager:
    """Gestionnaire centralisé pour la connexion et les opérations Instagram."""
    
    def __init__(
        self,
        username: str,
        password: str,
        session_file: str,
        totp_secret: Optional[str] = None,
        proxy_url: Optional[str] = None
    ):
        """
        Initialise le gestionnaire Instagram.
        
        Args:
            username: Nom d'utilisateur Instagram
            password: Mot de passe Instagram
            session_file: Chemin du fichier de session
            totp_secret: Secret TOTP pour Google Authenticator (optionnel)
            proxy_url: URL du proxy (optionnel)
        """
        self.username = username
        self.password = password
        self.session_file = session_file
        self.totp_secret = totp_secret
        self.proxy_url = proxy_url
        
        self.client = Client()
        self.client.delay_range = [1, 3]  # Délais humains
        
        if proxy_url:
            try:
                self.client.set_proxy(proxy_url)
                logger.info("Proxy configuré pour Instagram")
            except Exception as e:
                logger.warning("Impossible de configurer le proxy: %s", e)
        
        # Charger la session si elle existe
        self._load_session()
    
    def _load_session(self) -> bool:
        """
        Charge une session Instagram existante depuis le disque.
        
        Returns:
            True si la session a été chargée avec succès
        """
        if os.path.exists(self.session_file):
            try:
                self.client.load_settings(self.session_file)
                if self.proxy_url:
                    self.client.set_proxy(self.proxy_url)
                logger.info("✅ Session Instagram chargée depuis %s", self.session_file)
                return True
            except Exception as exc:
                logger.warning("Impossible de charger la session: %s", exc)
        return False
    
    def _save_session(self) -> bool:
        """
        Sauvegarde la session Instagram sur le disque.
        
        Returns:
            True si la session a été sauvegardée avec succès
        """
        try:
            # Créer le dossier parent si nécessaire
            Path(self.session_file).parent.mkdir(parents=True, exist_ok=True)
            self.client.dump_settings(self.session_file)
            logger.info("💾 Session Instagram sauvegardée dans %s", self.session_file)
            return True
        except Exception as exc:
            logger.error("Impossible de sauvegarder la session: %s", exc)
            return False
    
    def _generate_totp_code(self) -> Optional[str]:
        """
        Génère un code TOTP depuis le secret si disponible.
        
        Returns:
            Code TOTP à 6 chiffres ou None
        """
        if not self.totp_secret:
            return None
        
        try:
            sanitized_secret = self.totp_secret.replace(" ", "").strip().upper()
            code = pyotp.TOTP(sanitized_secret).now()
            logger.info("🔢 Code TOTP généré automatiquement")
            return code
        except Exception as exc:
            logger.warning("Erreur génération TOTP: %s", exc)
            return None
    
    async def request_2fa_code(
        self,
        chat_id: int,
        context: ContextTypes.DEFAULT_TYPE
    ) -> Optional[str]:
        """
        Demande un code 2FA à l'utilisateur via Telegram.
        
        Args:
            chat_id: ID du chat Telegram
            context: Contexte Telegram
        
        Returns:
            Code 2FA fourni par l'utilisateur ou None
        """
        try:
            # Tenter génération automatique TOTP
            auto_code = self._generate_totp_code()
            if auto_code:
                return auto_code
            
            # Sinon, demander à l'utilisateur via Telegram
            message = (
                "🔐 *Code 2FA requis*\n\n"
                "Instagram demande un code d'authentification à deux facteurs.\n\n"
                "Envoie le code avec la commande:\n"
                "`/code 123456`\n\n"
                "Tu as 2 minutes pour répondre."
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
            # Attendre le code (géré par le handler /code dans bot.py)
            import asyncio
            for _ in range(24):  # 2 minutes (24 * 5 secondes)
                await asyncio.sleep(5)
                if chat_id in _pending_2fa_codes:
                    code = _pending_2fa_codes.pop(chat_id)
                    logger.info("Code 2FA reçu depuis Telegram pour chat %s", chat_id)
                    return code
            
            logger.warning("Timeout: aucun code 2FA reçu pour chat %s", chat_id)
            await context.bot.send_message(
                chat_id=chat_id,
                text="⏱️ Timeout: aucun code 2FA reçu. Réessaye avec /login"
            )
            return None
            
        except Exception as exc:
            logger.error("Erreur lors de la demande de code 2FA: %s", exc)
            return None
    
    async def login(
        self,
        chat_id: Optional[int] = None,
        context: Optional[ContextTypes.DEFAULT_TYPE] = None,
        force: bool = False
    ) -> bool:
        """
        Connexion Instagram avec gestion intelligente de session et 2FA.
        
        Args:
            chat_id: ID du chat Telegram pour notifications (optionnel)
            context: Contexte Telegram pour interactions (optionnel)
            force: Forcer la reconnexion même si déjà connecté
        
        Returns:
            True si connexion réussie, False sinon
        """
        with _ig_lock:
            try:
                # Si déjà connecté et pas de force
                if not force and self.client.user_id:
                    logger.info("Déjà connecté à Instagram (user_id: %s)", self.client.user_id)
                    return True
                
                # Tentative avec session existante
                if not force and self._load_session():
                    try:
                        # Tester la session avec une requête légère
                        self.client.get_timeline_feed()
                        logger.info("✅ Session valide, connecté en tant que %s", self.username)
                        return True
                    except Exception:
                        logger.warning("Session expirée, nouvelle connexion requise")
                
                # Nouvelle connexion
                logger.info("Connexion Instagram pour %s...", self.username)
                
                # Première tentative sans 2FA
                try:
                    self.client.login(self.username, self.password)
                    logger.info("✅ Connexion réussie sans 2FA")
                    self._save_session()
                    
                    if chat_id and context:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="✅ Connexion Instagram réussie!"
                        )
                    return True
                    
                except Exception as login_exc:
                    error_msg = str(login_exc).lower()
                    
                    # 2FA requis
                    if "two" in error_msg or "verification" in error_msg:
                        logger.info("2FA requis pour la connexion")
                        
                        if chat_id and context:
                            verification_code = await self.request_2fa_code(chat_id, context)
                            
                            if not verification_code:
                                if context:
                                    await context.bot.send_message(
                                        chat_id=chat_id,
                                        text="❌ Code 2FA non reçu, connexion annulée"
                                    )
                                return False
                            
                            # Reconnexion avec le code 2FA
                            try:
                                self.client.login(
                                    self.username,
                                    self.password,
                                    verification_code=verification_code
                                )
                                logger.info("✅ Connexion réussie avec 2FA")
                                self._save_session()
                                
                                await context.bot.send_message(
                                    chat_id=chat_id,
                                    text="✅ Connexion Instagram réussie avec 2FA!"
                                )
                                return True
                                
                            except Exception as twofa_exc:
                                logger.error("Échec connexion avec 2FA: %s", twofa_exc)
                                if context:
                                    await context.bot.send_message(
                                        chat_id=chat_id,
                                        text=f"❌ Erreur avec code 2FA: {twofa_exc}"
                                    )
                                return False
                        else:
                            logger.error("2FA requis mais pas de contexte Telegram fourni")
                            return False
                    
                    # Autres erreurs
                    else:
                        logger.error("Erreur de connexion: %s", login_exc)
                        if chat_id and context:
                            error_text = (
                                f"❌ Connexion Instagram échouée:\n{login_exc}\n\n"
                                "Vérifie tes credentials dans les variables d'environnement."
                            )
                            await context.bot.send_message(chat_id=chat_id, text=error_text)
                        return False
                        
            except Exception as exc:
                logger.exception("Erreur critique lors de la connexion Instagram: %s", exc)
                if chat_id and context:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Erreur critique: {exc}"
                    )
                return False
    
    def is_logged_in(self) -> bool:
        """Vérifie si le client est connecté."""
        return bool(self.client.user_id)
    
    def get_client(self) -> Client:
        """Retourne le client Instagram."""
        return self.client


def set_pending_2fa_code(chat_id: int, code: str) -> None:
    """
    Enregistre un code 2FA fourni par l'utilisateur.
    
    Args:
        chat_id: ID du chat Telegram
        code: Code 2FA à 6 chiffres
    """
    _pending_2fa_codes[chat_id] = code
    logger.info("Code 2FA enregistré pour chat %s", chat_id)
