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

# INTERVAL = 5 # Dipercepat menjadi 5 detik agar lebih responsif terhadap JMeter
# COOLDOWN = 5 # Menyesuaikan dengan interval

# Diperlambat menjadi 15 detik agar metrik stabil dan mencegah thrashing
INTERVAL = 15 
COOLDOWN = 15

ALPHA = 0.1
GAMMA = 0.9
EPSILON = 0.2

CPU_PERIOD = 100000

# Batas aman agar container Node.js tidak gampang OOM / starvation saat spike
SAFE_MIN_CPU = 0.25
SAFE_MIN_MEM = 256
SAFE_SCALE_DOWN_RT_MS = 120
SAFE_SCALE_DOWN_CPU_PERCENT = 35
SAFE_SCALE_DOWN_MEM_RATIO = 0.50

# Limit Default Awal (RAM diset maksimum untuk FASE 1 agar V8 Node.js stabil)
CURRENT_CPU_LIMIT = 1.0
CURRENT_MEM_LIMIT = 512

APPLIED_CPU_LIMIT = CURRENT_CPU_LIMIT
APPLIED_MEM_LIMIT = CURRENT_MEM_LIMIT

# Batasan Resource (Diperlebar untuk menampung JMeter)
MIN_CPU = SAFE_MIN_CPU
MAX_CPU = 1.0  # Jangan melebihi 1.0 karena cpuset kita cuma kasih 1 core
MIN_MEM = SAFE_MIN_MEM
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


def refresh_container():
    global container
    try:
        container = docker_client.containers.get(CONTAINER_NAME)
        return container
    except docker.errors.NotFound:
        return None

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
def sanitize_metric(value, default=0.0, minimum=0.0):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default

    if math.isnan(numeric) or math.isinf(numeric):
        return default

    return max(minimum, numeric)

def query(q):
    try:
        r = requests.get(PROM_URL, params={"query": q}, timeout=2)
        res = r.json().get("data", {}).get("result", [])
        return sanitize_metric(res[0]["value"][1]) if res else 0.0
    except:
        return 0.0

def metrics():
    try:
        # 1. Query Response Time (Rata-rata dalam 1 menit terakhir)
        # Go menggunakan suffix _sum dan _count untuk Histogram
        query_rt = 'sum(rate(http_request_duration_seconds_sum{service="api-rl"}[1m])) / sum(rate(http_request_duration_seconds_count{service="api-rl"}[1m])) * 1000'
        
        # 2. Query CPU & RAM (Tetap sama menggunakan cAdvisor)
        query_cpu = 'rate(container_cpu_usage_seconds_total{name="api-rl"}[1m]) * 100'
        query_mem = 'container_memory_usage_bytes{name="api-rl"} / 1024 / 1024'

        res_rt = requests.get(PROM_URL, params={'query': query_rt}).json()
        res_cpu = requests.get(PROM_URL, params={'query': query_cpu}).json()
        res_mem = requests.get(PROM_URL, params={'query': query_mem}).json()

        rt = sanitize_metric(
            res_rt['data']['result'][0]['value'][1] if res_rt['data']['result'] else 0.0
        )
        cpu = sanitize_metric(
            res_cpu['data']['result'][0]['value'][1] if res_cpu['data']['result'] else 0.0
        )
        mem = sanitize_metric(
            res_mem['data']['result'][0]['value'][1] if res_mem['data']['result'] else 0.0
        )

        return cpu, mem, rt
    except Exception as e:
        print(f"Error metrics: {e}")
        return 0.0, 0.0, 0.0

def reward(cpu_util, mem_util, rt):
    # NORMALISASI: Agar angka RT dan Energy memiliki skala yang sama (0-1)
    # Target RT ideal = 100ms. Jika 200ms, penalty = 2.0
    rt_penalty = rt / 100.0 
    
    # Energy penalty (CPU dlm %, RAM dlm MB)
    # Semakin besar pemakaian, semakin besar penalty
    energy_penalty = (cpu_util / 100.0) + (mem_util / 512.0)
    
    # Balance: Berikan bobot yang sama antara performa dan energi
    # Reward adalah negatif dari total penalty
    return -(0.5 * rt_penalty + 0.5 * energy_penalty)

