#!/usr/bin/env python3
"""
Script helper pour se connecter à Instagram en local et générer ig_session.json.
Utilise ton IP locale (non blacklistée) pour créer une session valide.
Ensuite, copie ce fichier sur Render pour éviter les re-logins.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from instagrapi import Client

# Charger les variables d'environnement
load_dotenv()

IG_USER = os.getenv("IG_USER")
IG_PASS = os.getenv("IG_PASS")
IG_TOTP_SECRET = os.getenv("IG_TOTP_SECRET")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
SESSION_FILE = os.path.join(DOWNLOAD_DIR, "ig_session.json")

# Vérifier les credentials
if not IG_USER or not IG_PASS:
    print("❌ Erreur: IG_USER et IG_PASS doivent être définis dans .env")
    sys.exit(1)

# Créer le dossier si nécessaire
Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

print("🔐 Instagram Login Helper")
print("=" * 50)
print(f"Utilisateur: {IG_USER}")
print(f"Session sera sauvée dans: {SESSION_FILE}")
print()

# Initialiser le client Instagram
cl = Client()
cl.delay_range = [1, 3]

# Tenter de charger une session existante
if os.path.exists(SESSION_FILE):
    print("📂 Session existante trouvée, tentative de chargement...")
    try:
        cl.load_settings(SESSION_FILE)
        cl.login(IG_USER, IG_PASS)
        print("✅ Session existante réutilisée avec succès!")
        print(f"👤 Connecté en tant que: {cl.username} (ID: {cl.user_id})")
        print()
        print("📋 Prochaines étapes:")
        print(f"1. Le fichier de session est déjà dans: {SESSION_FILE}")
        print("2. Sur Render, configure DOWNLOAD_DIR=/data")
        print("3. Copie ce fichier vers /data/ig_session.json sur Render")
        print("   (via persistent disk ou script de déploiement)")
        sys.exit(0)
    except Exception as exc:
        print(f"⚠️  Session expirée ou invalide: {exc}")
        print("🔄 Tentative de nouvelle connexion...")

# Connexion avec 2FA
print("🔑 Connexion à Instagram...")

verification_code = None

# Si TOTP secret fourni, générer automatiquement le code
if IG_TOTP_SECRET:
    try:
        import pyotp
        sanitized_secret = IG_TOTP_SECRET.replace(" ", "").strip().upper()
        verification_code = pyotp.TOTP(sanitized_secret).now()
        print(f"🔢 Code TOTP généré: {verification_code}")
    except ImportError:
        print("⚠️  pyotp non installé, 2FA manuel requis")
        print("   Installe avec: pip install pyotp")
    except Exception as e:
        print(f"⚠️  Erreur génération TOTP: {e}")

# Si pas de code TOTP, demander manuellement
if not verification_code:
    print()
    print("📱 Instagram demande un code 2FA.")
    print("   Ouvre ton Google Authenticator et entre le code à 6 chiffres:")
    verification_code = input("Code 2FA: ").strip()

# Tentative de connexion
try:
    cl.login(IG_USER, IG_PASS, verification_code=verification_code)
    print()
    print("✅ Connexion Instagram réussie!")
    print(f"👤 Connecté en tant que: {cl.username} (ID: {cl.user_id})")
    
    # Sauvegarder la session
    cl.dump_settings(SESSION_FILE)
    print()
    print(f"💾 Session sauvegardée dans: {SESSION_FILE}")
    print()
    print("=" * 50)
    print("📋 Prochaines étapes:")
    print("=" * 50)
    print()
    print("1. Sur Render, configure ces variables d'environnement:")
    print("   DOWNLOAD_DIR=/data")
    print()
    print("2. Ajoute un Persistent Disk:")
    print("   - Name: instagram-session")
    print("   - Mount Path: /data")
    print("   - Size: 1 GB")
    print()
    print(f"3. Copie le fichier {SESSION_FILE}")
    print("   vers /data/ig_session.json sur Render")
    print()
    print("   Option A - Via script de déploiement:")
    print("   Ajoute dans build/start command:")
    print(f"   cp {SESSION_FILE} /data/ig_session.json 2>/dev/null || true")
    print()
    print("   Option B - Upload manuel via Render shell:")
    print("   - Ouvre le shell Render")
    print("   - Copie le contenu du fichier")
    print("   - cat > /data/ig_session.json")
    print("   - Colle le contenu + Ctrl+D")
    print()
    print("4. Redémarre le service Render")
    print()
    print("✅ Ensuite le bot réutilisera cette session sans 2FA!")
    
except Exception as exc:
    error_msg = str(exc)
    print()
    print(f"❌ Échec de connexion: {error_msg}")
    print()
    
    if "Two-factor" in error_msg or "verification_code" in error_msg:
        print("💡 Solutions:")
        print("1. Vérifie que le code 2FA est correct")
        print("2. Si tu utilises IG_TOTP_SECRET, vérifie qu'il est correct")
        print("3. Réessaye avec un nouveau code (ils expirent après 30s)")
    elif "blacklist" in error_msg.lower() or "checkpoint" in error_msg.lower():
        print("💡 Solutions:")
        print("1. Vérifie ton IP locale (utilise un autre réseau si nécessaire)")
        print("2. Essaye depuis un navigateur d'abord pour valider le compte")
        print("3. Attends quelques heures si Instagram a détecté trop de tentatives")
    else:
        print("💡 Solutions:")
        print("1. Vérifie IG_USER et IG_PASS dans .env")
        print("2. Vérifie que ton compte Instagram n'a pas de challenge en cours")
        print("3. Essaye de te connecter depuis l'app mobile Instagram d'abord")
    
    sys.exit(1)
