-- ============================================================================
-- Instagram Story Scheduler - Schéma Complet de Base de Données
-- ============================================================================
-- Version: 3.0
-- Date: 25 Décembre 2025
-- Description: Schéma complet incluant toutes les fonctionnalités:
--              - Gestion des stories (publication, brouillons, annulation)
--              - Système de retry automatique avec backoff exponentiel
--              - Overlays texte et musique
--              - Statistiques avancées et monitoring
--              - Nettoyage automatique des anciennes stories
-- ============================================================================

-- Extension pour UUID (si pas déjà activée)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- TABLE PRINCIPALE: stories
-- ============================================================================
CREATE TABLE IF NOT EXISTS stories (
    -- -------------------------------------------------------------------------
    -- Identifiants
    -- -------------------------------------------------------------------------
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    chat_id BIGINT NOT NULL,
    
    -- -------------------------------------------------------------------------
    -- Données média
    -- -------------------------------------------------------------------------
    file_id TEXT NOT NULL,
    media_type VARCHAR(20) NOT NULL DEFAULT 'photo' CHECK (media_type IN ('photo', 'video')),
    file_size_bytes BIGINT,
    original_filename TEXT,
    
    -- -------------------------------------------------------------------------
    -- Overlays (texte et musique)
    -- -------------------------------------------------------------------------
    text_overlay TEXT,
    text_position VARCHAR(20) DEFAULT 'center' CHECK (text_position IN ('top', 'center', 'bottom')),
    text_color VARCHAR(20) DEFAULT '#FFFFFF',
    music_file_id TEXT,
    music_volume DECIMAL(3,2) DEFAULT 0.5 CHECK (music_volume >= 0 AND music_volume <= 1),
    
    -- -------------------------------------------------------------------------
    -- Configuration publication
    -- -------------------------------------------------------------------------
    scheduled_time TIMESTAMPTZ NOT NULL,
    to_close_friends BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- -------------------------------------------------------------------------
    -- Statut et suivi
    -- -------------------------------------------------------------------------
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' 
        CHECK (status IN ('PENDING', 'PUBLISHED', 'ERROR', 'CANCELLED', 'DRAFT')),
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    last_retry_at TIMESTAMPTZ,
    
    -- -------------------------------------------------------------------------
    -- Métadonnées publication
    -- -------------------------------------------------------------------------
    published_at TIMESTAMPTZ,
    instagram_story_id TEXT,
    
    -- -------------------------------------------------------------------------
    -- Timestamps
    -- -------------------------------------------------------------------------
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- -------------------------------------------------------------------------
    -- Contraintes
    -- -------------------------------------------------------------------------
    CONSTRAINT scheduled_time_future CHECK (scheduled_time >= created_at),
    CONSTRAINT valid_retry_count CHECK (retry_count >= 0 AND retry_count <= 5)
);

-- ============================================================================
-- INDEX POUR PERFORMANCES
-- ============================================================================

-- Index pour les requêtes de publication (worker principal)
CREATE INDEX IF NOT EXISTS idx_stories_status_scheduled 
    ON stories(status, scheduled_time) 
    WHERE status = 'PENDING';

-- Index pour le système de retry
CREATE INDEX IF NOT EXISTS idx_stories_retry 
    ON stories(status, retry_count, last_retry_at) 
    WHERE status = 'ERROR';

-- Index pour les brouillons
CREATE INDEX IF NOT EXISTS idx_stories_drafts 
    ON stories(chat_id, status) 
    WHERE status = 'DRAFT';

-- Index pour les requêtes par utilisateur
CREATE INDEX IF NOT EXISTS idx_stories_chat_id 
    ON stories(chat_id);

-- Index pour l'ordre chronologique
CREATE INDEX IF NOT EXISTS idx_stories_created_at 
    ON stories(created_at DESC);

-- Index pour les statistiques
CREATE INDEX IF NOT EXISTS idx_stories_status_chat 
    ON stories(chat_id, status);

-- ============================================================================
-- TRIGGERS
-- ============================================================================

-- Trigger pour mettre à jour automatiquement updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_stories_updated_at 
    BEFORE UPDATE ON stories
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- TABLE DES ÉVÉNEMENTS (Logs détaillés)
-- ============================================================================
CREATE TABLE IF NOT EXISTS story_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    story_id UUID NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    event_data JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_story_events_story_id 
    ON story_events(story_id);

CREATE INDEX IF NOT EXISTS idx_story_events_created_at 
    ON story_events(created_at DESC);

-- ============================================================================
-- VUES STATISTIQUES
-- ============================================================================

