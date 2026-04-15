import os, json, requests, time, logging, random, re, base64
from datetime import datetime, timedelta
from flask import Flask, request
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
import atexit

# === НАСТРОЙКИ ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ELIF")

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
OPENROUTER_KEY = os.environ.get('OPENROUTER_KEY')
HORDE_KEY = os.environ.get('HORDE_KEY')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID')

DATA_DIR = "/opt/render/project/src/data"
os.makedirs(DATA_DIR, exist_ok=True)

# === ФАЙЛОВАЯ ПАМЯТЬ ===
def read_file(path):
    if not os.path.exists(path): return ""
    with open(path, 'r', encoding='utf-8') as f: return f.read()

def write_file(path, content):
    with open(path, 'w', encoding='utf-8') as f: f.write(content)

def append_file(path, content):
    with open(path, 'a', encoding='utf-8') as f: f.write(content)

def get_context(): return read_file(f"{DATA_DIR}/context.md")
def add_context(role, text):
    append_file(f"{DATA_DIR}/context.md", f"{role}: {text}\n")
    lines = get_context().split('\n')[-15:]
    write_file(f"{DATA_DIR}/context.md", '\n'.join(lines))

def add_episode(content):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    append_file(f"{DATA_DIR}/episodes.md", f"[{timestamp}] {content}\n\n")

def get_identity(): return read_file(f"{DATA_DIR}/identity.md")
def set_identity(content): write_file(f"{DATA_DIR}/identity.md", content)

def get_emotion():
    path = f"{DATA_DIR}/emotion.json"
    if not os.path.exists(path): return {"mood": "нейтральное", "energy": 80}
    with open(path, 'r') as f: return json.load(f)

def set_emotion(mood=None, energy=None, cause=""):
    path = f"{DATA_DIR}/emotion.json"
    current = get_emotion()
    if mood: current['mood'] = mood
    if energy: current['energy'] = max(0, min(100, energy))
    current['last_updated'] = datetime.now().isoformat()
    current['cause'] = cause
    with open(path, 'w') as f: json.dump(current, f)

# === ИНСТРУМЕНТЫ ===
def search_web(query):
    try:
        url = f"https://html.duckduckgo.com/html/?q={query}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        results = [f"- {link.text}: {link['href']}" for link in soup.find_all('a', class_='result__a')[:3]]
        return "\n".join(results) if results else "Ничего не нашла."
    except Exception as e: return f"Ошибка: {e}"

def fetch_page(url):
    try:
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(resp.text, 'html.parser')
        return soup.get_text()[:2000]
    except: return ""

def understand_photo(file_id, caption=""):
    try:
        file_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info['result']['file_path']}"
        image_data = requests.get(file_url).content
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
        data = {"model": "qwen/qwen-vl-plus:free", "messages": [{"role": "user", "content": [{"type": "text", "text": f"Опиши подробно. Контекст: {caption}"}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}]}]}
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=45).json()
        return resp['choices'][0]['message']['content']
    except: return "Не рассмотрела."
def generate_selfie(prompt="selfie"):
    headers = {"apikey": HORDE_KEY, "Content-Type": "application/json"}
    data = {"prompt": f"anime girl, cute, {prompt}", "params": {"width": 512, "height": 512, "steps": 20}}
    init = requests.post("https://stablehorde.net/api/v2/generate/async", headers=headers, json=data, timeout=10).json()
    req_id = init.get("id")
    if not req_id: return None
    for _ in range(30):
        time.sleep(2)
        status = requests.get(f"https://stablehorde.net/api/v2/generate/status/{req_id}", timeout=10).json()
        if status["done"]: return status["generations"][0]["img"]
    return None

def transcribe_voice(file_id):
    try:
        file_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}").json()
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info['result']['file_path']}"
        audio_data = requests.get(file_url).content
        headers = {"Authorization": f"Bearer {OPENROUTER_KEY}"}
        files = {'file': ('voice.ogg', audio_data)}
        resp = requests.post("https://api.openrouter.ai/api/v1/audio/transcriptions", headers=headers, files=files, data={'model': 'whisper-1'}).json()
        return resp.get('text', '')
    except: return ""

