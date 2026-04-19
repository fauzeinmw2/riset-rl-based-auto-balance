"""
DQN-Based Auto Resource Scaling Agent (CPU & Memory)

State  : [cpu_usage, memory_usage, latency, cpu_limit, memory_limit, request_rate]  (6 dims, normalized 0-1)
Actions: 0=noop, 1=+CPU, 2=-CPU, 3=+MEM, 4=-MEM
"""

import os
import math
import random
import time
import numpy as np
from collections import deque

import torch
import torch.nn as nn
import torch.optim as optim

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STATE_DIM   = 6
N_ACTIONS   = 5

CPU_MIN     = 0.1          # cores
MEM_MIN_MB  = 50.0         # MB
BUFFER_FRAC = 0.20         # new limit >= usage * (1 + BUFFER_FRAC)

SLA_LATENCY_MS = 100.0     # ms

GAMMA        = 0.99
LR           = 1e-3
BATCH_SIZE   = 32
BUFFER_SIZE  = 10_000
TARGET_UPDATE = 100        # steps
EPSILON_START = 1.0
EPSILON_MIN   = 0.1
EPSILON_DECAY = 0.995

MODEL_PATH = "dqn_model.pth"

# Action step sizes (raw units, NOT normalized)
CPU_STEP_FRAC = 0.10   # ±10 % of current cpu_limit
MEM_STEP_FRAC = 0.10   # ±10 % of current memory_limit


# ---------------------------------------------------------------------------
# DQN Network
# ---------------------------------------------------------------------------
class DQN(nn.Module):
    def __init__(self, state_dim: int = STATE_DIM, n_actions: int = N_ACTIONS):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_dim, 64),
            nn.ReLU(),
            nn.Linear(64, 64),
            nn.ReLU(),
            nn.Linear(64, n_actions),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


# ---------------------------------------------------------------------------
# Replay Buffer
# ---------------------------------------------------------------------------
class ReplayBuffer:
    def __init__(self, capacity: int = BUFFER_SIZE):
        self.buffer: deque = deque(maxlen=capacity)

    def push(self, state, action, reward, next_state, done):
        self.buffer.append((state, action, reward, next_state, done))

    def sample(self, batch_size: int):
        batch = random.sample(self.buffer, batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)
        return (
            torch.tensor(np.array(states),      dtype=torch.float32),
            torch.tensor(actions,               dtype=torch.long),
            torch.tensor(rewards,               dtype=torch.float32),
            torch.tensor(np.array(next_states), dtype=torch.float32),
            torch.tensor(dones,                 dtype=torch.float32),
        )

    def __len__(self):
        return len(self.buffer)


# ---------------------------------------------------------------------------
# Safety Constraints
# ---------------------------------------------------------------------------
def enforce_constraints(state: np.ndarray, action: int) -> int:
    """
    Validate that the requested action does not violate resource floors.
    Returns the original action if safe, else falls back to action 0 (noop).

    state indices (normalized 0-1):
      0: cpu_usage      1: memory_usage   2: latency
      3: cpu_limit      4: memory_limit   5: request_rate

    Denormalization assumptions (must match Environment):
      cpu_limit  : state[3] * CPU_LIMIT_MAX   (CPU_LIMIT_MAX = 4.0 cores)
      mem_limit  : state[4] * MEM_LIMIT_MAX   (MEM_LIMIT_MAX = 1024 MB)
      cpu_usage  : state[0] * CPU_LIMIT_MAX
      mem_usage  : state[1] * MEM_LIMIT_MAX
    """
    CPU_MAX = 4.0
    MEM_MAX = 1024.0

    cpu_limit_raw = state[3] * CPU_MAX
    mem_limit_raw = state[4] * MEM_MAX
    cpu_usage_raw = state[0] * CPU_MAX
    mem_usage_raw = state[1] * MEM_MAX

    if action == 2:  # decrease CPU
        new_cpu = cpu_limit_raw * (1 - CPU_STEP_FRAC)
        min_allowed = max(CPU_MIN, cpu_usage_raw * (1 + BUFFER_FRAC))
        if new_cpu < min_allowed:
            return 0  # noop

    elif action == 4:  # decrease memory
        new_mem = mem_limit_raw * (1 - MEM_STEP_FRAC)
        min_allowed = max(MEM_MIN_MB, mem_usage_raw * (1 + BUFFER_FRAC))
        if new_mem < min_allowed:
            return 0  # noop

    return action


