
from flask import Flask, request, jsonify, send_from_directory
try:
    from openai import OpenAI
except Exception:
    OpenAI = None
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


# --- V4.2 DISEASE-SPECIFIC SCORES ---
# Decision-support calculators. Missing inputs are never guessed.
def _num(v):
    try:
        return float(v)
    except Exception:
        return None

def disease_specific_scores(data):
    """Return only scores relevant to the clinical presentation."""
    out=[]
    complaint=str(data.get("complaint") or "").lower()
    vit=data.get("vitals") or {}
    exam=data.get("exam") or {}
    labs=data.get("labs") or {}

    age=_num(data.get("age"))
    sbp=_num(vit.get("sbp"))
    rr=_num(vit.get("rr"))
    hr=_num(vit.get("hr"))
    gcs=_num(vit.get("gcs"))
    temp=_num(vit.get("temp"))
    spo2=_num(vit.get("spo2"))
    glucose=_num(labs.get("glucose") or data.get("rbs"))
    ketone=_num(labs.get("ketone"))
    creat=_num(labs.get("creatinine"))
    bun=_num(labs.get("bun"))
    sodium=_num(labs.get("sodium"))
    potassium=_num(labs.get("potassium"))

    # DKA severity: use pH/bicarbonate when supplied; no diagnosis from glucose alone.
    if any(x in complaint for x in ["dka","diabetic keto","hypergly", "ketone"]):
        ph=_num(labs.get("ph")); hco3=_num(labs.get("hco3"))
        if ph is not None and hco3 is not None:
            if ph < 7.0 or hco3 < 10: sev="Severe"
            elif ph < 7.25 or hco3 < 15: sev="Moderate"
            else: sev="Mild"
            out.append({"score":"DKA severity (biochemical)","result":sev,
                        "inputs":"pH + HCO3","note":"Confirm against current institutional/consensus DKA criteria; beta-hydroxybutyrate and clinical status matter."})
        else:
            out.append({"score":"DKA severity","result":"Incomplete","note":"Enter venous/arterial pH and HCO3; do not infer severity from glucose alone."})

    # BISAP for pancreatitis: requires BUN, mental status, SIRS, age, pleural effusion.
    if "pancre" in complaint:
        vals=[bun, age]
        sirs=None
        if vit.get("sirs") is not None: sirs=_num(vit.get("sirs"))
        pleural=data.get("pleural_effusion")
        if bun is not None and age is not None and sirs is not None and pleural is not None and gcs is not None:
            score=(1 if bun>25 else 0)+(1 if sirs>=2 else 0)+(1 if age>=60 else 0)+(1 if gcs<15 else 0)+(1 if str(pleural).lower() in ["yes","true","1"] else 0)
            out.append({"score":"BISAP","result":str(score)+"/5","note":"Higher scores correlate with increased risk; interpret with clinical assessment."})
        else:
            out.append({"score":"BISAP","result":"Incomplete","note":"Requires BUN, SIRS, age, mental status and pleural effusion."})

    # AF stroke risk
    if "atrial fibrillation" in complaint or re.search(r"\baf\b", complaint):
        cha=data.get("chads2vasc")
        has=data.get("hasbled")
        out.append({"score":"CHA₂DS₂-VASc","result":str(cha) if cha not in [None,""] else "Incomplete",
                    "note":"Use validated components; anticoagulation decision requires clinician assessment."})
        out.append({"score":"HAS-BLED","result":str(has) if has not in [None,""] else "Incomplete",
                    "note":"Bleeding-risk assessment; not a reason by itself to withhold anticoagulation."})

    # Stroke: ABCD2 when TIA-like complaint
    if any(x in complaint for x in ["tia","transient weakness","transient aphasia","transient vision","stroke"]):
        ab=data.get("abcd2")
        out.append({"score":"ABCD²","result":str(ab) if ab not in [None,""] else "Incomplete",
                    "note":"Use only when the presentation is clinically appropriate for TIA risk assessment."})

    # Trauma: shock index
    if any(x in complaint for x in ["trauma","injury","accident","fall","road traffic","rt a"]):
        if hr is not None and sbp and sbp>0:
            si=hr/sbp
            out.append({"score":"Shock Index","result":f"{si:.2f}","note":"Interpret in context; age, pregnancy, medications and mechanism affect meaning."})

    # GI bleed: GBS (basic completeness flag)
    if any(x in complaint for x in ["gi bleed","gastrointestinal bleed","melena","hematemesis","upper gi"]):
        gbs=data.get("glasgow_blatchford")
        out.append({"score":"Glasgow-Blatchford Score","result":str(gbs) if gbs not in [None,""] else "Incomplete",
                    "note":"Requires BUN, Hb, SBP, pulse, melena, syncope, hepatic disease and cardiac failure."})

    # Sepsis: SOFA/qSOFA only as decision support, not diagnosis.
    if any(x in complaint for x in ["sepsis","septic","infection","fever"]):
        qsofa=0
        complete=True
        for x,cut in [(rr,22),(sbp,100),(gcs,15)]:
            if x is None: complete=False
        if complete:
            qsofa=(1 if rr>=22 else 0)+(1 if sbp<=100 else 0)+(1 if gcs<15 else 0)
            out.append({"score":"qSOFA","result":str(qsofa)+"/3",
                        "note":"qSOFA is not a sepsis diagnostic test; use organ dysfunction and clinical assessment."})
        else:
            out.append({"score":"qSOFA","result":"Incomplete","note":"Requires RR, SBP and GCS."})

    # CURB-65 for suspected pneumonia
    if any(x in complaint for x in ["pneumonia","lower respiratory infection","cough","dyspnea","shortness of breath"]):
        bun_mmol = None
        if bun is not None: bun_mmol=bun/2.8
        conf=data.get("confusion")
        if age is not None and rr is not None and sbp is not None and bun_mmol is not None and conf is not None:
            score=(1 if conf else 0)+(1 if rr>=30 else 0)+(1 if sbp<90 or (sbp<=60) else 0)+(1 if age>=65 else 0)+(1 if bun_mmol>=7 else 0)
            out.append({"score":"CURB-65","result":str(score)+"/5","note":"Use for suspected community-acquired pneumonia; interpret with oxygenation, comorbidity and clinical severity."})
        else:
            out.append({"score":"CURB-65","result":"Incomplete","note":"Requires confusion, RR, SBP, age and BUN."})

    return out

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


