# 🎓 EduTrack - Student Attendance & Academic Performance Portal

A complete, production-grade full-stack web application designed for educational institutions to manage student enrollment, track daily attendance, record subject grades with auto-calculated performance ratings, and print clean student report cards.

---

## 🏗️ Architecture & Tech Stack

- **Frontend**: Single-Page Application (SPA) built with pure **Vanilla HTML5, CSS3, and modern JavaScript (ES6+)**. Fully mobile-friendly, responsive, and PWA-ready with offline service worker.
- **Backend**: **Python Flask REST API** with `psycopg2-binary` and CORS support (`flask-cors`), deployed on Render.
- **Database**: **PostgreSQL / Neon Postgres** (`Edutrack_project`) with automatic schema migration, cascade deletion, and multi-column indexes.

```
Student_Attendance_full-stack/
├── frontend/
│   ├── index.html             # Responsive Single-Page Application (HTML, CSS, JS)
│   ├── manifest.json          # PWA Web App Manifest
│   └── sw.js                  # PWA Offline Service Worker
├── docs/                      # GitHub Pages / static mirror
│   ├── index.html
│   ├── manifest.json
│   └── sw.js
├── backend/
│   ├── app.py                 # Flask REST API server with Neon/Render DB support
│   ├── init_database.py       # Automated database provisioning & seed script
│   ├── schema.sql             # SQL table schema, constraints & index definitions
│   ├── requirements.txt       # Python dependencies
│   ├── .env                   # Database connection credentials (local)
│   └── .env.example           # Example configuration template
└── README.md                  # Project documentation
```

---

## 📑 Application Features

### 1. 📊 Top Dashboard
- **Total Students**: Total count of registered students in the system.
- **Present Today**: Students marked as Present for today's date.
- **Absent Today**: Students marked as Absent for today's date.
- **Today's Attendance Rate**: Dynamically calculated percentage for today with visual badges.
- **Auto-Sync**: Automatically updates whenever attendance is saved or students are enrolled/removed.

### 2. 📅 Mark Daily Attendance (Mobile-First & Zero Cut-off)
- **Zero Cut-off Guarantee**: Attendance table adapts to mobile devices with full-width segmented radio buttons (`✓ Present` and `✗ Absent`) with ample touch targets.
- **Search & Filter Bar**: Instant search by Student Name or Roll Number, plus dropdown to filter by Class (Primary 1, Primary 2, JSS 1, SSS 1, etc.).
- **Batch Shortcuts**: "Mark All Present" & "Mark All Absent" applying directly to visible filtered students.
- **Direct Database Sync**: Attendance statuses (`Present` / `Absent`) are saved directly to Neon Postgres via `POST /api/attendance` with duplicate prevention for re-takes.

### 3. 👤 Add Student & Directory Management
- **Full Enrollment Form**: Enrolls students with Name, Roll Number, Class (e.g. Primary 1 to 6, JSS 1 to 3, SSS 1 to 3, or custom), Gender, and Parent Contact Number.
- **Duplicate Protection**: Prevents duplicate roll numbers client-side and via HTTP 409 backend responses.
- **Remove Student Feature**: Each student has a `🗑️ Remove` button with confirmation popup: *"Are you sure you want to remove this student?"*. Cascades removal of the student, all attendance rows, and all grades from Neon Postgres via `DELETE /api/students/<id>`.

### 4. 📈 Grades & Auto-Calculation
- **Grading Scale**:
  - **A**: 70%+ (Distinction)
  - **B**: 60 - 69% (Credit)
  - **C**: 50 - 59% (Credit)
  - **D**: 40 - 49% (Pass)
  - **F**: Below 40% (Fail)
- **Live Grade Preview**: Previews the resulting letter grade in real-time as the score is typed.
- **Cumulative Analytics**: Displays cumulative average score and overall letter grade badge.
- **Delete Grade**: Ability to remove individual subject scores.

### 5. 🖨️ Printable Student Report Card
- Generates a clean, professional official report card including:
  - School Header & Branding
  - Student Profile (Name, Roll, Class, Gender, Parent Contact)
  - Editable Academic Session / Term
  - Attendance Summary (Total Days, Days Present, Days Absent, Attendance %)
  - Academic Subject Performance Table (Subject, Score, Grade, Remark)
  - Cumulative Average Score & Overall Grade
  - Editable Class Teacher Remarks / Observations
  - Signature lines for Class Teacher, Principal stamp, and Parent
- **Print / PDF Download**: Optimized with `@media print` CSS so only the pristine report card prints on standard A4 / Letter pages.

---

## 🔌 API Endpoints Reference

| Method | Endpoint | Description | Request Payload | Response |
| :--- | :--- | :--- | :--- | :--- |
| **GET** | `/` | API status check | N/A | Status JSON |
| **GET** | `/api/health` | DB connection health check | N/A | `{"status":"healthy"}` |
| **GET** | `/api/dashboard` | Dashboard metrics for today | Optional: `?date=YYYY-MM-DD` | `{"total_students":N, "present_today":N, "absent_today":N, "attendance_percentage":N}` |
| **GET** | `/api/students` | Get all distinct students | N/A | `[{"id":1, "student_name":"...", "roll_no":"...", "student_class":"...", "gender":"...", "parent_contact":"..."}]` |
| **POST** | `/api/students` | Enroll new student | `{"student_name":"...", "roll_no":"...", "student_class":"...", "gender":"...", "parent_contact":"..."}` | `{"message":"Student added successfully", "student":{...}}` |
| **DELETE** | `/api/students/<id>` | Remove student and all records | URL param: `id` or `roll_no` | `{"message":"Student and all records deleted"}` |
| **POST** | `/api/attendance` | Record daily attendance | `[{"student_name":"...", "roll_no":"...", "student_class":"...", "date":"...", "status":"Present"}]` | `{"message":"Attendance saved successfully"}` |
| **GET** | `/api/report/<roll_no>` | Get student attendance stats | URL param: `roll_no` | `{"roll_no":"...", "present":N, "total":N, "percentage":N}` |
| **POST** | `/api/grades` | Add subject grade | `{"roll_no":"...", "subject":"Math", "score":85}` | `{"message":"Grade added successfully", "grade":{...}}` |
| **GET** | `/api/grades/<roll_no>` | Get all grades for a student | URL param: `roll_no` | `[{"id":1, "subject":"Math", "score":85.0, "grade":"A"}]` |
| **DELETE** | `/api/grades/<id>` | Delete single subject grade | URL param: `id` | `{"message":"Grade record deleted successfully"}` |

---

## 🚀 Running the App Locally

### 1. Backend Setup
```bash
cd backend
pip install -r requirements.txt
python init_database.py
python app.py
```

### 2. Frontend Setup
Open `frontend/index.html` in your browser or run:
```bash
cd frontend
python -m http.server 8000
```
Then visit `http://localhost:8000`.
When deployed to Vercel, the frontend connects seamlessly to your Render backend API.
