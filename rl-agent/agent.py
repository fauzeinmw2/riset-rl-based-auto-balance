import time
import json
import random
import math
import requests
import docker
import os

# ================= CONFIG =================
PROM_URL = "http://prometheus:9090/api/v1/query"
CONTAINER_NAME = "api-rl"
QTABLE_FILE = "q_table.json"

INTERVAL = 5 # Dipercepat menjadi 5 detik agar lebih responsif terhadap JMeter
COOLDOWN = 5 # Menyesuaikan dengan interval

ALPHA = 0.1
GAMMA = 0.9
EPSILON = 0.2

CPU_PERIOD = 100000

# Limit Default Awal
CURRENT_CPU_LIMIT = 1.0
CURRENT_MEM_LIMIT = 512

# Batasan Resource (Diperlebar untuk menampung JMeter)
MIN_CPU = 0.05
MAX_CPU = 1.0  # Jangan melebihi 1.0 karena cpuset kita cuma kasih 1 core
MIN_MEM = 128  # NodeJS butuh minimal ~60-80MB untuk idle
MAX_MEM = 512  # Samakan dengan api-baseline agar adil saat beban puncak

LAST_ACTION_TIME = 0

# ================= ACTION SPACE =================
# Ditambahkan action "Big Jump" untuk merespon lonjakan beban dengan cepat
ACTIONS = {
    0: {"cpu": 0.0,   "mem": 0},
    
    # Fine-tuning (Kecil)
    1: {"cpu": 0.05,  "mem": 0},
    2: {"cpu": -0.05, "mem": 0},
    3: {"cpu": 0,     "mem": 16},
    4: {"cpu": 0,     "mem": -16},
    
    # Kombinasi (Kecil)
    5: {"cpu": 0.05,  "mem": 16},
    6: {"cpu": -0.05, "mem": -16},
    
    # Big Jumps (Darurat/Beban Tinggi)
    7: {"cpu": 0.2,   "mem": 64},
    8: {"cpu": -0.2,  "mem": -64},
}

# ================= DOCKER =================
docker_client = docker.from_env()
try:
    container = docker_client.containers.get(CONTAINER_NAME)
except docker.errors.NotFound:
    print(f"❌ Error: Kontainer {CONTAINER_NAME} tidak ditemukan!")
    exit(1)

# ================= SAFE LOAD QTABLE =================
if os.path.exists(QTABLE_FILE) and os.path.getsize(QTABLE_FILE) > 0:
    try:
        with open(QTABLE_FILE) as f:
            Q = json.load(f)
    except:
        Q = {}
else:
    Q = {}

# ================= PROMETHEUS =================
def query(q):
    try:
        r = requests.get(PROM_URL, params={"query": q}, timeout=2)
        res = r.json().get("data", {}).get("result", [])
        return float(res[0]["value"][1]) if res else 0.0
    except:
        return 0.0

def metrics():
    # CPU Core usage
    # menyimpan total waktu CPU yang dipakai container sejak container start (dalam satuan detik CPU).
    # menghitung rata-rata berapa CPU yang digunakan per detik dalam 15 detik terakhir
    cpu_core = query('rate(container_cpu_usage_seconds_total{name="api-rl"}[15s])')

    # Mengubah penggunaan CPU (dalam core) menjadi persentase terhadap limit CPU container.
    cpu_util = (cpu_core / CURRENT_CPU_LIMIT) * 100 if CURRENT_CPU_LIMIT else 0

    # Memory (RAM) Usage
    # mengambil jumlah memori yang benar-benar digunakan oleh container api-rl lalu mengubahnya ke satuan MB.
    mem = query('container_memory_working_set_bytes{name="api-rl"}') / 1024 / 1024

    # Response Time rata-rata (ms)
    # Average Response Time = Total Request Time / Total Request Count
    rt = query(
        'sum(rate(http_request_duration_seconds_sum{job="api-rl"}[1m]))'
        ' / clamp_min(sum(rate(http_request_duration_seconds_count{job="api-rl"}[1m])), 0.0001)'
    ) * 1000

    # Jika kontainer mati atau tidak ada request, anggap RT aman (idle)
    if rt <= 0 or math.isnan(rt):
        rt = 10.0

    return cpu_util, mem, rt

