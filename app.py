from flask import Flask, render_template, request, redirect, url_for, session, send_file, has_request_context
from pathlib import Path
from datetime import datetime, timedelta
import json, io, socket, threading, webbrowser, urllib.request, os

BASE = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE/"templates"), static_folder=str(BASE/"static"))
app.secret_key = os.environ.get("SMARTSCHEDULE_SECRET", "change-this-secret")
# Built-in access password for the schedule composer. Change via COMPOSER_PASSWORD in production if needed.
COMPOSER_PASSWORD = os.environ.get("COMPOSER_PASSWORD", "SmartSchedule2026!")
app.config["TEMPLATES_AUTO_RELOAD"] = True
from database import load, save, load_with_revision, revision

DAYS=["Понедельник","Вторник","Среда","Четверг","Пятница","Суббота"]

@app.before_request
def access_guard():
    public={"index","login","logout","static","health","api_revision"}
    if request.endpoint in public or request.path.startswith("/static/"):
        return None
    if not require_role():
        return redirect(url_for("login"))
    role=current_role()
    if role=="teacher":
        allowed_get={"my_day","schedule","check","help","set_shift","logout","api_revision","static"}
        allowed_write={"teacher_room"}
        if request.method in ("POST","PUT","PATCH","DELETE") and request.endpoint not in allowed_write:
            return "Учителю доступен только просмотр и изменение кабинета своих уроков.",403
        if request.method=="GET" and request.endpoint not in allowed_get:
            return "Учителю доступен только личный кабинет, расписание и проверка.",403
    elif role=="student":
        allowed_get={"student_view","help","set_shift","logout","api_revision","static"}
        if request.method in ("POST","PUT","PATCH","DELETE") or request.endpoint not in allowed_get:
            return "Ученику доступен только просмотр расписания своего класса.",403
    return None

def current_shift():
    # The shift is shared by the current browser session, but an explicit
    # ?shift=1/2 in any page URL is authoritative. This makes the switcher
    # work consistently for the composer, teachers and students, including
    # direct links/bookmarks to a particular shift.
    if has_request_context():
        requested=request.args.get("shift")
        if requested in ("1","2"):
            session["shift"]=requested
    s=session.get("shift","1")
    return s if s in ("1","2") else "1"

def current_role():
    role=session.get("role")
    return role if role in ("composer","teacher","student") else None

def current_teacher():
    return session.get("teacher_name", "") if current_role()=="teacher" else ""

def teacher_can_edit():
    return current_role()=="teacher"

def require_role():
    return current_role() is not None

def safe_int(value, default=0, minimum=None, maximum=None):
    try: n=int(value)
    except: n=default
    if minimum is not None: n=max(minimum,n)
    if maximum is not None: n=min(maximum,n)
    return n

NAV=[
("Главная","/home","⌂"),("Школа","/school","▣"),("Классы","/classes","▦"),("Учителя","/teachers","◉"),
("Предметы","/subjects","◇"),("Кабинеты","/rooms","▤"),("Подгруппы","/subgroups","◎"),("Дополнительные","/extras","＋"),
("Правила","/rules","⚙"),("Расписание","/schedule","▥"),("Проверка","/check","✓"),("AI-чат","/ai","✦"),
("Документы","/documents","□"),("Помощь","/help","?")]

@app.template_filter("room_short")
def room_short(value):
    """Display common room names in compact timetable form."""
    text=str(value or "").strip()
    replacements={
        "Спортивный зал":"с/з",
        "спортивный зал":"с/з",
        "Спортзал":"с/з",
        "спортзал":"с/з",
    }
    return replacements.get(text,text)

@app.context_processor
def enum_helper(): return {"enumerate": enumerate}

@app.context_processor
def inject():
    return {"active_shift":current_shift(),"nav":NAV,"role":current_role(),"teacher_name":current_teacher()}

@app.route("/login", methods=["GET","POST"])
def login():
    d=load()
    error=""
    if request.method=="POST":
        role=request.form.get("role","")
        if role=="composer":
            password=request.form.get("password","")
            if password==COMPOSER_PASSWORD:
                session["role"]="composer"
                session.pop("teacher_name",None); session.pop("student_class",None)
                return redirect(url_for("home",shift=current_shift()))
            error="Неверный пароль составителя расписания."
        elif role=="teacher":
            name=request.form.get("teacher_name","").strip()
            teachers=[str(x.get("name","")).strip() for x in d.get("teachers",[]) if str(x.get("name","")).strip()]
            if name and name in teachers:
                session["role"]="teacher"; session["teacher_name"]=name; session.pop("student_class",None)
                return redirect(url_for("my_day",shift=current_shift()))
            error="Выберите свою фамилию из списка учителей."
        elif role=="student":
            class_name=request.form.get("student_class","").strip()
            classes=[str(x.get("name","")).strip() for x in d.get("classes",[]) if str(x.get("name","")).strip()]
            if class_name and class_name in classes:
                session["role"]="student"; session["student_class"]=class_name; session.pop("teacher_name",None)
                return redirect(url_for("student_view",shift=current_shift()))
            error="Выберите свой класс."
    teachers=sorted({str(x.get("name","")).strip() for x in d.get("teachers",[]) if str(x.get("name","")).strip()})
    classes=sorted({str(x.get("name","")).strip() for x in d.get("classes",[]) if str(x.get("name","")).strip()})
    return render_template("login.html",title="Вход в систему",teachers=teachers,classes=classes,error=error)

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.get("/")
def index():
    return redirect(url_for("home",shift=current_shift()))

