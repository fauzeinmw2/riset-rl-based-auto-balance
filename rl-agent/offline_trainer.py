import os
from dataclasses import dataclass

import gymnasium as gym
import numpy as np
import yaml
from gymnasium import spaces
from stable_baselines3 import PPO, SAC
from stable_baselines3.common.evaluation import evaluate_policy
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import DummyVecEnv


@dataclass
class TrainerConfig:
    algorithm: str
    model_output: str
    total_timesteps: int
    eval_episodes: int
    target_cpu_core: float
    target_mem_mb: float
    max_rt_ms: float
    max_error_rate: float
    w_sla: float
    w_energy: float
    w_throughput: float
    severe_sla_penalty: float
    oom_penalty: float
    downscale_under_load_penalty: float
    episode_steps: int
    action_cpu_step: float
    action_mem_step_mb: float
    min_cpu_core: float
    max_cpu_core: float
    min_mem_mb: float
    max_mem_mb: float
    seed: int


def load_config(path: str) -> TrainerConfig:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    return TrainerConfig(
        algorithm=cfg["algorithm"],
        model_output=cfg["model_output"],
        total_timesteps=int(cfg["total_timesteps"]),
        eval_episodes=int(cfg["eval_episodes"]),
        target_cpu_core=float(cfg["constraints"]["target_cpu_core"]),
        target_mem_mb=float(cfg["constraints"]["target_mem_mb"]),
        max_rt_ms=float(cfg["constraints"]["max_rt_ms"]),
        max_error_rate=float(cfg["constraints"]["max_error_rate"]),
        w_sla=float(cfg["reward"]["w_sla"]),
        w_energy=float(cfg["reward"]["w_energy"]),
        w_throughput=float(cfg["reward"]["w_throughput"]),
        severe_sla_penalty=float(cfg["reward"]["severe_sla_penalty"]),
        oom_penalty=float(cfg["reward"]["oom_penalty"]),
        downscale_under_load_penalty=float(cfg["reward"]["downscale_under_load_penalty"]),
        episode_steps=int(cfg["environment"]["episode_steps"]),
        action_cpu_step=float(cfg["environment"]["action_cpu_step"]),
        action_mem_step_mb=float(cfg["environment"]["action_mem_step_mb"]),
        min_cpu_core=float(cfg["environment"]["min_cpu_core"]),
        max_cpu_core=float(cfg["environment"]["max_cpu_core"]),
        min_mem_mb=float(cfg["environment"]["min_mem_mb"]),
        max_mem_mb=float(cfg["environment"]["max_mem_mb"]),
        seed=int(cfg["seed"]),
    )


