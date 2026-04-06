import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Tuple

import docker
import numpy as np
import requests
from stable_baselines3 import PPO, SAC


PROM_URL = os.getenv("PROM_URL", "http://prometheus:9090/api/v1/query")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "api-rl")
MODEL_PATH = os.getenv("MODEL_PATH", "/app/models/sac_resource_controller.zip")
ALGO = os.getenv("POLICY_ALGO", "sac").lower()
CONTROL_INTERVAL_SEC = int(os.getenv("CONTROL_INTERVAL_SEC", "10"))
WARMUP_CYCLES = int(os.getenv("WARMUP_CYCLES", "6"))

# Initial and hard boundaries requested by the experiment design.
MIN_CPU_CORE = float(os.getenv("MIN_CPU_CORE", "0.1"))
MAX_CPU_CORE = float(os.getenv("MAX_CPU_CORE", "1.0"))
TARGET_CPU_CORE = float(os.getenv("TARGET_CPU_CORE", "0.7"))
MIN_MEM_MB = int(os.getenv("MIN_MEM_MB", "64"))
MAX_MEM_MB = int(os.getenv("MAX_MEM_MB", "512"))
TARGET_MEM_MB = int(os.getenv("TARGET_MEM_MB", "80"))

# Safety thresholds: prevent service down before aggressive downscale happens.
MAX_ERROR_RATE = float(os.getenv("MAX_ERROR_RATE", "0.03"))
MAX_RT_MS = float(os.getenv("MAX_RT_MS", "350"))
NEAR_OOM_RATIO = float(os.getenv("NEAR_OOM_RATIO", "0.9"))
HIGH_LOAD_REQ_RATE = float(os.getenv("HIGH_LOAD_REQ_RATE", "20"))

CPU_PERIOD = 100000


@dataclass
class Metrics:
    cpu_pct: float
    cpu_core: float
    mem_mb: float
    rt_ms: float
    req_total_rate: float
    req_error_rate: float


class PrometheusReader:
    def __init__(self, prom_url: str) -> None:
        self.prom_url = prom_url

    def _query(self, query: str) -> float:
        try:
            response = requests.get(self.prom_url, params={"query": query}, timeout=3)
            response.raise_for_status()
            payload = response.json()
            result = payload.get("data", {}).get("result", [])
            if not result:
                return 0.0
            return float(result[0]["value"][1])
        except Exception:
            return 0.0

    def read_api_metrics(self, service_name: str, container_name: str, cpu_limit_core: float, mem_limit_mb: int) -> Metrics:
        rt_ms = self._query(
            '1000 * (sum(rate(http_request_duration_seconds_sum{service="%s"}[20s])) '
            '/ clamp_min(sum(rate(http_request_duration_seconds_count{service="%s"}[20s])), 1e-6))'
            % (service_name, service_name)
        )
        req_total_rate = self._query(
            'sum(rate(http_request_duration_seconds_count{service="%s"}[20s]))' % service_name
        )
        req_error_rate = self._query(
            'sum(rate(http_requests_total{service="%s",status=~"5.."}[20s])) / '
            'clamp_min(sum(rate(http_requests_total{service="%s"}[20s])), 1e-6)'
            % (service_name, service_name)
        )

        cpu_pct = self._query('100 * rate(container_cpu_usage_seconds_total{name="%s"}[20s])' % container_name)
        mem_mb = self._query('container_memory_usage_bytes{name="%s"} / 1024 / 1024' % container_name)
        cpu_core = max(0.0, cpu_pct / 100.0)

        # Fallback when /5xx counter is not instrumented yet.
        if req_error_rate == 0.0:
            req_error_rate = self._query(
                'sum(rate(http_request_errors_total{service="%s"}[20s])) / '
                'clamp_min(sum(rate(http_request_duration_seconds_count{service="%s"}[20s])), 1e-6)'
                % (service_name, service_name)
            )

        # Guard against unrealistic values from empty windows.
        cpu_core = float(np.clip(cpu_core, 0.0, max(1.0, cpu_limit_core)))
        mem_mb = float(max(mem_mb, 0.0))
        rt_ms = float(max(rt_ms, 0.0))
        req_total_rate = float(max(req_total_rate, 0.0))
        req_error_rate = float(np.clip(req_error_rate, 0.0, 1.0))

        return Metrics(
            cpu_pct=cpu_core * 100.0,
            cpu_core=cpu_core,
            mem_mb=mem_mb,
            rt_ms=rt_ms,
            req_total_rate=req_total_rate,
            req_error_rate=req_error_rate,
        )


