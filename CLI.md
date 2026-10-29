# d-backend CLI — `dbcli.py` (v2)

Alat baris perintah untuk mengatur database d-backend, dijalankan dari
root repo (tempat file `.env` berada), memakai venv yang sama dengan service:

```
venv/bin/python dbcli.py --help
```

## Model user per app

- **dbn6sport, haylen:** login = kolom `login`, hak = `role`
  (`owner`/`admin`/`head`/`coach`), id `u1`, `u2`, ...
- **dbacc:** login = `email` (disimpan huruf kecil), hak = `tier`
  (0 Owner, 1 Admin, 2 Staff, 3 Viewer), id UUID otomatis.
- **dfin:** login = `username` (3-64 karakter), hak = `tier` (0 Owner, 1-3),
  id integer otomatis.
- **claisrox:** login = `username` (3-50 karakter), hak = `tier`
  (0 Owner, 1 Manager, 2 Staf Produksi & Gudang, 3 Kasir & Toko Online),
  id integer otomatis.
- Tier default user baru = 3 (paling bawah) di ketiga app tier.

## Perintah

| Perintah | Fungsi |
|---|---|
| `status` | Daftar schema, tabel, dan jumlah baris. Baca saja, tidak mengubah apa pun. |
| `users list --app <app>` | Daftar user (id, login, role/tier, aktif untuk app tier). |
| `user create --app haylen --login owner --name "Owner Haylen" --role owner` | Buat user olahraga. Kata sandi diminta interaktif (tidak tampil saat diketik). |
| `user create --app dbacc --login user@example.com --name "User Baru" [--tier 3]` | Buat user tier. `--tier` default 3 bila tidak diisi. |
| `user passwd --app <app> --login <login>` | Ganti kata sandi user. |
| `user delete --app <app> --login <login>` | Hapus user, dengan konfirmasi ketik login. Owner/tier-0 satu-satunya ditolak, kecuali `--force`. |
| `user set-role --app haylen --login <login> --role admin` | Ubah role (hanya sport). |
| `user set-tier --app dbacc --login <login> --tier 2` | Ubah tier (hanya app tier). Penurunan tier-0 satu-satunya ditolak, kecuali `--force`. |
| `user set-active --app dfin --login <login> --active false` | Aktifkan/nonaktifkan user (hanya app tier). Nonaktifkan tier-0 satu-satunya ditolak, kecuali `--force`. |
| `settings list/get/set --app haylen` | Kelola setting olahraga (`targets`, `acl`, ...). Nilai `set` harus JSON dan mengganti isi setting itu sepenuhnya. |
| `db init [--basics]` | Buat schema dan tabel bila belum ada. `--basics` sekalian membuat settings `targets`/`acl` olahraga bila belum ada. |
| `db backup --app <app> --out /tmp/<app>.sql` | `pg_dump` satu schema ke file. |
| `db reset --app <app> --confirm <app> --backup /tmp/<app>.sql` | **Destruktif:** kosongkan satu schema, lalu buat ulang settings dasar olahraga. |

## Aturan keamanan

- Kata sandi **tidak pernah** lewat argumen CLI. Tidak ada opsi `--password`.
- CLI **tidak pernah** mencetak hash kata sandi atau URL database.
- `db reset` hanya satu schema per perintah (tidak ada reset semua app sekaligus),
  wajib `--confirm <app>` yang persis sama, wajib backup atau `--no-backup`
  eksplisit, dan wajib mengetik `RESET <app>` secara interaktif.
- Perintah `settings` hanya untuk `dbn6sport`/`haylen`, karena hanya kedua
  app itu yang punya tabel Setting seperti itu.
- Setelah `db reset`, app akan terkunci sampai owner/tier-0 baru dibuat. Pakai
  `--create-owner` untuk menampilkan perintah pembuat owner, atau jalankan
  langsung `user create` dengan `--role owner` (sport) / `--tier 0` (app tier).

## Catatan

- File ini dan `dbcli.py` adalah perkakas operator, bukan bagian API.
  Jangan pernah memasukkan kata sandi ke dalam file, commit, atau chat.