@app.get("/home")
def home():
    # One atomic read avoids a second DB connection for revision() and prevents
    # transient 500 errors while another device is saving the shared state.
    d, db_revision, _updated_at = load_with_revision()
    s=current_shift();r=d.get("schedule",{}).get(s,{}) or {}
    grid=r.get("grid",{}) if isinstance(r,dict) else {}
    filled=sum(bool(v) for day in grid.values() if isinstance(day,dict) for v in day.values())
    counts={k:len(d.get(k,[])) for k in ["classes","teachers","subjects","rooms","subgroups","extras"]}
    return render_template("home.html",title="Главная",d=d,counts=counts,filled=filled,db_revision=db_revision)

@app.get("/set-shift/<s>")
def set_shift(s):
    # Store the selected shift in the session for every role. Then redirect
    # back to the page the user was viewing, now carrying the new shift.
    selected=s if s in ("1","2") else "1"
    session["shift"]=selected
    next_url=request.args.get("next") or url_for("home")
    # Never preserve an old shift query parameter from the previous page.
    try:
        from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
        parts=urlsplit(next_url)
        query=[(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if k!="shift"]
        query.append(("shift",selected))
        next_url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),parts.fragment))
    except Exception:
        next_url=f"{next_url}{'&' if '?' in next_url else '?'}shift={selected}"
    return redirect(next_url)

@app.route("/school",methods=["GET","POST"])
def school():
    d=load();s=current_shift()
    if request.method=="POST":
        sc=d["school"]
        sc["name"]=request.form.get("name","Моя школа").strip() or "Моя школа"
        sc["city"]=request.form.get("city","").strip()
        sc["year"]=request.form.get("year","2026/2027").strip()
        sc["days"]=[x for x in DAYS if request.form.get("day_"+x)=="on"] or DAYS[:5]
        for sh in ("1","2"):
            cfg=sc["shifts"][sh]
            cfg["enabled"]=request.form.get(f"enabled_{sh}")=="on"
            cfg["start"]=request.form.get(f"start_{sh}",cfg.get("start","08:30"))
            cfg["lesson"]=safe_int(request.form.get(f"lesson_{sh}",cfg.get("lesson",45)),45,30,120)
            # Перемена после каждого урока. 0 = без перемены.
            cfg["breaks"]=[safe_int(request.form.get(f"break_{sh}_{i}",0),0,0,60) for i in range(1,13)]
        
        for _sh in ("1","2"):
            if d.get("schedule",{}).get(_sh): d["schedule"][_sh]["stale"]=True
        save(d);return redirect(url_for("school",shift=s))
    return render_template("school.html",title="Школа",d=d)

@app.route("/classes",methods=["GET","POST"])
def classes():
    d=load();s=current_shift()
    if request.method=="POST":
        d["classes"].append({
            "id":f"class_{datetime.now().strftime('%H%M%S%f')}",
            "name":request.form.get("name","").strip(),
            "parallel":request.form.get("parallel","").strip(),
            "students":safe_int(request.form.get("students","0"),0,0,100),
            "shift":request.form.get("class_shift",s) if request.form.get("class_shift",s) in ("1","2") else s,
            "max_day":safe_int(request.form.get("max_day","7"),7,1,12)
        });
        for _sh in ("1","2"):
            if d.get("schedule",{}).get(_sh): d["schedule"][_sh]["stale"]=True
        save(d);return redirect(url_for("classes",shift=s))
    return render_template("classes.html",title="Классы",d=d)

@app.route("/edit-class/<item_id>",methods=["GET","POST"])
def edit_class(item_id):
    d=load(); s=current_shift(); item=next((x for x in d["classes"] if x.get("id")==item_id),None)
    if item is None: return "Not found",404
    if request.method=="POST":
        item["name"]=request.form.get("name","").strip()
        item["parallel"]=request.form.get("parallel","").strip()
        item["students"]=safe_int(request.form.get("students","0"),0,0,100)
        item["shift"]=request.form.get("class_shift",s) if request.form.get("class_shift",s) in ("1","2") else s
        item["max_day"]=safe_int(request.form.get("max_day","7"),7,1,12)
        
        for _sh in ("1","2"):
            if d.get("schedule",{}).get(_sh): d["schedule"][_sh]["stale"]=True
        save(d); return redirect(url_for("classes",shift=s))
    fields=[("name","Класс","text"),("parallel","Параллель","text"),("students","Ученики","number"),("class_shift","Смена","shift"),("max_day","Макс. уроков в день","number")]
    return render_template("edit.html",title="Редактировать · Класс",key="classes",item=item,fields=fields)

