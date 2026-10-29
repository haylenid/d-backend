-- Migrasi: token acak untuk link portal publik klien (2026-10-23)
-- Jalankan SEKALI di database produksi (schema haylen), lalu pull backend
-- + restart. Setelah migrasi, link lama #c=HY-xxxx berhenti membuka portal
-- publik (uid urut = mudah ditebak); link baru memakai portal_token acak.
ALTER TABLE haylen.clients ADD COLUMN IF NOT EXISTS portal_token VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS ix_haylen_clients_portal_token
  ON haylen.clients (portal_token);
-- Backfill: token acak 64 heksadesimal untuk semua klien yang belum punya.
UPDATE haylen.clients
   SET portal_token = md5(random()::text || id || clock_timestamp()::text)
                   || md5(id || random()::text || clock_timestamp()::text)
 WHERE portal_token IS NULL;
