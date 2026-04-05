#!/usr/bin/env python3
"""
Compare Metrics: Baseline vs RL-Controlled API.

The output is intentionally conservative: a negative value always means regression,
and the summary only marks RL as effective when energy improves without hurting
success rate or error rate.
"""

import json
import os
import sys
import time
from datetime import datetime

import requests


PROM_URL = os.getenv("PROM_URL", "http://localhost:9090/api/v1/query_range")
METRICS_OUTPUT_FILE = "metrics_comparison.json"
MAX_EFFECTIVE_MEM_MB = float(os.getenv("MAX_EFFECTIVE_MEM_MB", "256"))
ENERGY_CPU_WEIGHT = float(os.getenv("ENERGY_CPU_WEIGHT", "0.65"))
ENERGY_MEM_WEIGHT = float(os.getenv("ENERGY_MEM_WEIGHT", "0.35"))


def verdict_mark(is_good):
	return "✓" if is_good else "✗"


def normalized_energy_score(cpu_pct, mem_mb):
	cpu_norm = max(0.0, min(cpu_pct / 100.0, 1.0))
	mem_norm = max(0.0, min(mem_mb / MAX_EFFECTIVE_MEM_MB, 1.0))
	return (ENERGY_CPU_WEIGHT * cpu_norm) + (ENERGY_MEM_WEIGHT * mem_norm)


def query_range(query_str, start_time, end_time, step="30s"):
	try:
		params = {
			"query": query_str,
			"start": int(start_time),
			"end": int(end_time),
			"step": step,
		}
		response = requests.get(PROM_URL, params=params, timeout=10)
		result = response.json()
		if result.get("status") == "success":
			return result.get("data", {}).get("result", [])
	except Exception as error:
		print(f"Query error: {error}")
	return []


def get_metric_avg(results):
	if not results:
		return 0.0
	values = results[0].get("values", [])
	usable_values = [float(value) for _, value in values if value != "NaN"]
	if not usable_values:
		return 0.0
	return sum(usable_values) / len(usable_values)


def get_metric_percentile(results, percentile=95):
	if not results:
		return 0.0
	values = sorted(float(value) for _, value in results[0].get("values", []) if value != "NaN")
	if not values:
		return 0.0
	index = int(len(values) * percentile / 100)
	return values[min(index, len(values) - 1)]


def get_metric_max(results):
	if not results:
		return 0.0
	values = [float(value) for _, value in results[0].get("values", []) if value != "NaN"]
	return max(values) if values else 0.0