CONFIG={
"teachers":("Учителя",[("name","ФИО","text"),("subject","Предмет","text"),("max_day","Макс. уроков/день","number"),("wishes","Пожелания","wishes")]),
"subjects":("Предметы",[("name","Предмет","text"),("class_name","Класс (необязательно)","text"),("hours","Часов в неделю","number"),("textbook","Учебник","text"),("goal","Цели / темы","textarea"),("difficulty","Сложность","difficulty"),("room_kind","Тип кабинета","text"),("combined_classes","Совмещённые классы (через запятую)","text")]),
"rooms":("Кабинеты",[("name","Кабинет","text"),("capacity","Вместимость","number"),("kind","Тип","text"),("equipment","Оборудование","textarea")]),
"subgroups":("Подгруппы",[("class_name","Класс","text"),("name","Группа","text"),("teacher","Учитель","text"),("room","Кабинет","text")]),
"extras":("Дополнительные",[("name","Название","text"),("day","День","text"),("lesson","Урок / время","text"),("note","Что учитывать","textarea")]),
}

@app.route("/<key>",methods=["GET","POST"])
def crud(key):
    if key not in CONFIG:return "Not found",404
    d=load();s=current_shift()
    if request.method=="POST":
        item={"id":f"{key}_{datetime.now().strftime('%H%M%S%f')}"}
        for f,_,typ in CONFIG[key][1]:
            v=request.form.get(f,"").strip()
            if f in ("max_day","hours","capacity"):
                try:v=int(v or 0)
                except:v=0
            item[f]=v
        if key=="teachers":
            item["wish_priority"]=safe_int(request.form.get("wish_priority","1"),1,1,3)
            item["wish_options"]={k:(request.form.get(k)=="on") for k in (
                "no_first","no_last","no_two_consecutive","max_one_window","prefer_midday","avoid_monday","avoid_saturday") if request.form.get(k)=="on"}
        d[key].append(item); 
        for _sh in ("1","2"):
            if d.get("schedule",{}).get(_sh): d["schedule"][_sh]["stale"]=True
        save(d);return redirect(url_for("crud",key=key,shift=s))
    return render_template("crud.html",title=CONFIG[key][0],key=key,fields=CONFIG[key][1],items=d.get(key,[]))

@app.route("/edit/<key>/<item_id>",methods=["GET","POST"])
def edit_item(key,item_id):
    if key not in CONFIG: return "Not found",404
    d=load(); s=current_shift()
    item=next((x for x in d[key] if x.get("id")==item_id),None)
    if item is None: return "Not found",404
    if request.method=="POST":
        for f,_,typ in CONFIG[key][1]:
            v=request.form.get(f,"").strip()
            if f in ("max_day","hours","capacity"): v=safe_int(v,0,0,120)
            item[f]=v
        if key=="teachers":
            item["wish_priority"]=safe_int(request.form.get("wish_priority","1"),1,1,3)
            item["wish_options"]={k:(request.form.get(k)=="on") for k in (
                "no_first","no_last","no_two_consecutive","max_one_window","prefer_midday","avoid_monday","avoid_saturday") if request.form.get(k)=="on"}
        
        for _sh in ("1","2"):
            if d.get("schedule",{}).get(_sh): d["schedule"][_sh]["stale"]=True
        save(d); return redirect(url_for("crud",key=key,shift=s))
    return render_template("edit.html",title="Редактировать · "+CONFIG[key][0],key=key,item=item,fields=CONFIG[key][1])

@app.post("/delete/<key>/<item_id>")
def delete_item(key,item_id):
    d=load();s=current_shift();d[key]=[x for x in d[key] if x.get("id")!=item_id]
    for _sh in ("1","2"):
        if d.get("schedule",{}).get(_sh): d["schedule"][_sh]["stale"]=True
    save(d)
    return redirect(url_for("crud",key=key,shift=s))

@app.route("/rules",methods=["GET","POST"])
def rules():
    d=load();s=current_shift()
    if request.method=="POST":
        r=d["rules"]
        r["class_max"]=safe_int(request.form.get("class_max","7"),7,1,12);r["teacher_max"]=safe_int(request.form.get("teacher_max","6"),6,1,12);r["max_hard_per_day"]=safe_int(request.form.get("max_hard_per_day","2"),2,1,6);r["max_hard_chain"]=safe_int(request.form.get("max_hard_chain","2"),2,1,6);r["max_load_points"]=safe_int(request.form.get("max_load_points","12"),12,3,30)
        for k in ("no_same_subject","balance","avoid_hard_chain","min_windows"):r[k]=k in request.form
        
        for _sh in ("1","2"):
            if d.get("schedule",{}).get(_sh): d["schedule"][_sh]["stale"]=True
        save(d);return redirect(url_for("rules",shift=s))
    return render_template("rules.html",title="Правила",r=d["rules"])

def room_options(sub,d):
    kind=str(sub.get("room_kind","")).strip().lower()
    rooms=d.get("rooms",[])
    if kind:
        preferred=[r for r in rooms if str(r.get("kind","")).strip().lower()==kind]
        if preferred:return preferred
    return rooms or [{"id":"none","name":"—","capacity":9999}]

def get_room(sub,d):
    opts=room_options(sub,d)
    return opts[0] if opts else {"id":"none","name":"—","capacity":9999}

def _windows(positions):
    """Количество внутренних окон: 0 = уроки подряд, 1 = максимум одно окно."""
    if not positions:
        return 0
    a=sorted(positions)
    return sum(1 for x in range(a[0],a[-1]) if x not in a)

def _day_load(positions):
    return len(positions)