def _clean_json(text):
    text=(text or "").strip()
    if text.startswith("```"):
        text=re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text=re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m=re.search(r"\{.*\}", text, flags=re.S)
        if m:
            try:return json.loads(m.group(0))
            except Exception:pass
    return {"raw": text}

def calculate_clinical_scores(d):
    v=d.get("vitals",{}) or {}; si=d.get("score_inputs",{}) or {}
    def n(x):
        try: return None if x in (None,"") else float(x)
        except: return None
    age=n(d.get("age")); rr=n(v.get("rr")); sbp=n(v.get("sbp")); hr=n(v.get("hr")); gcs=n(v.get("gcs")); dbp=n(si.get("dbp")); urea=n(si.get("urea_mmol")); hb=n(si.get("hb_gdl"))
    out={}
    q=[rr is not None and rr>=22,sbp is not None and sbp<=100,gcs is not None and gcs<15]
    out["qSOFA"]={"score":sum(q) if all(x is not None for x in q) else None,"complete":all(x is not None for x in q),"interpretation":"Use with suspected infection; not a sepsis rule-in/rule-out test."}
    c=[bool(si.get("confusion")) if "confusion" in si else None, urea>7 if urea is not None else None, rr>=30 if rr is not None else None, (sbp<90 or dbp<=60) if sbp is not None and dbp is not None else None, age>=65 if age is not None else None]
    out["CURB-65"]={"score":sum(c) if all(x is not None for x in c) else None,"complete":all(x is not None for x in c),"interpretation":"Pneumonia severity aid; use clinical judgement/local pathway."}
    heart=[n(si.get("heart_history")),n(si.get("heart_ecg")),(0 if age<45 else 1 if age<65 else 2) if age is not None else None,n(si.get("heart_risk_factors")),n(si.get("troponin"))]
    out["HEART"]={"score":sum(heart) if all(x is not None for x in heart) else None,"complete":all(x is not None for x in heart),"interpretation":"Chest-pain risk stratification; interpret troponin with assay/serial testing."}
    keys=["dvt_signs","pe_most_likely","recent_surgery_immobilization","previous_vte","hemoptysis","malignancy"]
    complete=hr is not None and all(k in si for k in keys)
    wells=(3*bool(si.get("dvt_signs"))+3*bool(si.get("pe_most_likely"))+(1.5 if hr is not None and hr>100 else 0)+(1.5 if si.get("recent_surgery_immobilization") else 0)+(1.5 if si.get("previous_vte") else 0)+(1 if si.get("hemoptysis") else 0)+(1 if si.get("malignancy") else 0)) if complete else None
    out["Wells PE"]={"score":wells,"complete":complete,"interpretation":"PE pretest probability; pair with a validated diagnostic algorithm."}
    pk=["age_ge50","pulse_ge100","spo2_lt95","unilateral_leg_swelling","hemoptysis","recent_surgery_trauma","prior_vte","exogenous_estrogen"]
    pc=all(k in si for k in pk)
    out["PERC"]={"positive":sum(bool(si.get(k)) for k in pk) if pc else None,"complete":pc,"interpretation":"Only for clinically low-risk PE patients."}
    agep=(2 if age>=60 else 0) if age is not None else None; bpp=(2 if (sbp is not None and sbp>=140) or (dbp is not None and dbp>=90) else 0) if (sbp is not None or dbp is not None) else None
    clin=(2 if si.get("abcdn_unilateral_weakness") else 1 if si.get("abcdn_speech_without_weakness") else 0) if ("abcdn_unilateral_weakness" in si or "abcdn_speech_without_weakness" in si) else None
    dur=n(si.get("tia_duration_min")); durp=(2 if dur>=60 else 1 if dur>=10 else 0) if dur is not None else None; dp=1 if si.get("diabetes") else 0 if "diabetes" in si else None
    ab=[agep,bpp,clin,durp,dp]
    out["ABCD2"]={"score":sum(ab) if all(x is not None for x in ab) else None,"complete":all(x is not None for x in ab),"interpretation":"TIA risk aid; do not delay urgent stroke assessment."}
    gb=None
    if all(x is not None for x in [urea,hb,sbp,age]) and all(k in si for k in ["melena","syncope","hepatic_disease","cardiac_failure"]):
        sex=(d.get("sex") or "").lower(); ub=0 if urea<6.5 else 2 if urea<8 else 3 if urea<10 else 4 if urea<25 else 6
        hp=(0 if hb>=13 else 1 if hb>=12 else 3 if hb>=10 else 6) if sex=="male" else (0 if hb>=12 else 1 if hb>=10 else 6)
        sp=0 if sbp>=110 else 1 if sbp>=100 else 2 if sbp>=90 else 3; ap=0 if age<60 else 1 if age<70 else 2
        gb=ub+hp+sp+ap+(1 if hr is not None and hr>=100 else 0)+(1 if si.get("melena") else 0)+(2 if si.get("syncope") else 0)+(2 if si.get("hepatic_disease") else 0)+(2 if si.get("cardiac_failure") else 0)
    out["Glasgow-Blatchford"]={"score":gb,"complete":gb is not None,"interpretation":"Upper GI bleeding risk aid."}
    return out

