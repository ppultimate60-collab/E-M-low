from flask import Flask, request, jsonify, send_from_directory
import sqlite3, json, os
from datetime import datetime
from openai import OpenAI

app = Flask(__name__)
DB = "ed_flow.db"
MODEL = os.getenv("OPENAI_MODEL", "gpt-6-astra")

SYSTEM_PROMPT = """You are an emergency medicine clinical decision-support assistant for a physician.
Analyze the supplied ED patient information deeply and conservatively. Do not invent missing data.
Prioritize ABCDE and immediately life-threatening diagnoses over scores. Give a clinically useful differential and initial emergency-department management, but do not present the answer as an autonomous diagnosis or prescription.
Use concise physician-facing language. Mention uncertainty and important missing information.
Return ONLY valid JSON with these keys:
most_likely_diagnosis: string
risk: one of RED, URGENT, STABLE
why: array of strings
differentials: array of strings
must_not_miss: array of strings
initial_er_management: array of strings
investigations: array of strings
disposition: string
"""

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS patients(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      patient_code TEXT NOT NULL, name TEXT, age INTEGER, sex TEXT,
      patient_type TEXT, chief_complaint TEXT, history TEXT, examination TEXT,
      vitals TEXT, red_flags TEXT, news2 INTEGER, risk TEXT,
      provisional_diagnosis TEXT, ai_reasoning TEXT, created_at TEXT, updated_at TEXT
    )""")
    # Upgrade older V4/V3 databases without breaking existing data.
    cols = {r[1] for r in c.execute("PRAGMA table_info(patients)").fetchall()}
    if "ai_reasoning" not in cols:
        c.execute("ALTER TABLE patients ADD COLUMN ai_reasoning TEXT")
    c.commit(); c.close()

def decode(x):
    try: return json.loads(x or "{}")
    except Exception: return {}

def row_out(r):
    x = dict(r)
    for k in ["history", "examination", "vitals", "red_flags", "ai_reasoning"]:
        x[k] = decode(x.get(k))
    return x

@app.route("/")
def index():
    return send_from_directory(".", "ed_flow_v43_index.html")

@app.route("/api/dashboard")
def dashboard():
    c=db(); total=c.execute("SELECT COUNT(*) n FROM patients").fetchone()["n"]
    red=c.execute("SELECT COUNT(*) n FROM patients WHERE risk='RED'").fetchone()["n"]
    urgent=c.execute("SELECT COUNT(*) n FROM patients WHERE risk='URGENT'").fetchone()["n"]
    stable=c.execute("SELECT COUNT(*) n FROM patients WHERE risk='STABLE'").fetchone()["n"]
    recent=c.execute("SELECT * FROM patients ORDER BY updated_at DESC LIMIT 8").fetchall(); c.close()
    return jsonify({"total":total,"red":red,"urgent":urgent,"stable":stable,"recent":[row_out(x) for x in recent]})

@app.route("/api/patients", methods=["GET"])
def patients():
    q=request.args.get("q","").strip(); c=db()
    if q:
        rows=c.execute("""SELECT * FROM patients WHERE patient_code LIKE ? OR name LIKE ? OR chief_complaint LIKE ? OR patient_type LIKE ? ORDER BY updated_at DESC""", tuple([f"%{q}%"]*4)).fetchall()
    else: rows=c.execute("SELECT * FROM patients ORDER BY updated_at DESC").fetchall()
    c.close(); return jsonify([row_out(r) for r in rows])

@app.route("/api/patients/<int:pid>")
def patient(pid):
    c=db(); r=c.execute("SELECT * FROM patients WHERE id=?",(pid,)).fetchone(); c.close()
    if not r:return jsonify({"error":"Patient not found"}),404
    return jsonify(row_out(r))

@app.route("/api/patients", methods=["POST"])
def create_patient():
    d=request.json or {}; now=datetime.now().isoformat(timespec="seconds"); c=db()
    cur=c.execute("""INSERT INTO patients(patient_code,name,age,sex,patient_type,chief_complaint,history,examination,vitals,red_flags,news2,risk,provisional_diagnosis,ai_reasoning,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
      d.get("patient_code") or f"ED-{datetime.now().strftime('%y%m%d%H%M%S')}",d.get("name",""),d.get("age"),d.get("sex",""),d.get("patient_type",""),d.get("chief_complaint",""),json.dumps(d.get("history",{})),json.dumps(d.get("examination",{})),json.dumps(d.get("vitals",{})),json.dumps(d.get("red_flags",[])),d.get("news2",0),d.get("risk","STABLE"),d.get("provisional_diagnosis","") ,json.dumps(d.get("ai_reasoning",{})),now,now))
    c.commit(); pid=cur.lastrowid; c.close(); return jsonify({"id":pid,"message":"Patient saved"})

@app.route("/api/ai/clinical-reasoning", methods=["POST"])
def clinical_reasoning():
    if not os.getenv("OPENAI_API_KEY"):
        return jsonify({"error":"OPENAI_API_KEY is not configured on the server."}),503
    data=request.json or {}
    try:
        client=OpenAI()
        response=client.responses.create(model=MODEL, instructions=SYSTEM_PROMPT, input=[{"role":"user","content":json.dumps(data, ensure_ascii=False)}])
        text=response.output_text.strip()
        # tolerate fenced JSON if the model returns it
        if text.startswith("```"):
            text=text.strip("`").replace("json\n", "", 1).strip()
        result=json.loads(text)
        return jsonify(result)
    except json.JSONDecodeError:
        return jsonify({"error":"AI returned non-JSON output. Please retry."}),502
    except Exception as e:
        return jsonify({"error":f"AI service error: {str(e)}"}),502

init()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