def teacher_wish_options(t):
    """Normalized structured teacher wishes saved by the UI."""
    raw=t.get("wish_options", {}) or {}
    if not isinstance(raw, dict): raw={}
    return {str(k): bool(v) for k,v in raw.items() if v}

def teacher_wish_penalty(t, day, slot, slots, current_positions):
    """Soft score for teacher wishes. Wishes never override mandatory constraints."""
    w=teacher_wish_options(t)
    if not w: return 0
    penalty=0
    # Importance: 1 normal, 2 important, 3 very important. Stored globally for selected options.
    try: mult=max(1,min(3,int(t.get("wish_priority",1) or 1)))
    except: mult=1
    if w.get("no_first") and slot==0: penalty += 900*mult
    if w.get("no_last") and slot==slots-1: penalty += 650*mult
    if w.get("no_two_consecutive") and current_positions and any(abs(slot-x)==1 for x in current_positions): penalty += 500*mult
    if w.get("max_one_window") and current_positions:
        old=_windows(current_positions); new=_windows(set(current_positions)|{slot})
        if new>1: penalty += 700*mult
    if w.get("prefer_midday") and slot in (0,slots-1): penalty += 180*mult
    if w.get("avoid_monday") and day=="Понедельник": penalty += 250*mult
    if w.get("avoid_saturday") and day=="Суббота": penalty += 250*mult
    return penalty

def _combined_classes(sub):
    """Return explicitly allowed classes for a combined lesson.

    Example: ``5А, 5Б`` means the same teacher may teach both classes
    simultaneously for this subject. Empty field = simultaneous teaching is forbidden.
    """
    raw=str(sub.get("combined_classes","") or "").replace(";",",")
    return {x.strip().lower() for x in raw.split(",") if x.strip()}

def _shared_teacher_entry(grid, day, slot, teacher_id):
    entries=grid.get(day,{}).get(str(slot),{})
    for value in entries.values():
        if isinstance(value,dict) and value.get("_teacher_id")==teacher_id:
            return value
    return None

def _can_share_teacher(grid, day, slot, teacher_id, sub, class_name):
    """A teacher may be in two classes at once only for an explicitly
    configured combined lesson containing both classes."""
    entries=grid.get(day,{}).get(str(slot),{})
    existing=[v for v in entries.values() if v.get("_teacher_id")==teacher_id]
    if not existing:
        return True
    allowed=_combined_classes(sub)
    if len(allowed)<2 or str(class_name).strip().lower() not in allowed:
        return False
    subject_name=str(sub.get("name","")).strip().lower()
    for v in existing:
        other=str(v.get("class","")).strip().lower()
        if other not in allowed or str(v.get("subject","")).strip().lower()!=subject_name:
            return False
    return True

