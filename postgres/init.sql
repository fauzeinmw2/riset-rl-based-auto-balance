-- ============================================================
-- KRS (Kuliah Rencana Studi) Database Schema
-- untuk API Load Testing
-- ============================================================

-- Tabel Students
CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    nim VARCHAR(20) UNIQUE NOT NULL,
    email VARCHAR(100),
    phone VARCHAR(15),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabel Courses
CREATE TABLE IF NOT EXISTS courses (
    id SERIAL PRIMARY KEY,
    code VARCHAR(10) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    sks INT NOT NULL DEFAULT 3,
    credit_hours INT DEFAULT 3,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabel Classes (Kelas untuk setiap Matakuliah)
CREATE TABLE IF NOT EXISTS classes (
    id SERIAL PRIMARY KEY,
    course_id INT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    code VARCHAR(20) UNIQUE,
    capacity INT NOT NULL DEFAULT 30,
    day VARCHAR(10),        -- Monday, Tuesday, etc
    start_time TIME,
    end_time TIME,
    semester INT,
    academic_year VARCHAR(10),
    lecturer VARCHAR(100),
    location VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tabel Enrollments (Daftar Peserta untuk Matakuliah/Kelas)
CREATE TABLE IF NOT EXISTS enrollments (
    id SERIAL PRIMARY KEY,
    student_id INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    class_id INT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    enrollment_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) DEFAULT 'active',  -- active, dropped, completed
    grade VARCHAR(2),
    UNIQUE(student_id, class_id)
);

-- Tabel Course Prerequisites
CREATE TABLE IF NOT EXISTS course_prerequisites (
    course_id INT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    prerequisite_id INT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    PRIMARY KEY (course_id, prerequisite_id),
    CONSTRAINT no_self_prerequisite CHECK (course_id != prerequisite_id)
);

-- ============================================================
-- CREATE INDEXES untuk query optimization
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_students_nim ON students(nim);
CREATE INDEX IF NOT EXISTS idx_courses_code ON courses(code);
CREATE INDEX IF NOT EXISTS idx_classes_course_id ON classes(course_id);
CREATE INDEX IF NOT EXISTS idx_classes_day ON classes(day);
CREATE INDEX IF NOT EXISTS idx_enrollments_student_id ON enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_class_id ON enrollments(class_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_student_class ON enrollments(student_id, class_id);

-- ============================================================
-- SEED DATA (100 Students, 10 Courses, 30 Classes)
-- ============================================================

-- Insert Courses (10 courses)
INSERT INTO courses (code, name, sks, credit_hours) VALUES 
('CS101', 'Discrete Mathematics', 3, 3),
('CS102', 'Data Structures', 3, 3),
('CS103', 'Algorithms', 3, 3),
('CS104', 'Database Design', 3, 3),
('CS105', 'Web Development', 3, 3),
('CS201', 'Advanced Algorithms', 3, 3),
('CS202', 'Machine Learning', 3, 3),
('CS203', 'Cloud Computing', 3, 3),
('CS204', 'Cybersecurity', 3, 3),
('CS205', 'Distributed Systems', 3, 3)
ON CONFLICT (code) DO NOTHING;

-- Insert Students (100 students)
INSERT INTO students (name, nim, email, phone) VALUES
('Ahmad Rizki', '2021001', 'ahmad.rizki@telkomuniversity.ac.id', '081234567890'),
('Budi Santoso', '2021002', 'budi.santoso@telkomuniversity.ac.id', '081234567891'),
('Citra Dewi', '2021003', 'citra.dewi@telkomuniversity.ac.id', '081234567892'),
('Dedi Gunawan', '2021004', 'dedi.gunawan@telkomuniversity.ac.id', '081234567893'),
('Eka Putri', '2021005', 'eka.putri@telkomuniversity.ac.id', '081234567894'),
('Fajar Wijaya', '2021006', 'fajar.wijaya@telkomuniversity.ac.id', '081234567895'),
('Gita Sari', '2021007', 'gita.sari@telkomuniversity.ac.id', '081234567896'),
('Hendra Kusuma', '2021008', 'hendra.kusuma@telkomuniversity.ac.id', '081234567897'),
('Indra Pratama', '2021009', 'indra.pratama@telkomuniversity.ac.id', '081234567898'),
('Jaka Seminar', '2021010', 'jaka.seminar@telkomuniversity.ac.id', '081234567899'),
('Kiki Amalia', '2021011', 'kiki.amalia@telkomuniversity.ac.id', '081234567900'),
('Lina Handayani', '2021012', 'lina.handayani@telkomuniversity.ac.id', '081234567901'),
('Malik Ibrahim', '2021013', 'malik.ibrahim@telkomuniversity.ac.id', '081234567902'),
('Nanda Prasetya', '2021014', 'nanda.prasetya@telkomuniversity.ac.id', '081234567903'),
('Okta Ramdhani', '2021015', 'okta.ramdhani@telkomuniversity.ac.id', '081234567904'),
('Putri Lestari', '2021016', 'putri.lestari@telkomuniversity.ac.id', '081234567905'),
('Quinto Rahman', '2021017', 'quinto.rahman@telkomuniversity.ac.id', '081234567906'),
('Ridho Satria', '2021018', 'ridho.satria@telkomuniversity.ac.id', '081234567907'),
('Siti Zainab', '2021019', 'siti.zainab@telkomuniversity.ac.id', '081234567908'),
('Toni Setiawan', '2021020', 'toni.setiawan@telkomuniversity.ac.id', '081234567909'),
('Usman Harahap', '2021021', 'usman.harahap@telkomuniversity.ac.id', '081234567910'),
('Vita Kusuma', '2021022', 'vita.kusuma@telkomuniversity.ac.id', '081234567911'),
('Wayan Adi', '2021023', 'wayan.adi@telkomuniversity.ac.id', '081234567912'),
('Xander Pratama', '2021024', 'xander.pratama@telkomuniversity.ac.id', '081234567913'),
('Yuni Safitri', '2021025', 'yuni.safitri@telkomuniversity.ac.id', '081234567914'),
('Zaenal Arifin', '2021026', 'zaenal.arifin@telkomuniversity.ac.id', '081234567915'),
('Aline Wijaya', '2021027', 'aline.wijaya@telkomuniversity.ac.id', '081234567916'),
('Bambang Suryanto', '2021028', 'bambang.suryanto@telkomuniversity.ac.id', '081234567917'),
('Cantika Dewi', '2021029', 'cantika.dewi@telkomuniversity.ac.id', '081234567918'),
('Diana Kusuma', '2021030', 'diana.kusuma@telkomuniversity.ac.id', '081234567919'),
('Edi Gunawan', '2021031', 'edi.gunawan@telkomuniversity.ac.id', '081234567920'),
('Faisal Anwar', '2021032', 'faisal.anwar@telkomuniversity.ac.id', '081234567921'),
('Gina Sartika', '2021033', 'gina.sartika@telkomuniversity.ac.id', '081234567922'),
('Haro Kusuma', '2021034', 'haro.kusuma@telkomuniversity.ac.id', '081234567923'),
('Ilham Pratama', '2021035', 'ilham.pratama@telkomuniversity.ac.id', '081234567924'),
('Jihan Sari', '2021036', 'jihan.sari@telkomuniversity.ac.id', '081234567925'),
('Karina Handayani', '2021037', 'karina.handayani@telkomuniversity.ac.id', '081234567926'),
('Levi Wijaya', '2021038', 'levi.wijaya@telkomuniversity.ac.id', '081234567927'),
('Mira Kusuma', '2021039', 'mira.kusuma@telkomuniversity.ac.id', '081234567928'),
('Nadia Rahma', '2021040', 'nadia.rahma@telkomuniversity.ac.id', '081234567929'),
('Olin Aprilianto', '2021041', 'olin.aprilianto@telkomuniversity.ac.id', '081234567930'),
('Priya Lestari', '2021042', 'priya.lestari@telkomuniversity.ac.id', '081234567931'),
('Quantum Firdaus', '2021043', 'quantum.firdaus@telkomuniversity.ac.id', '081234567932'),
('Reva Kusuma', '2021044', 'reva.kusuma@telkomuniversity.ac.id', '081234567933'),
('Sinta Dewi', '2021045', 'sinta.dewi@telkomuniversity.ac.id', '081234567934'),
('Taufik Wijaya', '2021046', 'taufik.wijaya@telkomuniversity.ac.id', '081234567935'),
('Usha Prakash', '2021047', 'usha.prakash@telkomuniversity.ac.id', '081234567936'),
('Viona Handayani', '2021048', 'viona.handayani@telkomuniversity.ac.id', '081234567937'),
('Wisnu Adi', '2021049', 'wisnu.adi@telkomuniversity.ac.id', '081234567938'),
('Xenia Kusuma', '2021050', 'xenia.kusuma@telkomuniversity.ac.id', '081234567939'),
('Yanti Sari', '2021051', 'yanti.sari@telkomuniversity.ac.id', '081234567940'),
('Zandra Wijaya', '2021052', 'zandra.wijaya@telkomuniversity.ac.id', '081234567941'),
('Adit Kusuma', '2021053', 'adit.kusuma@telkomuniversity.ac.id', '081234567942'),
('Bella Sari', '2021054', 'bella.sari@telkomuniversity.ac.id', '081234567943'),
('Chrisna Wijaya', '2021055', 'chrisna.wijaya@telkomuniversity.ac.id', '081234567944'),
('Daisy Kusuma', '2021056', 'daisy.kusuma@telkomuniversity.ac.id', '081234567945'),
('Elsa Handayani', '2021057', 'elsa.handayani@telkomuniversity.ac.id', '081234567946'),
('Farad Wijaya', '2021058', 'farad.wijaya@telkomuniversity.ac.id', '081234567947'),
('Gena Kusuma', '2021059', 'gena.kusuma@telkomuniversity.ac.id', '081234567948'),
('Haris Sari', '2021060', 'haris.sari@telkomuniversity.ac.id', '081234567949'),
('Iswara Kusuma', '2021061', 'iswara.kusuma@telkomuniversity.ac.id', '081234567950'),
('Jaya Handayani', '2021062', 'jaya.handayani@telkomuniversity.ac.id', '081234567951'),
('Kayla Wijaya', '2021063', 'kayla.wijaya@telkomuniversity.ac.id', '081234567952'),
('Lena Kusuma', '2021064', 'lena.kusuma@telkomuniversity.ac.id', '081234567953'),
('Mona Sari', '2021065', 'mona.sari@telkomuniversity.ac.id', '081234567954'),
('Novan Wijaya', '2021066', 'novan.wijaya@telkomuniversity.ac.id', '081234567955'),
('Obie Kusuma', '2021067', 'obie.kusuma@telkomuniversity.ac.id', '081234567956'),
('Puri Handayani', '2021068', 'puri.handayani@telkomuniversity.ac.id', '081234567957'),
('Qori Wijaya', '2021069', 'qori.wijaya@telkomuniversity.ac.id', '081234567958'),
('Rita Kusuma', '2021070', 'rita.kusuma@telkomuniversity.ac.id', '081234567959'),
('Stela Sari', '2021071', 'stela.sari@telkomuniversity.ac.id', '081234567960'),
('Terang Wijaya', '2021072', 'terang.wijaya@telkomuniversity.ac.id', '081234567961'),
('Utami Kusuma', '2021073', 'utami.kusuma@telkomuniversity.ac.id', '081234567962'),
('Vega Handayani', '2021074', 'vega.handayani@telkomuniversity.ac.id', '081234567963'),
('Wina Wijaya', '2021075', 'wina.wijaya@telkomuniversity.ac.id', '081234567964'),
('Xenia Kusuma', '2021076', 'xenia.kusuma@telkomuniversity.ac.id', '081234567965'),
('Yasmine Sari', '2021077', 'yasmine.sari@telkomuniversity.ac.id', '081234567966'),
('Zahra Wijaya', '2021078', 'zahra.wijaya@telkomuniversity.ac.id', '081234567967'),
('Adi Kumar', '2021079', 'adi.kumar@telkomuniversity.ac.id', '081234567968'),
('Bianca Kusuma', '2021080', 'bianca.kusuma@telkomuniversity.ac.id', '081234567969'),
('Cahya Wijaya', '2021081', 'cahya.wijaya@telkomuniversity.ac.id', '081234567970'),
('Dewi Sari', '2021082', 'dewi.sari@telkomuniversity.ac.id', '081234567971'),
('Eric Kusuma', '2021083', 'eric.kusuma@telkomuniversity.ac.id', '081234567972'),
('Fika Handayani', '2021084', 'fika.handayani@telkomuniversity.ac.id', '081234567973'),
('Giri Wijaya', '2021085', 'giri.wijaya@telkomuniversity.ac.id', '081234567974'),
('Hana Kusuma', '2021086', 'hana.kusuma@telkomuniversity.ac.id', '081234567975'),
('Indah Sari', '2021087', 'indah.sari@telkomuniversity.ac.id', '081234567976'),
('Jojo Wijaya', '2021088', 'jojo.wijaya@telkomuniversity.ac.id', '081234567977'),
('Kanda Kusuma', '2021089', 'kanda.kusuma@telkomuniversity.ac.id', '081234567978'),
('Laila Handayani', '2021090', 'laila.handayani@telkomuniversity.ac.id', '081234567979'),
('Manda Wijaya', '2021091', 'manda.wijaya@telkomuniversity.ac.id', '081234567980'),
('Nadia Kusuma', '2021092', 'nadia.kusuma@telkomuniversity.ac.id', '081234567981'),
('Olivia Sari', '2021093', 'olivia.sari@telkomuniversity.ac.id', '081234567982'),
('Putri Wijaya', '2021094', 'putri.wijaya@telkomuniversity.ac.id', '081234567983'),
('Qonita Kusuma', '2021095', 'qonita.kusuma@telkomuniversity.ac.id', '081234567984'),
('Rena Handayani', '2021096', 'rena.handayani@telkomuniversity.ac.id', '081234567985'),
('Salsa Wijaya', '2021097', 'salsa.wijaya@telkomuniversity.ac.id', '081234567986'),
('Tasha Kusuma', '2021098', 'tasha.kusuma@telkomuniversity.ac.id', '081234567987'),
('Ujian Sari', '2021099', 'ujian.sari@telkomuniversity.ac.id', '081234567988'),
('Vina Wijaya', '2021100', 'vina.wijaya@telkomuniversity.ac.id', '081234567989')
ON CONFLICT (nim) DO NOTHING;

-- Insert Classes (30 classes dari 10 courses, masing2 3 class session)
INSERT INTO classes (course_id, code, capacity, day, start_time, end_time, semester, academic_year, lecturer, location) VALUES
-- CS101 - Discrete Mathematics (Classes A, B, C)
(1, 'CS101-A', 30, 'Monday', '08:00:00', '10:00:00', 1, '2023/2024', 'Dr. Ahmad Rizki', 'Ruang 101'),
(1, 'CS101-B', 30, 'Tuesday', '10:00:00', '12:00:00', 1, '2023/2024', 'Dr. Ahmad Rizki', 'Ruang 102'),
(1, 'CS101-C', 30, 'Wednesday', '13:00:00', '15:00:00', 1, '2023/2024', 'Prof. Budi Santoso', 'Ruang 103'),
-- CS102 - Data Structures (Classes A, B, C)
(2, 'CS102-A', 30, 'Monday', '10:00:00', '12:00:00', 1, '2023/2024', 'Dr. Citra Dewi', 'Ruang 104'),
(2, 'CS102-B', 30, 'Wednesday', '08:00:00', '10:00:00', 1, '2023/2024', 'Dr. Citra Dewi', 'Ruang 105'),
(2, 'CS102-C', 30, 'Friday', '10:00:00', '12:00:00', 1, '2023/2024', 'Prof. Dedi Gunawan', 'Ruang 106'),
-- CS103 - Algorithms (Classes A, B, C)
(3, 'CS103-A', 30, 'Tuesday', '13:00:00', '15:00:00', 1, '2023/2024', 'Dr. Eka Putri', 'Ruang 107'),
(3, 'CS103-B', 30, 'Thursday', '08:00:00', '10:00:00', 1, '2023/2024', 'Dr. Eka Putri', 'Ruang 108'),
(3, 'CS103-C', 30, 'Friday', '13:00:00', '15:00:00', 1, '2023/2024', 'Prof. Fajar Wijaya', 'Ruang 109'),
-- CS104 - Database Design (Classes A, B, C)
(4, 'CS104-A', 30, 'Monday', '13:00:00', '15:00:00', 1, '2023/2024', 'Dr. Gita Sari', 'Ruang 110'),
(4, 'CS104-B', 30, 'Wednesday', '10:00:00', '12:00:00', 1, '2023/2024', 'Dr. Gita Sari', 'Ruang 111'),
(4, 'CS104-C', 30, 'Thursday', '13:00:00', '15:00:00', 1, '2023/2024', 'Prof. Hendra Kusuma', 'Ruang 112'),
-- CS105 - Web Development (Classes A, B, C)
(5, 'CS105-A', 30, 'Tuesday', '08:00:00', '10:00:00', 1, '2023/2024', 'Dr. Indra Pratama', 'Ruang 113'),
(5, 'CS105-B', 30, 'Thursday', '10:00:00', '12:00:00', 1, '2023/2024', 'Dr. Indra Pratama', 'Ruang 114'),
(5, 'CS105-C', 30, 'Friday', '08:00:00', '10:00:00', 1, '2023/2024', 'Prof. Jaka Seminar', 'Ruang 115'),
-- CS201 - Advanced Algorithms (Classes A, B, C)
(6, 'CS201-A', 30, 'Monday', '08:00:00', '10:00:00', 2, '2023/2024', 'Dr. Kiki Amalia', 'Ruang 201'),
(6, 'CS201-B', 30, 'Wednesday', '13:00:00', '15:00:00', 2, '2023/2024', 'Dr. Kiki Amalia', 'Ruang 202'),
(6, 'CS201-C', 30, 'Friday', '10:00:00', '12:00:00', 2, '2023/2024', 'Prof. Lina Handayani', 'Ruang 203'),
-- CS202 - Machine Learning (Classes A, B, C)
(7, 'CS202-A', 25, 'Tuesday', '10:00:00', '12:00:00', 2, '2023/2024', 'Dr. Malik Ibrahim', 'Ruang 204'),
(7, 'CS202-B', 25, 'Thursday', '13:00:00', '15:00:00', 2, '2023/2024', 'Dr. Malik Ibrahim', 'Ruang 205'),
(7, 'CS202-C', 25, 'Friday', '13:00:00', '15:00:00', 2, '2023/2024', 'Prof. Nanda Prasetya', 'Ruang 206'),
-- CS203 - Cloud Computing (Classes A, B, C)
(8, 'CS203-A', 25, 'Monday', '10:00:00', '12:00:00', 2, '2023/2024', 'Dr. Okta Ramdhani', 'Ruang 207'),
(8, 'CS203-B', 25, 'Wednesday', '08:00:00', '10:00:00', 2, '2023/2024', 'Dr. Okta Ramdhani', 'Ruang 208'),
(8, 'CS203-C', 25, 'Thursday', '10:00:00', '12:00:00', 2, '2023/2024', 'Prof. Putri Lestari', 'Ruang 209'),
-- CS204 - Cybersecurity (Classes A, B, C)
(9, 'CS204-A', 25, 'Tuesday', '13:00:00', '15:00:00', 2, '2023/2024', 'Dr. Quinto Rahman', 'Ruang 210'),
(9, 'CS204-B', 25, 'Thursday', '08:00:00', '10:00:00', 2, '2023/2024', 'Dr. Quinto Rahman', 'Ruang 211'),
(9, 'CS204-C', 25, 'Friday', '08:00:00', '10:00:00', 2, '2023/2024', 'Prof. Ridho Satria', 'Ruang 212'),
-- CS205 - Distributed Systems (Classes A, B, C)
(10, 'CS205-A', 25, 'Monday', '13:00:00', '15:00:00', 2, '2023/2024', 'Dr. Siti Zainab', 'Ruang 213'),
(10, 'CS205-B', 25, 'Wednesday', '10:00:00', '12:00:00', 2, '2023/2024', 'Dr. Siti Zainab', 'Ruang 214'),
(10, 'CS205-C', 25, 'Friday', '13:00:00', '15:00:00', 2, '2023/2024', 'Prof. Toni Setiawan', 'Ruang 215')
ON CONFLICT (code) DO NOTHING;

-- Insert Course Prerequisites
INSERT INTO course_prerequisites (course_id, prerequisite_id) VALUES
-- CS102 (Data Structures) requires CS101 (Discrete Mathematics)
(2, 1),
-- CS103 (Algorithms) requires CS101
(3, 1),
-- CS103 juga requires CS102
(3, 2),
-- CS104 (Database Design) requires CS102
(4, 2),
-- CS105 (Web Development) requires CS102
(5, 2),
-- CS201 (Advanced Algorithms) requires CS103
(6, 3),
-- CS202 (Machine Learning) requires CS103 and CS104
(7, 3),
(7, 4),
-- CS203 (Cloud Computing) requires CS102 and CS105
(8, 2),
(8, 5),
-- CS204 (Cybersecurity) requires CS102
(9, 2),
-- CS205 (Distributed Systems) requires CS103 and CS105
(10, 3),
(10, 5)
ON CONFLICT (course_id, prerequisite_id) DO NOTHING;

-- Insert sample enrollments (50 enrollments dengan distribusi acak untuk load testing)
-- Dipilih 50 dari 100 students, masing2 di 1-3 classes
INSERT INTO enrollments (student_id, class_id, status) VALUES
(1, 1, 'active'), (1, 4, 'active'), (2, 1, 'active'),
(3, 2, 'active'), (3, 5, 'active'), (4, 2, 'active'),
(5, 3, 'active'), (5, 6, 'active'), (6, 3, 'active'),
(7, 7, 'active'), (7, 10, 'active'), (8, 7, 'active'),
(9, 8, 'active'), (9, 11, 'active'), (10, 8, 'active'),
(11, 9, 'active'), (11, 12, 'active'), (12, 9, 'active'),
(13, 13, 'active'), (13, 16, 'active'), (14, 13, 'active'),
(15, 14, 'active'), (15, 17, 'active'), (16, 14, 'active'),
(17, 15, 'active'), (17, 18, 'active'), (18, 15, 'active'),
(19, 19, 'active'), (19, 22, 'active'), (20, 19, 'active'),
(21, 20, 'active'), (21, 23, 'active'), (22, 20, 'active'),
(23, 21, 'active'), (23, 24, 'active'), (24, 21, 'active'),
(25, 25, 'active'), (25, 28, 'active'), (26, 25, 'active'),
(27, 26, 'active'), (27, 29, 'active'), (28, 26, 'active'),
(29, 27, 'active'), (29, 30, 'active'), (30, 27, 'active'),
(31, 4, 'active'), (32, 5, 'active'), (33, 6, 'active'),
(34, 11, 'active'), (35, 12, 'active'), (36, 16, 'active'),
(37, 17, 'active'), (38, 23, 'active'), (39, 24, 'active'),
(40, 29, 'active')
ON CONFLICT (student_id, class_id) DO NOTHING;

-- ============================================================
-- SUMMARY
-- ============================================================
-- Students: 100
-- Courses: 10
-- Classes: 30
-- Prerequisites: 15
-- Enrollments: 50
-- ============================================================

COMMIT;
