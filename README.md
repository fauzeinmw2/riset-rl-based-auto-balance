# RL-Based Auto Resource Balancing for Containerized API

## 1. Ringkasan Proyek Riset

Proyek ini adalah eksperimen **Reinforcement Learning (Q-Learning)** untuk mengatur resource container Docker secara otomatis (CPU dan RAM) pada layanan API.

Tujuan utamanya:
- Menurunkan konsumsi resource (proxy energi) pada service RL-controlled.
- Tetap menjaga kualitas layanan (latency, success rate, error rate).
- Membandingkan hasil RL vs baseline statis dalam skenario load spike realistis.

Baseline (`api-baseline`) dijalankan dengan limit statis.
RL-controlled (`api-rl`) dijalankan dengan limit yang diubah berkala oleh agent.

---

## 2. Apa yang Diteliti

Hipotesis riset:
1. Agent RL dapat menemukan konfigurasi CPU/RAM yang lebih efisien dibanding baseline statis.
2. Penghematan resource dapat dicapai tanpa menurunkan performa secara signifikan.
3. Ada trade-off antara efisiensi dan reliability, sehingga evaluasi harus multi-metrik.

Metrik utama:
- `CPU avg %`
- `RAM avg MB`
- `Energy Proxy` (normalisasi CPU + RAM)
- `Latency avg/p95/p99`
- `Success rate`
- `Error rate`

---

## 3. Teknologi yang Digunakan

### Runtime & Services
- Docker + Docker Compose
- Node.js (Express) untuk API
- PostgreSQL untuk data workload
- Prometheus untuk scraping metrics
- cAdvisor untuk container metrics
- Grafana untuk visualisasi
- K6 untuk spike load testing

### Agent & Tools
- Python 3 (Q-Learning agent)
- Docker SDK for Python
- Requests
- NumPy
- Utility script:
  - `tools/train_agent.py` untuk training terstruktur multi-seed
  - `tools/compare_results.py` untuk komparasi baseline vs RL

---

## 4. Arsitektur Singkat

Service utama:
- `api-baseline`: API Node.js limit statis (kontrol pembanding)
- `api-rl`: API Node.js yang limit-nya bisa diubah agent
- `rl-agent`: proses RL untuk observasi metrics + aksi kontrol resource
- `postgres`: database seeded untuk endpoint read/write
- `prometheus` + `cadvisor`: observability
- `k6`: beban spike terstruktur

Aliran data:
1. K6 mengirim request ke baseline dan RL API.
2. Prometheus mengumpulkan metrik API + container.
3. RL agent membaca metrik dari Prometheus.
4. RL agent memilih aksi (ubah CPU/RAM `api-rl`).
5. Hasil aksi dinilai melalui reward dan disimpan ke Q-table.
6. Setelah test selesai, script komparasi menghitung hasil akhir.

---

## 5. Struktur Folder

```text
rl-based-auto-balance/
├── api/                       # Node.js API (baseline + rl)
├── api-go/                    # Optional service pembanding sekunder
├── postgres/
│   └── init.sql               # Schema + seed data
├── prometheus/
│   └── prometheus.yml
├── rl-agent/
│   ├── agent.py               # RL agent utama
│   ├── requirements.txt
│   ├── q_table.json           # Policy (runtime default)
│   └── logs/                  # Log agent + training/eval artifacts
├── k6/
│   └── spike-test.js          # Skenario load spike
├── tools/
│   ├── train_agent.py         # Orkestrasi training multi-seed
│   └── compare_results.py     # Evaluasi hasil eksperimen
├── docker-compose.yml
├── QUICKSTART.md
└── README.md
```

---

## 6. Prasyarat

Pastikan tersedia:
- Docker Engine (20+)
- Docker Compose plugin (`docker compose`)
- Python 3.8+

Cek cepat:

```bash
docker --version
docker compose version
python3 --version
```

---

## 7. Langkah Lengkap Eksperimen (End-to-End)

## 7.1 Clone Project

```bash
git clone <URL_REPO_ANDA>
cd rl-based-auto-balance
```

Jika project sudah ada lokal, cukup masuk ke folder root.

## 7.2 Build dan Start Infrastruktur

```bash
docker compose build
docker compose up -d postgres cadvisor prometheus grafana api-baseline api-rl
```

Validasi service:

```bash
docker compose ps
curl http://localhost:3000/health
curl http://localhost:3002/health
```

Port penting:
- Baseline API: `localhost:3000`
- RL API: `localhost:3002`
- PostgreSQL: `localhost:5433`
- Prometheus: `localhost:9090`
- Grafana: `localhost:3001` (admin/admin)

## 7.3 Training Terstruktur (Multi-Seed)

Tujuan training terstruktur:
- Menghasilkan policy yang lebih stabil dibanding satu run tunggal.
- Mengevaluasi variasi hasil antar seed.

Perintah contoh:

```bash
python3 tools/train_agent.py 15 3
```

Arti parameter:
- `15`: episode per seed
- `3`: jumlah seed

Output training:
- `training_results/training_summary.json`
- `rl-agent/logs/training/seed_1/...`
- `rl-agent/logs/training/seed_2/...`
- `rl-agent/logs/training/seed_3/...`

Isi per seed:
- `q_table.json`
- `decisions.csv`
- `agent.log`

## 7.4 Pilih Policy Terbaik

Lihat ringkasan:

```bash
cat training_results/training_summary.json
```

Pilih seed terbaik berdasarkan:
- reward mean lebih baik
- penalti reliability lebih kecil
- action profile tidak terlalu agresif scale-up

