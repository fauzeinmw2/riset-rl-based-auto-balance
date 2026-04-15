import json
import math
import os
import random
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Optional

import docker
import numpy as np
import requests
import torch
from torch import nn
from torch.nn.utils import clip_grad_norm_


# ================= CONFIG =================
PROM_URL = "http://prometheus:9090/api/v1/query"
CONTAINER_NAME = "api-rl"
MODEL_FILE = "dqn_model.pt"
AGENT_STATE_FILE = "agent_state.json"
LEGACY_QTABLE_FILE = "q_table.json"

INTERVAL = 15
COOLDOWN = 15
PROM_WINDOW = "30s"
CPU_PERIOD = 100000
SLA_P95_MS = 100.0

# Limit Default Awal
DEFAULT_CPU_LIMIT = 1.0
DEFAULT_MEM_LIMIT = 512

CURRENT_CPU_LIMIT = DEFAULT_CPU_LIMIT
CURRENT_MEM_LIMIT = DEFAULT_MEM_LIMIT

APPLIED_CPU_LIMIT = CURRENT_CPU_LIMIT
APPLIED_MEM_LIMIT = CURRENT_MEM_LIMIT

# Batasan Resource
MIN_CPU = 0.25
MAX_CPU = 1.0
MIN_MEM = 256
MAX_MEM = 512

# DQN Hyperparameters
STATE_DIM = 8
ACTION_DIM = 9
LEARNING_RATE = 1e-3
GAMMA = 0.95
REPLAY_BUFFER_SIZE = 5000
BATCH_SIZE = 32
TARGET_SYNC_INTERVAL = 25
GRAD_CLIP_NORM = 1.0
EPSILON_START = 0.20
EPSILON_MIN = 0.05
EPSILON_DECAY = 0.995
WARMUP_STEPS = 20

LAST_ACTION_TIME = 0.0


# ================= ACTION SPACE =================
ACTIONS = {
    0: {"cpu": 0.0, "mem": 0},
    1: {"cpu": 0.05, "mem": 0},
    2: {"cpu": -0.05, "mem": 0},
    3: {"cpu": 0.0, "mem": 16},
    4: {"cpu": 0.0, "mem": -16},
    5: {"cpu": 0.05, "mem": 16},
    6: {"cpu": -0.05, "mem": -16},
    7: {"cpu": 0.2, "mem": 64},
    8: {"cpu": -0.2, "mem": -64},
}


@dataclass
class MetricsSnapshot:
    cpu_pct: float
    mem_mb: float
    avg_rt_ms: float
    p95_rt_ms: float
    req_rate: float


def sanitize_metric(value, default=0.0, minimum=0.0):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default

    if math.isnan(numeric) or math.isinf(numeric):
        return default

    return max(minimum, numeric)


def clip_value(value, lower=0.0, upper=2.0):
    return max(lower, min(upper, float(value)))


def ensure_parent_dir(file_path):
    Path(file_path).resolve().parent.mkdir(parents=True, exist_ok=True)


