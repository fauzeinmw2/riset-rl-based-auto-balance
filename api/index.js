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
});

/* ================= PROMETHEUS ================= */
const register = new client.Registry();
client.collectDefaultMetrics({ register });

const httpRequestDuration = new client.Histogram({
    name: "http_request_duration_seconds",
    help: "API response time",
    labelNames: ["method", "route", "service"],
    buckets: [0.05, 0.1, 0.2, 0.5, 1, 2, 5]
});

register.registerMetric(httpRequestDuration);

/* ======================================================
   1️⃣ GET Available Courses (JOIN + Aggregation)
====================================================== */
app.get("/api/courses", async (req, res) => {
    const end = httpRequestDuration.startTimer({
        method: "GET",
        route: "/api/courses",
        service: process.env.SERVICE_NAME
    });

    try {
        const result = await pool.query(`
            SELECT c.id, c.code, c.name, c.sks,
                   COUNT(e.id) as enrolled,
                   cl.capacity
            FROM courses c
            JOIN classes cl ON cl.course_id = c.id
            LEFT JOIN enrollments e ON e.class_id = cl.id
            GROUP BY c.id, cl.capacity
        `);

        end();
        res.json(result.rows);
    } catch (err) {
        end();
        res.status(500).json({ error: err.message });
    }
});

/* ======================================================
   2️⃣ POST Register Course (Heavy Validation Logic)
====================================================== */
app.post("/api/register", async (req, res) => {
    const end = httpRequestDuration.startTimer({
        method: "POST",
        route: "/api/register",
    });

    const { student_id, class_id } = req.body;

    try {
        await pool.query("BEGIN");

        // 1. Check capacity
        const capCheck = await pool.query(`
            SELECT capacity,
                   (SELECT COUNT(*) FROM enrollments WHERE class_id=$1) as enrolled
            FROM classes WHERE id=$1
        `, [class_id]);

        if (capCheck.rows[0].enrolled >= capCheck.rows[0].capacity) {
            throw new Error("Class full");
        }

        // 2. Check schedule conflict
        const scheduleConflict = await pool.query(`
            SELECT 1
            FROM enrollments e
            JOIN classes c1 ON e.class_id = c1.id
            JOIN classes c2 ON c2.id = $1
            WHERE e.student_id=$2
              AND c1.day = c2.day
              AND c1.start_time < c2.end_time
              AND c1.end_time > c2.start_time
        `, [class_id, student_id]);

        if (scheduleConflict.rowCount > 0) {
            throw new Error("Schedule conflict");
        }

        // 3. Check prerequisite
        const prereqCheck = await pool.query(`
            SELECT prerequisite_id
            FROM course_prerequisites cp
            JOIN classes cl ON cl.course_id = cp.course_id
            WHERE cl.id=$1
        `, [class_id]);

        for (let row of prereqCheck.rows) {
            const passed = await pool.query(`
                SELECT 1 FROM enrollments e
                JOIN classes c ON e.class_id = c.id
                WHERE e.student_id=$1 AND c.course_id=$2
            `, [student_id, row.prerequisite_id]);

            if (passed.rowCount === 0) {
                throw new Error("Prerequisite not satisfied");
            }
        }

        // 4. Insert enrollment
        await pool.query(`
            INSERT INTO enrollments(student_id, class_id)
            VALUES($1,$2)
        `, [student_id, class_id]);

        await pool.query("COMMIT");

        end();
        res.json({ status: "registered" });

    } catch (err) {
        await pool.query("ROLLBACK");
        end();
        res.status(400).json({ error: err.message });
    }
});

/* ======================================================
   3️⃣ GET Student Schedule (Multi Join Heavy Query)
====================================================== */
app.get("/api/student/:id/schedule", async (req, res) => {
    const end = httpRequestDuration.startTimer({
        method: "GET",
        route: "/api/student/schedule",
    });

    try {
        const result = await pool.query(`
            SELECT s.name as student,
                   c.name as course,
                   cl.day, cl.start_time, cl.end_time
            FROM enrollments e
            JOIN students s ON s.id=e.student_id
            JOIN classes cl ON cl.id=e.class_id
            JOIN courses c ON c.id=cl.course_id
            WHERE s.id=$1
            ORDER BY cl.day, cl.start_time
        `, [req.params.id]);

        end();
        res.json(result.rows);

    } catch (err) {
        end();
        res.status(500).json({ error: err.message });
    }
});

/* ================= Metrics Endpoint ================= */
app.get("/metrics", async (req, res) => {
    res.set("Content-Type", register.contentType);
    res.end(await register.metrics());
});

app.listen(3000, () => {
    console.log("KRS API running on port 3000");
});
