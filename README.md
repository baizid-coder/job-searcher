# Job finder

A local page that searches Bdjobs and public remote boards, ranks the listings against your requirements, and lets you review an edited CV before you apply yourself.

## Run

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app
```

Open http://127.0.0.1:8000

## Chat model

The chat and the CV summary use an OpenAI-compatible model. Search, the form, and a plain CV still work if you do not connect one.

Ollama, on this PC: install Ollama, run `ollama pull llama3.2`, then in Settings set the base URL to `http://127.0.0.1:11434/v1` and the model to `llama3.2`.

A hosted model: set its base URL, model name, and API key in Settings. The key stays in the local database on this computer.

## What a search does

Bdjobs is queried with your job titles. Himalayas, Jobicy, Remotive, and Remote OK contribute their latest public listings, which this app then filters. Each card names the source and links back to that board. Apply opens the company page when one is available, or the board page, and stores the CV you edited. It does not submit the application.