def make_schedule(d,s):
    """Quality-first scheduler.

    Hard quality guards:
      * at most 2 difficulty-3 lessons per class/day;
      * at most 2 heavy (difficulty >=2) lessons in a row;
      * daily load points are capped;
      * target is 0 windows, with at most 1 window allowed;
      * teacher/room/class double booking is forbidden.
    The generator tries many randomized variants and keeps the best valid result.
    """
    import random
    classes=[c for c in d.get("classes",[]) if str(c.get("shift","1"))==s]
    if not classes:
        classes=list(d.get("classes",[]))
    days=d.get("school",{}).get("days",DAYS) or DAYS[:5]
    cfg=d.get("school",{}).get("shifts",{}).get(s,{})
    rules=d.get("rules",{})
    default_max=safe_int(rules.get("class_max",7),7,1,12)
    max_hard=safe_int(rules.get("max_hard_per_day",2),2,1,6)
    max_chain=safe_int(rules.get("max_hard_chain",2),2,1,6)
    max_points=safe_int(rules.get("max_load_points",12),12,3,30)
    class_by_name={str(c.get("name","")).strip().lower():c for c in classes}
    slots=max([safe_int(c.get("max_day",default_max),default_max,1,12) for c in classes]+[1])
    cur=datetime.strptime(cfg.get("start","08:30"),"%H:%M")
    times=[];breaks=cfg.get("breaks",[10]*12)
    for i in range(slots):
        endt=cur+timedelta(minutes=safe_int(cfg.get("lesson",45),45,30,120))
        times.append(f"{cur:%H:%M}–{endt:%H:%M}")
        cur=endt+timedelta(minutes=safe_int(breaks[i] if i<len(breaks) else 10,10,0,60))

    def subject_tasks(c):
        subs=[x for x in d.get("subjects",[]) if not x.get("class_name") or str(x.get("class_name")).lower()==str(c.get("name")).lower()]
        if not subs: subs=list(d.get("subjects",[]))
        out=[]
        for sub in subs:
            for _ in range(safe_int(sub.get("hours",0),0,0,20)):
                out.append(sub)
        return out

    def teachers_for(sub):
        name=str(sub.get("name","")).strip().lower()
        ts=[t for t in d.get("teachers",[]) if str(t.get("subject","")).strip().lower()==name]
        return ts or list(d.get("teachers",[])) or [{"id":"none","name":"—","max_day":99}]

    def room_candidates(sub):
        opts=room_options(sub,d)
        return opts or [{"id":"none","name":"—","capacity":9999}]

    def difficulty(sub): return safe_int(sub.get("difficulty",2),2,1,3)
    def windows(pos):
        if not pos:return 0
        a=sorted(pos)
        return sum(1 for i in range(a[0],a[-1]) if i not in pos)
    def heavy_chain(pos_to_diff):
        best=cur=0
        for i in range(slots):
            if pos_to_diff.get(i,0)>=2: cur+=1;best=max(best,cur)
            else: cur=0
        return best

    best=None
    rng=random.Random()
    for attempt in range(120):
        grid={day:{str(i):{} for i in range(slots)} for day in days}
        busy_t=set();busy_r=set();class_pos={(c["name"],day):set() for c in classes for day in days}
        class_diff={(c["name"],day):{} for c in classes for day in days}
        teacher_pos={}; teacher_load={}; unscheduled=[]
        tasks_by_class={c["name"]:subject_tasks(c) for c in classes}
        order=classes[:];rng.shuffle(order);order.sort(key=lambda c:-len(tasks_by_class[c["name"]]))
        feasible=True
        for c in order:
            cname=c["name"]; tasks=list(tasks_by_class[cname]);rng.shuffle(tasks)
            # Harder tasks first, but shuffle among equal difficulty.
            tasks.sort(key=lambda x:-difficulty(x))
            for sub in tasks:
                diff=difficulty(sub); candidates=[]
                for day in days:
                    pos=class_pos[(cname,day)]; diffmap=class_diff[(cname,day)]
                    cmax=safe_int(c.get("max_day",default_max),default_max,1,12)
                    if len(pos)>=cmax: continue
                    day_hard=sum(1 for v in diffmap.values() if v==3)
                    day_points=sum(diffmap.values())
                    if diff==3 and day_hard>=max_hard: continue
                    if day_points+diff>max_points: continue
                    ts=teachers_for(sub)
                    for t in ts:
                        tday=teacher_pos.setdefault((t.get("id"),day),set())
                        tmax=safe_int(t.get("max_day",rules.get("teacher_max",6)),rules.get("teacher_max",6),1,12)
                        if len(tday)>=tmax: continue
                        rooms=room_candidates(sub)
                        for i in range(slots):
                            key=(day,i)
                            if i in pos: continue
                            # Simultaneous teaching is forbidden by default.
                            # It is allowed only when this subject explicitly lists
                            # the participating classes in "Совмещённые классы".
                            if not _can_share_teacher(grid,day,i,t.get("id"),sub,cname): continue
                            # Prefer a continuation of the class block. Do not create >1 window.
                            wp=windows(pos|{i})
                            if wp>1: continue
                            # Avoid heavy chains > max_chain.
                            nd={**diffmap,i:diff}
                            if heavy_chain(nd)>max_chain: continue
                            # Subject repetition on a day is discouraged.
                            same=sum(1 for v in grid[day].values() if v.get(cname,{}).get("subject")==sub.get("name"))
                            if rules.get("no_same_subject",True) and same: continue
                            shared=_shared_teacher_entry(grid,day,i,t.get("id"))
                            if shared is not None:
                                # A combined lesson happens in one physical room.
                                room_name=str(shared.get("room","—"))
                                rooms2=[rr for rr in rooms if str(rr.get("name","—"))==room_name]
                                if not rooms2: continue
                                allowed=_combined_classes(sub)
                                total_students=sum(safe_int(class_by_name.get(x,{}).get("students",0),0) for x in allowed)
                                if safe_int(rooms2[0].get("capacity",9999),9999) < total_students: continue
                            else:
                                rooms2=[rr for rr in rooms if (rr.get("id"),key) not in busy_r]
                                if not rooms2: continue
                            room=min(rooms2,key=lambda rr:abs(safe_int(rr.get("capacity",9999),9999)-safe_int(c.get("students",0),0)))
                            # Candidate score: first zero windows, then low load, then balanced difficulty.
                            gap_pen=wp*100
                            cont_pen=0 if (not pos or i==max(pos)+1) else 15
                            teacher_gap=windows(tday|{i})
                            hard_pen=day_hard*18 if diff>=2 else day_hard*4
                            wish_pen=teacher_wish_penalty(t,day,i,slots,tday)
                            score=(gap_pen+cont_pen+teacher_gap*8+hard_pen+day_points*2+wish_pen+len(pos),rng.random())
                            candidates.append((score,day,i,t,room))
                if not candidates:
                    feasible=False;unscheduled.append(sub);break
                _,day,i,t,room=min(candidates,key=lambda x:x[0])
                key=(day,i)
                grid[day][str(i)][cname]={"class":cname,"subject":str(sub.get("name","Предмет")),"teacher":t.get("name","—"),"room":room.get("name","—"),"difficulty":diff,"_teacher_id":t.get("id"),"_room_id":room.get("id"),
                                      "_combined_classes":sorted(_combined_classes(sub))}
                class_pos[(cname,day)].add(i);class_diff[(cname,day)][i]=diff
                teacher_pos.setdefault((t.get("id"),day),set()).add(i);busy_t.add((t.get("id"),key));busy_r.add((room.get("id"),key));teacher_load[t.get("id")]=teacher_load.get(t.get("id"),0)+1
            if not feasible: break
        # Score even incomplete attempts, but prefer complete and then quality.
        window_total=sum(windows(class_pos[(c["name"],day)]) for c in classes for day in days)
        hard_viol=sum(max(0,sum(1 for v in class_diff[(c["name"],day)].values() if v==3)-max_hard) for c in classes for day in days)
        chain_viol=sum(max(0,heavy_chain(class_diff[(c["name"],day)])-max_chain) for c in classes for day in days)
        points_viol=sum(max(0,sum(class_diff[(c["name"],day)].values())-max_points) for c in classes for day in days)
        score=(len(unscheduled)*100000 + hard_viol*50000 + chain_viol*30000 + points_viol*10000 + window_total*1000)
        if best is None or score<best[0]:
            best=(score,grid,class_pos,class_diff,teacher_pos,unscheduled)
        if feasible and score==0:
            break

    score,grid,class_pos,class_diff,teacher_pos,unscheduled=best
    window_report={c["name"]:{day:windows(class_pos[(c["name"],day)]) for day in days} for c in classes}
    teacher_window_report={t.get("name","—"):{day:windows(teacher_pos.get((t.get("id"),day),set())) for day in days} for t in d.get("teachers",[])}
    quality={
        "windows_total":sum(v for byday in window_report.values() for v in byday.values()),
        "max_windows":max([v for byday in window_report.values() for v in byday.values()]+[0]),
        "unscheduled":len(unscheduled),
        "max_hard_per_day":max_hard,"max_hard_chain":max_chain,"max_load_points":max_points,
        "status":"OK" if not unscheduled and score==0 else "ТРЕБУЕТ ПРОВЕРКИ"
    }
    return {"classes":[c["name"] for c in classes],"times":times,"grid":grid,"windows":window_report,"teacher_windows":teacher_window_report,
            "unscheduled":[str(x.get("name","Предмет")) for x in unscheduled],"quality":quality,"seed":datetime.now().strftime("%H%M%S%f"),"generated_at":datetime.now().strftime("%d.%m.%Y %H:%M:%S")}

