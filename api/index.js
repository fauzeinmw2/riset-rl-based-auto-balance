const express = require("express");
const { Pool } = require("pg");
const client = require("prom-client");

const app = express();

const PORT = Number(process.env.PORT || 3000);
const SERVICE_NAME = process.env.SERVICE_NAME || "api";
const REQUEST_BODY_LIMIT = process.env.REQUEST_BODY_LIMIT || "16kb";
const DB_POOL_MAX = Number(process.env.DB_POOL_MAX || 20);
const DB_QUERY_TIMEOUT_MS = Number(process.env.DB_QUERY_TIMEOUT_MS || 8000);
const DB_CONNECTION_TIMEOUT_MS = Number(process.env.DB_CONNECTION_TIMEOUT_MS || 2000);
const MAX_IN_FLIGHT_REQUESTS = Number(process.env.MAX_IN_FLIGHT_REQUESTS || 64);
const MAX_REQUEST_QUEUE = Number(process.env.MAX_REQUEST_QUEUE || 256);
const MAX_QUEUE_WAIT_MS = Number(process.env.MAX_QUEUE_WAIT_MS || 5000);
const REGISTER_MAX_CONCURRENT = Number(process.env.REGISTER_MAX_CONCURRENT || 8);
const REGISTER_MAX_QUEUE = Number(process.env.REGISTER_MAX_QUEUE || 128);
const SERVER_REQUEST_TIMEOUT_MS = Number(process.env.SERVER_REQUEST_TIMEOUT_MS || 15000);
const SERVER_HEADERS_TIMEOUT_MS = Number(process.env.SERVER_HEADERS_TIMEOUT_MS || 17000);
const SERVER_KEEP_ALIVE_TIMEOUT_MS = Number(process.env.SERVER_KEEP_ALIVE_TIMEOUT_MS || 5000);

app.use(express.json({ limit: REQUEST_BODY_LIMIT }));

let shuttingDown = false;

function parsePositiveCount(value, fallback) {
    return Number.isFinite(value) && value > 0 ? value : fallback;
}

function createHttpError(statusCode, message) {
    const error = new Error(message);
    error.statusCode = statusCode;
    return error;
}

function sendError(res, error, fallbackStatus = 500) {
    if (res.headersSent) {
        return;
    }

    const statusCode = Number(error.statusCode || fallbackStatus);
    res.status(statusCode).json({ error: error.message || "Internal server error" });
}

/* ================= DB ================= */
const pool = new Pool({
    user: process.env.DB_USER,
    host: process.env.DB_HOST,
    database: process.env.DB_NAME,
    password: process.env.DB_PASS,
    port: 5432,
    max: parsePositiveCount(DB_POOL_MAX, 20),
    idleTimeoutMillis: 10000,
    connectionTimeoutMillis: parsePositiveCount(DB_CONNECTION_TIMEOUT_MS, 2000),
    statement_timeout: parsePositiveCount(DB_QUERY_TIMEOUT_MS, 8000),
    query_timeout: parsePositiveCount(DB_QUERY_TIMEOUT_MS, 8000),
    idle_in_transaction_session_timeout: 5000,
    maxUses: 7500,
});

pool.on("error", (err) => {
    console.error("Unexpected PostgreSQL pool error:", err.message);
});

/* ================= PROMETHEUS ================= */
const register = new client.Registry();
client.collectDefaultMetrics({ register });

const httpRequestDuration = new client.Histogram({
    name: "http_request_duration_seconds",
    help: "API response time",
    labelNames: ["method", "route", "service"],
    buckets: [0.05, 0.1, 0.2, 0.5, 1, 2, 5],
});

const rejectedRequestsTotal = new client.Counter({
    name: "http_requests_rejected_total",
    help: "Rejected requests caused by overload protection",
    labelNames: ["gate", "reason"],
});

const inFlightRequestsGauge = new client.Gauge({
    name: "http_requests_in_flight",
    help: "Requests currently being processed by the API gate",
});

const queuedRequestsGauge = new client.Gauge({
    name: "http_requests_queued",
    help: "Requests waiting in the global API queue",
});

const registerInFlightGauge = new client.Gauge({
    name: "register_requests_in_flight",
    help: "Register requests currently executing",
});

const registerQueueGauge = new client.Gauge({
    name: "register_requests_queued",
    help: "Register requests waiting to execute",
});

register.registerMetric(httpRequestDuration);
register.registerMetric(rejectedRequestsTotal);
register.registerMetric(inFlightRequestsGauge);
register.registerMetric(queuedRequestsGauge);
register.registerMetric(registerInFlightGauge);
register.registerMetric(registerQueueGauge);

