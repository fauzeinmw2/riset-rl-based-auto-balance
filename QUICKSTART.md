# RL-Based Auto Resource Balancing - Quick Start Guide

## Overview
Riset untuk mengoptimalkan energy consumption (CPU dan RAM) dari container Node.js API menggunakan Q-Learning RL agent, dibandingkan dengan baseline static container.

## Project Structure
```
rl-based-auto-balance/
├── api/                          # Node.js REST API (baseline & RL-controlled)
│   ├── Dockerfile
│   ├── index.js
│   └── package.json
├── api-go/                       # Go API (optional, untuk pembanding sekunder)
├── rl-agent/                     # RL Agent (Q-Learning)
│   ├── agent.py                  # Main agent loop
│   ├── q_table.json              # Learned Q-table
│   ├── requirements.txt
│   ├── logs/                     # Decision logs CSV
│   └── Dockerfile
├── postgres/                     # Database (KRS schema)
│   └── init.sql                  # Schema + seed data
├── prometheus/                   # Metrics collection
│   └── prometheus.yml
├── k6/                           # Load testing
│   └── spike-test.js             # K6 scenario (low/medium/high spike)
├── tools/                        # Analysis & evaluation
│   ├── compare_results.py        # Compare baseline vs RL
│   └── train_agent.py            # Multi-seed training orchestration
└── docker-compose.yml            # Orchestration
```

## Quick Start

### 1. Check Prerequisites
```bash
docker --version           # Docker 20.10+
docker-compose --version   # Docker Compose 1.29+
python3 --version         # Python 3.8+
```

### 2. Build & Start Stack
```bash
# Build images
docker-compose build

# Start all services
docker-compose up -d

# Verify services are healthy
docker-compose ps

# Check API health
curl http://localhost:3000/health
curl http://localhost:3002/health
```

Services will be available at:
- **Baseline API**: http://localhost:3000
- **RL-Controlled API**: http://localhost:3002
- **PostgreSQL**: localhost:5433 (user: rluser, pass: rlpass)
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3001 (admin/admin)
- **RL Agent**: Running in container (check logs with `docker logs rl-agent`)

### 3. Validate Database & API
```bash
# Check API endpoints
curl http://localhost:3000/api/courses | jq
curl http://localhost:3002/api/students | jq

# Monitor RL agent logs
docker logs -f rl-agent
```

### 4. Run Load Test & Comparison

#### Option A: Manual K6 Test (Recommended for Development)
```bash
# Install K6 locally (if not using container)
# macOS:
brew install k6

# Run spike test
k6 run k6/spike-test.js --vus 20 --duration 10m

# Or via Docker (for longer tests):
docker-compose --profile load-test up k6
```

#### Option B: Scripted Test Suite (Production)
```bash
# Start fresh stack
docker-compose down -v
docker-compose up -d --no-deps api-baseline api-rl postgres prometheus cadvisor rl-agent

# Let agent warm up for 5 min
sleep 300

# Record start time
START_TIME=$(date +%s)

# Run K6 load test (10 minutes)
k6 run k6/spike-test.js --vus 50 --duration 600s

# Record end time
END_TIME=$(date +%s)

# Compare results
python3 tools/compare_results.py $START_TIME $END_TIME
```

### 5. Visualize Results

**Option A: CLI Table (Recommended)**
```bash
# Generate comparison table
python3 tools/compare_results.py

# Output:
# - Prints 3-column table (Baseline | RL-Controlled | Comparison)
# - Saves metrics_comparison.json
```

