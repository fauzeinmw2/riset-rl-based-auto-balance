import time, json, random, math, requests, docker, os

# ================= CONFIG =================
PROM_URL = "http://prometheus:9090/api/v1/query"
CONTAINER_NAME = "api-rl"
QTABLE_FILE = "q_table.json"

INTERVAL = 5
ALPHA, GAMMA, EPSILON = 0.1, 0.9, 0.1
OBJECTIVES = ["performance", "energy"]

CPU_PERIOD = 100000
CURRENT_CPU_LIMIT = 1.0  # default core

# ACTION = memory limit only (Docker Desktop compatible)
# ACTIONS = {
#     0: "256m",
#     1: "512m",
#     2: "1024m"
# }

ACTIONS = {
    0: {"cpu": 0.25, "mem": "256m"},   # ultra energy saving
    1: {"cpu": 0.5,  "mem": "512m"},   # balanced
    2: {"cpu": 1.0,  "mem": "1024m"}   # performance
}

docker_client = docker.from_env()
container = docker_client.containers.get(CONTAINER_NAME)
CONTAINER_ID = container.id

Q = json.load(open(QTABLE_FILE)) if os.path.exists(QTABLE_FILE) else {}

# ================= PROMETHEUS =================
def query(q):
    try:
        r = requests.get(PROM_URL, params={"query": q}, timeout=2)
        res = r.json()["data"]["result"]
        return float(res[0]["value"][1]) if res else 0.0
    except:
        return 0.0

def metrics():
    container_id = container.id
    
    # Ratarata cpu usage selama 1 menit terakhir
    # cpu_core = query(
    #     f'rate(container_cpu_usage_seconds_total{{id="/docker/{container_id}"}}[1m])'
    # )
    cpu_core = query(
        f'rate(container_cpu_usage_seconds_total{{name="api-rl"}}[1m])'
    )

    cpu_util = (cpu_core / CURRENT_CPU_LIMIT) * 100 if CURRENT_CPU_LIMIT else 0

    # mem = query(
    #     f'container_memory_usage_bytes{{id="/docker/{container_id}"}}'
    # ) / 1024 / 1024
    mem = query(
        f'container_memory_usage_bytes{{name="api-rl"}}'
    ) / 1024 / 1024

    # rt = query(
    #     'sum(rate(http_request_duration_seconds_sum[1m]))'
    #     ' / sum(rate(http_request_duration_seconds_count[1m]))'
    # ) * 1000

    rt = query(
        'sum(rate(http_request_duration_seconds_sum{job="api-rl"}[1m]))'
        ' / clamp_min(sum(rate(http_request_duration_seconds_count{job="api-rl"}[1m])), 0.0001)'
    ) * 1000


    if rt <= 0 or math.isnan(rt):
        rt = 50.0

    return cpu_util, mem, rt



def state(cpu, mem, rt):
    return f"{int(cpu//25)}|{int(mem//256)}|{int(rt//100)}"

def reward(cpu, mem, rt, obj):
    if obj == "performance":
        return -rt
    else:  # energy
        # return -(cpu + mem * 0.01)
        return -(cpu * 1.0 + mem * 0.005)

def choose(s, obj):
    Q.setdefault(s, {}).setdefault(obj, {})
    if random.random() < EPSILON or not Q[s][obj]:
        return random.choice(list(ACTIONS.keys()))
    return int(max(Q[s][obj], key=Q[s][obj].get))

def update_q(s, a, r, ns, obj):
    Q.setdefault(s, {}).setdefault(obj, {})
    Q.setdefault(ns, {}).setdefault(obj, {})

    a = str(a)
    old = Q[s][obj].get(a, 0)
    future = max(Q[ns][obj].values(), default=0)
    Q[s][obj][a] = old + ALPHA * (r + GAMMA * future - old)

def save():
    json.dump(Q, open(QTABLE_FILE, "w"), indent=2)

def mem_bytes(m):
    return int(m[:-1]) * 1024 * 1024

def nano_cpus(cores):
    return int(cores * 1e9)

# ================= DOCKER =================
def apply_action(a):
    global CURRENT_CPU_LIMIT
    try:
        a = int(a)
        action = ACTIONS[a]

        CURRENT_CPU_LIMIT = action["cpu"]
        mem = mem_bytes(action["mem"])

        container.update(
            cpu_period=CPU_PERIOD,
            cpu_quota=int(action["cpu"] * CPU_PERIOD),
            mem_limit=mem,
            memswap_limit=mem   # HARD LIMIT
        )

    except Exception as e:
        print("⚠️ Docker update failed:", e)


def get_focus_prom():
    try:
        r = requests.get(
            "http://prometheus:9090/api/v1/query",
            params={"query": 'rl_focus'},  # nama metric sesuai Node.js
            timeout=2
        )
        res = r.json()["data"]["result"]
        if not res:
            return "performance"  # default
        labels = res[0]["metric"]
        val = float(res[0]["value"][1])
        # Ambil mode dari label
        mode = labels.get("mode", "performance")
        return mode if val > 0 else "energy"
    except Exception as e:
        print("⚠️ Failed to get focus from Prometheus:", e)
        return "performance"
        

# ================= MAIN =================
print("RL agent started (Prometheus + Docker API)")

while True:
    cpu, mem, rt = metrics()
    s = state(cpu, mem, rt)

    obj = get_focus_prom()
    a = choose(s, obj)
    apply_action(a)

    time.sleep(INTERVAL)

    cpu2, mem2, rt2 = metrics()
    ns = state(cpu2, mem2, rt2)
    r = reward(cpu2, mem2, rt2, obj)

    update_q(s, a, r, ns, obj)

    print(f"[{obj}] s={s} a={a} cpu={cpu2:.1f}% mem={mem2:.0f}MB rt={rt2:.1f}ms r={r:.2f}")

    save()
