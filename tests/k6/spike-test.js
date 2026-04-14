import http from 'k6/http';
import { check, sleep } from 'k6';

const baseUrl = __ENV.BASE_URL || 'http://localhost:3000';
const serviceName = __ENV.SERVICE_NAME || 'api-service';
const stageProfile = __ENV.STAGES_JSON
  ? JSON.parse(__ENV.STAGES_JSON)
  : [
      { duration: '3m', target: 10 },
      { duration: '3m', target: 60 },
      { duration: '3m', target: 25 },
      { duration: '1m', target: 0 },
    ];

export const options = {
  stages: stageProfile,
  thresholds: {
    http_req_failed: ['rate<0.10'],
    http_req_duration: ['p(95)<2000'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)'],
};

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function pickRequest() {
  const roll = Math.random();

  if (roll < 0.45) {
    return {
      name: 'list_courses',
      method: 'GET',
      url: `${baseUrl}/api/courses`,
      params: { tags: { endpoint: 'courses', service: serviceName } },
    };
  }

  if (roll < 0.75) {
    const studentId = randomInt(1, 25);
    return {
      name: 'student_schedule',
      method: 'GET',
      url: `${baseUrl}/api/student/${studentId}/schedule`,
      params: { tags: { endpoint: 'student_schedule', service: serviceName } },
    };
  }

  return {
    name: 'register_course',
    method: 'POST',
    url: `${baseUrl}/api/register`,
    body: JSON.stringify({
      student_id: randomInt(1, 25),
      class_id: randomInt(1, 10),
    }),
    params: {
      headers: { 'Content-Type': 'application/json' },
      tags: { endpoint: 'register', service: serviceName },
    },
  };
}

export default function () {
  const request = pickRequest();

  let response;
  if (request.method === 'GET') {
    response = http.get(request.url, request.params);
  } else {
    response = http.post(request.url, request.body, request.params);
  }

  check(response, {
    'status is acceptable': (res) => res.status >= 200 && res.status < 500,
  });

  sleep(Math.random() * 1.5 + 0.2);
}