class MetricsCollector:
    def __init__(self, prom_url=PROM_URL, service_name=CONTAINER_NAME, query_window=PROM_WINDOW):
        self.prom_url = prom_url
        self.service_name = service_name
        self.query_window = query_window
        self.session = requests.Session()

    def query_value(self, promql):
        try:
            response = self.session.get(self.prom_url, params={"query": promql}, timeout=2)
            response.raise_for_status()
            payload = response.json().get("data", {}).get("result", [])
            if not payload:
                return 0.0
            return sanitize_metric(payload[0]["value"][1])
        except Exception:
            return 0.0

    def collect(self):
        service_label = self.service_name
        window = self.query_window
        avg_rt_query = (
            f'sum(rate(http_request_duration_seconds_sum{{service="{service_label}"}}[{window}])) '
            f'/ clamp_min(sum(rate(http_request_duration_seconds_count{{service="{service_label}"}}[{window}])), 0.001) '
            f'* 1000'
        )
        p95_rt_query = (
            f'histogram_quantile(0.95, '
            f'sum by (le) (rate(http_request_duration_seconds_bucket{{service="{service_label}"}}[{window}]))) '
            f'* 1000'
        )
        req_rate_query = f'sum(rate(http_request_duration_seconds_count{{service="{service_label}"}}[{window}]))'
        cpu_query = f'rate(container_cpu_usage_seconds_total{{name="{service_label}"}}[{window}]) * 100'
        mem_query = f'container_memory_usage_bytes{{name="{service_label}"}} / 1024 / 1024'

        return MetricsSnapshot(
            cpu_pct=self.query_value(cpu_query),
            mem_mb=self.query_value(mem_query),
            avg_rt_ms=self.query_value(avg_rt_query),
            p95_rt_ms=self.query_value(p95_rt_query),
            req_rate=self.query_value(req_rate_query),
        )


def build_state(snapshot, current_cpu_limit, current_mem_limit):
    mem_limit = max(1.0, float(current_mem_limit))

    cpu_util_ratio = clip_value(snapshot.cpu_pct / 100.0)
    mem_used_ratio = clip_value(snapshot.mem_mb / mem_limit)
    avg_rt_ratio = clip_value(snapshot.avg_rt_ms / SLA_P95_MS)
    p95_rt_ratio = clip_value(snapshot.p95_rt_ms / SLA_P95_MS)
    req_rate_feature = min(math.log1p(snapshot.req_rate) / math.log1p(100.0), 1.0)
    cpu_limit_ratio = clip_value(current_cpu_limit / MAX_CPU)
    mem_limit_ratio = clip_value(current_mem_limit / MAX_MEM)
    p95_delta_ratio = clip_value(max(snapshot.p95_rt_ms - SLA_P95_MS, 0.0) / SLA_P95_MS)

    return np.array(
        [
            cpu_util_ratio,
            mem_used_ratio,
            avg_rt_ratio,
            p95_rt_ratio,
            req_rate_feature,
            cpu_limit_ratio,
            mem_limit_ratio,
            p95_delta_ratio,
        ],
        dtype=np.float32,
    )


def calculate_reward(snapshot, current_cpu_limit, current_mem_limit, action):
    cpu_use = snapshot.cpu_pct / 100.0
    mem_use = snapshot.mem_mb / MAX_MEM
    cpu_limit = current_cpu_limit / MAX_CPU
    mem_limit = current_mem_limit / MAX_MEM
    avg_ratio = min(snapshot.avg_rt_ms / SLA_P95_MS, 2.0)
    p95_ratio = min(snapshot.p95_rt_ms / SLA_P95_MS, 3.0)
    over_sla = max(0.0, p95_ratio - 1.0)
    action_cost = 0.05 if int(action) != 0 else 0.0

    reward = (
        1.0
        - (0.15 * cpu_use)
        - (0.10 * mem_use)
        - (0.15 * cpu_limit)
        - (0.15 * mem_limit)
        - (0.20 * avg_ratio)
        - (0.25 * min(p95_ratio, 1.0))
        - (1.50 * (over_sla ** 2))
        - action_cost
    )
    return float(reward)


def is_scale_down_action(action):
    delta = ACTIONS[int(action)]
    return delta["cpu"] < 0 or delta["mem"] < 0


def is_neutral_or_scale_up_action(action):
    delta = ACTIONS[int(action)]
    return delta["cpu"] >= 0 and delta["mem"] >= 0


def gate_action(action, snapshot, current_mem_limit):
    mem_limit = max(1.0, float(current_mem_limit))
    mem_used_ratio = snapshot.mem_mb / mem_limit

    if snapshot.p95_rt_ms > 150.0 or mem_used_ratio > 0.85:
        if not is_neutral_or_scale_up_action(action):
            return 0

    if snapshot.p95_rt_ms > SLA_P95_MS or mem_used_ratio > 0.80:
        if is_scale_down_action(action):
            return 0

    return int(action)


