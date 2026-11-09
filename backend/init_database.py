import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'Richard123#*')
TARGET_DB = os.getenv('DB_NAME', 'Edutrack_project')
DATABASE_URL = os.getenv('DATABASE_URL')


def setup_database():
    print(f"Setting up normalized database for EduTrack...")

    if DATABASE_URL:
        db_url = DATABASE_URL
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        if 'sslmode=' not in db_url and 'localhost' not in db_url and '127.0.0.1' not in db_url:
            separator = '&' if '?' in db_url else '?'
            db_url = f"{db_url}{separator}sslmode=require"
        conn = psycopg2.connect(db_url)
    else:
        try:
            conn_admin = psycopg2.connect(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASSWORD,
                dbname='postgres'
            )
            conn_admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur_admin = conn_admin.cursor()

            cur_admin.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (TARGET_DB,))
            exists = cur_admin.fetchone()
            if not exists:
                print(f"Creating database '{TARGET_DB}'...")
                cur_admin.execute(f'CREATE DATABASE "{TARGET_DB}";')
                print(f"Database '{TARGET_DB}' created successfully.")
            else:
                print(f"Database '{TARGET_DB}' already exists.")

            cur_admin.close()
            conn_admin.close()
        except Exception as e:
            print(f"Notice during DB check: {e}")

        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=TARGET_DB
        )

    try:
        cur = conn.cursor()

        create_tables_sql = """
        -- Teachers Table
        CREATE TABLE IF NOT EXISTS teachers (
            id SERIAL PRIMARY KEY,
            school_id INT DEFAULT 1,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(150) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(20) DEFAULT 'teacher',
            assigned_class VARCHAR(100) DEFAULT 'Primary 1',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        ALTER TABLE teachers ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;
        UPDATE teachers SET school_id = 1 WHERE school_id IS NULL;
        CREATE INDEX IF NOT EXISTS idx_teachers_email ON teachers(email);
        CREATE INDEX IF NOT EXISTS idx_teachers_school_id ON teachers(school_id);

        -- Students Table (1 row per student)
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

        -- Attendance Table (UNIQUE on student_id + att_date)
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

        -- Grades Table
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
        """
        cur.execute(create_tables_sql)

        # Check and migrate legacy student_records if present
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'student_records';")
        if cur.fetchone():
            print("Migrating legacy student_records into normalized tables...")
            cur.execute("""
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
            """)
            print(f"Students migrated: {cur.rowcount}")

            cur.execute("""
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
            """)
            print(f"Attendance records migrated: {cur.rowcount}")

            cur.execute("""
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
            """)
            print(f"Grades migrated: {cur.rowcount}")

        # Seed Admin & Teachers
        cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'admin@edutrack.com' LIMIT 1;")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                VALUES (1, 'Administrator', 'admin@edutrack.com', %s, 'admin', 'All');
            """, (generate_password_hash('admin123'),))
            print("Admin account created: admin@edutrack.com / admin123")

        cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'teacher@edutrack.com' LIMIT 1;")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                VALUES (1, 'Sarah Jenkins', 'teacher@edutrack.com', %s, 'teacher', 'Primary 1');
            """, (generate_password_hash('teacher123'),))
            print("Primary 1 Teacher created: teacher@edutrack.com / teacher123")

        cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'teacher2@edutrack.com' LIMIT 1;")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                VALUES (1, 'Michael Obi', 'teacher2@edutrack.com', %s, 'teacher', 'Primary 2');
            """, (generate_password_hash('teacher123'),))
            print("Primary 2 Teacher created: teacher2@edutrack.com / teacher123")

        # Seed Chidera Okafor if missing in students
        cur.execute("SELECT id FROM students WHERE LOWER(roll_no) = '201' AND school_id = 1 LIMIT 1;")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO students (school_id, full_name, roll_no, class, gender, parent_contact)
                VALUES (1, 'Chidera Okafor', '201', 'Primary 2', 'Female', '+2348011223399')
                RETURNING id;
            """)
            chidera_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO attendance (student_id, school_id, att_date, status, marked_by)
                VALUES (%s, 1, CURRENT_DATE, 'Present', 'System')
                ON CONFLICT (student_id, att_date) DO NOTHING;
            """, (chidera_id,))
            cur.execute("""
                INSERT INTO grades (student_id, school_id, subject, score, grade, term, session)
                VALUES (%s, 1, 'Mathematics', 78.0, 'A', 'First Term 2026/2027', '2026/2027');
            """, (chidera_id,))
            print("Chidera Okafor seeded (Primary 2) for edit testing.")

        conn.commit()
        print("Database setup, migration, and verification completed successfully.")
        cur.close()
        conn.close()
        return True

    except Exception as e:
        print(f"Error setting up database: {e}")
        if conn:
            conn.rollback()
        return False


if __name__ == '__main__':
    setup_database()