class SyntheticResourceEnv(gym.Env):
    """Synthetic environment for offline pretraining before realtime rollout."""

    metadata = {"render_modes": []}

    def __init__(self, cfg: TrainerConfig):
        super().__init__()
        self.cfg = cfg
        self.max_steps = cfg.episode_steps

        self.observation_space = spaces.Box(low=0.0, high=2.0, shape=(10,), dtype=np.float32)
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(2,), dtype=np.float32)

        self.rng = np.random.default_rng(cfg.seed)
        self.step_count = 0
        self.cpu_limit_core = cfg.target_cpu_core
        self.mem_limit_mb = cfg.target_mem_mb
        self.prev = np.zeros(5, dtype=np.float32)

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.step_count = 0
        self.cpu_limit_core = self.cfg.target_cpu_core
        self.mem_limit_mb = self.cfg.target_mem_mb

        base_load = self.rng.uniform(0.15, 0.5)
        self.prev = np.array([base_load, base_load, 0.12, 0.05, 0.01], dtype=np.float32)
        obs = self._make_obs(self.prev)
        return obs, {}

    def step(self, action):
        self.step_count += 1

        cpu_delta = float(np.clip(action[0], -1.0, 1.0) * self.cfg.action_cpu_step)
        mem_delta = float(np.clip(action[1], -1.0, 1.0) * self.cfg.action_mem_step_mb)

        self.cpu_limit_core = float(np.clip(self.cpu_limit_core + cpu_delta, self.cfg.min_cpu_core, self.cfg.max_cpu_core))
        self.mem_limit_mb = float(np.clip(self.mem_limit_mb + mem_delta, self.cfg.min_mem_mb, self.cfg.max_mem_mb))

        load = self._sample_load(self.step_count)
        now = self._simulate_system(load)

        rt_ms = now[2] * 1000.0
        err_rate = now[4]
        cpu_core = now[0] * self.cpu_limit_core
        mem_mb = now[1] * self.mem_limit_mb
        throughput_ratio = now[3] / max(0.1 + load, 1e-6)

        sla_violation = 1.0 if (rt_ms > self.cfg.max_rt_ms or err_rate > self.cfg.max_error_rate) else 0.0
        near_oom = 1.0 if mem_mb > self.mem_limit_mb * 0.92 else 0.0
        unsafe_downscale = 1.0 if (load > 0.6 and (cpu_delta < -0.01 or mem_delta < -8.0)) else 0.0

        energy = 0.65 * (cpu_core / self.cfg.max_cpu_core) + 0.35 * (mem_mb / self.cfg.max_mem_mb)
        sla_loss = 0.7 * min(rt_ms / self.cfg.max_rt_ms, 2.0) + 0.3 * min(err_rate / max(self.cfg.max_error_rate, 1e-6), 2.0)
        throughput_loss = 1.0 - float(np.clip(throughput_ratio, 0.0, 1.1))

        reward = 1.0 - (
            self.cfg.w_sla * sla_loss
            + self.cfg.w_energy * energy
            + self.cfg.w_throughput * throughput_loss
        )
        reward -= sla_violation * self.cfg.severe_sla_penalty
        reward -= near_oom * self.cfg.oom_penalty
        reward -= unsafe_downscale * self.cfg.downscale_under_load_penalty

        obs = self._make_obs(now)
        terminated = self.step_count >= self.max_steps
        truncated = False

        return obs, float(reward), terminated, truncated, {
            "cpu_core": cpu_core,
            "mem_mb": mem_mb,
            "rt_ms": rt_ms,
            "error_rate": err_rate,
        }

    def _sample_load(self, t: int) -> float:
        # Non-monotonic load pattern to mimic random low-medium-high spikes.
        base = 0.3 + 0.2 * np.sin(t / 11.0)
        random_spike = self.rng.uniform(0.0, 0.6) if self.rng.random() < 0.15 else 0.0
        dip = -self.rng.uniform(0.0, 0.2) if self.rng.random() < 0.10 else 0.0
        load = np.clip(base + random_spike + dip, 0.05, 1.2)
        return float(load)

    def _simulate_system(self, load: float) -> np.ndarray:
        cpu_sat = np.clip(load / max(self.cpu_limit_core, 1e-6), 0.0, 1.6)
        mem_sat = np.clip((0.35 + 0.85 * load) * (80.0 / max(self.mem_limit_mb, 1e-6)), 0.0, 1.6)

        rt_norm = np.clip(0.08 + 0.55 * max(cpu_sat - 0.85, 0.0) + 0.45 * max(mem_sat - 0.85, 0.0), 0.02, 1.8)
        served_ratio = np.clip(1.0 - 0.60 * max(cpu_sat - 1.0, 0.0) - 0.45 * max(mem_sat - 1.0, 0.0), 0.25, 1.0)
        req_rate_norm = np.clip((0.1 + load) * served_ratio, 0.02, 2.0)
        err_rate = np.clip(0.01 + 0.12 * max(cpu_sat - 1.0, 0.0) + 0.15 * max(mem_sat - 1.0, 0.0), 0.0, 1.0)

        now = np.array([
            np.clip(cpu_sat, 0.0, 2.0),
            np.clip(mem_sat, 0.0, 2.0),
            rt_norm,
            req_rate_norm,
            err_rate,
        ], dtype=np.float32)
        self.prev = now
        return now

    def _make_obs(self, now: np.ndarray) -> np.ndarray:
        pred = 0.65 * now + 0.35 * self.prev
        return np.clip(np.concatenate([now, pred]).astype(np.float32), 0.0, 2.0)


def build_model(cfg: TrainerConfig, env):
    if cfg.algorithm.lower() == "ppo":
        return PPO("MlpPolicy", env, verbose=1, seed=cfg.seed)

    return SAC(
        "MlpPolicy",
        env,
        verbose=1,
        seed=cfg.seed,
        learning_rate=3e-4,
        buffer_size=100000,
        batch_size=256,
        train_freq=1,
        gradient_steps=1,
    )


def main():
    cfg_path = os.getenv("TRAINING_CONFIG", "/app/training_config.yaml")
    cfg = load_config(cfg_path)

    os.makedirs(os.path.dirname(cfg.model_output), exist_ok=True)

    env = DummyVecEnv([lambda: Monitor(SyntheticResourceEnv(cfg))])
    model = build_model(cfg, env)

    model.learn(total_timesteps=cfg.total_timesteps)
    model.save(cfg.model_output)

    eval_env = Monitor(SyntheticResourceEnv(cfg))
    mean_reward, std_reward = evaluate_policy(model, eval_env, n_eval_episodes=cfg.eval_episodes)
    print(f"Saved model: {cfg.model_output}")
    print(f"Evaluation reward mean={mean_reward:.3f}, std={std_reward:.3f}")


if __name__ == "__main__":
    main()
