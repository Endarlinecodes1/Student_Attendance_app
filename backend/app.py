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


# ==========================================================
# DATABASE INITIALIZATION & RELATIONAL MIGRATION
# ==========================================================

def init_db():
    """
    Initializes database schema with a 3-table normalized design:
      1. students: id, school_id, full_name, roll_no, class, gender, parent_contact, created_at. (1 row per student)
      2. attendance: id, student_id, school_id, att_date, status, marked_by, created_at. UNIQUE(student_id, att_date).
      3. grades: id, student_id, school_id, subject, score, grade, term, session, created_at.
      4. teachers: id, school_id, name, email, password_hash, assigned_class, role, created_at.

    Non-destructively migrates legacy records from `student_records` without data loss.
    """
    create_schema_query = """
    -- 4. Teachers Table for Authentication & RBAC
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
    UPDATE teachers SET school_id = 1 WHERE school_id IS NULL;
    CREATE INDEX IF NOT EXISTS idx_teachers_email ON teachers (email);
    CREATE INDEX IF NOT EXISTS idx_teachers_school_id ON teachers (school_id);

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
    CREATE INDEX IF NOT EXISTS idx_students_school_roll ON students (school_id, roll_no);
    CREATE INDEX IF NOT EXISTS idx_students_school_class ON students (school_id, class);

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
    CREATE INDEX IF NOT EXISTS idx_attendance_student_date ON attendance (student_id, att_date);
    CREATE INDEX IF NOT EXISTS idx_attendance_school_date ON attendance (school_id, att_date);

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
    CREATE INDEX IF NOT EXISTS idx_grades_student_id ON grades (student_id);
    CREATE INDEX IF NOT EXISTS idx_grades_school_id ON grades (school_id);
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(create_schema_query)

            # Check if legacy student_records exists to migrate data safely
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'student_records';")
            if cur.fetchone():
                logger.info("Migrating legacy data from student_records into normalized tables...")

                # 1. Copy distinct students into students table
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

                # 2. Copy rows where att_date is NOT NULL into attendance table
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

                # 3. Copy rows where subject is NOT NULL into grades table
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

            # Seed default admin if missing
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'admin@edutrack.com' LIMIT 1;")
            if not cur.fetchone():
                admin_pw = generate_password_hash('admin123')
                cur.execute("""
                    INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                    VALUES (1, 'Administrator', 'admin@edutrack.com', %s, 'admin', 'All');
                """, (admin_pw,))
                logger.info("Default admin account created: admin@edutrack.com / admin123")

            # Seed default teacher for Primary 1 if missing
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'teacher@edutrack.com' LIMIT 1;")
            if not cur.fetchone():
                teacher_pw = generate_password_hash('teacher123')
                cur.execute("""
                    INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                    VALUES (1, 'Sarah Jenkins', 'teacher@edutrack.com', %s, 'teacher', 'Primary 1');
                """, (teacher_pw,))
                logger.info("Default Primary 1 teacher created: teacher@edutrack.com / teacher123")

            # Seed default teacher for Primary 2 if missing
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'teacher2@edutrack.com' LIMIT 1;")
            if not cur.fetchone():
                teacher2_pw = generate_password_hash('teacher123')
                cur.execute("""
                    INSERT INTO teachers (school_id, name, email, password_hash, role, assigned_class)
                    VALUES (1, 'Michael Obi', 'teacher2@edutrack.com', %s, 'teacher', 'Primary 2');
                """, (teacher2_pw,))
                logger.info("Default Primary 2 teacher created: teacher2@edutrack.com / teacher123")

            # Ensure sample student Chidera Okafor exists in students table
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
                logger.info("Sample student Chidera Okafor initialized in students table.")

        conn.commit()
        logger.info("Normalized database schema and auth accounts verified.")
    except Exception as e:
        logger.warning("Could not auto-initialize database tables on startup: %s", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


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
        "architecture": "Normalized 3-Table Design (students, attendance, grades, teachers)",
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
# PROTECTED DASHBOARD ENDPOINT (JOIN + SCHOOL_ID FILTERED)
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
            # 1. Total distinct students (filtered by school_id and class if teacher)
            if is_admin:
                cur.execute("""
                    SELECT COUNT(*)
                    FROM students
                    WHERE school_id = %s;
                """, (school_id,))
            else:
                cur.execute("""
                    SELECT COUNT(*)
                    FROM students
                    WHERE school_id = %s
                      AND LOWER(class) = LOWER(%s);
                """, (school_id, assigned_class))
            total_students = cur.fetchone()[0] or 0

            # 2. Attendance counts for target_date using JOIN between attendance and students
            if is_admin:
                cur.execute("""
                    SELECT 
                        COUNT(*) FILTER (WHERE a.status = 'Present') AS present_count,
                        COUNT(*) FILTER (WHERE a.status = 'Absent') AS absent_count
                    FROM attendance a
                    JOIN students s ON a.student_id = s.id
                    WHERE a.school_id = %s AND a.att_date = %s;
                """, (school_id, target_date))
            else:
                cur.execute("""
                    SELECT 
                        COUNT(*) FILTER (WHERE a.status = 'Present') AS present_count,
                        COUNT(*) FILTER (WHERE a.status = 'Absent') AS absent_count
                    FROM attendance a
                    JOIN students s ON a.student_id = s.id
                    WHERE a.school_id = %s AND a.att_date = %s AND LOWER(s.class) = LOWER(%s);
                """, (school_id, target_date, assigned_class))

            att_row = cur.fetchone()
            present_today = att_row[0] if att_row else 0
            absent_today = att_row[1] if att_row else 0

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
# PROTECTED STUDENTS ENDPOINTS (SCHOOL_ID + ROLE-FILTERED)
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
            if is_admin:
                cur.execute("""
                    SELECT 
                        s.id,
                        s.school_id,
                        s.full_name AS student_name,
                        s.full_name AS name,
                        s.full_name,
                        s.roll_no,
                        s.class AS student_class,
                        s.class,
                        s.gender,
                        s.parent_contact,
                        s.created_at
                    FROM students s
                    WHERE s.school_id = %s
                    ORDER BY s.roll_no ASC;
                """, (school_id,))
            else:
                cur.execute("""
                    SELECT 
                        s.id,
                        s.school_id,
                        s.full_name AS student_name,
                        s.full_name AS name,
                        s.full_name,
                        s.roll_no,
                        s.class AS student_class,
                        s.class,
                        s.gender,
                        s.parent_contact,
                        s.created_at
                    FROM students s
                    WHERE s.school_id = %s
                      AND LOWER(s.class) = LOWER(%s)
                    ORDER BY s.roll_no ASC;
                """, (school_id, assigned_class))

            students = cur.fetchall()

        result = [
            {
                "id": row["id"],
                "school_id": row["school_id"],
                "student_name": row["student_name"],
                "name": row["name"],
                "full_name": row["full_name"],
                "roll_no": row["roll_no"],
                "student_class": row["student_class"] or "Primary 1",
                "class": row["class"] or "Primary 1",
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

    # If teacher, lock class to teacher's assigned class
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
            # Check duplicate roll_no for this school
            cur.execute("""
                SELECT 1 FROM students 
                WHERE school_id = %s AND LOWER(roll_no) = LOWER(%s) 
                LIMIT 1;
            """, (school_id, roll_no))
            if cur.fetchone():
                return jsonify({"error": f"Student with roll number '{roll_no}' already exists in this school"}), 409

            cur.execute("""
                INSERT INTO students (school_id, full_name, roll_no, class, gender, parent_contact)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (school_id, student_name, roll_no, student_class, gender, parent_contact))
            new_id = cur.fetchone()[0]

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
    """
    Updates Full Name, Roll Number, Class, Gender, Parent Contact.
    Updates the single normalized row in `students`.
    """
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
        new_class = assigned_class  # Teacher cannot transfer student out of their class

    if not new_name or not new_roll:
        return jsonify({"error": "Student name and roll number cannot be empty"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 1. Look up student and check class authorization
            cur.execute("""
                SELECT id, roll_no, full_name, class
                FROM students
                WHERE school_id = %s AND (id::text = %s OR LOWER(roll_no) = LOWER(%s))
                LIMIT 1;
            """, (school_id, student_id, student_id))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": f"Student '{student_id}' not found"}), 404

            actual_id, old_roll_no, old_name, old_class = row[0], row[1], row[2], row[3]

            # If teacher, verify student belongs to their class
            if not is_admin and str(old_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: You can only edit students in your assigned class"}), 403

            # 2. If roll_no changed, check for conflict in same school
            if new_roll.lower() != old_roll_no.lower():
                cur.execute("""
                    SELECT 1 FROM students 
                    WHERE school_id = %s AND LOWER(roll_no) = LOWER(%s) AND id != %s 
                    LIMIT 1;
                """, (school_id, new_roll, actual_id))
                if cur.fetchone():
                    return jsonify({"error": f"Cannot rename roll number to '{new_roll}': already in use"}), 409

            # 3. Update the single student record
            cur.execute("""
                UPDATE students
                SET full_name = %s,
                    roll_no = %s,
                    class = %s,
                    gender = %s,
                    parent_contact = %s
                WHERE id = %s AND school_id = %s;
            """, (new_name, new_roll, new_class, new_gender, new_contact, actual_id, school_id))

        conn.commit()
        logger.info("Updated student %s (Roll: %s -> %s) in students table.", old_name, old_roll_no, new_roll)
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
    school_id = current_user.get('school_id', 1)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, roll_no, full_name, class
                FROM students
                WHERE school_id = %s AND (id::text = %s OR LOWER(roll_no) = LOWER(%s))
                LIMIT 1;
            """, (school_id, student_id, student_id))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": f"Student '{student_id}' not found"}), 404

            actual_id, roll_no, student_name, student_class = row[0], row[1], row[2], row[3]

            # If teacher, enforce class boundary
            if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: You can only remove students in your assigned class"}), 403

            # Deletion cascades to attendance and grades via foreign keys ON DELETE CASCADE
            cur.execute("DELETE FROM students WHERE id = %s AND school_id = %s;", (actual_id, school_id))
            deleted_rows = cur.rowcount

        conn.commit()
        return jsonify({
            "message": f"Student '{student_name}' (Roll: {roll_no}) and all records deleted successfully.",
            "deleted_records": deleted_rows
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
# ATTENDANCE ENDPOINTS (JOIN + SCHOOL_ID FILTERED)
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

                # Look up student in students table
                cur.execute("""
                    SELECT id, class 
                    FROM students 
                    WHERE school_id = %s AND (LOWER(roll_no) = LOWER(%s) OR id::text = %s)
                    LIMIT 1;
                """, (school_id, roll_no, str(record.get('id', ''))))
                student_row = cur.fetchone()

                if not student_row:
                    continue

                student_id, student_class = student_row[0], student_row[1]

                # If teacher, enforce class boundary
                if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                    continue

                # Insert/update attendance with UNIQUE(student_id, att_date) constraint
                cur.execute("""
                    INSERT INTO attendance (student_id, school_id, att_date, status, marked_by)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (student_id, att_date) 
                    DO UPDATE SET status = EXCLUDED.status, marked_by = EXCLUDED.marked_by;
                """, (student_id, school_id, att_date, status, marked_by))
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
# REPORT & GRADES ENDPOINTS (JOIN + SCHOOL_ID FILTERED)
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
            # JOIN students with attendance to calculate attendance summary
            cur.execute("""
                SELECT 
                    s.id,
                    s.full_name,
                    s.roll_no,
                    s.class,
                    s.gender,
                    s.parent_contact,
                    COUNT(a.id) AS total,
                    COUNT(a.id) FILTER (WHERE a.status = 'Present') AS present,
                    COUNT(a.id) FILTER (WHERE a.status = 'Absent') AS absent
                FROM students s
                LEFT JOIN attendance a ON s.id = a.student_id AND a.school_id = s.school_id
                WHERE s.school_id = %s AND LOWER(s.roll_no) = LOWER(%s)
                GROUP BY s.id, s.full_name, s.roll_no, s.class, s.gender, s.parent_contact;
            """, (school_id, roll_no))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": f"No student found with roll number '{roll_no}'"}), 404

            student_id, full_name, st_roll, st_class, gender, parent_contact, total, present, absent = row
            st_class = st_class or "Primary 1"

            # If teacher, verify class boundary
            if not is_admin and str(st_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            total = total or 0
            present = present or 0
            absent = absent or 0
            percentage = round((present / total) * 100, 1) if total > 0 else 0.0

            return jsonify({
                "id": student_id,
                "school_id": school_id,
                "roll_no": st_roll,
                "student_name": full_name,
                "name": full_name,
                "full_name": full_name,
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
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    roll_no = data.get('roll_no')
    subject = data.get('subject')
    score = data.get('score')
    term = str(data.get('term') or 'First Term 2026/2027').strip()
    session = str(data.get('session') or '2026/2027').strip()

    if not roll_no or not subject or score is None:
        return jsonify({"error": "'roll_no', 'subject', and 'score' are required"}), 400

    roll_no = str(roll_no).strip()
    subject = str(subject).strip()

    try:
        score_val = round(float(score), 2)
        if score_val < 0 or score_val > 100:
            return jsonify({"error": "'score' must be between 0 and 100"}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "'score' must be a valid numeric value between 0 and 100"}), 400

    grade_letter = compute_grade(score_val)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Verify student exists in students table
            cur.execute("""
                SELECT id, full_name, class
                FROM students 
                WHERE school_id = %s AND LOWER(roll_no) = LOWER(%s)
                LIMIT 1;
            """, (school_id, roll_no))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": f"Student with roll number '{roll_no}' not found"}), 404

            student_id, student_name, student_class = row[0], row[1], row[2] or "Primary 1"

            # If teacher, check class authorization
            if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            # Check if grade for this subject already exists in same term
            cur.execute("""
                SELECT id FROM grades 
                WHERE student_id = %s AND school_id = %s AND LOWER(subject) = LOWER(%s)
                LIMIT 1;
            """, (student_id, school_id, subject))
            existing_grade = cur.fetchone()

            if existing_grade:
                cur.execute("""
                    UPDATE grades
                    SET score = %s, grade = %s, term = %s, session = %s
                    WHERE id = %s AND school_id = %s;
                """, (score_val, grade_letter, term, session, existing_grade[0], school_id))
                grade_id = existing_grade[0]
            else:
                cur.execute("""
                    INSERT INTO grades (student_id, school_id, subject, score, grade, term, session)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                """, (student_id, school_id, subject, score_val, grade_letter, term, session))
                grade_id = cur.fetchone()[0]

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
            # Check student class and get student_id
            cur.execute("""
                SELECT id, class FROM students 
                WHERE school_id = %s AND LOWER(roll_no) = LOWER(%s)
                LIMIT 1;
            """, (school_id, roll_no))
            st_row = cur.fetchone()
            if not st_row:
                return jsonify([]), 200

            if not is_admin and str(st_row['class']).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            student_id = st_row['id']

            # Query normalized grades table by student_id
            cur.execute("""
                SELECT g.id, g.student_id, g.school_id, g.subject, g.score, g.grade, g.term, g.session
                FROM grades g
                WHERE g.student_id = %s AND g.school_id = %s
                ORDER BY g.id ASC;
            """, (student_id, school_id))
            rows = cur.fetchall()

        grades = []
        for row in rows:
            score_val = float(row["score"]) if row["score"] is not None else 0.0
            grade_letter = row["grade"] or compute_grade(score_val)
            grades.append({
                "id": row["id"],
                "student_id": row["student_id"],
                "school_id": row["school_id"],
                "subject": row["subject"],
                "score": score_val,
                "grade": grade_letter,
                "term": row.get("term"),
                "session": row.get("session")
            })

        return jsonify(grades), 200

    except Exception as e:
        logger.error("Error fetching grades: %s", e)
        return jsonify({"error": "Database error while retrieving grades", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route('/api/grades/<int:grade_id>', methods=['DELETE'])
@require_auth
def delete_grade(current_user, grade_id):
    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')
    school_id = current_user.get('school_id', 1)

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Check grade ownership and class permission via JOIN with students
            cur.execute("""
                SELECT g.id, s.class
                FROM grades g
                JOIN students s ON g.student_id = s.id
                WHERE g.id = %s AND g.school_id = %s;
            """, (grade_id, school_id))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": "Grade record not found"}), 404

            student_class = row[1]
            if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: You can only delete grades for your assigned class"}), 403

            cur.execute("DELETE FROM grades WHERE id = %s AND school_id = %s;", (grade_id, school_id))

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
    init_db()

    port = int(os.getenv('FLASK_PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'True').lower() in ('true', '1', 't')

    logger.info("Starting EduTrack API server on port %s...", port)
    app.run(host='0.0.0.0', port=port, debug=debug)
