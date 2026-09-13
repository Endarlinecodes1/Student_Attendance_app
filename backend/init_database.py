import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'Richard123#*')
TARGET_DB = os.getenv('DB_NAME', 'attendance_db')


def setup_database():
    print(f"Connecting to PostgreSQL server at {DB_HOST}:{DB_PORT} as user '{DB_USER}'...")

    # Step 1: Connect to default 'postgres' database to create 'attendance_db'
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname='postgres'
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()

        # Check if database exists
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (TARGET_DB,))
        exists = cur.fetchone()
        if not exists:
            print(f"Creating database '{TARGET_DB}'...")
            cur.execute(f'CREATE DATABASE "{TARGET_DB}";')
            print(f"Database '{TARGET_DB}' created successfully.")
        else:
            print(f"Database '{TARGET_DB}' already exists.")

        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error while checking/creating database '{TARGET_DB}': {e}")
        return False

    # Step 2: Connect to the target database 'attendance_db' and create table & index
    try:
        print(f"Connecting to '{TARGET_DB}' to create table and indexes...")
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=TARGET_DB
        )
        cur = conn.cursor()

        # Create student_records table
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS student_records (
            id SERIAL PRIMARY KEY,
            student_name VARCHAR(100) NOT NULL,
            roll_no VARCHAR(20) NOT NULL,
            att_date DATE,
            status VARCHAR(10) CHECK (status IN ('Present', 'Absent')),
            subject VARCHAR(100),
            score NUMERIC(5, 2) CHECK (score >= 0 AND score <= 100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        cur.execute(create_table_sql)
        print("Table 'student_records' verified/created.")

        # Create Index
        create_index_sql = """
        CREATE INDEX IF NOT EXISTS idx_roll_date ON student_records(roll_no, att_date);
        """
        cur.execute(create_index_sql)
        print("Index 'idx_roll_date' verified/created.")

        # Check existing records
        cur.execute("SELECT COUNT(*) FROM student_records;")
        count = cur.fetchone()[0]

        if count == 0:
            print("Seeding dummy data (3 students, 2 attendance rows each, 1 grade each)...")
            seed_data_sql = """
            -- Student 1: Alice Johnson (Roll: 101)
            INSERT INTO student_records (student_name, roll_no) VALUES ('Alice Johnson', '101');
            INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Alice Johnson', '101', '2026-09-11', 'Present');
            INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Alice Johnson', '101', '2026-09-12', 'Present');
            INSERT INTO student_records (student_name, roll_no, subject, score) VALUES ('Alice Johnson', '101', 'Mathematics', 95.00);

            -- Student 2: Bob Smith (Roll: 102)
            INSERT INTO student_records (student_name, roll_no) VALUES ('Bob Smith', '102');
            INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Bob Smith', '102', '2026-09-11', 'Present');
            INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Bob Smith', '102', '2026-09-12', 'Absent');
            INSERT INTO student_records (student_name, roll_no, subject, score) VALUES ('Bob Smith', '102', 'Physics', 82.50);

            -- Student 3: Charlie Davis (Roll: 103)
            INSERT INTO student_records (student_name, roll_no) VALUES ('Charlie Davis', '103');
            INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Charlie Davis', '103', '2026-09-11', 'Absent');
            INSERT INTO student_records (student_name, roll_no, att_date, status) VALUES ('Charlie Davis', '103', '2026-09-12', 'Present');
            INSERT INTO student_records (student_name, roll_no, subject, score) VALUES ('Charlie Davis', '103', 'Chemistry', 78.00);
            """
            cur.execute(seed_data_sql)
            conn.commit()
            print("Successfully inserted 3 dummy students with 2 attendance rows each and 1 grade each.")
        else:
            print(f"Table already contains {count} records. Skipping initial seeding to avoid duplicates.")

        cur.close()
        conn.close()
        print("\nDatabase and table setup completed successfully!")
        return True

    except Exception as e:
        print(f"Error setting up table/seed data in '{TARGET_DB}': {e}")
        return False


if __name__ == '__main__':
    setup_database()