Contoh policy yang dipakai:
- `rl-agent/logs/training/seed_3/q_table.json`

## 7.5 Jalankan Agent Real (Mode Eval)

Matikan agent lama (jika ada):

```bash
docker compose stop rl-agent
```

Nyalakan agent mode evaluasi dengan policy terpilih:

```bash
docker compose run --rm -d \
  -e AGENT_LOG_FILE=/app/logs/eval/agent.log \
  rl-agent \
  python agent.py \
  --mode eval \
  --qtable-file /app/logs/training/seed_3/q_table.json \
  --decision-log /app/logs/eval/decisions.csv
```

Validasi log eval:

```bash
tail -f rl-agent/logs/eval/agent.log
```

Yang perlu terlihat:
- `Loaded Q-table with ... states`
- tidak ada error `Docker update failed`
- aksi agent dan limit ter-apply

## 7.6 Jalankan Spike Test Real

Sebelum test, simpan timestamp agar window evaluasi presisi:

```bash
date +%s > .eval_start_epoch
docker compose --profile load-test run --rm k6
date +%s > .eval_end_epoch
```

K6 akan menjalankan profil spike bertahap hingga peak VU.

## 7.7 Evaluasi Hasil Eksperimen

Gunakan window waktu exact dari test:

```bash
START=$(cat .eval_start_epoch)
END=$(cat .eval_end_epoch)
python3 tools/compare_results.py "$START" "$END"
```

Output utama:
- ringkasan tabel di terminal
- file `metrics_comparison.json`

Interpretasi cepat:
- `cpu_reduction_% > 0` -> RL lebih hemat CPU
- `mem_reduction_% > 0` -> RL lebih hemat RAM
- `energy_reduction_% > 0` -> RL lebih efisien energi (proxy)
- `latency_change_% <= 0` -> RL lebih cepat/sama
- `success_rate_improvement_% >= 0` dan `error_rate_reduction_% >= 0` -> reliability tidak memburuk

---

## 8. Cara Kerja Agent (Detail)

Agent bekerja dalam loop episode:

1. **Observe**
   - Ambil metrics dari Prometheus untuk `api-rl` dan baseline.

2. **State Encoding**
   - Metrics kontinu (CPU, mem, latency, error) didiskretkan ke state bin.

3. **Action Selection**
   - Train mode: epsilon-greedy (eksplorasi + eksploitasi).
   - Eval mode: epsilon ~ 0 (lebih deterministik).
   - Action mask membatasi aksi agar tidak overprovision saat kondisi sehat.

4. **Apply Action**
   - Ubah limit CPU/RAM container `api-rl` via Docker API.
   - Reconnect container handle otomatis jika container direcreate.

5. **Reward Calculation**
   - Penalti penggunaan energi (CPU+RAM normalized)
   - Penalti latency/error
   - Penalti headroom berlebih (overprovision)
   - Bonus efisiensi vs baseline rolling average

6. **Learning Update (train mode)**
   - Update Q-value: `Q(s,a) <- Q(s,a) + alpha * (r + gamma * maxQ(s') - Q(s,a))`
   - Simpan Q-table periodik dengan fallback write aman untuk bind mount.

7. **Logging**
   - Simpan keputusan episode ke CSV agar bisa dianalisis offline.

---

## 9. Mode Operasi Agent

### Train Mode
Digunakan untuk belajar policy baru.

Contoh:

```bash
python3 rl-agent/agent.py \
  --mode train \
  --episodes 100 \
  --seed 1 \
  --reset-q-table
```

### Eval Mode
Digunakan untuk eksperimen fair/comparison.

Contoh:

```bash
python3 rl-agent/agent.py \
  --mode eval \
  --qtable-file rl-agent/logs/training/seed_3/q_table.json
```

---

## 10. Protokol Eksperimen yang Direkomendasikan

Untuk hasil yang lebih reliabel (bukan one-off):
1. Jalankan training multi-seed.
2. Pilih policy terbaik.
3. Jalankan spike test real minimal 3 kali.
4. Hitung median hasil tiap metrik.
5. Bandingkan dengan baseline di window waktu yang sama.

---

## 11. Troubleshooting

### A. Error Docker update di agent
Gejala:
- `Docker update failed`
- `No such container`

Cek:
```bash
tail -f rl-agent/logs/eval/agent.log
docker ps
```

Pastikan `api-rl` berjalan dan agent log menunjukkan reconnect sukses.

### B. Q-table tidak tersimpan
Gejala:
- file Q-table tidak berubah

Cek path output yang dipakai (`--qtable-file`) dan izin mount volume.

### C. Result aneh karena window waktu salah
Selalu pakai start-end timestamp yang dicatat sebelum dan sesudah spike test.

### D. Success rate rendah
Pada workload write-heavy (`/api/register`), error business logic (kelas penuh, konflik jadwal) bisa valid terjadi. Pisahkan error sistem vs error domain jika butuh analisis lebih dalam.

---

## 12. Catatan Reproducibility

Agar eksperimen dapat direplikasi:
- lock versi image/dependency
- simpan artifact per run (Q-table, decision log, summary JSON)
- dokumentasikan seed, episode, skenario load, start/end time

---

## 13. Next Research Directions

- Fine-tune reward weights berbasis objective riset.
- Tambahkan ranking `best seed` berbasis composite score (energy + guardrail reliability), bukan hanya jumlah state.
- Bandingkan dengan framework RL siap pakai (contoh PPO di Stable-Baselines3) sebagai benchmark metode.
- Tambahkan prediktor short-horizon (forecast load) agar scaling lebih anticipative.
