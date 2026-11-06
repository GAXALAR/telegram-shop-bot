import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tg_bot_shop_mulrigoods as bot


class CatalogAdminTests(unittest.TestCase):
    def test_default_catalog_and_update(self):
        default = bot.build_default_catalog()
        self.assertIn("win10", default)
        self.assertIn("netflix", default)

        temp_dir = Path(tempfile.mkdtemp())
        catalog_path = temp_dir / "catalog.json"
        item = {"id": "testitem", "name": "Test item", "price": 555, "file": "testitem.txt"}

        bot.save_catalog(catalog_path, {item["id"]: item})
        loaded = bot.load_catalog(catalog_path)
        self.assertEqual(loaded[item["id"]]["price"], 555)

        updated = bot.upsert_product(catalog_path, "newitem", "Новый товар", 789)
        self.assertEqual(updated["newitem"]["price"], 789)

        self.assertTrue(bot.remove_product(catalog_path, "newitem"))
        self.assertTrue(bot.remove_product(catalog_path, "testitem"))
        self.assertEqual(bot.load_catalog(catalog_path), {})

    def test_load_settings_reads_external_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                '{"BOT_TOKEN": "test-bot-token", "PAYMENT_TOKEN": "test-payment-token", "ADMIN_ID": 12345}',
                encoding="utf-8",
            )
            with patch.object(bot, "CONFIG_FILE", config_path):
                self.assertEqual(
                    bot.load_settings(),
                    ("test-bot-token", "test-payment-token", 12345),
                )

    def test_public_product_button_does_not_show_stock(self):
        label = bot.public_product_button_text({"name": "Тестовый товар", "price": 250})
        self.assertIn("Тестовый товар", label)
        self.assertIn("250 ₽", label)
        self.assertNotIn("ост.", label)

    def test_save_welcome_settings_preserves_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(
                '{"BOT_TOKEN": "test-bot-token", "PAYMENT_TOKEN": "test-payment-token", "ADMIN_ID": 12345}',
                encoding="utf-8",
            )
            with patch.object(bot, "CONFIG_FILE", config_path), patch.object(bot, "WELCOME_TEXT", "Новое приветствие"), patch.object(bot, "WELCOME_IMAGE_PATH", None):
                bot.save_welcome_settings()

            saved_settings = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_settings["BOT_TOKEN"], "test-bot-token")
            self.assertEqual(saved_settings["WELCOME_TEXT"], "Новое приветствие")
            self.assertEqual(saved_settings["WELCOME_IMAGE"], "")


if __name__ == "__main__":
    unittest.main()
