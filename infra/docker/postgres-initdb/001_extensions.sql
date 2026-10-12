-- Nur fuer das schlanke lokale Postgres-Profil (docker-compose.dev.yml ohne
-- Supabase). Bei Supabase sind diese Extensions bereits vorhanden.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
