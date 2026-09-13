# 🎓 EduTrack - Student Attendance & Grades Management System

A complete full-stack web application designed for educational institutions to manage student enrollment, track daily attendance, and record academic performance grades.

---

## 🏗️ Architecture & Tech Stack

- **Frontend**: Single-Page Application (SPA) built with pure **Vanilla HTML5, CSS3, and modern JavaScript (ES6+)**. No external JS frameworks or node dependencies required.
- **Backend**: **Python Flask REST API** with `psycopg2` and CORS support (`flask-cors`).
- **Database**: **PostgreSQL** (`attendance_db`) with a single unified table `student_records` and optimized multi-column indexes.

```
Student_Attendance_full-stack/
├── frontend/
│   └── index.html             # Responsive Single-Page Application (HTML, CSS, JS)
├── backend/
│   ├── app.py                 # Flask REST API server
│   ├── init_database.py       # Automated database provisioning & seed script
│   ├── schema.sql             # SQL table schema, constraints & index definitions
│   ├── requirements.txt       # Python dependencies
│   ├── .env                   # Database connection credentials (local)
│   └── .env.example           # Example configuration template
└── README.md                  # Project documentation
```

---

## 📑 Application Features

### 1. 📅 Mark Attendance
- **Daily Date Selector**: Displays and defaults to today's date (`YYYY-MM-DD`).
- **Student Roster**: Automatically pulls enrolled students from `GET /api/students`.
- **Status Selection**: Interactive **Present** / **Absent** radio toggles (default: *Present*).
- **Batch Shortcuts**: "Mark All Present" & "Mark All Absent" buttons.
- **Save Attendance**: Submits batch attendance records to `POST /api/attendance`.

### 2. 👤 Add Student
- **Enrollment Form**: Quick registration using **Student Full Name** and **Roll Number**.
- **Duplicate Protection**: Prevents duplicate roll numbers client-side and via HTTP 409 backend responses.
- **Auto Sync**: Immediately updates student rosters across all tabs.
- **Student Directory**: Interactive table of all registered students with shortcuts to their performance reports.

### 3. 📊 Grades & Report
- **Student Selector**: Dropdown to choose any enrolled student.
- **Attendance Overview**: Queries `GET /api/report/<roll_no>` and computes `present/total = percentage%` with an animated, color-coded visual progress bar.
- **Record Grades**: Form to record subject names and numerical scores (0 to 100) via `POST /api/grades`.
- **Grade History & Analytics**: Queries `GET /api/grades/<roll_no>` to show subject marks, letter grade badges (A+, A, B, C, D, F), and cumulative average score.

---

## 🗄️ Database Schema (`attendance_db`)

The system utilizes a single table `student_records` with constraints and indexing:

```sql
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

CREATE INDEX IF NOT EXISTS idx_roll_date ON student_records(roll_no, att_date);
```

---

## 🔌 API Endpoints Reference

Base URL: `http://127.0.0.1:5000/api`

| Method | Endpoint | Description | Request Payload | Response |
| :--- | :--- | :--- | :--- | :--- |
| **GET** | `/api/students` | Get all unique students | N/A | `[{"student_name":"...","roll_no":"..."}]` |
| **POST** | `/api/students` | Add new student | `{"student_name":"...","roll_no":"..."}` | `{"message":"Student added"}` |
| **POST** | `/api/attendance` | Record daily attendance | `[{"student_name":"...","roll_no":"...","date":"...","status":"..."}]` | `{"message":"Attendance saved"}` |
| **GET** | `/api/report/<roll_no>` | Get student attendance stats | URL param: `roll_no` | `{"roll_no":"...","present":5,"total":10,"percentage":50.00}` |
| **POST** | `/api/grades` | Add subject grade | `{"student_name":"...","roll_no":"...","subject":"Math","score":85}` | `{"message":"Grade added"}` |
| **GET** | `/api/grades/<roll_no>` | Get all grades for a student | URL param: `roll_no` | `[{"subject":"Math","score":85.0}]` |

---

## 🚀 Setup & Execution Guide

### Prerequisites
- Python 3.8+
- PostgreSQL server installed and running

---

### Step 1: Install Dependencies
Navigate to the `backend` folder and install Python packages:
```bash
cd backend
pip install -r requirements.txt
```

---

### Step 2: Database Configuration & Setup
1. Verify/edit your PostgreSQL credentials in `backend/.env`:
   ```ini
   DB_HOST=localhost
   DB_PORT=5432
   DB_NAME=attendance_db
   DB_USER=postgres
   DB_PASSWORD=your_postgres_password
   FLASK_PORT=5000
   ```

2. Run the automated database initialization script:
   ```bash
   python init_database.py
   ```
   *This automatically creates `attendance_db`, generates the `student_records` table & index, and seeds initial dummy data.*

---

### Step 3: Start the Backend Server
```bash
python app.py
```
The Flask API server will start on `http://127.0.0.1:5000`.

---

### Step 4: Open the Frontend
Open `frontend/index.html` directly in your web browser:
- Double-click `frontend/index.html`, OR
- Use any static server:
  ```bash
  # Option A: Using Python
  cd frontend
  python -m http.server 8000

  # Option B: Using Node.js
  npx serve frontend
  ```
  Then open `http://localhost:8000` in your browser.