def collect_metrics(start_time, end_time):
	print("Collecting metrics from Prometheus...")

	metrics = {
		"baseline": {},
		"rl": {},
		"test_duration_seconds": int(end_time - start_time),
		"test_period": {
			"start": datetime.fromtimestamp(start_time).isoformat(),
			"end": datetime.fromtimestamp(end_time).isoformat(),
		},
	}

	cpu_baseline = query_range('rate(container_cpu_usage_seconds_total{name="api-baseline"}[1m]) * 100', start_time, end_time)
	mem_baseline = query_range('container_memory_usage_bytes{name="api-baseline"} / 1024 / 1024', start_time, end_time)
	rt_baseline = query_range('sum(rate(http_request_duration_seconds_sum{service="api-baseline"}[1m])) / sum(rate(http_request_duration_seconds_count{service="api-baseline"}[1m])) * 1000', start_time, end_time)
	success_baseline = query_range('sum(rate(http_requests_total{service="api-baseline", status=~"2.."}[1m])) / sum(rate(http_requests_total{service="api-baseline"}[1m])) * 100', start_time, end_time)
	error_baseline = query_range('sum(rate(http_requests_total{service="api-baseline", status=~"5.."}[1m])) / sum(rate(http_requests_total{service="api-baseline"}[1m])) * 100', start_time, end_time)

	metrics["baseline"]["cpu_avg_%"] = get_metric_avg(cpu_baseline)
	metrics["baseline"]["cpu_max_%"] = get_metric_max(cpu_baseline)
	metrics["baseline"]["cpu_p95_%"] = get_metric_percentile(cpu_baseline, 95)
	metrics["baseline"]["mem_avg_MB"] = get_metric_avg(mem_baseline)
	metrics["baseline"]["mem_max_MB"] = get_metric_max(mem_baseline)
	metrics["baseline"]["latency_avg_ms"] = get_metric_avg(rt_baseline)
	metrics["baseline"]["latency_p95_ms"] = get_metric_percentile(rt_baseline, 95)
	metrics["baseline"]["latency_p99_ms"] = get_metric_percentile(rt_baseline, 99)
	metrics["baseline"]["success_rate_%"] = get_metric_avg(success_baseline)
	metrics["baseline"]["error_rate_%"] = get_metric_avg(error_baseline)

	cpu_rl = query_range('rate(container_cpu_usage_seconds_total{name="api-rl"}[1m]) * 100', start_time, end_time)
	mem_rl = query_range('container_memory_usage_bytes{name="api-rl"} / 1024 / 1024', start_time, end_time)
	rt_rl = query_range('sum(rate(http_request_duration_seconds_sum{service="api-rl"}[1m])) / sum(rate(http_request_duration_seconds_count{service="api-rl"}[1m])) * 1000', start_time, end_time)
	success_rl = query_range('sum(rate(http_requests_total{service="api-rl", status=~"2.."}[1m])) / sum(rate(http_requests_total{service="api-rl"}[1m])) * 100', start_time, end_time)
	error_rl = query_range('sum(rate(http_requests_total{service="api-rl", status=~"5.."}[1m])) / sum(rate(http_requests_total{service="api-rl"}[1m])) * 100', start_time, end_time)

	metrics["rl"]["cpu_avg_%"] = get_metric_avg(cpu_rl)
	metrics["rl"]["cpu_max_%"] = get_metric_max(cpu_rl)
	metrics["rl"]["cpu_p95_%"] = get_metric_percentile(cpu_rl, 95)
	metrics["rl"]["mem_avg_MB"] = get_metric_avg(mem_rl)
	metrics["rl"]["mem_max_MB"] = get_metric_max(mem_rl)
	metrics["rl"]["latency_avg_ms"] = get_metric_avg(rt_rl)
	metrics["rl"]["latency_p95_ms"] = get_metric_percentile(rt_rl, 95)
	metrics["rl"]["latency_p99_ms"] = get_metric_percentile(rt_rl, 99)
	metrics["rl"]["success_rate_%"] = get_metric_avg(success_rl)
	metrics["rl"]["error_rate_%"] = get_metric_avg(error_rl)

	return metrics


def calculate_comparison(metrics):
	comparison = {}

	baseline_cpu = metrics["baseline"]["cpu_avg_%"]
	rl_cpu = metrics["rl"]["cpu_avg_%"]
	comparison["cpu_reduction_%"] = ((baseline_cpu - rl_cpu) / baseline_cpu * 100) if baseline_cpu > 0 else 0.0

	baseline_mem = metrics["baseline"]["mem_avg_MB"]
	rl_mem = metrics["rl"]["mem_avg_MB"]
	comparison["mem_reduction_MB"] = baseline_mem - rl_mem
	comparison["mem_reduction_%"] = ((baseline_mem - rl_mem) / baseline_mem * 100) if baseline_mem > 0 else 0.0

	baseline_lat = metrics["baseline"]["latency_avg_ms"]
	rl_lat = metrics["rl"]["latency_avg_ms"]
	comparison["latency_change_%"] = ((rl_lat - baseline_lat) / baseline_lat * 100) if baseline_lat > 0 else 0.0

	baseline_success = metrics["baseline"]["success_rate_%"]
	rl_success = metrics["rl"]["success_rate_%"]
	comparison["success_rate_improvement_%"] = rl_success - baseline_success

	baseline_error = metrics["baseline"]["error_rate_%"]
	rl_error = metrics["rl"]["error_rate_%"]
	comparison["error_rate_reduction_%"] = baseline_error - rl_error

	baseline_energy = normalized_energy_score(baseline_cpu, baseline_mem)
	rl_energy = normalized_energy_score(rl_cpu, rl_mem)
	comparison["baseline_energy_score"] = baseline_energy
	comparison["rl_energy_score"] = rl_energy
	comparison["energy_reduction_%"] = ((baseline_energy - rl_energy) / baseline_energy * 100) if baseline_energy > 0 else 0.0

	return comparison