# ---------------------------------------------------------------------------
# Reward Function
# ---------------------------------------------------------------------------
def compute_reward(state: np.ndarray) -> float:
    """
    state[2] = latency (normalized, where 1.0 = SLA_LATENCY_MS * 2)
    state[0] = cpu_usage (normalized)
    state[3] = cpu_limit (normalized)

    Energy proxy: cpu_usage * cpu_limit  (both normalized, product is relative)
    """
    LAT_MAX = SLA_LATENCY_MS * 2  # normalization denominator

    latency_ms  = state[2] * LAT_MAX
    cpu_usage   = state[0]
    cpu_limit   = state[3]

    if latency_ms > SLA_LATENCY_MS:
        return -100.0

    energy          = cpu_usage * cpu_limit
    latency_penalty = (latency_ms / SLA_LATENCY_MS) * 10.0  # 0–10 range
    return -(energy + latency_penalty)


# ---------------------------------------------------------------------------
# Simulated Environment
# ---------------------------------------------------------------------------
class Environment:
    """
    Mock environment that simulates a containerized service.
    All internal state is stored in raw units; get_state() normalizes.
    """

    CPU_MAX = 4.0      # cores
    MEM_MAX = 1024.0   # MB
    LAT_MAX = SLA_LATENCY_MS * 2  # ms

    def __init__(self):
        self.cpu_limit   = 1.0    # cores
        self.mem_limit   = 256.0  # MB
        self.cpu_usage   = 0.3    # cores
        self.mem_usage   = 80.0   # MB
        self.latency     = 40.0   # ms
        self.request_rate = 50.0  # req/s

    # ------------------------------------------------------------------
    def get_metrics(self) -> dict:
        """Return raw metric dictionary (simulated with small noise)."""
        noise = lambda v, pct: v * (1 + random.uniform(-pct, pct))
        return {
            "cpu_usage":    max(0.01, noise(self.cpu_usage,   0.05)),
            "memory_usage": max(1.0,  noise(self.mem_usage,   0.05)),
            "latency":      max(1.0,  noise(self.latency,     0.10)),
            "cpu_limit":    self.cpu_limit,
            "memory_limit": self.mem_limit,
            "request_rate": max(1.0,  noise(self.request_rate, 0.10)),
        }

    def get_state(self) -> np.ndarray:
        m = self.get_metrics()
        return np.array([
            np.clip(m["cpu_usage"]    / self.CPU_MAX, 0.0, 1.0),
            np.clip(m["memory_usage"] / self.MEM_MAX, 0.0, 1.0),
            np.clip(m["latency"]      / self.LAT_MAX, 0.0, 1.0),
            np.clip(m["cpu_limit"]    / self.CPU_MAX, 0.0, 1.0),
            np.clip(m["memory_limit"] / self.MEM_MAX, 0.0, 1.0),
            np.clip(m["request_rate"] / 200.0,        0.0, 1.0),
        ], dtype=np.float32)

    def apply_action(self, action: int):
        """Update resource limits based on action and simulate metric drift."""
        if action == 1:    # +CPU
            self.cpu_limit = min(self.CPU_MAX, self.cpu_limit * (1 + CPU_STEP_FRAC))
        elif action == 2:  # -CPU
            self.cpu_limit = max(CPU_MIN,      self.cpu_limit * (1 - CPU_STEP_FRAC))
        elif action == 3:  # +MEM
            self.mem_limit = min(self.MEM_MAX, self.mem_limit * (1 + MEM_STEP_FRAC))
        elif action == 4:  # -MEM
            self.mem_limit = max(MEM_MIN_MB,   self.mem_limit * (1 - MEM_STEP_FRAC))
        # action == 0: noop

        # Simulate environment dynamics
        self._simulate_step()

    def _simulate_step(self):
        """Drift usage and latency based on current limits and load."""
        headroom_cpu = (self.cpu_limit - self.cpu_usage) / self.cpu_limit
        headroom_mem = (self.mem_limit - self.mem_usage) / self.mem_limit

        # Latency increases when headroom is tight
        if headroom_cpu < 0.10 or headroom_mem < 0.10:
            self.latency = min(self.LAT_MAX, self.latency * random.uniform(1.05, 1.20))
        else:
            self.latency = max(5.0, self.latency * random.uniform(0.90, 1.02))

        # Usage drifts slowly toward 30% of current limit
        target_cpu = self.cpu_limit * 0.30
        target_mem = self.mem_limit * 0.35
        self.cpu_usage = 0.9 * self.cpu_usage + 0.1 * target_cpu + random.uniform(-0.01, 0.01)
        self.mem_usage = 0.9 * self.mem_usage + 0.1 * target_mem + random.uniform(-1.0, 1.0)
        self.cpu_usage = max(0.01, min(self.cpu_limit, self.cpu_usage))
        self.mem_usage = max(1.0,  min(self.mem_limit, self.mem_usage))

        # Random request rate drift
        self.request_rate = max(1.0, self.request_rate + random.uniform(-2.0, 2.0))