function createRequestGate({ name, maxConcurrent, maxQueue, maxQueueWaitMs }) {
    const pending = [];
    let active = 0;

    function updateMetrics() {
        if (name === "global") {
            inFlightRequestsGauge.set(active);
            queuedRequestsGauge.set(pending.length);
            return;
        }

        registerInFlightGauge.set(active);
        registerQueueGauge.set(pending.length);
    }

    function removePending(entry) {
        const index = pending.indexOf(entry);
        if (index >= 0) {
            pending.splice(index, 1);
            updateMetrics();
        }
    }

    function releaseNext() {
        active = Math.max(0, active - 1);

        while (pending.length > 0) {
            const entry = pending.shift();
            clearTimeout(entry.timer);

            if (entry.req.aborted || entry.res.writableEnded) {
                continue;
            }

            active += 1;
            updateMetrics();
            entry.activate();
            return;
        }

        updateMetrics();
    }

    return function gate(req, res, next) {
        if (shuttingDown) {
            rejectedRequestsTotal.inc({ gate: name, reason: "shutdown" });
            return res.status(503).json({ error: "Server is restarting" });
        }

        const startRequest = () => {
            let finished = false;
            const finalize = () => {
                if (finished) {
                    return;
                }
                finished = true;
                releaseNext();
            };

            res.on("finish", finalize);
            res.on("close", finalize);
            next();
        };

        if (active < maxConcurrent) {
            active += 1;
            updateMetrics();
            return startRequest();
        }

        if (pending.length >= maxQueue) {
            rejectedRequestsTotal.inc({ gate: name, reason: "queue_full" });
            return res.status(503).json({ error: "Server is busy, please retry" });
        }

        const entry = {
            req,
            res,
            timer: null,
            activate: startRequest,
        };

        entry.timer = setTimeout(() => {
            removePending(entry);
            if (!res.writableEnded) {
                rejectedRequestsTotal.inc({ gate: name, reason: "queue_timeout" });
                res.status(503).json({ error: "Request queue timeout, please retry" });
            }
        }, maxQueueWaitMs);

        req.on("aborted", () => removePending(entry));
        res.on("close", () => {
            if (!res.writableEnded) {
                removePending(entry);
            }
        });

        pending.push(entry);
        updateMetrics();
    };
}

const apiGate = createRequestGate({
    name: "global",
    maxConcurrent: parsePositiveCount(MAX_IN_FLIGHT_REQUESTS, 64),
    maxQueue: parsePositiveCount(MAX_REQUEST_QUEUE, 256),
    maxQueueWaitMs: parsePositiveCount(MAX_QUEUE_WAIT_MS, 5000),
});

const registerGate = createRequestGate({
    name: "register",
    maxConcurrent: parsePositiveCount(REGISTER_MAX_CONCURRENT, 8),
    maxQueue: parsePositiveCount(REGISTER_MAX_QUEUE, 128),
    maxQueueWaitMs: parsePositiveCount(MAX_QUEUE_WAIT_MS, 5000),
});

app.use("/api", apiGate);

function trackRequest(route, method) {
    return httpRequestDuration.startTimer({
        method,
        route,
        service: SERVICE_NAME,
    });
}

/* ======================================================
   1️⃣ GET Available Courses (JOIN + Aggregation)
====================================================== */
app.get("/api/courses", async (req, res) => {
    const end = trackRequest("/api/courses", "GET");

    try {
        const result = await pool.query(`
            SELECT c.id, c.code, c.name, c.sks,
                   COUNT(e.id)::int AS enrolled,
                   cl.capacity
            FROM courses c
            JOIN classes cl ON cl.course_id = c.id
            LEFT JOIN enrollments e ON e.class_id = cl.id
            GROUP BY c.id, c.code, c.name, c.sks, cl.capacity
        `);

        end();
        res.json(result.rows);
    } catch (err) {
        end();
        sendError(res, err);
    }
});

/* ======================================================
   2️⃣ POST Register Course (Heavy Validation Logic)
====================================================== */
app.post("/api/register", registerGate, async (req, res) => {
    const end = trackRequest("/api/register", "POST");
    const { student_id, class_id } = req.body;
    let dbClient;

    try {
        if (!Number.isInteger(student_id) || !Number.isInteger(class_id)) {
            throw createHttpError(400, "student_id and class_id must be integers");
        }

        dbClient = await pool.connect();
        await dbClient.query("BEGIN");

        const classResult = await dbClient.query(`
            SELECT id, capacity
            FROM classes
            WHERE id = $1
            FOR UPDATE
        `, [class_id]);

        if (classResult.rowCount === 0) {
            throw createHttpError(404, "Class not found");
        }

        const existingEnrollment = await dbClient.query(`
            SELECT 1
            FROM enrollments
            WHERE student_id = $1 AND class_id = $2
            LIMIT 1
        `, [student_id, class_id]);

        if (existingEnrollment.rowCount > 0) {
            throw createHttpError(409, "Student already registered in this class");
        }

        const enrolledResult = await dbClient.query(`
            SELECT COUNT(*)::int AS enrolled
            FROM enrollments
            WHERE class_id = $1
        `, [class_id]);

        if (enrolledResult.rows[0].enrolled >= classResult.rows[0].capacity) {
            throw createHttpError(409, "Class full");
        }

        const scheduleConflict = await dbClient.query(`
            SELECT 1
            FROM enrollments e
            JOIN classes c1 ON e.class_id = c1.id
            JOIN classes c2 ON c2.id = $1
            WHERE e.student_id = $2
              AND c1.day = c2.day
              AND c1.start_time < c2.end_time
              AND c1.end_time > c2.start_time
            LIMIT 1
        `, [class_id, student_id]);

        if (scheduleConflict.rowCount > 0) {
            throw createHttpError(409, "Schedule conflict");
        }

        const prereqCheck = await dbClient.query(`
            SELECT COUNT(*)::int AS missing_prerequisites
            FROM course_prerequisites cp
            JOIN classes cl ON cl.course_id = cp.course_id
            WHERE cl.id = $1
              AND NOT EXISTS (
                  SELECT 1
                  FROM enrollments e
                  JOIN classes taken_class ON taken_class.id = e.class_id
                  WHERE e.student_id = $2
                    AND taken_class.course_id = cp.prerequisite_id
              )
        `, [class_id, student_id]);

        if (prereqCheck.rows[0].missing_prerequisites > 0) {
            throw createHttpError(409, "Prerequisite not satisfied");
        }

        await dbClient.query(`
            INSERT INTO enrollments(student_id, class_id)
            VALUES($1, $2)
        `, [student_id, class_id]);

        await dbClient.query("COMMIT");
        end();
        res.json({ status: "registered" });
    } catch (err) {
        if (dbClient) {
            try {
                await dbClient.query("ROLLBACK");
            } catch (rollbackError) {
                console.error("Rollback failed:", rollbackError.message);
            }
        }

        end();
        sendError(res, err);
    } finally {
        if (dbClient) {
            dbClient.release();
        }
    }
});

