
from flask import Flask, request, jsonify, send_from_directory
import sqlite3, json, base64, os
from datetime import datetime

app = Flask(__name__, static_folder="static")
DB = os.environ.get("ED_FLOW_DB", "ed_flow.db")
MAX_UPLOAD = 10 * 1024 * 1024

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS patients(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      patient_code TEXT NOT NULL,
      name TEXT,
      age INTEGER,
      sex TEXT,
      patient_type TEXT,
      chief_complaint TEXT,
      history TEXT,
      examination TEXT,
      vitals TEXT,
      red_flags TEXT,
      news2 INTEGER DEFAULT 0,
      risk TEXT DEFAULT 'STABLE',
      provisional_diagnosis TEXT,
      created_at TEXT,
      updated_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS investigations(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      patient_id INTEGER NOT NULL,
      kind TEXT NOT NULL,
      data TEXT,
      filename TEXT,
      mimetype TEXT,
      file_b64 TEXT,
      created_at TEXT,
      FOREIGN KEY(patient_id) REFERENCES patients(id)
    )""")
    c.commit()
    c.close()

def parse_json_fields(x):
    for k in ["history","examination","vitals","red_flags"]:
        try: x[k] = json.loads(x.get(k) or "{}")
        except Exception: x[k] = {}
    return x

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/patients", methods=["GET"])
def patients():
    q = request.args.get("q","").strip()
    c = db()
    if q:
        rows = c.execute("""SELECT * FROM patients
          WHERE patient_code LIKE ? OR name LIKE ? OR chief_complaint LIKE ? OR patient_type LIKE ?
          ORDER BY updated_at DESC""",
          tuple([f"%{q}%"]*4)).fetchall()
    else:
        rows = c.execute("SELECT * FROM patients ORDER BY updated_at DESC").fetchall()
    out = [parse_json_fields(dict(r)) for r in rows]
    c.close()
    return jsonify(out)

@app.route("/api/patients/<int:pid>", methods=["GET"])
def patient(pid):
    c = db()
    r = c.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    c.close()
    if not r: return jsonify({"error":"Patient not found"}),404
    return jsonify(parse_json_fields(dict(r)))

@app.route("/api/patients", methods=["POST"])
def create_patient():
    d = request.json or {}
    now = datetime.now().isoformat(timespec="seconds")
    code = d.get("patient_code") or f"ED-{datetime.now().strftime('%y%m%d%H%M%S')}"
    c = db()
    cur = c.execute("""INSERT INTO patients
      (patient_code,name,age,sex,patient_type,chief_complaint,history,examination,vitals,red_flags,news2,risk,provisional_diagnosis,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (code,d.get("name",""),d.get("age"),d.get("sex",""),d.get("patient_type",""),
       d.get("chief_complaint",""),json.dumps(d.get("history",{})),json.dumps(d.get("examination",{})),
       json.dumps(d.get("vitals",{})),json.dumps(d.get("red_flags",{})),d.get("news2",0),
       d.get("risk","STABLE"),d.get("provisional_diagnosis",""),now,now))
    c.commit(); pid = cur.lastrowid; c.close()
    return jsonify({"id":pid,"patient_code":code,"message":"Patient saved"})

@app.route("/api/patients/<int:pid>", methods=["PUT"])
def update_patient(pid):
    d = request.json or {}; now = datetime.now().isoformat(timespec="seconds")
    c = db()
    if not c.execute("SELECT id FROM patients WHERE id=?", (pid,)).fetchone():
        c.close(); return jsonify({"error":"Patient not found"}),404
    c.execute("""UPDATE patients SET name=?,age=?,sex=?,patient_type=?,chief_complaint=?,history=?,examination=?,vitals=?,red_flags=?,news2=?,risk=?,provisional_diagnosis=?,updated_at=? WHERE id=?""",
      (d.get("name",""),d.get("age"),d.get("sex",""),d.get("patient_type",""),d.get("chief_complaint",""),
       json.dumps(d.get("history",{})),json.dumps(d.get("examination",{})),json.dumps(d.get("vitals",{})),
       json.dumps(d.get("red_flags",{})),d.get("news2",0),d.get("risk","STABLE"),
       d.get("provisional_diagnosis",""),now,pid))
    c.commit(); c.close()
    return jsonify({"message":"Updated"})

@app.route("/api/patients/<int:pid>", methods=["DELETE"])
def delete_patient(pid):
    c=db(); c.execute("DELETE FROM investigations WHERE patient_id=?",(pid,)); c.execute("DELETE FROM patients WHERE id=?",(pid,))
    c.commit(); c.close(); return jsonify({"message":"Deleted"})

@app.route("/api/dashboard")
def dashboard():
    c=db()
    total=c.execute("SELECT COUNT(*) n FROM patients").fetchone()["n"]
    red=c.execute("SELECT COUNT(*) n FROM patients WHERE risk='RED'").fetchone()["n"]
    urgent=c.execute("SELECT COUNT(*) n FROM patients WHERE risk='URGENT'").fetchone()["n"]
    stable=c.execute("SELECT COUNT(*) n FROM patients WHERE risk='STABLE'").fetchone()["n"]
    recent=c.execute("SELECT * FROM patients ORDER BY updated_at DESC LIMIT 8").fetchall()
    c.close()
    return jsonify({"total":total,"red":red,"urgent":urgent,"stable":stable,"recent":[dict(x) for x in recent]})

@app.route("/api/patients/<int:pid>/investigations", methods=["GET"])
def get_investigations(pid):
    c=db()
    rows=c.execute("""SELECT id,patient_id,kind,data,filename,mimetype,created_at
                      FROM investigations WHERE patient_id=? ORDER BY created_at DESC""",(pid,)).fetchall()
    c.close()
    out=[]
    for r in rows:
        x=dict(r)
        try:x["data"]=json.loads(x["data"] or "{}")
        except Exception:x["data"]={}
        out.append(x)
    return jsonify(out)

@app.route("/api/patients/<int:pid>/investigations", methods=["POST"])
def add_investigation(pid):
    c=db()
    if not c.execute("SELECT id FROM patients WHERE id=?",(pid,)).fetchone():
        c.close(); return jsonify({"error":"Patient not found"}),404
    kind=request.form.get("kind","Other")
    data=request.form.get("data","{}")
    try: json.loads(data)
    except Exception: data="{}"
    f=request.files.get("file")
    filename=mimetype=file_b64=None
    if f and f.filename:
        raw=f.read()
        if len(raw)>MAX_UPLOAD:
            c.close(); return jsonify({"error":"File too large. Maximum 10 MB."}),413
        filename=f.filename[:255]
        mimetype=f.mimetype or "application/octet-stream"
        file_b64=base64.b64encode(raw).decode("ascii")
    now=datetime.now().isoformat(timespec="seconds")
    cur=c.execute("""INSERT INTO investigations(patient_id,kind,data,filename,mimetype,file_b64,created_at)
                     VALUES(?,?,?,?,?,?,?)""",(pid,kind,data,filename,mimetype,file_b64,now))
    c.commit(); iid=cur.lastrowid; c.close()
    return jsonify({"id":iid,"message":"Investigation saved to patient"})

@app.route("/api/investigations/<int:iid>/file", methods=["GET"])
def investigation_file(iid):
    c=db(); r=c.execute("SELECT filename,mimetype,file_b64 FROM investigations WHERE id=?",(iid,)).fetchone(); c.close()
    if not r or not r["file_b64"]: return jsonify({"error":"No file"}),404
    raw=base64.b64decode(r["file_b64"])
    from flask import Response
    return Response(raw, mimetype=r["mimetype"], headers={"Content-Disposition":f'inline; filename="{r["filename"]}"'})

@app.route("/api/investigations/<int:iid>", methods=["DELETE"])
def delete_investigation(iid):
    c=db(); c.execute("DELETE FROM investigations WHERE id=?",(iid,)); c.commit(); c.close()
    return jsonify({"message":"Investigation deleted"})

init()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=5000,debug=False)
