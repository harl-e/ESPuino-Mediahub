"""Regression coverage for tag-centred assignments and dynamic folders."""
import json
import os
import importlib
import re
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

    def test_migrated_legacy_assignment_with_existing_nonstandard_id_is_editable(self):
        legacy_card_id = "123456789"
        old = {"settings": {}, "devices": {}, "cards": {
            "ESP_A/123456789": {"esp_id": "ESP_A", "card_id": legacy_card_id, "status": "assigned", "name": "Legacy", "kind": "files", "play_mode": 5, "files": []},
        }}
        module = self._load_app(old)
        client = module.app.test_client()
        overview = client.get("/cards")
        detail_link = re.search(r'href="([^"]*' + legacy_card_id + r'[^"]*)"', overview.get_data(as_text=True)).group(1)
        detail = client.get(detail_link)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"ESP_A", detail.data)
        self.assertEqual(client.get(f"/cards/{legacy_card_id}/overrides/ESP_A/assign").status_code, 200)

    def test_detail_and_edit_routes_keep_query_filters_out_of_legacy_card_id(self):
        legacy_card_id = "CARD_1"
        old = {"settings": {}, "devices": {}, "cards": {
            "ESP_A/CARD_1": {"esp_id": "ESP_A", "card_id": legacy_card_id, "status": "assigned", "name": "Legacy foo", "kind": "files", "play_mode": 5, "files": []},
        }}
        module = self._load_app(old)
        module.store.touch_device("ESP_A", "127.0.0.1", legacy_card_id, "2026-08-21T12:00:00+00:00")
        client = module.app.test_client()
        queries = ("", "?status=assigned", "?espId=ESP_A", "?search=CARD", "?status=assigned&espId=ESP_A&search=foo", "?status=assigned&esp_id=ESP_A&search=foo")
        for query in queries:
            self.assertEqual(client.get(f"/cards/{legacy_card_id}{query}").status_code, 200, query)

        detail = client.get(f"/cards/{legacy_card_id}?status=assigned&espId=ESP_A&search=foo")
        self.assertIn(b"/cards?status=assigned&amp;search=foo&amp;espId=ESP_A", detail.data)
        edit_url = f"/cards/{legacy_card_id}/overrides/ESP_A/assign?status=assigned&espId=ESP_A&search=foo"
        response = client.post(edit_url, data={"kind": "webradio", "name": "Legacy foo", "stream_url": "http://radio.example/stream.mp3"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/cards/CARD_1?status=assigned&search=foo&espId=ESP_A")

    def test_all_tag_shapes_open_with_combined_query_filters(self):
        module = self._load_app()
        module.store.save_assignment("GLOBAL", None, True, "Global", "files", 5, None, [], [])
        module.store.save_assignment("OVERRIDE", None, True, "Global", "files", 5, None, [], [])
        module.store.save_assignment("OVERRIDE", "ESP_A", False, "Override", "files", 5, None, [], [])
        module.store.register_pending("ESP_A", "PENDING", "2026-08-21T12:00:00+00:00")
        client = module.app.test_client()
        for card_id in ("GLOBAL", "OVERRIDE", "PENDING"):
            self.assertEqual(client.get(f"/cards/{card_id}?status=assigned&espId=ESP_A&search=foo").status_code, 200)

    def test_promote_override_to_global_preserves_assignment_and_other_overrides(self):
        self.save("ESP_A", name="X", folders=["Album"], files=["Album/01.mp3"])
        self.save("ESP_B", name="Y", folders=["Other"], files=["Other/01.mp3"])
        self.store._mutate(lambda data: data["cards"]["123456789012"]["overrides"]["ESP_A"].update({"legacy_fields": {"retained": True}, "custom": ["all", "fields"]}))
        original = self.store.get_tag("123456789012")["overrides"]["ESP_A"]

        self.assertEqual(self.store.promote_override_to_global("123456789012", "ESP_A"), "promoted")
        tag = self.store.get_tag("123456789012")
        expected = dict(original)
        expected["enabled_for_all"] = True
        self.assertEqual(tag["default_assignment"], expected)
        self.assertNotIn("ESP_A", tag["overrides"])
        self.assertEqual(tag["overrides"]["ESP_B"]["name"], "Y")
        self.assertEqual(self.store.resolve("ESP_A", "123456789012")[2], "global")
        self.assertEqual(self.store.resolve("ESP_B", "123456789012")[2], "override")
        self.assertEqual(self.store.resolve("ESP_C", "123456789012")[1]["name"], "X")

    def test_promote_override_requires_confirmation_before_replacing_global(self):
        self.save(global_=True, name="G")
        self.save("ESP_A", name="X")
        self.save("ESP_B", name="Y")
        self.assertEqual(self.store.promote_override_to_global("123456789012", "ESP_A"), "confirmation_required")
        tag = self.store.get_tag("123456789012")
        self.assertEqual(tag["default_assignment"]["name"], "G")
        self.assertIn("ESP_A", tag["overrides"])

        self.assertEqual(self.store.promote_override_to_global("123456789012", "ESP_A", replace_global=True), "promoted")
        tag = self.store.get_tag("123456789012")
        self.assertEqual(tag["default_assignment"]["name"], "X")
        self.assertNotIn("ESP_A", tag["overrides"])
        self.assertEqual(tag["overrides"]["ESP_B"]["name"], "Y")

    def test_promote_route_preserves_filters_and_manifest_resolution(self):
        module = self._load_app()
        with open(os.path.join(module.MEDIA_DIR, "x.mp3"), "wb") as f: f.write(b"x")
        module.store.save_assignment("123456789012", "ESP_A", False, "X", "files", 5, None, [], ["x.mp3"])
        client = module.app.test_client()
        url = "/cards/123456789012/overrides/ESP_A/promote?status=assigned&espId=ESP_A&search=foo"
        response = client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/cards/123456789012?status=assigned&search=foo&espId=ESP_A")
        self.assertNotIn("ESP_A", module.store.get_tag("123456789012")["overrides"])
        manifest_response = client.get("/cards/123456789012/manifest-preview?esp_id=ESP_A")
        self.assertEqual(manifest_response.status_code, 200)
        self.assertEqual([entry["path"] for entry in manifest_response.get_json()["files"]], ["x.mp3"])

    def test_manifest_preview_uses_effective_assignment_and_folder_expansion(self):
        module = self._load_app()
        album = os.path.join(module.MEDIA_DIR, "Album")
        os.mkdir(album)
        for filename in ("01.mp3", "02.mp3"):
            with open(os.path.join(album, filename), "wb") as f: f.write(filename.encode())
        with open(os.path.join(module.MEDIA_DIR, "override.mp3"), "wb") as f: f.write(b"override")
        module.store.save_assignment("123456789012", None, True, "Global", "files", 5, None, ["Album"], ["Album/01.mp3"])
        module.store.save_assignment("123456789012", "ESP_A", False, "Override", "files", 5, None, [], ["override.mp3"])
        client = module.app.test_client()

        global_manifest = client.get("/cards/123456789012/manifest-preview")
        override_manifest = client.get("/cards/123456789012/manifest-preview?esp_id=ESP_A")
        inherited_manifest = client.get("/cards/123456789012/manifest-preview?esp_id=ESP_B")

        self.assertEqual(global_manifest.status_code, 200)
        self.assertEqual([f["path"] for f in global_manifest.get_json()["files"]], ["Album/01.mp3", "Album/02.mp3"])
        self.assertEqual([f["path"] for f in override_manifest.get_json()["files"]], ["override.mp3"])
        self.assertEqual([f["path"] for f in inherited_manifest.get_json()["files"]], ["Album/01.mp3", "Album/02.mp3"])

    def test_manifest_preview_reports_missing_explicit_assignment(self):
        module = self._load_app()
        module.store.save_assignment("123456789012", "ESP_A", False, "Explicit", "files", 5, None, [], ["missing.mp3"])
        response = module.app.test_client().get("/cards/123456789012/manifest-preview?esp_id=ESP_B")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "no_assignment"})


if __name__ == "__main__": unittest.main()
