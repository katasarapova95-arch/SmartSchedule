from pathlib import Path
import json, os, threading
from datetime import datetime, timezone
from sqlalchemy import create_engine, text

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DATA_DIR.mkdir(exist_ok=True)
LOCAL_JSON = DATA_DIR / "school.json"
LOCAL_REV = DATA_DIR / "revision.txt"
LOCK = threading.RLock()

DEFAULT = {
    "school": {"name":"Демонстрационная школа", "city":"", "year":"2026/2027",
               "days":["Понедельник","Вторник","Среда","Четверг","Пятница"],
               "shifts":{"1":{"enabled":True,"start":"08:30","lesson":45,"breaks":[10]*12},
                         "2":{"enabled":True,"start":"13:30","lesson":45,"breaks":[10]*12}}},
    "classes":[], "teachers":[], "subjects":[], "rooms":[], "subgroups":[], "extras":[],
    "rules":{"class_max":7,"teacher_max":6,"no_same_subject":True,"balance":True,"avoid_hard_chain":True,
             "min_windows":True,"max_hard_per_day":2,"max_hard_chain":2,"max_load_points":12},
    "schedule":{"1":{},"2":{}}, "ai_history":[]
}

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_CENTRAL_DB = bool(DATABASE_URL)
url = DATABASE_URL
if url.startswith("postgres://"): url = "postgresql+psycopg://" + url[len("postgres://"):]
elif url.startswith("postgresql://"): url = "postgresql+psycopg://" + url[len("postgresql://"):]
engine = create_engine(url, pool_pre_ping=True, future=True) if USE_CENTRAL_DB else None

def clone_default(): return json.loads(json.dumps(DEFAULT, ensure_ascii=False))

def _local_seed():
    if LOCAL_JSON.exists():
        try: return json.loads(LOCAL_JSON.read_text(encoding="utf-8"))
        except Exception: pass
    return clone_default()

def normalize(data):
    data = data if isinstance(data,dict) else clone_default()
    data.setdefault("school", clone_default()["school"])
    data.setdefault("classes",[]); data.setdefault("teachers",[]); data.setdefault("subjects",[]); data.setdefault("rooms",[])
    data.setdefault("subgroups",[]); data.setdefault("extras",[]); data.setdefault("schedule",{"1":{},"2":{}}); data.setdefault("ai_history",[])
    data.setdefault("rules",clone_default()["rules"])
    for k,v in DEFAULT["rules"].items(): data["rules"].setdefault(k,v)
    data["school"].setdefault("days",DEFAULT["school"]["days"])
    data["school"].setdefault("shifts",{})
    for sh in ("1","2"):
        data["school"]["shifts"].setdefault(sh,json.loads(json.dumps(DEFAULT["school"]["shifts"][sh],ensure_ascii=False)))
        data["school"]["shifts"][sh].setdefault("breaks",[10]*12)
    difficulty_map={"easy":1,"light":1,"легкий":1,"лёгкий":1,"medium":2,"normal":2,"средний":2,"hard":3,"сложный":3}
    for sub in data.get("subjects",[]):
        raw=sub.get("difficulty",2)
        if isinstance(raw,str):
            try: sub["difficulty"]=max(1,min(3,int(raw)))
            except Exception: sub["difficulty"]=difficulty_map.get(raw.strip().lower(),2)
        else:
            try: sub["difficulty"]=max(1,min(3,int(raw)))
            except Exception: sub["difficulty"]=2
    return data

def _local_load(): return normalize(_local_seed())
def _local_revision():
    try: return int(LOCAL_REV.read_text(encoding="ascii").strip())
    except Exception: return 1

def _local_save(data):
    data=normalize(data)
    tmp=LOCAL_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(LOCAL_JSON)
    rev=_local_revision()+1
    LOCAL_REV.write_text(str(rev),encoding="ascii")
    return rev

def init_db():
    if not USE_CENTRAL_DB: return
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS school_state (id INTEGER PRIMARY KEY,payload TEXT NOT NULL,revision BIGINT NOT NULL,updated_at TEXT NOT NULL)"))
        if conn.execute(text("SELECT id FROM school_state WHERE id=1")).first() is None:
            conn.execute(text("INSERT INTO school_state(id,payload,revision,updated_at) VALUES (1,:p,1,:u)"),
                         {"p":json.dumps(normalize(_local_seed()),ensure_ascii=False),"u":datetime.now(timezone.utc).isoformat()})

def load_with_revision():
    with LOCK:
        if not USE_CENTRAL_DB:
            return _local_load(), _local_revision(), "local"
        init_db()
        with engine.connect() as conn:
            row=conn.execute(text("SELECT payload,revision,updated_at FROM school_state WHERE id=1")).first()
            if not row: raise RuntimeError("Центральная база SmartSchedule не инициализирована")
            return normalize(json.loads(row[0])),int(row[1]),row[2]

def load(): return load_with_revision()[0]

def save(data):
    with LOCK:
        if not USE_CENTRAL_DB: return _local_save(data)
        init_db(); payload=json.dumps(normalize(data),ensure_ascii=False)
        with engine.begin() as conn:
            row=conn.execute(text("SELECT revision FROM school_state WHERE id=1")).first(); rev=int(row[0])+1 if row else 1
            conn.execute(text("UPDATE school_state SET payload=:p,revision=:r,updated_at=:u WHERE id=1"),
                         {"p":payload,"r":rev,"u":datetime.now(timezone.utc).isoformat()})
        try:
            LOCAL_JSON.with_suffix('.tmp').write_text(payload,encoding='utf-8'); LOCAL_JSON.with_suffix('.tmp').replace(LOCAL_JSON)
        except Exception: pass
        return rev

def revision(): return load_with_revision()[1]
