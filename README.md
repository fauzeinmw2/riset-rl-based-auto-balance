# RL-Based Auto Balance

Project ini adalah prototipe sistem auto-balancing berbasis Reinforcement Learning (RL) untuk mengatur alokasi resource container secara dinamis. Fokus utamanya adalah membandingkan layanan baseline dengan layanan yang resource-nya dapat disesuaikan otomatis oleh agen RL berdasarkan metrik performa runtime.

## Deskripsi Project

Sistem ini terdiri dari beberapa komponen utama:

- `api/`: layanan backend berbasis Node.js dan Express yang mensimulasikan API sistem KRS.
- `rl-agent/`: agen RL berbasis Python yang membaca metrik dari Prometheus lalu mengubah limit CPU dan memori container `api-rl`.
- `postgres/`: inisialisasi database PostgreSQL.
- `prometheus/`: konfigurasi monitoring untuk mengumpulkan metrik dari API dan cAdvisor.
- `docker-compose.yml`: orkestrasi seluruh service eksperimen.

Secara konsep, arsitektur project ini memisahkan dua service API:

- `api-baseline`: service pembanding dengan resource statis.
- `api-rl`: service yang resource CPU dan memorinya disesuaikan oleh agen RL.

Agen RL memantau:

- penggunaan CPU container,
- penggunaan memori container,
- response time API.

Berdasarkan kondisi tersebut, agen memilih aksi untuk menaikkan, menurunkan, atau mempertahankan limit resource agar performa tetap baik dengan penggunaan resource yang efisien.

## Tujuan Project

Project ini bertujuan untuk:

- membangun simulasi auto-scaling atau auto-balancing berbasis RL pada environment container,
- membandingkan performa service baseline dan service yang dikontrol RL,
- mengevaluasi trade-off antara kualitas layanan dan efisiensi resource,
- menyediakan lingkungan eksperimen yang bisa dipakai untuk penelitian, pengujian, dan analisis monitoring.

## Alur Kerja Sistem

1. Client mengirim request ke API.
2. API mengekspor metrik response time ke endpoint `/metrics`.
3. Prometheus mengumpulkan metrik dari API dan cAdvisor.
4. Agen RL membaca metrik dari Prometheus.
5. Agen RL menghitung state, memilih action, lalu memperbarui limit CPU dan memori container `api-rl`.
6. Q-table agen disimpan di `rl-agent/q_table.json` agar pembelajaran dapat dilanjutkan.

## Endpoint API

Beberapa endpoint utama pada service API:

- `GET /api/courses`: menampilkan daftar mata kuliah dan kapasitas kelas.
- `POST /api/register`: melakukan pendaftaran kelas dengan validasi kapasitas, konflik jadwal, dan prasyarat.
- `GET /api/student/:id/schedule`: menampilkan jadwal mahasiswa.
- `GET /metrics`: endpoint metrik Prometheus.

## Teknologi yang Digunakan

- Node.js + Express
- PostgreSQL
- Python
- Docker dan Docker Compose
- Prometheus
- cAdvisor
- Grafana

## Cara Menggunakan Project

### 1. Prasyarat

Pastikan environment Anda memiliki:

- Docker
- Docker Compose

Jika ingin mengembangkan tiap service secara manual di luar Docker, siapkan juga:

- Node.js 18+
- Python 3.10+

### 2. Cek Kondisi Repository

Sebelum menjalankan project, perhatikan kondisi repository saat ini:

- `docker-compose.yml` menggunakan build context `./api-go` untuk `api-baseline` dan `api-rl`.
- Source API yang tersedia di repo saat ini berada di folder `api/`.
- File `postgres/init.sql` masih kosong, sehingga schema database belum otomatis dibuat dari repo ini.

Artinya, agar sistem dapat berjalan penuh secara end-to-end, Anda perlu melakukan salah satu langkah berikut:

- memindahkan atau menyalin source API ke folder `api-go`, atau
- mengubah `docker-compose.yml` agar build menggunakan folder `./api`.

Selain itu, Anda juga perlu mengisi `postgres/init.sql` dengan schema tabel dan data awal yang dibutuhkan API.

### 3. Menjalankan dengan Docker Compose

Setelah struktur project sudah disesuaikan, jalankan:

```bash
docker compose up --build
```

Atau jika environment Anda masih memakai format lama:

```bash
docker-compose up --build
```

### 4. Akses Service

Secara default, service akan tersedia pada port berikut:

- API baseline: `http://localhost:3000`
- Grafana: `http://localhost:3001`
- API RL: `http://localhost:3002`
- PostgreSQL: `localhost:5433`
- Prometheus: `http://localhost:9090`
- cAdvisor: `http://localhost:8080`

### 5. Menguji API

Contoh request:

```bash
curl http://localhost:3000/api/courses
```

```bash
curl http://localhost:3002/api/courses
```

Contoh register kelas:

