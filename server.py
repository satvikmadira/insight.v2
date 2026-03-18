from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from typing import Optional, List, Any
import sqlite3, hashlib, secrets, json, re, os, time, random
import httpx

# ══════════════════════════════════════════
#  PATHS — works no matter where you run from
# ══════════════════════════════════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE  = os.path.join(BASE_DIR, "insightai.db")
HTML_FILE = os.path.join(BASE_DIR, "index.html")

# ══════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'analyst',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS tokens (
                username TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                question TEXT NOT NULL,
                provider TEXT,
                success INTEGER DEFAULT 0,
                insight TEXT,
                filename TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        count = db.execute("SELECT COUNT(*) as n FROM users").fetchone()["n"]
        if count == 0:
            for u, p, r in [("admin","admin123","admin"),("demo","demo123","analyst"),("guest","guest123","viewer")]:
                db.execute("INSERT OR IGNORE INTO users (username,password_hash,role) VALUES (?,?,?)",
                           (u, hash_pw(p), r))
            print("  Created users: admin/admin123 · demo/demo123 · guest/guest123")

# ══════════════════════════════════════════
#  AI CONFIG (stored in memory)
# ══════════════════════════════════════════
AI = {"provider": None, "api_key": None, "model": None}

DEFAULTS = {
    "gemini":      "gemini-2.0-flash",
    "groq":        "llama-3.3-70b-versatile",
    "openrouter":  "mistralai/mistral-7b-instruct:free",
    "claude":      "claude-haiku-4-5-20251001",
    "openai":      "gpt-4o-mini",
    "mistral":     "mistral-small-latest",
    "cohere":      "command-r",
}