**Option B: Grafana Dashboard**
- Open http://localhost:3001
- Login with admin/admin
- Add data source: Prometheus (http://prometheus:9090)
- Import dashboard for container metrics

**Option C: Prometheus Queries**
- Open http://localhost:9090
- Example queries:
  ```
  rate(container_cpu_usage_seconds_total{name="api-rl"}[1m]) * 100
  container_memory_usage_bytes{name="api-rl"} / 1024 / 1024
  http_request_duration_seconds_bucket{service="api-rl"}
  ```

## Key Metrics

### Energy Consumption (Primary KPI)
- **Energy Proxy**: `(CPU% / 100) + (RAM_MB / 256)`
- **Target**: RL API energy proxy < Baseline energy proxy
- **Measurement**: Average over entire test period

### CPU Usage (%)
- **Format**: Core (%) e.g., "10% (0.10 core)"
- **Goal**: Reduce from baseline while maintaining performance
- **Bounds**: 0.1 core (MIN_CPU) to 1.0 core (MAX_CPU)

### RAM Usage (MB/GB)
- **Format**: Auto-format to MB or GB
- **Goal**: Reduce from baseline while maintaining performance
- **Bounds**: 64 MB (MIN_MEM) to 256 MB (MAX_MEM)

### Response Time (ms)
- **Metric**: Average, P95, P99 latency
- **Trade-off**: Slight latency increase acceptable for energy savings
- **Threshold**: < 500ms average, < 1000ms P99

### Request Success Rate (%)
- **Target**: > 99% success rate
- **Alert**: < 95% indicates system stress

## Output Format

### CLI Comparison Table
```
====================================================================================
                   BASELINE vs RL-CONTROLLED API COMPARISON REPORT
====================================================================================

Test Duration: 600 seconds (10.0 minutes)
Test Period: 2024-04-05T10:00:00 to 2024-04-05T10:10:00

ENERGY & RESOURCE CONSUMPTION
====================================================================================
Metric                                  | BASELINE                 | RL-CONTROLLED        | IMPROVEMENT
====================================================================================
Avg CPU Usage                           |   85.3% (0.85 core)     |   62.4% (0.62 core)  |    -27.0% ✓
Peak CPU Usage                          |   98.2%                 |   79.1%              | 
P95 CPU Usage                           |   94.5%                 |   71.2%              | 

Avg RAM Usage                           |   384 MB                |   298 MB             |    -86 MB (-22.4%) ✓
Peak RAM Usage                          |   512 MB                |   428 MB             | 

Energy Proxy (CPU+RAM)                  |                         |                      |    -25.3% ✓

PERFORMANCE & RELIABILITY
====================================================================================
Metric                                  | BASELINE                 | RL-CONTROLLED        | COMPARISON
====================================================================================
Avg Response Time                       |  145.2 ms               |  156.8 ms            |    +11.6% ✗
P95 Response Time                       |  387.1 ms               |  312.4 ms            | 
P99 Response Time                       |  891.3 ms               |  541.2 ms            | 

Success Rate                            |   98.5%                 |   99.1%              |    +0.6% ✓
Error Rate                              |    1.5%                 |    0.9%              |    -0.6% ✓

====================================================================================
SUMMARY
====================================================================================

✅ ENERGY REDUCTION: -25.3%
✅ CPU REDUCTION: -27.0%
✅ MEMORY REDUCTION: -22.4% (-86 MB)
📊 LATENCY CHANGE: +11.6%
📊 SUCCESS RATE: 99.1% (baseline: 98.5%)

🎯 RESULT: RL Agent is EFFECTIVE - Better energy efficiency with acceptable performance trade-off

====================================================================================
```

### JSON Output (metrics_comparison.json)
```json
{
  "metrics": {
    "baseline": {
      "cpu_avg_%": 85.3,
      "cpu_max_%": 98.2,
      "mem_avg_MB": 384.0,
      "mem_max_MB": 512.0,
      "latency_avg_ms": 145.2,
      "latency_p95_ms": 387.1,
      "latency_p99_ms": 891.3,
      "success_rate_%": 98.5,
      "error_rate_%": 1.5
    },
    "rl": {...},
    "test_duration_seconds": 600
  },
  "comparison": {
    "cpu_reduction_%": -27.0,
    "mem_reduction_%": -22.4,
    "energy_reduction_%": -25.3,
    "latency_change_%": 11.6,
    "success_rate_improvement_%": 0.6
  }
}
```

## RL Agent Configuration

### State Space (4 dimensions)
- **CPU Util Bin** (0-3): 0%, 25%, 50%, 75%+
- **RAM Util Ratio Bin** (0-3): 0-25%, 25-50%, 50-75%, 75%+ of limit
- **Latency Bin** (0-4): 0-100ms, 100-200ms, 200-500ms, 500-1000ms, 1000ms+
- **Error Rate Bin** (0-2): <0.5%, 0.5-2%, 2%+

**Total state space**: 4 × 4 × 5 × 3 = 240 possible states

### Action Space (9 actions)
| ID | CPU Delta | MEM Delta | Purpose |
|----|-----------|-----------|---------|
| 0  | 0         | 0         | Idle |
| 1  | +0.05     | 0         | Relax CPU |
| 2  | -0.05     | 0         | Tighten CPU |
| 3  | 0         | +16 MB    | Increase RAM |
| 4  | 0         | -16 MB    | Decrease RAM |
| 5  | +0.05     | +16 MB    | Relax both |
| 6  | -0.05     | -16 MB    | Tighten both |
| 7  | +0.1      | +32 MB    | Emergency expand |
| 8  | -0.1      | -32 MB    | Emergency shrink |

### Reward Function
```
Reward = -(energy_penalty + latency_penalty + error_penalty) + efficiency_bonus

where:
  energy_penalty = 0.5 × (CPU%/100) + 0.5 × (RAM/MAX_RAM)
  latency_penalty = 0.25 × min(RT/2000, 1.0)
  error_penalty = 0.15 × min(error_rate/10, 1.0)
  efficiency_bonus = 0.1 × max(0, baseline_energy - current_energy)
```

### Hyperparameters
- **Learning Rate (α)**: 0.15
- **Discount Factor (γ)**: 0.90
- **Exploration Rate (ε)**: 0.20 → 0.05 (decay 0.98 per episode)
- **Observation Interval**: 15 seconds
- **Action Cooldown**: 15 seconds

### Resource Bounds
- **CPU Limit**: 0.1 core (MIN_CPU) → 1.0 core (MAX_CPU)
- **RAM Limit**: 64 MB (MIN_MEM) → 256 MB (MAX_MEM)

## Troubleshooting

### Agent not learning (Q-table size = 1-2 states)
```bash
# Check agent logs
docker logs rl-agent -f

# Common issues:
# 1. Prometheus not responding → Check docker-compose health
# 2. API not exporting metrics → Verify /metrics endpoint
# 3. Container load too low → Increase k6 VUs
```

### High latency from RL API
```bash
# Check resource limits
docker stats api-rl

# If approaching limit, increase MAX_MEM in agent.py
# Default: 256 MB, try 400 MB for larger workloads
```

### Prometheus queries return empty results
```bash
# Verify scrape configuration
curl http://localhost:9090/api/v1/targets

# Check metric names available
curl http://localhost:9090/api/v1/label/__name__/values
```

###  K6 timeout or OOM
```bash
# Reduce VUs or duration
k6 run k6/spike-test.js --vus 30 --duration 300s

# Or use container (more resources)
docker-compose --profile load-test up k6
```

## Advanced: Training Multi-Seed

For statistically rigorous results:

```bash
# Run 3 seeds with 100 episodes each
python3 tools/train_agent.py 100 3

# Outputs:
# - training_results/training_summary.json
# - Q-table stats (avg states, convergence metrics)
# - Best seed recommended for final deployment
```

## Architecture Decision: Q-Learning vs Library RL

Current implementation uses **Q-Learning** (hand-crafted) because:
1. **Fast prototyping**: No heavy library overhead
2. **Transparency**: Direct control over state/action/reward
3. **Container constraints**: Lightweight, fits in agent container

**Optional migration** to Stable-Baselines3 (PPO) when:
- Current Q-Learning plateaus in performance
- Need to handle higher-dimensional state spaces
- Team has reinforcement learning expertise

## Further Improvements

1. **Phase 4**: Short-horizon forecasting (EWMA/trend) to predict OOM/latency
2. **Phase 5**: Hybrid comparison against Stable-Baselines3 PPO
3. **Phase 7**: Multi-metric Pareto optimization (energy vs latency trade-off)
4. **Future**: Fine-tune reward function based on Phase 6 test results

## References

- **RL Q-Learning**: https://en.wikipedia.org/wiki/Q-learning
- **Docker resource limits**: https://docs.docker.com/config/containers/resource_constraints/
- **K6 Load Testing**: https://k6.io/docs/
- **Prometheus**: https://prometheus.io/docs/
-
