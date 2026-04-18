import time
import json
import random
import math
import requests
import docker
import os
import re

# ================= CONFIG =================
PROM_URL = "http://prometheus:9090/api/v1/query"
CONTAINER_NAME = "api-rl"
QTABLE_FILE = "q_table.json"

# INTERVAL = 5 # Dipercepat menjadi 5 detik agar lebih responsif terhadap JMeter
# COOLDOWN = 5 # Menyesuaikan dengan interval

# Dipercepat agar agent lebih responsif terhadap lonjakan latency singkat.
INTERVAL = 5
COOLDOWN = 5

ALPHA = 0.1
GAMMA = 0.9
EPSILON = 0.2

CPU_PERIOD = 100000
SLA_TARGET_MS = 100.0

# Limit Default Awal (RAM diset maksimum untuk FASE 1 agar V8 Node.js stabil)
CURRENT_CPU_LIMIT = 1.0
CURRENT_MEM_LIMIT = 512

APPLIED_CPU_LIMIT = CURRENT_CPU_LIMIT
APPLIED_MEM_LIMIT = CURRENT_MEM_LIMIT

# Batasan Resource (Diperlebar untuk menampung JMeter)
MIN_CPU = 0.05
MAX_CPU = 1.0  # Jangan melebihi 1.0 karena cpuset kita cuma kasih 1 core
MIN_MEM = 50  
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

STATE_KEY_PATTERN = re.compile(r"^[0-3]\|[0-3]\|[0-5]$")


def sanitize_number(value, default=0.0, min_value=0.0, max_value=None):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default

    if not math.isfinite(v):
        return default

    if min_value is not None and v < min_value:
        v = min_value
    if max_value is not None and v > max_value:
        v = max_value
    return v


def clean_state_actions(action_map):
    if not isinstance(action_map, dict):
        return {}

    cleaned = {}
    for action_key, action_value in action_map.items():
        try:
            action_id = int(action_key)
        except (TypeError, ValueError):
            continue

        if action_id not in ACTIONS:
            continue

        q_val = sanitize_number(action_value, default=0.0, min_value=None, max_value=None)
        cleaned[str(action_id)] = q_val
    return cleaned


def normalize_q_table(q_table):
    if not isinstance(q_table, dict):
        return {}

    normalized = {}
    for state_key, action_map in q_table.items():
        if not isinstance(state_key, str) or not STATE_KEY_PATTERN.match(state_key):
            continue
        normalized[state_key] = clean_state_actions(action_map)
    return normalized

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

Q = normalize_q_table(Q)

# ================= PROMETHEUS =================
def query(q):
    try:
        r = requests.get(PROM_URL, params={"query": q}, timeout=2)
        res = r.json().get("data", {}).get("result", [])
        raw_val = res[0]["value"][1] if res else 0.0
        return sanitize_number(raw_val, default=0.0, min_value=0.0)
    except:
        return 0.0

def metrics():
    try:
        # 1. Query Response Time p95 (lebih representatif untuk SLA/tail latency)
        # Fallback ke rata-rata jika bucket belum terisi.
        query_rt_p95 = 'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{service="api-rl"}[30s]))) * 1000'
        query_rt_avg = 'sum(rate(http_request_duration_seconds_sum{service="api-rl"}[30s])) / sum(rate(http_request_duration_seconds_count{service="api-rl"}[30s])) * 1000'
        
        # 2. Query CPU & RAM (Tetap sama menggunakan cAdvisor)
        query_cpu = 'rate(container_cpu_usage_seconds_total{name="api-rl"}[1m]) * 100'
        query_mem = 'container_memory_usage_bytes{name="api-rl"} / 1024 / 1024'

        rt = query(query_rt_p95)
        if rt <= 0:
            rt = query(query_rt_avg)
        cpu = query(query_cpu)
        mem = query(query_mem)

        return cpu, mem, rt
    except Exception as e:
        print(f"Error metrics: {e}")
        return 0.0, 0.0, 0.0

def reward(cpu_util, mem_util, rt):
    cpu_util = sanitize_number(cpu_util, default=0.0, min_value=0.0)
    mem_util = sanitize_number(mem_util, default=0.0, min_value=0.0)
    rt = sanitize_number(rt, default=0.0, min_value=0.0)

    rt_penalty = rt / SLA_TARGET_MS

    # Energy penalty (CPU dlm %, RAM dlm MB).
    # Dinormalisasi agar tetap comparable dengan penalty latency.
    energy_penalty = (cpu_util / 100.0) + (mem_util / 512.0)

    # Base tradeoff: saat sistem sehat, agen tetap didorong hemat resource
    # tanpa mengorbankan latency.
    base_penalty = (0.35 * rt_penalty) + (0.35 * energy_penalty)

    # SLA-aware shaping:
    # - Jika RT < 100ms, beri bonus kecil agar policy stabil di bawah SLA.
    # - Jika RT > 100ms, penalty tumbuh non-linear supaya performa jadi prioritas.
    if rt <= SLA_TARGET_MS:
        sla_bonus = 0.30 * (1.0 - rt_penalty)
        return sla_bonus - base_penalty

    breach_ratio = (rt - SLA_TARGET_MS) / SLA_TARGET_MS
    sla_penalty = 0.60 + min(3.0, breach_ratio ** 2)
    return -(base_penalty + sla_penalty)