def _ai_client():
    key=os.getenv("OPENAI_API_KEY")
    if not key or OpenAI is None:
        return None
    return OpenAI(api_key=key)

def _ai_call(prompt, image_data_url=None, model=None):
    client=_ai_client()
    if client is None:
        return None, "OPENAI_API_KEY is not configured on the server."
    model=model or os.getenv("OPENAI_MODEL","gpt-6-sol")
    content=[{"type":"input_text","text":prompt}]
    if image_data_url:
        content.append({"type":"input_image","image_url":image_data_url,"detail":"high"})
    try:
        r=client.responses.create(model=model, input=[{"role":"user","content":content}])
        return _clean_json(r.output_text), None
    except Exception as e:
        return None, f"AI request failed: {type(e).__name__}: {str(e)[:300]}"

@app.route("/api/scores", methods=["POST"])
def api_scores():
    return jsonify({"scores":calculate_clinical_scores(request.json or {})})

@app.route("/api/ai/assessment", methods=["POST"])
def ai_assessment():
    d=request.json or {}
    patient={
      "age":d.get("age"),"sex":d.get("sex"),"patient_type":d.get("patient_type"),
      "chief_complaint":d.get("chief_complaint"),"history":d.get("history",{}),
      "examination":d.get("examination",{}),"vitals":d.get("vitals",{}),
      "news2":d.get("news2"),"risk":d.get("risk"),"red_flags":d.get("red_flags",{}),
      "investigations":d.get("investigations",[]),"score_inputs":d.get("score_inputs",{})
    }
    patient["clinical_scores"]=calculate_clinical_scores(d)
    prompt="""You are an emergency-medicine clinical decision-support assistant. Analyze the supplied patient data as a senior ED physician would. This is NOT autonomous diagnosis or prescribing. Do not invent missing findings. Explicitly separate documented facts from inference.

Return ONLY valid JSON with exactly these keys:
working_diagnosis: {diagnosis, confidence, reasoning}
differential_diagnoses: [{diagnosis, likelihood: "high|moderate|low", supporting_features: [], contradictory_or_missing_features: [], discriminator: ""}]
red_flags_and_must_not_miss: [{condition, why_it_matters, immediate_check}]
initial_treatment: [{priority, action, rationale, dose_or_target: ""}]
recommended_investigations: [{test, reason, urgency: "immediate|urgent|routine"}]
monitoring: []
disposition: {suggestion, criteria, escalation_triggers: []}
missing_information: []
clinical_uncertainty: ""

Rules: identify the dominant syndrome and specific symptom pattern before naming the diagnosis. Use onset, time course, location/radiation, provoking/relieving factors, associated symptoms, examination, age/sex, vitals, NEWS2, red flags and investigations together. Give ONE best specific provisional diagnosis when evidence supports it; avoid generic labels such as “ACS”, “sepsis” or “abdominal pain” when a more specific syndrome is justified. Then rank 3-6 differentials by likelihood and danger, with supporting features, contradictory/missing features and the best discriminator for each. Explicitly include must-not-miss diagnoses. Use only complete calculated scores; never invent missing score components and state incomplete scores. For chest pain consider ACS/MI, aortic dissection, PE, pneumothorax, pericarditis/tamponade and esophageal rupture when relevant. For dyspnea distinguish asthma/COPD exacerbation, pulmonary edema, pneumonia, PE, pneumothorax and upper-airway disease. For fever distinguish focal infection, sepsis with organ dysfunction and noninfectious mimics. For abdominal pain localize by symptom pattern and consider surgical/vascular causes. For neurologic symptoms distinguish ischemic stroke/TIA, ICH, seizure and mimics. Initial treatment must be clinician-verified ED stabilization and syndrome-specific guidance; avoid dosing when required patient parameters are missing.

PATIENT DATA:\n"""+json.dumps(patient, ensure_ascii=False)
    result,err=_ai_call(prompt)
    if err:return jsonify({"error":err,"ai_available":False}),503
    return jsonify({"ai_available":True,"result":result})

