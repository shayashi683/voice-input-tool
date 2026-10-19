import json
import os
import tempfile
import unittest
from unittest.mock import patch

from voice_input_tool import config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("OPENROUTER_API_KEY", None)

    def test_normalize_repairs_known_invalid_values(self):
        normalized = config.normalize_config(
            {"use_llm": 0, "hotkey_record": None, "llm_provider_order": "Cerebras"}
        )
        self.assertFalse(normalized["use_llm"])
        self.assertEqual(normalized["hotkey_record"], config.DEFAULTS["hotkey_record"])
        self.assertNotIn("llm_provider_order", normalized)

    def test_save_separates_api_key_from_json(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.json")
            dotenv_path = os.path.join(directory, ".env")
            values = config.normalize_config(
                {"openrouter_api_key": "router-secret", "openai_api_key": "openai-secret", "use_llm": False}
            )
            with patch.multiple(
                config,
                CONFIG_DIR=directory,
                CONFIG_PATH=config_path,
                DOTENV_PATH=dotenv_path,
            ):
                config.save_config(values)

            with open(config_path, "r", encoding="utf-8") as file_handle:
                saved = json.load(file_handle)
            with open(dotenv_path, "r", encoding="utf-8") as file_handle:
                dotenv = file_handle.read()

            self.assertNotIn("openrouter_api_key", saved)
            self.assertNotIn("openai_api_key", saved)
            self.assertNotIn("OPENROUTER_API_KEY", dotenv)
            self.assertNotIn("router-secret", dotenv)
            self.assertIn("OPENAI_API_KEY=openai-secret", dotenv)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "openai-secret")
            self.assertNotIn("OPENROUTER_API_KEY", os.environ)
            self.assertEqual(os.stat(dotenv_path).st_mode & 0o777, 0o600)

    def test_cloud_defaults_do_not_migrate_existing_users(self):
        normalized = config.normalize_config({"use_llm": False})
        self.assertEqual(normalized["asr_backend"], "reazonspeech")
        self.assertFalse(config.DEFAULTS["use_llm"])
        self.assertEqual(normalized["asr_languages"], ["ja", "en"])
        normalized["asr_languages"].append("fr")
        self.assertEqual(config.DEFAULTS["asr_languages"], ["ja", "en"])

    def test_cloud_fields_repair_invalid_types_and_backend(self):
        normalized = config.normalize_config({
            "asr_backend": "typo", "openai_api_key": 42, "asr_prompt": [],
            "asr_keywords": ["SQL", 7], "asr_languages": "ja",
        })
        for key in ("asr_backend", "openai_api_key", "asr_prompt", "asr_keywords", "asr_languages"):
            self.assertEqual(normalized[key], config.DEFAULTS[key])

    def test_api_keys_prefer_environment_then_dotenv_then_legacy_json(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.json")
            dotenv_path = os.path.join(directory, ".env")
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump({"openai_api_key": "legacy-openai", "openrouter_api_key": "legacy-router"}, handle)
            with open(dotenv_path, "w", encoding="utf-8") as handle:
                handle.write("OPENAI_API_KEY=dotenv-openai\nOPENROUTER_API_KEY=dotenv-router\n")
            with patch.multiple(config, CONFIG_PATH=config_path, DOTENV_PATH=dotenv_path):
                os.environ["OPENAI_API_KEY"] = "environment-openai"
                loaded = config.load_config()
                self.assertEqual(loaded["openai_api_key"], "environment-openai")
                self.assertNotIn("openrouter_api_key", loaded)
                self.assertNotIn("OPENROUTER_API_KEY", os.environ)
                os.environ.pop("OPENAI_API_KEY")
                loaded = config.load_config()
                self.assertEqual(loaded["openai_api_key"], "dotenv-openai")
                os.environ.pop("OPENAI_API_KEY")
                os.unlink(dotenv_path)
                loaded = config.load_config()
                self.assertEqual(loaded["openai_api_key"], "legacy-openai")
                self.assertNotIn("openrouter_api_key", loaded)

    def test_save_clears_keys_and_preserves_unrelated_dotenv_values(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.json")
            dotenv_path = os.path.join(directory, ".env")
            with open(dotenv_path, "w", encoding="utf-8") as handle:
                handle.write("# personal settings\nOPENAI_API_KEY = old\nOPENAI_API_KEY=duplicate\nOPENROUTER_API_KEY=retired\nOTHER=value\n")
            os.environ["OPENAI_API_KEY"] = "old"
            os.environ["OPENROUTER_API_KEY"] = "old-router"
            with patch.multiple(config, CONFIG_DIR=directory, CONFIG_PATH=config_path, DOTENV_PATH=dotenv_path):
                config.save_config(config.DEFAULTS)
            with open(dotenv_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "# personal settings\nOTHER=value\n")
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertNotIn("OPENROUTER_API_KEY", os.environ)

    def test_invalid_keys_and_keywords_reject_save_before_writing(self):
        for invalid in (
            {"openai_api_key": "key\nOTHER=injection"},
            {"asr_keywords": ["<bad>"]},
            {"asr_keywords": ["SQL\nMQL"]},
        ):
            with self.subTest(invalid=invalid), patch.object(config, "_atomic_write") as write:
                with self.assertRaises(ValueError):
                    config.save_config(invalid)
                write.assert_not_called()

    def test_legacy_correction_is_migrated_without_implicit_cloud_opt_in(self):
        for model, expected in (
            ("openai/gpt-oss-120b", "gpt-5.6-luna"),
            ("openai/gpt-5.6-terra", "gpt-5.6-terra"),
            ("custom/model", "gpt-5.6-luna"),
        ):
            with self.subTest(model=model):
                normalized = config.normalize_config({
                    "use_llm": True, "llm_model": model,
                    "llm_provider_order": ["Cerebras"],
                    "openrouter_api_key": "retired-secret",
                })
                self.assertEqual(normalized["llm_model"], expected)
                self.assertEqual(normalized["asr_backend"], "reazonspeech")
                self.assertFalse(normalized["use_llm"])
                self.assertEqual(normalized["openai_api_key"], "")
                self.assertNotIn("openrouter_api_key", normalized)
                self.assertNotIn("llm_provider_order", normalized)
                normalized["use_llm"] = True
                self.assertTrue(config.normalize_config(normalized)["use_llm"])

    def test_save_removes_retired_fields_from_json(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "config.json")
            dotenv_path = os.path.join(directory, ".env")
            values = {"openrouter_api_key": "retired-secret", "llm_provider_order": ["Cerebras"],
                      "llm_model": "openai/gpt-oss-120b", "use_llm": True}
            with patch.multiple(config, CONFIG_DIR=directory, CONFIG_PATH=config_path, DOTENV_PATH=dotenv_path):
                config.save_config(values)
            with open(config_path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertNotIn("openrouter_api_key", saved)
            self.assertNotIn("llm_provider_order", saved)
            self.assertFalse(saved["use_llm"])
            self.assertEqual(saved["llm_model"], "gpt-5.6-luna")

    def test_model_options_have_unique_models(self):
        models = [option["model"] for option in config.LLM_MODEL_OPTIONS]
        self.assertEqual(len(models), len(set(models)))
        self.assertIn(config.DEFAULTS["llm_model"], models)
        self.assertTrue(all("/" not in model for model in models))


if __name__ == "__main__":
    unittest.main()