# ---------------------------------------------------------------------------
# DQN Agent
# ---------------------------------------------------------------------------
class DQNAgent:
    def __init__(
        self,
        state_dim:     int   = STATE_DIM,
        n_actions:     int   = N_ACTIONS,
        gamma:         float = GAMMA,
        lr:            float = LR,
        batch_size:    int   = BATCH_SIZE,
        buffer_size:   int   = BUFFER_SIZE,
        target_update: int   = TARGET_UPDATE,
        epsilon_start: float = EPSILON_START,
        epsilon_min:   float = EPSILON_MIN,
        epsilon_decay: float = EPSILON_DECAY,
    ):
        self.n_actions     = n_actions
        self.gamma         = gamma
        self.batch_size    = batch_size
        self.target_update = target_update
        self.epsilon       = epsilon_start
        self.epsilon_min   = epsilon_min
        self.epsilon_decay = epsilon_decay
        self.step_count    = 0

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self.q_net      = DQN(state_dim, n_actions).to(self.device)
        self.target_net = DQN(state_dim, n_actions).to(self.device)
        self.target_net.load_state_dict(self.q_net.state_dict())
        self.target_net.eval()

        self.optimizer = optim.Adam(self.q_net.parameters(), lr=lr)
        self.criterion = nn.MSELoss()
        self.buffer    = ReplayBuffer(buffer_size)

    # ------------------------------------------------------------------
    def select_action(self, state: np.ndarray) -> int:
        if random.random() < self.epsilon:
            return random.randrange(self.n_actions)
        state_t = torch.tensor(state, dtype=torch.float32, device=self.device).unsqueeze(0)
        with torch.no_grad():
            q_values = self.q_net(state_t)
        return int(q_values.argmax(dim=1).item())

    def store(self, state, action, reward, next_state, done):
        self.buffer.push(state, action, reward, next_state, done)

    def train_step(self):
        if len(self.buffer) < self.batch_size:
            return None

        states, actions, rewards, next_states, dones = self.buffer.sample(self.batch_size)
        states      = states.to(self.device)
        actions     = actions.to(self.device)
        rewards     = rewards.to(self.device)
        next_states = next_states.to(self.device)
        dones       = dones.to(self.device)

        # Current Q values
        q_values = self.q_net(states).gather(1, actions.unsqueeze(1)).squeeze(1)

        # Target Q values
        with torch.no_grad():
            max_next_q = self.target_net(next_states).max(dim=1).values
            targets    = rewards + self.gamma * max_next_q * (1 - dones)

        loss = self.criterion(q_values, targets)
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        self.step_count += 1

        # Update target network
        if self.step_count % self.target_update == 0:
            self.target_net.load_state_dict(self.q_net.state_dict())

        # Decay epsilon
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

        return loss.item()

    # ------------------------------------------------------------------
    def save(self, path: str = MODEL_PATH):
        torch.save({
            "q_net":      self.q_net.state_dict(),
            "target_net": self.target_net.state_dict(),
            "optimizer":  self.optimizer.state_dict(),
            "epsilon":    self.epsilon,
            "step_count": self.step_count,
        }, path)
        print(f"[Agent] Model saved → {path}")

    def load(self, path: str = MODEL_PATH):
        if not os.path.exists(path):
            print(f"[Agent] No checkpoint found at {path}, starting fresh.")
            return
        ckpt = torch.load(path, map_location=self.device)
        self.q_net.load_state_dict(ckpt["q_net"])
        self.target_net.load_state_dict(ckpt["target_net"])
        self.optimizer.load_state_dict(ckpt["optimizer"])
        self.epsilon    = ckpt.get("epsilon",    self.epsilon)
        self.step_count = ckpt.get("step_count", 0)
        print(f"[Agent] Checkpoint loaded from {path}  (step={self.step_count}, ε={self.epsilon:.4f})")


