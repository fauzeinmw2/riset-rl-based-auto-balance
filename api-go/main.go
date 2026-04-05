package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	_ "github.com/lib/pq"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// ================= PROMETHEUS (Global) =================
// Cukup deklarasikan SATU KALI di sini
var httpRequestDuration = prometheus.NewHistogramVec(
	prometheus.HistogramOpts{
		Name:    "http_request_duration_seconds",
		Help:    "API response time",
		Buckets: []float64{0.05, 0.1, 0.2, 0.5, 1, 2, 5},
	},
	[]string{"method", "route", "service"},
)

var db *sql.DB

func init() {
	// Daftarkan metrik saat aplikasi pertama kali dimuat
	prometheus.MustRegister(httpRequestDuration)
}

func main() {
	var err error
	
	// Konfigurasi DB dari Environment Variables
	dbUser := os.Getenv("DB_USER")
	dbPass := os.Getenv("DB_PASS")
	dbHost := os.Getenv("DB_HOST")
	dbName := os.Getenv("DB_NAME")
	connStr := fmt.Sprintf("user=%s password=%s host=%s port=5432 dbname=%s sslmode=disable",
		dbUser, dbPass, dbHost, dbName)

	db, err = sql.Open("postgres", connStr)
	if err != nil {
		log.Fatal("Gagal koneksi DB:", err)
	}
	defer db.Close()

	// Optimasi koneksi untuk pengujian beban (JMeter)
	db.SetMaxOpenConns(50)
	db.SetMaxIdleConns(25)
	db.SetConnMaxIdleTime(5 * time.Minute)

	// Routing
	mux := http.NewServeMux()
	mux.HandleFunc("/api/courses", metricMiddleware("GET", "/api/courses", getCourses))
	mux.HandleFunc("/api/register", metricMiddleware("POST", "/api/register", registerCourse))
	mux.HandleFunc("/api/student/", metricMiddleware("GET", "/api/student/schedule", getStudentSchedule))
	
	// Endpoint khusus untuk di-scrape oleh Prometheus
	mux.Handle("/metrics", promhttp.Handler())

	port := "3000"
	fmt.Printf("KRS API (Golang) running on port %s\n", port)
	
	server := &http.Server{
		Addr:         ":" + port,
		Handler:      mux,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	log.Fatal(server.ListenAndServe())
}

// ================= MIDDLEWARE =================
func metricMiddleware(method, route string, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != method {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		serviceName := os.Getenv("SERVICE_NAME")
		if serviceName == "" {
			serviceName = "api-go-service"
		}

		// Mulai timer metrik
		timer := prometheus.NewTimer(httpRequestDuration.WithLabelValues(method, route, serviceName))
		defer timer.ObserveDuration()

		next(w, r)
	}
}

// ================= HANDLERS (Logika Utama) =================

func getCourses(w http.ResponseWriter, r *http.Request) {
	rows, err := db.Query(`
		SELECT c.id, c.code, c.name, c.sks, COUNT(e.id) as enrolled, cl.capacity
		FROM courses c
		JOIN classes cl ON cl.course_id = c.id
		LEFT JOIN enrollments e ON e.class_id = cl.id
		GROUP BY c.id, cl.id, cl.capacity
	`)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	var courses []map[string]interface{}
	for rows.Next() {
		var id, sks, enrolled, capacity int
		var code, name string
		rows.Scan(&id, &code, &name, &sks, &enrolled, &capacity)
		courses = append(courses, map[string]interface{}{
			"id": id, "code": code, "name": name, "sks": sks, "enrolled": enrolled, "capacity": capacity,
		})
	}
	renderJSON(w, courses)
}

func registerCourse(w http.ResponseWriter, r *http.Request) {
	var req struct {
		StudentID int `json:"student_id"`
		ClassID   int `json:"class_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	tx, _ := db.Begin()
	defer tx.Rollback()

	// Cek Kapasitas
	var cap, enr int
	err := tx.QueryRow("SELECT capacity, (SELECT COUNT(*) FROM enrollments WHERE class_id=$1) FROM classes WHERE id=$1", req.ClassID).Scan(&cap, &enr)
	if err != nil || enr >= cap {
		http.Error(w, "Class full", http.StatusBadRequest)
		return
	}

	// Simpan
	_, err = tx.Exec("INSERT INTO enrollments(student_id, class_id) VALUES($1, $2)", req.StudentID, req.ClassID)
	if err != nil {
		http.Error(w, "Failed to register", http.StatusInternalServerError)
		return
	}

	tx.Commit()
	renderJSON(w, map[string]string{"status": "registered"})
}

func getStudentSchedule(w http.ResponseWriter, r *http.Request) {
    // URL Path saat ini: /api/student/{id}/schedule
    // Kita gunakan strings.TrimPrefix untuk mendapatkan "{id}/schedule"
    importPath := r.URL.Path
    trimmed := "/api/student/"
    
    // Pastikan path mengandung prefix yang benar
    if len(importPath) < len(trimmed) {
        http.Error(w, "Invalid path", http.StatusBadRequest)
        return
    }

    // Mengambil bagian setelah /api/student/
    afterPrefix := importPath[len(trimmed):] 
    
    // Pisahkan berdasarkan "/" untuk mengambil ID-nya saja
    // Contoh: "1/schedule" -> ["1", "schedule"]
    parts := strings.Split(afterPrefix, "/")
    if len(parts) == 0 {
        http.Error(w, "ID not found", http.StatusBadRequest)
        return
    }
    studentID := parts[0]

    rows, err := db.Query(`
        SELECT s.name, c.name, cl.day, cl.start_time, cl.end_time
        FROM enrollments e
        JOIN students s ON s.id=e.student_id
        JOIN classes cl ON cl.id=e.class_id
        JOIN courses c ON c.id=cl.course_id
        WHERE s.id=$1
    `, studentID)
    
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    defer rows.Close()

    var res []interface{}
    for rows.Next() {
        var s, c, d, st, et string
        rows.Scan(&s, &c, &d, &st, &et)
        res = append(res, map[string]string{
            "student": s, 
            "course": c, 
            "day": d, 
            "start": st, 
            "end": et,
        })
    }

    // Pastikan jika kosong tetap mengirim array [] bukan null
    if res == nil {
        res = []interface{}{}
    }
    renderJSON(w, res)
}

func renderJSON(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(data)
}