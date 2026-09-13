import os
import logging
# pyrefly: ignore [missing-import]
from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Enable CORS for all routes (enabling frontend on any origin/port to connect)
CORS(app, resources={r"/*": {"origins": "*"}})

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Database configuration
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'attendance_db')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')
DATABASE_URL = os.getenv('DATABASE_URL')


def get_db_connection():
    """
    Creates and returns a new psycopg2 database connection.
    Supports either DATABASE_URL or individual connection parameters.
    """
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


def init_db():
    """
    Initializes the database schema by ensuring the single `student_records` table exists.
    """
    create_table_query = """
    CREATE TABLE IF NOT EXISTS student_records (
        id SERIAL PRIMARY KEY,
        student_name VARCHAR(255),
        roll_no VARCHAR(100),
        att_date DATE,
        status VARCHAR(20),
        subject VARCHAR(255),
        score NUMERIC(5, 2),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_student_records_roll_no ON student_records (roll_no);
    CREATE INDEX IF NOT EXISTS idx_student_records_att_date ON student_records (att_date);
    CREATE INDEX IF NOT EXISTS idx_student_records_subject ON student_records (subject);
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(create_table_query)
        conn.commit()
        logger.info("Database schema verified: `student_records` table is ready.")
    except Exception as e:
        logger.warning("Could not auto-initialize database table on startup (DB may not be ready yet): %s", e)
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


# ==========================================================
# HEALTH & STATUS ENDPOINTS
# ==========================================================

@app.route('/', methods=['GET'])
def root():
    """Root endpoint for status check."""
    return jsonify({
        "status": "online",
        "service": "Student Attendance & Grades API",
        "database": DB_NAME,
        "endpoints": [
            "POST /api/students",
            "GET /api/students",
            "POST /api/attendance",
            "GET /api/report/<roll_no>",
            "POST /api/grades",
            "GET /api/grades/<roll_no>"
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
# 1. POST /api/students
# Body: {"student_name":"John Doe", "roll_no":"101"}
# Inserts a row with only name + roll_no. Other fields NULL.
# Return {"message":"Student added"}
# ==========================================================
@app.route('/api/students', methods=['POST'])
def add_student():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    # Support student_name or name for flexibility
    student_name = data.get('student_name') or data.get('name')
    roll_no = data.get('roll_no')

    if not student_name or not roll_no:
        return jsonify({"error": "Both 'student_name' and 'roll_no' are required"}), 400

    student_name = str(student_name).strip()
    roll_no = str(roll_no).strip()

    if not student_name or not roll_no:
        return jsonify({"error": "'student_name' and 'roll_no' cannot be empty"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Check for duplicate roll_no
            cur.execute(
                "SELECT 1 FROM student_records WHERE roll_no = %s LIMIT 1;",
                (roll_no,)
            )
            if cur.fetchone():
                return jsonify({"error": f"Student with roll number '{roll_no}' already exists"}), 409

            # Parameterized insert: name + roll_no only, other fields remain NULL
            cur.execute(
                "INSERT INTO student_records (student_name, roll_no) VALUES (%s, %s);",
                (student_name, roll_no)
            )
        conn.commit()
        return jsonify({"message": "Student added"}), 201

    except Exception as e:
        logger.error("Error adding student: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while adding student", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# 2. GET /api/students
# Return distinct students: SELECT DISTINCT student_name, roll_no FROM student_records
# ==========================================================
@app.route('/api/students', methods=['GET'])
def get_students():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT DISTINCT student_name, roll_no
                FROM student_records
                WHERE student_name IS NOT NULL AND roll_no IS NOT NULL
                ORDER BY roll_no ASC;
                """
            )
            students = cur.fetchall()

        # Format output as clean list of JSON objects
        result = [
            {
                "student_name": row["student_name"],
                "roll_no": row["roll_no"],
                "name": row["student_name"]  # Compatibility alias
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


# ==========================================================
# 3. POST /api/attendance
# Body: [{"student_name":"John Doe","roll_no":"101","date":"2026-09-11","status":"Present"}]
# Insert new row per student per day.
# Return {"message":"Attendance saved"}
# ==========================================================
@app.route('/api/attendance', methods=['POST'])
def save_attendance():
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    # Ensure data is a list of records
    records = data if isinstance(data, list) else [data]

    if not records:
        return jsonify({"error": "Attendance records array cannot be empty"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            insert_query = """
                INSERT INTO student_records (student_name, roll_no, att_date, status)
                VALUES (%s, %s, %s, %s);
            """
            for record in records:
                roll_no = record.get('roll_no')
                student_name = record.get('student_name') or record.get('name')
                att_date = record.get('date') or record.get('att_date')
                status = record.get('status', 'Present')

                if not roll_no:
                    # If roll_no is missing but student_id provided, look up roll_no
                    student_id = record.get('student_id')
                    if student_id:
                        cur.execute("SELECT student_name, roll_no FROM student_records WHERE id = %s LIMIT 1;", (student_id,))
                        row = cur.fetchone()
                        if row:
                            student_name = student_name or row[0]
                            roll_no = row[1]

                if not roll_no:
                    continue  # Skip invalid entries

                # If student_name is not provided, fetch from existing records
                if not student_name:
                    cur.execute(
                        "SELECT student_name FROM student_records WHERE roll_no = %s AND student_name IS NOT NULL LIMIT 1;",
                        (roll_no,)
                    )
                    row = cur.fetchone()
                    student_name = row[0] if row else "Unknown"

                cur.execute(
                    insert_query,
                    (student_name, str(roll_no), att_date, status)
                )

        conn.commit()
        return jsonify({"message": "Attendance saved"}), 201

    except Exception as e:
        logger.error("Error saving attendance: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while saving attendance", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# 4. GET /api/report/<roll_no>
# - total = COUNT rows where att_date IS NOT NULL
# - present = COUNT rows where status='Present'
# - percentage = round(present/total*100, 2) or 0 if total=0
# Return {"roll_no":roll_no, "present":5, "total":10, "percentage":50.00}
# ==========================================================
@app.route('/api/report/<string:roll_no>', methods=['GET'])
def get_student_report(roll_no):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Query total attendance days and present days for this roll_no
            query = """
                SELECT 
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE status = 'Present') AS present
                FROM student_records
                WHERE roll_no = %s AND att_date IS NOT NULL;
            """
            cur.execute(query, (str(roll_no),))
            row = cur.fetchone()

            total = row[0] if row else 0
            present = row[1] if row else 0

            percentage = round((present / total) * 100, 2) if total > 0 else 0.0

            return jsonify({
                "roll_no": str(roll_no),
                "present": present,
                "total": total,
                "percentage": percentage
            }), 200

    except Exception as e:
        logger.error("Error generating report for roll_no %s: %s", roll_no, e)
        return jsonify({"error": "Database error while generating report", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# 5. POST /api/grades
# Body: {"student_name":"John Doe","roll_no":"101","subject":"Math","score":85}
# Insert new row with grade.
# Return {"message":"Grade added"}
# ==========================================================
@app.route('/api/grades', methods=['POST'])
def add_grade():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON"}), 400

    roll_no = data.get('roll_no')
    student_name = data.get('student_name') or data.get('name')
    subject = data.get('subject')
    score = data.get('score')

    if not roll_no:
        # Check student_id alias
        student_id = data.get('student_id')
        if student_id:
            roll_no = str(student_id)

    if not roll_no or not subject or score is None:
        return jsonify({"error": "'roll_no', 'subject', and 'score' are required"}), 400

    try:
        score_val = float(score)
        if score_val < 0 or score_val > 100:
            return jsonify({"error": "'score' must be between 0 and 100"}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "'score' must be a valid numeric value"}), 400

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Look up student_name if not provided
            if not student_name:
                cur.execute(
                    "SELECT student_name FROM student_records WHERE roll_no = %s AND student_name IS NOT NULL LIMIT 1;",
                    (str(roll_no),)
                )
                row = cur.fetchone()
                student_name = row[0] if row else "Unknown"

            # Parameterized insert with grade record
            cur.execute(
                """
                INSERT INTO student_records (student_name, roll_no, subject, score)
                VALUES (%s, %s, %s, %s);
                """,
                (student_name, str(roll_no), str(subject).strip(), score_val)
            )

        conn.commit()
        return jsonify({"message": "Grade added"}), 201

    except Exception as e:
        logger.error("Error adding grade: %s", e)
        if conn:
            conn.rollback()
        return jsonify({"error": "Database error while adding grade", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# 6. GET /api/grades/<roll_no>
# Return all rows where subject IS NOT NULL: [{"subject":"Math","score":85}]
# ==========================================================
@app.route('/api/grades/<string:roll_no>', methods=['GET'])
def get_grades(roll_no):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT subject, score
                FROM student_records
                WHERE roll_no = %s AND subject IS NOT NULL
                ORDER BY id ASC;
            """
            cur.execute(query, (str(roll_no),))
            rows = cur.fetchall()

        # Format output as clean JSON list
        result = [
            {
                "subject": row["subject"],
                "score": float(row["score"]) if row["score"] is not None else 0.0
            }
            for row in rows
        ]
        return jsonify(result), 200

    except Exception as e:
        logger.error("Error fetching grades for roll_no %s: %s", roll_no, e)
        return jsonify({"error": "Database error while retrieving grades", "details": str(e)}), 500
    finally:
        if conn:
            conn.close()


# ==========================================================
# SERVER STARTUP
# ==========================================================
if __name__ == '__main__':
    # Initialize DB table on startup
    init_db()

    port = int(os.getenv('FLASK_PORT', 5000))
    debug = os.getenv('FLASK_DEBUG', 'True').lower() in ('true', '1', 't')
    
    logger.info("Starting Student Attendance & Grades API server on port %s...", port)
    app.run(host='0.0.0.0', port=port, debug=debug)
