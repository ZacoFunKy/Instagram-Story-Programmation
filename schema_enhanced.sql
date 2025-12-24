-- Instagram Story Scheduler - Enhanced Database Schema
-- Version: 2.0
-- Description: Schema professionnel avec index, contraintes et métadonnées

-- Extension pour UUID (si pas déjà activée)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Table principale des stories
CREATE TABLE IF NOT EXISTS stories (
    -- Identifiants
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    chat_id BIGINT NOT NULL,
    
    -- Données média
    file_id TEXT NOT NULL,
    media_type VARCHAR(20) NOT NULL DEFAULT 'photo' CHECK (media_type IN ('photo', 'video')),
    file_size_bytes BIGINT,
    original_filename TEXT,
    
    -- Configuration publication
    scheduled_time TIMESTAMPTZ NOT NULL,
    to_close_friends BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Statut et suivi
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' 
        CHECK (status IN ('PENDING', 'PUBLISHED', 'ERROR', 'CANCELLED')),
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    
    -- Métadonnées publication
    published_at TIMESTAMPTZ,
    instagram_story_id TEXT,
    
    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Contraintes
    CONSTRAINT scheduled_time_future CHECK (scheduled_time >= created_at),
    CONSTRAINT valid_retry_count CHECK (retry_count >= 0 AND retry_count <= 5)
);

-- Index pour les requêtes fréquentes
CREATE INDEX IF NOT EXISTS idx_stories_status_scheduled 
    ON stories(status, scheduled_time) 
    WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_stories_chat_id 
    ON stories(chat_id);

CREATE INDEX IF NOT EXISTS idx_stories_created_at 
    ON stories(created_at DESC);

-- Index pour les statistiques
CREATE INDEX IF NOT EXISTS idx_stories_status_chat 
    ON stories(chat_id, status);

-- Trigger pour mettre à jour automatiquement updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_stories_updated_at 
    BEFORE UPDATE ON stories
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Table pour les logs d'événements (optionnel mais professionnel)
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

-- Vue pour les statistiques (très utile pour monitoring)
CREATE OR REPLACE VIEW story_statistics AS
SELECT 
    chat_id,
    COUNT(*) as total_stories,
    COUNT(*) FILTER (WHERE status = 'PENDING') as pending_count,
    COUNT(*) FILTER (WHERE status = 'PUBLISHED') as published_count,
    COUNT(*) FILTER (WHERE status = 'ERROR') as error_count,
    COUNT(*) FILTER (WHERE status = 'CANCELLED') as cancelled_count,
    COUNT(*) FILTER (WHERE to_close_friends = TRUE) as close_friends_count,
    COUNT(*) FILTER (WHERE media_type = 'video') as video_count,
    COUNT(*) FILTER (WHERE media_type = 'photo') as photo_count,
    MAX(published_at) as last_published_at,
    MIN(scheduled_time) FILTER (WHERE status = 'PENDING') as next_scheduled_at
FROM stories
GROUP BY chat_id;

-- Fonction pour nettoyer les vieilles stories (> 30 jours)
CREATE OR REPLACE FUNCTION cleanup_old_stories()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM stories
    WHERE status IN ('PUBLISHED', 'ERROR', 'CANCELLED')
    AND updated_at < NOW() - INTERVAL '30 days';
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Commentaires pour documentation
COMMENT ON TABLE stories IS 'Table principale stockant toutes les stories programmées';
COMMENT ON COLUMN stories.chat_id IS 'ID du chat Telegram de l''utilisateur';
COMMENT ON COLUMN stories.file_id IS 'ID du fichier sur les serveurs Telegram';
COMMENT ON COLUMN stories.to_close_friends IS 'Story visible uniquement pour les amis proches Instagram';
COMMENT ON COLUMN stories.retry_count IS 'Nombre de tentatives de publication (max 5)';
COMMENT ON VIEW story_statistics IS 'Vue agrégée des statistiques par utilisateur';

-- Données initiales / exemples (à commenter en production)
-- INSERT INTO stories (chat_id, file_id, scheduled_time, media_type) 
-- VALUES (123456789, 'test_file_id', NOW() + INTERVAL '1 hour', 'photo');
