# Load Test and Comparison

Folder ini berisi spike test acak (non linear) dan skrip ringkasan hasil baseline vs RL.

## Prasyarat

- Stack docker sudah berjalan (`api-baseline`, `api-rl`, `postgres`, `prometheus`).
- k6 terpasang.
- Python environment memiliki `requests`.

## 1. Jalankan spike test + summary perbandingan

```bash
python load_test/run_compare.py \
  --prom-url http://localhost:9090 \
  --baseline-url http://localhost:3000 \
  --rl-url http://localhost:3002
```

Output terminal berupa tabel 3 kolom:
- API-Baseline
- API-RL
- Perbandingan

Metrik yang ditampilkan:
- CPU Usage (persen dan core)
- RAM Usage (MB/GB)
- Response Time (ms)
- Request Success

## 2. Jalankan k6 manual (opsional)

```bash
k6 run load_test/spike_test.js -e TARGET_URL=http://localhost:3000
k6 run load_test/spike_test.js -e TARGET_URL=http://localhost:3002
```

## 3. Catatan

- Script perbandingan menggunakan seed sama untuk baseline dan RL agar pola spike setara.
- Untuk validitas riset, jalankan minimal 3 kali dan hitung rata-rata serta variansi.
