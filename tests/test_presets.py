"""Tests for model presets and provider resolution."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.db as db
from app import model_presets
from app.main import app


class PresetResolutionTests(unittest.TestCase):
    def test_get_presets_shows_availability(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False):
            presets = model_presets.get_presets()
            groq_presets = [p for p in presets if p["provider_id"] == "groq"]
            self.assertTrue(len(groq_presets) > 0, "Should have Groq presets")
            self.assertTrue(all(p["available"] for p in groq_presets), "Groq presets should be available")
        
        with patch.dict(os.environ, {}, clear=True):
            presets = model_presets.get_presets()
            self.assertTrue(all(not p["available"] for p in presets), "No presets should be available without keys")
    
    def test_get_default_preset_returns_first_available(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False):
            default = model_presets.get_default_preset()
            self.assertIsNotNone(default, "Should have a default preset")
            self.assertEqual(default["provider_id"], "groq", "Should default to first available")
        
        with patch.dict(os.environ, {}, clear=True):
            default = model_presets.get_default_preset()
            self.assertIsNone(default, "Should have no default without keys")
    
    def test_resolve_preset_succeeds_with_key(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False):
            resolved = model_presets.resolve_preset("groq", "llama-3.3-70b-versatile")
            self.assertIsNotNone(resolved, "Should resolve with key")
            self.assertEqual(resolved["base_url"], "https://api.groq.com/openai/v1")
            self.assertEqual(resolved["model"], "llama-3.3-70b-versatile")
            self.assertEqual(resolved["api_key"], "test-key")
    
    def test_resolve_preset_fails_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            resolved = model_presets.resolve_preset("groq", "llama-3.3-70b-versatile")
            self.assertIsNone(resolved, "Should not resolve without key")
    
    def test_resolve_preset_fails_for_invalid_provider(self):
        resolved = model_presets.resolve_preset("invalid", "model")
        self.assertIsNone(resolved, "Should not resolve invalid provider")
    
    def test_resolve_preset_fails_for_invalid_model(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False):
            resolved = model_presets.resolve_preset("groq", "invalid-model")
            self.assertIsNone(resolved, "Should not resolve invalid model")


class PresetSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "jobfinder.db"
        db.init()
    
    def tearDown(self):
        self.tmp.cleanup()
    
    def test_save_preset_settings(self):
        result = db.save_preset_settings("groq", "llama-3.3-70b-versatile")
        self.assertTrue(result.get("use_preset"), "Should use preset")
        
        settings = db.get_settings()
        self.assertEqual(settings["preset_provider"], "groq")
        self.assertEqual(settings["preset_model"], "llama-3.3-70b-versatile")
        self.assertTrue(settings["use_preset"])
    
    def test_save_advanced_settings_disables_preset(self):
        # First set a preset
        db.save_preset_settings("groq", "llama-3.3-70b-versatile")
        
        # Then use advanced settings
        db.save_settings("http://localhost:11434/v1", "llama3.2", None, False)
        
        settings = db.get_settings()
        self.assertFalse(settings["use_preset"], "Should disable preset when using advanced")
    
    def test_public_settings_with_available_preset(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False):
            db.save_preset_settings("groq", "llama-3.3-70b-versatile")
            
            public = db.public_settings()
            self.assertTrue(public["configured"], "Should be configured")
            self.assertTrue(public["use_preset"], "Should use preset")
            self.assertEqual(public["preset_provider"], "groq")
            self.assertEqual(public["base_url"], "https://api.groq.com/openai/v1")
    
    def test_public_settings_with_unavailable_preset(self):
        with patch.dict(os.environ, {}, clear=True):
            db.save_preset_settings("groq", "llama-3.3-70b-versatile")
            
            public = db.public_settings()
            self.assertFalse(public["configured"], "Should not be configured without key")
            self.assertIn("GROQ_API_KEY", public["model_note"], "Should mention env var")
    
    def test_public_settings_auto_selects_default(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False):
            # No preset set, should auto-select
            public = db.public_settings()
            self.assertTrue(public["configured"], "Should auto-configure")
            self.assertEqual(public["preset_provider"], "groq")


class PresetAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "jobfinder.db"
        self.client = TestClient(app)
        self.client.__enter__()
    
    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()
    
    def test_list_presets_endpoint(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False):
            response = self.client.get("/api/presets")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn("presets", data)
            presets = data["presets"]
            self.assertTrue(len(presets) > 0, "Should have presets")
            
            groq_presets = [p for p in presets if p["provider_id"] == "groq"]
            self.assertTrue(all(p["available"] for p in groq_presets), "Groq should be available")
    
    def test_save_preset_endpoint(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False):
            response = self.client.put("/api/settings/preset", json={
                "provider_id": "groq",
                "model_id": "llama-3.3-70b-versatile"
            })
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["configured"], "Should be configured")
            self.assertTrue(data["use_preset"], "Should use preset")
    
    def test_chat_with_preset_requires_key(self):
        with patch.dict(os.environ, {}, clear=True):
            # Set a preset without key
            self.client.put("/api/settings/preset", json={
                "provider_id": "groq",
                "model_id": "llama-3.3-70b-versatile"
            })
            
            # Chat should return honest empty state
            response = self.client.post("/api/chat", json={"message": "test"})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertFalse(data["configured"], "Should not be configured without key")
            self.assertIn("model", data["reply"].lower(), "Should mention model needed")
            
            # Settings should show the env var requirement
            state = self.client.get("/api/state").json()
            self.assertIn("GROQ_API_KEY", state["settings"]["model_note"], "Settings should mention env var")


if __name__ == "__main__":
    unittest.main()
