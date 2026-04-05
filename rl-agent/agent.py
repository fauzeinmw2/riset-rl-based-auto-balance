#!/usr/bin/env python3
"""
RL Agent for Container Resource Optimization using Q-Learning.

This version focuses on runtime correctness first:
1. Robust container reconnection after restart/recreate.
2. Bind-mount-safe Q-table persistence.
3. Train vs eval mode separation.
4. Tighter state bins based on observed workload ranges.
5. Action masking to reduce overprovision bias.
6. Reward shaping that penalizes both actual usage and excess headroom.
"""

import argparse
import csv
import json
import logging
import os
import random
import time
from collections import deque
from datetime import datetime

import docker
import requests


# ============================================================
# CONFIG DEFAULTS
# ============================================================
PROM_URL = os.getenv("PROM_URL", "http://prometheus:9090/api/v1/query")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "api-rl")
QTABLE_FILE = os.getenv("QTABLE_FILE", "q_table.json")
DECISION_LOG_FILE = os.getenv("DECISION_LOG_FILE", "logs/decisions.csv")
LOG_FILE = os.getenv("AGENT_LOG_FILE", "logs/agent.log")

DEFAULT_INTERVAL = 15
DEFAULT_COOLDOWN = 15
CPU_UPDATE_PERIOD = 100000
METRICS_WINDOW_SIZE = 5

ALPHA = 0.15
GAMMA = 0.90
EPSILON_START = 0.20
EPSILON_MIN = 0.05
EPSILON_DECAY = 0.98

ENERGY_CPU_WEIGHT = 0.65
ENERGY_MEM_WEIGHT = 0.35
LATENCY_WEIGHT = 0.20
ERROR_WEIGHT = 0.20
HEADROOM_WEIGHT = 0.25
EFFICIENCY_BONUS_WEIGHT = 0.35


# ============================================================
# LOGGING SETUP
# ============================================================
os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s [%(levelname)s] %(message)s",
	handlers=[
		logging.FileHandler(LOG_FILE),
		logging.StreamHandler(),
	],
)
logger = logging.getLogger(__name__)


# ============================================================
# RUNTIME STATE
# ============================================================
CURRENT_CPU_LIMIT = 1.0
CURRENT_MEM_LIMIT = 256

APPLIED_CPU_LIMIT = CURRENT_CPU_LIMIT
APPLIED_MEM_LIMIT = CURRENT_MEM_LIMIT

MIN_CPU = 0.1
MAX_CPU = 1.0
MIN_MEM = 64
MAX_MEM = 256

LAST_ACTION_TIME = 0.0
INTERVAL = DEFAULT_INTERVAL
COOLDOWN = DEFAULT_COOLDOWN
RUN_MODE = "train"
MAX_EPISODES = None
RESET_Q_TABLE = False

baseline_metrics = deque(maxlen=METRICS_WINDOW_SIZE)
Q = {}

ACTIONS = {
	0: {"cpu": 0.0, "mem": 0},
	1: {"cpu": 0.05, "mem": 0},
	2: {"cpu": -0.05, "mem": 0},
	3: {"cpu": 0.0, "mem": 16},
	4: {"cpu": 0.0, "mem": -16},
	5: {"cpu": 0.05, "mem": 16},
	6: {"cpu": -0.05, "mem": -16},
	7: {"cpu": 0.1, "mem": 32},
	8: {"cpu": -0.1, "mem": -32},
}


# ============================================================
# DOCKER ACCESS
# ============================================================
docker_client = docker.from_env()
container = None


def get_container(force_refresh=False):
	"""Resolve current container handle and recover from compose recreate events."""
	global container

	if force_refresh:
		container = None

	if container is None:
		container = docker_client.containers.get(CONTAINER_NAME)
		logger.info(f"✅ Connected to container: {CONTAINER_NAME} ({container.id[:12]})")
		return container

	try:
		container.reload()
		return container
	except docker.errors.NotFound:
		container = docker_client.containers.get(CONTAINER_NAME)
		logger.info(f"🔄 Reconnected to container: {CONTAINER_NAME} ({container.id[:12]})")
		return container


