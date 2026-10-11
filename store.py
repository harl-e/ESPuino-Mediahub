"""Atomic JSON storage for MediaHub's tag-centred assignment model."""

import json
import logging
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone

_lock = threading.Lock()
log = logging.getLogger(__name__)
DEFAULT_RECURSION_DEPTH = 3
SCHEMA_VERSION = 2
_DEFAULT_DB = {"schema_version": SCHEMA_VERSION, "settings": {"delete_mode": "lazy", "password_hash": None, "recursion_depth": DEFAULT_RECURSION_DEPTH, "auto_register": True}, "devices": {}, "cards": {}}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_assignment():
    return {"name": "", "kind": "files", "play_mode": None, "stream_url": None, "folders": [], "files": [], "time_release_start": 0, "time_release_interval": 0, "time_release_interval_value": 0, "time_release_interval_unit": "", "force_epoch": 0}


class Store:
    def __init__(self, data_dir):
        self.data_dir, self.db_path = data_dir, os.path.join(data_dir, "db.json")
        self._cache, self._cache_mtime_ns = None, None
        os.makedirs(data_dir, exist_ok=True)
        if not os.path.exists(self.db_path): self._write(deepcopy(_DEFAULT_DB))
        self._migrate_to_tag_schema()

    def _read(self):
        with open(self.db_path, "r", encoding="utf-8") as f: return json.load(f)

    def _read_cached(self):
        # In-memory mirror of db.json, revalidated by mtime: a stat() per read
        # instead of a full open()+json.load() on every request. Writers use
        # the atomic os.replace() in _write(), which always changes mtime, so
        # other workers/processes picking the file up stay consistent too.
        try:
            mtime_ns = os.stat(self.db_path).st_mtime_ns
        except OSError:
            return self._read()
        if self._cache is not None and self._cache_mtime_ns == mtime_ns:
            return self._cache
        data = self._read()
        self._cache, self._cache_mtime_ns = data, mtime_ns
        return data

    def _write(self, data):
        tmp_path = self.db_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        os.replace(tmp_path, self.db_path)
        try:
            self._cache, self._cache_mtime_ns = data, os.stat(self.db_path).st_mtime_ns
        except OSError:
            self._cache, self._cache_mtime_ns = data, None

    def _mutate(self, fn):
        with _lock:
            data = self._read_cached()
            result = fn(data)
            self._write(data)
            return result

    def _locked_read(self):
        # _read_cached() is safe outside the lock: the atomic os.replace() in
        # _write() means a reader either sees the old or the new complete file
        # and the mtime revalidation picks up external writes immediately.
        return self._read_cached()

    def _migrate_to_tag_schema(self):
        """Losslessly upgrade v1's esp_id/card_id rows. Old assignments are
        intentionally explicit; equal rows must never be guessed global."""
        def mutate(data):
            if data.get("schema_version", 1) >= SCHEMA_VERSION:
                # Early v2 development builds could contain ``cardId`` or a
                # non-canonical map key.  Normalise those records as well:
                # the list can render their value, but the detail route must
                # be able to look it up by exactly that value.
                normalized = 0
                cards = data.setdefault("cards", {})
                for key, tag in list(cards.items()):
                    if not isinstance(tag, dict):
                        log.warning("MediaHub: skipping corrupt v2 card %r", key)
                        continue
                    card_id = str(tag.get("card_id") or tag.get("cardId") or key.rsplit("/", 1)[-1])
                    if not card_id:
                        log.warning("MediaHub: skipping v2 card %r without card ID", key)
                        continue
                    if tag.get("card_id") != card_id:
                        tag["card_id"] = card_id
                        normalized += 1
                    if "default_assignment" not in tag and "defaultAssignment" in tag:
                        tag["default_assignment"] = tag["defaultAssignment"]
                        normalized += 1
                    # The released v2 shape uses a dictionary keyed by
                    # esp_id. Accept the earlier list-shaped draft as well
                    # so a legacy tag with no global assignment can still be
                    # rendered and edited instead of failing in Jinja.
                    overrides = tag.get("overrides") or {}
                    if isinstance(overrides, list):
                        overrides = {
                            str(item.get("esp_id") or item.get("espId")): item
                            for item in overrides if isinstance(item, dict) and (item.get("esp_id") or item.get("espId"))
                        }
                        normalized += 1
                    if isinstance(overrides, dict):
                        string_keys = {str(esp_id): assignment for esp_id, assignment in overrides.items()}
                        if string_keys != overrides:
                            overrides = string_keys
                            normalized += 1
                    else:
                        log.warning("MediaHub: invalid overrides for v2 card %s; treating as empty", card_id)
                        overrides = {}
                        normalized += 1
                    tag["overrides"] = overrides
                    tag.setdefault("default_assignment", None)
                    tag.setdefault("seen_by", {})
                    tag.setdefault("name", "")
                    if key != card_id and card_id not in cards:
                        cards[card_id] = tag
                        del cards[key]
                        normalized += 1
                return (normalized, 0)
            tags, assignments = {}, 0
            for key, old in data.get("cards", {}).items():
                card_id = str(old.get("card_id") or old.get("cardId") or key.rsplit("/", 1)[-1])
                esp_id = old.get("esp_id") or old.get("espId") or old.get("last_seen_esp_id")
                if esp_id is not None:
                    esp_id = str(esp_id)
                if not card_id:
                    log.warning("MediaHub: skipping corrupt legacy card %r (no card_id)", key); continue
                tag = tags.setdefault(card_id, {"card_id": card_id, "status": "pending", "name": old.get("name", ""), "default_assignment": None, "overrides": {}, "seen_by": {}, "first_seen": old.get("first_seen"), "last_seen": old.get("last_seen"), "created_at": old.get("created_at", now_iso()), "updated_at": old.get("updated_at", now_iso())})
                if esp_id: tag["seen_by"][esp_id] = {"first_seen": old.get("first_seen"), "last_seen": old.get("last_seen")}
                if old.get("status") == "assigned" and esp_id:
                    a = _empty_assignment()
                    a.update({k: old.get(k) for k in ("name", "kind", "play_mode", "stream_url", "force_epoch") if k in old})
                    a["files"] = [f.get("path") if isinstance(f, dict) else f for f in old.get("files", [])]
                    a["folders"] = list(old.get("folders", []))
                    known = {"esp_id", "card_id", "last_seen_esp_id", "status", "name", "kind", "play_mode", "stream_url", "force_epoch", "files", "folders", "first_seen", "last_seen", "created_at", "updated_at"}
                    extras = {k: v for k, v in old.items() if k not in known}
                    if extras: a["legacy_fields"] = extras
                    tag["overrides"][esp_id], tag["status"] = a, "assigned"
                    assignments += 1
            data["cards"], data["schema_version"] = tags, SCHEMA_VERSION
            log.info("MediaHub: migrating assignment schema v1 -> v2; migrated %d tags and %d assignments", len(tags), assignments)
            return len(tags), assignments
        self._mutate(mutate)

    def list_devices(self): return self._locked_read().get("devices", {})
    def touch_device(self, esp_id, ip, card_id, ts):
        def mutate(data):
            dev = data.setdefault("devices", {}).setdefault(esp_id, {"first_seen": ts, "alias": None})
            dev.update({"last_seen": ts, "ip": ip, "last_card_id": card_id}); return dev
        return self._mutate(mutate)
    def set_device_alias(self, esp_id, alias):
        def mutate(d):
            dev = d["devices"].get(esp_id)
            if dev is not None: dev["alias"] = alias or None
            return dev
        return self._mutate(mutate)
    def count_cards_for_device(self, esp_id): return sum(1 for tag in self.list_cards().values() if self.resolve_assignment(tag, esp_id)[0])
    def delete_device(self, esp_id): return self._mutate(lambda d: d["devices"].pop(esp_id, None) is not None)

    def list_cards(self): return self._locked_read().get("cards", {})
    def get_tag(self, card_id):
        card_id = str(card_id)
        cards = self.list_cards()
        tag = cards.get(card_id)
        if tag is not None:
            return tag
        # Defensive fallback for a manually edited/corrupt JSON file.  This
        # keeps every displayed tag navigable even before the next migration
        # pass has rewritten its map key.
        return next((tag for key, tag in cards.items() if isinstance(tag, dict) and str(tag.get("card_id") or tag.get("cardId") or key) == card_id), None)
    def get_card(self, esp_id, card_id):
        """Compatibility view for internal callers written for v1.

        New code should use :meth:`get_tag` / :meth:`resolve`; presenting an
        effective record here keeps small integrations from having to know the
        storage representation during the schema transition.
        """
        tag, assignment, _ = self.resolve(esp_id, card_id)
        if not tag: return None
        view = dict(tag)
        view.update(assignment or {})
        view["esp_id"] = esp_id
        view["status"] = "assigned" if assignment else "pending"
        view.update(tag.get("seen_by", {}).get(esp_id, {}))
        return view
    @staticmethod
    def resolve_assignment(tag, esp_id):
        if not tag: return None, "miss"
        if esp_id in tag.get("overrides", {}): return tag["overrides"][esp_id], "override"
        default = tag.get("default_assignment")
        if default and default.get("enabled_for_all"): return default, "global"
        return None, "miss"
    def resolve(self, esp_id, card_id):
        tag = self.get_tag(card_id); assignment, source = self.resolve_assignment(tag, esp_id)
        return tag, assignment, source
    def register_pending_if_enabled(self, esp_id, card_id, ts, auto_register):
        """Lookup + registration under one lock, gated on auto_register:
        returns (tag, registered); tag stays None when registration is
        disabled and the tag is unknown (ignored tap, concept §5.3)."""
        def mutate(data):
            tag = data["cards"].get(card_id)
            if tag is not None:
                seen = tag.setdefault("seen_by", {}).setdefault(esp_id, {"first_seen": ts})
                seen["last_seen"] = ts; tag["last_seen"], tag["updated_at"] = ts, ts
                return tag, False
            if not auto_register:
                return None, False
            tag = {"card_id": card_id, "status": "pending", "name": "", "default_assignment": None, "overrides": {}, "seen_by": {}, "first_seen": ts, "last_seen": ts, "created_at": ts, "updated_at": ts}
            tag["seen_by"][esp_id] = {"first_seen": ts, "last_seen": ts}
            data["cards"][card_id] = tag
            return tag, True
        return self._mutate(mutate)

    def register_pending(self, esp_id, card_id, ts):
        def mutate(data):
            tag = data["cards"].setdefault(card_id, {"card_id": card_id, "status": "pending", "name": "", "default_assignment": None, "overrides": {}, "seen_by": {}, "first_seen": ts, "last_seen": ts, "created_at": ts, "updated_at": ts})
            seen = tag.setdefault("seen_by", {}).setdefault(esp_id, {"first_seen": ts})
            seen["last_seen"] = ts; tag["last_seen"], tag["updated_at"] = ts, ts
            return tag
        return self._mutate(mutate)
    def delete_pending_cards(self):
        """Removes all tags still in 'pending' state (never assigned).
        Returns the number of entries removed."""
        def mutate(data):
            keys = [key for key, tag in data["cards"].items() if tag.get("status") == "pending"]
            for key in keys:
                del data["cards"][key]
            return len(keys)
        return self._mutate(mutate)

    def touch_card_seen(self, esp_id, card_id, ts): return self.register_pending(esp_id, card_id, ts)
    def save_assignment(self, *args):
        """Save a v2 assignment, accepting the former v1 argument order as
        a temporary compatibility shim for programmatic callers.

        v2: ``card_id, esp_id, global, name, kind, play_mode, stream, folders, files``.
        v3: v2 plus Unix start timestamp and interval seconds for TIME_RELEASE.
        v1: ``esp_id, card_id, name, kind, play_mode, stream, files``.
        """
        if len(args) == 7:
            esp_id, card_id, name, kind, play_mode, stream_url, files = args
            enabled_for_all, folders, time_release_start, time_release_interval, time_release_interval_value, time_release_interval_unit = False, [], 0, 0, 0, ""
        elif len(args) == 9:
            card_id, esp_id, enabled_for_all, name, kind, play_mode, stream_url, folders, files = args
            time_release_start, time_release_interval, time_release_interval_value, time_release_interval_unit = 0, 0, 0, ""
        elif len(args) == 11:
            card_id, esp_id, enabled_for_all, name, kind, play_mode, stream_url, folders, files, time_release_start, time_release_interval = args
            time_release_interval_value, time_release_interval_unit = 0, ""
        elif len(args) == 13:
            card_id, esp_id, enabled_for_all, name, kind, play_mode, stream_url, folders, files, time_release_start, time_release_interval, time_release_interval_value, time_release_interval_unit = args
        else:
            raise TypeError("save_assignment expects 7 (legacy), 9, 11, or 13 arguments")
        def mutate(data):
            ts = now_iso()
            tag = data["cards"].setdefault(card_id, {"card_id": card_id, "status": "pending", "name": "", "default_assignment": None, "overrides": {}, "seen_by": {}, "first_seen": ts, "last_seen": ts, "created_at": ts})
            a = _empty_assignment(); a.update({"name": name, "kind": kind, "play_mode": play_mode, "stream_url": stream_url, "folders": folders, "files": files, "time_release_start": time_release_start, "time_release_interval": time_release_interval, "time_release_interval_value": time_release_interval_value, "time_release_interval_unit": time_release_interval_unit})
            if enabled_for_all:
                a["enabled_for_all"] = True; a["force_epoch"] = (tag.get("default_assignment") or {}).get("force_epoch", 0)
                tag["default_assignment"] = a; tag["name"] = name or tag.get("name", "")
            else:
                if not esp_id: raise ValueError("An ESPuino is required for a non-global assignment")
                a["force_epoch"] = tag.get("overrides", {}).get(esp_id, {}).get("force_epoch", 0)
                tag.setdefault("overrides", {})[esp_id] = a
                if not tag.get("name"): tag["name"] = name
            tag["status"], tag["updated_at"] = "assigned", ts
            return tag
        return self._mutate(mutate)
    def delete_override(self, card_id, esp_id):
        def mutate(data):
            tag = data["cards"].get(card_id)
            if not tag: return None
            result = tag.get("overrides", {}).pop(esp_id, None)
            tag["status"] = "assigned" if tag.get("default_assignment") or tag.get("overrides") else "pending"; tag["updated_at"] = now_iso()
            return result
        return self._mutate(mutate)
    def promote_override_to_global(self, card_id, esp_id, replace_global=False):
        """Atomically make one explicit assignment the tag's global default.

        The original assignment is deep-copied so all current and future
        assignment fields survive the transition.  The selected override is
        then removed in the same write; other overrides are untouched.
        """
        def mutate(data):
            tag = data["cards"].get(card_id)
            if not tag or esp_id not in tag.get("overrides", {}): return None
            if tag.get("default_assignment") and not replace_global:
                return "confirmation_required"
            assignment = deepcopy(tag["overrides"][esp_id])
            assignment["enabled_for_all"] = True
            tag["default_assignment"] = assignment
            del tag["overrides"][esp_id]
            tag["name"] = assignment.get("name") or tag.get("name", "")
            tag["status"], tag["updated_at"] = "assigned", now_iso()
            return "promoted"
        return self._mutate(mutate)
    def bump_force_epoch(self, card_id, esp_id=None):
        def mutate(data):
            tag = data["cards"].get(card_id)
            if not tag: return None
            target = tag.get("overrides", {}).get(esp_id) if esp_id else tag.get("default_assignment")
            if target is None: return None
            target["force_epoch"] = target.get("force_epoch", 0) + 1; tag["updated_at"] = now_iso(); return target
        return self._mutate(mutate)
    def bump_force_epoch_all(self):
        def mutate(data):
            n = 0
            for tag in data["cards"].values():
                for a in [tag.get("default_assignment")] + list(tag.get("overrides", {}).values()):
                    if a: a["force_epoch"] = a.get("force_epoch", 0) + 1; n += 1
            return n
        return self._mutate(mutate)
    def delete_card(self, card_id): return self._mutate(lambda d: d["cards"].pop(card_id, None))

    def get_settings(self): return {**_DEFAULT_DB["settings"], **self._locked_read().get("settings", {})}
    def set_delete_mode(self, mode): return self._mutate(lambda d: d["settings"].update({"delete_mode": mode}) or mode)
    def set_recursion_depth(self, depth): return self._mutate(lambda d: d["settings"].update({"recursion_depth": depth}) or depth)
    def set_password_hash(self, password_hash): return self._mutate(lambda d: d["settings"].update({"password_hash": password_hash}) or password_hash)
    def set_auto_register(self, enabled): return self._mutate(lambda d: d["settings"].update({"auto_register": bool(enabled)}) or bool(enabled))
