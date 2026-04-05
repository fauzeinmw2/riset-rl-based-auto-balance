const express = require("express");
const { Pool } = require("pg");
const client = require("prom-client");

const app = express();
app.use(express.json());

/* ================= DB ================= */
const pool = new Pool({
    user: process.env.DB_USER,
    host: process.env.DB_HOST,
    database: process.env.DB_NAME,
    password: process.env.DB_PASS,
    port: 5432,
    max: 50,
    idleTimeoutMillis: 30000,
});

/* ================= PROMETHEUS ================= */
const register = new client.Registry();
client.collectDefaultMetrics({ register });

const httpRequestDuration = new client.Histogram({
    name: "http_request_duration_seconds",
    help: "API response time in seconds",
    labelNames: ["method", "route", "service", "status"],
    buckets: [0.01, 0.05, 0.1, 0.2, 0.5, 1, 2, 5]
});

const httpRequestsTotal = new client.Counter({
    name: "http_requests_total",
    help: "Total HTTP requests",
    labelNames: ["method", "route", "service", "status"]
});

const dbConnectionPoolSize = new client.Gauge({
    name: "db_connection_pool_size",
    help: "Database connection pool size",
    labelNames: ["service"]
});

register.registerMetric(httpRequestDuration);
register.registerMetric(httpRequestsTotal);
register.registerMetric(dbConnectionPoolSize);

const SERVICE_NAME = process.env.SERVICE_NAME || "api-node";

// Update pool size metric every 10s
setInterval(() => {
    dbConnectionPoolSize.set({ service: SERVICE_NAME }, pool.totalCount);
}, 10000);

/* ================= MIDDLEWARE: Metrics Recorder ================= */
app.use((req, res, next) => {
    const timer = httpRequestDuration.startTimer({
        method: req.method,
        route: `${req.method} ${req.path.split('/')[1] || 'root'}`,
        service: SERVICE_NAME
    });

    res.on('finish', () => {
        timer({ status: res.statusCode });
        httpRequestsTotal
            .labels(req.method, `${req.method} ${req.path.split('/')[1] || 'root'}`, SERVICE_NAME, res.statusCode)
            .inc();
    });

    next();
});

/* ================= ENDPOINTS ================= */

// Health check
app.get("/health", (req, res) => {
    res.json({ status: "ok", service: SERVICE_NAME });
});

// Get all courses (simple read query)
app.get("/api/courses", async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT c.id, c.code, c.name, c.sks,
                   COUNT(e.id) as enrolled,
                   cl.capacity
            FROM courses c
            JOIN classes cl ON cl.course_id = c.id
            LEFT JOIN enrollments e ON e.class_id = cl.id
            GROUP BY c.id, c.code, c.name, c.sks, cl.capacity
            LIMIT 100
        `);
        res.json(result.rows);
    } catch (err) {
        console.error("Error:", err);
        res.status(500).json({ error: err.message });
    }
});

// Get all classes with enrollment info (moderate query)
app.get("/api/classes", async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT cl.id, cl.code, c.code as course_code, c.name as course_name,
                   cl.day, cl.start_time, cl.end_time, cl.capacity,
                   COUNT(e.id) as enrolled
            FROM classes cl
            JOIN courses c ON cl.course_id = c.id
            LEFT JOIN enrollments e ON e.class_id = cl.id
            GROUP BY cl.id, cl.code, c.code, c.name, cl.day, cl.start_time, cl.end_time, cl.capacity
            ORDER BY cl.day, cl.start_time
            LIMIT 100
        `);
        res.json(result.rows);
    } catch (err) {
        console.error("Error:", err);
        res.status(500).json({ error: err.message });
    }
});

// Get student schedule by ID (heavy join + complex query)
app.get("/api/student/:id/schedule", async (req, res) => {
    try {
        const studentId = parseInt(req.params.id);
        
        const result = await pool.query(`
            SELECT s.id, s.name as student, s.nim,
                   c.code as course_code, c.name as course_name, c.sks,
                   cl.day, cl.start_time, cl.end_time, cl.capacity,
                   e.enrollment_date
            FROM enrollments e
            JOIN students s ON s.id = e.student_id
            JOIN classes cl ON cl.id = e.class_id
            JOIN courses c ON c.id = cl.course_id
            WHERE s.id = $1
            ORDER BY cl.day, cl.start_time
        `, [studentId]);

        if (result.rows.length === 0) {
            return res.status(404).json({ error: "Student not found" });
        }

        res.json(result.rows);
    } catch (err) {
        console.error("Error:", err);
        res.status(500).json({ error: err.message });
    }
});

// Register student to a class (write transaction + validations)
app.post("/api/register", async (req, res) => {
    const { student_id, class_id } = req.body;

    if (!student_id || !class_id) {
        return res.status(400).json({ error: "student_id and class_id required" });
    }

    try {
        await pool.query("BEGIN");

        // Check capacity
        const capCheck = await pool.query(`
            SELECT capacity,
                   (SELECT COUNT(*) FROM enrollments WHERE class_id=$1) as enrolled
            FROM classes WHERE id=$1
        `, [class_id]);

        if (capCheck.rows.length === 0) {
            await pool.query("ROLLBACK");
            return res.status(404).json({ error: "Class not found" });
        }

        const { capacity, enrolled } = capCheck.rows[0];
        if (enrolled >= capacity) {
            await pool.query("ROLLBACK");
            return res.status(400).json({ error: "Class is full" });
        }

        // Check schedule conflict
        const scheduleConflict = await pool.query(`
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

        if (scheduleConflict.rows.length > 0) {
            await pool.query("ROLLBACK");
            return res.status(400).json({ error: "Schedule conflict detected" });
        }

        // Check prerequisite
        const prereqCheck = await pool.query(`
            SELECT cp.prerequisite_id
            FROM course_prerequisites cp
            JOIN classes cl ON cl.course_id = cp.course_id
            WHERE cl.id = $1
        `, [class_id]);

        for (const row of prereqCheck.rows) {
            const passed = await pool.query(`
                SELECT 1 FROM enrollments e
                JOIN classes c ON e.class_id = c.id
                WHERE e.student_id = $1 AND c.course_id = $2
                LIMIT 1
            `, [student_id, row.prerequisite_id]);

            if (passed.rows.length === 0) {
                await pool.query("ROLLBACK");
                return res.status(400).json({ error: `Prerequisite course required` });
            }
        }

        // Insert enrollment
        await pool.query(`
            INSERT INTO enrollments(student_id, class_id)
            VALUES($1, $2)
        `, [student_id, class_id]);

        await pool.query("COMMIT");
        res.json({ status: "registered", student_id, class_id });

    } catch (err) {
        await pool.query("ROLLBACK").catch(() => {});
        console.error("Error:", err);
        res.status(500).json({ error: err.message });
    }
});

// Get all students (simple read)
app.get("/api/students", async (req, res) => {
    try {
        const result = await pool.query(`
            SELECT id, name, nim, email
            FROM students
            ORDER BY nim
            LIMIT 100
        `);
        res.json(result.rows);
    } catch (err) {
        console.error("Error:", err);
        res.status(500).json({ error: err.message });
    }
});

/* ================= Metrics Endpoint ================= */
app.get("/metrics", async (req, res) => {
    res.set("Content-Type", register.contentType);
    res.end(await register.metrics());
});

/* ================= SERVER ================= */
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`✅ KRS API (Node.js) [${SERVICE_NAME}] running on port ${PORT}`);
});

// Graceful shutdown
process.on('SIGTERM', () => {
    console.log("SIGTERM received, closing server...");
    pool.end();
    process.exit(0);
});