def safe_update_container(cpu_limit, mem_limit_mb):
	"""Apply resource updates, retrying once after reconnect if needed."""
	mem_bytes = int(mem_limit_mb * 1024 * 1024)

	try:
		active_container = get_container()
		active_container.update(
			cpu_period=CPU_UPDATE_PERIOD,
			cpu_quota=int(cpu_limit * CPU_UPDATE_PERIOD),
			mem_limit=mem_bytes,
			memswap_limit=mem_bytes,
		)
		return active_container.id[:12]
	except docker.errors.NotFound:
		active_container = get_container(force_refresh=True)
		active_container.update(
			cpu_period=CPU_UPDATE_PERIOD,
			cpu_quota=int(cpu_limit * CPU_UPDATE_PERIOD),
			mem_limit=mem_bytes,
			memswap_limit=mem_bytes,
		)
		return active_container.id[:12]


try:
	get_container(force_refresh=True)
except docker.errors.NotFound:
	logger.error(f"❌ Container {CONTAINER_NAME} not found")
	raise SystemExit(1)


# ============================================================
# ARGUMENTS
# ============================================================
def parse_args():
	parser = argparse.ArgumentParser(description="Q-learning agent for container resource optimization")
	parser.add_argument("--mode", choices=["train", "eval"], default=os.getenv("AGENT_MODE", "train"))
	parser.add_argument("--episodes", type=int, default=None, help="Stop after N episodes")
	parser.add_argument("--seed", type=int, default=None, help="Seed for reproducible training")
	parser.add_argument("--reset-q-table", action="store_true", help="Ignore persisted Q-table")
	parser.add_argument("--qtable-file", default=QTABLE_FILE)
	parser.add_argument("--decision-log", default=DECISION_LOG_FILE)
	parser.add_argument("--interval", type=int, default=int(os.getenv("INTERVAL", DEFAULT_INTERVAL)))
	parser.add_argument("--cooldown", type=int, default=int(os.getenv("COOLDOWN", DEFAULT_COOLDOWN)))
	return parser.parse_args()


# ============================================================
# Q-TABLE AND LOGGING
# ============================================================
def load_q_table():
	if RESET_Q_TABLE:
		logger.info("🧪 Reset Q-table requested, starting fresh")
		return {}

	if os.path.exists(QTABLE_FILE) and os.path.getsize(QTABLE_FILE) > 0:
		try:
			with open(QTABLE_FILE) as file_handle:
				table = json.load(file_handle)
			logger.info(f"✅ Loaded Q-table with {len(table)} states")
			return table
		except Exception as error:
			logger.warning(f"⚠️ Failed to load Q-table: {error}, starting fresh")
	else:
		logger.info("📝 Q-table empty, starting from scratch")

	return {}


def save_q_table():
	"""Persist Q-table with a direct-write fallback for Docker bind mounts."""
	try:
		directory = os.path.dirname(QTABLE_FILE)
		if directory:
			os.makedirs(directory, exist_ok=True)

		temp_file = QTABLE_FILE + ".tmp"
		with open(temp_file, "w") as file_handle:
			json.dump(Q, file_handle, indent=2)
			file_handle.flush()
			os.fsync(file_handle.fileno())

		try:
			os.replace(temp_file, QTABLE_FILE)
		except OSError as replace_error:
			with open(QTABLE_FILE, "w") as file_handle:
				json.dump(Q, file_handle, indent=2)
				file_handle.flush()
				os.fsync(file_handle.fileno())
			if os.path.exists(temp_file):
				os.remove(temp_file)
			logger.warning(f"⚠️ Atomic replace failed, used direct write fallback: {replace_error}")

		logger.info(f"💾 Q-table saved ({len(Q)} states)")
	except Exception as error:
		logger.error(f"⚠️ Save failed: {error}")


