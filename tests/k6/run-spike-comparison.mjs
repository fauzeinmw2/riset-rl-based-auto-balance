import fs from 'node:fs';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';

const rootDir = process.cwd();
const resultsDir = path.join(rootDir, 'tests', 'k6', 'results');
fs.mkdirSync(resultsDir, { recursive: true });

const durationMinutes = Number(process.env.TEST_DURATION_MINUTES || 10);
const prometheusBaseUrl = process.env.PROMETHEUS_URL || 'http://localhost:9090';
const maxCpuCores = Number(process.env.MAX_CPU_CORES || 1);
const maxMemoryMb = Number(process.env.MAX_MEMORY_MB || 512);
const serviceTargets = [
  {
    key: 'api-baseline',
    title: 'API-Baseline',
    baseUrl: process.env.BASELINE_URL || 'http://localhost:3000',
    containerName: process.env.BASELINE_CONTAINER || 'api-baseline',
  },
  {
    key: 'api-rl',
    title: 'API-RL',
    baseUrl: process.env.RL_URL || 'http://localhost:3002',
    containerName: process.env.RL_CONTAINER || 'api-rl',
  },
];

const spikePatterns = [
  ['low', 'high', 'medium'],
  ['high', 'low', 'medium'],
];

const profileTargets = {
  low: Number(process.env.LOW_VUS || 10),
  medium: Number(process.env.MEDIUM_VUS || 25),
  high: Number(process.env.HIGH_VUS || 60),
};

function choosePattern() {
  const requested = process.env.SPIKE_PATTERN;
  if (!requested || requested === 'random') {
    return spikePatterns[Math.floor(Math.random() * spikePatterns.length)];
  }

  const parts = requested.split('-').map((part) => part.trim().toLowerCase());
  if (parts.length !== 3 || parts.some((part) => !(part in profileTargets))) {
    throw new Error(
      'SPIKE_PATTERN harus berformat seperti "low-high-medium" atau "high-low-medium".'
    );
  }
  return parts;
}

function buildStages(pattern, totalMinutes) {
  const cooldownMinutes = 1;
  const activeMinutes = totalMinutes - cooldownMinutes;
  const perStageMinutes = Math.max(1, Math.floor(activeMinutes / pattern.length));
  const remainder = Math.max(0, activeMinutes - perStageMinutes * pattern.length);

  const stages = pattern.map((level, index) => ({
    duration: `${perStageMinutes + (index < remainder ? 1 : 0)}m`,
    target: profileTargets[level],
    label: level,
  }));

  stages.push({ duration: `${cooldownMinutes}m`, target: 0, label: 'cooldown' });
  return stages;
}

function runK6(service, stages) {
  const summaryPath = path.join(resultsDir, `${service.key}-summary.json`);
  const metaPath = path.join(resultsDir, `${service.key}-meta.json`);

  const startEpochMs = Date.now();
  const k6Run = spawnSync(
    'k6',
    [
      'run',
      '--summary-export',
      summaryPath,
      path.join('tests', 'k6', 'spike-test.js'),
    ],
    {
      cwd: rootDir,
      stdio: 'inherit',
      env: {
        ...process.env,
        BASE_URL: service.baseUrl,
        SERVICE_NAME: service.key,
        STAGES_JSON: JSON.stringify(
          stages.map((stage) => ({ duration: stage.duration, target: stage.target }))
        ),
      },
    }
  );
  const endEpochMs = Date.now();

  if (k6Run.error) {
    throw k6Run.error;
  }

  const exitCode = k6Run.status ?? 0;
  const thresholdsBreached = exitCode === 99;
  if (exitCode !== 0 && !thresholdsBreached) {
    throw new Error(`k6 gagal untuk ${service.title} dengan exit code ${exitCode}`);
  }

  const meta = {
    service: service.key,
    title: service.title,
    baseUrl: service.baseUrl,
    containerName: service.containerName,
    startEpochMs,
    endEpochMs,
    startEpochSec: Math.floor(startEpochMs / 1000),
    endEpochSec: Math.ceil(endEpochMs / 1000),
    durationSeconds: Math.max(1, Math.round((endEpochMs - startEpochMs) / 1000)),
    k6ExitCode: exitCode,
    thresholdsBreached,
  };

  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2));
  return { summaryPath, metaPath, meta };
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function queryPrometheusRange(query, startSec, endSec, step = '15s') {
  const url = new URL('/api/v1/query_range', prometheusBaseUrl);
  url.searchParams.set('query', query);
  url.searchParams.set('start', String(startSec));
  url.searchParams.set('end', String(endSec));
  url.searchParams.set('step', step);

  const output = execFileSync('curl', ['-sS', url.toString()], {
    cwd: rootDir,
    encoding: 'utf8',
  });

  const parsed = JSON.parse(output);
  return parsed?.data?.result ?? [];
}

