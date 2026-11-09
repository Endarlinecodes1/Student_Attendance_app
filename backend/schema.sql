-- ============================================================
-- EduTrack Database Schema (PostgreSQL / Neon Database)
-- Normalized 3-Table Relational Design
-- ============================================================

-- 4. Teachers Table for Authentication & Role-Based Access Control
CREATE TABLE IF NOT EXISTS teachers (
    id SERIAL PRIMARY KEY,
    school_id INT DEFAULT 1,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) DEFAULT 'teacher', -- 'teacher' or 'admin'
    assigned_class VARCHAR(100) DEFAULT 'Primary 1',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE teachers ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;
CREATE INDEX IF NOT EXISTS idx_teachers_email ON teachers(email);
CREATE INDEX IF NOT EXISTS idx_teachers_school_id ON teachers(school_id);

-- 1. Students Table (1 row per student, no NULL repeats)
CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    school_id INT DEFAULT 1,
    full_name VARCHAR(255) NOT NULL,
    roll_no VARCHAR(100) NOT NULL,
    class VARCHAR(100),
    gender VARCHAR(20) DEFAULT 'Unspecified',
    parent_contact VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_students_school_roll UNIQUE (school_id, roll_no)
);

CREATE INDEX IF NOT EXISTS idx_students_school_roll ON students(school_id, roll_no);
CREATE INDEX IF NOT EXISTS idx_students_school_class ON students(school_id, class);

-- 2. Attendance Table (UNIQUE on student_id + att_date prevents duplicate days)
CREATE TABLE IF NOT EXISTS attendance (
    id SERIAL PRIMARY KEY,
    student_id INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    school_id INT DEFAULT 1,
    att_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('Present', 'Absent')),
    marked_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_attendance_student_date UNIQUE (student_id, att_date)
);

CREATE INDEX IF NOT EXISTS idx_attendance_student_date ON attendance(student_id, att_date);
CREATE INDEX IF NOT EXISTS idx_attendance_school_date ON attendance(school_id, att_date);

-- 3. Grades Table (Academic scores, term, session)
CREATE TABLE IF NOT EXISTS grades (
    id SERIAL PRIMARY KEY,
    student_id INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    school_id INT DEFAULT 1,
    subject VARCHAR(255) NOT NULL,
    score NUMERIC(5, 2) NOT NULL CHECK (score >= 0 AND score <= 100),
    grade VARCHAR(10),
    term VARCHAR(100) DEFAULT 'First Term 2026/2027',
    session VARCHAR(100) DEFAULT '2026/2027',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_grades_student_id ON grades(student_id);
CREATE INDEX IF NOT EXISTS idx_grades_school_id ON grades(school_id);

-- ============================================================
-- Migration Query: Migrate legacy student_records without data loss
-- ============================================================

-- 1. Migrate distinct students
INSERT INTO students (school_id, full_name, roll_no, class, gender, parent_contact)
SELECT 
    1 AS school_id,
    COALESCE(MAX(NULLIF(student_name, '')), 'Student') AS full_name,
    roll_no,
    COALESCE(MAX(NULLIF(student_class, '')), 'Primary 1') AS class,
    COALESCE(MAX(NULLIF(gender, '')), 'Unspecified') AS gender,
    COALESCE(MAX(NULLIF(parent_contact, '')), '') AS parent_contact
FROM student_records
WHERE roll_no IS NOT NULL
GROUP BY roll_no
ON CONFLICT (school_id, roll_no) DO UPDATE SET
    full_name = EXCLUDED.full_name,
    class = EXCLUDED.class,
    gender = CASE WHEN EXCLUDED.gender != 'Unspecified' THEN EXCLUDED.gender ELSE students.gender END,
    parent_contact = CASE WHEN EXCLUDED.parent_contact != '' THEN EXCLUDED.parent_contact ELSE students.parent_contact END;

-- 2. Migrate attendance
INSERT INTO attendance (student_id, school_id, att_date, status, marked_by)
SELECT 
    s.id,
    1 AS school_id,
    sr.att_date,
    sr.status,
    'Migration'
FROM student_records sr
JOIN students s ON LOWER(sr.roll_no) = LOWER(s.roll_no) AND s.school_id = 1
WHERE sr.att_date IS NOT NULL AND sr.status IN ('Present', 'Absent')
ON CONFLICT (student_id, att_date) DO UPDATE SET
    status = EXCLUDED.status;

-- 3. Migrate grades
INSERT INTO grades (student_id, school_id, subject, score, grade, term, session)
SELECT 
    s.id,
    1 AS school_id,
    sr.subject,
    sr.score,
    COALESCE(sr.grade, CASE 
        WHEN sr.score >= 70 THEN 'A'
        WHEN sr.score >= 60 THEN 'B'
        WHEN sr.score >= 50 THEN 'C'
        WHEN sr.score >= 40 THEN 'D'
        ELSE 'F'
    END),
    'First Term 2026/2027',
    '2026/2027'
FROM student_records sr
JOIN students s ON LOWER(sr.roll_no) = LOWER(s.roll_no) AND s.school_id = 1
WHERE sr.subject IS NOT NULL AND sr.score IS NOT NULL
ON CONFLICT DO NOTHING;