def init_decision_log():
	directory = os.path.dirname(DECISION_LOG_FILE)
	if directory:
		os.makedirs(directory, exist_ok=True)

	with open(DECISION_LOG_FILE, "w", newline="") as file_handle:
		writer = csv.writer(file_handle)
		writer.writerow([
			"timestamp",
			"mode",
			"episode",
			"state",
			"action",
			"reward",
			"cpu_usage_pct",
			"mem_usage_mb",
			"latency_ms",
			"error_rate_pct",
			"cpu_limit_applied",
			"mem_limit_applied",
			"baseline_cpu_pct",
			"baseline_mem_mb",
			"baseline_latency_ms",
		])


def log_decision(episode, state, action, reward, metrics, baseline):
	with open(DECISION_LOG_FILE, "a", newline="") as file_handle:
		writer = csv.writer(file_handle)
		writer.writerow([
			datetime.now().isoformat(),
			RUN_MODE,
			episode,
			state,
			action,
			f"{reward:.4f}",
			f"{metrics['cpu']:.2f}",
			f"{metrics['mem']:.2f}",
			f"{metrics['rt']:.2f}",
			f"{metrics.get('error_rate', 0):.4f}",
			f"{APPLIED_CPU_LIMIT:.2f}",
			f"{APPLIED_MEM_LIMIT:.0f}",
			f"{baseline.get('cpu', 0):.2f}",
			f"{baseline.get('mem', 0):.2f}",
			f"{baseline.get('rt', 0):.2f}",
		])


# ============================================================
# METRIC COLLECTION
# ============================================================
def query_prometheus(query_str, timeout=2):
	try:
		response = requests.get(PROM_URL, params={"query": query_str}, timeout=timeout)
		response.raise_for_status()
		results = response.json().get("data", {}).get("result", [])
		if results:
			return float(results[0]["value"][1])
	except Exception as error:
		logger.debug(f"Query failed: {error}")
	return 0.0


def metrics_rl():
	return {
		"cpu": max(0.0, query_prometheus('rate(container_cpu_usage_seconds_total{name="api-rl"}[1m]) * 100')),
		"mem": max(0.0, query_prometheus('container_memory_usage_bytes{name="api-rl"} / 1024 / 1024')),
		"rt": max(0.0, query_prometheus('sum(rate(http_request_duration_seconds_sum{service="api-rl"}[1m])) / sum(rate(http_request_duration_seconds_count{service="api-rl"}[1m])) * 1000')),
		"error_rate": max(0.0, query_prometheus('sum(rate(http_requests_total{service="api-rl", status=~"5.."}[1m])) / sum(rate(http_requests_total{service="api-rl"}[1m])) * 100')),
	}


def metrics_baseline():
	return {
		"cpu": max(0.0, query_prometheus('rate(container_cpu_usage_seconds_total{name="api-baseline"}[1m]) * 100')),
		"mem": max(0.0, query_prometheus('container_memory_usage_bytes{name="api-baseline"} / 1024 / 1024')),
		"rt": max(0.0, query_prometheus('sum(rate(http_request_duration_seconds_sum{service="api-baseline"}[1m])) / sum(rate(http_request_duration_seconds_count{service="api-baseline"}[1m])) * 1000')),
	}


# ============================================================
# RL CORE
# ============================================================
def state_discrete(cpu, mem, rt, error_rate, request_rate=0):
	"""Discretize to bins tuned for the observed operating region."""
	if cpu < 3:
		cpu_bin = 0
	elif cpu < 8:
		cpu_bin = 1
	elif cpu < 15:
		cpu_bin = 2
	else:
		cpu_bin = 3

	mem_ratio = (mem / CURRENT_MEM_LIMIT) if CURRENT_MEM_LIMIT > 0 else 0
	if mem_ratio < 0.20:
		mem_bin = 0
	elif mem_ratio < 0.35:
		mem_bin = 1
	elif mem_ratio < 0.55:
		mem_bin = 2
	else:
		mem_bin = 3

	if rt < 100:
		rt_bin = 0
	elif rt < 200:
		rt_bin = 1
	elif rt < 500:
		rt_bin = 2
	elif rt < 1000:
		rt_bin = 3
	else:
		rt_bin = 4

	if error_rate < 0.5:
		err_bin = 0
	elif error_rate < 2:
		err_bin = 1
	else:
		err_bin = 2

	return f"{cpu_bin}|{mem_bin}|{rt_bin}|{err_bin}"


