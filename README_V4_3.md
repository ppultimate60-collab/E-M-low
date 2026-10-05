# ED Flow V4.3 — Render Ready

## Features
- Functional Flask + SQLite ED workflow.
- Dashboard, new patient, previous patients.
- No standalone/open Scores tab.
- Contextual scores appear only within the patient assessment/vitals context and only when relevant to the selected presentation: NEWS2, GCS, qSOFA, CURB-65, HEART and Wells PE.
- Deep Clinical Reasoning using the OpenAI Responses API with high reasoning effort.
- AI output: most likely diagnosis, why, differential diagnosis, must-not-miss diagnoses, initial ER management, investigations, disposition and missing information.
- AI is clinician-facing decision support and does not replace bedside assessment or local protocols.
- `/health` endpoint for deployment checks.

## Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`gunicorn --bind 0.0.0.0:$PORT app:app`

Environment variables:
- `OPENAI_API_KEY` = your OpenAI API key (server-side only)
- `OPENAI_MODEL` = `gpt-5.6-sol` by default; change if your API account uses another supported reasoning model.

Do not put the API key in `index.html`.