# ================= STATE =================
def state(cpu, mem, rt):
    cpu = sanitize_metric(cpu)
    mem = sanitize_metric(mem)
    rt = sanitize_metric(rt)

    # Disederhanakan
    cpu_ratio = cpu / 100
    mem_ratio = mem / CURRENT_MEM_LIMIT if CURRENT_MEM_LIMIT > 0 else 0

    # Simplifikasi bucket state agar Q-Table lebih cepat konvergen
    cpu_bin = max(0, min(int(cpu_ratio * 4), 3)) # 0-3
    mem_bin = max(0, min(int(mem_ratio * 4), 3)) # 0-3
    rt_bin = max(0, min(int(rt // 100), 5))      # Setiap 100ms masuk bucket baru, max 5

    return f"{cpu_bin}|{mem_bin}|{rt_bin}"

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


def can_scale_down(cpu, mem, rt):
    cpu = sanitize_metric(cpu)
    mem = sanitize_metric(mem)
    rt = sanitize_metric(rt)

    mem_ratio = mem / CURRENT_MEM_LIMIT if CURRENT_MEM_LIMIT > 0 else 1.0
    return (
        cpu <= SAFE_SCALE_DOWN_CPU_PERCENT
        and mem_ratio <= SAFE_SCALE_DOWN_MEM_RATIO
        and rt <= SAFE_SCALE_DOWN_RT_MS
    )


# ================= DOCKER CONTROL (UPDATED)=================
# Parameter:
# - a: index action dari ACTIONS RL
# - force_cpu: jika ingin override langsung limit CPU (untuk emergency scaling)
# - force_mem: jika ingin override langsung limit Memori (untuk emergency scaling)
def apply_action(a=None, force_cpu=None, force_mem=None):
    global CURRENT_CPU_LIMIT, CURRENT_MEM_LIMIT
    global APPLIED_CPU_LIMIT, APPLIED_MEM_LIMIT

    action = None
    if a is not None:
        action = ACTIONS[int(a)]
        next_cpu_limit = CURRENT_CPU_LIMIT + action["cpu"]
        next_mem_limit = CURRENT_MEM_LIMIT + action["mem"]
        CURRENT_CPU_LIMIT = next_cpu_limit
        CURRENT_MEM_LIMIT = next_mem_limit
        
    if force_cpu is not None: CURRENT_CPU_LIMIT = force_cpu
    if force_mem is not None: CURRENT_MEM_LIMIT = force_mem

    # Enforcement batasan absolut dengan minimum aman untuk workload Node.js
    CURRENT_CPU_LIMIT = max(MIN_CPU, min(MAX_CPU, round(CURRENT_CPU_LIMIT, 2)))
    CURRENT_MEM_LIMIT = max(MIN_MEM, min(MAX_MEM, int(CURRENT_MEM_LIMIT)))

    cpu_diff = abs(CURRENT_CPU_LIMIT - APPLIED_CPU_LIMIT)
    mem_diff = abs(CURRENT_MEM_LIMIT - APPLIED_MEM_LIMIT)

    is_emergency = (force_mem is not None) or (force_cpu is not None)
    
    # Threshold diubah menjadi 16MB agar fine-tuning agen tereksekusi
    if cpu_diff >= 0.1 or mem_diff >= 16 or is_emergency:
        mem_bytes_val = int(CURRENT_MEM_LIMIT * 1024 * 1024)
        try:
            live_container = refresh_container()
            if live_container is None:
                print(f"⚠️ Container {CONTAINER_NAME} tidak ditemukan saat apply_action.")
                return

            live_container.update(
                cpu_period=CPU_PERIOD,
                cpu_quota=int(CURRENT_CPU_LIMIT * CPU_PERIOD),
                mem_limit=mem_bytes_val,
                memswap_limit=mem_bytes_val
            )
            print(f"⚙️ Applied to Docker -> CPU={CURRENT_CPU_LIMIT:.2f} | MEM={CURRENT_MEM_LIMIT}MB")
            
            APPLIED_CPU_LIMIT = CURRENT_CPU_LIMIT
            APPLIED_MEM_LIMIT = CURRENT_MEM_LIMIT
        except Exception as e:
            print("⚠️ Docker update failed:", e)
    else:
        print(f"⏩ Skipped Docker Update (Delta terlalu kecil) -> Target: CPU={CURRENT_CPU_LIMIT:.2f} | MEM={CURRENT_MEM_LIMIT}MB")

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

        # Jangan turunkan resource saat service belum benar-benar stabil.
        if ACTIONS[a]["cpu"] < 0 or ACTIONS[a]["mem"] < 0:
            if not can_scale_down(cpu, mem, rt):
                print("🛡️ Scale-down diblokir untuk menjaga stabilitas container.")
                a = 0

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
