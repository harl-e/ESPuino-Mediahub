"""ESPuino MediaHub web UI and backwards-compatible firmware API."""
import json
import logging
import os
import secrets

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask_babel import Babel, get_locale, gettext as _, ngettext
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import manifest as manifest_lib
import media_library
import store as store_lib
from espuino_client import delete_rfid_on_device

DATA_DIR, MEDIA_DIR, LANGUAGES = os.environ.get("MEDIAHUB_DATA", "/data"), os.environ.get("MEDIAHUB_MEDIA", "/media"), ["de", "en", "fr"]
log = logging.getLogger(__name__)
app = Flask(__name__); app.config["BABEL_DEFAULT_LOCALE"] = "de"; app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
store = store_lib.Store(DATA_DIR)

def _secret_key():
    path = os.path.join(DATA_DIR, "secret.key"); os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "wb") as f: f.write(os.urandom(32))
    with open(path, "rb") as f: return f.read()
app.secret_key = _secret_key()
def _select_locale(): return session.get("lang") if session.get("lang") in LANGUAGES else request.accept_languages.best_match(LANGUAGES, "de")
babel = Babel(app, locale_selector=_select_locale)
app.jinja_env.globals["_"] = app.jinja_env.globals["gettext"]; app.jinja_env.globals["get_locale"] = get_locale
app.jinja_env.filters["shortdt"] = lambda value: value[:16].replace("T", " ") if value else value

@app.route("/lang/<lang_code>")
def set_language(lang_code):
    if lang_code in LANGUAGES: session["lang"] = lang_code
    return redirect(request.args.get("next") or url_for("index"))

PUBLIC_ENDPOINTS = {"login", "set_language", "health", "card_resolve", "card_seen", "card_manifest", "serve_media", "static"}
@app.before_request
def _csrf_protect():
    """Same-site CSRF protection for all state-changing browser requests.
    Public API endpoints (ESPuino-facing, tokenless devices) are exempt;
    same-origin HTML forms carry a per-session token in a hidden field
    (see the csrf_token context processor)."""
    if request.method == "POST" and request.endpoint not in PUBLIC_ENDPOINTS:
        token = session.get("csrf_token")
        if not token or token != request.form.get("csrf_token"):
            return "CSRF token missing or invalid.", 400

@app.before_request
def _require_login():
    if request.endpoint not in PUBLIC_ENDPOINTS and request.endpoint and store.get_settings().get("password_hash") and not session.get("authenticated"):
        return redirect(url_for("login", next=request.full_path))
@app.context_processor
def _auth(): return {"hub_password_set": bool(store.get_settings().get("password_hash"))}

@app.context_processor
def _csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return {"csrf_token": session["csrf_token"]}
@app.route("/login", methods=["GET", "POST"])
def login():
    password_hash = store.get_settings().get("password_hash"); next_url = request.values.get("next") or url_for("index")
    if not password_hash: return redirect(url_for("index"))
    if request.method == "POST" and check_password_hash(password_hash, request.form.get("password", "")):
        session["authenticated"] = True; return redirect(next_url)
    if request.method == "POST": flash(_("Incorrect password."), "error")
    return render_template("login.html", next=next_url)
@app.route("/logout", methods=["POST"])
def logout(): session.pop("authenticated", None); return redirect(url_for("index"))

def _assignment_files(assignment):
    if not assignment or assignment.get("kind") == "webradio": return []
    return manifest_lib.resolve_files(assignment, MEDIA_DIR, store.get_settings()["recursion_depth"])
def _content_label(assignment):
    if not assignment: return "—"
    if assignment.get("kind") == "webradio": return "📻 " + _("Webradio")
    sources = assignment.get("folders", []) + assignment.get("files", [])
    return ", ".join(sources[:2]) + (" …" if len(sources) > 2 else "") if sources else "—"

def _ui_state():
    """Return only list-filter query parameters, never route parameters.

    ``esp_id`` was the first filter spelling, but collides with the path
    parameter used by the override routes.  Preserve its value while emitting
    the unambiguous UI-only spelling ``espId`` for all new navigation links.
    """
    state = {key: request.args.get(key, "") for key in ("status", "search")}
    esp_id = request.args.get("espId", "") or request.args.get("esp_id", "")
    if esp_id:
        state["espId"] = esp_id
    return {key: value for key, value in state.items() if value}