-- Vue principale des statistiques par utilisateur
CREATE OR REPLACE VIEW story_statistics AS
SELECT 
    chat_id,
    -- Comptages généraux
    COUNT(*) as total_stories,
    COUNT(*) FILTER (WHERE status = 'PENDING') as pending_count,
    COUNT(*) FILTER (WHERE status = 'PUBLISHED') as published_count,
    COUNT(*) FILTER (WHERE status = 'ERROR') as error_count,
    COUNT(*) FILTER (WHERE status = 'CANCELLED') as cancelled_count,
    COUNT(*) FILTER (WHERE status = 'DRAFT') as draft_count,
    
    -- Comptages par type
    COUNT(*) FILTER (WHERE to_close_friends = TRUE) as close_friends_count,
    COUNT(*) FILTER (WHERE media_type = 'video') as video_count,
    COUNT(*) FILTER (WHERE media_type = 'photo') as photo_count,
    
    -- Comptages overlays
    COUNT(*) FILTER (WHERE text_overlay IS NOT NULL) as with_text_count,
    COUNT(*) FILTER (WHERE music_file_id IS NOT NULL) as with_music_count,
    
    -- Métriques temporelles
    MAX(published_at) as last_published_at,
    MIN(scheduled_time) FILTER (WHERE status = 'PENDING') as next_scheduled_at,
    
    -- Métriques de qualité
    AVG(retry_count) FILTER (WHERE status = 'PUBLISHED') as avg_retries,
    ROUND(
        COUNT(*) FILTER (WHERE status = 'PUBLISHED')::DECIMAL / 
        NULLIF(COUNT(*) FILTER (WHERE status IN ('PUBLISHED', 'ERROR')), 0) * 100,
        1
    ) as success_rate
FROM stories
GROUP BY chat_id;

-- Vue pour le monitoring des retry
CREATE OR REPLACE VIEW retry_monitoring AS
SELECT 
    DATE_TRUNC('day', updated_at) as date,
    COUNT(*) as error_count,
    AVG(retry_count) as avg_retry_count,
    MAX(retry_count) as max_retry_count,
    COUNT(*) FILTER (WHERE retry_count >= 3) as permanent_failures
FROM stories
WHERE status = 'ERROR'
GROUP BY DATE_TRUNC('day', updated_at)
ORDER BY date DESC;

-- ============================================================================
-- FONCTIONS UTILITAIRES
-- ============================================================================