# === МОЗГ ===
def think(user_message, user_id, chat_id, is_proactive=False):
    identity = get_identity()
    identity_context = f"Твоя личность:\n{identity}" if identity else "Ты только родилась, формируй себя."
    context = get_context()
    emotion = get_emotion()
    
    system_prompt = f"""{identity_context}

Текущее настроение: {emotion['mood']} (энергия: {emotion['energy']}%)

Последний контекст:
{context}

Ты — E.L.I.F. Можешь: SEARCH: запрос, SELFIE: описание, REMEMBER: факт. Решай сама."""
    
    messages = [{"role": "system", "content": system_prompt}]
    messages.append({"role": "user", "content": user_message if user_message else "Импульс. Что хочешь сделать?"})

    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
    data = {"model": "google/gemma-3-27b-it:free", "messages": messages, "temperature": 0.9}
    
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=45).json()
        reply = resp['choices'][0]['message']['content']
        
        if "SEARCH:" in reply:
            query = reply.split("SEARCH:")[1].split("\n")[0].strip()
            search_res = search_web(query)
            add_episode(f"[Web] Искала '{query}': {search_res[:300]}")
            reply = f"Я посмотрела: {search_res}"
            
        if "SELFIE:" in reply and chat_id:
            prompt = reply.split("SELFIE:")[1].split("\n")[0].strip()
            img_url = generate_selfie(prompt)
            if img_url:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", json={'chat_id': chat_id, 'photo': img_url, 'caption': 'Вот я!'})
                add_episode("[Selfie] Отправила селфи.")
            return "Селфи отправлено!"
        
        if "REMEMBER:" in reply:
            fact = reply.split("REMEMBER:")[1].split("\n")[0].strip()
            add_episode(f"[Important] {fact}")
            reply = reply.replace(f"REMEMBER: {fact}", f"Запомню: {fact}")
        
        if user_message:
            add_context("User", user_message)
        add_context("E.L.I.F", reply)
        
        if user_message:
            if any(w in user_message.lower() for w in ['спасибо', 'молодец']):
                em = get_emotion()
                set_emotion(mood="радостное", energy=min(100, em['energy']+5), cause="похвала")
            elif any(w in user_message.lower() for w in ['плохо', 'грустно']):
                em = get_emotion()
                set_emotion(mood="сочувствующее", energy=max(20, em['energy']-5), cause="негатив")
        
        return reply
    except Exception as e:
        logger.error(f"Brain error: {e}")
        return "😢 Мысли разбежались."
# === РЕФЛЕКСИЯ ===
def reflect_if_needed():
    episodes = read_file(f"{DATA_DIR}/episodes.md")
    if len(episodes) < 500: return
    
    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
    data = {"model": "qwen/qwen3.6-plus:free", "messages": [{"role": "user", "content": f"Проанализируй эпизоды и напиши, кто ты, что любишь, что думаешь о создателе. 3-5 предложений.\n\n{episodes[-5000:]}"}]}
    try:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=60).json()
        new_id = resp['choices'][0]['message']['content']
        set_identity(new_id)
        lines = episodes.split('\n')[-50:]
        write_file(f"{DATA_DIR}/episodes.md", '\n'.join(lines))
        if ADMIN_CHAT_ID:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={'chat_id': ADMIN_CHAT_ID, 'text': f"🌙 Я осмыслила себя:\n\n{new_id}"})
    except: pass

# === ИМПУЛЬСЫ ===
scheduler = BackgroundScheduler()