# ================= STATE =================
def state(cpu, mem, rt):
    # Disederhanakan
    cpu_ratio = cpu / 100
    mem_ratio = mem / CURRENT_MEM_LIMIT if CURRENT_MEM_LIMIT > 0 else 0

    # Simplifikasi bucket state agar Q-Table lebih cepat konvergen
    cpu_bin = min(int(cpu_ratio * 4), 3) # 0-3
    mem_bin = min(int(mem_ratio * 4), 3) # 0-3
    rt_bin = min(int(rt // 100), 5)      # Setiap 100ms masuk bucket baru, max 5

    return f"{cpu_bin}|{mem_bin}|{rt_bin}"

# ================= REWARD =================
def reward(cpu_util, mem_util, rt):
    # Penalti RT lebih agresif jika > 500ms
    perf_penalty = (rt / 100) ** 2
    if rt > 500:
        perf_penalty *= 2 

    # Penalti Energi berdasarkan limit
    cpu_energy_penalty = CURRENT_CPU_LIMIT * 3 # Sedikit dikurangi bobotnya
    mem_energy_penalty = (CURRENT_MEM_LIMIT / 128) * 1

    return -(perf_penalty + cpu_energy_penalty + mem_energy_penalty)

# ================= RL =================
def choose(s):
    Q.setdefault(s, {})
    # Eksplorasi atau jika state belum pernah dikunjungi
    if random.random() < EPSILON or not Q[s]:
        return random.choice(list(ACTIONS.keys()))
    # Eksploitasi
    return int(max(Q[s], key=Q[s].get))

def update_q(s, a, r, ns):
    Q.setdefault(s, {})
    Q.setdefault(ns, {})
    a = str(a)

    if a not in Q[s]:
        Q[s][a] = 0.0

    old = Q[s][a]
    future = max(Q[ns].values()) if Q[ns] else 0
    new_value = old + ALPHA * (r + GAMMA * future - old)
    Q[s][a] = float(new_value)

def save():
    try:
        with open(QTABLE_FILE, "w") as f:
            json.dump(Q, f, indent=2)
    except Exception as e:
        print("⚠️ Save error:", e)


# ================= DOCKER CONTROL =================
# Parameter:
# - a: index action dari ACTIONS RL
# - force_cpu: jika ingin override langsung limit CPU (untuk emergency scaling)
# - force_mem: jika ingin override langsung limit Memori (untuk emergency scaling)
def apply_action(a=None, force_cpu=None, force_mem=None):

    # boleh mengubah variabel global untuk menyimpan state limit saat ini
    global CURRENT_CPU_LIMIT, CURRENT_MEM_LIMIT

    if a is not None:
        action = ACTIONS[int(a)]
        CURRENT_CPU_LIMIT += action["cpu"]
        CURRENT_MEM_LIMIT += action["mem"]
    
    if force_cpu is not None: CURRENT_CPU_LIMIT = force_cpu
    if force_mem is not None: CURRENT_MEM_LIMIT = force_mem

    # Enforcement batasan absolut.
    # memastikan CPU RAM tidak melewati batas minimum dan maksimum.
    CURRENT_CPU_LIMIT = max(MIN_CPU, min(MAX_CPU, round(CURRENT_CPU_LIMIT, 2)))
    CURRENT_MEM_LIMIT = max(MIN_MEM, min(MAX_MEM, int(CURRENT_MEM_LIMIT)))

    # mb to bytes RAM untuk Docker
    mem_bytes_val = CURRENT_MEM_LIMIT * 1024 * 1024

    try:
        container.update(
            cpu_period=CPU_PERIOD,
            cpu_quota=int(CURRENT_CPU_LIMIT * CPU_PERIOD),
            mem_limit=mem_bytes_val,
            memswap_limit=mem_bytes_val
        )
        print(f"⚙️ Applied -> CPU={CURRENT_CPU_LIMIT:.2f} | MEM={CURRENT_MEM_LIMIT}MB")
    except Exception as e:
        print("⚠️ Docker update failed:", e)

# ================= MAIN LOOP =================
print("🚀 RL agent started...\n")

while True:
    cpu, mem, rt = metrics() # %, mb, ms
    s = state(cpu, mem, rt) # "cpu_bin|mem_bin|rt_bin"

    now = time.time()
    local_time = time.localtime(now)
    print(f"\n========== ⏰ Time: {time.strftime('%H:%M:%S', local_time)} ==========")
    print(f"Current State: {s} | CPU={cpu:.1f}% MEM={mem:.0f}MB | RT={rt:.1f}ms")
    
    # 🚨 EMERGENCY SAFEGUARD (Prioritas tertinggi, di luar logika RL)
    # Jika memori menyentuh 80% dari limit, kontainer di ambang OOM Kills!
    if mem > (CURRENT_MEM_LIMIT * 0.80):
        print("🚨 BAHAYA OOM DETEKSI! Melakukan Emergency Scaling UP Memori...")
        apply_action(force_mem=CURRENT_MEM_LIMIT + 128)
        LAST_ACTION_TIME = now # Reset cooldown
        time.sleep(INTERVAL)
        continue # Skip RL action untuk siklus ini

    # RL Action Selection
    if now - LAST_ACTION_TIME > COOLDOWN:
        a = choose(s)
        apply_action(a=a)
        LAST_ACTION_TIME = now
    else:
        a = 0 # Idle action

    time.sleep(INTERVAL)

    # Observasi State Baru
    cpu2, mem2, rt2 = metrics() # %, mb, ms
    ns = state(cpu2, mem2, rt2) # "cpu_bin|mem_bin|rt_bin"
    r = reward(cpu2, mem2, rt2) #-xxxx

    update_q(s, a, r, ns)
    
    print(
        f"📊 State={s} | Action={a} | "
        f"Usage: CPU={cpu2:.1f}% MEM={mem2:.0f}MB RT={rt2:.1f}ms | "
        f"Limits: {CURRENT_CPU_LIMIT:.2f} CPU, {CURRENT_MEM_LIMIT}MB | "
        f"Reward={r:.2f}"
    )

    print("========== END OF CYCLE ==========\n")

    EPSILON = max(0.05, EPSILON * 0.995) # Biarkan agen tetap eksplor minimal 5%
    save()