```bash
curl -X POST http://localhost:3002/api/register \
  -H "Content-Type: application/json" \
  -d '{"student_id":1,"class_id":1}'
```

Melalui pengujian ini, Anda dapat membandingkan perilaku `api-baseline` dan `api-rl`.

### 6. Monitoring

Gunakan monitoring berikut selama eksperimen:

- Prometheus untuk melihat metrik mentah scraping.
- Grafana untuk membuat dashboard visualisasi.
- cAdvisor untuk memantau penggunaan resource container.

Service API juga menyediakan endpoint:

```bash
http://localhost:3000/metrics
http://localhost:3002/metrics
```

### 7. Agen RL

Service `rl-agent` berjalan sebagai loop periodik yang:

- mengambil metrik CPU, memori, dan response time,
- membentuk state diskrit,
- memilih action dari action space,
- mengubah limit resource container `api-rl`,
- memperbarui nilai Q-table.

File pembelajaran agen tersimpan di:

- `rl-agent/q_table.json`

### 8. Menghentikan Service

Untuk menghentikan seluruh container:

```bash
docker compose down
```

Jika ingin ikut menghapus volume:

```bash
docker compose down -v
```

## Struktur Folder

```text
.
├── api/
├── api-go/
├── postgres/
├── prometheus/
├── rl-agent/
└── docker-compose.yml
```

## Catatan Penting

- Repository ini sudah memuat agen RL dan service API, tetapi masih ada bagian setup yang perlu dilengkapi agar eksperimen berjalan penuh.
- `api-go/` belum berisi source API yang dapat dibangun, sementara `docker-compose.yml` masih merujuk ke folder tersebut.
- `postgres/init.sql` masih kosong, sehingga database belum memiliki schema bawaan.
- Konfigurasi Prometheus saat ini juga memuat target `focus-exporter:9105`, tetapi service tersebut tidak ada di `docker-compose.yml`.

## Saran Pengembangan Lanjutan

- menambahkan schema dan seed database pada `postgres/init.sql`,
- menyelaraskan folder build API di `docker-compose.yml`,
- menambahkan dashboard Grafana siap pakai,
- menambahkan skenario load testing, misalnya dengan JMeter,
- menambahkan evaluasi hasil eksperimen antara baseline dan RL.

## Pengujian Spike Test dengan K6

Repository ini sekarang juga memuat skenario spike test K6 di folder `tests/k6/`.

File yang tersedia:

- `tests/k6/spike-test.js`: script K6 untuk mengirim request acak ke endpoint API.
- `tests/k6/run-spike-comparison.mjs`: runner untuk menjalankan pengujian secara bergantian, dimulai dari `api-baseline` lalu `api-rl`, dengan pola spike yang identik.
- `tests/k6/format-spike-report.mjs`: formatter untuk menampilkan resume hasil pengujian.

### Pola Spike

Runner akan memilih salah satu pola berikut:

- `low -> high -> medium`
- `high -> low -> medium`

Setiap pengujian dijalankan selama 10 menit secara default, dan kedua service memakai pola yang sama agar perbandingan tetap adil.

### Menjalankan Pengujian

Pastikan service berikut sudah aktif:

- `api-baseline`
- `api-rl`
- `prometheus`
- `cadvisor`

Lalu jalankan:

```bash
node tests/k6/run-spike-comparison.mjs
```

Jika ingin menentukan pola spike secara manual:

```bash
SPIKE_PATTERN=high-low-medium node tests/k6/run-spike-comparison.mjs
```

Jika URL service berbeda dari default:

```bash
BASELINE_URL=http://localhost:3000 \
RL_URL=http://localhost:3002 \
PROMETHEUS_URL=http://localhost:9090 \
node tests/k6/run-spike-comparison.mjs
```

### Output Hasil

Hasil pengujian akan disimpan di:

- `tests/k6/results/comparison-report.txt`
- `tests/k6/results/comparison-report.json`
- `tests/k6/results/api-baseline-summary.json`
- `tests/k6/results/api-rl-summary.json`

Untuk menampilkan ulang resume hasil:

```bash
node tests/k6/format-spike-report.mjs
```

Format output resume:

```text
=== Spike Test Result ===
Durasi Pengujian: 10 Menit
Pola Spike: low -> high -> medium

1. API-Baseline
    - CPU Usage: 10% (0.1 Core)
    - RAM Usage: 100 mb
    - Response Time (Avg): 50ms
    - Request Success: 100 req
    - Request Error: 0 req

2. API-RL
    - CPU Usage: 8% (0.08 Core)
    - RAM Usage: 110 mb
    - Response Time (Avg): 55ms
    - Request Success: 110 req
    - Request Error: 0 req

3. Result
    - Energy Consumption: Hemat 3%
        - CPU Usage turun 2% (0.02 Core)
        - RAM Usage turun 10 mb
    - Performance: Turun 10%
```

## Lisensi

Belum ada lisensi yang didefinisikan pada repository ini.
