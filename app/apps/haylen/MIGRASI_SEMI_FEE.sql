-- Komisi semi privat custom per program + per kota (Haylen, 2026-11-16).
-- NULL = pakai lapisan di atasnya: tarif kota -> tarif program -> bawaan 75.000/85.000.
-- Jalankan sebagai postgres: sudo -u postgres psql -d dbackend -f /tmp/MIGRASI_SEMI_FEE.sql
ALTER TABLE haylen.programs ADD COLUMN IF NOT EXISTS semi_fee_1 INTEGER;
ALTER TABLE haylen.programs ADD COLUMN IF NOT EXISTS semi_fee_2 INTEGER;
ALTER TABLE haylen.program_city_rates ADD COLUMN IF NOT EXISTS semi_fee_1 INTEGER;
ALTER TABLE haylen.program_city_rates ADD COLUMN IF NOT EXISTS semi_fee_2 INTEGER;
