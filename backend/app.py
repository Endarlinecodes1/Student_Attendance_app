import os
import logging
import datetime
from functools import wraps
# pyrefly: ignore [missing-import]
from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Enable CORS for all routes (enabling frontend on Vercel or any origin to connect)
CORS(app, resources={r"/*": {"origins": "*"}})

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Secret key for authentication tokens
SECRET_KEY = os.getenv('SECRET_KEY', 'edutrack-secret-jwt-key-2026-attendance')
auth_serializer = URLSafeTimedSerializer(SECRET_KEY)

# Database configuration
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'Edutrack_project')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'Richard123#*')
DATABASE_URL = os.getenv('DATABASE_URL')


def get_db_connection():
    """
    Creates and returns a new psycopg2 database connection.
    Supports either DATABASE_URL (for Neon Postgres / Render)
    or individual connection parameters (for local development).
    """
    if DATABASE_URL:
        db_url = DATABASE_URL.strip()
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        if 'sslmode=' not in db_url and 'localhost' not in db_url and '127.0.0.1' not in db_url:
            separator = '&' if '?' in db_url else '?'
            db_url = f"{db_url}{separator}sslmode=require"
        return psycopg2.connect(db_url)

    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


def get_all_tables(conn):
    """Returns a set of all lowercase table names in the public schema."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT LOWER(table_name) FROM information_schema.tables WHERE table_schema = 'public';")
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        logger.warning("Could not fetch table names: %s", e)
        return set()


def get_table_columns(conn, table_name):
    """
    Dynamically inspects and returns a set of lowercase column names that physically
    exist in the specified table. Prevents SQL errors from missing/mismatched columns.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT LOWER(column_name) 
                FROM information_schema.columns 
                WHERE LOWER(table_name) = LOWER(%s);
            """, (table_name,))
            return {row[0] for row in cur.fetchall()}
    except Exception as e:
        logger.warning("Could not inspect columns for table %s: %s", table_name, e)
        return set()


# ==========================================================
# AUTH TOKEN HELPERS & DECORATOR
# ==========================================================

def generate_token(user_id, role, assigned_class, email, name, school_id=1):
    """Generates a tamper-proof signed auth token containing school_id and role."""
    return auth_serializer.dumps({
        "user_id": user_id,
        "school_id": school_id,
        "role": role,
        "assigned_class": assigned_class,
        "email": email,
        "name": name
    })


def decode_token(token):
    """Decodes and validates token (valid for 30 days). Defaults school_id to 1 if omitted."""
    try:
        data = auth_serializer.loads(token, max_age=2592000)
        if "school_id" not in data:
            data["school_id"] = 1
        return data
    except Exception:
        return None


def get_current_user():
    """Extracts authenticated user from Authorization header or ?token= query."""
    auth_header = request.headers.get('Authorization', '')
    token = None
    if auth_header.startswith('Bearer '):
        token = auth_header.split(' ', 1)[1].strip()
    elif request.args.get('token'):
        token = request.args.get('token').strip()

    if not token:
        return None

    return decode_token(token)


def require_auth(f):
    """Route decorator enforcing token authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Unauthorized: Token missing or invalid"}), 401
        return f(user, *args, **kwargs)
    return decorated


def compute_grade(score):
    """
    Auto-calculates letter grade:
      A = 70+
      B = 60-69
      C = 50-59
      D = 40-49
      F = below 40
    """
    try:
        s = float(score)
        if s >= 70:
            return 'A'
        elif s >= 60:
            return 'B'
        elif s >= 50:
            return 'C'
        elif s >= 40:
            return 'D'
        else:
            return 'F'
    except (ValueError, TypeError):
        return 'N/A'


def compute_grade_remarks(score):
    """Auto-calculates remark corresponding to score."""
    try:
        s = float(score)
        if s >= 70:
            return 'Distinction'
        elif s >= 60:
            return 'Credit'
        elif s >= 50:
            return 'Pass'
        elif s >= 40:
            return 'Fair'
        else:
            return 'Needs Improvement'
    except (ValueError, TypeError):
        return 'N/A'


# ==========================================================
# DATABASE INITIALIZATION & RELATIONAL MIGRATION
# ==========================================================