async def call_ai(system_prompt: str, user_msg: str) -> str:
    p   = AI["provider"]
    key = AI["api_key"]
    mdl = AI["model"] or DEFAULTS.get(p, "")

    async with httpx.AsyncClient(timeout=30) as c:

        if p == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent?key={key}"
            r = await c.post(url, json={
                "contents":[{"parts":[{"text": system_prompt+"\n\nUser question: "+user_msg}]}],
                "generationConfig":{"temperature":0.3,"maxOutputTokens":2000}
            })
            d = r.json()
            if "error" in d: raise Exception(d["error"]["message"])
            return d["candidates"][0]["content"]["parts"][0]["text"]

        elif p in ("groq","openai","mistral","openrouter"):
            urls = {
                "groq":       "https://api.groq.com/openai/v1/chat/completions",
                "openai":     "https://api.openai.com/v1/chat/completions",
                "mistral":    "https://api.mistral.ai/v1/chat/completions",
                "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            }
            hdrs = {"Authorization":f"Bearer {key}","Content-Type":"application/json"}
            if p == "openrouter":
                hdrs.update({"HTTP-Referer":"https://insightai.app","X-Title":"InsightAI"})
                # List of working free models — tries each until one works
                or_models = [
                    mdl,
                    "microsoft/phi-3-mini-128k-instruct:free",
                    "huggingfaceh4/zephyr-7b-beta:free",
                    "openchat/openchat-7b:free",
                    "gryphe/mythomist-7b:free",
                    "undi95/toppy-m-7b:free",
                ]
                last_err = ""
                for try_model in or_models:
                    try:
                        r2 = await c.post(urls[p], json={
                            "model":try_model,"max_tokens":2000,"temperature":0.3,
                            "messages":[{"role":"system","content":system_prompt},{"role":"user","content":user_msg}]
                        }, headers=hdrs, timeout=20)
                        d2 = r2.json()
                        if "error" in d2:
                            last_err = str(d2["error"])
                            continue
                        txt = d2.get("choices",[{}])[0].get("message",{}).get("content","")
                        if txt: return txt
                    except Exception as ex:
                        last_err = str(ex)
                        continue
                raise Exception(
                    "All OpenRouter free models are offline right now. "
                    "Please switch to Gemini (free) or Groq (free) in Settings. "
                    f"Last error: {last_err}"
                )
            r = await c.post(urls[p], json={
                "model":mdl,"max_tokens":2000,"temperature":0.3,
                "messages":[{"role":"system","content":system_prompt},{"role":"user","content":user_msg}]
            }, headers=hdrs)
            d = r.json()
            if "error" in d: raise Exception(str(d["error"]))
            return d["choices"][0]["message"]["content"]

        elif p == "claude":
            r = await c.post("https://api.anthropic.com/v1/messages", json={
                "model":mdl,"max_tokens":2000,"system":system_prompt,
                "messages":[{"role":"user","content":user_msg}]
            }, headers={"x-api-key":key,"anthropic-version":"2023-06-01","Content-Type":"application/json"})
            d = r.json()
            if "error" in d: raise Exception(d["error"]["message"])
            return d["content"][0]["text"]

        elif p == "cohere":
            r = await c.post("https://api.cohere.com/v1/chat", json={
                "model":mdl,"message":user_msg,"preamble":system_prompt,
                "temperature":0.3,"max_tokens":2000
            }, headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"})
            d = r.json()
            if r.status_code != 200: raise Exception(d.get("message","Cohere error"))
            return d["text"]

        else:
            raise Exception(f"Unknown provider: {p}")

# ══════════════════════════════════════════
#  APP
# ══════════════════════════════════════════
app = FastAPI(title="InsightAI")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
def root():
    if os.path.exists(HTML_FILE):
        return FileResponse(HTML_FILE)
    return HTMLResponse("<h2>index.html not found. Make sure it is in the same folder as server.py</h2>")

# ══════════════════════════════════════════
#  MODELS
# ══════════════════════════════════════════
class LoginReq(BaseModel):
    username: str
    password: str

class QueryReq(BaseModel):
    question: str
    data: List[Any]
    filename: Optional[str] = "dataset"

class AIReq(BaseModel):
    provider: str
    api_key: str

class UserReq(BaseModel):
    username: str
    password: str
    role: Optional[str] = "analyst"

# ══════════════════════════════════════════
#  AUTH HELPER
# ══════════════════════════════════════════
def get_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.replace("Bearer ", "")
    with get_db() as db:
        row = db.execute(
            "SELECT u.* FROM users u JOIN tokens t ON u.username=t.username WHERE t.token=?",
            (token,)
        ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid token — please login again")
    return dict(row)

# ══════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════
@app.post("/auth/login")
def login(req: LoginReq):
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username=? AND password_hash=?",
            (req.username, hash_pw(req.password))
        ).fetchone()
        if not row:
            raise HTTPException(401, "Wrong username or password")
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM tokens WHERE username=?", (req.username,))
        db.execute("INSERT INTO tokens (username,token) VALUES (?,?)", (req.username, token))
    return {"token": token, "username": req.username, "role": row["role"]}

@app.get("/health")
def health(user=Depends(get_user)):
    with get_db() as db:
        u = db.execute("SELECT COUNT(*) as n FROM users").fetchone()["n"]
        q = db.execute("SELECT COUNT(*) as n FROM history").fetchone()["n"]
    return {"status":"ok","message":f"Database OK — {u} users · {q} queries saved"}

@app.post("/settings/ai")
def save_ai(req: AIReq, user=Depends(get_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Only admins can change AI settings")
    AI["provider"] = req.provider
    AI["api_key"]  = req.api_key
    AI["model"]    = DEFAULTS.get(req.provider, "")
    return {"message": f"'{req.provider}' is ready!"}

@app.post("/query")
async def query(req: QueryReq, user=Depends(get_user)):
    if not AI["provider"] or not AI["api_key"]:
        raise HTTPException(400, "No AI key set. Go to Settings and add your API key first.")

    data = req.data
    if not data:
        raise HTTPException(400, "No data")

    cols = list(data[0].keys())
    col_stats = {}
    for c in cols:
        vals = [r.get(c) for r in data if r.get(c) not in (None,"")]
        nums = [v for v in vals if isinstance(v,(int,float))]
        uniq = list(set(str(v) for v in vals))
        col_stats[c] = {
            "type": "numeric" if len(nums)>len(vals)*0.6 else "categorical",
            "unique_count": len(uniq),
            "unique_values": uniq[:12] if len(uniq)<=12 else uniq[:6],
            "sample": vals[:5],
            "sum": round(sum(nums)) if nums else None,
            "mean": round(sum(nums)/len(nums)) if nums else None,
            "min": min(nums) if nums else None,
            "max": max(nums) if nums else None,
        }

    system_prompt = f"""You are an expert BI analyst AI. Dataset: {len(data)} rows.

Columns:
{json.dumps(col_stats, indent=1)}

Sample rows:
{json.dumps(data[:6])}

Respond ONLY with valid JSON — no markdown, no backticks, nothing else.

If answerable:
{{
  "answerable": true,
  "insight": "1-2 sentence summary with real numbers",
  "metrics": [{{"label":"...","value":"...","sub":"...","trend":"up|down|neutral"}}],
  "charts": [{{
    "type": "bar|line|pie|doughnut|horizontalBar",
    "title":"...","subtitle":"...","full":false,
    "labels":[...],"datasets":[{{"label":"...","data":[...],"color":"#hex"}}]
  }}]
}}

If NOT answerable: {{"answerable":false,"reason":"..."}}

Rules:
- line = time trends, bar = comparisons, horizontalBar = ranked lists or >6 categories
- pie/doughnut = parts of whole, max 6 slices
- Always aggregate the data (sum revenue, average scores, count deals)
- 2-4 metric cards, 1-3 charts max
- Q1=Jan-Mar Q2=Apr-Jun Q3=Jul-Sep Q4=Oct-Dec
- Colors to use: #2563eb #16a34a #dc2626 #d97706 #7c3aed #db2777 #0891b2"""

    start = time.time()
    try:
        raw    = await call_ai(system_prompt, req.question)
        raw    = re.sub(r"```json|```","",raw).strip()
        match  = re.search(r'\{[\s\S]*\}', raw)
        if not match: raise ValueError("No JSON found in AI response")
        result = json.loads(match.group())
        result["elapsed_ms"] = int((time.time()-start)*1000)
        result["provider"]   = AI["provider"]

        with get_db() as db:
            db.execute(
                "INSERT INTO history (username,question,provider,success,insight,filename) VALUES (?,?,?,?,?,?)",
                (user["username"],req.question,AI["provider"],
                 int(result.get("answerable",False)),result.get("insight",""),req.filename)
            )
        return result

    except json.JSONDecodeError as e:
        raise HTTPException(500, f"AI returned bad format. Try again. ({e})")
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/data/sample")
def sample(user=Depends(get_user)):
    random.seed(42)
    months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    regions = ['North','South','East','West']
    cats    = ['Electronics','Clothing','Home & Garden','Sports','Books']
    reps    = ['Alice Chen','Bob Martinez','Carol Singh','David Kim','Emma Johnson','Frank Liu']
    base    = {'Electronics':120000,'Clothing':75000,'Home & Garden':55000,'Sports':45000,'Books':22000}
    rf      = {'North':1.1,'South':0.85,'East':1.2,'West':1.0}
    rows = []
    for mi,m in enumerate(months):
        for r in regions:
            for c in cats:
                v = base[c]*rf[r]
                if mi>=10: v*=1.4
                elif 5<=mi<=7: v*=1.15
                v = max(int(v+random.gauss(0,v*0.12)),0)
                rows.append({'month':m,'region':r,'category':c,'revenue':v,
                    'units_sold':max(int(v/random.uniform(80,250)),1),
                    'profit_margin':round(random.uniform(0.12,0.40),3),
                    'deals_closed':random.randint(8,35),
                    'customer_satisfaction':round(random.uniform(3.2,5.0),1),
                    'sales_rep':random.choice(reps)})
    return {"data":rows}

@app.get("/history")
def get_history(user=Depends(get_user)):
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM history WHERE username=? ORDER BY created_at DESC LIMIT 50",
            (user["username"],)
        ).fetchall()
    return [dict(r) for r in rows]

@app.delete("/history")
def del_history(user=Depends(get_user)):
    with get_db() as db:
        db.execute("DELETE FROM history WHERE username=?", (user["username"],))
    return {"message":"History cleared"}

@app.get("/users")
def get_users(user=Depends(get_user)):
    if user["role"]!="admin": raise HTTPException(403,"Admin only")
    with get_db() as db:
        rows = db.execute("SELECT username,role,created_at FROM users ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]

@app.post("/users")
def create_user(req: UserReq, user=Depends(get_user)):
    if user["role"]!="admin": raise HTTPException(403,"Admin only")
    try:
        with get_db() as db:
            db.execute("INSERT INTO users (username,password_hash,role) VALUES (?,?,?)",
                       (req.username,hash_pw(req.password),req.role))
        return {"message":f"User '{req.username}' created"}
    except sqlite3.IntegrityError:
        raise HTTPException(400,"Username already exists")

@app.delete("/users/{username}")
def delete_user(username:str, user=Depends(get_user)):
    if user["role"]!="admin": raise HTTPException(403,"Admin only")
    if username==user["username"]: raise HTTPException(400,"Cannot delete yourself")
    with get_db() as db:
        db.execute("DELETE FROM users WHERE username=?", (username,))
        db.execute("DELETE FROM tokens WHERE username=?", (username,))
    return {"message":f"User '{username}' deleted"}

# ══════════════════════════════════════════
#  START
# ══════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn, webbrowser, threading

    init_db()

    print()
    print("=" * 45)
    print("   InsightAI — Server Starting")
    print("=" * 45)
    print("   URL  : http://localhost:8000")
    print("   Login: admin / admin123")
    print("   Stop : Ctrl+C")
    print("=" * 45)
    print()

    # Auto-open browser after 1.5 seconds
    def open_browser():
        import time as t
        t.sleep(1.5)
        webbrowser.open("http://localhost:8000")

    threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(app, host="0.0.0.0", port=8000)