class ForecastModel:
    """Lightweight short-horizon predictor from recent trend (no heavy dependency)."""

    def __init__(self, maxlen: int = 18) -> None:
        self.history: Deque[Metrics] = deque(maxlen=maxlen)

    def push(self, metrics: Metrics) -> None:
        self.history.append(metrics)

    def predict(self) -> Metrics:
        if len(self.history) < 3:
            if self.history:
                return self.history[-1]
            return Metrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        recent = list(self.history)
        first = recent[0]
        last = recent[-1]
        span = max(len(recent) - 1, 1)

        cpu_slope = (last.cpu_core - first.cpu_core) / span
        mem_slope = (last.mem_mb - first.mem_mb) / span
        rt_slope = (last.rt_ms - first.rt_ms) / span
        req_slope = (last.req_total_rate - first.req_total_rate) / span
        err_slope = (last.req_error_rate - first.req_error_rate) / span

        horizon = 2.0
        cpu_core = max(0.0, last.cpu_core + cpu_slope * horizon)
        mem_mb = max(0.0, last.mem_mb + mem_slope * horizon)
        rt_ms = max(0.0, last.rt_ms + rt_slope * horizon)
        req_total_rate = max(0.0, last.req_total_rate + req_slope * horizon)
        req_error_rate = float(np.clip(last.req_error_rate + err_slope * horizon, 0.0, 1.0))

        return Metrics(
            cpu_pct=cpu_core * 100.0,
            cpu_core=cpu_core,
            mem_mb=mem_mb,
            rt_ms=rt_ms,
            req_total_rate=req_total_rate,
            req_error_rate=req_error_rate,
        )


class ResourceController:
    def __init__(self, container_name: str) -> None:
        self.client = docker.from_env()
        self.container = self.client.containers.get(container_name)
        self.cpu_core_limit = min(MAX_CPU_CORE, max(MIN_CPU_CORE, TARGET_CPU_CORE))
        self.mem_limit_mb = min(MAX_MEM_MB, max(MIN_MEM_MB, TARGET_MEM_MB))

    def apply(self, cpu_delta: float, mem_delta_mb: float) -> None:
        next_cpu = float(np.clip(self.cpu_core_limit + cpu_delta, MIN_CPU_CORE, MAX_CPU_CORE))
        next_mem = int(np.clip(self.mem_limit_mb + mem_delta_mb, MIN_MEM_MB, MAX_MEM_MB))

        cpu_quota = int(next_cpu * CPU_PERIOD)
        mem_bytes = int(next_mem * 1024 * 1024)

        try:
            self.container.update(
                cpu_period=CPU_PERIOD,
                cpu_quota=cpu_quota,
                mem_limit=mem_bytes,
                memswap_limit=mem_bytes,
            )
        except Exception:
            self.container.update(
                cpu_period=CPU_PERIOD,
                cpu_quota=cpu_quota,
                mem_limit=mem_bytes,
            )
        self.cpu_core_limit = next_cpu
        self.mem_limit_mb = next_mem

    def force_safe_scale(self) -> None:
        safe_cpu = min(MAX_CPU_CORE, max(self.cpu_core_limit, TARGET_CPU_CORE))
        safe_mem = min(MAX_MEM_MB, max(self.mem_limit_mb + 64, TARGET_MEM_MB + 32))
        self.apply(cpu_delta=safe_cpu - self.cpu_core_limit, mem_delta_mb=safe_mem - self.mem_limit_mb)


def normalize_observation(now: Metrics, pred: Metrics, cpu_limit: float, mem_limit: int) -> np.ndarray:
    obs = np.array(
        [
            now.cpu_core / max(cpu_limit, 1e-6),
            now.mem_mb / max(float(mem_limit), 1e-6),
            now.rt_ms / 1000.0,
            now.req_total_rate / 300.0,
            now.req_error_rate,
            pred.cpu_core / max(cpu_limit, 1e-6),
            pred.mem_mb / max(float(mem_limit), 1e-6),
            pred.rt_ms / 1000.0,
            pred.req_total_rate / 300.0,
            pred.req_error_rate,
        ],
        dtype=np.float32,
    )
    return np.clip(obs, 0.0, 2.0)


def map_action_to_delta(action: np.ndarray) -> Tuple[float, float]:
    # Upscale can move faster than downscale to keep SLA safer.
    raw_cpu = float(np.clip(action[0], -1.0, 1.0))
    raw_mem = float(np.clip(action[1], -1.0, 1.0))

    cpu_delta = raw_cpu * (0.10 if raw_cpu >= 0.0 else 0.04)
    mem_delta = raw_mem * (40.0 if raw_mem >= 0.0 else 16.0)
    return cpu_delta, mem_delta


def load_policy(model_path: str, algo: str):
    if not os.path.exists(model_path):
        return None
    if algo == "ppo":
        return PPO.load(model_path)
    return SAC.load(model_path)


