# One-Click Model Picker Feature

## Overview

Added a one-click model picker that eliminates the need for users to manually enter base URLs and model names. Users can now select from preset free models and paste their API key once, or choose "Advanced / Custom" for Ollama and other endpoints.

---

## What Was Built

### 1. Model Presets System

**File:** `app/model_presets.py`

Provides preset configurations for three providers:

**Groq (Default, Free)**
- Llama 3.3 70B Versatile ⭐ **Default**
- Llama 3.1 70B Versatile
- Mixtral 8x7B

**OpenRouter (Free)**
- Gemini 2.0 Flash (Free)
- Gemini 1.5 Flash
- Llama 3.2 3B (Free)

**Google Gemini (Free)**
- Gemini 2.0 Flash
- Gemini 1.5 Flash

### 2. API Key Storage

API keys are entered by users in the Settings UI and stored locally in the SQLite database:

- ✅ **No typing** of base URLs or model names for presets
- ✅ **One-time entry** of API key per provider
- ✅ **Saved locally** in the app's database (never committed to repo)
- ✅ **Key hint** shown (last 4 characters) after saving

### 3. Database Changes

**File:** `app/db.py`

Added fields to settings:
- `preset_provider` - The selected provider (groq, openrouter, gemini)
- `preset_model` - The selected model ID
- `use_preset` - Whether using preset (true) or advanced mode (false)
- `api_key` - User's API key (works for both preset and advanced modes)

New functions:
- `save_preset_settings()` - Save preset selection and optional API key
- Updated `public_settings()` - Resolves preset and checks if key is available

### 4. LLM Integration

**File:** `app/llm.py`

Updated to use API key from settings (works for both preset and advanced mode):
- Checks `settings["api_key"]` for authorization header
- Uses preset's auto-filled base_url and model
- Simple and consistent

### 5. API Endpoints

**File:** `app/main.py`

New endpoints:
- `GET /api/presets` - List all presets
- `PUT /api/settings/preset` - Save preset selection with optional API key

Existing endpoint preserved:
- `PUT /api/settings` - Save advanced/custom settings (disables preset mode)

### 6. Frontend UI

**File:** `app/static/index.html`

Added:
- Model picker dropdown grouped by provider
- Shows "(Free)" tag for free models
- "Advanced / Custom" option reveals manual base URL and model fields
- **API key field always visible** for entering/updating keys
- Auto-hides base URL/model fields when preset is selected
- Shows key hint after saving (e.g., "A key is saved (····sk_1234)")

### 7. Tests

**File:** `tests/test_presets.py`

New tests covering:
- Preset resolution (valid/invalid providers and models)
- Default preset selection (Groq Llama 3.3 70B)
- Settings save/retrieve with and without API keys
- API endpoints
- Chat behavior requiring API key

**All tests pass** (original + new preset tests)

### 8. Documentation

**File:** `README.md`

Added section explaining:
- One-click presets
- How to get API keys
- Free models available
- Fallback to advanced mode

---

## How API Keys Are Supplied

### For Personal Use (Recommended)

1. **Get an API key** from your chosen provider:
   - **Groq** (recommended, fast & free): https://console.groq.com/
   - **OpenRouter** (free models available): https://openrouter.ai/keys
   - **Google Gemini** (free with quota): https://aistudio.google.com/app/apikey

2. **Enter it once** in Settings:
   - Open Settings
   - Select a preset (e.g., "Groq - Llama 3.3 70B Versatile")
   - Paste your API key in the API key field
   - Click "Save settings"
   - Done! The key is saved locally on your PC.

3. **For advanced/custom endpoints** (Ollama, etc.):
   - Choose "Advanced / Custom (Ollama, etc.)"
   - Enter base URL and model name
   - Optionally enter API key (if needed)
   - Click "Save settings"

### No Keys Committed

- ✅ No API keys in code
- ✅ API keys only in local database (not in repo)
- ✅ Keys never committed to git
- ✅ Safe for personal use

---

## Available Presets

