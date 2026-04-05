import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Rate, Counter, Histogram, Trend } from 'k6/metrics';

// ============================================================
// CUSTOM METRICS
// ============================================================
const baselineErrorRate = new Rate('baseline_errors');
const baselineSuccessRate = new Rate('baseline_success');
const baselineReqDuration = new Trend('baseline_request_duration');

const rlErrorRate = new Rate('rl_errors');
const rlSuccessRate = new Rate('rl_success');
const rlReqDuration = new Trend('rl_request_duration');

const baselineReqCount = new Counter('baseline_requests');
const rlReqCount = new Counter('rl_requests');

// ============================================================
// TEST CONFIG
// ============================================================
const BASELINE_URL = `http://${__ENV.BASELINE_API || 'api-baseline:3000'}`;
const RL_URL = `http://${__ENV.RL_API || 'api-rl:3000'}`;

export const options = {
  scenarios: {
    load_test: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        // Warm-up (low traffic)
        { duration: '2m', target: 20 },
        
        // Normal load
        { duration: '3m', target: 50 },
        
        // Medium spike
        { duration: '2m', target: 100 },
        
        // High spike (production-like spike)
        { duration: '2m', target: 200 },
        
        // Peak spike (extreme condition)
        { duration: '1m', target: 300 },
        
        // Sustained spike
        { duration: '2m', target: 300 },
        
        // Cool down
        { duration: '2m', target: 50 },
        
        // Cool down more
        { duration: '1m', target: 0 },
      ],
      gracefulStop: '30s',
    },
  },
  
  // Thresholds untuk alerting
  thresholds: {
    'baseline_request_duration': ['p(95)<500', 'p(99)<2000'],
    'rl_request_duration': ['p(95)<500', 'p(99)<2000'],
  },
};

// ============================================================
// HELPER FUNCTIONS
// ============================================================
function makeBaselineRequest(endpoint) {
  const url = `${BASELINE_URL}${endpoint}`;
  const res = http.get(url, { tags: { service: 'baseline' } });
  
  const success = res.status === 200;
  baselineSuccessRate.add(success);
  baselineErrorRate.add(!success);
  baselineReqDuration.add(res.timings.duration);
  baselineReqCount.add(1);
  
  return res;
}

function makeRLRequest(endpoint) {
  const url = `${RL_URL}${endpoint}`;
  const res = http.get(url, { tags: { service: 'rl' } });
  
  const success = res.status === 200;
  rlSuccessRate.add(success);
  rlErrorRate.add(!success);
  rlReqDuration.add(res.timings.duration);
  rlReqCount.add(1);
  
  return res;
}

function makeBaselineRegisterRequest(studentId, classId) {
  const url = `${BASELINE_URL}/api/register`;
  const body = JSON.stringify({ student_id: studentId, class_id: classId });
  const res = http.post(url, body, {
    headers: { 'Content-Type': 'application/json' },
    tags: { service: 'baseline' }
  });
  
  const success = res.status === 200;
  baselineSuccessRate.add(success);
  baselineErrorRate.add(!success);
  baselineReqDuration.add(res.timings.duration);
  baselineReqCount.add(1);
  
  return res;
}

function makeRLRegisterRequest(studentId, classId) {
  const url = `${RL_URL}/api/register`;
  const body = JSON.stringify({ student_id: studentId, class_id: classId });
  const res = http.post(url, body, {
    headers: { 'Content-Type': 'application/json' },
    tags: { service: 'rl' }
  });
  
  const success = res.status === 200;
  rlSuccessRate.add(success);
  rlErrorRate.add(!success);
  rlReqDuration.add(res.timings.duration);
  rlReqCount.add(1);
  
  return res;
}

// ============================================================
// MAIN VU SCRIPT
// ============================================================
export default function () {
  // Random student and class ID for register attempts
  const studentId = Math.floor(Math.random() * 100) + 1;
  const classId = Math.floor(Math.random() * 30) + 1;
  
  // Distribute traffic across endpoints
  const endpoint = __VU % 3;
  
  group('Baseline API', () => {
    switch (endpoint) {
      case 0:
        makeBaselineRequest('/api/courses');
        break;
      case 1:
        makeBaselineRequest(`/api/student/${studentId}/schedule`);
        break;
      case 2:
        makeBaselineRegisterRequest(studentId, classId);
        break;
    }
  });
  
  group('RL-Controlled API', () => {
    switch (endpoint) {
      case 0:
        makeRLRequest('/api/courses');
        break;
      case 1:
        makeRLRequest(`/api/student/${studentId}/schedule`);
        break;
      case 2:
        makeRLRegisterRequest(studentId, classId);
        break;
    }
  });
  
  // Small delay between requests
  sleep(1);
}

// ============================================================
// TEARDOWN: Log test completion
// ============================================================
export function teardown(data) {
  console.log('✅ Load test completed!');
}
