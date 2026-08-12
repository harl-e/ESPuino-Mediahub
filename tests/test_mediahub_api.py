import importlib
import sys


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
    module.store.touch_device("esp-1", "127.0.0.1", "004123087255", "2026-08-11T10:00:00+00:00")
    module.store.save_assignment(
        "esp-1",
        "004123087255",
        "Story",
        "files",
        3,
        None,
        [{"path": "Folder/01.mp3", "size": 123, "sha256": "a" * 64}],
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
    assert body["files"] == [{"path": "Folder/01.mp3", "size": 123, "sha256": "a" * 64}]
    assert len(body["version"]) == 64


def test_force_refresh_changes_version_without_content_change(tmp_path, monkeypatch):
    module = load_app(tmp_path, monkeypatch)
    module.store.touch_device("esp-1", "127.0.0.1", "004123087255", "2026-08-11T10:00:00+00:00")
    module.store.save_assignment(
        "esp-1",
        "004123087255",
        "Story",
        "files",
        3,
        None,
        [{"path": "Folder/01.mp3", "size": 123, "sha256": "a" * 64}],
    )
    client = module.app.test_client()

    before = client.get("/esp-1/card/004123087255/manifest.json").get_json()
    module.store.bump_force_epoch("esp-1", "004123087255")
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