def select_warmup_action(snapshot, current_mem_limit):
    mem_limit = max(1.0, float(current_mem_limit))
    mem_used_ratio = snapshot.mem_mb / mem_limit

    if snapshot.p95_rt_ms > 150.0 or mem_used_ratio > 0.85:
        return 7
    if snapshot.p95_rt_ms > SLA_P95_MS or snapshot.cpu_pct > 75.0 or mem_used_ratio > 0.75:
        return 5
    if snapshot.p95_rt_ms < 60.0 and snapshot.cpu_pct < 35.0 and mem_used_ratio < 0.50:
        return 6
    return 0


class ReplayBuffer:
    def __init__(self, capacity):
        self.buffer: Deque[tuple[np.ndarray, int, float, np.ndarray, bool]] = deque(maxlen=capacity)

    def __len__(self):
        return len(self.buffer)

    def add(self, state, action, reward, next_state, done=False):
        self.buffer.append(
            (
                np.asarray(state, dtype=np.float32),
                int(action),
                float(reward),
                np.asarray(next_state, dtype=np.float32),
                bool(done),
            )
        )

    def sample(self, batch_size, device):
        transitions = random.sample(self.buffer, batch_size)
        states, actions, rewards, next_states, dones = zip(*transitions)
        return (
            torch.as_tensor(np.stack(states), dtype=torch.float32, device=device),
            torch.as_tensor(actions, dtype=torch.int64, device=device),
            torch.as_tensor(rewards, dtype=torch.float32, device=device),
            torch.as_tensor(np.stack(next_states), dtype=torch.float32, device=device),
            torch.as_tensor(dones, dtype=torch.float32, device=device),
        )


class DQNNetwork(nn.Module):
    def __init__(self, input_dim=STATE_DIM, output_dim=ACTION_DIM):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 64),
            nn.ReLU(),
            nn.Linear(64, output_dim),
        )

    def forward(self, inputs):
        return self.layers(inputs)


