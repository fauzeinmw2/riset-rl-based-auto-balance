import importlib.util
import math
import sys
import tempfile
import unittest
from pathlib import Path

import torch


ROOT_DIR = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT_DIR / "rl-agent" / "agent.py"


def load_agent_module():
    module_name = "rl_agent_agent_testable"
    spec = importlib.util.spec_from_file_location(module_name, AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class AgentModuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = load_agent_module()

    def test_build_state_normalizes_expected_features(self):
        snapshot = self.agent.MetricsSnapshot(
            cpu_pct=50.0,
            mem_mb=128.0,
            avg_rt_ms=50.0,
            p95_rt_ms=120.0,
            req_rate=10.0,
        )

        state = self.agent.build_state(snapshot, current_cpu_limit=0.5, current_mem_limit=256)

        self.assertEqual(len(state), 8)
        self.assertAlmostEqual(state[0], 0.5, places=6)
        self.assertAlmostEqual(state[1], 0.5, places=6)
        self.assertAlmostEqual(state[2], 0.5, places=6)
        self.assertAlmostEqual(state[3], 1.2, places=6)
        self.assertAlmostEqual(state[4], math.log1p(10.0) / math.log1p(100.0), places=6)
        self.assertAlmostEqual(state[5], 0.5, places=6)
        self.assertAlmostEqual(state[6], 0.5, places=6)
        self.assertAlmostEqual(state[7], 0.2, places=6)

    def test_reward_prefers_lower_resource_under_sla(self):
        low_cost_snapshot = self.agent.MetricsSnapshot(
            cpu_pct=30.0,
            mem_mb=100.0,
            avg_rt_ms=55.0,
            p95_rt_ms=80.0,
            req_rate=8.0,
        )
        high_cost_snapshot = self.agent.MetricsSnapshot(
            cpu_pct=75.0,
            mem_mb=280.0,
            avg_rt_ms=55.0,
            p95_rt_ms=80.0,
            req_rate=8.0,
        )

        low_reward = self.agent.calculate_reward(low_cost_snapshot, 0.5, 256, action=0)
        high_reward = self.agent.calculate_reward(high_cost_snapshot, 1.0, 512, action=0)

        self.assertGreater(low_reward, high_reward)

    def test_reward_penalizes_sla_breach(self):
        under_sla = self.agent.MetricsSnapshot(
            cpu_pct=45.0,
            mem_mb=180.0,
            avg_rt_ms=60.0,
            p95_rt_ms=90.0,
            req_rate=12.0,
        )
        over_sla = self.agent.MetricsSnapshot(
            cpu_pct=45.0,
            mem_mb=180.0,
            avg_rt_ms=60.0,
            p95_rt_ms=160.0,
            req_rate=12.0,
        )

        reward_under = self.agent.calculate_reward(under_sla, 0.75, 384, action=0)
        reward_over = self.agent.calculate_reward(over_sla, 0.75, 384, action=0)

        self.assertGreater(reward_under, reward_over)
        self.assertGreater(reward_under - reward_over, 0.5)

    def test_gate_action_blocks_scale_down_when_latency_or_memory_risky(self):
        latency_risk = self.agent.MetricsSnapshot(
            cpu_pct=30.0,
            mem_mb=100.0,
            avg_rt_ms=70.0,
            p95_rt_ms=120.0,
            req_rate=5.0,
        )
        memory_risk = self.agent.MetricsSnapshot(
            cpu_pct=30.0,
            mem_mb=220.0,
            avg_rt_ms=70.0,
            p95_rt_ms=80.0,
            req_rate=5.0,
        )
        severe_risk = self.agent.MetricsSnapshot(
            cpu_pct=30.0,
            mem_mb=120.0,
            avg_rt_ms=70.0,
            p95_rt_ms=170.0,
            req_rate=5.0,
        )

        self.assertEqual(self.agent.gate_action(6, latency_risk, current_mem_limit=256), 0)
        self.assertEqual(self.agent.gate_action(6, memory_risk, current_mem_limit=256), 0)
        self.assertEqual(self.agent.gate_action(8, severe_risk, current_mem_limit=256), 0)
        self.assertEqual(self.agent.gate_action(5, severe_risk, current_mem_limit=256), 5)

    def test_checkpoint_round_trip_restores_model_and_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "dqn_model.pt"
            state_path = Path(temp_dir) / "agent_state.json"
            agent = self.agent.DQNAgent()
            runtime_stub = type(
                "RuntimeStub",
                (),
                {
                    "current_cpu_limit": 0.75,
                    "current_mem_limit": 384,
                    "applied_cpu_limit": 0.70,
                    "applied_mem_limit": 384,
                    "last_action_time": 123.0,
                },
            )()

            with torch.no_grad():
                for parameter in agent.policy_net.parameters():
                    parameter.fill_(0.25)
                for parameter in agent.target_net.parameters():
                    parameter.fill_(0.5)

            agent.epsilon = 0.11
            agent.control_steps = 7
            self.agent.save_checkpoint(agent, runtime_stub, model_path=model_path, state_path=state_path)

            reloaded_agent = self.agent.DQNAgent()
            self.assertTrue(reloaded_agent.load_model(model_path=model_path))
            runtime_state = self.agent.load_runtime_state(state_path=state_path)
            reloaded_agent.apply_runtime_state(runtime_state)

            self.assertAlmostEqual(reloaded_agent.epsilon, 0.11, places=6)
            self.assertEqual(reloaded_agent.control_steps, 7)
            self.assertAlmostEqual(runtime_state["current_cpu_limit"], 0.75, places=6)
            self.assertEqual(runtime_state["current_mem_limit"], 384)
            first_policy_weight = next(reloaded_agent.policy_net.parameters()).flatten()[0].item()
            first_target_weight = next(reloaded_agent.target_net.parameters()).flatten()[0].item()
            self.assertAlmostEqual(first_policy_weight, 0.25, places=6)
            self.assertAlmostEqual(first_target_weight, 0.5, places=6)
