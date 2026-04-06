import http from 'k6/http';
import { check, sleep } from 'k6';

const TARGET_URL = __ENV.TARGET_URL || 'http://localhost:3000';
const SEED = Number(__ENV.SEED || 42);

function lcg(seed) {
  let s = seed >>> 0;
  return function rnd() {
    s = (1664525 * s + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function randomStages(seed) {
  const rnd = lcg(seed);
  const stages = [];

  stages.push({ duration: '30s', target: 5 });
  stages.push({ duration: '30s', target: 12 });

  // Random up-down traffic waves instead of linear low->medium->high.
  for (let i = 0; i < 14; i += 1) {
    const nextVu = 3 + Math.floor(rnd() * 58); // 3..60
    const durationSec = 15 + Math.floor(rnd() * 35); // 15..49 sec
    stages.push({ duration: `${durationSec}s`, target: nextVu });
  }

  stages.push({ duration: '30s', target: 10 });
  stages.push({ duration: '30s', target: 0 });
  return stages;
}

export const options = {
  stages: randomStages(SEED),
  thresholds: {
    http_req_duration: ['p(95)<1200'],
    http_req_failed: ['rate<0.08'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

function randomInt(maxExclusive) {
  return Math.floor(Math.random() * maxExclusive);
}

export default function () {
  const pick = Math.random();

  if (pick < 0.45) {
    const res = http.get(`${TARGET_URL}/api/courses`);
    check(res, {
      'courses status 200': (r) => r.status === 200,
      'courses rt < 1.2s': (r) => r.timings.duration < 1200,
    });
  } else if (pick < 0.8) {
    const sid = 1 + randomInt(350);
    const res = http.get(`${TARGET_URL}/api/student/${sid}/schedule`);
    check(res, {
      'schedule status 200': (r) => r.status === 200,
      'schedule rt < 1.2s': (r) => r.timings.duration < 1200,
    });
  } else {
    const payload = JSON.stringify({
      student_id: 1 + randomInt(350),
      class_id: 1 + randomInt(24),
    });
    const res = http.post(`${TARGET_URL}/api/register`, payload, {
      headers: { 'Content-Type': 'application/json' },
      // Domain rule: 400 can be valid (class full/prerequisite/schedule conflict),
      // so it should not be counted as failed HTTP request in k6 aggregated metric.
      responseCallback: http.expectedStatuses(200, 400),
    });
    check(res, {
      'register status 200/400': (r) => r.status === 200 || r.status === 400,
      'register rt < 1.2s': (r) => r.timings.duration < 1200,
    });
  }

  sleep(0.2 + Math.random() * 0.8);
}
