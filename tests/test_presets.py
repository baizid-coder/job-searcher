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
    def test_get_presets_returns_all_providers(self):
        presets = model_presets.get_presets()
        groq_presets = [p for p in presets if p["provider_id"] == "groq"]
        self.assertTrue(len(groq_presets) > 0, "Should have Groq presets")
        
        openrouter_presets = [p for p in presets if p["provider_id"] == "openrouter"]
        self.assertTrue(len(openrouter_presets) > 0, "Should have OpenRouter presets")
    
    def test_get_default_preset_returns_groq(self):
        default = model_presets.get_default_preset()
        self.assertIsNotNone(default, "Should have a default preset")
        self.assertEqual(default["provider_id"], "groq", "Should default to Groq")
        self.assertEqual(default["model_id"], "llama-3.3-70b-versatile")
    
    def test_resolve_preset_succeeds_for_valid_preset(self):
        resolved = model_presets.resolve_preset("groq", "llama-3.3-70b-versatile")
        self.assertIsNotNone(resolved, "Should resolve valid preset")
        self.assertEqual(resolved["base_url"], "https://api.groq.com/openai/v1")
        self.assertEqual(resolved["model"], "llama-3.3-70b-versatile")
    
    def test_resolve_preset_fails_for_invalid_provider(self):
        resolved = model_presets.resolve_preset("invalid", "model")
        self.assertIsNone(resolved, "Should not resolve invalid provider")
    
    def test_resolve_preset_fails_for_invalid_model(self):
        resolved = model_presets.resolve_preset("groq", "invalid-model")
        self.assertIsNone(resolved, "Should not resolve invalid model")


class PresetSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "jobfinder.db"
        db.init()
    
    def tearDown(self):
        self.tmp.cleanup()
    
    def test_save_preset_settings_with_key(self):
        result = db.save_preset_settings("groq", "llama-3.3-70b-versatile", "test-key")
        self.assertTrue(result.get("use_preset"), "Should use preset")
        self.assertTrue(result.get("configured"), "Should be configured with key")
        
        settings = db.get_settings()
        self.assertEqual(settings["preset_provider"], "groq")
        self.assertEqual(settings["preset_model"], "llama-3.3-70b-versatile")
        self.assertTrue(settings["use_preset"])
        self.assertEqual(settings["api_key"], "test-key")
    
    def test_save_preset_settings_without_key(self):
        result = db.save_preset_settings("groq", "llama-3.3-70b-versatile")
        self.assertTrue(result.get("use_preset"), "Should use preset")
        self.assertFalse(result.get("configured"), "Should not be configured without key")
    
    def test_save_advanced_settings_disables_preset(self):
        # First set a preset
        db.save_preset_settings("groq", "llama-3.3-70b-versatile", "key")
        
        # Then use advanced settings
        db.save_settings("http://localhost:11434/v1", "llama3.2", "key", False)
        
        settings = db.get_settings()
        self.assertFalse(settings["use_preset"], "Should disable preset when using advanced")
    
    def test_public_settings_with_preset_and_key(self):
        db.save_preset_settings("groq", "llama-3.3-70b-versatile", "test-key")
        
        public = db.public_settings()
        self.assertTrue(public["configured"], "Should be configured")
        self.assertTrue(public["use_preset"], "Should use preset")
        self.assertEqual(public["preset_provider"], "groq")
        self.assertEqual(public["base_url"], "https://api.groq.com/openai/v1")
        self.assertTrue(public["has_key"], "Should have key")
    
    def test_public_settings_with_preset_without_key(self):
        db.save_preset_settings("groq", "llama-3.3-70b-versatile")
        
        public = db.public_settings()
        self.assertFalse(public["configured"], "Should not be configured without key")
        self.assertTrue(public["use_preset"], "Should use preset")
        self.assertFalse(public["has_key"], "Should not have key")


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
        response = self.client.get("/api/presets")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("presets", data)
        presets = data["presets"]
        self.assertTrue(len(presets) > 0, "Should have presets")
        
        groq_presets = [p for p in presets if p["provider_id"] == "groq"]
        self.assertTrue(len(groq_presets) > 0, "Should have Groq presets")
    
    def test_save_preset_endpoint_with_key(self):
        response = self.client.put("/api/settings/preset", json={
            "provider_id": "groq",
            "model_id": "llama-3.3-70b-versatile",
            "api_key": "test-key"
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["configured"], "Should be configured")
        self.assertTrue(data["use_preset"], "Should use preset")
        self.assertTrue(data["has_key"], "Should have key")
    
    def test_save_preset_endpoint_without_key(self):
        response = self.client.put("/api/settings/preset", json={
            "provider_id": "groq",
            "model_id": "llama-3.3-70b-versatile"
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["configured"], "Should not be configured without key")
        self.assertTrue(data["use_preset"], "Should use preset")
        self.assertFalse(data["has_key"], "Should not have key")
    
    def test_chat_with_preset_requires_key(self):
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


if __name__ == "__main__":
    unittest.main()