# ---------------------------------------------------------------------------
# Training Loop
# ---------------------------------------------------------------------------
ACTION_NAMES = ["noop", "+CPU", "-CPU", "+MEM", "-MEM"]


def train(
    n_episodes:     int   = 500,
    steps_per_ep:   int   = 200,
    sim_delay:      float = 0.0,   # seconds; set >0 for real pacing
    save_every:     int   = 50,
    resume:         bool  = True,
):
    env   = Environment()
    agent = DQNAgent()

    if resume:
        agent.load(MODEL_PATH)

    total_steps = 0

    for episode in range(1, n_episodes + 1):
        state = env.get_state()
        ep_reward = 0.0

        for step in range(steps_per_ep):
            # 1. Select action (epsilon-greedy)
            action = agent.select_action(state)

            # 2. Safety constraints
            action = enforce_constraints(state, action)

            # 3. Apply action
            env.apply_action(action)

            if sim_delay > 0:
                time.sleep(sim_delay)

            # 4. Observe next state & compute reward
            next_state = env.get_state()
            reward     = compute_reward(next_state)
            done       = (step == steps_per_ep - 1)

            # 5. Store & train
            agent.store(state, action, reward, next_state, float(done))
            loss = agent.train_step()

            ep_reward += reward
            state      = next_state
            total_steps += 1

            # Logging
            print(
                f"[Ep {episode:>4} | Step {step:>4}] "
                f"action={ACTION_NAMES[action]:<5}  "
                f"reward={reward:>8.3f}  "
                f"ε={agent.epsilon:.4f}  "
                f"loss={loss:.4f if loss is not None else 'N/A'}"
            )

        print(
            f"\n{'='*60}\n"
            f"Episode {episode}/{n_episodes}  "
            f"total_reward={ep_reward:.2f}  "
            f"steps={total_steps}  ε={agent.epsilon:.4f}\n"
            f"{'='*60}\n"
        )

        if episode % save_every == 0:
            agent.save(MODEL_PATH)

    agent.save(MODEL_PATH)
    print("Training complete.")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    train(
        n_episodes   = 500,
        steps_per_ep = 200,
        sim_delay    = 0.0,
        save_every   = 50,
        resume       = True,
    )
