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
    print(f"Setting up database for EduTrack...")

    if DATABASE_URL:
        db_url = DATABASE_URL
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
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

        create_table_sql = """
        CREATE TABLE IF NOT EXISTS teachers (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(150) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(20) DEFAULT 'teacher',
            assigned_class VARCHAR(100) DEFAULT 'Primary 1',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS student_records (
            id SERIAL PRIMARY KEY,
            student_name VARCHAR(100) NOT NULL,
            roll_no VARCHAR(50) NOT NULL,
            student_class VARCHAR(100),
            gender VARCHAR(20),
            parent_contact VARCHAR(50),
            att_date DATE,
            status VARCHAR(10) CHECK (status IN ('Present', 'Absent')),
            subject VARCHAR(100),
            score NUMERIC(5, 2) CHECK (score >= 0 AND score <= 100),
            grade VARCHAR(10),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        ALTER TABLE student_records ADD COLUMN IF NOT EXISTS student_class VARCHAR(100);
        ALTER TABLE student_records ADD COLUMN IF NOT EXISTS gender VARCHAR(20);
        ALTER TABLE student_records ADD COLUMN IF NOT EXISTS parent_contact VARCHAR(50);
        ALTER TABLE student_records ADD COLUMN IF NOT EXISTS grade VARCHAR(10);

        CREATE INDEX IF NOT EXISTS idx_teachers_email ON teachers(email);
        CREATE INDEX IF NOT EXISTS idx_student_records_roll_no ON student_records(roll_no);
        CREATE INDEX IF NOT EXISTS idx_student_records_att_date ON student_records(att_date);
        CREATE INDEX IF NOT EXISTS idx_student_records_roll_date ON student_records(roll_no, att_date);
        CREATE INDEX IF NOT EXISTS idx_student_records_class ON student_records(student_class);
        CREATE INDEX IF NOT EXISTS idx_student_records_subject ON student_records(subject);
        """
        cur.execute(create_table_sql)

        # Seed Admin & Teachers
        cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'admin@edutrack.com' LIMIT 1;")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO teachers (name, email, password_hash, role, assigned_class)
                VALUES ('Administrator', 'admin@edutrack.com', %s, 'admin', 'All');
            """, (generate_password_hash('admin123'),))
            print("Admin account created: admin@edutrack.com / admin123")

        cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'teacher@edutrack.com' LIMIT 1;")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO teachers (name, email, password_hash, role, assigned_class)
                VALUES ('Sarah Jenkins', 'teacher@edutrack.com', %s, 'teacher', 'Primary 1');
            """, (generate_password_hash('teacher123'),))
            print("Primary 1 Teacher created: teacher@edutrack.com / teacher123")

        cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'teacher2@edutrack.com' LIMIT 1;")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO teachers (name, email, password_hash, role, assigned_class)
                VALUES ('Michael Obi', 'teacher2@edutrack.com', %s, 'teacher', 'Primary 2');
            """, (generate_password_hash('teacher123'),))
            print("Primary 2 Teacher created: teacher2@edutrack.com / teacher123")

        # Seed Chidera Okafor if missing
        cur.execute("SELECT id FROM student_records WHERE LOWER(roll_no) = '201' LIMIT 1;")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO student_records (student_name, roll_no, student_class, gender, parent_contact)
                VALUES ('Chidera Okafor', '201', 'Primary 2', 'Unspecified', 'N/A');
                INSERT INTO student_records (student_name, roll_no, student_class, att_date, status)
                VALUES ('Chidera Okafor', '201', 'Primary 2', CURRENT_DATE, 'Present');
                INSERT INTO student_records (student_name, roll_no, student_class, subject, score, grade)
                VALUES ('Chidera Okafor', '201', 'Primary 2', 'Mathematics', 78.0, 'A');
            """)
            print("Chidera Okafor seeded (Primary 2) for edit testing.")

        conn.commit()
        print("Database setup and verification completed successfully.")
        cur.close()
        conn.close()
        return True

    except Exception as e:
        print(f"Error setting up database: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False


if __name__ == '__main__':
    setup_database()
