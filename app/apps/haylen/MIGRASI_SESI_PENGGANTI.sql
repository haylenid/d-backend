-- Migrasi Sesi Pengganti (2026-10-21)
-- Tabel makeup: sesi pengganti hasil pengunduran izin coach, ditempatkan pada
-- tanggal + jam pilihan (boleh di luar pola mingguan klien), dilatih coach sama.
-- Aman diulang (IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS haylen.makeup (
    id          VARCHAR(16) PRIMARY KEY,
    client_id   VARCHAR(16) REFERENCES haylen.clients(id),
    coach_id    VARCHAR(16) NOT NULL,
    date        DATE NOT NULL,
    time        VARCHAR(5)  NOT NULL,
    request_id  VARCHAR(16),
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_makeup_client ON haylen.makeup (client_id);
CREATE INDEX IF NOT EXISTS ix_makeup_date ON haylen.makeup (date);
CREATE INDEX IF NOT EXISTS ix_makeup_coach ON haylen.makeup (coach_id);