def compute_reward(metrics, baseline_snapshot):
	cpu_norm = max(0.0, min(metrics["cpu"] / 100.0, 1.0))
	mem_norm = max(0.0, min(metrics["mem"] / MAX_MEM, 1.0))
	rt_norm = max(0.0, min(metrics["rt"] / 2000.0, 1.0))
	error_norm = max(0.0, min(metrics["error_rate"] / 10.0, 1.0))

	provisioned_cpu_norm = max(0.0, min(CURRENT_CPU_LIMIT / MAX_CPU, 1.0))
	provisioned_mem_norm = max(0.0, min(CURRENT_MEM_LIMIT / MAX_MEM, 1.0))
	headroom_penalty = HEADROOM_WEIGHT * (
		0.5 * max(0.0, provisioned_cpu_norm - cpu_norm) +
		0.5 * max(0.0, provisioned_mem_norm - mem_norm)
	)

	energy_penalty = ENERGY_CPU_WEIGHT * cpu_norm + ENERGY_MEM_WEIGHT * mem_norm
	latency_penalty = LATENCY_WEIGHT * rt_norm
	error_penalty = ERROR_WEIGHT * error_norm
	total_penalty = energy_penalty + latency_penalty + error_penalty + headroom_penalty

	baseline_energy = (
		ENERGY_CPU_WEIGHT * (baseline_snapshot.get("cpu", 50.0) / 100.0) +
		ENERGY_MEM_WEIGHT * (baseline_snapshot.get("mem", 150.0) / MAX_MEM)
	)
	efficiency_bonus = EFFICIENCY_BONUS_WEIGHT * max(0.0, baseline_energy - energy_penalty)

	return -total_penalty + efficiency_bonus


def filter_allowed_actions(metrics):
	allowed = {0, 1, 2, 3, 4, 5, 6, 7, 8}

	if CURRENT_CPU_LIMIT <= MIN_CPU:
		allowed -= {2, 6, 8}
	if CURRENT_CPU_LIMIT >= MAX_CPU:
		allowed -= {1, 5, 7}
	if CURRENT_MEM_LIMIT <= MIN_MEM:
		allowed -= {4, 6, 8}
	if CURRENT_MEM_LIMIT >= MAX_MEM:
		allowed -= {3, 5, 7}

	cpu = metrics["cpu"]
	mem = metrics["mem"]
	rt = metrics["rt"]
	error_rate = metrics["error_rate"]

	if cpu < 4 and mem < 48 and rt < 60 and error_rate < 0.5:
		allowed -= {1, 3, 5, 7}
	elif cpu < 10 and mem < 72 and rt < 120 and error_rate < 1:
		allowed -= {7}
	elif cpu > 20 or mem > 96 or rt > 180 or error_rate > 1:
		allowed -= {2, 4, 6, 8}

	return sorted(allowed) if allowed else [0]


def choose_action(state, epsilon, metrics):
	Q.setdefault(state, {})
	allowed_actions = filter_allowed_actions(metrics)

	if random.random() < epsilon:
		return random.choice(allowed_actions)

	if not Q[state]:
		return random.choice(allowed_actions)

	ranked_actions = sorted(
		allowed_actions,
		key=lambda action_idx: Q[state].get(str(action_idx), 0.0),
		reverse=True,
	)
	return int(ranked_actions[0])


