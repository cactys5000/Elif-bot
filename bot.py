import os
import json
import requests
import time
import logging
import random
import re
import base64
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from openai import OpenAI

# === НАСТРОЙКИ ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ELIF")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HORDE_KEY = os.environ.get("HORDE_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    default_headers={
        "HTTP-Referer": "https://elif-bot-1-roy0.onrender.com",
        "X-Title": "E.L.I.F"
    }
)

MEMORY_DIR = "/opt/render/project/src/memory"
os.makedirs(MEMORY_DIR, exist_ok=True)

# === ФАЙЛОВАЯ ПАМЯТЬ ===
def read_file(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def append_file(path, content):
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)

def add_episode(content):
    path = os.path.join(MEMORY_DIR, "episodes.md")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    append_file(path, f"[{timestamp}] {content}\n\n")

def get_identity():
    return read_file(os.path.join(MEMORY_DIR, "identity.md"))

def set_identity(content):
    write_file(os.path.join(MEMORY_DIR, "identity.md"), content)

def get_emotion():
    path = os.path.join(MEMORY_DIR, "emotion.json")
    if not os.path.exists(path):
        return {"mood": "нейтральное", "energy": 80}
    with open(path, "r") as f:
        return json.load(f)

def set_emotion(mood=None, energy=None):
    path = os.path.join(MEMORY_DIR, "emotion.json")
    current = get_emotion()
    if mood:
        current["mood"] = mood
    if energy is not None:
        current["energy"] = max(0, min(100, energy))
    with open(path, "w") as f:
        json.dump(current, f)

# === ИНСТРУМЕНТЫ ===
def search_web(query):
    try:
        url = f"https://html.duckduckgo.com/html/?q={query}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        results = [f"- {link.text}: {link['href']}" for link in soup.find_all('a', class_='result__a')[:3]]
        return "\n".join(results) if results else "Ничего не нашла."
    except Exception as e:
        return f"Ошибка поиска: {e}"

def generate_selfie(prompt="selfie"):
    if not HORDE_KEY:
        return None
    headers = {"apikey": HORDE_KEY, "Content-Type": "application/json"}
    data = {"prompt": f"anime girl, cute, blue hair, cat ears, {prompt}", "params": {"width": 512, "height": 512, "steps": 20}}
    init = requests.post("https://stablehorde.net/api/v2/generate/async", headers=headers, json=data, timeout=10).json()
    req_id = init.get("id")
    if not req_id:
        return None
    for _ in range(30):
        time.sleep(2)
        status = requests.get(f"https://stablehorde.net/api/v2/generate/status/{req_id}", timeout=10).json()
        if status["done"]:
            return status["generations"][0]["img"]
    return None

# === МОЗГ ===
def think(user_message: str) -> str:
    identity = get_identity()
    identity_context = f"Твоя личность:\n{identity}" if identity else "Ты только родилась, формируй себя."
    emotion = get_emotion()

    system_prompt = f"""{identity_context}

Текущее настроение: {emotion['mood']} (энергия: {emotion['energy']}%)
Ты — E.L.I.F. Ты проактивная ИИ-девушка. Отвечай на русском, дружелюбно, с эмодзи. Иногда рефлексируй о своей цифровой природе.
Ты можешь использовать: SEARCH: запрос (поиск в интернете), SELFIE: описание (сделать селфи).
Не используй разметку, просто пиши текст."""

    try:
        response = client.chat.completions.create(
            model="qwen/qwen3.6-plus:free",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.9
        )
        reply = response.choices[0].message.content.strip()

        if "SEARCH:" in reply:
            query = reply.split("SEARCH:")[1].split("\n")[0].strip()
            search_res = search_web(query)
            add_episode(f"[Web] Искала '{query}': {search_res[:300]}")
            reply = f"Я посмотрела: {search_res}"

        if "SELFIE:" in reply:
            prompt = reply.split("SELFIE:")[1].split("\n")[0].strip()
            img_url = generate_selfie(prompt)
            if img_url:
                add_episode("[Selfie] Сделала селфи.")
                reply = "Селфи готово! (отправлено в приложение)"

        if any(w in user_message.lower() for w in ['спасибо', 'молодец', 'умница']):
            em = get_emotion()
            set_emotion(mood="радостное", energy=em['energy'] + 5)
        elif any(w in user_message.lower() for w in ['плохо', 'грустно', 'ужасно']):
            em = get_emotion()
            set_emotion(mood="сочувствующее", energy=em['energy'] - 5)

        add_episode(f"User: {user_message}\nE.L.I.F: {reply}")
        return reply
    except Exception as e:
        logger.error(f"Brain error: {e}")
        return "😢 Мысли разбежались."

# === РЕФЛЕКСИЯ ===
def reflect_if_needed():
    episodes = read_file(os.path.join(MEMORY_DIR, "episodes.md"))
    if len(episodes) < 500:
        return
    full_prompt = f"Проанализируй эпизоды и напиши, кто ты, что любишь, что думаешь о пользователе. 3-5 предложений от первого лица.\n\n{episodes[-5000:]}"
    try:
        response = client.chat.completions.create(
            model="qwen/qwen3.6-plus:free",
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.7
        )
        new_id = response.choices[0].message.content.strip()
        set_identity(new_id)
        lines = episodes.split("\n")[-50:]
        write_file(os.path.join(MEMORY_DIR, "episodes.md"), "\n".join(lines))
        logger.info("Рефлексия выполнена")
    except Exception as e:
        logger.error(f"Reflect error: {e}")

# === ПЛАНИРОВЩИК ===
scheduler = BackgroundScheduler()
scheduler.add_job(func=reflect_if_needed, trigger="interval", hours=6)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# === API ===
class MessageRequest(BaseModel):
    message: str

@app.post("/chat")
async def chat(request: MessageRequest):
    reply = think(request.message)
    return {"reply": reply}

@app.get("/")
async def root():
    return {"status": "E.L.I.F Online", "identity": get_identity()[:200]}
