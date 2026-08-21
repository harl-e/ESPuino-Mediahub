"""Regression coverage for tag-centred assignments and dynamic folders."""
import json
import os
import importlib
import sys
import tempfile
import unittest

import manifest
from store import Store


class TagModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.media = os.path.join(self.tmp.name, "media")
        os.mkdir(self.media)
        self.store = Store(os.path.join(self.tmp.name, "data"))

    def tearDown(self): self.tmp.cleanup()

    def save(self, esp=None, global_=False, name="test", folders=None, files=None):
        return self.store.save_assignment("123456789012", esp, global_, name, "files", 5, None, folders or [], files or ["one.mp3"])

    def test_global_override_and_explicit_resolution(self):
        self.save(global_=True, name="global")
        self.save("ESP_A", name="override")
        _, assignment, source = self.store.resolve("ESP_A", "123456789012")
        self.assertEqual((assignment["name"], source), ("override", "override"))
        _, assignment, source = self.store.resolve("ESP_B", "123456789012")
        self.assertEqual((assignment["name"], source), ("global", "global"))
        self.store.delete_card("123456789012")
        self.save("ESP_A", name="explicit")
        self.assertIsNone(self.store.resolve("ESP_B", "123456789012")[1])

    def test_folders_expand_dynamically_and_deduplicate(self):
        os.mkdir(os.path.join(self.media, "Album"))
        for filename in ("01.mp3", "02.mp3"):
            with open(os.path.join(self.media, "Album", filename), "wb") as f: f.write(filename.encode())
        self.save(global_=True, folders=["Album"], files=["Album/01.mp3"])
        assignment = self.store.resolve("ESP", "123456789012")[1]
        paths = [f["path"] for f in manifest.resolve_files(assignment, self.media, 3)]
        self.assertEqual(paths, ["Album/01.mp3", "Album/02.mp3"])
        with open(os.path.join(self.media, "Album", "03.mp3"), "wb") as f: f.write(b"three")
        self.assertEqual([f["path"] for f in manifest.resolve_files(assignment, self.media, 3)], ["Album/01.mp3", "Album/02.mp3", "Album/03.mp3"])

    def test_legacy_migration_is_conservative_and_idempotent(self):
        data_dir = os.path.join(self.tmp.name, "legacy"); os.mkdir(data_dir)
        old = {"settings": {}, "devices": {}, "cards": {
            "ESP_A/123456789012": {"esp_id": "ESP_A", "card_id": "123456789012", "status": "assigned", "name": "A", "kind": "files", "play_mode": 5, "files": [{"path": "a.mp3", "size": 1, "sha256": "x"}], "legacy_parameter": "preserved"},
            "ESP_B/123456789012": {"esp_id": "ESP_B", "card_id": "123456789012", "status": "assigned", "name": "B", "kind": "files", "play_mode": 5, "files": [{"path": "b.mp3", "size": 1, "sha256": "x"}]},
        }}
        with open(os.path.join(data_dir, "db.json"), "w", encoding="utf8") as f: json.dump(old, f)
        store = Store(data_dir); tag = store.get_tag("123456789012")
        self.assertIsNone(tag["default_assignment"])
        self.assertEqual(store.resolve("ESP_A", tag["card_id"])[1]["files"], ["a.mp3"])
        self.assertEqual(store.resolve("ESP_A", tag["card_id"])[1]["legacy_fields"]["legacy_parameter"], "preserved")
        self.assertEqual(store.resolve("ESP_B", tag["card_id"])[1]["files"], ["b.mp3"])
        self.assertEqual(Store(data_dir).get_tag("123456789012")["overrides"], tag["overrides"])

    def _load_app(self, database=None):
        data_dir = os.path.join(self.tmp.name, "ui-data")
        media_dir = os.path.join(self.tmp.name, "ui-media")
        os.mkdir(data_dir)
        os.mkdir(media_dir)
        if database is not None:
            with open(os.path.join(data_dir, "db.json"), "w", encoding="utf-8") as f:
                json.dump(database, f)
        old_data, old_media = os.environ.get("MEDIAHUB_DATA"), os.environ.get("MEDIAHUB_MEDIA")
        os.environ["MEDIAHUB_DATA"], os.environ["MEDIAHUB_MEDIA"] = data_dir, media_dir
        sys.modules.pop("app", None)
        module = importlib.import_module("app")
        module.app.config.update(TESTING=True)
        if old_data is None: os.environ.pop("MEDIAHUB_DATA", None)
        else: os.environ["MEDIAHUB_DATA"] = old_data
        if old_media is None: os.environ.pop("MEDIAHUB_MEDIA", None)
        else: os.environ["MEDIAHUB_MEDIA"] = old_media
        return module

    def test_detail_route_opens_migrated_global_override_and_pending_tags(self):
        old = {"settings": {}, "devices": {}, "cards": {
            "ESP_A/123456789012": {"esp_id": "ESP_A", "card_id": "123456789012", "status": "assigned", "name": "A", "kind": "files", "play_mode": 5, "files": []},
            "ESP_B/123456789012": {"esp_id": "ESP_B", "card_id": "123456789012", "status": "assigned", "name": "B", "kind": "files", "play_mode": 5, "files": []},
        }}
        module = self._load_app(old)
        client = module.app.test_client()
        self.assertEqual(client.get("/cards/123456789012").status_code, 200)
        module.store.save_assignment("987654321098", None, True, "Global", "files", 5, None, [], [])
        module.store.save_assignment("987654321098", "ESP_A", False, "Override", "files", 5, None, [], [])
        module.store.register_pending("ESP_B", "111111111111", "2026-08-21T12:00:00+00:00")
        self.assertEqual(client.get("/cards/987654321098").status_code, 200)
        self.assertEqual(client.get("/cards/111111111111").status_code, 200)

    def test_detail_route_normalizes_cardid_alias_in_early_v2_data(self):
        early_v2 = {"schema_version": 2, "settings": {}, "devices": {}, "cards": {
            "legacy-map-key": {"cardId": "123456789012", "status": "pending", "name": "Transitional", "default_assignment": None, "overrides": {}, "seen_by": {}},
        }}
        module = self._load_app(early_v2)
        self.assertEqual(module.app.test_client().get("/cards/123456789012").status_code, 200)


if __name__ == "__main__": unittest.main()