def _return_cards():
    # Preserve every active filter for all normal CRUD actions.
    return redirect(url_for("cards", **_ui_state()))

@app.route("/")
def index():
    tags, devices = store.list_cards(), store.list_devices()
    return render_template("index.html", device_label=ngettext("%(num)s device", "%(num)s devices", len(devices)) % {"num": len(devices)}, pending_count=sum(t.get("status") == "pending" for t in tags.values()), assigned_count=sum(t.get("status") == "assigned" for t in tags.values()), total_bytes=sum(f["size"] for t in tags.values() for a in [t.get("default_assignment")] + list(t.get("overrides", {}).values()) if a and a.get("kind") == "files" for f in _assignment_files(a)))
@app.route("/health")
def health(): return jsonify(status="ok", service="espuino-mediahub")

@app.route("/devices")
def devices():
    ds = store.list_devices(); rows = sorted(((i, d, store.count_cards_for_device(i), _("Delete ESPuino %(esp_id)s? Existing tag assignments are retained.", esp_id=i)) for i, d in ds.items()), key=lambda r: r[1].get("last_seen", ""), reverse=True)
    return render_template("devices.html", devices=rows)
@app.route("/devices/<esp_id>/alias", methods=["POST"])
def set_device_alias(esp_id):
    if esp_id not in store.list_devices(): abort(404)
    store.set_device_alias(esp_id, request.form.get("alias", "").strip()); flash(_("Alias saved."), "success"); return redirect(url_for("devices"))
@app.route("/devices/<esp_id>/delete", methods=["POST"])
def delete_device(esp_id):
    if not store.delete_device(esp_id): abort(404)
    flash(_("Device %(esp_id)s deleted. Tag assignments were retained.", esp_id=esp_id), "success"); return redirect(url_for("devices"))

@app.route("/cards")
def cards():
    status = request.args.get("status", "")
    esp_id = request.args.get("espId", "") or request.args.get("esp_id", "")
    search = request.args.get("search", "").strip().casefold()
    devices = store.list_devices(); rows = []
    for tag in store.list_cards().values():
        assignment, source = store.resolve_assignment(tag, esp_id) if esp_id else (tag.get("default_assignment") or next(iter(tag.get("overrides", {}).values()), None), "")
        haystack = " ".join([tag.get("card_id", ""), tag.get("name", ""), *tag.get("overrides", {}).keys(), *sum(([a.get("name", ""), *a.get("folders", []), *a.get("files", [])] for a in [tag.get("default_assignment")] + list(tag.get("overrides", {}).values()) if a), [])]).casefold()
        if (status and tag.get("status") != status) or (esp_id and not assignment) or (search and search not in haystack): continue
        validity = _("All ESPuinos") if (tag.get("default_assignment") or {}).get("enabled_for_all") else ngettext("%(num)s ESPuino", "%(num)s ESPuinos", len(tag.get("overrides", {}))) % {"num": len(tag.get("overrides", {}))}
        rows.append((tag, _content_label(assignment), validity, len(tag.get("overrides", {}))))
    rows.sort(key=lambda r: r[0].get("last_seen", ""), reverse=True)
    return render_template("cards.html", cards=rows, devices=devices, status=status, esp_id_filter=esp_id, search=search, ui_state=_ui_state())

@app.route("/cards/add", methods=["POST"])
def add_card():
    card_id = request.form.get("card_id", "").strip()
    if not manifest_lib.is_valid_card_id(card_id): flash(_("Card ID must be exactly 12 digits."), "error"); return _return_cards()
    esp_id = request.form.get("esp_id", "").strip()
    if esp_id:
        if esp_id not in store.list_devices(): flash(_("Please choose a known ESPuino."), "error"); return _return_cards()
        return redirect(url_for("assign_card", card_id=card_id, esp_id=esp_id, **_ui_state()))
    return redirect(url_for("assign_card", card_id=card_id, **_ui_state()))