def print_comparison_table(metrics, comparison):
	print("\n" + "=" * 120)
	print("BASELINE vs RL-CONTROLLED API COMPARISON REPORT".center(120))
	print("=" * 120 + "\n")

	print(f"Test Duration: {metrics['test_duration_seconds']} seconds ({metrics['test_duration_seconds'] / 60:.1f} minutes)")
	print(f"Test Period: {metrics['test_period']['start']} to {metrics['test_period']['end']}")
	print()

	print("-" * 120)
	print("ENERGY & RESOURCE CONSUMPTION")
	print("-" * 120)
	print(f"{'Metric':<40} | {'BASELINE':<25} | {'RL-CONTROLLED':<25} | {'IMPROVEMENT':<20}")
	print("-" * 120)

	baseline_cpu = metrics["baseline"]["cpu_avg_%"]
	rl_cpu = metrics["rl"]["cpu_avg_%"]
	cpu_improvement = comparison["cpu_reduction_%"]
	print(
		f"{'Avg CPU Usage':<40} | {baseline_cpu:>7.2f}% ({baseline_cpu / 100:.2f} core) | "
		f"{rl_cpu:>7.2f}% ({rl_cpu / 100:.2f} core) | {cpu_improvement:>+7.2f}% {verdict_mark(cpu_improvement > 0)}"
	)
	print(f"{'Peak CPU Usage':<40} | {metrics['baseline']['cpu_max_%']:>7.2f}% | {metrics['rl']['cpu_max_%']:>7.2f}% | ")
	print(f"{'P95 CPU Usage':<40} | {metrics['baseline']['cpu_p95_%']:>7.2f}% | {metrics['rl']['cpu_p95_%']:>7.2f}% | ")
	print()

	baseline_mem = metrics["baseline"]["mem_avg_MB"]
	rl_mem = metrics["rl"]["mem_avg_MB"]
	mem_reduction = comparison["mem_reduction_MB"]
	mem_reduction_pct = comparison["mem_reduction_%"]
	baseline_mem_str = f"{baseline_mem:.0f} MB" if baseline_mem < 1024 else f"{baseline_mem / 1024:.2f} GB"
	rl_mem_str = f"{rl_mem:.0f} MB" if rl_mem < 1024 else f"{rl_mem / 1024:.2f} GB"
	print(
		f"{'Avg RAM Usage':<40} | {baseline_mem_str:>25} | {rl_mem_str:>25} | "
		f"{mem_reduction:>+7.0f} MB ({mem_reduction_pct:+.1f}%) {verdict_mark(mem_reduction > 0)}"
	)
	print(f"{'Peak RAM Usage':<40} | {metrics['baseline']['mem_max_MB']:>7.0f} MB | {metrics['rl']['mem_max_MB']:>7.0f} MB | ")
	print()
	print(
		f"{'Energy Proxy (normalized)':<40} | {comparison['baseline_energy_score']:<25.3f} | "
		f"{comparison['rl_energy_score']:<25.3f} | {comparison['energy_reduction_%']:>+7.2f}% "
		f"{verdict_mark(comparison['energy_reduction_%'] > 0)}"
	)

	print("\n" + "-" * 120)
	print("PERFORMANCE & RELIABILITY")
	print("-" * 120)
	print(f"{'Metric':<40} | {'BASELINE':<25} | {'RL-CONTROLLED':<25} | {'COMPARISON':<20}")
	print("-" * 120)

	latency_change = comparison["latency_change_%"]
	print(
		f"{'Avg Response Time':<40} | {metrics['baseline']['latency_avg_ms']:>7.1f} ms | "
		f"{metrics['rl']['latency_avg_ms']:>7.1f} ms | {latency_change:>+7.2f}% {verdict_mark(latency_change <= 5)}"
	)
	print(f"{'P95 Response Time':<40} | {metrics['baseline']['latency_p95_ms']:>7.1f} ms | {metrics['rl']['latency_p95_ms']:>7.1f} ms | ")
	print(f"{'P99 Response Time':<40} | {metrics['baseline']['latency_p99_ms']:>7.1f} ms | {metrics['rl']['latency_p99_ms']:>7.1f} ms | ")
	print()
	print(
		f"{'Success Rate':<40} | {metrics['baseline']['success_rate_%']:>7.2f}% | "
		f"{metrics['rl']['success_rate_%']:>7.2f}% | {comparison['success_rate_improvement_%']:>+7.2f}% "
		f"{verdict_mark(comparison['success_rate_improvement_%'] >= 0)}"
	)
	print(
		f"{'Error Rate':<40} | {metrics['baseline']['error_rate_%']:>7.2f}% | "
		f"{metrics['rl']['error_rate_%']:>7.2f}% | {comparison['error_rate_reduction_%']:>+7.2f}% "
		f"{verdict_mark(comparison['error_rate_reduction_%'] >= 0)}"
	)

	print("\n" + "-" * 120)
	print("SUMMARY")
	print("-" * 120)
	print(f"\n{'✅' if comparison['energy_reduction_%'] > 0 else '❌'} ENERGY REDUCTION: {comparison['energy_reduction_%']:+.1f}%")
	print(f"{'✅' if comparison['cpu_reduction_%'] > 0 else '❌'} CPU REDUCTION: {comparison['cpu_reduction_%']:+.1f}%")
	print(f"{'✅' if comparison['mem_reduction_MB'] > 0 else '❌'} MEMORY REDUCTION: {comparison['mem_reduction_%']:+.1f}% ({comparison['mem_reduction_MB']:+.0f} MB)")
	print(f"📊 LATENCY CHANGE: {comparison['latency_change_%']:+.1f}%")
	print(f"📊 SUCCESS RATE: {metrics['rl']['success_rate_%']:.2f}% (baseline: {metrics['baseline']['success_rate_%']:.2f}%)")

	if (
		comparison["energy_reduction_%"] > 0
		and comparison["latency_change_%"] <= 10
		and comparison["success_rate_improvement_%"] >= 0
		and comparison["error_rate_reduction_%"] >= 0
	):
		print("\nRESULT: RL Agent is EFFECTIVE - Better energy efficiency with acceptable performance trade-off")
	elif comparison["energy_reduction_%"] > 0:
		print("\nRESULT: RL Agent saves energy but still violates one or more guardrails - tune reward/action design")
	else:
		print("\nRESULT: RL Agent not effective yet - Review runtime correctness, state bins, and reward shaping")

	print("\n" + "=" * 120)


if __name__ == "__main__":
	if len(sys.argv) >= 3:
		start_time = int(sys.argv[1])
		end_time = int(sys.argv[2])
	else:
		end_time = time.time()
		start_time = end_time - 1800
		print(
			f"No time range specified, using last 30 minutes: "
			f"{datetime.fromtimestamp(start_time).isoformat()} to {datetime.fromtimestamp(end_time).isoformat()}"
		)

	metrics = collect_metrics(start_time, end_time)
	comparison = calculate_comparison(metrics)
	print_comparison_table(metrics, comparison)

	output_data = {
		"metrics": metrics,
		"comparison": comparison,
		"generated_at": datetime.now().isoformat(),
		"prometheus_url": PROM_URL,
	}

	with open(METRICS_OUTPUT_FILE, "w") as file_handle:
		json.dump(output_data, file_handle, indent=2)

	print(f"\nFull report saved to: {METRICS_OUTPUT_FILE}")