@app.route("/api/ai/ecg", methods=["POST"])
def ai_ecg():
    d=request.json or {}
    image=d.get("image_data_url")
    if not image or not str(image).startswith("data:image/"):
        return jsonify({"error":"Upload a readable ECG image first."}),400
    context={k:d.get(k) for k in ["age","sex","chief_complaint","history","vitals"]}
    prompt="""You are an ECG-focused emergency-medicine decision-support assistant. Inspect the attached 12-lead ECG image systematically. Never invent measurements that are not visible. If the image is cropped, blurred, rotated, missing leads, or calibration is not visible, state that limitation.

Return ONLY valid JSON with exactly these keys:
image_quality: {adequate: true/false, limitations: []}
rate_bpm: {value: null, confidence: "high|moderate|low", method: ""}
rhythm: {interpretation: "", confidence: "high|moderate|low"}
axis: {interpretation: "", confidence: "high|moderate|low"}
intervals: {PR_ms: null, QRS_ms: null, QTc_ms: null, confidence: "high|moderate|low"}
conduction: []
chamber_or_hypertrophy: []
q_waves: []
st_segment: {findings: [], territories: [], STEMI_or_occlusion_concern: "none|possible|high"}
t_wave: []
other_critical_findings: []
provisional_ecg_diagnosis: ""
differential_ecg_diagnoses: []
urgent_action: []
comparison_needed: ""
uncertainty: ""

Use standard 12-lead ECG reasoning: rate, rhythm, axis, intervals, QRS morphology, R-wave progression, Q waves, ST elevation/depression, T-wave changes, QT/QTc, blocks, pre-excitation and dangerous patterns. If acute coronary occlusion is possible, describe the leads/territory and reciprocal changes if visible, but do not overcall STEMI from artifact or poor-quality images. Distinguish STEMI-equivalent/occlusion patterns only when supported. This is decision support, not a definitive ECG report; clinician verification is mandatory.

CLINICAL CONTEXT:\n"""+json.dumps(context, ensure_ascii=False)
    result,err=_ai_call(prompt,image)
    if err:return jsonify({"error":err,"ai_available":False}),503
    return jsonify({"ai_available":True,"result":result})


init()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=5000,debug=False)


@app.route("/api/disease-scores", methods=["POST"])
def api_disease_scores():
    payload=request.get_json(silent=True) or {}
    return jsonify({"scores": disease_specific_scores(payload)})

