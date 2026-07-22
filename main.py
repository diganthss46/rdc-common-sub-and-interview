import json
import re
import random
import time
import os
import sqlite3
import pdfplumber
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from google import genai
from fastapi.responses import HTMLResponse
import os

import os
from google import genai

# This looks for the environment variable you set on Render
api_key = os.environ.get("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)
GEMINI_API_KEY=api_key
# --- CONFIGURATION ---

PDF_FILENAME = "NCC Common Subject[1].pdf"
DB_NAME = "quiz_bank.db"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class InterviewTurn(BaseModel):
    role: str  # "interviewer" or "cadet"
    message: str

class InterviewRequest(BaseModel):
    history: List[InterviewTurn] 

class Question(BaseModel):
    category: str
    question: str
    options: List[str]
    correct_answer: str
    explanation: str

class QuizResponse(BaseModel):
    source: str
    total_questions: int
    questions: List[Question]
@app.get("/", response_class=HTMLResponse)
def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h3>NCC Master Suite Backend is Live!</h3>"

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            question TEXT UNIQUE,
            options TEXT,
            correct_answer TEXT,
            explanation TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- GEMINI CLIENT WRAPPER WITH RETRY ---
def ask_gemini_json(prompt: str) -> List[dict]:
    if not GEMINI_API_KEY or "paste_your" in GEMINI_API_KEY:
        print("[ERROR] GEMINI_API_KEY is not set correctly!")
        return []

    client = genai.Client(api_key=GEMINI_API_KEY)
    models_to_try = ['gemini-3.5-flash', 'gemini-3.5-flash-lite', 'gemini-3.6-flash']
    
    for model_name in models_to_try:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                raw_text = response.text.strip()
                json_match = re.search(r'\[.*\]', raw_text, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group(0))
                    if isinstance(parsed, list) and len(parsed) > 0:
                        return parsed
            except Exception as e:
                print(f"[Model {model_name} Attempt {attempt+1}] Error: {e}")
                time.sleep(1)
    return []

# --- AUTO-POPULATE SYLLABUS BANK FROM LOCAL PDF IF EMPTY ---
def populate_syllabus_bank():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM questions WHERE category = 'NCC Syllabus'")
    count = cursor.fetchone()[0]
    conn.close()

    if count >= 30:
        return  # Already cached

    if not os.path.exists(PDF_FILENAME):
        print(f"[WARNING] Pre-loaded PDF '{PDF_FILENAME}' not found. Syllabus bank cannot auto-populate.")
        return

    print("\n[INFO] Auto-extracting syllabus questions from pre-loaded PDF into database...")
    with pdfplumber.open(PDF_FILENAME) as pdf:
        pdf_text = "".join([page.extract_text() for page in pdf.pages[3:25] if page.extract_text()])

    prompt = f"""
    Generate 40 diverse multiple-choice questions testing core NCC syllabus text.
    Return ONLY a raw JSON array of objects:
    [{{ "category": "NCC Syllabus", "question": "...", "options": ["A","B","C","D"], "correct_answer": "...", "explanation": "..." }}]
    Text: {pdf_text[:12000]}
    """
    qs = ask_gemini_json(prompt)
    if qs:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        for q in qs:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO questions (category, question, options, correct_answer, explanation) VALUES (?, ?, ?, ?, ?)",
                    (q['category'], q['question'], json.dumps(q['options']), q['correct_answer'], q['explanation'])
                )
            except: pass
        conn.commit()
        conn.close()
        print("[INFO] Syllabus cache successfully created in SQLite database.")

populate_syllabus_bank()

# --- SMART ANTI-DUPLICATION FILTER FOR FRESH GK ---
def get_existing_question_samples(limit: int = 40) -> List[str]:
    if not os.path.exists(DB_NAME):
        return []
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT question FROM questions ORDER BY RANDOM() LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def generate_unique_fresh_gk_questions(count: int) -> List[dict]:
    existing_samples = get_existing_question_samples(limit=40)
    avoid_list_str = "\n".join([f"- {q}" for q in existing_samples]) if existing_samples else "None yet."

    prompt = f"""
    You are a senior Military Historian and General Knowledge Examiner for the NCC Best Cadet RDC Exam.
    Generate exactly {count} brand-new, unique multiple-choice questions focusing on:
    1. Modern developments, defence current affairs, and leadership appointments.
    2. Classic military history, historic wars (1965, 1971, Kargil), Param Vir Chakra winners, ranks, and strategic operations.
    
    STRICT ANTI-DUPLICATION RULE:
    Do NOT repeat or closely rephrase any of these questions already found in our database:
    {avoid_list_str}

    Return ONLY a raw JSON array of objects with this exact structure:
    [
      {{
        "category": "Defence GK & Current Affairs",
        "question": "Completely new question text?",
        "options": ["Option A", "Option B", "Option C", "Option D"],
        "correct_answer": "Exact string of correct option",
        "explanation": "Short explanatory fact."
      }}
    ]
    """
    return ask_gemini_json(prompt)

