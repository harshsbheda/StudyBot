# StudyBot Setup (XAMPP + MySQL)

## 1) Install prerequisites
- XAMPP (Apache + MySQL)
- Python 3.10+
- (Optional) Tesseract OCR for image text extraction

## 2) Start MySQL in XAMPP
1. Open XAMPP Control Panel.
2. Start `MySQL`.
3. Optional: Start `Apache` if you want to serve frontend with XAMPP.

## 3) Configure backend environment
From `backend/`:

```powershell
Copy-Item .env.example .env
```

Edit `backend/.env` as needed:
- `DB_HOST=127.0.0.1`
- `DB_PORT=3306`
- `DB_USER=root`
- `DB_PASSWORD=` (blank by default in XAMPP)
- `DB_NAME=studybot_db`
- `GEMINI_API_KEY=...` (optional, but needed for AI features)

## 4) Create database schema
Option A: phpMyAdmin
1. Open `http://localhost/phpmyadmin`
2. Import `backend/database/schema.sql`

Option B: MySQL CLI
```powershell
& "C:\xampp\mysql\bin\mysql.exe" -u root < backend\database\schema.sql
```

## 5) Install Python dependencies
```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 6) Run backend
```powershell
python app.py
```

Health check:
- `http://localhost:5000/api/health`

## 7) Run frontend

Option A (recommended for this project):
- Open `http://localhost:5000`

Option B (serve with XAMPP Apache):
1. Copy `frontend/` into `C:\xampp\htdocs\studybot\`
2. Open `http://localhost/studybot/`
3. Ensure API target in HTML is:
   - `window.STUDYBOT_API_BASE = 'http://localhost:5000/api';`

## 8) Google login (optional)
Set in `frontend/index.html`:
```html
<script>
  window.STUDYBOT_GOOGLE_CLIENT_ID = 'your-google-client-id.apps.googleusercontent.com';
</script>
```
And set matching value in `backend/.env`:
- `GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com`

## 9) Default admin login
- Email: `admin@studybot.com`
- Password: `password`

## Notes
- If `GEMINI_API_KEY` is empty, core app still runs but AI-generated answers/tests will be limited.
- Uploads are saved under `backend/uploads/` by default.