@app.route("/generate",methods=["GET","POST"])
def generate():
    d=load(); s=current_shift()
    d["schedule"][s]=make_schedule(d,s)
    d["schedule"][s]["stale"]=False
    save(d)
    return redirect(url_for("schedule",shift=s, regenerated="1"))

@app.post("/teacher-room")
def teacher_room():
    if not teacher_can_edit(): return "Forbidden",403
    d=load(); s=current_shift(); r=d.get("schedule",{}).get(s,{}) or {}
    teacher=current_teacher(); day=request.form.get("day",""); slot=str(request.form.get("slot","")); class_name=request.form.get("class_name","").strip(); room=request.form.get("room","").strip() or "—"
    v=r.get("grid",{}).get(day,{}).get(slot,{}).get(class_name)
    if not v or str(v.get("teacher","")).strip()!=teacher: return "Недоступно",403
    # Teacher may only change the room of their own lesson. Validate against room list and capacity.
    rooms={str(x.get("name","")).strip():x for x in d.get("rooms",[]) if str(x.get("name","")).strip()}
    if room!="—" and room not in rooms: return "Такого кабинета нет в системе.",400
    if room!="—":
        try:
            capacity=int(rooms[room].get("capacity",0) or 0)
        except: capacity=0
        cls=next((c for c in d.get("classes",[]) if str(c.get("name","")).strip()==class_name),{})
        students=int(cls.get("students",0) or 0)
        if capacity and students and capacity<students: return "Кабинет меньше количества учеников в классе.",400
    # Avoid stealing a room already used by another class unless this exact lesson is a permitted combined lesson.
    for cname, other in r.get("grid",{}).get(day,{}).get(slot,{}).items():
        if cname!=class_name and room!="—" and str(other.get("room","")).strip()==room:
            return "Этот кабинет уже занят в это время.",400
    v["room"]=room
    r["stale"]=False
    d["schedule"][s]=r
    save(d)
    return redirect(url_for("schedule",shift=s))

@app.get("/my-day")
def my_day():
    """Personal teacher dashboard: today's lessons with class, subject and room."""
    d=load(); s=current_shift(); r=d.get("schedule",{}).get(s,{}) or {}
    teacher=current_teacher()
    # Monday=0 ... Sunday=6. The schedule normally contains Monday-Saturday.
    day_names=DAYS
    today_name=day_names[datetime.now().weekday()] if datetime.now().weekday() < len(day_names) else "Воскресенье"
    lessons=[]
    grid=r.get("grid",{}) if isinstance(r,dict) else {}
    classes=r.get("classes",[]) if isinstance(r,dict) else []
    times=r.get("times",[]) if isinstance(r,dict) else []
    for day in day_names:
        if day != today_name:
            continue
        day_grid=grid.get(day,{}) or {}
        for i,t in enumerate(times):
            row=day_grid.get(str(i),{}) or {}
            for cname in classes:
                v=row.get(cname)
                if isinstance(v,dict) and str(v.get("teacher","")).strip()==teacher:
                    lessons.append({"slot":i+1,"time":t,"class_name":cname,"subject":v.get("subject","—"),"room":v.get("room","—")})
    # If there is no generated schedule yet, the page explains that directly.
    return render_template("my_day.html",title="Мой день",d=d,r=r,teacher=teacher,today=today_name,lessons=lessons)

