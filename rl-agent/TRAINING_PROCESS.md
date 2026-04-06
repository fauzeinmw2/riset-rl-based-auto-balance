# RL Agent Training Process

Dokumen ini menetapkan proses training sebelum agent dipakai realtime agar tidak menyebabkan layanan mati.

## 1. Offline Pretraining (Wajib)

Tujuan:
- Melatih policy SAC pada lingkungan simulasi dulu.
- Menghindari eksplorasi berbahaya langsung pada API nyata.

Langkah:
1. Build image dan install dependency RL.
2. Jalankan trainer offline menggunakan synthetic environment.
3. Simpan model checkpoint ke folder models.
4. Evaluasi reward rata-rata dan memastikan SLA violation tidak naik.

Command contoh:

```bash
python rl-agent/offline_trainer.py
```

Output:
- `rl-agent/models/sac_resource_controller.zip`

## 2. Staging Fine-Tuning (Direkomendasikan)

Tujuan:
- Menyesuaikan policy ke trafik yang lebih realistis tanpa mengganggu layanan produksi.

Langkah:
1. Jalankan stack staging (api + postgres + prometheus + rl-agent).
2. Replay trafik uji dengan `load_test/spike_test.js`.
3. Jalankan agent dalam mode inference dan monitor metrik.
4. Promosikan model jika:
   - p95 response time membaik atau setidaknya tidak memburuk signifikan.
   - rata-rata CPU/RAM turun dibanding baseline.
   - error rate tetap di bawah threshold.

## 3. Controlled Online Rollout (Bertahap)

Tujuan:
- Memakai model di runtime nyata dengan guardrail keselamatan.

Langkah:
1. Mulai dengan interval kontrol konservatif (10 detik).
2. Aktifkan safe upscale pada kondisi berikut:
   - near OOM (memori > 90% limit)
   - error rate melewati threshold
   - response time lonjakan ekstrem
3. Gunakan forecast-aware limiter untuk mencegah downscale agresif ketika spike diprediksi.
4. Evaluasi berkala via script perbandingan baseline vs RL.

## 4. Benchmark dan Pelaporan

1. Jalankan spike test baseline dan RL dengan pola acak yang sama.
2. Gunakan `load_test/run_compare.py` untuk mengeluarkan tabel CLI 3 kolom:
   - API-Baseline
   - API-RL
   - Perbandingan
3. Gunakan satuan konsisten:
   - CPU: persen dan core
   - RAM: MB/GB
   - Performance: response time (ms), request success

## 5. Rekomendasi Siklus Eksperimen

- Jalankan minimal 3 putaran benchmark.
- Ambil rata-rata dan variansi agar hasil riset stabil.
- Simpan model terbaik per eksperimen (misalnya: `sac_resource_controller_v1.zip`, `v2.zip`).
