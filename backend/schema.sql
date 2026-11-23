-- ============================================================
-- EduTrack Database Schema (PostgreSQL / Neon Database)
-- Normalized 3-Table Relational Design with Standardized Canonical Fields
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

-- 1. Students Table (1 row per student, canonical 5 names)
CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    school_id INT DEFAULT 1,
    student_name VARCHAR(255),
    full_name VARCHAR(255),
    roll_no VARCHAR(100) NOT NULL,
    student_class VARCHAR(100),
    class VARCHAR(100),
    gender VARCHAR(20) DEFAULT 'Unspecified',
    parent_contact VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE students ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;
ALTER TABLE students ADD COLUMN IF NOT EXISTS student_name VARCHAR(255);
ALTER TABLE students ADD COLUMN IF NOT EXISTS full_name VARCHAR(255);
ALTER TABLE students ADD COLUMN IF NOT EXISTS roll_no VARCHAR(100);
ALTER TABLE students ADD COLUMN IF NOT EXISTS student_class VARCHAR(100);
ALTER TABLE students ADD COLUMN IF NOT EXISTS class VARCHAR(100);
ALTER TABLE students ADD COLUMN IF NOT EXISTS gender VARCHAR(20) DEFAULT 'Unspecified';
ALTER TABLE students ADD COLUMN IF NOT EXISTS parent_contact VARCHAR(100);
ALTER TABLE students ALTER COLUMN full_name DROP NOT NULL;
ALTER TABLE students ALTER COLUMN class DROP NOT NULL;

UPDATE students SET student_name = COALESCE(student_name, full_name) WHERE student_name IS NULL;
UPDATE students SET full_name = COALESCE(full_name, student_name) WHERE full_name IS NULL;
UPDATE students SET student_class = COALESCE(student_class, class) WHERE student_class IS NULL;
UPDATE students SET class = COALESCE(class, student_class) WHERE class IS NULL;

CREATE INDEX IF NOT EXISTS idx_students_school_roll ON students(school_id, roll_no);
CREATE INDEX IF NOT EXISTS idx_students_roll ON students(roll_no);

-- 2. Attendance Table (Daily attendance tracking)
CREATE TABLE IF NOT EXISTS attendance (
    id SERIAL PRIMARY KEY,
    student_id INT,
    school_id INT DEFAULT 1,
    roll_no VARCHAR(100),
    student_name VARCHAR(255),
    student_class VARCHAR(100),
    att_date DATE,
    date DATE,
    status VARCHAR(20) DEFAULT 'Present',
    marked_by VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE attendance ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_id INT;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS roll_no VARCHAR(100);
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_name VARCHAR(255);
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_class VARCHAR(100);
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS att_date DATE;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS date DATE;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS status VARCHAR(20);
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS marked_by VARCHAR(100);
ALTER TABLE attendance ALTER COLUMN student_id DROP NOT NULL;
ALTER TABLE attendance ALTER COLUMN school_id DROP NOT NULL;
ALTER TABLE attendance ALTER COLUMN marked_by DROP NOT NULL;
ALTER TABLE attendance ALTER COLUMN status DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_attendance_student_date ON attendance(student_id, att_date);
CREATE INDEX IF NOT EXISTS idx_attendance_school_date ON attendance(school_id, att_date);
CREATE INDEX IF NOT EXISTS idx_attendance_roll_date ON attendance(roll_no, att_date);

-- 3. Grades Table (Academic scores, term, session, remarks)
CREATE TABLE IF NOT EXISTS grades (
    id SERIAL PRIMARY KEY,
    student_id INT,
    school_id INT DEFAULT 1,
    roll_no VARCHAR(100),
    student_name VARCHAR(255),
    student_class VARCHAR(100),
    subject VARCHAR(255),
    subject_name VARCHAR(255),
    score NUMERIC(5, 2),
    grade VARCHAR(20),
    remarks VARCHAR(255),
    term VARCHAR(100) DEFAULT 'First Term 2026/2027',
    session VARCHAR(100) DEFAULT '2026/2027',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE grades ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;
ALTER TABLE grades ADD COLUMN IF NOT EXISTS student_id INT;
ALTER TABLE grades ADD COLUMN IF NOT EXISTS roll_no VARCHAR(100);
ALTER TABLE grades ADD COLUMN IF NOT EXISTS student_name VARCHAR(255);
ALTER TABLE grades ADD COLUMN IF NOT EXISTS student_class VARCHAR(100);
ALTER TABLE grades ADD COLUMN IF NOT EXISTS subject VARCHAR(255);
ALTER TABLE grades ADD COLUMN IF NOT EXISTS subject_name VARCHAR(255);
ALTER TABLE grades ADD COLUMN IF NOT EXISTS score NUMERIC(5, 2);
ALTER TABLE grades ADD COLUMN IF NOT EXISTS grade VARCHAR(20);
ALTER TABLE grades ADD COLUMN IF NOT EXISTS remarks VARCHAR(255);
ALTER TABLE grades ADD COLUMN IF NOT EXISTS term VARCHAR(100) DEFAULT 'First Term 2026/2027';
ALTER TABLE grades ADD COLUMN IF NOT EXISTS session VARCHAR(100) DEFAULT '2026/2027';
ALTER TABLE grades ALTER COLUMN student_id DROP NOT NULL;
ALTER TABLE grades ALTER COLUMN school_id DROP NOT NULL;
ALTER TABLE grades ALTER COLUMN grade DROP NOT NULL;
ALTER TABLE grades ALTER COLUMN remarks DROP NOT NULL;
ALTER TABLE grades ALTER COLUMN term DROP NOT NULL;
ALTER TABLE grades ALTER COLUMN session DROP NOT NULL;
ALTER TABLE grades ALTER COLUMN score DROP NOT NULL;
ALTER TABLE grades ALTER COLUMN subject DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_grades_student_id ON grades(student_id);
CREATE INDEX IF NOT EXISTS idx_grades_school_id ON grades(school_id);
CREATE INDEX IF NOT EXISTS idx_grades_roll_no ON grades(roll_no);
