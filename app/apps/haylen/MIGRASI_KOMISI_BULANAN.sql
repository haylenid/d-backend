-- Migrasi Komisi Bulanan (Fase A + C, 2026-10-21)
-- 1) Kolom end_orig pada clients: tanggal selesai klien SEBELUM pengunduran akibat
--    izin/cuti coach (mode "diundur"). end tetap tanggal selesai yang berlaku.
-- 2) Tabel titip (Fase C): klien yang dititipkan ke coach pengganti per tanggal.
-- Aman diulang (IF NOT EXISTS).
ALTER TABLE haylen.clients ADD COLUMN IF NOT EXISTS end_orig DATE;

CREATE TABLE IF NOT EXISTS haylen.titip (
    id          VARCHAR(16) PRIMARY KEY,
    client_id   VARCHAR(16) REFERENCES haylen.clients(id),
    date        DATE NOT NULL,
    from_coach  VARCHAR(16) NOT NULL,
    to_coach    VARCHAR(16) NOT NULL,
    time        VARCHAR(5)  NOT NULL,
    request_id  VARCHAR(16),
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_titip_client ON haylen.titip (client_id);
CREATE INDEX IF NOT EXISTS ix_titip_date ON haylen.titip (date);
CREATE INDEX IF NOT EXISTS ix_titip_to ON haylen.titip (to_coach);
