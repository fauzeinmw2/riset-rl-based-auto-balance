CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS courses (
    id SERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    sks INT NOT NULL
);

CREATE TABLE IF NOT EXISTS classes (
    id SERIAL PRIMARY KEY,
    course_id INT NOT NULL REFERENCES courses(id),
    day TEXT NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    capacity INT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_prerequisites (
    course_id INT NOT NULL REFERENCES courses(id),
    prerequisite_id INT NOT NULL REFERENCES courses(id),
    PRIMARY KEY (course_id, prerequisite_id)
);

CREATE TABLE IF NOT EXISTS enrollments (
    id SERIAL PRIMARY KEY,
    student_id INT NOT NULL REFERENCES students(id),
    class_id INT NOT NULL REFERENCES classes(id),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(student_id, class_id)
);

CREATE INDEX IF NOT EXISTS idx_enrollments_student ON enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_class ON enrollments(class_id);
CREATE INDEX IF NOT EXISTS idx_classes_course ON classes(course_id);

INSERT INTO students (name)
SELECT 'Student ' || g FROM generate_series(1, 400) g
ON CONFLICT DO NOTHING;

INSERT INTO courses (code, name, sks) VALUES
('IF101', 'Algorithms', 3),
('IF102', 'Data Structures', 3),
('IF103', 'Databases', 3),
('IF104', 'Operating Systems', 3),
('IF105', 'Networks', 3),
('IF106', 'Machine Learning', 3),
('IF107', 'Distributed Systems', 3),
('IF108', 'Software Engineering', 3)
ON CONFLICT (code) DO NOTHING;

INSERT INTO classes (course_id, day, start_time, end_time, capacity)
SELECT c.id,
       d.day,
       d.start_time,
       d.end_time,
       40 + ((c.id * 7) % 25)
FROM courses c
CROSS JOIN (
    VALUES
      ('Monday', '08:00', '09:40'),
      ('Tuesday', '10:00', '11:40'),
      ('Wednesday', '13:00', '14:40'),
      ('Thursday', '15:00', '16:40')
) AS d(day, start_time, end_time)
WHERE NOT EXISTS (
    SELECT 1 FROM classes cc WHERE cc.course_id = c.id AND cc.day = d.day
);

INSERT INTO course_prerequisites(course_id, prerequisite_id)
SELECT c2.id, c1.id
FROM courses c1
JOIN courses c2 ON c2.id = c1.id + 1
WHERE NOT EXISTS (
    SELECT 1 FROM course_prerequisites cp
    WHERE cp.course_id = c2.id AND cp.prerequisite_id = c1.id
);

INSERT INTO enrollments(student_id, class_id)
SELECT ((g * 11) % 400) + 1,
       ((g * 13) % (SELECT COUNT(*) FROM classes)) + 1
FROM generate_series(1, 1400) g
ON CONFLICT DO NOTHING;