function inspectContainer(containerName) {
  try {
    const output = execFileSync('docker', ['inspect', containerName], {
      cwd: rootDir,
      encoding: 'utf8',
    });
    const [info] = JSON.parse(output);
    return {
      restartCount: Number(info?.RestartCount || 0),
      oomKilled: Boolean(info?.State?.OOMKilled),
      status: info?.State?.Status || 'unknown',
      exitCode: Number(info?.State?.ExitCode ?? 0),
    };
  } catch {
    return {
      restartCount: 0,
      oomKilled: false,
      status: 'unknown',
      exitCode: 0,
    };
  }
}

function averageRangeValues(resultSet) {
  const values = [];
  for (const series of resultSet) {
    for (const point of series.values || []) {
      const numeric = Number(point[1]);
      if (Number.isFinite(numeric)) {
        values.push(numeric);
      }
    }
  }

  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function collectPrometheusMetrics(serviceMeta) {
  const container = serviceMeta.containerName;
  const responseQuery = `avg_over_time(http_request_duration_seconds_sum{service="${serviceMeta.service}"}[1m]) / clamp_min(avg_over_time(http_request_duration_seconds_count{service="${serviceMeta.service}"}[1m]), 0.0001) * 1000`;
  const cpuQuery = `rate(container_cpu_usage_seconds_total{name="${container}"}[1m]) * 100`;
  const memQuery = `container_memory_usage_bytes{name="${container}"} / 1024 / 1024`;

  const responseAvg = averageRangeValues(
    queryPrometheusRange(responseQuery, serviceMeta.startEpochSec, serviceMeta.endEpochSec)
  );
  const cpuPercentAvg = averageRangeValues(
    queryPrometheusRange(cpuQuery, serviceMeta.startEpochSec, serviceMeta.endEpochSec)
  );
  const memMbAvg = averageRangeValues(
    queryPrometheusRange(memQuery, serviceMeta.startEpochSec, serviceMeta.endEpochSec)
  );

  return {
    cpuPercentAvg,
    cpuCoreAvg: (cpuPercentAvg / 100) * maxCpuCores,
    memMbAvg,
    responseAvgMs: responseAvg,
  };
}

function buildServiceResult(service, summary, meta) {
  const metrics = collectPrometheusMetrics(meta);
  const containerInfo = inspectContainer(service.containerName);
  const totalRequests = summary.metrics.http_reqs?.count || 0;
  const failedRequests =
    summary.metrics.http_req_failed?.fails ??
    Math.round(totalRequests * (summary.metrics.http_req_failed?.value || 0));
  const successRequests = Math.max(0, totalRequests - failedRequests);

  return {
    key: service.key,
    title: service.title,
    totalRequests,
    successRequests,
    failedRequests,
    responseAvgMs: metrics.responseAvgMs || summary.metrics.http_req_duration?.avg || 0,
    cpuPercentAvg: metrics.cpuPercentAvg,
    cpuCoreAvg: metrics.cpuCoreAvg,
    memMbAvg: metrics.memMbAvg,
    thresholdsBreached: Boolean(meta.thresholdsBreached),
    k6ExitCode: meta.k6ExitCode,
    containerRestartCount: containerInfo.restartCount,
    containerOOMKilled: containerInfo.oomKilled,
    containerStatus: containerInfo.status,
    containerExitCode: containerInfo.exitCode,
  };
}

function round(value, digits = 2) {
  return Number.isFinite(value) ? Number(value.toFixed(digits)) : 0;
}

function formatSignedChange(value, digits = 2) {
  const rounded = round(Math.abs(value), digits);
  const label = value > 0 ? 'naik' : value < 0 ? 'turun' : 'tetap';
  return { label, value: rounded };
}

function compareResults(baseline, rl) {
  const baselineEnergyScore =
    baseline.cpuPercentAvg + (baseline.memMbAvg / maxMemoryMb) * 100;
  const rlEnergyScore = rl.cpuPercentAvg + (rl.memMbAvg / maxMemoryMb) * 100;
  const energyDiffPercent =
    baselineEnergyScore === 0
      ? 0
      : ((baselineEnergyScore - rlEnergyScore) / baselineEnergyScore) * 100;

  const rtDiffPercent =
    baseline.responseAvgMs === 0
      ? 0
      : ((baseline.responseAvgMs - rl.responseAvgMs) / baseline.responseAvgMs) * 100;

  return {
    energyDirection: energyDiffPercent >= 0 ? 'Hemat' : 'Boros',
    energyDiffPercent: Math.abs(round(energyDiffPercent)),
    cpuChange: baseline.cpuPercentAvg - rl.cpuPercentAvg,
    cpuCoreChange: baseline.cpuCoreAvg - rl.cpuCoreAvg,
    memChange: baseline.memMbAvg - rl.memMbAvg,
    performanceDirection: rtDiffPercent >= 0 ? 'Naik' : 'Turun',
    performanceDiffPercent: Math.abs(round(rtDiffPercent)),
  };
}

function formatResultText(durationMinutesValue, pattern, baseline, rl, comparison) {
  const cpuChange = formatSignedChange(comparison.cpuChange);
  const cpuCoreChange = round(Math.abs(comparison.cpuCoreChange));
  const memChange = formatSignedChange(comparison.memChange);

  return [
    '=== Spike Test Result ===',
    `Durasi Pengujian: ${durationMinutesValue} Menit`,
    `Pola Spike: ${pattern.join(' -> ')}`,
    '',
    '1. API-Baseline',
    `    - CPU Usage: ${round(baseline.cpuPercentAvg)}% (${round(baseline.cpuCoreAvg)} Core)`,
    `    - RAM Usage: ${round(baseline.memMbAvg)} mb`,
    `    - Response Time (Avg): ${round(baseline.responseAvgMs)}ms`,
    `    - Request Success: ${baseline.successRequests} req`,
    `    - Request Error: ${baseline.failedRequests} req`,
    `    - Threshold Status: ${baseline.thresholdsBreached ? 'Threshold crossed' : 'OK'}`,
    `    - Container Restart: ${baseline.containerRestartCount}x | OOMKilled: ${baseline.containerOOMKilled ? 'Yes' : 'No'}`,
    '',
    '2. API-RL',
    `    - CPU Usage: ${round(rl.cpuPercentAvg)}% (${round(rl.cpuCoreAvg)} Core)`,
    `    - RAM Usage: ${round(rl.memMbAvg)} mb`,
    `    - Response Time (Avg): ${round(rl.responseAvgMs)}ms`,
    `    - Request Success: ${rl.successRequests} req`,
    `    - Request Error: ${rl.failedRequests} req`,
    `    - Threshold Status: ${rl.thresholdsBreached ? 'Threshold crossed' : 'OK'}`,
    `    - Container Restart: ${rl.containerRestartCount}x | OOMKilled: ${rl.containerOOMKilled ? 'Yes' : 'No'}`,
    '',
    '3. Result',
    `    - Energy Consumption: ${comparison.energyDirection} ${comparison.energyDiffPercent}%`,
    `        - CPU Usage ${cpuChange.label} ${cpuChange.value}% (${cpuCoreChange} Core)`,
    `        - RAM Usage ${memChange.label} ${memChange.value} mb`,
    `    - Performance: ${comparison.performanceDirection} ${comparison.performanceDiffPercent}%`,
    '',
  ].join('\n');
}

function main() {
  const pattern = choosePattern();
  const stages = buildStages(pattern, durationMinutes);

  console.log(`Pattern spike terpilih: ${pattern.join(' -> ')}`);
  console.log(`Durasi total pengujian: ${durationMinutes} menit\n`);

  const runArtifacts = serviceTargets.map((service) => {
    console.log(`Menjalankan spike test untuk ${service.title} (${service.baseUrl})`);
    const artifact = runK6(service, stages);
    if (artifact.meta.thresholdsBreached) {
      console.log(
        `Threshold K6 terlewati pada ${service.title}, tetapi hasil tetap disimpan dan pengujian dilanjutkan.\n`
      );
    }
    return { service, ...artifact };
  });

  const serviceResults = runArtifacts.map(({ service, summaryPath, meta }) =>
    buildServiceResult(service, readJson(summaryPath), meta)
  );

  const baseline = serviceResults.find((item) => item.key === 'api-baseline');
  const rl = serviceResults.find((item) => item.key === 'api-rl');
  const comparison = compareResults(baseline, rl);
  const textReport = formatResultText(durationMinutes, pattern, baseline, rl, comparison);

  fs.writeFileSync(path.join(resultsDir, 'comparison-report.txt'), textReport);
  fs.writeFileSync(
    path.join(resultsDir, 'comparison-report.json'),
    JSON.stringify(
      {
        durationMinutes,
        pattern,
        stages,
        baseline,
        rl,
        comparison,
      },
      null,
      2
    )
  );

  console.log(textReport);
  console.log(`Resume hasil tersimpan di ${path.join('tests', 'k6', 'results')}`);
}

main();