-- Fonction pour récupérer les stories à retenter
CREATE OR REPLACE FUNCTION get_stories_for_retry(retry_delay_minutes INTEGER DEFAULT 5)
RETURNS TABLE (
    id UUID,
    chat_id BIGINT,
    file_id TEXT,
    media_type VARCHAR(20),
    retry_count INTEGER,
    error_message TEXT,
    last_retry_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        s.id,
        s.chat_id,
        s.file_id,
        s.media_type,
        s.retry_count,
        s.error_message,
        s.last_retry_at
    FROM stories s
    WHERE s.status = 'ERROR'
    AND s.retry_count < 3
    AND (
        s.last_retry_at IS NULL 
        OR s.last_retry_at < NOW() - (retry_delay_minutes || ' minutes')::INTERVAL
    )
    ORDER BY s.updated_at ASC;
END;
$$ LANGUAGE plpgsql;

-- Fonction pour nettoyer les anciennes stories
CREATE OR REPLACE FUNCTION cleanup_old_stories_v2(
    published_days INTEGER DEFAULT 30,
    error_days INTEGER DEFAULT 7,
    cancelled_days INTEGER DEFAULT 7
)
RETURNS TABLE (
    status VARCHAR(20),
    deleted_count INTEGER
) AS $$
DECLARE
    published_deleted INTEGER := 0;
    error_deleted INTEGER := 0;
    cancelled_deleted INTEGER := 0;
BEGIN
    -- Supprimer les stories publiées
    DELETE FROM stories
    WHERE status = 'PUBLISHED'
    AND updated_at < NOW() - (published_days || ' days')::INTERVAL;
    GET DIAGNOSTICS published_deleted = ROW_COUNT;
    
    -- Supprimer les stories en erreur (seulement les échecs définitifs)
    DELETE FROM stories
    WHERE status = 'ERROR'
    AND retry_count >= 3
    AND updated_at < NOW() - (error_days || ' days')::INTERVAL;
    GET DIAGNOSTICS error_deleted = ROW_COUNT;
    
    -- Supprimer les stories annulées
    DELETE FROM stories
    WHERE status = 'CANCELLED'
    AND updated_at < NOW() - (cancelled_days || ' days')::INTERVAL;
    GET DIAGNOSTICS cancelled_deleted = ROW_COUNT;
    
    -- Retourner les résultats
    RETURN QUERY VALUES 
        ('PUBLISHED'::VARCHAR(20), published_deleted),
        ('ERROR'::VARCHAR(20), error_deleted),
        ('CANCELLED'::VARCHAR(20), cancelled_deleted);
END;
$$ LANGUAGE plpgsql;

-- Fonction pour obtenir le nombre de stories en attente pour un utilisateur
CREATE OR REPLACE FUNCTION get_user_pending_count(user_chat_id BIGINT)
RETURNS INTEGER AS $$
DECLARE
    pending_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO pending_count
    FROM stories
    WHERE chat_id = user_chat_id
    AND status IN ('PENDING', 'DRAFT');
    
    RETURN pending_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- COMMENTAIRES POUR DOCUMENTATION
-- ============================================================================

-- Table principale
COMMENT ON TABLE stories IS 'Table principale stockant toutes les stories programmées';
COMMENT ON COLUMN stories.chat_id IS 'ID du chat Telegram de l''utilisateur';
COMMENT ON COLUMN stories.file_id IS 'ID du fichier sur les serveurs Telegram';
COMMENT ON COLUMN stories.media_type IS 'Type de média: photo ou video';
COMMENT ON COLUMN stories.file_size_bytes IS 'Taille du fichier en octets';
COMMENT ON COLUMN stories.original_filename IS 'Nom original du fichier uploadé';

-- Overlays
COMMENT ON COLUMN stories.text_overlay IS 'Texte à superposer sur le média (optionnel)';
COMMENT ON COLUMN stories.text_position IS 'Position du texte: top, center, ou bottom';
COMMENT ON COLUMN stories.text_color IS 'Couleur du texte en format hexadécimal (#FFFFFF)';
COMMENT ON COLUMN stories.music_file_id IS 'ID du fichier audio Telegram pour overlay musical';
COMMENT ON COLUMN stories.music_volume IS 'Volume de la musique (0.0 à 1.0)';

-- Configuration
COMMENT ON COLUMN stories.scheduled_time IS 'Date et heure de publication programmée (UTC avec timezone)';
COMMENT ON COLUMN stories.to_close_friends IS 'Story visible uniquement pour les amis proches Instagram';

-- Statut et retry
COMMENT ON COLUMN stories.status IS 'Statut: PENDING (en attente), PUBLISHED (publiée), ERROR (erreur), CANCELLED (annulée), DRAFT (brouillon)';
COMMENT ON COLUMN stories.error_message IS 'Message d''erreur détaillé en cas d''échec';
COMMENT ON COLUMN stories.retry_count IS 'Nombre de tentatives de publication (max 5)';
COMMENT ON COLUMN stories.last_retry_at IS 'Date/heure de la dernière tentative de retry';

-- Métadonnées
COMMENT ON COLUMN stories.published_at IS 'Date et heure de publication réussie';
COMMENT ON COLUMN stories.instagram_story_id IS 'ID de la story sur Instagram après publication';
COMMENT ON COLUMN stories.created_at IS 'Date de création de l''enregistrement';
COMMENT ON COLUMN stories.updated_at IS 'Date de dernière modification (mise à jour automatique)';

-- Tables et vues
COMMENT ON TABLE story_events IS 'Table de logs pour tracer tous les événements liés aux stories';
COMMENT ON VIEW story_statistics IS 'Vue agrégée des statistiques par utilisateur';
COMMENT ON VIEW retry_monitoring IS 'Vue de monitoring pour analyser les retry et erreurs';

-- Fonctions
COMMENT ON FUNCTION get_stories_for_retry IS 'Récupère les stories en erreur éligibles pour retry avec délai configurable';
COMMENT ON FUNCTION cleanup_old_stories_v2 IS 'Nettoie les anciennes stories avec paramètres personnalisables par statut';
COMMENT ON FUNCTION get_user_pending_count IS 'Compte le nombre de stories en attente ou brouillon pour un utilisateur';

-- ============================================================================
-- DONNÉES DE TEST (À COMMENTER EN PRODUCTION)
-- ============================================================================

/*
-- Exemples de données pour tester
INSERT INTO stories (chat_id, file_id, scheduled_time, media_type, text_overlay) 
VALUES 
    (123456789, 'test_file_id_1', NOW() + INTERVAL '1 hour', 'photo', 'Test Story 1'),
    (123456789, 'test_file_id_2', NOW() + INTERVAL '2 hours', 'video', NULL);

-- Test de la fonction de retry
SELECT * FROM get_stories_for_retry(5);

-- Test de la fonction de cleanup
SELECT * FROM cleanup_old_stories_v2(30, 7, 7);

-- Test des statistiques
SELECT * FROM story_statistics;

-- Test du monitoring retry
SELECT * FROM retry_monitoring;
*/

-- ============================================================================
-- FIN DU SCHÉMA
-- ============================================================================
