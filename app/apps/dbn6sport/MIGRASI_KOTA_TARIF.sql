-- MIGRASI_KOTA_TARIF.sql — tarif per kota (2026-11-12).
-- Dijalankan sekali oleh pemilik di VPS (psql -d dbackend -f file).
-- Tabel baru (cities, program_city_rates) juga dibuat otomatis oleh
-- create_all saat backend start; CREATE TABLE di sini idempotent.
-- Klien/anggota lama: city_id NULL (belum ada kota) — ditandai di FE,
-- sesinya belum bisa dihitung sampai diisi kotanya.

CREATE TABLE IF NOT EXISTS dn6sport.cities (
    id VARCHAR(16) PRIMARY KEY,
    name VARCHAR(160) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dn6sport.program_city_rates (
    program_id VARCHAR(16) NOT NULL REFERENCES dn6sport.programs(id),
    city_id VARCHAR(16) NOT NULL REFERENCES dn6sport.cities(id),
    price INTEGER,
    fee INTEGER,
    PRIMARY KEY (program_id, city_id)
);

ALTER TABLE dn6sport.clients
    ADD COLUMN IF NOT EXISTS city_id VARCHAR(16) REFERENCES dn6sport.cities(id);
ALTER TABLE dn6sport.members
    ADD COLUMN IF NOT EXISTS city_id VARCHAR(16) REFERENCES dn6sport.cities(id);

CREATE INDEX IF NOT EXISTS ix_dn6sport_clients_city_id ON dn6sport.clients (city_id);
CREATE INDEX IF NOT EXISTS ix_dn6sport_members_city_id ON dn6sport.members (city_id);
