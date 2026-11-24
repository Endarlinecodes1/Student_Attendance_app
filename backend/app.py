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


def get_student_query_fields(conn):
    """
    Returns exact SQL column expressions for student_name, student_class, gender, parent_contact
    based ONLY on columns that physically exist in the students table.
    Guarantees no column-not-found errors in PostgreSQL.
    """
    cols = get_table_columns(conn, 'students')
    if 'student_name' in cols and 'full_name' in cols:
        name_expr = "COALESCE(student_name, full_name, 'Student')"
    elif 'student_name' in cols:
        name_expr = "COALESCE(student_name, 'Student')"
    elif 'full_name' in cols:
        name_expr = "COALESCE(full_name, 'Student')"
    else:
        name_expr = "'Student'"

    if 'student_class' in cols and 'class' in cols:
        class_expr = "COALESCE(student_class, class, 'Primary 1')"
    elif 'student_class' in cols:
        class_expr = "COALESCE(student_class, 'Primary 1')"
    elif 'class' in cols:
        class_expr = "COALESCE(class, 'Primary 1')"
    else:
        class_expr = "'Primary 1'"

    gender_expr = "COALESCE(gender, 'Unspecified')" if 'gender' in cols else "'Unspecified'"
    contact_expr = "COALESCE(parent_contact, '')" if 'parent_contact' in cols else "''"
    school_expr = "school_id" if 'school_id' in cols else "1 AS school_id"

    return {
        "cols": cols,
        "name_expr": name_expr,
        "class_expr": class_expr,
        "gender_expr": gender_expr,
        "contact_expr": contact_expr,
        "school_expr": school_expr
    }


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
    """Extracts and verifies Bearer token from Authorization header."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    token = auth_header.split(' ', 1)[1].strip()
    return decode_token(token)


def require_auth(f):
    """Decorator to enforce valid authentication on API endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({
                "error": "Authentication required. Please log in.",
                "code": "AUTH_REQUIRED"
            }), 401
        return f(current_user=user, *args, **kwargs)
    return decorated


def compute_grade(score):
    """Computes academic grade letter according to Nigerian standard grading."""
    try:
        s = float(score)
    except (ValueError, TypeError):
        return 'F'
    if s >= 70:
        return 'A'
    if s >= 60:
        return 'B'
    if s >= 50:
        return 'C'
    if s >= 40:
        return 'D'
    return 'F'


def compute_grade_remarks(score):
    """Computes academic remark based on score percentage."""
    try:
        s = float(score)
    except (ValueError, TypeError):
        return 'Fail'
    if s >= 70:
        return 'Distinction'
    if s >= 60:
        return 'Credit'
    if s >= 50:
        return 'Credit'
    if s >= 40:
        return 'Pass'
    return 'Fail'