# ================= STATE =================
def state(cpu, mem, rt):
    cpu = sanitize_number(cpu, default=0.0, min_value=0.0)
    mem = sanitize_number(mem, default=0.0, min_value=0.0)
    rt = sanitize_number(rt, default=0.0, min_value=0.0)

    # Disederhanakan
    cpu_ratio = cpu / 100
    mem_ratio = mem / CURRENT_MEM_LIMIT if CURRENT_MEM_LIMIT > 0 else 0

    # Simplifikasi bucket state agar Q-Table lebih cepat konvergen
    cpu_bin = min(int(cpu_ratio * 4), 3) # 0-3
    mem_bin = min(int(mem_ratio * 4), 3) # 0-3
    rt_bin = min(int(rt // 100), 5)      # Setiap 100ms masuk bucket baru, max 5

    return f"{cpu_bin}|{mem_bin}|{rt_bin}"

# ================= RL =================
def choose(s, mem, cpu):
    best_action = 0
    Q[s] = clean_state_actions(Q.get(s, {}))

    # Eksplorasi atau jika state belum pernah dikunjungi
    if random.random() < EPSILON or not Q[s]:
        
        # Jika Mem atau CPU limitnya sangat tinggi
        if (CURRENT_MEM_LIMIT > mem + (mem*0.20)) and (CURRENT_CPU_LIMIT > cpu + (cpu*0.20)):
            print("🚨 Deteksi Boros Limit CPU dan RAM!")
            return random.choice([6,8]) 
        
        elif (CURRENT_MEM_LIMIT < mem - (mem*0.20)):
            print("🚨 Deteksi Boros Limit RAM!")
            return 8

        elif (CURRENT_CPU_LIMIT < cpu - (cpu*0.20)):
            print("🚨 Deteksi Boros Limit CPU!")
            return 2

        else:
            return random.choice(list(ACTIONS.keys()))
        
    # Eksploitasi
    best_action = int(max(Q[s], key=Q[s].get))

    return best_action if best_action in ACTIONS else 0

def update_q(s, a, r, ns):
    Q[s] = clean_state_actions(Q.get(s, {}))
    Q[ns] = clean_state_actions(Q.get(ns, {}))
    a = str(a)

    r = sanitize_number(r, default=0.0, min_value=None, max_value=None)

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


# ================= DOCKER CONTROL (UPDATED)=================
# Parameter:
# - a: index action dari ACTIONS RL
# - force_cpu: jika ingin override langsung limit CPU (untuk emergency scaling)
# - force_mem: jika ingin override langsung limit Memori (untuk emergency scaling)
def apply_action(a=None, force_cpu=None, force_mem=None):
    global CURRENT_CPU_LIMIT, CURRENT_MEM_LIMIT
    global APPLIED_CPU_LIMIT, APPLIED_MEM_LIMIT

    if a is not None:
        action = ACTIONS.get(int(a), ACTIONS[0])
        CURRENT_CPU_LIMIT += action["cpu"]
        # FASE 2 AKTIF: Agen sekarang mengontrol memori
        CURRENT_MEM_LIMIT += action["mem"] 
        
    if force_cpu is not None: CURRENT_CPU_LIMIT = force_cpu
    if force_mem is not None: CURRENT_MEM_LIMIT = force_mem

    # Enforcement batasan absolut (MIN_MEM sekarang 32)
    CURRENT_CPU_LIMIT = max(MIN_CPU, min(MAX_CPU, round(CURRENT_CPU_LIMIT, 2)))
    CURRENT_MEM_LIMIT = max(MIN_MEM, min(MAX_MEM, int(CURRENT_MEM_LIMIT)))

    cpu_diff = abs(CURRENT_CPU_LIMIT - APPLIED_CPU_LIMIT)
    mem_diff = abs(CURRENT_MEM_LIMIT - APPLIED_MEM_LIMIT)

    is_emergency = (force_mem is not None) or (force_cpu is not None)
    
    # Threshold diubah menjadi 16MB agar fine-tuning agen tereksekusi
    if cpu_diff >= 0.1 or mem_diff >= 16 or is_emergency:
        mem_bytes_val = int(CURRENT_MEM_LIMIT * 1024 * 1024)
        try:
            container.update(
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
        a = choose(s, mem, cpu)
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