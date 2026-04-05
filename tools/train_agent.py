#!/usr/bin/env python3
"""
Train Agent with Multi-Seed Runs and Evaluation.

This script now orchestrates finite training jobs by calling agent.py with:
- explicit train mode,
- fixed episode count,
- seed-specific outputs,
- isolated Q-table and log artifacts.
"""

import csv
import json
import os
import subprocess
import time
from datetime import datetime
from statistics import mean, pstdev


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RL_AGENT_DIR = os.path.join(ROOT_DIR, "rl-agent")
RESULTS_DIR = os.path.join(ROOT_DIR, "training_results")
SUMMARY_FILE = os.path.join(RESULTS_DIR, "training_summary.json")
LOGS_ROOT = os.path.join(RL_AGENT_DIR, "logs", "training")

os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(LOGS_ROOT, exist_ok=True)


def train_single_seed(seed_id, num_episodes=100, timeout=None):
	print(f"\nStarting SEED {seed_id} training ({num_episodes} episodes)...")

	result = {
		"seed_id": seed_id,
		"num_episodes": num_episodes,
		"start_time": datetime.now().isoformat(),
		"status": "running",
	}

	start_epoch = time.time()
	seed_dir = os.path.join(RESULTS_DIR, f"seed_{seed_id}")
	os.makedirs(seed_dir, exist_ok=True)

	artifact_dir = os.path.join(LOGS_ROOT, f"seed_{seed_id}")
	os.makedirs(artifact_dir, exist_ok=True)

	q_table_file = os.path.join(artifact_dir, "q_table.json")
	decisions_file = os.path.join(artifact_dir, "decisions.csv")
	agent_log_file = os.path.join(artifact_dir, "agent.log")
	container_seed_dir = f"/app/logs/training/seed_{seed_id}"

	try:
		os.makedirs(seed_dir, exist_ok=True)

		cmd = [
			"docker",
			"compose",
			"run",
			"--rm",
			"-e",
			f"AGENT_LOG_FILE={container_seed_dir}/agent.log",
			"rl-agent",
			"python",
			"agent.py",
			"--mode",
			"train",
			"--episodes",
			str(num_episodes),
			"--seed",
			str(seed_id),
			"--reset-q-table",
			"--qtable-file",
			f"{container_seed_dir}/q_table.json",
			"--decision-log",
			f"{container_seed_dir}/decisions.csv",
		]

		process = subprocess.Popen(cmd, cwd=ROOT_DIR)
		if timeout:
			process.wait(timeout=timeout)
		else:
			process.wait()

		if process.returncode != 0:
			raise RuntimeError(f"agent.py exited with code {process.returncode}")

		result["end_time"] = datetime.now().isoformat()
		result["duration_seconds"] = int(time.time() - start_epoch)
		result["status"] = "completed"

		if os.path.exists(q_table_file):
			with open(q_table_file) as file_handle:
				q_table = json.load(file_handle)
			result["q_table_states"] = len(q_table)

			q_values = []
			for state in q_table.values():
				q_values.extend(state.values())

			if q_values:
				result["q_value_stats"] = {
					"mean": float(mean(q_values)),
					"std": float(pstdev(q_values)) if len(q_values) > 1 else 0.0,
					"min": float(min(q_values)),
					"max": float(max(q_values)),
				}

		if os.path.exists(decisions_file):
			with open(decisions_file, newline="") as file_handle:
				decisions = list(csv.DictReader(file_handle))

			result["total_decisions"] = len(decisions)
			if decisions:
				rewards = [float(row["reward"]) for row in decisions]
				unique_states = {row["state"] for row in decisions}
				action_histogram = {}
				for row in decisions:
					action_histogram[row["action"]] = action_histogram.get(row["action"], 0) + 1

				result["reward_stats"] = {
					"mean": float(mean(rewards)),
					"min": float(min(rewards)),
					"max": float(max(rewards)),
				}
				result["unique_states"] = len(unique_states)
				result["action_histogram"] = action_histogram

		result["artifacts"] = {
			"q_table": q_table_file,
			"decisions": decisions_file,
			"agent_log": agent_log_file,
		}
		return result

	except Exception as error:
		result["end_time"] = datetime.now().isoformat()
		result["duration_seconds"] = int(time.time() - start_epoch)
		result["status"] = "failed"
		result["error"] = str(error)
		return result


