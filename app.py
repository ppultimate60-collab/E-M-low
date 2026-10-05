
from flask import Flask, request, jsonify, send_from_directory
import sqlite3, json
from datetime import datetime

app=Flask(__name__, static_folder="static")
DB="ed_flow.db"

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init():
    c=db()
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
      news2 INTEGER,
      risk TEXT,
      provisional_diagnosis TEXT,
      created_at TEXT,
      updated_at TEXT
    )""")
    c.commit(); c.close()

@app.route("/")
def index(): return send_from_directory("static","index.html")

@app.route("/api/patients",methods=["GET"])
def patients():
    q=request.args.get("q","").strip()
    c=db()
    if q:
        rows=c.execute("""SELECT * FROM patients WHERE patient_code LIKE ? OR name LIKE ?
                          OR chief_complaint LIKE ? OR patient_type LIKE ?
                          ORDER BY updated_at DESC""",(f"%{q}%",f"%{q}%",f"%{q}%",f"%{q}%")).fetchall()
    else:
        rows=c.execute("SELECT * FROM patients ORDER BY updated_at DESC").fetchall()
    out=[]
    for r in rows:
        x=dict(r)
        for k in ["history","examination","vitals","red_flags"]:
            try:x[k]=json.loads(x[k] or "{}")
            except:x[k]={}
        out.append(x)
    c.close(); return jsonify(out)

@app.route("/api/patients/<int:pid>",methods=["GET"])
def patient(pid):
    c=db(); r=c.execute("SELECT * FROM patients WHERE id=?",(pid,)).fetchone(); c.close()
    if not r:return jsonify({"error":"Patient not found"}),404
    x=dict(r)
    for k in ["history","examination","vitals","red_flags"]:
        try:x[k]=json.loads(x[k] or "{}")
        except:x[k]={}
    return jsonify(x)

@app.route("/api/patients",methods=["POST"])
def create_patient():
    d=request.json or {}
    now=datetime.now().isoformat(timespec="seconds")
    c=db()
    cur=c.execute("""INSERT INTO patients
    (patient_code,name,age,sex,patient_type,chief_complaint,history,examination,vitals,red_flags,news2,risk,provisional_diagnosis,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
      d.get("patient_code") or f"ED-{datetime.now().strftime('%y%m%d%H%M%S')}",
      d.get("name",""),d.get("age"),d.get("sex",""),d.get("patient_type",""),
      d.get("chief_complaint",""),json.dumps(d.get("history",{})),json.dumps(d.get("examination",{})),
      json.dumps(d.get("vitals",{})),json.dumps(d.get("red_flags",{})),d.get("news2",0),d.get("risk","STABLE"),
      d.get("provisional_diagnosis",""),now,now))
    c.commit(); pid=cur.lastrowid; c.close()
    return jsonify({"id":pid,"message":"Patient saved"})

@app.route("/api/patients/<int:pid>",methods=["PUT"])
def update_patient(pid):
    d=request.json or {}; now=datetime.now().isoformat(timespec="seconds")
    c=db()
    exists=c.execute("SELECT id FROM patients WHERE id=?",(pid,)).fetchone()
    if not exists:return jsonify({"error":"Patient not found"}),404
    c.execute("""UPDATE patients SET name=?,age=?,sex=?,patient_type=?,chief_complaint=?,history=?,examination=?,vitals=?,red_flags=?,news2=?,risk=?,provisional_diagnosis=?,updated_at=? WHERE id=?""",(
      d.get("name",""),d.get("age"),d.get("sex",""),d.get("patient_type",""),d.get("chief_complaint",""),
      json.dumps(d.get("history",{})),json.dumps(d.get("examination",{})),json.dumps(d.get("vitals",{})),
      json.dumps(d.get("red_flags",{})),d.get("news2",0),d.get("risk","STABLE"),d.get("provisional_diagnosis",""),now,pid))
    c.commit(); c.close(); return jsonify({"message":"Updated"})

@app.route("/api/patients/<int:pid>",methods=["DELETE"])
def delete_patient(pid):
    c=db(); c.execute("DELETE FROM patients WHERE id=?",(pid,)); c.commit(); c.close()
    return jsonify({"message":"Deleted"})

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

init()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=5000,debug=False)
