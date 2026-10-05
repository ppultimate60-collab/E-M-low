# ED Flow V4.3

## What changed
- Removed the separate/open clinical score dashboard.
- Scores are contextual and attached to the vitals/pathway only.
- NEWS2, Shock Index and GCS are visible with vitals.
- Additional contextual score signal appears only for relevant pathways (for example qSOFA for sepsis, HEART prompt for ACS).
- Added an on-demand **Deep AI Differential + ER Management** button.
- AI receives patient pathway, complaint, history, examination, vitals and red flags.
- AI returns: most likely diagnosis, why, important differentials, must-not-miss diagnoses, initial ER management, investigations and disposition.
- API key remains server-side; it is never placed in the HTML/mobile client.

## Run
```bash
pip install -r requirements_v43.txt
# Linux/macOS
export OPENAI_API_KEY="YOUR_KEY"
export OPENAI_MODEL="gpt-6-astra"
python app_v43.py
```
Windows PowerShell:
```powershell
$env:OPENAI_API_KEY="YOUR_KEY"
$env:OPENAI_MODEL="gpt-6-astra"
python app_v43.py
```
Then open `http://localhost:5000`.

## Important
This is a clinical decision-support prototype. It is not a validated EMR and must not be used as an autonomous diagnostic or treatment system. For real patient use, add authentication, role-based access, encryption, audit logs, backups, privacy/compliance controls and clinical validation.
