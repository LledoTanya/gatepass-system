# QR Code Gatepass Management System

Staff scan a rotating QR code at the guard desk, pick their name, and submit a
gatepass request. Admins approve or deny. Guards monitor live and mark returns.

**Stack:** FastAPI + SQLite/SQLAlchemy + Tailwind + vanilla JS + SSE

---

## Setup & Run

```bash
cd gatepass_system
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate
pip install -r requirements.txt
python load_staff.py
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` → sign in with **admin / admin123**.

| Screen          | URL                            | Access           |
|-----------------|--------------------------------|------------------|
| Sign in         | `/login`                       | everyone         |
| Admin dashboard | `/admin`                       | admin            |
| Manage Accounts | `/admin/accounts`              | admin            |
| Guard dashboard | `/guard`                       | guard (or admin) |
| Staff form      | scanned via QR                 | token-gated      |

Default admin account: `admin` / `admin123`. Create guard accounts via Manage Accounts.

---

## Features

### 1. Account Management (Admin)
- **Create / Edit / Delete** admin and guard accounts
- **Reset passwords**, activate/deactivate accounts
- Duplicate username and email prevention, email validation
- Full **audit log** of all account actions

### 2. Notification Center
- Bell icon with unread badge on admin and guard headers
- Notifications triggered on: new requests, approvals, rejections, returns, account changes
- Mark as Read / Mark All as Read
- Notification dropdown panel with history

### 3. Real-Time Guard Dashboard
- **SSE (Server-Sent Events)** for instant updates
- **Polling fallback every 5s** — catches anything SSE misses (tunnels, reconnects)
- Green pulse dot indicates live connection
- New requests appear immediately, status changes update in-place
- Pending count and active counter update automatically

### 4. Staff Notes Visible to Guards
- Notes field shows on guard request cards
- Notes column on admin pending/out/log tables
- Notes included in the printable PDF slip

### 5. Gatepass Workflow
- Staff scan QR → form → pending → admin accepts/declines
- Guard marks returns via "Return" button
- Printable PDF slip matching CPFI-HR-SOP-F-03
- Sickness/illness flow with diagnosis and recommendation
- Staff page survives refresh (localStorage persistence)

---

## Database Schema

**users** — admin and guard accounts

| Column        | Type     | Notes                    |
|---------------|----------|--------------------------|
| id            | INTEGER  | PK                       |
| username      | VARCHAR  | unique                   |
| email         | VARCHAR  | unique                   |
| password_hash | VARCHAR  | SHA-256                  |
| role          | ENUM     | admin / guard            |
| is_active     | BOOLEAN  | true/false               |
| created_at    | DATETIME |                          |

**notifications** — system notification center

| Column      | Type     | Notes                         |
|-------------|----------|-------------------------------|
| id          | INTEGER  | PK                            |
| message     | TEXT     | notification text             |
| type        | VARCHAR  | request / status / account    |
| target_role | VARCHAR  | admin / guard / null (all)    |
| is_read     | BOOLEAN  |                               |
| created_at  | DATETIME |                               |

**audit_log** — account management actions

| Column       | Type     | Notes              |
|--------------|----------|--------------------|
| id           | INTEGER  | PK                 |
| action       | VARCHAR  | create/update/delete/reset |
| details      | TEXT     | human-readable     |
| performed_by | VARCHAR  | username           |
| created_at   | DATETIME |                    |

**staff** and **gatepasses** — unchanged from previous versions.

---

## API Reference

| Method | Path                              | Purpose                          |
|--------|-----------------------------------|----------------------------------|
| POST   | `/login`                          | Sign in                          |
| GET    | `/api/qr`                         | Current QR image + token         |
| POST   | `/api/validate-token`             | Check a scanned token            |
| GET    | `/api/staff`                      | Roster for dropdown              |
| POST   | `/api/gatepass`                   | Submit a request                 |
| GET    | `/api/gatepass/{id}`              | One request's status             |
| GET    | `/api/gatepass/{id}/pdf`          | Printable slip                   |
| GET    | `/api/gatepasses?scope=...`       | pending/active/out/all           |
| POST   | `/api/gatepass/{id}/return`       | Guard marks return               |
| POST   | `/api/gatepass/{id}/{action}`     | Admin approve/deny               |
| GET    | `/api/users`                      | List all accounts                |
| POST   | `/api/users`                      | Create account                   |
| PUT    | `/api/users/{id}`                 | Update account                   |
| DELETE | `/api/users/{id}`                 | Delete account                   |
| POST   | `/api/users/{id}/reset-password`  | Reset password                   |
| GET    | `/api/notifications`              | List notifications               |
| POST   | `/api/notifications/{id}/read`    | Mark one read                    |
| POST   | `/api/notifications/read-all`     | Mark all read                    |
| GET    | `/api/audit-log`                  | Account audit history            |
| GET    | `/api/events`                     | SSE stream                       |
