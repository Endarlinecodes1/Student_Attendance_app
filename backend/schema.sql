-- ============================================================
-- EduTrack Database Schema (PostgreSQL / Neon Database)
-- ============================================================

-- Teachers Table for Authentication & Role-Based Access Control
CREATE TABLE IF NOT EXISTS teachers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) DEFAULT 'teacher', -- 'teacher' or 'admin'
    assigned_class VARCHAR(100) DEFAULT 'Primary 1',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_teachers_email ON teachers(email);

-- Student Records Table
CREATE TABLE IF NOT EXISTS student_records (
    id SERIAL PRIMARY KEY,
    student_name VARCHAR(100) NOT NULL,
    roll_no VARCHAR(50) NOT NULL,
    student_class VARCHAR(100),       -- e.g. Primary 1, Primary 2, JSS 1, SSS 1
    gender VARCHAR(20),               -- Male, Female, Other
    parent_contact VARCHAR(50),       -- e.g. Phone number
    att_date DATE,                    -- NULL when row is for base enrollment or grades
    status VARCHAR(10) CHECK (status IN ('Present', 'Absent')), -- NULL when row is not attendance
    subject VARCHAR(100),             -- NULL when row is for attendance or enrollment
    score NUMERIC(5, 2) CHECK (score >= 0 AND score <= 100), -- NULL when row is for attendance or enrollment
    grade VARCHAR(10),                -- Auto-calculated grade: A (70+), B (60-69), C (50-59), D (40-49), F (<40)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- Indexes for High Performance
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_student_records_roll_no ON student_records(roll_no);
CREATE INDEX IF NOT EXISTS idx_student_records_att_date ON student_records(att_date);
CREATE INDEX IF NOT EXISTS idx_student_records_roll_date ON student_records(roll_no, att_date);
CREATE INDEX IF NOT EXISTS idx_student_records_class ON student_records(student_class);
CREATE INDEX IF NOT EXISTS idx_student_records_subject ON student_records(subject);

-- ============================================================
-- Sample Seed Data
-- ============================================================

-- Primary 1 Students
INSERT INTO student_records (student_name, roll_no, student_class, gender, parent_contact) 
VALUES ('Alice Johnson', '101', 'Primary 1', 'Female', '+2348011223344');

INSERT INTO student_records (student_name, roll_no, student_class, att_date, status) 
VALUES ('Alice Johnson', '101', 'Primary 1', CURRENT_DATE, 'Present');

INSERT INTO student_records (student_name, roll_no, student_class, subject, score, grade) 
VALUES ('Alice Johnson', '101', 'Primary 1', 'Mathematics', 88.00, 'A');

INSERT INTO student_records (student_name, roll_no, student_class, gender, parent_contact) 
VALUES ('Bob Smith', '102', 'Primary 1', 'Male', '+2348022334455');

INSERT INTO student_records (student_name, roll_no, student_class, att_date, status) 
VALUES ('Bob Smith', '102', 'Primary 1', CURRENT_DATE, 'Absent');

-- Primary 2 Students (including Chidera Okafor for edit testing)
INSERT INTO student_records (student_name, roll_no, student_class, gender, parent_contact) 
VALUES ('Chidera Okafor', '201', 'Primary 2', 'Unspecified', 'N/A');

INSERT INTO student_records (student_name, roll_no, student_class, att_date, status) 
VALUES ('Chidera Okafor', '201', 'Primary 2', CURRENT_DATE, 'Present');

INSERT INTO student_records (student_name, roll_no, student_class, subject, score, grade) 
VALUES ('Chidera Okafor', '201', 'Primary 2', 'Mathematics', 78.00, 'A');

INSERT INTO student_records (student_name, roll_no, student_class, gender, parent_contact) 
VALUES ('Emeka Nnamdi', '202', 'Primary 2', 'Male', '+2348044556677');

-- JSS 1 Students
INSERT INTO student_records (student_name, roll_no, student_class, gender, parent_contact) 
VALUES ('Charlie Davis', '103', 'JSS 1', 'Male', '+2348033445566');