### Groq ⭐ Default
- **Base URL:** https://api.groq.com/openai/v1
- **Models:** Llama 3.3 70B, Llama 3.1 70B, Mixtral 8x7B
- **Free:** Yes
- **Speed:** Very fast
- **Get Key:** https://console.groq.com/

### OpenRouter
- **Base URL:** https://openrouter.ai/api/v1
- **Models:** Gemini 2.0 Flash (Free), Gemini 1.5 Flash, Llama 3.2 3B (Free)
- **Free:** Yes (selected models)
- **Get Key:** https://openrouter.ai/keys

### Google Gemini
- **Base URL:** https://generativelanguage.googleapis.com/v1beta/openai
- **Models:** Gemini 2.0 Flash, Gemini 1.5 Flash
- **Free:** Yes (with quota)
- **Get Key:** https://aistudio.google.com/app/apikey

---

## UI Flow

### First Time Setup
1. Start the app
2. Open Settings (gear icon)
3. See "Groq" presets in dropdown (default)
4. Paste your Groq API key in the API key field
5. Click "Save settings"
6. Start chatting! 🎉

### Selecting a Different Preset
1. Open Settings
2. Choose from dropdown (e.g., "Gemini 2.0 Flash (Free)")
3. Paste that provider's API key (if different)
4. Click "Save settings"
5. Chat with new model

### Using Advanced Mode (Ollama, etc.)
1. Open Settings
2. Select "Advanced / Custom (Ollama, etc.)"
3. Manual fields appear (base URL, model)
4. Enter your custom endpoint details
5. Optionally enter API key
6. Click "Save settings"
7. Works same as before

---

## Test Results

### Status

✅ **All tests pass** (original + new preset tests)

**What Was Tested:**
- ✅ Preset resolution works
- ✅ Default preset returns Groq Llama 3.3 70B
- ✅ Base URL and model are set correctly by presets
- ✅ Settings save and retrieve with API keys
- ✅ Chat requires API key for presets
- ✅ API endpoints work correctly
- ✅ Advanced mode still works

### Live API Verification

⚠️ **Live Groq API reply not verified** by the assistant.

**Reason:** No API key was provided to the VM for testing.

**Owner should verify:**
1. Get a Groq API key from https://console.groq.com/
2. Start the app locally
3. Open Settings
4. Select "Groq - Llama 3.3 70B Versatile (Free)" (should be default)
5. Paste your API key
6. Click "Save settings"
7. Send a chat message
8. Should get a real reply from Llama 3.3 70B ✨

---

## Changes Summary

### New Files
- `app/model_presets.py` - Preset configurations
- `tests/test_presets.py` - Preset tests
- `MODEL_PICKER_SUMMARY.md` - This document

### Modified Files
- `app/db.py` - Preset storage and resolution
- `app/llm.py` - Simplified API key handling
- `app/main.py` - Preset endpoints
- `app/static/index.html` - Model picker UI
- `README.md` - Documentation

### All on Branch
`cursor/combine-open-prs-0197` - PR #8

**Not merged to master, original PRs not closed** ✅

---

## Security Notes

### Safe Storage
- API keys are stored in local SQLite database (`jobfinder.db`)
- Database is in `.gitignore` and never committed
- Keys stay on your PC, never go to GitHub

### For Developers
- Never commit API keys to code
- Never hardcode keys in source files
- Database file is automatically excluded from git

---

## Future Enhancements (Not Implemented)

- Key validation UI (test connection before saving)
- Multiple saved keys (switch between providers easily)
- Usage tracking per provider
- Model capability badges (vision, function calling, etc.)
- More providers (Anthropic, Cohere, etc.)

---

## Conclusion

✅ **Feature Complete**
- One-click model selection working
- API keys entered once in UI
- Saved locally (never committed)
- Groq as fast free default
- Honest empty state preserved
- All tests passing
- Documented

⚠️ **Live API verification pending** - Owner should test with real API key

📝 **PR:** [#8](https://github.com/baizid-coder/job-searcher/pull/8)

🔒 **Security:** API keys stored locally, never committed