@app.route("/cards/<card_id>/assign", methods=["GET", "POST"])
@app.route("/cards/<card_id>/overrides/<esp_id>/assign", methods=["GET", "POST"])
def assign_card(card_id, esp_id=None):
    # Firmware IDs are always twelve digits, but an already persisted legacy
    # tag must remain viewable and editable even when its historical ID does
    # not satisfy that protocol validation. New tags still enter through
    # add_card(), which validates the format before this route is reached.
    tag = store.get_tag(card_id)
    if tag is None and not manifest_lib.is_valid_card_id(card_id): abort(404)
    tag = tag or {"card_id": str(card_id), "default_assignment": None, "overrides": {}}
    card_id = str(tag.get("card_id") or card_id)
    is_global = esp_id is None
    assignment = tag.get("default_assignment") if is_global else tag.get("overrides", {}).get(esp_id)
    assignment = assignment or {"name": tag.get("name", ""), "kind": "files", "play_mode": None, "stream_url": "", "folders": [], "files": [], "time_release_start": 0, "time_release_interval": 0, "time_release_interval_value": 0, "time_release_interval_unit": ""}
    if request.method == "POST":
        kind, name = request.form.get("kind", "files"), request.form.get("name", "").strip()
        target = None if is_global else esp_id
        if not is_global and target not in store.list_devices(): flash(_("Please choose a known ESPuino."), "error"); return _return_cards()
        if kind == "webradio":
            stream = request.form.get("stream_url", "").strip()
            if not stream: flash(_("A stream URL is required for webradio."), "error"); return _return_cards()
            store.save_assignment(card_id, target, is_global, name, kind, None, stream, [], [])
        else:
            try: play_mode = int(request.form.get("play_mode", "")); selected = json.loads(request.form.get("selected_paths_json", "{}"))
            except (ValueError, TypeError): play_mode, selected = -1, {}
            folders, files = selected.get("folders", []), selected.get("files", [])
            if not manifest_lib.is_valid_file_play_mode(play_mode): flash(_("Please choose a valid play mode."), "error"); return _return_cards()

            time_release_start, time_release_interval, time_release_interval_value, time_release_interval_unit = 0, 0, 0, ""
            if play_mode == 19:
                try: time_release_start = int(request.form.get("time_release_start", "0")); time_release_interval_value = int(request.form.get("time_release_interval_value", "0")); time_release_interval_unit = request.form.get("time_release_interval_unit", "")
                except (ValueError, TypeError): pass
                factors = {"minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800}
                if time_release_interval_unit == "months": time_release_interval = 0
                elif time_release_interval_unit in factors: time_release_interval = time_release_interval_value * factors[time_release_interval_unit]
                else: time_release_interval = int(request.form.get("time_release_interval", "0")); time_release_interval_unit = ""; time_release_interval_value = 0
                if time_release_start <= 0 or (time_release_interval_unit == "months" and time_release_interval_value <= 0) or (time_release_interval_unit != "months" and time_release_interval <= 0): flash(_("Timed release requires a start date and a positive interval."), "error"); return _return_cards()
            if not folders and not files: flash(_("At least one audio file or folder is required."), "error"); return _return_cards()
            if play_mode in manifest_lib.SINGLE_FILE_PLAY_MODES and (folders or len(files) != 1): flash(_("This play mode only supports a single file — please select just one."), "error"); return _return_cards()
            store.save_assignment(card_id, target, is_global, name, "files", play_mode, None, folders, files, time_release_start, time_release_interval, time_release_interval_value, time_release_interval_unit)
        flash(_("Card %(id)s assigned.", id=card_id), "success"); return redirect(url_for("card_detail", card_id=card_id, **_ui_state()))
    return render_template("card_form.html", card_id=card_id, card=assignment, tag=tag, esp_id=esp_id, devices=store.list_devices(), is_global=is_global, play_modes=manifest_lib.FILE_PLAY_MODES, single_file_play_modes=list(manifest_lib.SINGLE_FILE_PLAY_MODES), recursive_play_modes=list(manifest_lib.RECURSIVE_PLAY_MODES), ui_state=_ui_state())

@app.route("/cards/<card_id>")
def card_detail(card_id):
    tag = store.get_tag(card_id)
    if not tag: abort(404)
    devices = store.list_devices()
    return render_template("card_detail.html", tag=tag, devices=devices, content_label=_content_label, ui_state=_ui_state())
@app.route("/cards/<card_id>/overrides/<esp_id>/delete", methods=["POST"])
def delete_override(card_id, esp_id):
    if store.delete_override(card_id, esp_id) is None: abort(404)
    flash(_("Override deleted. This ESPuino now uses the global assignment when one exists."), "success"); return redirect(url_for("card_detail", card_id=card_id, **_ui_state()))
@app.route("/cards/<card_id>/overrides/<esp_id>/promote", methods=["POST"])
def promote_override(card_id, esp_id):
    result = store.promote_override_to_global(card_id, esp_id, request.form.get("replace_global") == "1")
    if result is None: abort(404)
    if result == "confirmation_required":
        flash(_("A global assignment already exists. Please confirm its replacement."), "error")
    else:
        flash(_("Override promoted to global assignment."), "success")
    return redirect(url_for("card_detail", card_id=card_id, **_ui_state()))
@app.route("/cards/pending/delete-all", methods=["POST"])
def delete_pending_all():
    n = store.delete_pending_cards()
    flash(_("Deleted all %(num)s pending card(s). Tapping an unknown card will register it again (unless disabled in the settings).", num=n), "success")
    return _return_cards()

@app.route("/cards/<card_id>/delete", methods=["POST"])
def delete_card(card_id):
    tag = store.get_tag(card_id)
    if not tag: abort(404)
    if store.get_settings()["delete_mode"] == "secure":
        for esp_id in tag.get("overrides", {}):
            dev = store.list_devices().get(esp_id, {})
            if not delete_rfid_on_device(dev.get("ip"), card_id): flash(_("Secure delete could not be confirmed by %(esp_id)s.", esp_id=esp_id), "error"); return _return_cards()
    store.delete_card(card_id); flash(_("Card %(id)s deleted.", id=card_id), "success"); return _return_cards()
@app.route("/cards/<card_id>/force-refresh", methods=["POST"])
def force_refresh_card(card_id):
    tag = store.get_tag(card_id)
    if not tag: abort(404)
    n = 0
    if tag.get("default_assignment"): store.bump_force_epoch(card_id); n += 1
    for esp in tag.get("overrides", {}): store.bump_force_epoch(card_id, esp); n += 1
    flash(ngettext("Force refresh triggered for %(num)s assignment.", "Force refresh triggered for %(num)s assignments.", n) % {"num": n}, "success"); return _return_cards()
@app.route("/cards/force-refresh-all", methods=["POST"])
def force_refresh_all():
    n = store.bump_force_epoch_all(); flash(_("Force refresh triggered for all %(num)s assignments.", num=n), "success"); return _return_cards()
@app.route("/cards/<card_id>/manifest-preview")
def manifest_preview(card_id):
    esp_id = request.args.get("esp_id", "")
    _, assignment, _ = store.resolve(esp_id, card_id)
    if not assignment:
        return jsonify(error="no_assignment"), 404
    return jsonify(manifest_lib.build_manifest(card_id, assignment, url_for("serve_media", filename="", _external=True), MEDIA_DIR, store.get_settings()["recursion_depth"]))

@app.route("/media")
def media_overview():
    rows = []
    for tag in store.list_cards().values():
        for source, a in [("global", tag.get("default_assignment"))] + list(tag.get("overrides", {}).items()):
            if a and a.get("kind") == "files": rows.append((tag, source, len(_assignment_files(a))))
    return render_template("media.html", rows=rows)
@app.route("/media/browse")
def media_browse():
    relpath = request.args.get("path", ""); listing = media_library.list_directory(MEDIA_DIR, relpath)
    if listing is None: abort(404)
    return jsonify(path=relpath.strip("/"), dirs=listing["dirs"], files=listing["files"])
@app.route("/media/browse-recursive")
def media_browse_recursive():
    relpath = request.args.get("path", "")
    if media_library.list_directory(MEDIA_DIR, relpath) is None: abort(404)
    return jsonify(path=relpath.strip("/"), files=media_library.list_audio_files_recursive(MEDIA_DIR, relpath, store.get_settings()["recursion_depth"]))

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        mode = request.form.get("delete_mode")
        if mode in ("lazy", "secure"): store.set_delete_mode(mode)
        # Unchecked checkboxes are absent from the POST body entirely — absence means "off".
        store.set_auto_register(request.form.get("auto_register") == "1")
        try: depth = int(request.form.get("recursion_depth", ""))
        except ValueError: depth = -1
        if not 0 <= depth <= 20: flash(_("Recursion depth must be between 0 and 20."), "error"); return redirect(url_for("settings"))
        store.set_recursion_depth(depth); flash(_("Settings saved."), "success"); return redirect(url_for("settings"))
    return render_template("settings.html", settings=store.get_settings())
@app.route("/settings/password", methods=["POST"])
def set_password():
    p, confirm = request.form.get("new_password", ""), request.form.get("confirm_password", "")
    if not p: flash(_("Please enter a password."), "error")
    elif p != confirm: flash(_("Passwords do not match."), "error")
    else: store.set_password_hash(generate_password_hash(p)); flash(_("Hub password set."), "success")
    return redirect(url_for("settings"))
@app.route("/settings/password/remove", methods=["POST"])
def remove_password(): store.set_password_hash(None); session.pop("authenticated", None); flash(_("Hub password removed."), "success"); return redirect(url_for("settings"))

@app.route("/<esp_id>/card/<card_id>/resolve")
def card_resolve(esp_id, card_id):
    if not manifest_lib.is_valid_card_id(card_id): return jsonify(error="invalid_card_id"), 400
    _, assignment, source = store.resolve(esp_id, card_id)
    log.info("Tag resolve: cardId=%s espId=%s source=%s", card_id, esp_id, source)
    return jsonify(assigned=assignment is not None, status="assigned" if assignment else "not_assigned")
@app.route("/<esp_id>/card/<card_id>/seen", methods=["POST"])
def card_seen(esp_id, card_id):
    if not manifest_lib.is_valid_card_id(card_id): return jsonify(error="invalid_card_id"), 400
    ts = store_lib.now_iso(); store.touch_device(esp_id, request.remote_addr, card_id, ts); tag, assignment, _ = store.resolve(esp_id, card_id)
    if not assignment: tag, _ = store.register_pending_if_enabled(esp_id, card_id, ts, store.get_settings().get("auto_register", True))
    else: tag = store.touch_card_seen(esp_id, card_id, ts)
    return jsonify(assigned=assignment is not None, status="assigned" if assignment else "pending", first_seen_at=(tag or {}).get("first_seen"), last_seen_at=(tag or {}).get("last_seen"))
@app.route("/<esp_id>/card/<card_id>/manifest.json")
def card_manifest(esp_id, card_id):
    if not manifest_lib.is_valid_card_id(card_id): return jsonify(error="invalid_card_id"), 400
    ts = store_lib.now_iso(); store.touch_device(esp_id, request.remote_addr, card_id, ts); tag, assignment, source = store.resolve(esp_id, card_id); was_known = tag is not None
    if not assignment:
        tag, _ = store.register_pending_if_enabled(esp_id, card_id, ts, store.get_settings().get("auto_register", True))
        if tag is None:
            return jsonify(error="unknown_card", status="ignored"), 404
        return jsonify(error="not_assigned" if was_known else "unknown_card", status="pending"), 404
    store.touch_card_seen(esp_id, card_id, ts); log.info("Tag resolve: cardId=%s espId=%s source=%s", card_id, esp_id, source)
    return jsonify(manifest_lib.build_manifest(card_id, assignment, url_for("serve_media", filename="", _external=True), MEDIA_DIR, store.get_settings()["recursion_depth"]))
@app.route("/media/<path:filename>")
def serve_media(filename): return send_from_directory(MEDIA_DIR, filename)

if __name__ == "__main__": app.run(host="0.0.0.0", port=8080, debug=True)