def choose_heuristic_action(now: Metrics, pred: Metrics, cpu_limit: float, mem_limit: int) -> Tuple[float, float]:
    # Conservative fallback when model is not available.
    cpu_delta = 0.0
    mem_delta = 0.0

    rt_pressure = max(now.rt_ms, pred.rt_ms) > MAX_RT_MS
    mem_pressure = max(now.mem_mb, pred.mem_mb) > (mem_limit * NEAR_OOM_RATIO)

    if rt_pressure or mem_pressure:
        cpu_delta += 0.06
        mem_delta += 32.0
    else:
        if now.cpu_core < TARGET_CPU_CORE * 0.7 and pred.cpu_core < TARGET_CPU_CORE * 0.8:
            cpu_delta -= 0.04
        if now.mem_mb < TARGET_MEM_MB * 0.9 and pred.mem_mb < TARGET_MEM_MB * 0.95:
            mem_delta -= 12.0

    return cpu_delta, mem_delta


def main() -> None:
    service_name = os.getenv("SERVICE_NAME", "api-rl")
    print("Starting RL inference controller")

    reader = PrometheusReader(PROM_URL)
    predictor = ForecastModel()
    controller = ResourceController(CONTAINER_NAME)
    model = load_policy(MODEL_PATH, ALGO)

    if model is None:
        print("Model file not found. Running with heuristic fallback policy.")
    else:
        print("Loaded policy model from", MODEL_PATH)

    cycle = 0

    while True:
        cycle += 1
        metrics_now = reader.read_api_metrics(
            service_name=service_name,
            container_name=CONTAINER_NAME,
            cpu_limit_core=controller.cpu_core_limit,
            mem_limit_mb=controller.mem_limit_mb,
        )
        predictor.push(metrics_now)
        metrics_pred = predictor.predict()

        if (
            metrics_now.req_error_rate > MAX_ERROR_RATE
            or metrics_now.rt_ms > MAX_RT_MS * 1.4
            or metrics_now.mem_mb > controller.mem_limit_mb * NEAR_OOM_RATIO
        ):
            controller.force_safe_scale()
            print(
                "SAFE_UPSCALE"
                f" cpu={metrics_now.cpu_pct:.1f}% ({metrics_now.cpu_core:.2f} core)"
                f" mem={metrics_now.mem_mb:.1f}MB"
                f" rt={metrics_now.rt_ms:.1f}ms"
                f" err={metrics_now.req_error_rate:.3f}"
                f" limits=({controller.cpu_core_limit:.2f} core, {controller.mem_limit_mb}MB)"
            )
            time.sleep(CONTROL_INTERVAL_SEC)
            continue

        if model is None:
            cpu_delta, mem_delta = choose_heuristic_action(
                now=metrics_now,
                pred=metrics_pred,
                cpu_limit=controller.cpu_core_limit,
                mem_limit=controller.mem_limit_mb,
            )
        else:
            obs = normalize_observation(metrics_now, metrics_pred, controller.cpu_core_limit, controller.mem_limit_mb)
            action, _ = model.predict(obs, deterministic=True)
            cpu_delta, mem_delta = map_action_to_delta(action)

            # Risk-aware limiter: block aggressive downscale when spike is predicted.
            spike_risk = (
                metrics_pred.rt_ms > metrics_now.rt_ms * 1.2
                or metrics_pred.req_total_rate > metrics_now.req_total_rate * 1.2
                or metrics_pred.mem_mb > metrics_now.mem_mb * 1.15
            )
            overload_risk = (
                metrics_now.rt_ms > MAX_RT_MS * 0.65
                or metrics_pred.rt_ms > MAX_RT_MS * 0.75
                or metrics_now.req_total_rate > HIGH_LOAD_REQ_RATE
                or metrics_pred.req_total_rate > HIGH_LOAD_REQ_RATE
            )
            warmup_risk = cycle <= WARMUP_CYCLES

            if spike_risk or overload_risk or warmup_risk:
                cpu_delta = max(cpu_delta, 0.0)
                mem_delta = max(mem_delta, 0.0)
            elif metrics_now.rt_ms > MAX_RT_MS * 0.45 or metrics_pred.rt_ms > MAX_RT_MS * 0.55:
                cpu_delta = max(cpu_delta, -0.005)
                mem_delta = max(mem_delta, -4.0)

        controller.apply(cpu_delta=cpu_delta, mem_delta_mb=mem_delta)
        print(
            "ACTION"
            f" cpu={metrics_now.cpu_pct:.1f}% ({metrics_now.cpu_core:.2f} core)"
            f" mem={metrics_now.mem_mb:.1f}MB"
            f" rt={metrics_now.rt_ms:.1f}ms"
            f" req_rate={metrics_now.req_total_rate:.2f}/s"
            f" err={metrics_now.req_error_rate:.3f}"
            f" limits=({controller.cpu_core_limit:.2f} core, {controller.mem_limit_mb}MB)"
            f" delta=({cpu_delta:+.3f} core, {mem_delta:+.1f}MB)"
            f" pred_rt={metrics_pred.rt_ms:.1f}ms pred_mem={metrics_pred.mem_mb:.1f}MB"
        )

        time.sleep(CONTROL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
