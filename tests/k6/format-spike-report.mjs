import fs from 'node:fs';
import path from 'node:path';

const resultsDir = path.join(process.cwd(), 'tests', 'k6', 'results');
const inputPath =
  process.argv[2] || path.join(resultsDir, 'comparison-report.json');

function round(value, digits = 2) {
  return Number.isFinite(value) ? Number(value.toFixed(digits)) : 0;
}

function formatSignedChange(value, digits = 2) {
  const rounded = round(Math.abs(value), digits);
  const label = value > 0 ? 'naik' : value < 0 ? 'turun' : 'tetap';
  return { label, value: rounded };
}

function formatText(report) {
  const cpuChange = formatSignedChange(report.comparison.cpuChange);
  const memChange = formatSignedChange(report.comparison.memChange);

  return [
    '=== Spike Test Result ===',
    `Durasi Pengujian: ${report.durationMinutes} Menit`,
    `Pola Spike: ${report.pattern.join(' -> ')}`,
    '',
    '1. API-Baseline',
    `    - CPU Usage: ${round(report.baseline.cpuPercentAvg)}% (${round(report.baseline.cpuCoreAvg)} Core)`,
    `    - RAM Usage: ${round(report.baseline.memMbAvg)} mb`,
    `    - Response Time (Avg): ${round(report.baseline.responseAvgMs)}ms`,
    `    - Request Success: ${report.baseline.successRequests} req`,
    `    - Request Error: ${report.baseline.failedRequests} req`,
    '',
    '2. API-RL',
    `    - CPU Usage: ${round(report.rl.cpuPercentAvg)}% (${round(report.rl.cpuCoreAvg)} Core)`,
    `    - RAM Usage: ${round(report.rl.memMbAvg)} mb`,
    `    - Response Time (Avg): ${round(report.rl.responseAvgMs)}ms`,
    `    - Request Success: ${report.rl.successRequests} req`,
    `    - Request Error: ${report.rl.failedRequests} req`,
    '',
    '3. Result',
    `    - Energy Consumption: ${report.comparison.energyDirection} ${round(report.comparison.energyDiffPercent)}%`,
    `        - CPU Usage ${cpuChange.label} ${cpuChange.value}% (${round(Math.abs(report.comparison.cpuCoreChange))} Core)`,
    `        - RAM Usage ${memChange.label} ${memChange.value} mb`,
    `    - Performance: ${report.comparison.performanceDirection} ${round(report.comparison.performanceDiffPercent)}%`,
  ].join('\n');
}

const report = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
console.log(formatText(report));