def update_q(state, action, reward, next_state):
	Q.setdefault(state, {})
	Q.setdefault(next_state, {})

	action_key = str(action)
	old_value = Q[state].get(action_key, 0.0)
	future_value = max(Q[next_state].values()) if Q[next_state] else 0.0
	new_value = old_value + ALPHA * (reward + GAMMA * future_value - old_value)
	Q[state][action_key] = float(new_value)


def apply_action(action_idx=None, force_cpu=None, force_mem=None):
	global CURRENT_CPU_LIMIT, CURRENT_MEM_LIMIT, APPLIED_CPU_LIMIT, APPLIED_MEM_LIMIT

	candidate_cpu = CURRENT_CPU_LIMIT
	candidate_mem = CURRENT_MEM_LIMIT

	if action_idx is not None:
		action = ACTIONS[int(action_idx)]
		candidate_cpu += action["cpu"]
		candidate_mem += action["mem"]

	if force_cpu is not None:
		candidate_cpu = force_cpu
	if force_mem is not None:
		candidate_mem = force_mem

	candidate_cpu = max(MIN_CPU, min(MAX_CPU, round(candidate_cpu, 2)))
	candidate_mem = max(MIN_MEM, min(MAX_MEM, int(candidate_mem)))

	cpu_diff = abs(candidate_cpu - APPLIED_CPU_LIMIT)
	mem_diff = abs(candidate_mem - APPLIED_MEM_LIMIT)
	is_emergency = force_cpu is not None or force_mem is not None
	apply_threshold = 0.05 if not is_emergency else 0.0

	CURRENT_CPU_LIMIT = candidate_cpu
	CURRENT_MEM_LIMIT = candidate_mem

	if cpu_diff < apply_threshold and mem_diff < 4 and not is_emergency:
		logger.debug(f"⏩ Skipped Docker update (delta too small): CPU Δ={cpu_diff:.3f}, MEM Δ={mem_diff}MB")
		return

	try:
		container_id = safe_update_container(candidate_cpu, candidate_mem)
		APPLIED_CPU_LIMIT = candidate_cpu
		APPLIED_MEM_LIMIT = candidate_mem
		level = "🚨 EMERGENCY" if is_emergency else "⚙️ Applied"
		logger.info(
			f"{level} Docker update: CPU={candidate_cpu:.2f} ({candidate_cpu * 100:.0f}%), "
			f"MEM={candidate_mem}MB, container={container_id}"
		)
	except Exception as error:
		CURRENT_CPU_LIMIT = APPLIED_CPU_LIMIT
		CURRENT_MEM_LIMIT = APPLIED_MEM_LIMIT
		logger.error(f"Docker update failed: {type(error).__name__}: {error}")


def check_safety(metrics):
	mem_usage_ratio = metrics["mem"] / CURRENT_MEM_LIMIT if CURRENT_MEM_LIMIT > 0 else 0.0
	if mem_usage_ratio > 0.80:
		logger.warning(f"🚨 OOM RISK (mem_ratio={mem_usage_ratio:.2%}), expanding MEM")
		apply_action(force_mem=min(CURRENT_MEM_LIMIT + 64, MAX_MEM))
		return True

	if metrics["rt"] > 500:
		logger.warning(f"🚨 HIGH LATENCY (rt={metrics['rt']:.0f}ms), relaxing limits")
		apply_action(force_cpu=min(CURRENT_CPU_LIMIT + 0.1, MAX_CPU))
		return True

	if metrics["error_rate"] > 5:
		logger.warning(f"🚨 ERROR SPIKE (error_rate={metrics['error_rate']:.1f}%), scaling up")
		apply_action(
			force_cpu=min(CURRENT_CPU_LIMIT + 0.1, MAX_CPU),
			force_mem=min(CURRENT_MEM_LIMIT + 32, MAX_MEM),
		)
		return True

	return False


