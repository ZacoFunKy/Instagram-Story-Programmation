-- Table pour stocker les stories programmées
CREATE TABLE IF NOT EXISTS stories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id BIGINT NOT NULL,
    file_id TEXT NOT NULL,
    scheduled_time TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    error_message TEXT,
    
    CONSTRAINT valid_status CHECK (status IN ('PENDING', 'PUBLISHED', 'ERROR', 'CANCELLED'))
);

-- Index pour optimiser les requêtes fréquentes
CREATE INDEX IF NOT EXISTS idx_stories_status_scheduled 
ON stories(status, scheduled_time) 
WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_stories_chat_id 
ON stories(chat_id);

-- Fonction trigger pour mettre à jour updated_at automatiquement
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

-- Vue pour statistiques (optionnelle mais utile)
CREATE OR REPLACE VIEW stories_stats AS
SELECT 
    chat_id,
    COUNT(*) FILTER (WHERE status = 'PENDING') as pending_count,
    COUNT(*) FILTER (WHERE status = 'PUBLISHED') as published_count,
    COUNT(*) FILTER (WHERE status = 'ERROR') as error_count,
    COUNT(*) FILTER (WHERE status = 'CANCELLED') as cancelled_count,
    MAX(scheduled_time) FILTER (WHERE status = 'PENDING') as next_scheduled
FROM stories
GROUP BY chat_id;
