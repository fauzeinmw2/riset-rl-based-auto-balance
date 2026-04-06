# RL-Based Auto Balance for API Energy-Performance Trade-off

Project ini adalah prototipe riset untuk menguji apakah Reinforcement Learning (RL) dapat menyeimbangkan trade-off antara:
- Efisiensi energi container API (indikator: CPU usage dan RAM usage), dan
- Performa layanan API (indikator: response time dan jumlah request sukses).

Agent RL mengontrol resource limit untuk API target (api-rl), lalu hasilnya dibandingkan dengan API baseline (api-baseline) yang tidak dikontrol agent.

## 1. Latar Belakang dan Tujuan

### Masalah
Pada sistem containerized service, memberi resource terlalu besar membuat boros energi, tetapi memberi resource terlalu kecil membuat response time memburuk atau layanan tidak stabil.

### Tujuan Riset
1. Menurunkan konsumsi CPU dan RAM pada API yang dikontrol RL.
2. Menjaga kualitas layanan (response time tetap baik, request sukses tidak turun signifikan).
3. Membandingkan hasil API baseline vs API RL secara terukur dan berulang.

### Sasaran Eksperimen
- API-RL idealnya lebih hemat energi daripada baseline.
- Penurunan performa harus tetap terkontrol.
- Hasil diukur lewat skenario load spike yang realistis (naik-turun acak).

## 2. Apa yang Dibangun di Project Ini

### Komponen utama
1. API baseline (tanpa kontrol RL).
2. API RL (dikontrol RL agent, limit CPU/RAM dapat diubah runtime).
3. RL agent (Stable-Baselines3 SAC + safety guard).
4. Prometheus + cAdvisor untuk observability metrik container dan API.
5. PostgreSQL untuk data workload API.
6. K6 load testing untuk spike test non-linear.
7. Script perbandingan hasil (CLI table) baseline vs RL, termasuk mode multi-run mean/std.

### Keluaran yang diharapkan
- Tabel perbandingan metrik:
  - CPU (% dan core)
  - RAM (MB/GB)
  - Response time (ms)
  - Request success
- Ringkasan improvement atau regression RL terhadap baseline.

## 3. Arsitektur Singkat

1. K6 mengirim beban traffic ke API baseline dan API RL.
2. API mengekspor metrik ke Prometheus.
3. cAdvisor memberi metrik container (CPU, RAM).
4. RL agent membaca metrik dari Prometheus.
5. RL agent memutuskan aksi scale up/down resource untuk container api-rl.
6. Script benchmarking mengambil hasil K6 + Prometheus lalu membuat tabel perbandingan.

## 4. Teknologi yang Digunakan

### Runtime dan service
- Docker & Docker Compose
- Node.js (API)
- PostgreSQL
- Prometheus
- cAdvisor
- Grafana (opsional visualisasi)

### Reinforcement Learning
- Python 3.10
- Stable-Baselines3 (SAC sebagai algoritma utama, PPO fallback)
- Gymnasium (synthetic training env)
- NumPy

### Load test dan evaluasi
- k6
- Python script untuk agregasi metrik dan CLI summary

## 5. Struktur Folder

- `api/` : service API Node.js + endpoint metrics
- `rl-agent/` : agent runtime, offline trainer, config training
- `postgres/` : init schema + seed data
- `prometheus/` : konfigurasi scrape metrik
- `load_test/` : script k6 dan script perbandingan hasil
- `docker-compose.yml` : orkestrasi seluruh service

Catatan: `api-go/` belum dipakai di eksperimen aktif saat ini.

## 6. Prasyarat

1. Docker Desktop aktif.
2. Docker Compose tersedia.
3. Python 3 terpasang untuk menjalankan script benchmarking lokal.
4. k6 terpasang.

Cek cepat:

```bash
docker --version
docker compose version
python3 --version
k6 version
```

## 7. Langkah Eksperimen End-to-End

### Step 1 - Jalankan semua service

```bash
docker compose up --build -d
```

Verifikasi:

```bash
docker compose ps
```

Minimal service berikut harus `Up`:
- `postgres`
- `prometheus`
- `cadvisor`
- `api-baseline`
- `api-rl`
- `rl-agent`

### Step 2 - Smoke test API

```bash
curl -sS http://localhost:3000/api/courses | head -c 200 && echo
curl -sS http://localhost:3002/api/courses | head -c 200 && echo
```

### Step 3 - Latih model RL (offline pretraining)

```bash
docker compose run --rm rl-agent python offline_trainer.py
```

Output yang harus muncul di akhir training:
- `Saved model: /app/models/sac_resource_controller.zip`
- `Evaluation reward mean=...`

File model akan tersimpan di host:
- `rl-agent/models/sac_resource_controller.zip`