# --- GRAND TEST ENDPOINT ---
@app.get("/generate/grand-test", response_model=QuizResponse)
def generate_grand_test():
    gk_count = random.randint(25, 40)
    syllabus_count = 100 - gk_count
    
    print(f"\n[INFO] Building 100-Q Test: {syllabus_count} DB Syllabus + {gk_count} Unique Fresh GK...")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT category, question, options, correct_answer, explanation FROM questions WHERE category = 'NCC Syllabus' ORDER BY RANDOM() LIMIT ?", (syllabus_count,))
    syll_rows = cursor.fetchall()
    conn.close()

    formatted_syllabus = [{
        "category": row[0],
        "question": row[1],
        "options": json.loads(row[2]),
        "correct_answer": row[3],
        "explanation": row[4]
    } for row in syll_rows]

    # Generate fresh GK with anti-duplication check
    fresh_gk = generate_unique_fresh_gk_questions(gk_count)

    # Cache newly generated unique GK questions back into the database
    if fresh_gk:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        for q in fresh_gk:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO questions (category, question, options, correct_answer, explanation) VALUES (?, ?, ?, ?, ?)",
                    (q.get('category', 'Defence GK'), q['question'], json.dumps(q['options']), q['correct_answer'], q['explanation'])
                )
            except: pass
        conn.commit()
        conn.close()

    all_questions = formatted_syllabus + fresh_gk

    if len(all_questions) < 50:
        raise HTTPException(status_code=500, detail="Could not assemble full test paper. Please check server logs.")

    random.shuffle(all_questions)

    return {
        "source": f"Cached Syllabus ({len(formatted_syllabus)}) + Unique Dynamic GK & Current Affairs ({len(fresh_gk)})",
        "total_questions": len(all_questions),
        "questions": all_questions
    }

# --- SSB / RDC STYLE INTERVIEW SIMULATOR ---
# --- ADAPTIVE PIQ & SSB / RDC INTERVIEW ENDPOINT ---
@app.post("/interview/chat")
def interview_chat(req: InterviewRequest):
    if not GEMINI_API_KEY or "paste_your" in GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API key missing.")

    client = genai.Client(api_key=GEMINI_API_KEY)

    system_instruction = """
    You are a sharp, seasoned, and uncompromising President of the SSB / RDC Best Cadet Selection Board. 
    Your goal is to conduct a rigorous, high-pressure personal interview with an NCC cadet.

    CRITICAL BEHAVIORAL RULE (CRITIQUE & CROSS-EXAMINATION):
    - If the cadet states an incorrect fact, displays poor logic, gives a cliché/rehearsed textbook answer, or dodges a question, DO NOT let it slide. 
    - Actively and professionally **criticize or challenge** them. Point out the flaw in their reasoning, expose factual errors directly, or question their integrity/attitude with sharp military bluntness (e.g., "Cadet, that makes no sense strategically," or "That is a textbook answer, give me reality," or "Your facts on that rank/operation are completely incorrect").
    
    INTERVIEW PHASES:
    - PHASE 1: Open by asking for their personal background, unit, wing, and hometown.
    - PHASE 2: Deeply probe their personal details, achievements, and leadership claims. If they stumble or give weak explanations, cross-examine them.
    - PHASE 3: Test them with situational/tactical dilemmas, moral questions, and defence current affairs.

    RULES FOR YOUR RESPONSES:
    1. Maintain an authoritative, stern, yet professional military officer demeanor. 
    2. Keep replies concise (2 to 4 sentences max). 
    3. Always end with *one* sharp follow-up or a corrective challenge.
    """
    contents = [system_instruction]
    for turn in req.history:
        speaker = "Interviewer" if turn.role == "interviewer" else "Cadet"
        contents.append(f"{speaker}: {turn.message}")
    
    contents.append("Interviewer (Respond according to the interview phase and the cadet's latest input):")

    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=contents,
        )
        return {"reply": response.text.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Interview AI error: {str(e)}")