@app.get("/student")
def student_view():
    """Simple student view: only the selected class timetable, no editing."""
    d=load(); s=current_shift(); r=d.get("schedule",{}).get(s,{}) or {}
    class_name=session.get("student_class","")
    if class_name not in (r.get("classes",[]) if isinstance(r,dict) else []):
        return render_template("student.html",title="Расписание класса",d=d,r=r,class_name=class_name,missing=True)
    return render_template("student.html",title="Расписание класса",d=d,r=r,class_name=class_name,missing=False)

@app.get("/schedule")
def schedule():
    d=load();s=current_shift();return render_template("schedule.html",title="Расписание",d=d,r=d["schedule"].get(s,{}),regenerated=request.args.get("regenerated")=="1")

@app.get("/check")
def check():
    d=load();s=current_shift();r=d["schedule"].get(s,{})
    issues=[]
    for day,slots in r.get("grid",{}).items():
        for idx,classes_map in slots.items():
            seen_t=set();seen_r=set()
            entries=list(classes_map.values())
            for v in entries:
                if v.get("teacher") in seen_t and v.get("teacher")!="—":
                    allowed={str(x).strip().lower() for x in v.get("_combined_classes",[]) if str(x).strip()}
                    cname=str(v.get("class","")).strip().lower()
                    ok=bool(allowed and cname in allowed and all(
                        other is v or (other.get("teacher")!=v.get("teacher") or (
                            str(other.get("subject","")).strip().lower()==str(v.get("subject","")).strip().lower()
                            and str(other.get("class","")).strip().lower() in allowed
                            and str(other.get("room","")).strip()==str(v.get("room","")).strip()
                            and {str(x).strip().lower() for x in other.get("_combined_classes",[]) if str(x).strip()}==allowed))
                        for other in entries
                    ))
                    if not ok: issues.append(f"{day}, урок {int(idx)+1}: учитель занят одновременно.")
                if v.get("room") in seen_r and v.get("room")!="—":
                    allowed={str(x).strip().lower() for x in v.get("_combined_classes",[]) if str(x).strip()}
                    cname=str(v.get("class","")).strip().lower()
                    same_combined=bool(allowed and cname in allowed and any(
                        other is not v and other.get("room")==v.get("room")
                        and str(other.get("subject","")).strip().lower()==str(v.get("subject","")).strip().lower()
                        and str(other.get("class","")).strip().lower() in allowed
                        and {str(x).strip().lower() for x in other.get("_combined_classes",[]) if str(x).strip()}==allowed
                        for other in entries))
                    if not same_combined: issues.append(f"{day}, урок {int(idx)+1}: кабинет занят одновременно.")
                seen_t.add(v.get("teacher"));seen_r.add(v.get("room"))
    max_windows=1
    class_windows=[]
    for cname,byday in r.get("windows",{}).items():
        for day,w in byday.items():
            if w>max_windows: class_windows.append(f"{cname}: {day} — {w} окна(окон).")
    teacher_windows=[]
    for tname,byday in r.get("teacher_windows",{}).items():
        for day,w in byday.items():
            if w>max_windows: teacher_windows.append(f"{tname}: {day} — {w} окна(окон).")
    issues += ["Окна у классов: "+x for x in class_windows]
    issues += ["Окна у учителей: "+x for x in teacher_windows]
    return render_template("check.html",title="Проверка",issues=issues,has=bool(r),class_windows=class_windows,teacher_windows=teacher_windows)

FAQ_QUESTIONS = [
    "Как правильно заполнить данные школы перед генерацией?",
    "Как составитель добавляет всех педагогов?",
    "Как учителю войти под своей фамилией?",
    "Что учитель может изменять в системе?",
    "Как учитель меняет кабинет своего урока?",
    "Как сгенерировать расписание?",
    "Почему расписание не генерируется полностью?",
    "Как система не допускает двух уроков одного учителя одновременно?",
    "Как разрешить совмещённый урок для двух классов?",
    "Как система избегает тяжёлого расписания?",
    "Как уменьшить окна в расписании?",
    "Как включить субботу?",
    "Как работают первая и вторая смена?",
    "Как добавить кабинет и проверить его вместимость?",
    "Как добавить подгруппы?",
    "Как скачать расписание в Excel?",
    "Как пользоваться личным кабинетом учителя?",
    "Как открыть систему с телефона через интернет?",
    "Что делать, если появился конфликт кабинетов?",
    "Что проверяет раздел «Проверка»?",
    "Что умеет AI-чат SmartSchedule?",
]

@app.route("/ai",methods=["GET","POST"])
def ai():

    d=load();s=current_shift()
    if request.method=="POST":
        q=request.form.get("question","").strip()
        if q:
            answer = ai_answer(q,d,s)
            d["ai_history"].append({"q":q,"a":answer})
            d["ai_history"]=d["ai_history"][-20:]
            save(d)
        return redirect(url_for("ai",shift=s))
    return render_template("ai.html",title="AI-чат",history=d["ai_history"][-20:],faq_questions=FAQ_QUESTIONS)


