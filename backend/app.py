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

def generate_token(user_id, role, assigned_class, email, name):
    """Generates a tamper-proof signed auth token."""
    return auth_serializer.dumps({
        "user_id": user_id,
        "role": role,
        "assigned_class": assigned_class,
        "email": email,
        "name": name
    })


def decode_token(token):
    """Decodes and validates token (valid for 30 days)."""
    try:
        return auth_serializer.loads(token, max_age=2592000)
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


def init_db():
    """
    Initializes database schema by ensuring `teachers` and `student_records` tables exist.
    Non-destructively migrates existing Neon / local Postgres tables and creates
    default admin and teacher accounts.
    """
    create_schema_query = """
    -- Teachers Table for Authentication & RBAC
    CREATE TABLE IF NOT EXISTS teachers (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        email VARCHAR(150) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        role VARCHAR(20) DEFAULT 'teacher', -- 'teacher' or 'admin'
        assigned_class VARCHAR(100) DEFAULT 'Primary 1',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Student Records Table
    CREATE TABLE IF NOT EXISTS student_records (
        id SERIAL PRIMARY KEY,
        student_name VARCHAR(255) NOT NULL,
        roll_no VARCHAR(100) NOT NULL,
        student_class VARCHAR(100),
        gender VARCHAR(20),
        parent_contact VARCHAR(50),
        att_date DATE,
        status VARCHAR(20),
        subject VARCHAR(255),
        score NUMERIC(5, 2),
        grade VARCHAR(10),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Ensure newer columns exist if migrating an older database schema
    ALTER TABLE student_records ADD COLUMN IF NOT EXISTS student_class VARCHAR(100);
    ALTER TABLE student_records ADD COLUMN IF NOT EXISTS gender VARCHAR(20);
    ALTER TABLE student_records ADD COLUMN IF NOT EXISTS parent_contact VARCHAR(50);
    ALTER TABLE student_records ADD COLUMN IF NOT EXISTS grade VARCHAR(10);

    -- Indexes for optimal querying
    CREATE INDEX IF NOT EXISTS idx_student_records_roll_no ON student_records (roll_no);
    CREATE INDEX IF NOT EXISTS idx_student_records_att_date ON student_records (att_date);
    CREATE INDEX IF NOT EXISTS idx_student_records_subject ON student_records (subject);
    CREATE INDEX IF NOT EXISTS idx_student_records_class ON student_records (student_class);
    CREATE INDEX IF NOT EXISTS idx_teachers_email ON teachers (email);
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(create_schema_query)

            # Seed default admin if missing
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'admin@edutrack.com' LIMIT 1;")
            if not cur.fetchone():
                admin_pw = generate_password_hash('admin123')
                cur.execute("""
                    INSERT INTO teachers (name, email, password_hash, role, assigned_class)
                    VALUES ('Administrator', 'admin@edutrack.com', %s, 'admin', 'All');
                """, (admin_pw,))
                logger.info("Default admin account created: admin@edutrack.com / admin123")

            # Seed default teacher for Primary 1 if missing
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'teacher@edutrack.com' LIMIT 1;")
            if not cur.fetchone():
                teacher_pw = generate_password_hash('teacher123')
                cur.execute("""
                    INSERT INTO teachers (name, email, password_hash, role, assigned_class)
                    VALUES ('Sarah Jenkins', 'teacher@edutrack.com', %s, 'teacher', 'Primary 1');
                """, (teacher_pw,))
                logger.info("Default Primary 1 teacher created: teacher@edutrack.com / teacher123")

            # Seed default teacher for Primary 2 if missing
            cur.execute("SELECT id FROM teachers WHERE LOWER(email) = 'teacher2@edutrack.com' LIMIT 1;")
            if not cur.fetchone():
                teacher2_pw = generate_password_hash('teacher123')
                cur.execute("""
                    INSERT INTO teachers (name, email, password_hash, role, assigned_class)
                    VALUES ('Michael Obi', 'teacher2@edutrack.com', %s, 'teacher', 'Primary 2');
                """, (teacher2_pw,))
                logger.info("Default Primary 2 teacher created: teacher2@edutrack.com / teacher123")

            # Seed Chidera Okafor if missing so old sample student test works
            cur.execute("SELECT id FROM student_records WHERE LOWER(roll_no) = '201' LIMIT 1;")
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO student_records (student_name, roll_no, student_class, gender, parent_contact)
                    VALUES ('Chidera Okafor', '201', 'Primary 2', 'Unspecified', 'N/A');
                    INSERT INTO student_records (student_name, roll_no, student_class, att_date, status)
                    VALUES ('Chidera Okafor', '201', 'Primary 2', CURRENT_DATE, 'Present');
                    INSERT INTO student_records (student_name, roll_no, student_class, subject, score, grade)
                    VALUES ('Chidera Okafor', '201', 'Primary 2', 'Mathematics', 75.0, 'A');
                """)
                logger.info("Sample student Chidera Okafor added for edit testing.")

        conn.commit()
        logger.info("Database schema and auth accounts verified.")
    except Exception as e:
        logger.warning("Could not auto-initialize database table on startup: %s", e)
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
        "auth": "Token-based (Bearer Token / RBAC)",
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
    Body: { "name": "...", "email": "...", "password": "...", "assigned_class": "Primary 1" }
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    name = str(data.get('name', '')).strip()
    email = str(data.get('email', '')).strip().lower()
    password = str(data.get('password', '')).strip()
    assigned_class = str(data.get('assigned_class', 'Primary 1')).strip()
    role = str(data.get('role', 'teacher')).strip().lower()

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
                INSERT INTO teachers (name, email, password_hash, role, assigned_class)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
            """, (name, email, password_hash, role, assigned_class))
            user_id = cur.fetchone()[0]

        conn.commit()

        token = generate_token(user_id, role, assigned_class, email, name)
        return jsonify({
            "message": "Account registered successfully",
            "token": token,
            "user": {
                "id": user_id,
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
                SELECT id, name, email, password_hash, role, assigned_class
                FROM teachers
                WHERE LOWER(email) = %s
                LIMIT 1;
            """, (email,))
            user = cur.fetchone()

        if not user or not check_password_hash(user['password_hash'], password):
            return jsonify({"error": "Invalid email or password"}), 401

        token = generate_token(
            user['id'],
            user['role'],
            user['assigned_class'] or 'Primary 1',
            user['email'],
            user['name']
        )

        return jsonify({
            "message": "Login successful",
            "token": token,
            "user": {
                "id": user['id'],
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
    """Returns currently authenticated user profile."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, email, role, assigned_class
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
# PROTECTED DASHBOARD ENDPOINT (ROLE-FILTERED)
# ==========================================================

@app.route('/api/dashboard', methods=['GET'])
@require_auth
def get_dashboard_stats(current_user):
    date_param = request.args.get('date')
    target_date = date_param.strip() if date_param else datetime.date.today().isoformat()

    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 1. Total distinct students (scoped to class if teacher)
            if is_admin:
                cur.execute("""
                    SELECT COUNT(DISTINCT roll_no)
                    FROM student_records
                    WHERE student_name IS NOT NULL AND roll_no IS NOT NULL;
                """)
            else:
                cur.execute("""
                    SELECT COUNT(DISTINCT roll_no)
                    FROM student_records
                    WHERE student_name IS NOT NULL AND roll_no IS NOT NULL
                      AND LOWER(student_class) = LOWER(%s);
                """, (assigned_class,))
            total_students = cur.fetchone()[0] or 0

            # 2. Attendance counts for target_date (scoped to class if teacher)
            if is_admin:
                cur.execute("""
                    SELECT 
                        COUNT(DISTINCT roll_no) FILTER (WHERE status = 'Present') AS present_count,
                        COUNT(DISTINCT roll_no) FILTER (WHERE status = 'Absent') AS absent_count
                    FROM student_records
                    WHERE att_date = %s;
                """, (target_date,))
            else:
                cur.execute("""
                    SELECT 
                        COUNT(DISTINCT roll_no) FILTER (WHERE status = 'Present') AS present_count,
                        COUNT(DISTINCT roll_no) FILTER (WHERE status = 'Absent') AS absent_count
                    FROM student_records
                    WHERE att_date = %s AND LOWER(student_class) = LOWER(%s);
                """, (target_date, assigned_class))

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
# PROTECTED STUDENTS ENDPOINTS (ROLE-FILTERED)
# ==========================================================

@app.route('/api/students', methods=['GET'])
@require_auth
def get_students(current_user):
    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if is_admin:
                cur.execute("""
                    SELECT 
                        MIN(id) AS id,
                        student_name,
                        roll_no,
                        COALESCE(MAX(NULLIF(student_class, '')), 'Primary 1') AS student_class,
                        COALESCE(MAX(NULLIF(gender, '')), 'Unspecified') AS gender,
                        COALESCE(MAX(NULLIF(parent_contact, '')), '') AS parent_contact
                    FROM student_records
                    WHERE student_name IS NOT NULL AND roll_no IS NOT NULL
                    GROUP BY roll_no, student_name
                    ORDER BY roll_no ASC;
                """)
            else:
                cur.execute("""
                    SELECT 
                        MIN(id) AS id,
                        student_name,
                        roll_no,
                        COALESCE(MAX(NULLIF(student_class, '')), 'Primary 1') AS student_class,
                        COALESCE(MAX(NULLIF(gender, '')), 'Unspecified') AS gender,
                        COALESCE(MAX(NULLIF(parent_contact, '')), '') AS parent_contact
                    FROM student_records
                    WHERE student_name IS NOT NULL AND roll_no IS NOT NULL
                      AND LOWER(student_class) = LOWER(%s)
                    GROUP BY roll_no, student_name
                    ORDER BY roll_no ASC;
                """, (assigned_class,))

            students = cur.fetchall()

        result = [
            {
                "id": row["id"],
                "student_name": row["student_name"],
                "name": row["student_name"],
                "roll_no": row["roll_no"],
                "student_class": row["student_class"],
                "class": row["student_class"],
                "gender": row["gender"],
                "parent_contact": row["parent_contact"]
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

    student_name = data.get('student_name') or data.get('name')
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
            # Check duplicate roll_no
            cur.execute("SELECT 1 FROM student_records WHERE LOWER(roll_no) = LOWER(%s) LIMIT 1;", (roll_no,))
            if cur.fetchone():
                return jsonify({"error": f"Student with roll number '{roll_no}' already exists"}), 409

            cur.execute("""
                INSERT INTO student_records (student_name, roll_no, student_class, gender, parent_contact)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
            """, (student_name, roll_no, student_class, gender, parent_contact))
            new_id = cur.fetchone()[0]

        conn.commit()
        return jsonify({
            "message": "Student added successfully",
            "student": {
                "id": new_id,
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
    """
    Requirement 1: Edit Student Details
    Updates Full Name, Roll Number, Class, Gender, Parent Contact.
    Cascades updates to all attendance and grade rows for this student.
    """
    student_id = student_id.strip()
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')

    new_name = str(data.get('student_name') or data.get('name', '')).strip()
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
                SELECT roll_no, student_name, student_class
                FROM student_records
                WHERE id::text = %s OR LOWER(roll_no) = LOWER(%s)
                LIMIT 1;
            """, (student_id, student_id))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": f"Student '{student_id}' not found"}), 404

            old_roll_no, old_name, old_class = row[0], row[1], row[2]

            # If teacher, verify student belongs to their class
            if not is_admin and str(old_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: You can only edit students in your assigned class"}), 403

            # 2. If roll_no changed, check for conflict
            if new_roll.lower() != old_roll_no.lower():
                cur.execute("SELECT 1 FROM student_records WHERE LOWER(roll_no) = LOWER(%s) LIMIT 1;", (new_roll,))
                if cur.fetchone():
                    return jsonify({"error": f"Cannot rename roll number to '{new_roll}': already in use"}), 409

            # 3. Update all rows (base, attendance, grades) for this student
            cur.execute("""
                UPDATE student_records
                SET student_name = %s,
                    roll_no = %s,
                    student_class = %s,
                    gender = %s,
                    parent_contact = %s
                WHERE LOWER(roll_no) = LOWER(%s) OR id::text = %s;
            """, (new_name, new_roll, new_class, new_gender, new_contact, old_roll_no, student_id))
            updated_count = cur.rowcount

        conn.commit()
        logger.info("Updated student %s (Roll: %s -> %s) across %s rows.", old_name, old_roll_no, new_roll, updated_count)
        return jsonify({
            "message": f"Student '{new_name}' updated successfully",
            "student": {
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
    student_id = student_id.strip()
    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT roll_no, student_name, student_class
                FROM student_records
                WHERE id::text = %s OR LOWER(roll_no) = LOWER(%s)
                LIMIT 1;
            """, (student_id, student_id))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": f"Student '{student_id}' not found"}), 404

            roll_no, student_name, student_class = row[0], row[1], row[2]

            # If teacher, enforce class boundary
            if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: You can only remove students in your assigned class"}), 403

            cur.execute("DELETE FROM student_records WHERE LOWER(roll_no) = LOWER(%s);", (roll_no,))
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
# ATTENDANCE ENDPOINTS
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

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            saved_count = 0
            for record in records:
                roll_no = record.get('roll_no')
                student_name = record.get('student_name') or record.get('name')
                att_date = record.get('date') or record.get('att_date')
                status = record.get('status', 'Present')
                student_class = record.get('student_class') or record.get('class')

                if status not in ('Present', 'Absent'):
                    status = 'Present'

                if not roll_no:
                    continue

                roll_no = str(roll_no).strip()

                # Look up existing class to verify permission
                cur.execute("""
                    SELECT student_name, student_class 
                    FROM student_records 
                    WHERE LOWER(roll_no) = LOWER(%s) AND student_name IS NOT NULL 
                    LIMIT 1;
                """, (roll_no,))
                existing = cur.fetchone()

                if existing:
                    student_name = student_name or existing[0]
                    student_class = existing[1] or student_class or 'Primary 1'

                # If teacher, verify student belongs to their class
                if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                    continue  # Skip records for other classes

                # Delete existing attendance for this date and roll_no to prevent duplicates
                cur.execute(
                    "DELETE FROM student_records WHERE LOWER(roll_no) = LOWER(%s) AND att_date = %s;",
                    (roll_no, att_date)
                )

                cur.execute("""
                    INSERT INTO student_records (student_name, roll_no, student_class, att_date, status)
                    VALUES (%s, %s, %s, %s, %s);
                """, (student_name or "Student", roll_no, student_class, att_date, status))
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
# REPORT & GRADES ENDPOINTS
# ==========================================================

@app.route('/api/report/<string:roll_no>', methods=['GET'])
@app.route('/report/<string:roll_no>', methods=['GET'])
@require_auth
def get_student_report(current_user, roll_no):
    roll_no = roll_no.strip()
    is_admin = current_user.get('role') == 'admin'
    assigned_class = current_user.get('assigned_class')

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    COUNT(*) FILTER (WHERE att_date IS NOT NULL) AS total,
                    COUNT(*) FILTER (WHERE att_date IS NOT NULL AND status = 'Present') AS present,
                    COUNT(*) FILTER (WHERE att_date IS NOT NULL AND status = 'Absent') AS absent,
                    MAX(student_name) AS student_name,
                    COALESCE(MAX(NULLIF(student_class, '')), 'Primary 1') AS student_class,
                    COALESCE(MAX(NULLIF(gender, '')), 'Unspecified') AS gender,
                    COALESCE(MAX(NULLIF(parent_contact, '')), '') AS parent_contact
                FROM student_records
                WHERE LOWER(roll_no) = LOWER(%s);
            """, (roll_no,))
            row = cur.fetchone()

            if not row or not row[3]:
                return jsonify({"error": f"No student found with roll number '{roll_no}'"}), 404

            student_class = row[4] or "Primary 1"

            # If teacher, verify class boundary
            if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            total = row[0] or 0
            present = row[1] or 0
            absent = row[2] or 0
            student_name = row[3] or "Student"
            gender = row[5] or "Unspecified"
            parent_contact = row[6] or ""
            percentage = round((present / total) * 100, 1) if total > 0 else 0.0

            return jsonify({
                "roll_no": roll_no,
                "student_name": student_name,
                "student_class": student_class,
                "gender": gender,
                "parent_contact": parent_contact,
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

    roll_no = data.get('roll_no')
    student_name = data.get('student_name') or data.get('name')
    subject = data.get('subject')
    score = data.get('score')

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
            cur.execute("""
                SELECT student_name, student_class
                FROM student_records 
                WHERE LOWER(roll_no) = LOWER(%s) AND student_name IS NOT NULL 
                LIMIT 1;
            """, (roll_no,))
            row = cur.fetchone()

            if not row:
                return jsonify({"error": f"Student with roll number '{roll_no}' not found"}), 404

            student_name = student_name or row[0]
            student_class = row[1] or "Primary 1"

            # If teacher, check class authorization
            if not is_admin and str(student_class).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            # Check if grade for this subject already exists: if so, update it cleanly
            cur.execute("""
                SELECT id FROM student_records 
                WHERE LOWER(roll_no) = LOWER(%s) AND LOWER(subject) = LOWER(%s)
                LIMIT 1;
            """, (roll_no, subject))
            existing_grade = cur.fetchone()

            if existing_grade:
                cur.execute("""
                    UPDATE student_records
                    SET score = %s, grade = %s, student_name = %s, student_class = %s
                    WHERE id = %s;
                """, (score_val, grade_letter, student_name, student_class, existing_grade[0]))
                grade_id = existing_grade[0]
            else:
                cur.execute("""
                    INSERT INTO student_records (student_name, roll_no, student_class, subject, score, grade)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id;
                """, (student_name, roll_no, student_class, subject, score_val, grade_letter))
                grade_id = cur.fetchone()[0]

        conn.commit()
        return jsonify({
            "message": "Grade saved successfully",
            "grade": {
                "id": grade_id,
                "roll_no": roll_no,
                "student_name": student_name,
                "subject": subject,
                "score": score_val,
                "grade": grade_letter
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

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check student class
            cur.execute("""
                SELECT student_class FROM student_records 
                WHERE LOWER(roll_no) = LOWER(%s) AND student_name IS NOT NULL 
                LIMIT 1;
            """, (roll_no,))
            st_row = cur.fetchone()
            if not st_row:
                return jsonify([]), 200

            if not is_admin and str(st_row['student_class']).lower() != str(assigned_class).lower():
                return jsonify({"error": "Access denied: Student belongs to another class"}), 403

            cur.execute("""
                SELECT id, subject, score, grade
                FROM student_records
                WHERE LOWER(roll_no) = LOWER(%s) AND subject IS NOT NULL
                ORDER BY id ASC;
            """, (roll_no,))
            rows = cur.fetchall()

        grades = []
        for row in rows:
            score_val = float(row["score"]) if row["score"] is not None else 0.0
            grade_letter = row["grade"] or compute_grade(score_val)
            grades.append({
                "id": row["id"],
                "subject": row["subject"],
                "score": score_val,
                "grade": grade_letter
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
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM student_records WHERE id = %s AND subject IS NOT NULL;", (grade_id,))
            if cur.rowcount == 0:
                return jsonify({"error": "Grade record not found"}), 404
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