# ============================================================
# MAIN LOOP
# ============================================================
if __name__ == "__main__":
	args = parse_args()

	RUN_MODE = args.mode
	MAX_EPISODES = args.episodes
	RESET_Q_TABLE = args.reset_q_table
	QTABLE_FILE = args.qtable_file
	DECISION_LOG_FILE = args.decision_log
	INTERVAL = args.interval
	COOLDOWN = args.cooldown

	if args.seed is not None:
		random.seed(args.seed)

	Q = load_q_table()
	init_decision_log()

	logger.info("🚀 RL Agent started (API Node.js optimization)")
	logger.info(
		f"Config: ALPHA={ALPHA}, GAMMA={GAMMA}, EPSILON_START={EPSILON_START}, "
		f"CPU=[{MIN_CPU:.2f}, {MAX_CPU:.2f}], MEM=[{MIN_MEM}, {MAX_MEM}], "
		f"MODE={RUN_MODE}, EPISODES={MAX_EPISODES or 'infinite'}"
	)

	episode = 0
	epsilon = 0.0 if RUN_MODE == "eval" else EPSILON_START

	while True:
		try:
			episode += 1

			metrics_rl_current = metrics_rl()
			metrics_baseline_current = metrics_baseline()

			baseline_metrics.append(metrics_baseline_current)
			current_baseline = {
				"cpu": sum(item["cpu"] for item in baseline_metrics) / len(baseline_metrics),
				"mem": sum(item["mem"] for item in baseline_metrics) / len(baseline_metrics),
				"rt": sum(item["rt"] for item in baseline_metrics) / len(baseline_metrics),
			}

			state = state_discrete(
				metrics_rl_current["cpu"],
				metrics_rl_current["mem"],
				metrics_rl_current["rt"],
				metrics_rl_current["error_rate"],
			)

			timestamp = datetime.now().strftime("%H:%M:%S")
			logger.info(
				f"\n[EP{episode}] {timestamp} | State: {state} | "
				f"CPU={metrics_rl_current['cpu']:.1f}% RAM={metrics_rl_current['mem']:.0f}MB "
				f"RT={metrics_rl_current['rt']:.0f}ms ERR={metrics_rl_current['error_rate']:.1f}%"
			)

			if check_safety(metrics_rl_current):
				time.sleep(INTERVAL)
				continue

			now = time.time()
			if now - LAST_ACTION_TIME > COOLDOWN:
				action = choose_action(state, epsilon, metrics_rl_current)
				apply_action(action_idx=action)
				LAST_ACTION_TIME = now
			else:
				action = 0

			time.sleep(INTERVAL)

			metrics_rl_next = metrics_rl()
			next_state = state_discrete(
				metrics_rl_next["cpu"],
				metrics_rl_next["mem"],
				metrics_rl_next["rt"],
				metrics_rl_next["error_rate"],
			)
			reward = compute_reward(metrics_rl_next, current_baseline)

			if RUN_MODE == "train":
				update_q(state, action, reward, next_state)

			log_decision(episode, state, action, reward, metrics_rl_next, current_baseline)
			logger.info(
				f"        → Action={action} Reward={reward:.4f} | "
				f"Limits: CPU={APPLIED_CPU_LIMIT:.2f} MEM={APPLIED_MEM_LIMIT}MB | "
				f"ε={epsilon:.3f} Q-states={len(Q)}"
			)

			if RUN_MODE == "train" and episode % 20 == 0:
				save_q_table()

			if RUN_MODE == "train" and episode > 10:
				epsilon = max(EPSILON_MIN, epsilon * EPSILON_DECAY)

			if MAX_EPISODES and episode >= MAX_EPISODES:
				logger.info(f"🏁 Reached max episodes: {MAX_EPISODES}")
				if RUN_MODE == "train":
					save_q_table()
				break

		except KeyboardInterrupt:
			logger.info("\n⏹️ Shutting down gracefully...")
			if RUN_MODE == "train":
				save_q_table()
			break
		except Exception as error:
			logger.error(f"Cycle error: {error}", exc_info=True)
			time.sleep(INTERVAL)