def ai_answer(q,d,s):
    """Answer locally without requiring an API key. Ollama is optional."""
    ql=q.lower()
    # Optional local Ollama model. Never required for the app to work.
    try:
        import json as _json
        model=os.environ.get("OLLAMA_MODEL","qwen2.5:3b")
        payload=_json.dumps({
            "model":model,
            "prompt":("Ты помощник школьного планировщика SmartSchedule. Отвечай по-русски кратко и по делу. "
                      f"Школа: {d['school']['name']}; смена: {s}; классов: {len(d['classes'])}; учителей: {len(d['teachers'])}; "
                      f"предметов: {len(d['subjects'])}; кабинетов: {len(d['rooms'])}. Вопрос: {q}"),
            "stream":False
        }).encode("utf-8")
        req=urllib.request.Request("http://127.0.0.1:11434/api/generate",data=payload,headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=5) as resp:
            body=_json.loads(resp.read().decode("utf-8"))
            text=body.get("response","").strip()
            if text:return text
    except Exception:
        pass

    r=d.get("schedule",{}).get(s,{})
    if any(x in ql for x in ("как получается", "как получить расписание", "как создать расписание", "как генер")):
        return (f"Расписание строится в несколько этапов: сначала SmartSchedule получает классы и их лимиты, "
                f"затем часы предметов, подходящих учителей и кабинеты. После этого генератор размещает уроки по смене "
                f"и проверяет конфликты. Главный приоритет — обязательные ограничения, затем отсутствие окон и равномерная нагрузка. "
                f"Для смены {s} сейчас задано классов: {len([c for c in d['classes'] if str(c.get('shift','1'))==s])}. "
                "После изменения исходных данных нужно нажать «Перегенерировать расписание».")
    if "окн" in ql:
        total=0; maxw=0
        for byday in r.get("windows",{}).values():
            for w in byday.values(): total+=w; maxw=max(maxw,w)
        return (f"В текущем расписании смены {s} учтена цель 0 окон, а допустимый максимум — 1 окно. "
                f"Сейчас рассчитано внутренних окон: {total}, максимальное число окон за один день у класса — {maxw}. "
                "Если ограничения школы делают ноль окон невозможным, система должна показать это в разделе «Проверка», а не молча ухудшать расписание.")
    if "тяж" in ql or "сложн" in ql or "нагруз" in ql:
        return ("Тяжёлое расписание ограничивается тремя правилами: не более 2 сложных предметов (уровень 3) в день, "
                "не более 2 сложных подряд и ограничение суммарной нагрузки дня. Уровень сложности: 1 — лёгкий, "
                "2 — средний, 3 — сложный. Если все ограничения одновременно выполнить нельзя, это фиксируется как проблема, "
                "а не маскируется красивой, но перегруженной сеткой.")
    if "учител" in ql:
        return (f"В системе {len(d['teachers'])} записей учителей. При генерации один педагог не должен одновременно вести "
                "два класса. Его дневной максимум и пожелания учитываются как ограничения/предпочтения. "
                "Учителя можно редактировать без удаления записи.")
    if "кабин" in ql:
        return (f"Сейчас в системе {len(d['rooms'])} кабинетов. В один и тот же момент один кабинет не назначается "
                "двум занятиям. Тип кабинета помогает подбирать подходящее помещение для предмета.")
    return (f"Я вижу контекст SmartSchedule: смена {s}, классов — {len(d['classes'])}, учителей — {len(d['teachers'])}, "
            f"предметов — {len(d['subjects'])}, кабинетов — {len(d['rooms'])}. "
            "Задай конкретный вопрос, например: «почему у 7А появилось окно?», «как изменить учителя?», "
            "«почему расписание не генерируется?» или «как убрать тяжёлые предметы подряд?».")

@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    """Keep the school UI usable when an unexpected local error occurs.
    The technical detail is printed to the server console, while the browser gets a readable recovery page.
    """
    import traceback
    traceback.print_exc()
    try:
        return render_template("error.html", title="Ошибка", message="Произошла внутренняя ошибка. Данные не удалены. Перезапусти START.bat и попробуй ещё раз. Если ошибка повторяется, открой раздел «Помощь» и сообщи составителю, что SmartSchedule остановил действие."), 500
    except Exception:
        return "SmartSchedule: внутренняя ошибка. Перезапустите START.bat.", 500

@app.get("/documents")
def documents():return render_template("documents.html",title="Документы")

@app.get("/help")
def help_page():return render_template("help.html",title="Помощь")

@app.get("/export")
def export():
    d=load();s=current_shift();r=d["schedule"].get(s,{})
    if not r:return redirect(url_for("schedule",shift=s))
    from exporter import export_xlsx
    path=export_xlsx(d,s,r,BASE/"data"/f"SmartSchedule_смена_{s}.xlsx")
    return send_file(path,as_attachment=True,download_name=path.name,mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.get("/export/day/<day>")
def export_day(day):
    d=load();s=current_shift();r=d["schedule"].get(s,{})
    if not r or day not in d["school"]["days"]:return redirect(url_for("schedule",shift=s))
    from exporter import export_xlsx
    path=export_xlsx(d,s,r,BASE/"data"/f"SmartSchedule_{s}_{day}.xlsx",only_day=day)
    return send_file(path,as_attachment=True,download_name=path.name,mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=8765,debug=False,use_reloader=False)
