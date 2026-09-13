-- ============================================================
-- Database Creation
-- Note: Run in postgres database to create attendance_db
-- ============================================================
-- CREATE DATABASE attendance_db;

-- ============================================================
-- Table Creation in attendance_db
-- ============================================================
CREATE TABLE IF NOT EXISTS student_records (
    id SERIAL PRIMARY KEY,
    student_name VARCHAR(100) NOT NULL,
    roll_no VARCHAR(20) NOT NULL,
    att_date DATE, -- NULL when row is for grades only
    status VARCHAR(10) CHECK (status IN ('Present', 'Absent')), -- NULL when row is for grades only
    subject VARCHAR(100), -- NULL when row is for attendance only
    score NUMERIC(5, 2) CHECK (score >= 0 AND score <= 100), -- NULL when row is for attendance only
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- Index
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_roll_date ON student_records(roll_no, att_date);

-- ============================================================
-- Dummy Data: 3 students, each with 2 attendance rows and 1 grade row
-- ============================================================

-- Student 1: Alice Johnson (Roll: 101)
-- Base record
INSERT INTO student_records (student_name, roll_no) VALUES ('Alice Johnson', '101');
-- Attendance rows
INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Alice Johnson', '101', '2026-09-11', 'Present');
INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Alice Johnson', '101', '2026-09-12', 'Present');
-- Grade row
INSERT INTO student_records (student_name, roll_no, subject, score) VALUES ('Alice Johnson', '101', 'Mathematics', 95.00);

-- Student 2: Bob Smith (Roll: 102)
-- Base record
INSERT INTO student_records (student_name, roll_no) VALUES ('Bob Smith', '102');
-- Attendance rows
INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Bob Smith', '102', '2026-09-11', 'Present');
INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Bob Smith', '102', '2026-09-12', 'Absent');
-- Grade row
INSERT INTO student_records (student_name, roll_no, subject, score) VALUES ('Bob Smith', '102', 'Physics', 82.50);

-- Student 3: Charlie Davis (Roll: 103)
-- Base record
INSERT INTO student_records (student_name, roll_no) VALUES ('Charlie Davis', '103');
-- Attendance rows
INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Charlie Davis', '103', '2026-09-11', 'Absent');
INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Charlie Davis', '103', '2026-09-12', 'Present');
-- Grade row
INSERT INTO student_records (student_name, roll_no, subject, score) VALUES ('Charlie Davis', '103', 'Chemistry', 78.00);