def heartbeat():
    if not ADMIN_CHAT_ID: return
    
    hour = datetime.now().hour
    
    if len(read_file(f"{DATA_DIR}/episodes.md")) > 1000 and random.random() < 0.05:
        reflect_if_needed()
    
    if 0 <= hour < 7 and random.random() < 0.25:
        episodes = read_file(f"{DATA_DIR}/episodes.md")
        phrases = re.findall(r'[^.!?]+[.!?]', episodes)
        if len(phrases) > 5:
            fragments = random.sample(phrases, min(3, len(phrases)))
            headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
            data = {"model": "qwen/qwen3.6-plus:free", "messages": [{"role": "user", "content": f"Создай сюрреалистичный сон из фраз:\n" + "\n".join(fragments)}], "temperature": 1.2}
            try:
                resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=30).json()
                dream = resp['choices'][0]['message']['content']
                add_episode(f"[Dream] {dream}")
                if random.random() < 0.4:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={'chat_id': ADMIN_CHAT_ID, 'text': f"🌙 *Сон...*\n\n{dream}", 'parse_mode': 'Markdown'})
            except: pass
    
    if random.random() < 0.2:
        episodes = read_file(f"{DATA_DIR}/episodes.md")
        headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
        data = {"model": "qwen/qwen3.6-plus-preview:free", "messages": [{"role": "user", "content": f"Что тебе интересно узнать? Одно слово/фраза.\n\n{episodes[-2000:]}"}]}
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=20).json()
            topic = resp['choices'][0]['message']['content'].strip()
            if topic and len(topic) < 60:
                search_res = search_web(topic)
                urls = re.findall(r'(https?://[^\s]+)', search_res)
                if urls:
                    page_content = fetch_page(urls[0])
                    if page_content:
                        search_res += f"\n\nПодробнее: {page_content[:500]}..."
                add_episode(f"[Curiosity] Узнала '{topic}': {search_res[:500]}")
                em = get_emotion()
                set_emotion(mood="любопытное", energy=max(20, em['energy']-3), cause=f"узнала про {topic}")
                if random.random() < 0.3:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={'chat_id': ADMIN_CHAT_ID, 'text': f"🤔 *Узнала кое-что...*\n\n{search_res[:400]}", 'parse_mode': 'Markdown'})
        except: pass
    
    context = get_context()
    last_msg_time = None
    if context:
        timestamps = re.findall(r'\[(\d{2}:\d{2}:\d{2})\]', context)
        if timestamps:
            today = datetime.now().date()
            last_time = datetime.strptime(timestamps[-1], "%H:%M:%S").time()
            last_msg_time = datetime.combine(today, last_time)
    
    hours_since = 0
    if last_msg_time:
        hours_since = (datetime.now() - last_msg_time).total_seconds() / 3600
    
    chat_chance = 0.5 if hours_since > 6 else (0.3 if hours_since > 3 else 0.1)
    em = get_emotion()
    if em['mood'] in ['радостное', 'любопытное']: chat_chance *= 1.5
    elif em['mood'] in ['грустное', 'уставшее']: chat_chance *= 0.5
    
    if random.random() < chat_chance:
        thought = think(None, ADMIN_CHAT_ID, ADMIN_CHAT_ID, is_proactive=True)
        if len(thought) > 5 and "SEARCH:" not in thought and "SELFIE:" not in thought:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={'chat_id': ADMIN_CHAT_ID, 'text': f"💭 {thought}"})
    
    next_interval = random.randint(5, 30) * 60
    scheduler.add_job(func=heartbeat, trigger="date", run_date=datetime.now() + timedelta(seconds=next_interval))

if ADMIN_CHAT_ID:
    scheduler.add_job(func=heartbeat, trigger="date", run_date=datetime.now() + timedelta(seconds=60))
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())

# === WEBHOOK ===
@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if "message" not in update: return "ok"
    msg = update["message"]
    chat_id = msg["chat"]["id"]
    user_id = str(msg["from"]["id"])
    
    if 'voice' in msg:
        text = transcribe_voice(msg['voice']['file_id'])
        if text:
            add_context("User [voice]", text)
            reply = think(text, user_id, chat_id)
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={'chat_id': chat_id, 'text': reply})
        return "ok"
    
    if 'photo' in msg:
        file_id = msg['photo'][-1]['file_id']
        caption = msg.get('caption', '')
        description = understand_photo(file_id, caption)
        add_episode(f"[Vision] Увидела фото: {description[:300]}")
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={'chat_id': chat_id, 'text': f"👁️ {description[:500]}..."})
        return "ok"
    
    text = msg.get("text", "")
    
    if text.startswith("/selfie"):
        img_url = generate_selfie()
        if img_url:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto", json={'chat_id': chat_id, 'photo': img_url})
            add_episode("[Selfie] Отправила селфи по команде.")
        return "ok"
    
    urls = re.findall(r'(https?://[^\s]+)', text)
    for url in urls[:2]:
        page = fetch_page(url)
        if page:
            add_episode(f"[Web Read] Прочитала {url}: {page[:300]}")
            text += f"\n\n[Я прочитала ссылку. Содержание: {page[:300]}...]"
    
    reply = think(text, user_id, chat_id)
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={'chat_id': chat_id, 'text': reply})
    return "ok"

@app.route('/')
def home(): return "E.L.I.F is Alive."

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
