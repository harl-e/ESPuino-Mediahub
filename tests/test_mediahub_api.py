import importlib
import json
import os
import sys
from pathlib import Path


def csrf_post(client, url, data=None):
    """POST with the session CSRF token (all browser POSTs require it)."""
    response = client.get("/settings")
    token = None
    for line in response.data.decode().split('"'):
        pass
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', response.data.decode())
    data = dict(data or {})
    data["csrf_token"] = m.group(1)
    return client.post(url, data=data)


def load_app(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    monkeypatch.setenv("MEDIAHUB_DATA", str(data_dir))
    monkeypatch.setenv("MEDIAHUB_MEDIA", str(media_dir))
    sys.modules.pop("app", None)
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True)
    return module


def write_audio(module, relpath, payload=b"audio"):
    path = os.path.join(module.MEDIA_DIR, *relpath.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(payload)
    return module.media_library.stat_and_hash(module.MEDIA_DIR, relpath)


def file_assignment_form(audio, play_mode=3, **time_release):
    form = {
        "kind": "files",
        "name": "Existing card",
        "play_mode": str(play_mode),
        "selected_paths_json": json.dumps({"folders": [], "files": [audio]}),
    }
    form.update(time_release)
    return form


def test_timed_release_form_uses_existing_form_rows_and_all_units():
    template = (Path(__file__).resolve().parents[1] / "templates" / "card_form.html").read_text(encoding="utf-8")
    for unit in ("minutes", "hours", "days", "weeks", "months"):
        assert f'<option value="{unit}"' in template
    assert template.count('class="form-row"') >= 4
    assert "style.display = active ? 'contents' : 'none'" in template
    assert "time_release_start_date" in template
    assert "time_release_start_time" in template
    assert "var units = [604800, 86400, 3600, 60]" in template
    assert "savedInterval % unit === 0" in template


def test_all_file_play_modes_round_trip_without_time_release_fields(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    audio = write_audio(module, "Folder/01.mp3")
    client = module.app.test_client()
    card_id = "004123087255"
    form = {
        "kind": "files", "name": "Existing card", "selected_paths_json": json.dumps({"folders": [], "files": [audio]}),
    }

    for play_mode, _ in module.manifest_lib.FILE_PLAY_MODES:
        data = dict(form, play_mode=str(play_mode))
        if play_mode == 19:
            data.update(time_release_start="1796079600", time_release_interval="86400")
        response = csrf_post(client, f"/cards/{card_id}/assign", data=data)
        assert response.status_code == 302
        assignment = module.store.get_tag(card_id)["default_assignment"]
        assert assignment["play_mode"] == play_mode
        edit_page = client.get(f"/cards/{card_id}/assign").get_data(as_text=True)
        assert f'<option value="{play_mode}" selected>' in edit_page
        if play_mode == 19:
            assert assignment["time_release_start"] == 1796079600
            assert assignment["time_release_interval"] == 86400
        else:
            assert assignment["time_release_start"] == 0
            assert assignment["time_release_interval"] == 0


def test_assign_route_handles_legacy_assignment_without_time_release_fields(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    client = module.app.test_client()
    card_id = "004123087255"
    audio = write_audio(module, "Folder/01.mp3")
    module.store.save_assignment(card_id, None, True, "Old card", "files", 3, None, [], [audio])

    def remove_time_release_fields(data):
        assignment = data["cards"][card_id]["default_assignment"]
        for field in ("time_release_start", "time_release_interval", "time_release_interval_value", "time_release_interval_unit"):
            assignment.pop(field, None)
    module.store._mutate(remove_time_release_fields)

    assert client.get(f"/cards/{card_id}/assign").status_code == 200
    assert csrf_post(client, f"/cards/{card_id}/assign", data=file_assignment_form(audio)).status_code == 302
    assert csrf_post(client, f"/cards/{card_id}/assign", data=file_assignment_form(audio, play_mode=4)).status_code == 302
    assert module.store.get_tag(card_id)["default_assignment"]["play_mode"] == 4


def test_assign_route_handles_new_card_and_time_release_intervals(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    client = module.app.test_client()
    audio = write_audio(module, "Calendar/01.mp3")

    new_card_id = "004123087256"
    assert client.get(f"/cards/{new_card_id}/assign").status_code == 200
    assert csrf_post(client, f"/cards/{new_card_id}/assign", data=file_assignment_form(audio)).status_code == 302

    seconds_card_id = "004123087257"
    module.store.save_assignment(seconds_card_id, None, True, "Seconds", "files", 19, None, [], [audio], 1_796_079_600, 86_400)
    assert client.get(f"/cards/{seconds_card_id}/assign").status_code == 200
    assert csrf_post(client, f"/cards/{seconds_card_id}/assign", data=file_assignment_form(audio, 19, time_release_start="1796079600", time_release_interval="86400", time_release_interval_value="1", time_release_interval_unit="days")).status_code == 302

    months_card_id = "004123087258"
    module.store.save_assignment(months_card_id, None, True, "Months", "files", 19, None, [], [audio], 1_796_079_600, 0, 1, "months")
    assert client.get(f"/cards/{months_card_id}/assign").status_code == 200
    assert csrf_post(client, f"/cards/{months_card_id}/assign", data=file_assignment_form(audio, 19, time_release_start="1796079600", time_release_interval="0", time_release_interval_value="1", time_release_interval_unit="months")).status_code == 302
    assignment = module.store.get_tag(months_card_id)["default_assignment"]
    assert assignment["time_release_interval"] == 0
    assert assignment["time_release_interval_value"] == 1
    assert assignment["time_release_interval_unit"] == "months"


def test_unknown_file_play_mode_is_rejected(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    client = module.app.test_client()
    response = csrf_post(client, "/cards/004123087255/assign", data={
        "kind": "files", "name": "Invalid", "play_mode": "99", "selected_paths_json": '{"folders": [], "files": ["x"]}',
    })
    assert response.status_code == 302
    assert module.store.get_tag("004123087255") is None


def test_unknown_card_registers_pending_pair(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)

    response = module.app.test_client().get("/esp-1/card/004123087255/manifest.json")

    assert response.status_code == 404
    assert response.get_json() == {"error": "unknown_card", "status": "pending"}
    card = module.store.get_card("esp-1", "004123087255")
    assert card["status"] == "pending"
    assert module.store.list_devices()["esp-1"]["last_card_id"] == "004123087255"


def test_invalid_card_id_is_rejected_without_pending_side_effect(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)

    response = module.app.test_client().get("/esp-1/card/not-a-card/manifest.json")

    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid_card_id"}
    assert module.store.list_cards() == {}
    assert module.store.list_devices() == {}


def test_files_manifest_contains_expected_contract_fields(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    expected = write_audio(module, "Folder/01.mp3")
    module.store.touch_device("esp-1", "127.0.0.1", "004123087255", "2026-08-11T10:00:00+00:00")
    module.store.save_assignment(
        "esp-1",
        "004123087255",
        "Story",
        "files",
        3,
        None,
        [expected],
    )

    response = module.app.test_client().get(
        "/esp-1/card/004123087255/manifest.json",
        headers={"Host": "hub.example", "X-Forwarded-Proto": "https"},
    )

    body = response.get_json()
    assert response.status_code == 200
    assert body["schema"] == 1
    assert body["cardId"] == "004123087255"
    assert body["playMode"] == 3
    assert body["filesBaseUrl"] == "https://hub.example/media/"
    assert body["files"] == [expected]
    assert len(body["version"]) == 64


def test_timed_release_manifest_carries_local_release_metadata(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    expected = write_audio(module, "Calendar/01.mp3")
    module.store.save_assignment(
        "004123087255", None, True, "Calendar", "files", 19, None, [], [expected], 1_796_079_600, 86_400,
    )
    body = module.app.test_client().get("/esp-1/card/004123087255/manifest.json").get_json()
    assert body["playMode"] == 19
    assert body["timeReleaseStart"] == 1_796_079_600
    assert body["timeReleaseInterval"] == 86_400


def test_monthly_timed_release_manifest_keeps_value_and_unit(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    expected = write_audio(module, "Calendar/01.mp3")
    module.store.save_assignment(
        "004123087255", None, True, "Calendar", "files", 19, None, [], [expected], 1_796_079_600, 0, 1, "months",
    )
    body = module.app.test_client().get("/esp-1/card/004123087255/manifest.json").get_json()
    assert body["timeReleaseInterval"] == 0
    assert body["timeReleaseIntervalValue"] == 1
    assert body["timeReleaseIntervalUnit"] == "months"


def test_force_refresh_changes_version_without_content_change(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    expected = write_audio(module, "Folder/01.mp3")
    module.store.touch_device("esp-1", "127.0.0.1", "004123087255", "2026-08-11T10:00:00+00:00")
    module.store.save_assignment(
        "esp-1",
        "004123087255",
        "Story",
        "files",
        3,
        None,
        [expected],
    )
    client = module.app.test_client()

    before = client.get("/esp-1/card/004123087255/manifest.json").get_json()
    module.store.bump_force_epoch("004123087255", "esp-1")
    after = client.get("/esp-1/card/004123087255/manifest.json").get_json()

    assert before["version"] != after["version"]
    assert {k: v for k, v in before.items() if k != "version"} == {
        k: v for k, v in after.items() if k != "version"
    }


def test_webradio_manifest_has_stream_and_no_files(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    module.store.touch_device("esp-1", "127.0.0.1", "004123087255", "2026-08-11T10:00:00+00:00")
    module.store.save_assignment(
        "esp-1",
        "004123087255",
        "Radio",
        "webradio",
        None,
        "http://radio.example/stream.mp3",
        [],
    )

    body = module.app.test_client().get("/esp-1/card/004123087255/manifest.json").get_json()

    assert body["schema"] == 1
    assert body["cardId"] == "004123087255"
    assert body["playMode"] == 8
    assert body["stream"] == "http://radio.example/stream.mp3"
    assert "files" not in body
    assert "filesBaseUrl" not in body


def test_media_browse_rejects_path_traversal(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)

    response = module.app.test_client().get("/media/browse?path=../")

    assert response.status_code == 404


def test_auto_register_setting_gates_pending_registration(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    client = module.app.test_client()
    module.store.set_auto_register(False)
    response = client.get("/esp-1/card/004123087255/manifest.json")
    assert response.status_code == 404
    assert response.get_json() == {"error": "unknown_card", "status": "ignored"}
    assert module.store.list_cards() == {}
    assert module.store.list_devices()["esp-1"]["last_card_id"] == "004123087255"
    module.store.set_auto_register(True)
    response = client.get("/esp-1/card/004123087255/manifest.json")
    assert response.get_json() == {"error": "unknown_card", "status": "pending"}
    assert module.store.get_tag("004123087255")["status"] == "pending"


def test_settings_form_toggles_auto_register(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    client = module.app.test_client()
    assert module.store.get_settings()["auto_register"] is True
    # realistic browser POST: unchecked checkbox is absent from the body
    assert csrf_post(client, "/settings", data={"delete_mode": "lazy", "recursion_depth": "3"}).status_code == 302
    assert module.store.get_settings()["auto_register"] is False
    assert csrf_post(client, "/settings", data={"delete_mode": "lazy", "recursion_depth": "3", "auto_register": "1"}).status_code == 302
    assert module.store.get_settings()["auto_register"] is True


def test_delete_all_pending_removes_only_pending_tags(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    client = module.app.test_client()
    audio = write_audio(module, "Folder/01.mp3")
    client.get("/esp-1/card/004123087255/manifest.json")
    module.store.save_assignment("004123087256", None, True, "Kept", "files", 3, None, [], [audio])
    client.get("/esp-2/card/004123087257/manifest.json")
    assert csrf_post(client, "/cards/pending/delete-all").status_code == 302
    assert module.store.get_tag("004123087255") is None
    assert module.store.get_tag("004123087257") is None
    assert module.store.get_tag("004123087256")["status"] == "assigned"


def test_browser_post_without_csrf_token_is_rejected(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    client = module.app.test_client()
    assert client.post("/cards/pending/delete-all").status_code == 400
    # ESPuino-facing endpoints stay tokenless
    assert client.get("/esp-1/card/004123087255/manifest.json").status_code == 404