def init_db():
    """
    Initializes database schema with safe column additions and makes all non-essential
    columns nullable so insert/update operations never fail on missing columns.
    Runs on startup for both direct execution and Gunicorn on Render.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 1. Teachers Table for Auth & RBAC
            cur.execute("""
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
                CREATE INDEX IF NOT EXISTS idx_teachers_email ON teachers (email);
            """)

            # 2. Students Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    id SERIAL PRIMARY KEY,
                    school_id INT DEFAULT 1,
                    full_name VARCHAR(255),
                    student_name VARCHAR(255),
                    roll_no VARCHAR(100),
                    class VARCHAR(100),
                    student_class VARCHAR(100),
                    gender VARCHAR(20) DEFAULT 'Unspecified',
                    parent_contact VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                ALTER TABLE students ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;
                ALTER TABLE students ADD COLUMN IF NOT EXISTS full_name VARCHAR(255);
                ALTER TABLE students ADD COLUMN IF NOT EXISTS student_name VARCHAR(255);
                ALTER TABLE students ADD COLUMN IF NOT EXISTS roll_no VARCHAR(100);
                ALTER TABLE students ADD COLUMN IF NOT EXISTS class VARCHAR(100);
                ALTER TABLE students ADD COLUMN IF NOT EXISTS student_class VARCHAR(100);
                ALTER TABLE students ADD COLUMN IF NOT EXISTS gender VARCHAR(20) DEFAULT 'Unspecified';
                ALTER TABLE students ADD COLUMN IF NOT EXISTS parent_contact VARCHAR(100);
                CREATE INDEX IF NOT EXISTS idx_students_roll ON students (roll_no);
            """)

            # 3. Attendance Table (Make all non-essential columns nullable)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id SERIAL PRIMARY KEY,
                    student_id INT,
                    school_id INT DEFAULT 1,
                    roll_no VARCHAR(100),
                    student_name VARCHAR(255),
                    att_date DATE,
                    date DATE,
                    status VARCHAR(20),
                    marked_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                ALTER TABLE attendance ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;
                ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_id INT;
                ALTER TABLE attendance ADD COLUMN IF NOT EXISTS roll_no VARCHAR(100);
                ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_name VARCHAR(255);
                ALTER TABLE attendance ADD COLUMN IF NOT EXISTS att_date DATE;
                ALTER TABLE attendance ADD COLUMN IF NOT EXISTS date DATE;
                ALTER TABLE attendance ADD COLUMN IF NOT EXISTS status VARCHAR(20);
                ALTER TABLE attendance ADD COLUMN IF NOT EXISTS marked_by VARCHAR(100);
            """)

            # Make non-essential columns in attendance nullable so inserts never fail
            for col in ['student_id', 'school_id', 'marked_by', 'status']:
                try:
                    cur.execute(f"ALTER TABLE attendance ALTER COLUMN {col} DROP NOT NULL;")
                except Exception:
                    pass

            # 4. Grades Table (Make all non-essential columns nullable)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS grades (
                    id SERIAL PRIMARY KEY,
                    student_id INT,
                    school_id INT DEFAULT 1,
                    roll_no VARCHAR(100),
                    student_name VARCHAR(255),
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
                ALTER TABLE grades ADD COLUMN IF NOT EXISTS subject VARCHAR(255);
                ALTER TABLE grades ADD COLUMN IF NOT EXISTS subject_name VARCHAR(255);
                ALTER TABLE grades ADD COLUMN IF NOT EXISTS score NUMERIC(5, 2);
                ALTER TABLE grades ADD COLUMN IF NOT EXISTS grade VARCHAR(20);
                ALTER TABLE grades ADD COLUMN IF NOT EXISTS remarks VARCHAR(255);
                ALTER TABLE grades ADD COLUMN IF NOT EXISTS term VARCHAR(100) DEFAULT 'First Term 2026/2027';
                ALTER TABLE grades ADD COLUMN IF NOT EXISTS session VARCHAR(100) DEFAULT '2026/2027';
            """)

            # Make non-essential columns in grades nullable so inserts never fail
            for col in ['student_id', 'school_id', 'grade', 'remarks', 'term', 'session', 'score', 'subject']:
                try:
                    cur.execute(f"ALTER TABLE grades ALTER COLUMN {col} DROP NOT NULL;")
                except Exception:
                    pass

            # Safe data migration from legacy student_records if present
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'student_records';")
            if cur.fetchone():
                logger.info("Syncing legacy student_records into normalized tables...")
                try:
                    cur.execute("""
                        INSERT INTO students (school_id, full_name, student_name, roll_no, class, student_class, gender, parent_contact)
                        SELECT 
                            1 AS school_id,
                            COALESCE(MAX(NULLIF(student_name, '')), 'Student') AS full_name,
                            COALESCE(MAX(NULLIF(student_name, '')), 'Student') AS student_name,
                            roll_no,
                            COALESCE(MAX(NULLIF(student_class, '')), 'Primary 1') AS class,
                            COALESCE(MAX(NULLIF(student_class, '')), 'Primary 1') AS student_class,
                            COALESCE(MAX(NULLIF(gender, '')), 'Unspecified') AS gender,
                            COALESCE(MAX(NULLIF(parent_contact, '')), '') AS parent_contact
                        FROM student_records
                        WHERE roll_no IS NOT NULL
                        GROUP BY roll_no
                        ON CONFLICT DO NOTHING;
                    """)
                except Exception as _sync_err:
                    logger.debug("Student sync note: %s", _sync_err)

            # Seed default admin if missing
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'admin@edutrack.com' LIMIT 1;")
            if not cur.fetchone():
                admin_pw = generate_password_hash('admin123')
                cur.execute("""
                    INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                    VALUES (1, 'Administrator', 'admin@edutrack.com', %s, 'admin', 'All');
                """, (admin_pw,))
                logger.info("Default admin created: admin@edutrack.com / admin123")

            # Seed default Primary 1 teacher if missing
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'teacher@edutrack.com' LIMIT 1;")
            if not cur.fetchone():
                teacher_pw = generate_password_hash('teacher123')
                cur.execute("""
                    INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                    VALUES (1, 'Sarah Jenkins', 'teacher@edutrack.com', %s, 'teacher', 'Primary 1');
                """, (teacher_pw,))
                logger.info("Default Primary 1 teacher created: teacher@edutrack.com / teacher123")

            # Seed default Primary 2 teacher if missing
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'teacher2@edutrack.com' LIMIT 1;")
            if not cur.fetchone():
                teacher2_pw = generate_password_hash('teacher123')
                cur.execute("""
                    INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                    VALUES (1, 'Michael Obi', 'teacher2@edutrack.com', %s, 'teacher', 'Primary 2');
                """, (teacher2_pw,))
                logger.info("Default Primary 2 teacher created: teacher2@edutrack.com / teacher123")

        conn.commit()
        logger.info("Database schema initialized and verified successfully.")
    except Exception as e:
        logger.warning("Notice during database initialization: %s", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


# Auto-initialize database on application import so Gunicorn initializes on Render
try:
    init_db()
except Exception as _startup_e:
    logger.warning("Startup database init notice: %s", _startup_e)


# ==========================================================
# PUBLIC HEALTH & STATUS ENDPOINTS
# ==========================================================

@app.route('/', methods=['GET'])
def root():
    """Root endpoint for status check and API documentation."""
    return jsonify({
        "status": "online",
        "service": "EduTrack - Student Attendance & Academic Performance API",
        "database": DB_NAME if not DATABASE_URL else "PostgreSQL (Neon / Cloud)",
        "architecture": "Normalized Relational Design (students, attendance, grades, teachers)",
        "auth": "Token-based (Bearer Token / RBAC / Multi-tenant school_id)",
        "endpoints": [
            "GET /api/health",
            "POST /api/auth/register",
            "POST /api/auth/login",
            "GET /api/auth/me",
            "GET /api/dashboard",
            "GET /api/students",
            "POST /api/students",
            "PUT /api/students/<student_id>",
            "DELETE /api/students/<student_id>",
            "POST /api/attendance",
            "GET /api/report/<roll_no>",
            "POST /api/grades",
            "GET /api/grades/<roll_no>",
            "DELETE /api/grades/<grade_id>"
        ]
    }), 200


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint testing database connectivity."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "database_error": str(e)}), 503
    finally:
        if conn:
            conn.close()


# ==========================================================
# AUTHENTICATION ENDPOINTS
# ==========================================================

@app.route('/api/auth/register', methods=['POST'])
def register():
    """
    Registers a new teacher account:
    Body: { "name": "...", "email": "...", "password": "...", "assigned_class": "Primary 1", "school_id": 1 }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    name = str(data.get('name', '')).strip()
    email = str(data.get('email', '')).strip().lower()
    password = str(data.get('password', '')).strip()
    assigned_class = str(data.get('assigned_class', 'Primary 1')).strip()
    role = str(data.get('role', 'teacher')).strip().lower()

    try:
        school_id = int(data.get('school_id', 1))
    except (ValueError, TypeError):
        school_id = 1

    if role not in ('teacher', 'admin'):
        role = 'teacher'

    if not name or not email or not password:
        return jsonify({"error": "Name, email, and password are required"}), 400

    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM teachers WHERE LOWER(email) = %s LIMIT 1;", (email,))
            if cur.fetchone():
                return jsonify({"error": f"An account with email '{email}' already exists"}), 409

            password_hash = generate_password_hash(password)
            cur.execute("""
                INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (school_id, name, email, password_hash, role, assigned_class))
            user_id = cur.fetchone()[0]

        conn.commit()

        token = generate_token(user_id, role, assigned_class, email, name, school_id)
        return jsonify({
            "message": "Account registered successfully",
            "token": token,
            "user": {
                "id": user_id,
                "school_id": school_id,
                "name": name,
                "email": email,
                "role": role,
                "assigned_class": assigned_class
            }
        }), 201

    except Exception as e:
        logger.error("Error registering teacher: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while registering account", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/auth/login', methods=['POST'])
def login():
    """
    Authenticates teacher or admin:
    Body: { "email": "...", "password": "..." }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    email = str(data.get('email', '')).strip().lower()
    password = str(data.get('password', '')).strip()

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, school_id, name, email, password_hash, role, assigned_class
                FROM teachers
                WHERE LOWER(email) = %s
                LIMIT 1;
            """, (email,))
            user = cur.fetchone()

        if not user or not check_password_hash(user['password_hash'], password):
            return jsonify({"error": "Invalid email or password"}), 401

        school_id = user.get('school_id') or 1
        token = generate_token(
            user['id'],
            user['role'],
            user['assigned_class'] or 'Primary 1',
            user['email'],
            user['name'],
            school_id
        )

        return jsonify({
            "message": "Login successful",
            "token": token,
            "user": {
                "id": user['id'],
                "school_id": school_id,
                "name": user['name'],
                "email": user['email'],
                "role": user['role'],
                "assigned_class": user['assigned_class'] or 'Primary 1'
            }
        }), 200

    except Exception as e:
        logger.error("Error during login: %s", e)
        return jsonify({"error": "Database error during login", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/auth/me', methods=['GET'])
@require_auth
def get_current_user_profile(current_user):
    """Returns currently authenticated user profile including school_id."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, school_id, name, email, role, assigned_class
                FROM teachers
                WHERE id = %s
                LIMIT 1;
            """, (current_user['user_id'],))
            user = cur.fetchone()

        if not user:
            return jsonify({"error": "User no longer exists"}), 404

        return jsonify({"user": user}), 200

    except Exception as e:
        logger.error("Error in /api/auth/me: %s", e)
        return jsonify({"error": "Database error", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# PROTECTED DASHBOARD ENDPOINT
# ==========================================================

@app.route('/api/dashboard', methods=['GET'])
@require_auth
def get_dashboard_stats(current_user):
    date_param = request.args.get('date')
    target_date = date_param.strip() if date_param else datetime.date.today().isoformat()

    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            total_students = 0

            # 1. Total Students Count
            if 'students' in all_tables:
                st_cols = get_table_columns(conn, 'students')
                class_col = 'class' if 'class' in st_cols else ('student_class' if 'student_class' in st_cols else None)
                if is_admin or not class_col:
                    cur.execute("SELECT COUNT(*) FROM students;")
                else:
                    cur.execute(f"SELECT COUNT(*) FROM students WHERE LOWER({class_col}) = LOWER(%s);", (assigned_class,))
                total_students = cur.fetchone()[0] or 0
            elif 'student_records' in all_tables:
                if is_admin:
                    cur.execute("SELECT COUNT(DISTINCT roll_no) FROM student_records WHERE student_name IS NOT NULL;")
                else:
                    cur.execute("SELECT COUNT(DISTINCT roll_no) FROM student_records WHERE student_name IS NOT NULL AND LOWER(student_class) = LOWER(%s);", (assigned_class,))
                total_students = cur.fetchone()[0] or 0

            # 2. Present / Absent today
            present_today = 0
            absent_today = 0

            att_cols = get_table_columns(conn, 'attendance') if 'attendance' in all_tables else set()
            date_col = 'att_date' if 'att_date' in att_cols else ('date' if 'date' in att_cols else None)

            if att_cols and date_col:
                if is_admin:
                    cur.execute(f"""
                        SELECT 
                            COUNT(*) FILTER (WHERE status = 'Present'),
                            COUNT(*) FILTER (WHERE status = 'Absent')
                        FROM attendance
                        WHERE {date_col} = %s;
                    """, (target_date,))
                else:
                    cur.execute(f"""
                        SELECT 
                            COUNT(*) FILTER (WHERE a.status = 'Present'),
                            COUNT(*) FILTER (WHERE a.status = 'Absent')
                        FROM attendance a
                        LEFT JOIN students s ON a.student_id = s.id
                        WHERE a.{date_col} = %s AND (
                            (s.class IS NOT NULL AND LOWER(s.class) = LOWER(%s))
                            OR (a.roll_no IS NOT NULL AND LOWER(a.roll_no) IN (
                                SELECT LOWER(roll_no) FROM students WHERE LOWER(COALESCE(class, student_class)) = LOWER(%s)
                            ))
                        );
                    """, (target_date, assigned_class, assigned_class))
                row = cur.fetchone()
                if row:
                    present_today = row[0] or 0
                    absent_today = row[1] or 0

            # Fallback to student_records if attendance table had 0 results
            if present_today == 0 and absent_today == 0 and 'student_records' in all_tables:
                if is_admin:
                    cur.execute("""
                        SELECT 
                            COUNT(DISTINCT roll_no) FILTER (WHERE status = 'Present'),
                            COUNT(DISTINCT roll_no) FILTER (WHERE status = 'Absent')
                        FROM student_records WHERE att_date = %s;
                    """, (target_date,))
                else:
                    cur.execute("""
                        SELECT 
                            COUNT(DISTINCT roll_no) FILTER (WHERE status = 'Present'),
                            COUNT(DISTINCT roll_no) FILTER (WHERE status = 'Absent')
                        FROM student_records WHERE att_date = %s AND LOWER(student_class) = LOWER(%s);
                    """, (target_date, assigned_class))
                sr_row = cur.fetchone()
                if sr_row:
                    present_today = sr_row[0] or 0
                    absent_today = sr_row[1] or 0

            total_marked_today = present_today + absent_today
            attendance_percentage = round((present_today / total_marked_today) * 100, 1) if total_marked_today > 0 else 0.0

            return jsonify({
                "date": target_date,
                "role": current_user.get('role'),
                "assigned_class": assigned_class if not is_admin else "All",
                "total_students": total_students,
                "present_today": present_today,
                "absent_today": absent_today,
                "total_marked_today": total_marked_today,
                "attendance_percentage": attendance_percentage
            }), 200

    except Exception as e:
        logger.error("Error fetching dashboard stats: %s", e)
        return jsonify({"error": "Database error while fetching dashboard stats", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# PROTECTED STUDENTS ENDPOINTS
# ==========================================================

@app.route('/api/students', methods=['GET'])
@require_auth
def get_students(current_user):
    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            all_tables = get_all_tables(conn)
            students = []

            if 'students' in all_tables:
                st_cols = get_table_columns(conn, 'students')
                name_expr = "COALESCE(full_name, student_name, 'Student')" if ('full_name' in st_cols and 'student_name' in st_cols) else ("full_name" if 'full_name' in st_cols else "student_name")
                class_expr = "COALESCE(class, student_class, 'Primary 1')" if ('class' in st_cols and 'student_class' in st_cols) else ("class" if 'class' in st_cols else "student_class")
                gender_expr = "COALESCE(gender, 'Unspecified')" if 'gender' in st_cols else "'Unspecified'"
                contact_expr = "COALESCE(parent_contact, '')" if 'parent_contact' in st_cols else "''"
                school_expr = "school_id" if 'school_id' in st_cols else "1 AS school_id"

                if is_admin:
                    cur.execute(f"""
                        SELECT 
                            id,
                            {school_expr},
                            {name_expr} AS student_name,
                            roll_no,
                            {class_expr} AS student_class,
                            {gender_expr} AS gender,
                            {contact_expr} AS parent_contact
                        FROM students
                        ORDER BY roll_no ASC;
                    """)
                else:
                    cur.execute(f"""
                        SELECT 
                            id,
                            {school_expr},
                            {name_expr} AS student_name,
                            roll_no,
                            {class_expr} AS student_class,
                            {gender_expr} AS gender,
                            {contact_expr} AS parent_contact
                        FROM students
                        WHERE LOWER({class_expr}) = LOWER(%s)
                        ORDER BY roll_no ASC;
                    """, (assigned_class,))
                students = cur.fetchall()

            # Fallback to student_records if students was empty
            if not students and 'student_records' in all_tables:
                if is_admin:
                    cur.execute("""
                        SELECT 
                            MIN(id) AS id,
                            1 AS school_id,
                            COALESCE(MAX(NULLIF(student_name, '')), 'Student') AS student_name,
                            roll_no,
                            COALESCE(MAX(NULLIF(student_class, '')), 'Primary 1') AS student_class,
                            COALESCE(MAX(NULLIF(gender, '')), 'Unspecified') AS gender,
                            COALESCE(MAX(NULLIF(parent_contact, '')), '') AS parent_contact
                        FROM student_records
                        WHERE roll_no IS NOT NULL
                        GROUP BY roll_no
                        ORDER BY roll_no ASC;
                    """)
                else:
                    cur.execute("""
                        SELECT 
                            MIN(id) AS id,
                            1 AS school_id,
                            COALESCE(MAX(NULLIF(student_name, '')), 'Student') AS student_name,
                            roll_no,
                            COALESCE(MAX(NULLIF(student_class, '')), 'Primary 1') AS student_class,
                            COALESCE(MAX(NULLIF(gender, '')), 'Unspecified') AS gender,
                            COALESCE(MAX(NULLIF(parent_contact, '')), '') AS parent_contact
                        FROM student_records
                        WHERE roll_no IS NOT NULL AND LOWER(student_class) = LOWER(%s)
                        GROUP BY roll_no
                        ORDER BY roll_no ASC;
                    """, (assigned_class,))
                students = cur.fetchall()

        result = [
            {
                "id": row["id"],
                "school_id": row.get("school_id", 1),
                "student_name": row["student_name"],
                "name": row["student_name"],
                "full_name": row["student_name"],
                "roll_no": row["roll_no"],
                "student_class": row["student_class"] or "Primary 1",
                "class": row["student_class"] or "Primary 1",
                "gender": row["gender"] or "Unspecified",
                "parent_contact": row["parent_contact"] or ""
            }
            for row in students
        ]
        return jsonify(result), 200

    except Exception as e:
        logger.error("Error fetching students: %s", e)
        return jsonify({"error": "Database error while retrieving students", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/students', methods=['POST'])
@require_auth
def add_student(current_user):
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    student_name = data.get('student_name') or data.get('name') or data.get('full_name')
    roll_no = data.get('roll_no')
    gender = data.get('gender') or 'Unspecified'
    parent_contact = data.get('parent_contact') or data.get('contact') or ''

    if is_admin:
        student_class = data.get('student_class') or data.get('class') or 'Primary 1'
    else:
        student_class = assigned_class or 'Primary 1'

    if not student_name or not roll_no:
        return jsonify({"error": "Both 'student_name' and 'roll_no' are required"}), 400

    student_name = str(student_name).strip()
    roll_no = str(roll_no).strip()
    student_class = str(student_class).strip()
    gender = str(gender).strip()
    parent_contact = str(parent_contact).strip()

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            new_id = 1

            # 1. Check duplicate roll_no
            if 'students' in all_tables:
                cur.execute("SELECT 1 FROM students WHERE LOWER(roll_no) = LOWER(%s) LIMIT 1;", (roll_no,))
                if cur.fetchone():
                    return jsonify({"error": f"Student with roll number '{roll_no}' already exists"}), 409

                cols = get_table_columns(conn, 'students')
                fields = {}
                if 'full_name' in cols:
                    fields['full_name'] = student_name
                if 'student_name' in cols:
                    fields['student_name'] = student_name
                if 'roll_no' in cols:
                    fields['roll_no'] = roll_no
                if 'class' in cols:
                    fields['class'] = student_class
                if 'student_class' in cols:
                    fields['student_class'] = student_class
                if 'gender' in cols:
                    fields['gender'] = gender
                if 'parent_contact' in cols:
                    fields['parent_contact'] = parent_contact
                if 'school_id' in cols:
                    fields['school_id'] = school_id

                col_names = list(fields.keys())
                placeholders = ', '.join(['%s'] * len(col_names))
                cur.execute(f"""
                    INSERT INTO students ({', '.join(col_names)})
                    VALUES ({placeholders})
                    RETURNING id;
                """, list(fields.values()))
                new_id = cur.fetchone()[0]

            # 2. Sync into student_records if table exists
            if 'student_records' in all_tables:
                cur.execute("""
                    INSERT INTO student_records (student_name, roll_no, student_class, gender, parent_contact)
                    VALUES (%s, %s, %s, %s, %s);
                """, (student_name, roll_no, student_class, gender, parent_contact))

        conn.commit()
        return jsonify({
            "message": "Student added successfully",
            "student": {
                "id": new_id,
                "school_id": school_id,
                "student_name": student_name,
                "name": student_name,
                "full_name": student_name,
                "roll_no": roll_no,
                "student_class": student_class,
                "class": student_class,
                "gender": gender,
                "parent_contact": parent_contact
            }
        }), 201

    except Exception as e:
        logger.error("Error adding student: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while adding student", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/students/<string:student_id>', methods=['PUT'])
@require_auth
def update_student(current_user, student_id):
    """Updates student demographic information in both students and student_records."""
    student_id = student_id.strip()
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    new_name = str(data.get('student_name') or data.get('name') or data.get('full_name', '')).strip()
    new_roll = str(data.get('roll_no', '')).strip()
    new_gender = str(data.get('gender', 'Unspecified')).strip()
    new_contact = str(data.get('parent_contact') or data.get('contact', '')).strip()

    if is_admin:
        new_class = str(data.get('student_class') or data.get('class', 'Primary 1')).strip()
    else:
        new_class = assigned_class

    if not new_name or not new_roll:
        return jsonify({"error": "Student name and roll number cannot be empty"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            old_roll_no = new_roll
            actual_id = 1

            if 'students' in all_tables:
                cur.execute("""
                    SELECT id, roll_no, COALESCE(class, student_class)
                    FROM students
                    WHERE id::text = %s OR LOWER(roll_no) = LOWER(%s)
                    LIMIT 1;
                """, (student_id, student_id))
                row = cur.fetchone()
                if row:
                    actual_id, old_roll_no, old_class = row
                    if not is_admin and str(old_class).lower() != str(assigned_class).lower():
                        return jsonify({"error": "Access denied: You can only edit students in your assigned class"}), 403

                # Update students table dynamically
                cols = get_table_columns(conn, 'students')
                updates = []
                vals = []
                if 'full_name' in cols:
                    updates.append("full_name = %s")
                    vals.append(new_name)
                if 'student_name' in cols:
                    updates.append("student_name = %s")
                    vals.append(new_name)
                if 'roll_no' in cols:
                    updates.append("roll_no = %s")
                    vals.append(new_roll)
                if 'class' in cols:
                    updates.append("class = %s")
                    vals.append(new_class)
                if 'student_class' in cols:
                    updates.append("student_class = %s")
                    vals.append(new_class)
                if 'gender' in cols:
                    updates.append("gender = %s")
                    vals.append(new_gender)
                if 'parent_contact' in cols:
                    updates.append("parent_contact = %s")
                    vals.append(new_contact)

                if updates:
                    cur.execute(f"UPDATE students SET {', '.join(updates)} WHERE id::text = %s OR LOWER(roll_no) = LOWER(%s);", vals + [student_id, student_id])

            if 'student_records' in all_tables:
                cur.execute("""
                    UPDATE student_records
                    SET student_name = %s,
                        roll_no = %s,
                        student_class = %s,
                        gender = %s,
                        parent_contact = %s
                    WHERE LOWER(roll_no) = LOWER(%s) OR id::text = %s;
                """, (new_name, new_roll, new_class, new_gender, new_contact, old_roll_no, student_id))

        conn.commit()
        return jsonify({
            "message": f"Student '{new_name}' updated successfully",
            "student": {
                "id": actual_id,
                "school_id": school_id,
                "student_name": new_name,
                "name": new_name,
                "full_name": new_name,
                "roll_no": new_roll,
                "student_class": new_class,
                "class": new_class,
                "gender": new_gender,
                "parent_contact": new_contact
            }
        }), 200

    except Exception as e:
        logger.error("Error updating student: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while updating student", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/students/<string:student_id>', methods=['DELETE'])
@require_auth
def delete_student(current_user, student_id):
    student_id = student_id.strip()
    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            roll_no = student_id
            st_id = None
            student_name = "Student"

            if 'students' in all_tables:
                cur.execute("""
                    SELECT id, roll_no, COALESCE(full_name, student_name), COALESCE(class, student_class)
                    FROM students
                    WHERE id::text = %s OR LOWER(roll_no) = LOWER(%s)
                    LIMIT 1;
                """, (student_id, student_id))
                row = cur.fetchone()
                if row:
                    st_id, roll_no, student_name, st_class = row
                    if not is_admin and str(st_class).lower() != str(assigned_class).lower():
                        return jsonify({"error": "Access denied: You can only remove students in your assigned class"}), 403

                    cur.execute("DELETE FROM students WHERE id::text = %s OR LOWER(roll_no) = LOWER(%s);", (student_id, student_id))

            # Cascade manually to ensure everything is deleted regardless of FK constraints
            if 'attendance' in all_tables:
                att_cols = get_table_columns(conn, 'attendance')
                if 'student_id' in att_cols and st_id:
                    cur.execute("DELETE FROM attendance WHERE student_id = %s;", (st_id,))
                if 'roll_no' in att_cols:
                    cur.execute("DELETE FROM attendance WHERE LOWER(roll_no) = LOWER(%s);", (roll_no,))

            if 'grades' in all_tables:
                gr_cols = get_table_columns(conn, 'grades')
                if 'student_id' in gr_cols and st_id:
                    cur.execute("DELETE FROM grades WHERE student_id = %s;", (st_id,))
                if 'roll_no' in gr_cols:
                    cur.execute("DELETE FROM grades WHERE LOWER(roll_no) = LOWER(%s);", (roll_no,))

            if 'student_records' in all_tables:
                cur.execute("DELETE FROM student_records WHERE LOWER(roll_no) = LOWER(%s) OR id::text = %s;", (roll_no, student_id))

        conn.commit()
        return jsonify({
            "message": f"Student '{student_name}' (Roll: {roll_no}) and all records deleted successfully."
        }), 200

    except Exception as e:
        logger.error("Error deleting student: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while deleting student", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# ATTENDANCE ENDPOINTS (DYNAMIC SCHEMA COMPATIBILITY)
# ==========================================================

@app.route('/api/attendance', methods=['POST'])
@require_auth
def save_attendance(current_user):
    """
    Requirement 2: Mark Attendance
    Dynamically maps to whatever columns exist in the attendance table.
    Deletes previous attendance for the student on that date to prevent duplicates
    without relying on strict ON CONFLICT constraints that may be missing in Neon.
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    records = data if isinstance(data, list) else [data]
    if not records:
        return jsonify({"error": "Attendance records array cannot be empty"}), 400

    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)
    marked_by = current_user.get('name') or current_user.get('email') or 'Teacher'

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            cols = get_table_columns(conn, 'attendance') if 'attendance' in all_tables else set()
            date_col = 'att_date' if 'att_date' in cols else ('date' if 'date' in cols else None)

            saved_count = 0
            for record in records:
                roll_no = record.get('roll_no')
                att_date = record.get('date') or record.get('att_date')
                status = record.get('status', 'Present')

                if status not in ('Present', 'Absent'):
                    status = 'Present'

                if not roll_no or not att_date:
                    continue

                roll_no = str(roll_no).strip()

                # Look up student in students table or student_records
                student_id = None
                student_name = record.get('student_name') or record.get('name')
                student_class = record.get('student_class') or record.get('class')

                if 'students' in all_tables:
                    cur.execute("""
                        SELECT id, COALESCE(full_name, student_name), COALESCE(class, student_class)
                        FROM students 
                        WHERE LOWER(roll_no) = LOWER(%s) OR id::text = %s
                        LIMIT 1;
                    """, (roll_no, str(record.get('id', ''))))
                    st_row = cur.fetchone()
                    if st_row:
                        student_id = st_row[0]
                        student_name = student_name or st_row[1] or "Student"
                        student_class = student_class or st_row[2] or "Primary 1"

                if not student_class and 'student_records' in all_tables:
                    cur.execute("""
                        SELECT id, student_name, student_class 
                        FROM student_records 
                        WHERE LOWER(roll_no) = LOWER(%s) AND student_name IS NOT NULL 
                        LIMIT 1;
                    """, (roll_no,))
                    sr_row = cur.fetchone()
                    if sr_row:
                        student_name = student_name or sr_row[1] or "Student"
                        student_class = student_class or sr_row[2] or "Primary 1"

                student_name = student_name or "Student"
                student_class = student_class or "Primary 1"

                # If teacher, enforce class boundary
                if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                    continue

                # 1. Save in attendance table if table exists
                if cols and date_col:
                    # Prevent duplicate days: delete existing record for this student on this date
                    if 'student_id' in cols and student_id:
                        cur.execute(f"DELETE FROM attendance WHERE student_id = %s AND {date_col} = %s;", (student_id, att_date))
                    elif 'roll_no' in cols:
                        cur.execute(f"DELETE FROM attendance WHERE LOWER(roll_no) = LOWER(%s) AND {date_col} = %s;", (roll_no, att_date))

                    # Insert only columns that exist
                    fields = {}
                    if 'student_id' in cols and student_id:
                        fields['student_id'] = student_id
                    if 'school_id' in cols:
                        fields['school_id'] = school_id
                    if 'roll_no' in cols:
                        fields['roll_no'] = roll_no
                    if 'student_name' in cols:
                        fields['student_name'] = student_name
                    if 'att_date' in cols:
                        fields['att_date'] = att_date
                    elif 'date' in cols:
                        fields['date'] = att_date
                    if 'status' in cols:
                        fields['status'] = status
                    if 'marked_by' in cols:
                        fields['marked_by'] = marked_by

                    col_names = list(fields.keys())
                    placeholders = ', '.join(['%s'] * len(col_names))
                    cur.execute(f"INSERT INTO attendance ({', '.join(col_names)}) VALUES ({placeholders});", list(fields.values()))

                # 2. Sync to student_records as backup
                if 'student_records' in all_tables:
                    cur.execute("""
                        DELETE FROM student_records 
                        WHERE LOWER(roll_no) = LOWER(%s) AND att_date = %s;
                    """, (roll_no, att_date))
                    cur.execute("""
                        INSERT INTO student_records (student_name, roll_no, student_class, att_date, status)
                        VALUES (%s, %s, %s, %s, %s);
                    """, (student_name, roll_no, student_class, att_date, status))

                saved_count += 1

        conn.commit()
        return jsonify({
            "message": "Attendance saved successfully",
            "saved_count": saved_count
        }), 201

    except Exception as e:
        logger.error("Error saving attendance: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while saving attendance", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# REPORT & GRADES ENDPOINTS (DYNAMIC SCHEMA COMPATIBILITY)
# ==========================================================

@app.route('/api/report/<string:roll_no>', methods=['GET'])
@app.route('/report/<string:roll_no>', methods=['GET'])
@require_auth
def get_student_report(current_user, roll_no):
    roll_no = roll_no.strip()
    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)

            # 1. Student demographic info
            student_id = None
            full_name = None
            st_roll = roll_no
            st_class = "Primary 1"
            gender = "Unspecified"
            parent_contact = ""

            if 'students' in all_tables:
                cur.execute("""
                    SELECT id, COALESCE(full_name, student_name), roll_no, COALESCE(class, student_class), gender, parent_contact
                    FROM students
                    WHERE LOWER(roll_no) = LOWER(%s)
                    LIMIT 1;
                """, (roll_no,))
                row = cur.fetchone()
                if row:
                    student_id, full_name, st_roll, st_class, gender, parent_contact = row

            if not full_name and 'student_records' in all_tables:
                cur.execute("""
                    SELECT id, student_name, roll_no, student_class, gender, parent_contact
                    FROM student_records
                    WHERE LOWER(roll_no) = LOWER(%s) AND student_name IS NOT NULL
                    LIMIT 1;
                """, (roll_no,))
                sr_row = cur.fetchone()
                if sr_row:
                    student_id, full_name, st_roll, st_class, gender, parent_contact = sr_row

            if not full_name:
                return jsonify({"error": f"No student found with roll number '{roll_no}'"}), 404

            st_class = st_class or "Primary 1"
            if not is_admin and str(st_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            # 2. Attendance summary
            total = 0
            present = 0
            absent = 0

            att_cols = get_table_columns(conn, 'attendance') if 'attendance' in all_tables else set()
            if att_cols:
                date_col = 'att_date' if 'att_date' in att_cols else ('date' if 'date' in att_cols else None)
                if 'student_id' in att_cols and student_id:
                    cur.execute("""
                        SELECT 
                            COUNT(*) AS total,
                            COUNT(*) FILTER (WHERE status = 'Present') AS present,
                            COUNT(*) FILTER (WHERE status = 'Absent') AS absent
                        FROM attendance
                        WHERE student_id = %s;
                    """, (student_id,))
                elif 'roll_no' in att_cols:
                    cur.execute("""
                        SELECT 
                            COUNT(*) AS total,
                            COUNT(*) FILTER (WHERE status = 'Present') AS present,
                            COUNT(*) FILTER (WHERE status = 'Absent') AS absent
                        FROM attendance
                        WHERE LOWER(roll_no) = LOWER(%s);
                    """, (roll_no,))
                att_res = cur.fetchone()
                if att_res:
                    total, present, absent = att_res

            # Fallback to student_records if attendance table had 0 results
            if total == 0 and 'student_records' in all_tables:
                cur.execute("""
                    SELECT 
                        COUNT(*) FILTER (WHERE att_date IS NOT NULL) AS total,
                        COUNT(*) FILTER (WHERE att_date IS NOT NULL AND status = 'Present') AS present,
                        COUNT(*) FILTER (WHERE att_date IS NOT NULL AND status = 'Absent') AS absent
                    FROM student_records
                    WHERE LOWER(roll_no) = LOWER(%s);
                """, (roll_no,))
                sr_att = cur.fetchone()
                if sr_att and sr_att[0]:
                    total, present, absent = sr_att

            total = total or 0
            present = present or 0
            absent = absent or 0
            percentage = round((present / total) * 100, 1) if total > 0 else 0.0

            return jsonify({
                "id": student_id,
                "school_id": school_id,
                "roll_no": st_roll,
                "student_name": full_name or "Student",
                "name": full_name or "Student",
                "full_name": full_name or "Student",
                "student_class": st_class,
                "class": st_class,
                "gender": gender or "Unspecified",
                "parent_contact": parent_contact or "",
                "present": present,
                "absent": absent,
                "total": total,
                "percentage": percentage
            }), 200

    except Exception as e:
        logger.error("Error generating report: %s", e)
        return jsonify({"error": "Database error while generating report", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/grades', methods=['POST'])
@require_auth
def add_grade(current_user):
    """
    Requirement 1: Record Grade
    Dynamically maps Subject, Score, Grade, Remarks to real existing columns in Neon.
    Never fails on missing or null columns.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    roll_no = str(data.get('roll_no', '')).strip()
    subject = str(data.get('subject') or data.get('subject_name', '')).strip()
    score = data.get('score')
    term = str(data.get('term') or 'First Term 2026/2027').strip()
    session = str(data.get('session') or '2026/2027').strip()

    if not roll_no or not subject or score is None:
        return jsonify({"error": "'roll_no', 'subject', and 'score' are required"}), 400

    try:
        score_val = round(float(score), 2)
        if score_val < 0 or score_val > 100:
            return jsonify({"error": "'score' must be between 0 and 100"}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "'score' must be a valid numeric value between 0 and 100"}), 400

    grade_letter = compute_grade(score_val)
    remarks = compute_grade_remarks(score_val)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            student_id = None
            student_name = data.get('student_name') or data.get('name')
            student_class = None

            # 1. Student Lookup
            if 'students' in all_tables:
                cur.execute("""
                    SELECT id, COALESCE(full_name, student_name), COALESCE(class, student_class)
                    FROM students 
                    WHERE LOWER(roll_no) = LOWER(%s)
                    LIMIT 1;
                """, (roll_no,))
                st_row = cur.fetchone()
                if st_row:
                    student_id = st_row[0]
                    student_name = student_name or st_row[1]
                    student_class = st_row[2]

            if not student_class and 'student_records' in all_tables:
                cur.execute("""
                    SELECT id, student_name, student_class
                    FROM student_records
                    WHERE LOWER(roll_no) = LOWER(%s) AND student_name IS NOT NULL
                    LIMIT 1;
                """, (roll_no,))
                sr_row = cur.fetchone()
                if sr_row:
                    student_name = student_name or sr_row[1]
                    student_class = sr_row[2]

            student_name = student_name or "Student"
            student_class = student_class or "Primary 1"

            # Check teacher permission
            if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            # 2. Dynamic insertion into grades table
            cols = get_table_columns(conn, 'grades') if 'grades' in all_tables else set()
            grade_id = 1

            if cols:
                # Determine subject column name
                sub_col = 'subject' if 'subject' in cols else ('subject_name' if 'subject_name' in cols else None)

                # Check if existing grade for this student and subject exists
                existing_id = None
                if sub_col:
                    if 'student_id' in cols and student_id:
                        cur.execute(f"SELECT id FROM grades WHERE student_id = %s AND LOWER({sub_col}) = LOWER(%s) LIMIT 1;", (student_id, subject))
                    elif 'roll_no' in cols:
                        cur.execute(f"SELECT id FROM grades WHERE LOWER(roll_no) = LOWER(%s) AND LOWER({sub_col}) = LOWER(%s) LIMIT 1;", (roll_no, subject))
                    found = cur.fetchone()
                    if found:
                        existing_id = found[0]

                # Map data ONLY to columns that physically exist in the database table
                fields = {}
                if 'student_id' in cols and student_id:
                    fields['student_id'] = student_id
                if 'school_id' in cols:
                    fields['school_id'] = school_id
                if 'roll_no' in cols:
                    fields['roll_no'] = roll_no
                if 'student_name' in cols:
                    fields['student_name'] = student_name
                if 'subject' in cols:
                    fields['subject'] = subject
                elif 'subject_name' in cols:
                    fields['subject_name'] = subject
                if 'score' in cols:
                    fields['score'] = score_val
                if 'grade' in cols:
                    fields['grade'] = grade_letter
                if 'remarks' in cols:
                    fields['remarks'] = remarks
                if 'term' in cols:
                    fields['term'] = term
                if 'session' in cols:
                    fields['session'] = session

                if existing_id:
                    set_clause = ', '.join([f"{col} = %s" for col in fields.keys()])
                    cur.execute(f"UPDATE grades SET {set_clause} WHERE id = %s;", list(fields.values()) + [existing_id])
                    grade_id = existing_id
                else:
                    col_names = list(fields.keys())
                    placeholders = ', '.join(['%s'] * len(col_names))
                    if 'id' in cols:
                        cur.execute(f"""
                            INSERT INTO grades ({', '.join(col_names)})
                            VALUES ({placeholders})
                            RETURNING id;
                        """, list(fields.values()))
                        grade_id = cur.fetchone()[0]
                    else:
                        cur.execute(f"INSERT INTO grades ({', '.join(col_names)}) VALUES ({placeholders});", list(fields.values()))

            # Also sync into student_records for legacy compatibility
            if 'student_records' in all_tables:
                cur.execute("""
                    DELETE FROM student_records 
                    WHERE LOWER(roll_no) = LOWER(%s) AND LOWER(subject) = LOWER(%s);
                """, (roll_no, subject))
                cur.execute("""
                    INSERT INTO student_records (student_name, roll_no, student_class, subject, score, grade)
                    VALUES (%s, %s, %s, %s, %s, %s);
                """, (student_name, roll_no, student_class, subject, score_val, grade_letter))

        conn.commit()
        return jsonify({
            "message": "Grade saved successfully",
            "grade": {
                "id": grade_id,
                "student_id": student_id,
                "school_id": school_id,
                "roll_no": roll_no,
                "student_name": student_name,
                "subject": subject,
                "score": score_val,
                "grade": grade_letter,
                "remarks": remarks,
                "term": term,
                "session": session
            }
        }), 201

    except Exception as e:
        logger.error("Error adding grade: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while adding grade", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/grades/<string:roll_no>', methods=['GET'])
@require_auth
def get_grades(current_user, roll_no):
    roll_no = roll_no.strip()
    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            all_tables = get_all_tables(conn)
            st_class = None
            student_id = None

            if 'students' in all_tables:
                cur.execute("""
                    SELECT id, COALESCE(class, student_class) AS st_class 
                    FROM students 
                    WHERE LOWER(roll_no) = LOWER(%s)
                    LIMIT 1;
                """, (roll_no,))
                st_row = cur.fetchone()
                if st_row:
                    student_id = st_row['id']
                    st_class = st_row['st_class']

            if not st_class and 'student_records' in all_tables:
                cur.execute("""
                    SELECT id, student_class 
                    FROM student_records 
                    WHERE LOWER(roll_no) = LOWER(%s) AND student_name IS NOT NULL
                    LIMIT 1;
                """, (roll_no,))
                sr_row = cur.fetchone()
                if sr_row:
                    st_class = sr_row['student_class']

            if st_class and not is_admin and str(st_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            # Inspect columns of grades table
            cols = get_table_columns(conn, 'grades') if 'grades' in all_tables else set()
            rows = []

            if cols:
                select_parts = ['id'] if 'id' in cols else ['1 AS id']
                if 'subject' in cols and 'subject_name' in cols:
                    select_parts.append('COALESCE(subject, subject_name) AS subject')
                elif 'subject' in cols:
                    select_parts.append('subject')
                elif 'subject_name' in cols:
                    select_parts.append('subject_name AS subject')
                else:
                    select_parts.append("'General' AS subject")

                select_parts.append('score' if 'score' in cols else '0 AS score')
                select_parts.append('grade' if 'grade' in cols else "'' AS grade")
                select_parts.append('term' if 'term' in cols else "'' AS term")
                select_parts.append('session' if 'session' in cols else "'' AS session")
                if 'remarks' in cols:
                    select_parts.append('remarks')

                where_clauses = []
                params = []
                if 'student_id' in cols and student_id:
                    where_clauses.append("student_id = %s")
                    params.append(student_id)
                elif 'roll_no' in cols:
                    where_clauses.append("LOWER(roll_no) = LOWER(%s)")
                    params.append(roll_no)

                where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
                cur.execute(f"""
                    SELECT {', '.join(select_parts)}
                    FROM grades
                    {where_sql}
                    ORDER BY id ASC;
                """, tuple(params))
                rows = cur.fetchall()

            # Fallback to student_records if no rows found in grades table
            if not rows and 'student_records' in all_tables:
                cur.execute("""
                    SELECT id, subject, score, grade
                    FROM student_records
                    WHERE LOWER(roll_no) = LOWER(%s) AND subject IS NOT NULL
                    ORDER BY id ASC;
                """, (roll_no,))
                rows = cur.fetchall()

        grades = []
        for row in rows:
            score_val = float(row["score"]) if row.get("score") is not None else 0.0
            grade_letter = row.get("grade") or compute_grade(score_val)
            grades.append({
                "id": row.get("id", 1),
                "subject": row.get("subject", "General"),
                "score": score_val,
                "grade": grade_letter,
                "remarks": row.get("remarks") or compute_grade_remarks(score_val),
                "term": row.get("term"),
                "session": row.get("session")
            })

        return jsonify(grades), 200

    except Exception as e:
        logger.error("Error fetching grades: %s", e)
        # Never crash with 500 - return empty list gracefully
        return jsonify([]), 200
    finally:
        if conn:
            conn.close()


@app.route('/api/grades/<int:grade_id>', methods=['DELETE'])
@require_auth
def delete_grade(current_user, grade_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            if 'grades' in all_tables:
                cur.execute("DELETE FROM grades WHERE id = %s;", (grade_id,))
            if 'student_records' in all_tables:
                cur.execute("DELETE FROM student_records WHERE id = %s AND subject IS NOT NULL;", (grade_id,))

        conn.commit()
        return jsonify({"message": "Grade record deleted successfully"}), 200
    except Exception as e:
        logger.error("Error deleting grade: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while deleting grade", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# SERVER STARTUP
# ==========================================================
if __name__ == '__main__':
    port = int(os.getenv('FLASK_PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'True').lower() in ('true', '1', 't')

    logger.info("Starting EduTrack API server on port %s...", port)
    app.run(host='0.0.0.0', port=port, debug=debug)
