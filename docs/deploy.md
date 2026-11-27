# Workflow Deploy

## Tujuan

- **Lokal**: tempat develop & test cepat.
- **GitHub**: source of truth / blueprint repo.
- **Vercel**: target produksi (URL web).

## Status saat ini

- Repo GitHub: `ridayz/dark-fiber-core-manager` (branch `main`).
- App saat ini berjalan sebagai **Python server long-running** (`src/server/app.py`) + **SQLite** (`src/server/darkfiber.db`) + **background sheet sync thread**.
- Karena arsitektur itu, **deploy langsung ke Vercel belum plug-and-play**. Vercel cocok untuk frontend static/serverless; app ini perlu rework (backend serverless/API, DB hosted persisten, cron sync) sebelum jadi produksi Vercel yang penuh.

## Workflow yang dipakai

1. **Develop lokal**
   - Jalankan: `run.cmd` (Windows) / `./run.sh`
   - Test: `python -m unittest discover -s tests -q`
   - Cek UI: `http://localhost:4174`

2. **Push ke GitHub**
   ```bash
   git add <file>
   git commit -m "pesan"
   git push origin main
   ```
   GitHub jadi blueprint/history utama.

3. **Sambungkan GitHub ke Vercel**
   - Login ke [vercel.com](https://vercel.com)
   - **Add New... > Project**
   - Import repo GitHub `ridayz/dark-fiber-core-manager`
   - Set framework preset sesuai bentuk deploy yang dipakai nanti
   - Deploy

   Setelah tersambung, setiap push ke branch produksi (biasanya `main`) bisa otomatis trigger deploy Vercel.

## Catatan Vercel

- Domain default gratis: `*.vercel.app`.
- Custom domain bisa dipasang; koneksi di Vercel gratis, biaya domain tergantung registrar.
- Sebelum produksi Vercel aktif penuh, siapkan dulu:
  - frontend build/static strategy
  - backend API strategy (serverless / external service)
  - DB persisten hosted (bukan SQLite file lokal ephemeral)
  - scheduler/cron untuk sheet sync

## Opsi sementara untuk akses web tanpa rework

Kalau butuh URL web cepat untuk app yang sekarang:
- Cloudflare Tunnel / Tailscale Funnel / ngrok ke `localhost:4174`
- Atau host Python service di VPS/persistent volume