/* ======================================================
   3️⃣ GET Student Schedule (Multi Join Heavy Query)
====================================================== */
app.get("/api/student/:id/schedule", async (req, res) => {
    const end = trackRequest("/api/student/schedule", "GET");

    try {
        const studentId = Number(req.params.id);
        if (!Number.isInteger(studentId)) {
            throw createHttpError(400, "Student id must be an integer");
        }

        const result = await pool.query(`
            SELECT s.name AS student,
                   c.name AS course,
                   cl.day, cl.start_time, cl.end_time
            FROM enrollments e
            JOIN students s ON s.id = e.student_id
            JOIN classes cl ON cl.id = e.class_id
            JOIN courses c ON c.id = cl.course_id
            WHERE s.id = $1
            ORDER BY cl.day, cl.start_time
        `, [studentId]);

        end();
        res.json(result.rows);
    } catch (err) {
        end();
        sendError(res, err);
    }
});

app.get("/health", (req, res) => {
    const overloaded = pool.waitingCount > MAX_REQUEST_QUEUE;

    if (shuttingDown || overloaded) {
        return res.status(503).json({
            status: shuttingDown ? "shutting-down" : "overloaded",
            service: SERVICE_NAME,
            db_waiting: pool.waitingCount,
            db_total: pool.totalCount,
            db_idle: pool.idleCount,
        });
    }

    return res.json({
        status: "ok",
        service: SERVICE_NAME,
        db_waiting: pool.waitingCount,
        db_total: pool.totalCount,
        db_idle: pool.idleCount,
    });
});

/* ================= Metrics Endpoint ================= */
app.get("/metrics", async (req, res) => {
    try {
        res.set("Content-Type", register.contentType);
        res.end(await register.metrics());
    } catch (err) {
        sendError(res, err);
    }
});

app.use((err, req, res, next) => {
    if (!err) {
        return next();
    }

    if (err.type === "entity.too.large") {
        return res.status(413).json({ error: "Request body too large" });
    }

    if (err.type === "entity.parse.failed") {
        return res.status(400).json({ error: "Invalid JSON payload" });
    }

    return sendError(res, err);
});

const server = app.listen(PORT, () => {
    console.log(`KRS API running on port ${PORT}`);
});

server.requestTimeout = parsePositiveCount(SERVER_REQUEST_TIMEOUT_MS, 15000);
server.headersTimeout = parsePositiveCount(SERVER_HEADERS_TIMEOUT_MS, 17000);
server.keepAliveTimeout = parsePositiveCount(SERVER_KEEP_ALIVE_TIMEOUT_MS, 5000);

async function shutdown(signal) {
    if (shuttingDown) {
        return;
    }

    shuttingDown = true;
    console.log(`Received ${signal}, starting graceful shutdown`);

    server.close(async () => {
        try {
            await pool.end();
            process.exit(0);
        } catch (err) {
            console.error("Failed to close pool cleanly:", err.message);
            process.exit(1);
        }
    });

    setTimeout(() => {
        console.error("Graceful shutdown timed out");
        process.exit(1);
    }, 10000).unref();
}

process.on("SIGTERM", () => {
    shutdown("SIGTERM").catch((err) => {
        console.error("Shutdown failure:", err.message);
        process.exit(1);
    });
});

process.on("SIGINT", () => {
    shutdown("SIGINT").catch((err) => {
        console.error("Shutdown failure:", err.message);
        process.exit(1);
    });
});

process.on("uncaughtException", (err) => {
    console.error("Uncaught exception:", err);
    shutdown("uncaughtException").catch(() => process.exit(1));
});

process.on("unhandledRejection", (reason) => {
    console.error("Unhandled rejection:", reason);
});