### Step 4 - Pastikan agent memakai model terbaru

```bash
docker compose up --build -d rl-agent
docker compose logs --tail=60 rl-agent
```

Pastikan ada log:
- `Loaded policy model from /app/models/sac_resource_controller.zip`

### Step 5 - Jalankan benchmark 1 putaran

```bash
python3 load_test/run_compare.py \
  --prom-url http://localhost:9090 \
  --baseline-url http://localhost:3000 \
  --rl-url http://localhost:3002
```

### Step 6 - Jalankan benchmark multi-run (disarankan)

```bash
python3 load_test/run_compare.py \
  --prom-url http://localhost:9090 \
  --baseline-url http://localhost:3000 \
  --rl-url http://localhost:3002 \
  --rounds 3
```

`--rounds 3` akan memberi hasil mean/std yang lebih kuat untuk riset daripada single run.

## 8. Membaca Output Hasil

Script akan menampilkan tabel 3 kolom:
- API-Baseline
- API-RL
- Perbandingan

Contoh interpretasi:
1. Jika CPU dan RAM RL lebih kecil dari baseline, berarti efisiensi energi membaik.
2. Jika response time RL naik terlalu tinggi, berarti penghematan energi terlalu agresif.
3. Jika request success RL turun, berarti throughput/availability terdampak.

Rule praktis evaluasi:
1. Energi bagus jika CPU dan RAM turun konsisten pada beberapa run.
2. Performa aman jika response time dan request success tidak turun signifikan.
3. Keputusan akhir harus berdasarkan hasil agregat multi-run, bukan satu run.

## 9. Format Satuan yang Dipakai

- CPU: persen dan core, contoh `8.66% (0.09 core)`
- RAM: MB/GB, contoh `69.18 MB`
- Response time: ms
- Request success: `success/total`

## 10. Skenario Spike Test

Script k6 menggunakan pola beban non-monotonik (naik-turun acak), bukan linear low -> medium -> high.

Tujuannya:
- Mensimulasikan trafik real user yang fluktuatif.
- Menguji robustness kebijakan RL saat beban berubah mendadak.

## 11. File Penting untuk Tuning

- `rl-agent/training_config.yaml`
  - target constraint, bobot reward, action step
- `rl-agent/offline_trainer.py`
  - definisi synthetic env, reward shaping
- `rl-agent/agent.py`
  - runtime inference policy, safety guard, downscale limiter
- `load_test/spike_test.js`
  - skenario load dan threshold k6
- `load_test/run_compare.py`
  - perhitungan dan summary tabel

## 12. Troubleshooting

### A. K6 menampilkan threshold `http_req_failed` crossed
Penyebab umum: status 400 pada endpoint register dianggap gagal HTTP.

Status:
- Sudah ditangani dengan `responseCallback: http.expectedStatuses(200, 400)` pada request register.

### B. RL agent tidak load model
Cek:
1. File model ada di `rl-agent/models/sac_resource_controller.zip`.
2. Volume mount model benar di compose.
3. Restart `rl-agent` setelah training.

### C. RL agent crash saat update resource
Jika ada konflik opsi CPU update, pastikan konfigurasi Compose dan mekanisme update agent sinkron.

### D. Hasil RL hemat energi tapi latency memburuk
Lakukan:
1. Naikkan bobot SLA/throughput di reward.
2. Kurangi agresivitas downscale.
3. Perketat guard saat spike/overload.
4. Retrain model, ulang benchmark multi-run.

## 13. Alur Riset yang Disarankan

1. Setup stack dan verifikasi metrik.
2. Training offline model.
3. Validasi runtime agent.
4. Benchmark baseline vs RL (`--rounds 3` atau lebih).
5. Analisis trade-off energi vs performa.
6. Tuning reward/guard.
7. Ulang training + benchmark sampai target riset tercapai.

## 14. Command Ringkas (Quick Start)

```bash
# 1) Start stack
docker compose up --build -d

# 2) Train model
docker compose run --rm rl-agent python offline_trainer.py

# 3) Restart agent pakai model terbaru
docker compose up --build -d rl-agent

# 4) Compare baseline vs RL (3 rounds)
python3 load_test/run_compare.py --prom-url http://localhost:9090 --baseline-url http://localhost:3000 --rl-url http://localhost:3002 --rounds 3
```

## 15. Catatan Akhir

Project ini ditujukan untuk eksperimen riset, bukan deployment production langsung.

Jika ingin menuju production:
1. Tambah validasi SLA gate otomatis.
2. Tambah rollback policy otomatis jika performa jatuh.
3. Simpan versi model dan metadata eksperimen per run.
4. Tambah CI pipeline untuk benchmark regression.