# ==========================================================
# DATABASE INITIALIZATION & SCHEMA VERIFICATION
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
            """)
            conn.commit()

            for stmt in [
                "ALTER TABLE teachers ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;",
                "UPDATE teachers SET school_id = 1 WHERE school_id IS NULL;",
                "CREATE INDEX IF NOT EXISTS idx_teachers_email ON teachers (email);"
            ]:
                try:
                    cur.execute(stmt)
                    conn.commit()
                except Exception:
                    conn.rollback()

            # 2. Students Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    id SERIAL PRIMARY KEY,
                    school_id INT DEFAULT 1,
                    student_name VARCHAR(255),
                    full_name VARCHAR(255),
                    roll_no VARCHAR(100),
                    student_class VARCHAR(100),
                    class VARCHAR(100),
                    gender VARCHAR(20) DEFAULT 'Unspecified',
                    parent_contact VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

            for stmt in [
                "ALTER TABLE students ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;",
                "ALTER TABLE students ADD COLUMN IF NOT EXISTS student_name VARCHAR(255);",
                "ALTER TABLE students ADD COLUMN IF NOT EXISTS full_name VARCHAR(255);",
                "ALTER TABLE students ADD COLUMN IF NOT EXISTS roll_no VARCHAR(100);",
                "ALTER TABLE students ADD COLUMN IF NOT EXISTS student_class VARCHAR(100);",
                "ALTER TABLE students ADD COLUMN IF NOT EXISTS class VARCHAR(100);",
                "ALTER TABLE students ADD COLUMN IF NOT EXISTS gender VARCHAR(20) DEFAULT 'Unspecified';",
                "ALTER TABLE students ADD COLUMN IF NOT EXISTS parent_contact VARCHAR(100);",
                "ALTER TABLE students ALTER COLUMN full_name DROP NOT NULL;",
                "ALTER TABLE students ALTER COLUMN class DROP NOT NULL;",
                "UPDATE students SET student_name = COALESCE(student_name, full_name) WHERE student_name IS NULL;",
                "UPDATE students SET full_name = COALESCE(full_name, student_name) WHERE full_name IS NULL;",
                "UPDATE students SET student_class = COALESCE(student_class, class) WHERE student_class IS NULL;",
                "UPDATE students SET class = COALESCE(class, student_class) WHERE class IS NULL;",
                "CREATE INDEX IF NOT EXISTS idx_students_roll ON students (roll_no);"
            ]:
                try:
                    cur.execute(stmt)
                    conn.commit()
                except Exception:
                    conn.rollback()

            # 3. Attendance Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id SERIAL PRIMARY KEY,
                    student_id INT,
                    school_id INT DEFAULT 1,
                    roll_no VARCHAR(100),
                    student_name VARCHAR(255),
                    student_class VARCHAR(100),
                    att_date DATE,
                    date DATE,
                    status VARCHAR(20),
                    marked_by VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

            for stmt in [
                "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;",
                "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_id INT;",
                "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS roll_no VARCHAR(100);",
                "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_name VARCHAR(255);",
                "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS student_class VARCHAR(100);",
                "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS att_date DATE;",
                "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS date DATE;",
                "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS status VARCHAR(20);",
                "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS marked_by VARCHAR(100);",
                "ALTER TABLE attendance ALTER COLUMN student_id DROP NOT NULL;",
                "ALTER TABLE attendance ALTER COLUMN school_id DROP NOT NULL;",
                "ALTER TABLE attendance ALTER COLUMN marked_by DROP NOT NULL;",
                "ALTER TABLE attendance ALTER COLUMN status DROP NOT NULL;"
            ]:
                try:
                    cur.execute(stmt)
                    conn.commit()
                except Exception:
                    conn.rollback()

            # 4. Grades Table
            cur.execute("""
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
            """)
            conn.commit()

            for stmt in [
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS school_id INT DEFAULT 1;",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS student_id INT;",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS roll_no VARCHAR(100);",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS student_name VARCHAR(255);",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS student_class VARCHAR(100);",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS subject VARCHAR(255);",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS subject_name VARCHAR(255);",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS score NUMERIC(5, 2);",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS grade VARCHAR(20);",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS remarks VARCHAR(255);",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS term VARCHAR(100) DEFAULT 'First Term 2026/2027';",
                "ALTER TABLE grades ADD COLUMN IF NOT EXISTS session VARCHAR(100) DEFAULT '2026/2027';",
                "ALTER TABLE grades ALTER COLUMN student_id DROP NOT NULL;",
                "ALTER TABLE grades ALTER COLUMN school_id DROP NOT NULL;",
                "ALTER TABLE grades ALTER COLUMN grade DROP NOT NULL;",
                "ALTER TABLE grades ALTER COLUMN remarks DROP NOT NULL;",
                "ALTER TABLE grades ALTER COLUMN term DROP NOT NULL;",
                "ALTER TABLE grades ALTER COLUMN session DROP NOT NULL;",
                "ALTER TABLE grades ALTER COLUMN score DROP NOT NULL;",
                "ALTER TABLE grades ALTER COLUMN subject DROP NOT NULL;"
            ]:
                try:
                    cur.execute(stmt)
                    conn.commit()
                except Exception:
                    conn.rollback()

            # Sync legacy student_records if present
            try:
                cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'student_records';")
                if cur.fetchone():
                    cur.execute("""
                        INSERT INTO students (school_id, student_name, full_name, roll_no, student_class, class, gender, parent_contact)
                        SELECT 
                            1 AS school_id,
                            COALESCE(MAX(NULLIF(student_name, '')), 'Student') AS student_name,
                            COALESCE(MAX(NULLIF(student_name, '')), 'Student') AS full_name,
                            roll_no,
                            COALESCE(MAX(NULLIF(student_class, '')), 'Primary 1') AS student_class,
                            COALESCE(MAX(NULLIF(student_class, '')), 'Primary 1') AS class,
                            COALESCE(MAX(NULLIF(gender, '')), 'Unspecified') AS gender,
                            COALESCE(MAX(NULLIF(parent_contact, '')), '') AS parent_contact
                        FROM student_records
                        WHERE roll_no IS NOT NULL
                        GROUP BY roll_no
                        ON CONFLICT DO NOTHING;
                    """)
                    conn.commit()
            except Exception:
                conn.rollback()

            # Seed default teachers
            for email, name, role, assigned in [
                ('admin@edutrack.com', 'Administrator', 'admin', 'All'),
                ('teacher@edutrack.com', 'Sarah Jenkins', 'teacher', 'Primary 1'),
                ('teacher2@edutrack.com', 'Michael Obi', 'teacher', 'Primary 2')
            ]:
                try:
                    cur.execute("SELECT id FROM teachers WHERE LOWER(email) = LOWER(%s) LIMIT 1;", (email,))
                    if not cur.fetchone():
                        pw_hash = generate_password_hash('admin123' if role == 'admin' else 'teacher123')
                        cur.execute("""
                            INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                            VALUES (1, %s, %s, %s, %s, %s);
                        """, (name, email, pw_hash, role, assigned))
                        conn.commit()
                except Exception:
                    conn.rollback()

            # Sync primary key sequences so auto-increment never conflicts
            for tbl in ['students', 'attendance', 'grades', 'teachers']:
                try:
                    cur.execute(f"SELECT setval(pg_get_serial_sequence('{tbl}', 'id'), COALESCE(MAX(id), 1)) FROM {tbl};")
                    conn.commit()
                except Exception:
                    conn.rollback()

        logger.info("Database schema initialized and verified successfully.")
    except Exception as e:
        logger.warning("Notice during database initialization: %s", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


# Safe startup invocation
try:
    init_db()
except Exception as _startup_e:
    logger.warning("Startup database init notice: %s", _startup_e)


# ==========================================================
# PUBLIC & HEALTH ROUTES
# ==========================================================

@app.route('/', methods=['GET'])
def root():
    return jsonify({
        "status": "online",
        "service": "EduTrack Attendance API",
        "version": "2.1.0",
        "architecture": "Normalized Relational PostgreSQL Schema",
        "endpoints": {
            "health": "/api/health",
            "auth_login": "POST /api/auth/login",
            "auth_register": "POST /api/auth/register",
            "auth_me": "GET /api/auth/me",
            "dashboard": "GET /api/dashboard",
            "students": "/api/students",
            "attendance": "POST /api/attendance",
            "grades": "/api/grades",
            "report": "/api/report/<roll_no>"
        }
    }), 200


@app.route('/api/health', methods=['GET'])
def health_check():
    db_status = "disconnected"
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    finally:
        if conn:
            conn.close()

    return jsonify({
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z"
    }), 200 if db_status == "connected" else 500


# ==========================================================
# AUTHENTICATION ROUTES
# ==========================================================

@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    name = str(data.get('name', '')).strip()
    email = str(data.get('email', '')).strip().lower()
    password = str(data.get('password', '')).strip()
    assigned_class = str(data.get('assigned_class', 'Primary 1')).strip()
    role = str(data.get('role', 'teacher')).strip().lower()
    school_id = int(data.get('school_id', 1))

    if role not in ('teacher', 'admin'):
        role = 'teacher'

    if not name or not email or not password:
        return jsonify({"error": "Name, email, and password are required"}), 400

    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = LOWER(%s) LIMIT 1;", (email,))
            if cur.fetchone():
                return jsonify({"error": "An account with this email address already exists"}), 409

            password_hash = generate_password_hash(password)
            cur.execute("""
                INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, school_id, name, email, role, assigned_class, created_at;
            """, (school_id, name, email, password_hash, role, assigned_class))
            user = cur.fetchone()
            conn.commit()

            token = generate_token(
                user_id=user['id'],
                role=user['role'],
                assigned_class=user['assigned_class'],
                email=user['email'],
                name=user['name'],
                school_id=user.get('school_id', 1)
            )

            return jsonify({
                "message": "Account created successfully",
                "token": token,
                "user": {
                    "id": user['id'],
                    "school_id": user.get('school_id', 1),
                    "name": user['name'],
                    "email": user['email'],
                    "role": user['role'],
                    "assigned_class": user['assigned_class']
                }
            }), 201

    except Exception as e:
        logger.error("Error during user registration: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error during registration", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/auth/login', methods=['POST'])
def login():
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
            cols = get_table_columns(conn, 'teachers')
            school_col = "school_id" if "school_id" in cols else "1 AS school_id"
            cur.execute(f"""
                SELECT id, {school_col}, name, email, password_hash, role, assigned_class 
                FROM teachers 
                WHERE LOWER(email) = LOWER(%s) 
                LIMIT 1;
            """, (email,))
            user = cur.fetchone()

            if not user or not check_password_hash(user['password_hash'], password):
                return jsonify({"error": "Invalid email or password"}), 401

            token = generate_token(
                user_id=user['id'],
                role=user['role'],
                assigned_class=user['assigned_class'],
                email=user['email'],
                name=user['name'],
                school_id=user.get('school_id', 1)
            )

            return jsonify({
                "message": "Login successful",
                "token": token,
                "user": {
                    "id": user['id'],
                    "school_id": user.get('school_id', 1),
                    "name": user['name'],
                    "email": user['email'],
                    "role": user['role'],
                    "assigned_class": user['assigned_class']
                }
            }), 200

    except Exception as e:
        logger.error("Error during login: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error during login", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/auth/me', methods=['GET'])
@require_auth
def get_current_user_profile(current_user):
    return jsonify({
        "user": {
            "id": current_user.get('user_id'),
            "school_id": current_user.get('school_id', 1),
            "name": current_user.get('name'),
            "email": current_user.get('email'),
            "role": current_user.get('role'),
            "assigned_class": current_user.get('assigned_class')
        }
    }), 200


# ==========================================================
# DASHBOARD STATS
# ==========================================================

@app.route('/api/dashboard', methods=['GET'])
@require_auth
def get_dashboard_stats(current_user):
    target_date = request.args.get('date', datetime.date.today().isoformat())
    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            total_students = 0
            present_today = 0
            absent_today = 0

            # 1. Total Students
            if 'students' in all_tables:
                st_fields = get_student_query_fields(conn)
                if is_admin:
                    cur.execute("SELECT COUNT(*) FROM students;")
                else:
                    cur.execute(f"SELECT COUNT(*) FROM students WHERE LOWER({st_fields['class_expr']}) = LOWER(%s);", (assigned_class,))
                total_students = cur.fetchone()[0] or 0
            elif 'student_records' in all_tables:
                if is_admin:
                    cur.execute("SELECT COUNT(DISTINCT roll_no) FROM student_records WHERE roll_no IS NOT NULL;")
                else:
                    cur.execute("SELECT COUNT(DISTINCT roll_no) FROM student_records WHERE roll_no IS NOT NULL AND LOWER(student_class) = LOWER(%s);", (assigned_class,))
                total_students = cur.fetchone()[0] or 0

            # 2. Attendance Counts Today
            if 'attendance' in all_tables:
                att_cols = get_table_columns(conn, 'attendance')
                date_col = 'att_date' if 'att_date' in att_cols else ('date' if 'date' in att_cols else None)

                if date_col:
                    if is_admin:
                        cur.execute(f"""
                            SELECT 
                                COUNT(*) FILTER (WHERE status = 'Present'),
                                COUNT(*) FILTER (WHERE status = 'Absent')
                            FROM attendance
                            WHERE {date_col} = %s;
                        """, (target_date,))
                        p, a = cur.fetchone()
                        present_today = p or 0
                        absent_today = a or 0
                    else:
                        st_fields = get_student_query_fields(conn)
                        # Join attendance with students on student_id or roll_no
                        has_st_id = 'student_id' in att_cols
                        has_roll = 'roll_no' in att_cols
                        if has_st_id:
                            cur.execute(f"""
                                SELECT 
                                    COUNT(*) FILTER (WHERE a.status = 'Present'),
                                    COUNT(*) FILTER (WHERE a.status = 'Absent')
                                FROM attendance a
                                JOIN students s ON a.student_id = s.id
                                WHERE a.{date_col} = %s AND LOWER({st_fields['class_expr']}) = LOWER(%s);
                            """, (target_date, assigned_class))
                            p, a = cur.fetchone()
                            present_today = p or 0
                            absent_today = a or 0
                        elif has_roll:
                            cur.execute(f"""
                                SELECT 
                                    COUNT(*) FILTER (WHERE a.status = 'Present'),
                                    COUNT(*) FILTER (WHERE a.status = 'Absent')
                                FROM attendance a
                                JOIN students s ON LOWER(a.roll_no) = LOWER(s.roll_no)
                                WHERE a.{date_col} = %s AND LOWER({st_fields['class_expr']}) = LOWER(%s);
                            """, (target_date, assigned_class))
                            p, a = cur.fetchone()
                            present_today = p or 0
                            absent_today = a or 0

            # Fallback to student_records if 0 counts
            if present_today == 0 and absent_today == 0 and 'student_records' in all_tables:
                if is_admin:
                    cur.execute("""
                        SELECT 
                            COUNT(*) FILTER (WHERE status = 'Present'),
                            COUNT(*) FILTER (WHERE status = 'Absent')
                        FROM student_records
                        WHERE att_date = %s;
                    """, (target_date,))
                else:
                    cur.execute("""
                        SELECT 
                            COUNT(*) FILTER (WHERE status = 'Present'),
                            COUNT(*) FILTER (WHERE status = 'Absent')
                        FROM student_records
                        WHERE att_date = %s AND LOWER(student_class) = LOWER(%s);
                    """, (target_date, assigned_class))
                sr_p, sr_a = cur.fetchone()
                present_today = sr_p or 0
                absent_today = sr_a or 0

            marked_today = present_today + absent_today
            attendance_percentage = round((present_today / marked_today) * 100, 1) if marked_today > 0 else 0.0

            return jsonify({
                "date": target_date,
                "role": current_user.get('role'),
                "assigned_class": assigned_class if not is_admin else "All Classes",
                "total_students": total_students,
                "present_today": present_today,
                "absent_today": absent_today,
                "marked_today": marked_today,
                "attendance_percentage": attendance_percentage
            }), 200

    except Exception as e:
        logger.error("Error fetching dashboard stats: %s", e)
        if conn:
            conn.rollback()
        return jsonify({
            "date": target_date,
            "total_students": 0,
            "present_today": 0,
            "absent_today": 0,
            "marked_today": 0,
            "attendance_percentage": 0.0
        }), 200
    finally:
        if conn:
            conn.close()


# ==========================================================
# STUDENTS CRUD API
# Canonical 5 Fields: student_name, roll_no, student_class, gender, parent_contact
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
            raw_students = []

            if 'students' in all_tables:
                f = get_student_query_fields(conn)
                if is_admin:
                    cur.execute(f"""
                        SELECT 
                            id,
                            {f['school_expr']},
                            {f['name_expr']} AS student_name,
                            roll_no,
                            {f['class_expr']} AS student_class,
                            {f['gender_expr']} AS gender,
                            {f['contact_expr']} AS parent_contact
                        FROM students
                        ORDER BY roll_no ASC;
                    """)
                else:
                    cur.execute(f"""
                        SELECT 
                            id,
                            {f['school_expr']},
                            {f['name_expr']} AS student_name,
                            roll_no,
                            {f['class_expr']} AS student_class,
                            {f['gender_expr']} AS gender,
                            {f['contact_expr']} AS parent_contact
                        FROM students
                        WHERE LOWER({f['class_expr']}) = LOWER(%s)
                        ORDER BY roll_no ASC;
                    """, (assigned_class,))
                raw_students = cur.fetchall()

            if not raw_students and 'student_records' in all_tables:
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
                raw_students = cur.fetchall()

            # Return canonical 5 fields (+ id, school_id)
            students = []
            for s in raw_students:
                s_name = s.get('student_name') or s.get('full_name') or 'Student'
                s_roll = s.get('roll_no') or ''
                s_class = s.get('student_class') or s.get('class') or 'Primary 1'
                s_gender = s.get('gender') or 'Unspecified'
                s_contact = s.get('parent_contact') or ''
                students.append({
                    "id": s.get('id', 1),
                    "school_id": s.get('school_id', 1),
                    "student_name": s_name,
                    "roll_no": s_roll,
                    "student_class": s_class,
                    "gender": s_gender,
                    "parent_contact": s_contact
                })

            return jsonify(students), 200

    except Exception as e:
        logger.error("Error fetching students: %s", e)
        if conn:
            conn.rollback()
        return jsonify([]), 200
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

    student_name = str(data.get('student_name') or data.get('full_name') or data.get('name', '')).strip()
    roll_no = str(data.get('roll_no') or data.get('roll_number', '')).strip()
    gender = str(data.get('gender', 'Unspecified')).strip()
    parent_contact = str(data.get('parent_contact', '')).strip()

    if is_admin:
        student_class = str(data.get('student_class') or data.get('class_name') or data.get('class', 'Primary 1')).strip()
    else:
        student_class = assigned_class

    if not student_name or not roll_no or not student_class:
        return jsonify({"error": "student_name, roll_no, and student_class are required"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            inserted_id = 1

            # Check duplicate roll_no
            if 'students' in all_tables:
                cur.execute("SELECT id FROM students WHERE LOWER(roll_no) = LOWER(%s) LIMIT 1;", (roll_no,))
                if cur.fetchone():
                    return jsonify({"error": f"Roll number '{roll_no}' is already registered"}), 409

                cols = get_table_columns(conn, 'students')
                fields = {}
                if 'school_id' in cols:
                    fields['school_id'] = school_id
                if 'student_name' in cols:
                    fields['student_name'] = student_name
                if 'full_name' in cols:
                    fields['full_name'] = student_name
                if 'roll_no' in cols:
                    fields['roll_no'] = roll_no
                if 'student_class' in cols:
                    fields['student_class'] = student_class
                if 'class' in cols:
                    fields['class'] = student_class
                if 'gender' in cols:
                    fields['gender'] = gender
                if 'parent_contact' in cols:
                    fields['parent_contact'] = parent_contact

                col_names = list(fields.keys())
                placeholders = ', '.join(['%s'] * len(col_names))

                cur.execute(f"""
                    INSERT INTO students ({', '.join(col_names)})
                    VALUES ({placeholders})
                    RETURNING id;
                """, list(fields.values()))
                res = cur.fetchone()
                if res:
                    inserted_id = res[0]
                conn.commit()

            # Separate non-fatal sync to student_records
            if 'student_records' in all_tables:
                try:
                    cur.execute("""
                        INSERT INTO student_records (student_name, roll_no, student_class, gender, parent_contact)
                        VALUES (%s, %s, %s, %s, %s);
                    """, (student_name, roll_no, student_class, gender, parent_contact))
                    conn.commit()
                except Exception:
                    conn.rollback()

        return jsonify({
            "message": f"Student '{student_name}' enrolled successfully",
            "student": {
                "id": inserted_id,
                "school_id": school_id,
                "student_name": student_name,
                "roll_no": roll_no,
                "student_class": student_class,
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
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    new_name = str(data.get('student_name') or data.get('full_name') or data.get('name', '')).strip()
    new_roll = str(data.get('roll_no') or data.get('roll_number', '')).strip()
    new_gender = str(data.get('gender', 'Unspecified')).strip()
    new_contact = str(data.get('parent_contact') or data.get('contact', '')).strip()

    if is_admin:
        new_class = str(data.get('student_class') or data.get('class_name') or data.get('class', 'Primary 1')).strip()
    else:
        new_class = assigned_class

    if not new_name or not new_roll:
        return jsonify({"error": "student_name and roll_no cannot be empty"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            actual_id = 1
            old_roll_no = new_roll

            if 'students' in all_tables:
                f = get_student_query_fields(conn)
                cur.execute(f"""
                    SELECT id, roll_no, {f['class_expr']}
                    FROM students
                    WHERE id::text = %s OR LOWER(roll_no) = LOWER(%s)
                    LIMIT 1;
                """, (student_id, student_id))
                row = cur.fetchone()
                if row:
                    actual_id, old_roll_no, old_class = row
                    if not is_admin and str(old_class).lower() != str(assigned_class).lower():
                        return jsonify({"error": "Access denied: You can only edit students in your assigned class"}), 403

                cols = get_table_columns(conn, 'students')
                updates = []
                vals = []
                if 'student_name' in cols:
                    updates.append("student_name = %s")
                    vals.append(new_name)
                if 'full_name' in cols:
                    updates.append("full_name = %s")
                    vals.append(new_name)
                if 'roll_no' in cols:
                    updates.append("roll_no = %s")
                    vals.append(new_roll)
                if 'student_class' in cols:
                    updates.append("student_class = %s")
                    vals.append(new_class)
                if 'class' in cols:
                    updates.append("class = %s")
                    vals.append(new_class)
                if 'gender' in cols:
                    updates.append("gender = %s")
                    vals.append(new_gender)
                if 'parent_contact' in cols:
                    updates.append("parent_contact = %s")
                    vals.append(new_contact)

                if updates:
                    cur.execute(f"UPDATE students SET {', '.join(updates)} WHERE id::text = %s OR LOWER(roll_no) = LOWER(%s);", vals + [student_id, student_id])
                conn.commit()

            # Sync legacy student_records
            if 'student_records' in all_tables:
                try:
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
                except Exception:
                    conn.rollback()

        return jsonify({
            "message": f"Student '{new_name}' updated successfully",
            "student": {
                "id": actual_id,
                "school_id": school_id,
                "student_name": new_name,
                "roll_no": new_roll,
                "student_class": new_class,
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
    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            all_tables = get_all_tables(conn)
            resolved_roll = None
            resolved_id = None
            student_class = None

            if 'students' in all_tables:
                f = get_student_query_fields(conn)
                cur.execute(f"""
                    SELECT id, roll_no, {f['class_expr']}
                    FROM students
                    WHERE id::text = %s OR LOWER(roll_no) = LOWER(%s)
                    LIMIT 1;
                """, (student_id, student_id))
                row = cur.fetchone()
                if row:
                    resolved_id, resolved_roll, student_class = row

            if not is_admin and student_class and str(student_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: You can only remove students in your assigned class"}), 403

            target_roll = resolved_roll or student_id

            if 'attendance' in all_tables:
                if resolved_id:
                    cur.execute("DELETE FROM attendance WHERE student_id = %s;", (resolved_id,))
                cur.execute("DELETE FROM attendance WHERE LOWER(roll_no) = LOWER(%s);", (target_roll,))

            if 'grades' in all_tables:
                if resolved_id:
                    cur.execute("DELETE FROM grades WHERE student_id = %s;", (resolved_id,))
                cur.execute("DELETE FROM grades WHERE LOWER(roll_no) = LOWER(%s);", (target_roll,))

            if 'students' in all_tables:
                cur.execute("DELETE FROM students WHERE id::text = %s OR LOWER(roll_no) = LOWER(%s);", (student_id, target_roll))

            if 'student_records' in all_tables:
                cur.execute("DELETE FROM student_records WHERE LOWER(roll_no) = LOWER(%s) OR id::text = %s;", (target_roll, student_id))

            conn.commit()

        return jsonify({"message": f"Student '{target_roll}' and associated attendance and grade records deleted."}), 200

    except Exception as e:
        logger.error("Error deleting student: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while deleting student", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# ATTENDANCE API
# ==========================================================

@app.route('/api/attendance', methods=['POST'])
@require_auth
def save_attendance(current_user):
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

                student_id = None
                student_name = record.get('student_name') or record.get('name')
                student_class = record.get('student_class') or record.get('class')

                if 'students' in all_tables:
                    f = get_student_query_fields(conn)
                    cur.execute(f"""
                        SELECT id, {f['name_expr']}, {f['class_expr']}
                        FROM students 
                        WHERE LOWER(roll_no) = LOWER(%s) OR id::text = %s
                        LIMIT 1;
                    """, (roll_no, str(record.get('id', ''))))
                    st_row = cur.fetchone()
                    if st_row:
                        student_id = st_row[0]
                        student_name = student_name or st_row[1] or "Student"
                        student_class = student_class or st_row[2] or "Primary 1"

                student_name = student_name or "Student"
                student_class = student_class or "Primary 1"

                # Check teacher class boundary
                if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                    continue

                # Auto-create student in students table if not found
                if not student_id and 'students' in all_tables:
                    try:
                        st_cols = get_table_columns(conn, 'students')
                        f_ins = {}
                        if 'school_id' in st_cols: f_ins['school_id'] = school_id
                        if 'student_name' in st_cols: f_ins['student_name'] = student_name
                        if 'full_name' in st_cols: f_ins['full_name'] = student_name
                        if 'roll_no' in st_cols: f_ins['roll_no'] = roll_no
                        if 'student_class' in st_cols: f_ins['student_class'] = student_class
                        if 'class' in st_cols: f_ins['class'] = student_class
                        if 'gender' in st_cols: f_ins['gender'] = 'Unspecified'
                        if 'parent_contact' in st_cols: f_ins['parent_contact'] = ''

                        cur.execute(f"""
                            INSERT INTO students ({', '.join(f_ins.keys())})
                            VALUES ({', '.join(['%s'] * len(f_ins))})
                            RETURNING id;
                        """, list(f_ins.values()))
                        r = cur.fetchone()
                        if r: student_id = r[0]
                        conn.commit()
                    except Exception:
                        conn.rollback()

                # Insert into attendance table
                if cols and date_col:
                    try:
                        if 'student_id' in cols and student_id:
                            cur.execute(f"DELETE FROM attendance WHERE student_id = %s AND {date_col} = %s;", (student_id, att_date))
                        elif 'roll_no' in cols:
                            cur.execute(f"DELETE FROM attendance WHERE LOWER(roll_no) = LOWER(%s) AND {date_col} = %s;", (roll_no, att_date))

                        att_fields = {}
                        if 'student_id' in cols and student_id:
                            att_fields['student_id'] = student_id
                        if 'school_id' in cols:
                            att_fields['school_id'] = school_id
                        if 'roll_no' in cols:
                            att_fields['roll_no'] = roll_no
                        if 'student_name' in cols:
                            att_fields['student_name'] = student_name
                        if 'student_class' in cols:
                            att_fields['student_class'] = student_class
                        if 'att_date' in cols:
                            att_fields['att_date'] = att_date
                        if 'date' in cols:
                            att_fields['date'] = att_date
                        if 'status' in cols:
                            att_fields['status'] = status
                        if 'marked_by' in cols:
                            att_fields['marked_by'] = marked_by

                        cur.execute(f"""
                            INSERT INTO attendance ({', '.join(att_fields.keys())})
                            VALUES ({', '.join(['%s'] * len(att_fields))});
                        """, list(att_fields.values()))
                        conn.commit()
                        saved_count += 1
                    except Exception as _att_err:
                        conn.rollback()
                        logger.warning("Attendance row insert note: %s", _att_err)

                # Legacy student_records sync
                if 'student_records' in all_tables:
                    try:
                        cur.execute("DELETE FROM student_records WHERE LOWER(roll_no) = LOWER(%s) AND att_date = %s;", (roll_no, att_date))
                        cur.execute("""
                            INSERT INTO student_records (student_name, roll_no, student_class, att_date, status)
                            VALUES (%s, %s, %s, %s, %s);
                        """, (student_name, roll_no, student_class, att_date, status))
                        conn.commit()
                    except Exception:
                        conn.rollback()

            conn.commit()
            return jsonify({
                "message": f"Successfully recorded attendance for {len(records)} student(s).",
                "saved_count": saved_count
            }), 200

    except Exception as e:
        logger.error("Error saving attendance: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while saving attendance", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# REPORT CARD API
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

            student_id = None
            student_name = None
            st_roll = roll_no
            st_class = "Primary 1"
            gender = "Unspecified"
            parent_contact = ""

            if 'students' in all_tables:
                f = get_student_query_fields(conn)
                cur.execute(f"""
                    SELECT id, {f['name_expr']}, roll_no, {f['class_expr']}, {f['gender_expr']}, {f['contact_expr']}
                    FROM students
                    WHERE LOWER(roll_no) = LOWER(%s)
                    LIMIT 1;
                """, (roll_no,))
                row = cur.fetchone()
                if row:
                    student_id, student_name, st_roll, st_class, gender, parent_contact = row

            if not student_name and 'student_records' in all_tables:
                cur.execute("""
                    SELECT id, student_name, roll_no, student_class, gender, parent_contact
                    FROM student_records
                    WHERE LOWER(roll_no) = LOWER(%s) AND student_name IS NOT NULL
                    LIMIT 1;
                """, (roll_no,))
                sr_row = cur.fetchone()
                if sr_row:
                    student_id, student_name, st_roll, st_class, gender, parent_contact = sr_row

            if not student_name:
                return jsonify({"error": f"No student found with roll number '{roll_no}'"}), 404

            st_class = st_class or "Primary 1"
            if not is_admin and str(st_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            # Attendance calculations
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

            # Fallback to student_records
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
                "student_name": student_name or "Student",
                "name": student_name or "Student",
                "full_name": student_name or "Student",
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
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while generating report", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# GRADES API
# ==========================================================

@app.route('/api/grades', methods=['POST'])
@require_auth
def add_grade(current_user):
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

            # Student lookup
            if 'students' in all_tables:
                f = get_student_query_fields(conn)
                cur.execute(f"""
                    SELECT id, {f['name_expr']}, {f['class_expr']}
                    FROM students 
                    WHERE LOWER(roll_no) = LOWER(%s)
                    LIMIT 1;
                """, (roll_no,))
                st_row = cur.fetchone()
                if st_row:
                    student_id = st_row[0]
                    student_name = student_name or st_row[1]
                    student_class = st_row[2]

            student_name = student_name or "Student"
            student_class = student_class or "Primary 1"

            if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            # Ensure student is present in students table
            if not student_id and 'students' in all_tables:
                try:
                    st_cols = get_table_columns(conn, 'students')
                    f_ins = {}
                    if 'school_id' in st_cols: f_ins['school_id'] = school_id
                    if 'student_name' in st_cols: f_ins['student_name'] = student_name
                    if 'full_name' in st_cols: f_ins['full_name'] = student_name
                    if 'roll_no' in st_cols: f_ins['roll_no'] = roll_no
                    if 'student_class' in st_cols: f_ins['student_class'] = student_class
                    if 'class' in st_cols: f_ins['class'] = student_class
                    if 'gender' in st_cols: f_ins['gender'] = 'Unspecified'
                    if 'parent_contact' in st_cols: f_ins['parent_contact'] = ''

                    cur.execute(f"""
                        INSERT INTO students ({', '.join(f_ins.keys())})
                        VALUES ({', '.join(['%s'] * len(f_ins))})
                        RETURNING id;
                    """, list(f_ins.values()))
                    ret = cur.fetchone()
                    if ret: student_id = ret[0]
                    conn.commit()
                except Exception:
                    conn.rollback()
                    cur.execute("SELECT id FROM students WHERE LOWER(roll_no) = LOWER(%s) LIMIT 1;", (roll_no,))
                    st_f = cur.fetchone()
                    if st_f: student_id = st_f[0]

            # Dynamic insertion into grades table
            cols = get_table_columns(conn, 'grades') if 'grades' in all_tables else set()
            grade_id = 1

            if cols:
                sub_col = 'subject' if 'subject' in cols else ('subject_name' if 'subject_name' in cols else None)

                existing_id = None
                if sub_col:
                    if 'student_id' in cols and student_id:
                        cur.execute(f"SELECT id FROM grades WHERE student_id = %s AND LOWER({sub_col}) = LOWER(%s) LIMIT 1;", (student_id, subject))
                    elif 'roll_no' in cols:
                        cur.execute(f"SELECT id FROM grades WHERE LOWER(roll_no) = LOWER(%s) AND LOWER({sub_col}) = LOWER(%s) LIMIT 1;", (roll_no, subject))
                    found = cur.fetchone()
                    if found:
                        existing_id = found[0]

                fields = {}
                if 'student_id' in cols and student_id:
                    fields['student_id'] = student_id
                if 'school_id' in cols:
                    fields['school_id'] = school_id
                if 'roll_no' in cols:
                    fields['roll_no'] = roll_no
                if 'student_name' in cols:
                    fields['student_name'] = student_name
                if 'student_class' in cols:
                    fields['student_class'] = student_class
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
                        try:
                            cur.execute(f"""
                                INSERT INTO grades ({', '.join(col_names)})
                                VALUES ({placeholders})
                                RETURNING id;
                            """, list(fields.values()))
                            row_ret = cur.fetchone()
                            grade_id = row_ret[0] if row_ret else 1
                        except Exception:
                            conn.rollback()
                            cur.execute(f"INSERT INTO grades ({', '.join(col_names)}) VALUES ({placeholders});", list(fields.values()))
                            cur.execute("SELECT MAX(id) FROM grades;")
                            max_row = cur.fetchone()
                            grade_id = max_row[0] if max_row else 1
                    else:
                        cur.execute(f"INSERT INTO grades ({', '.join(col_names)}) VALUES ({placeholders});", list(fields.values()))

                conn.commit()

            # Separate non-fatal sync into student_records
            if 'student_records' in all_tables:
                try:
                    cur.execute("""
                        DELETE FROM student_records 
                        WHERE LOWER(roll_no) = LOWER(%s) AND LOWER(subject) = LOWER(%s);
                    """, (roll_no, subject))
                    cur.execute("""
                        INSERT INTO student_records (student_name, roll_no, student_class, subject, score, grade)
                        VALUES (%s, %s, %s, %s, %s, %s);
                    """, (student_name, roll_no, student_class, subject, score_val, grade_letter))
                    conn.commit()
                except Exception:
                    conn.rollback()

        return jsonify({
            "message": "Grade saved successfully",
            "grade": {
                "id": grade_id,
                "student_id": student_id,
                "school_id": school_id,
                "roll_no": roll_no,
                "student_name": student_name,
                "student_class": student_class,
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
        return jsonify({"error": f"Database error while adding grade: {str(e)}", "details": str(e)}), 500
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
                f = get_student_query_fields(conn)
                cur.execute(f"""
                    SELECT id, {f['class_expr']} AS st_class 
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

            # Fallback to student_records
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
        if conn:
            conn.rollback()
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
                cur.execute("DELETE FROM student_records WHERE id = %s;", (grade_id,))
            conn.commit()

        return jsonify({"message": f"Grade entry {grade_id} deleted successfully."}), 200

    except Exception as e:
        logger.error("Error deleting grade: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while deleting grade", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()



# ==========================================================
# ADMIN & TEACHER MANAGEMENT API (REQUIREMENT STEP 2)
# ==========================================================

@app.route('/api/teachers', methods=['GET'])
@require_auth
def get_teachers(current_user):
    """Admin-only: Retrieve all registered teachers with their assigned class."""
    if current_user.get('role') != 'admin':
        return jsonify({"error": "Admin access required"}), 403

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            all_tables = get_all_tables(conn)
            if 'teachers' not in all_tables:
                return jsonify([]), 200

            cols = get_table_columns(conn, 'teachers')
            select_cols = ['id', 'name', 'email', 'role', 'assigned_class']
            if 'created_at' in cols:
                select_cols.append('created_at')
            if 'school_id' in cols:
                select_cols.append('school_id')

            cur.execute(f"""
                SELECT {', '.join(select_cols)}
                FROM teachers
                ORDER BY role DESC, assigned_class ASC, name ASC;
            """)
            teachers = cur.fetchall()

            # Format datetime
            res = []
            for t in teachers:
                res.append({
                    "id": t['id'],
                    "name": t['name'],
                    "email": t['email'],
                    "role": t.get('role', 'teacher'),
                    "assigned_class": t.get('assigned_class', 'Primary 1'),
                    "school_id": t.get('school_id', 1),
                    "created_at": t['created_at'].isoformat() if t.get('created_at') and hasattr(t['created_at'], 'isoformat') else None
                })

            return jsonify(res), 200

    except Exception as e:
        logger.error("Error fetching teachers: %s", e)
        if conn:
            conn.rollback()
        return jsonify([]), 200
    finally:
        if conn:
            conn.close()


@app.route('/api/teachers', methods=['POST'])
@require_auth
def add_teacher(current_user):
    """Admin-only: Add a new teacher and assign them to a class."""
    if current_user.get('role') != 'admin':
        return jsonify({"error": "Admin access required"}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    name = str(data.get('name', '')).strip()
    email = str(data.get('email', '')).strip().lower()
    password = str(data.get('password', '')).strip()
    assigned_class = str(data.get('assigned_class', 'Primary 1')).strip()
    role = str(data.get('role', 'teacher')).strip().lower()
    school_id = current_user.get('school_id', 1)

    if not name or not email or not password or not assigned_class:
        return jsonify({"error": "Name, email, password, and assigned_class are required"}), 400

    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = LOWER(%s) LIMIT 1;", (email,))
            if cur.fetchone():
                return jsonify({"error": "A teacher with this email address already exists"}), 409

            password_hash = generate_password_hash(password)
            cur.execute("""
                INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, school_id, name, email, role, assigned_class, created_at;
            """, (school_id, name, email, password_hash, role, assigned_class))
            new_teacher = cur.fetchone()
            conn.commit()

            return jsonify({
                "message": f"Teacher '{name}' added successfully and assigned to {assigned_class}.",
                "teacher": {
                    "id": new_teacher['id'],
                    "name": new_teacher['name'],
                    "email": new_teacher['email'],
                    "role": new_teacher['role'],
                    "assigned_class": new_teacher['assigned_class']
                }
            }), 201

    except Exception as e:
        logger.error("Error adding teacher: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while creating teacher", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/teachers/<int:teacher_id>', methods=['PUT'])
@require_auth
def update_teacher(current_user, teacher_id):
    """Admin-only: Reassign teacher to a different class or update details."""
    if current_user.get('role') != 'admin':
        return jsonify({"error": "Admin access required"}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name, email, role, assigned_class FROM teachers WHERE id = %s LIMIT 1;", (teacher_id,))
            teacher = cur.fetchone()
            if not teacher:
                return jsonify({"error": "Teacher not found"}), 404

            updates = []
            vals = []

            if 'name' in data and str(data['name']).strip():
                updates.append("name = %s")
                vals.append(str(data['name']).strip())

            if 'assigned_class' in data and str(data['assigned_class']).strip():
                updates.append("assigned_class = %s")
                vals.append(str(data['assigned_class']).strip())

            if 'role' in data and str(data['role']).strip() in ('teacher', 'admin'):
                updates.append("role = %s")
                vals.append(str(data['role']).strip())

            if 'password' in data and len(str(data['password']).strip()) >= 6:
                updates.append("password_hash = %s")
                vals.append(generate_password_hash(str(data['password']).strip()))

            if updates:
                vals.append(teacher_id)
                cur.execute(f"UPDATE teachers SET {', '.join(updates)} WHERE id = %s;", vals)
                conn.commit()

            cur.execute("SELECT id, name, email, role, assigned_class FROM teachers WHERE id = %s;", (teacher_id,))
            updated = cur.fetchone()

            return jsonify({
                "message": f"Teacher '{updated['name']}' updated successfully.",
                "teacher": updated
            }), 200

    except Exception as e:
        logger.error("Error updating teacher: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while updating teacher", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/teachers/<int:teacher_id>', methods=['DELETE'])
@require_auth
def delete_teacher(current_user, teacher_id):
    """Admin-only: Remove a teacher from the system."""
    if current_user.get('role') != 'admin':
        return jsonify({"error": "Admin access required"}), 403

    if current_user.get('user_id') == teacher_id:
        return jsonify({"error": "You cannot delete your own admin account"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM teachers WHERE id = %s;", (teacher_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "Teacher not found"}), 404

            teacher_name = row[0]
            cur.execute("DELETE FROM teachers WHERE id = %s;", (teacher_id,))
            conn.commit()

            return jsonify({
                "message": f"Teacher '{teacher_name}' has been successfully removed."
            }), 200

    except Exception as e:
        logger.error("Error deleting teacher: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while removing teacher", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/admin/overview', methods=['GET'])
@require_auth
def get_admin_overview(current_user):
    """Admin-only: Aggregated overview of all classes, assigned teachers, student counts, and attendance."""
    if current_user.get('role') != 'admin':
        return jsonify({"error": "Admin access required"}), 403

    target_date = request.args.get('date', datetime.date.today().isoformat())
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            all_tables = get_all_tables(conn)

            # 1. Teachers map by class
            teachers_by_class = {}
            total_teachers = 0
            if 'teachers' in all_tables:
                cur.execute("SELECT id, name, email, role, assigned_class FROM teachers WHERE role = 'teacher';")
                for t in cur.fetchall():
                    cls = (t.get('assigned_class') or '').strip()
                    if cls:
                        teachers_by_class[cls.lower()] = t
                cur.execute("SELECT COUNT(*) AS total FROM teachers WHERE role = 'teacher';")
                row = cur.fetchone()
                total_teachers = row['total'] if row else 0

            # 2. Students breakdown by class
            total_students = 0
            students_per_class = {}
            if 'students' in all_tables:
                f = get_student_query_fields(conn)
                cur.execute(f"""
                    SELECT {f['class_expr']} AS student_class, COUNT(*) AS count
                    FROM students
                    GROUP BY {f['class_expr']}
                    ORDER BY student_class ASC;
                """)
                for r in cur.fetchall():
                    c_name = (r['student_class'] or 'Primary 1').strip()
                    c_count = r['count'] or 0
                    students_per_class[c_name.lower()] = c_count
                    total_students += c_count

            # 3. Attendance today per class
            att_per_class = {}
            if 'attendance' in all_tables:
                att_cols = get_table_columns(conn, 'attendance')
                date_col = 'att_date' if 'att_date' in att_cols else ('date' if 'date' in att_cols else None)
                if date_col:
                    f = get_student_query_fields(conn)
                    if 'student_id' in att_cols:
                        st_cols = get_table_columns(conn, 'students')
                        c_col = "s.student_class" if "student_class" in st_cols else ("s.class" if "class" in st_cols else "'Primary 1'")
                        cur.execute(f"""
                            SELECT {c_col} AS student_class,
                                   COUNT(*) FILTER (WHERE a.status = 'Present') AS present,
                                   COUNT(*) FILTER (WHERE a.status = 'Absent') AS absent
                            FROM attendance a
                            JOIN students s ON a.student_id = s.id
                            WHERE a.{date_col} = %s
                            GROUP BY {c_col};
                        """, (target_date,))
                        for row in cur.fetchall():
                            cls_k = (row['student_class'] or '').strip().lower()
                            att_per_class[cls_k] = {
                                "present": row['present'] or 0,
                                "absent": row['absent'] or 0
                            }

            # 4. Standard Classes Array
            standard_classes = [
                'Primary 1', 'Primary 2', 'Primary 3', 'Primary 4', 'Primary 5', 'Primary 6',
                'JSS 1', 'JSS 2', 'JSS 3', 'SSS 1', 'SSS 2', 'SSS 3'
            ]

            # Collect any custom classes found in DB
            all_class_keys = set(standard_classes)
            for c_low in students_per_class.keys():
                found_match = False
                for std in standard_classes:
                    if std.lower() == c_low:
                        found_match = True
                        break
                if not found_match and c_low:
                    all_class_keys.add(c_low.title())

            overview_list = []
            for c_name in standard_classes:
                c_low = c_name.lower()
                assigned_t = teachers_by_class.get(c_low)
                st_count = students_per_class.get(c_low, 0)
                att_info = att_per_class.get(c_low, {"present": 0, "absent": 0})
                marked = att_info['present'] + att_info['absent']
                rate = round((att_info['present'] / marked) * 100, 1) if marked > 0 else 0.0

                overview_list.append({
                    "class_name": c_name,
                    "teacher_name": assigned_t['name'] if assigned_t else "Unassigned",
                    "teacher_email": assigned_t['email'] if assigned_t else None,
                    "teacher_id": assigned_t['id'] if assigned_t else None,
                    "student_count": st_count,
                    "present_today": att_info['present'],
                    "absent_today": att_info['absent'],
                    "attendance_rate": rate
                })

            return jsonify({
                "date": target_date,
                "total_teachers": total_teachers,
                "total_students": total_students,
                "classes": overview_list
            }), 200

    except Exception as e:
        logger.error("Error fetching admin overview: %s", e)
        if conn:
            conn.rollback()
        return jsonify({
            "date": target_date,
            "total_teachers": 0,
            "total_students": 0,
            "classes": []
        }), 200
    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    port = int(os.getenv('FLASK_PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() in ('true', '1')
    logger.info("EduTrack Flask Server starting on port %d...", port)
    app.run(host='0.0.0.0', port=port, debug=debug)