def analyze_training_results(seed_results):
	if not seed_results:
		return None

	summary = {
		"num_seeds": len(seed_results),
		"test_date": datetime.now().isoformat(),
		"individual_runs": seed_results,
		"aggregated_stats": {},
	}

	completed = [result for result in seed_results if result["status"] == "completed"]
	q_table_sizes = [result.get("q_table_states", 0) for result in completed]
	durations = [result.get("duration_seconds", 0) for result in completed]

	if q_table_sizes:
		summary["aggregated_stats"]["q_table_states"] = {
			"mean": float(mean(q_table_sizes)),
			"min": float(min(q_table_sizes)),
			"max": float(max(q_table_sizes)),
			"std": float(pstdev(q_table_sizes)) if len(q_table_sizes) > 1 else 0.0,
		}

	if durations:
		summary["aggregated_stats"]["duration_seconds"] = {
			"mean": float(mean(durations)),
			"min": float(min(durations)),
			"max": float(max(durations)),
		}

	if completed:
		best_run = max(completed, key=lambda item: item.get("q_table_states", 0))
		worst_run = min(completed, key=lambda item: item.get("q_table_states", 0))
		summary["best_run"] = best_run["seed_id"]
		summary["worst_run"] = worst_run["seed_id"]

	status_counts = {}
	for result in seed_results:
		status = result.get("status", "unknown")
		status_counts[status] = status_counts.get(status, 0) + 1
	summary["run_status_counts"] = status_counts

	return summary


def print_training_summary(summary):
	print("\n" + "=" * 100)
	print("TRAINING SUMMARY".center(100))
	print("=" * 100 + "\n")

	print(f"Test Date: {summary['test_date']}")
	print(f"Number of Seeds: {summary['num_seeds']}")
	print()

	print("Run Status:")
	for status, count in summary["run_status_counts"].items():
		print(f"  - {status}: {count}")
	print()

	if "q_table_states" in summary["aggregated_stats"]:
		stats = summary["aggregated_stats"]["q_table_states"]
		print("Q-Table States (across seeds):")
		print(f"  - Average: {stats['mean']:.0f} states")
		print(f"  - Range: {stats['min']:.0f} to {stats['max']:.0f}")
		print(f"  - Std Dev: {stats['std']:.1f}")
		print()

	if "duration_seconds" in summary["aggregated_stats"]:
		stats = summary["aggregated_stats"]["duration_seconds"]
		print("Training Duration (per seed):")
		print(f"  - Average: {stats['mean'] / 60:.1f} minutes")
		print(f"  - Range: {stats['min'] / 60:.1f}m to {stats['max'] / 60:.1f}m")
		print()

	if "best_run" in summary:
		print(f"Best Seed: {summary['best_run']} (most learned states)")
		print(f"Worst Seed: {summary['worst_run']} (fewest learned states)")
		print()

	print("=" * 100)
	print("Training evaluation complete. Full results saved to training_results/")


if __name__ == "__main__":
	import sys

	num_episodes = int(sys.argv[1]) if len(sys.argv) > 1 else 100
	num_seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 3

	print("\nStarting multi-seed training")
	print(f"  Episodes per seed: {num_episodes}")
	print(f"  Number of seeds: {num_seeds}")
	print(f"  Total target episodes: {num_episodes * num_seeds}")
	print()

	seed_results = []
	for seed_id in range(1, num_seeds + 1):
		result = train_single_seed(seed_id, num_episodes=num_episodes, timeout=None)
		seed_results.append(result)

		checkpoint = {
			"seeds_completed": seed_id,
			"results": seed_results,
		}
		with open(os.path.join(RESULTS_DIR, "checkpoint.json"), "w") as file_handle:
			json.dump(checkpoint, file_handle, indent=2)

	summary = analyze_training_results(seed_results)
	with open(SUMMARY_FILE, "w") as file_handle:
		json.dump(summary, file_handle, indent=2)

	print_training_summary(summary)
	print(f"\nResults saved to: {RESULTS_DIR}/")
