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

### One-Click Presets

The app includes preset models from free providers:
- **Groq**: Llama 3.3 70B, Llama 3.1 70B, Mixtral 8x7B
- **OpenRouter**: Gemini 2.0 Flash (Free), Gemini 1.5 Flash, Llama 3.2 3B (Free)  
- **Google Gemini**: Gemini 2.0 Flash, Gemini 1.5 Flash

To enable presets, set these environment variables:
```bash
export GROQ_API_KEY="your-groq-api-key"
export OPENROUTER_API_KEY="your-openrouter-api-key"
export GEMINI_API_KEY="your-google-api-key"
```

The app auto-selects the first available free model. Pick a different preset from the dropdown in Settings, or choose "Advanced / Custom" for Ollama or another endpoint.

### Custom Models

Ollama, on this PC: install Ollama, run `ollama pull llama3.2`, then in Settings choose "Advanced / Custom" and set the base URL to `http://127.0.0.1:11434/v1` and the model to `llama3.2`.

Other hosted models: choose "Advanced / Custom" and set base URL, model name, and API key. The key stays in the local database on this computer.

### Fallback

Search, saving a resume, and a template CV still work when no model is configured. The page says so instead of asking you to connect one.

## What a search does

Bdjobs is queried with your job titles. Himalayas, Jobicy, Remotive, and Remote OK contribute their latest public listings, which this app then filters. Each card names the source and links back to that board. Apply opens the company page when one is available, or the board page, and stores the CV you edited. It does not submit the application.

While the app is running, the same check runs every 8 hours and adds new listings. Leads already on the page stay there, including LinkedIn jobs you pasted. The switch under Leads turns that 8-hour check off. Search still works with the switch off, and it also leaves older leads in place.

## LinkedIn

Paste public LinkedIn job links, including ones you copied from suggestions. The app fetches those public pages and looks for an off-LinkedIn apply link. It does not take a LinkedIn password or session cookie. LinkedIn does not let third-party apps read "Jobs you might be interested in" that way. If a page is only Easy Apply or a login wall, the lead keeps the LinkedIn link and says the external portal was not public.
