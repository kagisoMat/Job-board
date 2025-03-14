import os
import asyncio
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import create_client
import psycopg2
from psycopg2 import pool

# ✅ Load environment variables
load_dotenv()

# ✅ Database connection using a connection pool
DATABASE_URL = os.getenv("DATABASE_URL")
db_pool = psycopg2.pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)

# ✅ Supabase Credentials
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ✅ FastAPI instance
app = FastAPI()

# ✅ Security for authentication
security = HTTPBearer()

# ✅ Store active WebSocket connections
active_connections = []


# ✅ Home route
@app.get("/")
def home():
    return {"message": "Welcome to our Magic Job Board!"}


# ✅ User Sign-Up
@app.post("/signup")
def signup(email: str, password: str):
    response = supabase.auth.sign_up({"email": email, "password": password})
    if "error" in response:
        raise HTTPException(status_code=400, detail=response["error"]["message"])
    return {"message": "User registered successfully!"}


# ✅ User Login
@app.post("/login")
def login(email: str, password: str):
    response = supabase.auth.sign_in_with_password({"email": email, "password": password})
    if "error" in response:
        raise HTTPException(status_code=400, detail=response["error"]["message"])
    return {"message": "Login successful!", "token": response["session"]["access_token"]}


# ✅ Add Job Posting (Only authenticated users)
@app.post("/add-job")
def add_job(
    title: str, company: str, location: str, salary: int, description: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials

    # Verify user token
    user = supabase.auth.get_user(token)
    if not user or "error" in user:
        raise HTTPException(status_code=401, detail="Invalid token!")

    # Get a database connection
    conn = db_pool.getconn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO jobs (title, company, location, salary, description) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (title, company, location, salary, description)
        )
        job_id = cursor.fetchone()[0]  # ✅ Retrieve the inserted job ID
        conn.commit()

        # ✅ Broadcast new job post to all WebSocket clients
        job_data = {
            "id": job_id,
            "title": title,
            "company": company,
            "location": location,
            "salary": salary,
            "description": description,
        }

        for connection in active_connections:
            try:
                asyncio.create_task(connection.send_json(job_data))
            except:
                active_connections.remove(connection)  # ✅ Remove failed connections

        return {"message": "Job posted successfully!", "job": job_data}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db_pool.putconn(conn)  # ✅ Release the connection back to the pool


# ✅ Apply for a Job
@app.post("/apply")
def apply_for_job(
    job_id: int, cover_letter: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    token = credentials.credentials

    # Verify user token
    user = supabase.auth.get_user(token)
    if not user or "error" in user:
        raise HTTPException(status_code=401, detail="Invalid token!")

    user_email = user["user"]["email"]

    # Get a connection from the pool
    conn = db_pool.getconn()
    try:
        cursor = conn.cursor()

        # Fetch job details
        cursor.execute("SELECT title, company FROM jobs WHERE id = %s", (job_id,))
        job = cursor.fetchone()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found!")

        job_title, company = job

        # Insert application
        cursor.execute(
            "INSERT INTO applications (job_id, user_email, cover_letter) VALUES (%s, %s, %s)",
            (job_id, user_email, cover_letter)
        )
        conn.commit()

        # ✅ Notify WebSocket clients
        application_data = {
            "event": "new_application",
            "job_id": job_id,
            "job_title": job_title,
            "company": company,
            "applicant": user_email,
            "cover_letter": cover_letter
        }

        for connection in active_connections:
            try:
                asyncio.create_task(connection.send_json(application_data))
            except:
                active_connections.remove(connection)

        return {"message": "Application submitted successfully!"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db_pool.putconn(conn)  # ✅ Release the connection


# ✅ WebSocket for real-time job postings
@app.websocket("/ws/jobs")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)

    try:
        while True:
            await websocket.receive_text()  # ✅ Keep connection open
    except WebSocketDisconnect:
        active_connections.remove(websocket)  # ✅ Remove disconnected client