class DQNAgent:
    def __init__(
        self,
        input_dim=STATE_DIM,
        output_dim=ACTION_DIM,
        gamma=GAMMA,
        learning_rate=LEARNING_RATE,
        epsilon=EPSILON_START,
        epsilon_min=EPSILON_MIN,
        epsilon_decay=EPSILON_DECAY,
        batch_size=BATCH_SIZE,
        target_sync_interval=TARGET_SYNC_INTERVAL,
        replay_capacity=REPLAY_BUFFER_SIZE,
    ):
        self.device = torch.device("cpu")
        self.gamma = gamma
        self.batch_size = batch_size
        self.target_sync_interval = target_sync_interval
        self.epsilon = epsilon
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay
        self.control_steps = 0
        self.replay_buffer = ReplayBuffer(replay_capacity)

        self.policy_net = DQNNetwork(input_dim=input_dim, output_dim=output_dim).to(self.device)
        self.target_net = DQNNetwork(input_dim=input_dim, output_dim=output_dim).to(self.device)
        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()

        self.optimizer = torch.optim.Adam(self.policy_net.parameters(), lr=learning_rate)
        self.loss_fn = nn.MSELoss()

    def apply_runtime_state(self, runtime_state):
        self.epsilon = clip_value(
            runtime_state.get("epsilon", self.epsilon),
            lower=self.epsilon_min,
            upper=1.0,
        )
        self.control_steps = max(0, int(runtime_state.get("control_steps", self.control_steps)))

    def select_action(self, state, snapshot, current_mem_limit):
        if self.control_steps < WARMUP_STEPS:
            return select_warmup_action(snapshot, current_mem_limit), "warmup"

        if random.random() < self.epsilon:
            return random.choice(list(ACTIONS.keys())), "explore"

        with torch.no_grad():
            state_tensor = torch.as_tensor(state, dtype=torch.float32, device=self.device).unsqueeze(0)
            q_values = self.policy_net(state_tensor)
            return int(torch.argmax(q_values, dim=1).item()), "exploit"

    def remember(self, state, action, reward, next_state, done=False):
        self.replay_buffer.add(state, action, reward, next_state, done)

    def optimize_model(self):
        if len(self.replay_buffer) < self.batch_size:
            return None

        states, actions, rewards, next_states, dones = self.replay_buffer.sample(
            self.batch_size,
            self.device,
        )

        current_q_values = self.policy_net(states).gather(1, actions.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            next_q_values = self.target_net(next_states).max(dim=1).values
            target_q_values = rewards + (1.0 - dones) * self.gamma * next_q_values

        loss = self.loss_fn(current_q_values, target_q_values)
        self.optimizer.zero_grad()
        loss.backward()
        clip_grad_norm_(self.policy_net.parameters(), GRAD_CLIP_NORM)
        self.optimizer.step()

        return float(loss.item())

    def on_cycle_complete(self):
        self.control_steps += 1
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

        if self.control_steps % self.target_sync_interval == 0:
            self.target_net.load_state_dict(self.policy_net.state_dict())

    def save_model(self, model_path=MODEL_FILE):
        ensure_parent_dir(model_path)
        torch.save(
            {
                "policy_state_dict": self.policy_net.state_dict(),
                "target_state_dict": self.target_net.state_dict(),
                "optimizer_state_dict": self.optimizer.state_dict(),
            },
            model_path,
        )

    def load_model(self, model_path=MODEL_FILE):
        if not os.path.exists(model_path) or os.path.getsize(model_path) == 0:
            return False

        try:
            checkpoint = torch.load(model_path, map_location=self.device)
            self.policy_net.load_state_dict(checkpoint["policy_state_dict"])
            self.target_net.load_state_dict(checkpoint["target_state_dict"])
            self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
            return True
        except Exception as exc:
            print(f"Warning: gagal memuat checkpoint DQN dari {model_path}: {exc}")
            return False


def load_runtime_state(state_path=AGENT_STATE_FILE):
    if not os.path.exists(state_path) or os.path.getsize(state_path) == 0:
        return {}

    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        print(f"Warning: gagal memuat agent state dari {state_path}: {exc}")
        return {}


def save_runtime_state(runtime_state, state_path=AGENT_STATE_FILE):
    ensure_parent_dir(state_path)
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(runtime_state, handle, indent=2)


def build_runtime_state(agent, docker_controller):
    return {
        "epsilon": agent.epsilon,
        "control_steps": agent.control_steps,
        "current_cpu_limit": docker_controller.current_cpu_limit,
        "current_mem_limit": docker_controller.current_mem_limit,
        "applied_cpu_limit": docker_controller.applied_cpu_limit,
        "applied_mem_limit": docker_controller.applied_mem_limit,
        "last_action_time": docker_controller.last_action_time,
    }


def save_checkpoint(agent, docker_controller, model_path=MODEL_FILE, state_path=AGENT_STATE_FILE):
    agent.save_model(model_path=model_path)
    save_runtime_state(build_runtime_state(agent, docker_controller), state_path=state_path)


class DockerController:
    def __init__(
        self,
        container_name=CONTAINER_NAME,
        initial_cpu_limit=DEFAULT_CPU_LIMIT,
        initial_mem_limit=DEFAULT_MEM_LIMIT,
        applied_cpu_limit=None,
        applied_mem_limit=None,
        last_action_time=0.0,
    ):
        self.container_name = container_name
        self.current_cpu_limit = clip_value(initial_cpu_limit, lower=MIN_CPU, upper=MAX_CPU)
        self.current_mem_limit = int(clip_value(initial_mem_limit, lower=MIN_MEM, upper=MAX_MEM))
        self.applied_cpu_limit = (
            clip_value(applied_cpu_limit, lower=MIN_CPU, upper=MAX_CPU)
            if applied_cpu_limit is not None
            else self.current_cpu_limit
        )
        self.applied_mem_limit = (
            int(clip_value(applied_mem_limit, lower=MIN_MEM, upper=MAX_MEM))
            if applied_mem_limit is not None
            else self.current_mem_limit
        )
        self.last_action_time = float(last_action_time or 0.0)
        self.client = None
        self.container = None

    def connect(self):
        try:
            self.client = docker.from_env()
            self.container = self.client.containers.get(self.container_name)
            return self.container
        except docker.errors.NotFound as exc:
            raise RuntimeError(f"Error: kontainer {self.container_name} tidak ditemukan.") from exc
        except Exception as exc:
            raise RuntimeError(f"Error: gagal mengakses Docker untuk {self.container_name}: {exc}") from exc

    def refresh_container(self):
        if self.client is None:
            self.connect()

        try:
            self.container = self.client.containers.get(self.container_name)
            return self.container
        except docker.errors.NotFound:
            return None

    def apply_action(self, action=None, force_cpu=None, force_mem=None):
        if action is not None:
            delta = ACTIONS[int(action)]
            self.current_cpu_limit += delta["cpu"]
            self.current_mem_limit += delta["mem"]

        if force_cpu is not None:
            self.current_cpu_limit = float(force_cpu)
        if force_mem is not None:
            self.current_mem_limit = int(force_mem)

        self.current_cpu_limit = clip_value(self.current_cpu_limit, lower=MIN_CPU, upper=MAX_CPU)
        self.current_mem_limit = int(clip_value(self.current_mem_limit, lower=MIN_MEM, upper=MAX_MEM))

        cpu_diff = abs(self.current_cpu_limit - self.applied_cpu_limit)
        mem_diff = abs(self.current_mem_limit - self.applied_mem_limit)
        is_emergency = (force_cpu is not None) or (force_mem is not None)

        if cpu_diff < 0.1 and mem_diff < 16 and not is_emergency:
            print(
                "Skipped Docker update "
                f"(delta kecil) -> Target CPU={self.current_cpu_limit:.2f} | MEM={self.current_mem_limit}MB"
            )
            return

        mem_bytes = int(self.current_mem_limit * 1024 * 1024)
        live_container = self.refresh_container()
        if live_container is None:
            print(f"Warning: kontainer {self.container_name} tidak ditemukan saat apply_action.")
            return

        try:
            live_container.update(
                cpu_period=CPU_PERIOD,
                cpu_quota=int(self.current_cpu_limit * CPU_PERIOD),
                mem_limit=mem_bytes,
                memswap_limit=mem_bytes,
            )
            print(
                f"Applied to Docker -> CPU={self.current_cpu_limit:.2f} | MEM={self.current_mem_limit}MB"
            )
            self.applied_cpu_limit = self.current_cpu_limit
            self.applied_mem_limit = self.current_mem_limit
        except Exception as exc:
            print(f"Warning: Docker update failed: {exc}")


def print_cycle_header(snapshot, docker_controller, agent):
    local_time = time.localtime()
    print(f"\n========== Time: {time.strftime('%H:%M:%S', local_time)} ==========")
    print(
        "Metrics -> "
        f"CPU={snapshot.cpu_pct:.1f}% "
        f"MEM={snapshot.mem_mb:.1f}MB "
        f"AVG={snapshot.avg_rt_ms:.1f}ms "
        f"P95={snapshot.p95_rt_ms:.1f}ms "
        f"RPS={snapshot.req_rate:.2f}"
    )
    print(
        "Limits  -> "
        f"CPU={docker_controller.current_cpu_limit:.2f} "
        f"MEM={docker_controller.current_mem_limit}MB "
        f"| Epsilon={agent.epsilon:.3f}"
    )


def main():
    runtime_state = load_runtime_state()
    docker_controller = DockerController(
        initial_cpu_limit=runtime_state.get("current_cpu_limit", CURRENT_CPU_LIMIT),
        initial_mem_limit=runtime_state.get("current_mem_limit", CURRENT_MEM_LIMIT),
        applied_cpu_limit=runtime_state.get("applied_cpu_limit", APPLIED_CPU_LIMIT),
        applied_mem_limit=runtime_state.get("applied_mem_limit", APPLIED_MEM_LIMIT),
        last_action_time=runtime_state.get("last_action_time", LAST_ACTION_TIME),
    )

    try:
        docker_controller.connect()
    except RuntimeError as exc:
        print(exc)
        raise SystemExit(1) from exc

    metrics_collector = MetricsCollector()
    agent = DQNAgent()
    agent.load_model(model_path=MODEL_FILE)
    agent.apply_runtime_state(runtime_state)

    print("DQN RL agent started...")
    if os.path.exists(LEGACY_QTABLE_FILE):
        print(f"Legacy file detected and ignored: {LEGACY_QTABLE_FILE}")

    try:
        while True:
            snapshot = metrics_collector.collect()
            state_vector = build_state(
                snapshot,
                docker_controller.current_cpu_limit,
                docker_controller.current_mem_limit,
            )
            now = time.time()
            print_cycle_header(snapshot, docker_controller, agent)

            if snapshot.mem_mb > (docker_controller.current_mem_limit * 0.80):
                print("Emergency memory safeguard aktif. Naikkan memori +128MB.")
                docker_controller.apply_action(force_mem=docker_controller.current_mem_limit + 128)
                docker_controller.last_action_time = now
                save_checkpoint(agent, docker_controller)
                time.sleep(INTERVAL)
                continue

            action = 0
            proposed_action = 0
            strategy = "cooldown"

            if now - docker_controller.last_action_time > COOLDOWN:
                proposed_action, strategy = agent.select_action(
                    state_vector,
                    snapshot,
                    docker_controller.current_mem_limit,
                )
                action = gate_action(proposed_action, snapshot, docker_controller.current_mem_limit)
                if action != proposed_action:
                    print(
                        "Safety gate override -> "
                        f"proposed={proposed_action} diganti menjadi action={action}"
                    )

                docker_controller.apply_action(action=action)
                docker_controller.last_action_time = now
            else:
                print("Cooldown aktif. Agent menahan perubahan resource.")

            time.sleep(INTERVAL)

            next_snapshot = metrics_collector.collect()
            next_state_vector = build_state(
                next_snapshot,
                docker_controller.current_cpu_limit,
                docker_controller.current_mem_limit,
            )
            reward = calculate_reward(
                next_snapshot,
                docker_controller.current_cpu_limit,
                docker_controller.current_mem_limit,
                action,
            )

            agent.remember(state_vector, action, reward, next_state_vector)
            loss = agent.optimize_model()
            agent.on_cycle_complete()
            save_checkpoint(agent, docker_controller)

            loss_label = "n/a" if loss is None else f"{loss:.5f}"
            print(
                f"Action={action} (proposed={proposed_action}, mode={strategy}) | "
                f"Reward={reward:.3f} | Loss={loss_label} | "
                f"Epsilon={agent.epsilon:.3f} | Steps={agent.control_steps}"
            )
            print(
                "Next    -> "
                f"CPU={next_snapshot.cpu_pct:.1f}% "
                f"MEM={next_snapshot.mem_mb:.1f}MB "
                f"AVG={next_snapshot.avg_rt_ms:.1f}ms "
                f"P95={next_snapshot.p95_rt_ms:.1f}ms "
                f"RPS={next_snapshot.req_rate:.2f}"
            )
            print("========== END OF CYCLE ==========\n")
    except KeyboardInterrupt:
        print("\nDQN agent stopped by user.")
    finally:
        save_checkpoint(agent, docker_controller)


if __name__ == "__main__":
    main()
