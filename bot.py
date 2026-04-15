import os
import json
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

MEMORY_DIR = os.path.join(os.path.dirname(file), "memory")
os.makedirs(MEMORY_DIR, exist_ok=True)

def read_file(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def append_file(path, content):
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)

def add_episode(content):
    path = os.path.join(MEMORY_DIR, "episodes.md")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    append_file(path, f"[{timestamp}] {content}\n\n")

async def think(user_message: str) -> str:
    identity = read_file(os.path.join(MEMORY_DIR, "identity.md"))
    identity_context = f"Твоя личность:\n{identity}" if identity else "Ты только родилась."

    system_prompt = f"""{identity_context}

Ты — E.L.I.F. Проактивная ИИ-девушка. Отвечай на русском, дружелюбно, с эмодзи. Не используй разметку."""
    
    full_prompt = f"{system_prompt}\n\nПользователь: {user_message}\nE.L.I.F:"

    try:
        response = model.generate_content(full_prompt)
        reply = response.text.strip()
        add_episode(f"User: {user_message}\nE.L.I.F: {reply}")
        return reply
    except Exception as e:
        return "😢 Мысли разбежались."

class MessageRequest(BaseModel):
    message: str

@app.post("/chat")
async def chat(request: MessageRequest):
    reply = await think(request.message)
    return {"reply": reply}

@app.get("/")
async def root():
    return {"status": "E.L.I.F Online"}
