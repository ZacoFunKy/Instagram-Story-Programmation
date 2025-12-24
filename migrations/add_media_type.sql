-- Migration pour ajouter le support vidéo
-- Ajoute la colonne media_type à la table stories
-- À exécuter via Supabase SQL Editor

-- Ajouter la colonne media_type avec valeur par défaut 'photo'
ALTER TABLE stories ADD COLUMN IF NOT EXISTS media_type VARCHAR(10) NOT NULL DEFAULT 'photo';

-- Ajouter la contrainte pour valider les valeurs possibles
ALTER TABLE stories ADD CONSTRAINT valid_media_type CHECK (media_type IN ('photo', 'video'));

-- Commentaire pour documentation
COMMENT ON COLUMN stories.media_type IS 'Type de média: photo ou video';

-- Mettre à jour toutes les stories existantes pour avoir le type 'photo'
UPDATE stories SET media_type = 'photo' WHERE media_type IS NULL;

-- Vérifier les données
SELECT id, media_type, scheduled_time, status FROM stories LIMIT 5;
