#!/usr/bin/env python3
import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Tuple

import requests


@dataclass
class RunResult:
    label: str
    avg_cpu_pct: float
    avg_cpu_core: float
    avg_mem_mb: float
    avg_rt_ms: float
    req_success: float
    req_total: float
    req_fail_rate: float


@dataclass
class AggregateResult:
    avg_cpu_pct_mean: float
    avg_cpu_pct_std: float
    avg_cpu_core_mean: float
    avg_cpu_core_std: float
    avg_mem_mb_mean: float
    avg_mem_mb_std: float
    avg_rt_ms_mean: float
    avg_rt_ms_std: float
    req_success_mean: float
    req_success_std: float
    req_total_mean: float
    req_total_std: float


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def query_range(prom_url: str, query: str, start_ts: float, end_ts: float, step_sec: int = 5) -> List[float]:
    url = prom_url.rstrip("/") + "/api/v1/query_range"
    resp = requests.get(
        url,
        params={
            "query": query,
            "start": iso(start_ts),
            "end": iso(end_ts),
            "step": f"{step_sec}s",
        },
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    result = payload.get("data", {}).get("result", [])
    if not result:
        return []

    series = result[0].get("values", [])
    values: List[float] = []
    for point in series:
        try:
            values.append(float(point[1]))
        except Exception:
            continue
    return values


def mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def stddev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def run_k6(script_path: str, target_url: str, out_json: str, seed: int) -> None:
    cmd = [
        "k6",
        "run",
        script_path,
        "--summary-export",
        out_json,
        "-e",
        f"TARGET_URL={target_url}",
        "-e",
        f"SEED={seed}",
    ]
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0 and not os.path.exists(out_json):
        raise subprocess.CalledProcessError(returncode=completed.returncode, cmd=cmd)


def parse_k6_summary(path: str) -> Tuple[float, float, float]:
    with open(path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    metrics = summary.get("metrics", {})
    duration_avg_ms = float(metrics.get("http_req_duration", {}).get("avg", 0.0))
    req_total = float(metrics.get("http_reqs", {}).get("count", 0.0))
    fail_rate = float(metrics.get("http_req_failed", {}).get("value", 0.0))

    # Fallback for older/newer k6 formats with nested values object.
    if duration_avg_ms == 0.0:
        duration_avg_ms = float(metrics.get("http_req_duration", {}).get("values", {}).get("avg", 0.0))
    if req_total == 0.0:
        req_total = float(metrics.get("http_reqs", {}).get("values", {}).get("count", 0.0))
    if fail_rate == 0.0:
        fail_rate = float(metrics.get("http_req_failed", {}).get("values", {}).get("rate", 0.0))

    req_success = max(req_total * (1.0 - fail_rate), 0.0)
    return duration_avg_ms, req_total, req_success


def collect_result(
    label: str,
    service_name: str,
    container_name: str,
    summary_json: str,
    prom_url: str,
    start_ts: float,
    end_ts: float,
) -> RunResult:
    avg_rt_ms, req_total, req_success = parse_k6_summary(summary_json)

    cpu_pct_values = query_range(
        prom_url,
        f'100 * rate(container_cpu_usage_seconds_total{{name="{container_name}"}}[20s])',
        start_ts,
        end_ts,
    )
    mem_values = query_range(
        prom_url,
        f'container_memory_usage_bytes{{name="{container_name}"}} / 1024 / 1024',
        start_ts,
        end_ts,
    )
    err_values = query_range(
        prom_url,
        'sum(rate(http_requests_total{service="%s",status=~"5.."}[20s])) / clamp_min(sum(rate(http_requests_total{service="%s"}[20s])), 1e-6)'
        % (service_name, service_name),
        start_ts,
        end_ts,
    )

    avg_cpu_pct = mean(cpu_pct_values)
    return RunResult(
        label=label,
        avg_cpu_pct=avg_cpu_pct,
        avg_cpu_core=avg_cpu_pct / 100.0,
        avg_mem_mb=mean(mem_values),
        avg_rt_ms=avg_rt_ms,
        req_success=req_success,
        req_total=req_total,
        req_fail_rate=mean(err_values),
    )


def pct_delta(base: float, new: float) -> float:
    if base == 0:
        return 0.0
    return ((new - base) / base) * 100.0


def fmt_cpu(pct: float, core: float) -> str:
    return f"{pct:.2f}% ({core:.2f} core)"


def fmt_mem(mem_mb: float) -> str:
    if mem_mb >= 1024:
        return f"{mem_mb / 1024.0:.2f} GB"
    return f"{mem_mb:.2f} MB"


def aggregate(results: List[RunResult]) -> AggregateResult:
    return AggregateResult(
        avg_cpu_pct_mean=mean([item.avg_cpu_pct for item in results]),
        avg_cpu_pct_std=stddev([item.avg_cpu_pct for item in results]),
        avg_cpu_core_mean=mean([item.avg_cpu_core for item in results]),
        avg_cpu_core_std=stddev([item.avg_cpu_core for item in results]),
        avg_mem_mb_mean=mean([item.avg_mem_mb for item in results]),
        avg_mem_mb_std=stddev([item.avg_mem_mb for item in results]),
        avg_rt_ms_mean=mean([item.avg_rt_ms for item in results]),
        avg_rt_ms_std=stddev([item.avg_rt_ms for item in results]),
        req_success_mean=mean([item.req_success for item in results]),
        req_success_std=stddev([item.req_success for item in results]),
        req_total_mean=mean([item.req_total for item in results]),
        req_total_std=stddev([item.req_total for item in results]),
    )


def print_table(baseline: AggregateResult, rl: AggregateResult) -> None:
    rows = []

    cpu_delta_pct = baseline.avg_cpu_pct_mean - rl.avg_cpu_pct_mean
    cpu_delta_core = baseline.avg_cpu_core_mean - rl.avg_cpu_core_mean
    cpu_pct_imp = -pct_delta(baseline.avg_cpu_pct_mean, rl.avg_cpu_pct_mean)
    rows.append(
        (
            "CPU Usage",
            f"{fmt_cpu(baseline.avg_cpu_pct_mean, baseline.avg_cpu_core_mean)} ± {baseline.avg_cpu_pct_std:.2f}%",
            f"{fmt_cpu(rl.avg_cpu_pct_mean, rl.avg_cpu_core_mean)} ± {rl.avg_cpu_pct_std:.2f}%",
            f"{cpu_delta_pct:+.2f}% ({cpu_delta_core:+.2f} core), {cpu_pct_imp:+.2f}%",
        )
    )

    mem_delta_mb = baseline.avg_mem_mb_mean - rl.avg_mem_mb_mean
    mem_pct_imp = -pct_delta(baseline.avg_mem_mb_mean, rl.avg_mem_mb_mean)
    rows.append(
        (
            "RAM Usage",
            f"{fmt_mem(baseline.avg_mem_mb_mean)} ± {baseline.avg_mem_mb_std:.2f}",
            f"{fmt_mem(rl.avg_mem_mb_mean)} ± {rl.avg_mem_mb_std:.2f}",
            f"{mem_delta_mb:+.2f} MB, {mem_pct_imp:+.2f}%",
        )
    )

    rt_delta = baseline.avg_rt_ms_mean - rl.avg_rt_ms_mean
    rt_pct = -pct_delta(baseline.avg_rt_ms_mean, rl.avg_rt_ms_mean)
    rows.append(
        (
            "Response Time",
            f"{baseline.avg_rt_ms_mean:.2f} ms ± {baseline.avg_rt_ms_std:.2f}",
            f"{rl.avg_rt_ms_mean:.2f} ms ± {rl.avg_rt_ms_std:.2f}",
            f"{rt_delta:+.2f} ms, {rt_pct:+.2f}%",
        )
    )

    succ_delta = rl.req_success_mean - baseline.req_success_mean
    succ_pct = pct_delta(baseline.req_success_mean, rl.req_success_mean)
    rows.append(
        (
            "Req Success",
            f"{baseline.req_success_mean:.0f}/{baseline.req_total_mean:.0f} ± {baseline.req_success_std:.0f}",
            f"{rl.req_success_mean:.0f}/{rl.req_total_mean:.0f} ± {rl.req_success_std:.0f}",
            f"{succ_delta:+.0f} req, {succ_pct:+.2f}%",
        )
    )

    headers = (
        "Metric",
        "API-Baseline",
        "API-RL",
        "Perbandingan",
    )

    table = [headers] + rows
    widths = [max(len(str(row[col])) for row in table) for col in range(4)]

    def sep(char: str = "-") -> str:
        return "+" + "+".join(char * (w + 2) for w in widths) + "+"

    print(sep("="))
    print("| " + " | ".join(str(headers[i]).ljust(widths[i]) for i in range(4)) + " |")
    print(sep("-"))
    for row in rows:
        print("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(4)) + " |")
    print(sep("="))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run random spike test on baseline vs RL and print comparison table")
    parser.add_argument("--prom-url", default="http://localhost:9090", help="Prometheus base URL")
    parser.add_argument("--script", default="load_test/spike_test.js", help="Path to k6 script")
    parser.add_argument("--baseline-url", default="http://localhost:3000", help="Baseline API URL")
    parser.add_argument("--rl-url", default="http://localhost:3002", help="RL API URL")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic stage generation")
    parser.add_argument("--out-dir", default="load_test/results", help="Output folder for k6 summaries")
    parser.add_argument("--rounds", type=int, default=1, help="Number of repeated benchmark rounds")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    baseline_results: List[RunResult] = []
    rl_results: List[RunResult] = []

    try:
        for round_index in range(args.rounds):
            baseline_summary = os.path.join(args.out_dir, f"k6_baseline_summary_r{round_index + 1}.json")
            rl_summary = os.path.join(args.out_dir, f"k6_rl_summary_r{round_index + 1}.json")
            round_seed = args.seed + round_index

            print(f"Running baseline spike test (round {round_index + 1}/{args.rounds})...")
            start = time.time()
            run_k6(args.script, args.baseline_url, baseline_summary, round_seed)
            end = time.time()
            baseline_results.append(
                collect_result(
                    label=f"baseline-r{round_index + 1}",
                    service_name="api-baseline",
                    container_name="api-baseline",
                    summary_json=baseline_summary,
                    prom_url=args.prom_url,
                    start_ts=start,
                    end_ts=end,
                )
            )

            print(f"Running RL spike test (round {round_index + 1}/{args.rounds})...")
            start = time.time()
            run_k6(args.script, args.rl_url, rl_summary, round_seed)
            end = time.time()
            rl_results.append(
                collect_result(
                    label=f"rl-r{round_index + 1}",
                    service_name="api-rl",
                    container_name="api-rl",
                    summary_json=rl_summary,
                    prom_url=args.prom_url,
                    start_ts=start,
                    end_ts=end,
                )
            )
    except subprocess.CalledProcessError:
        print("k6 execution failed. Make sure k6 is installed and APIs are reachable.")
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"Prometheus query failed: {exc}")
        sys.exit(1)

    print_table(aggregate(baseline_results), aggregate(rl_results))


if __name__ == "__main__":
    main()
