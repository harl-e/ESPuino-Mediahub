"""Builds the per-card manifest (concept §7), including `version` hashing.

Per the concept, `version` is an "opaque change stamp = SHA-256 of the
canonical manifest, computed on the hub" — the ESPuino only ever compares
strings. We therefore hash on every request (manifests are tiny, this
costs nothing) rather than carrying a cached value that could go stale.
`force_epoch` feeds into the hash but never into the output — that's the
"force refresh" lever from §9: content unchanged, hash still new.
"""

import hashlib
import json
import logging
import re

from flask_babel import gettext as _

WEBSTREAM_PLAY_MODE = 8
log = logging.getLogger(__name__)

# SINGLE_TRACK / SINGLE_TRACK_LOOP (values.h) play back exactly one file —
# unlike e.g. SINGLE_TRACK_OF_DIR_RANDOM, which needs a whole folder as the
# pool to pick from. The card assignment UI and its server-side validation
# both use this to cap selection to a single file for these two modes.
SINGLE_FILE_PLAY_MODES = {1, 2}

# AUDIOBOOK_RECURSIVE / ALL_TRACKS_OF_DIR_SORTED_RECURSIVE /
# ALL_TRACKS_OF_DIR_RANDOM_RECURSIVE (values.h) descend into subfolders on
# the ESPuino. "Use folder" in the library browser mirrors that by
# gathering files recursively (capped by the configurable recursion depth,
# concept/settings) instead of just the folder's direct children.
RECURSIVE_PLAY_MODES = {15, 16, 17}

# The ESPuino always builds a card ID from exactly 4 UID bytes formatted as
# "%03d" each (see cardIdSize/cardIdStringSize in src/Rfid.h) — always 12
# decimal digits, never hex.
CARD_ID_PATTERN = re.compile(r"^\d{12}$")


def is_valid_card_id(card_id):
    return bool(CARD_ID_PATTERN.match(card_id))

# Playmodes that make sense as an assignment target (values.h) — WEBSTREAM
# and the MediaHub marker itself are deliberately excluded: WEBSTREAM is set
# via the "webradio" content type, not the dropdown; NO_PLAYLIST/BUSY are not
# real targets; LOCAL_M3U is not supported per the concept. Labels follow the
# terminology already used in values.h and are translated at render time via
# gettext in the template.
FILE_PLAY_MODES = [
    (1, "Single track"),
    (2, "Single track (loop)"),
    (3, "Audiobook (remembers position)"),
    (4, "Audiobook (loop)"),
    (16, "Audiobook, recursive (remembers position)"),
    (5, "All tracks of a folder (sorted)"),
    (6, "All tracks of a folder (shuffled)"),
    (7, "All tracks of a folder (sorted, loop)"),
    (9, "All tracks of a folder (shuffled, loop)"),
    (17, "All tracks, recursive (shuffled)"),
    (15, "All tracks, recursive (sorted)"),
    (12, "Single random track of a folder"),
    (13, "Random subfolder (sorted)"),
    (14, "Random subfolder (shuffled)"),
    (19, "Timed release"),
]

_FILE_PLAY_MODE_VALUES = {value for value, _label in FILE_PLAY_MODES}


def is_valid_file_play_mode(play_mode):
    return play_mode in _FILE_PLAY_MODE_VALUES


def _translation_placeholders():  # pragma: no cover
    """Never called at runtime. `pybabel extract` only picks up string
    literals passed directly to `_()`; the template renders these labels
    via a variable (`{{ _(label) }}`), which a static extractor can't
    resolve. This mirrors the FILE_PLAY_MODES labels above so `pybabel
    extract` still finds them for translation.
    """
    return [
        _("Single track"),
        _("Single track (loop)"),
        _("Audiobook (remembers position)"),
        _("Audiobook (loop)"),
        _("Audiobook, recursive (remembers position)"),
        _("All tracks of a folder (sorted)"),
        _("All tracks of a folder (shuffled)"),
        _("All tracks of a folder (sorted, loop)"),
        _("All tracks of a folder (shuffled, loop)"),
        _("All tracks, recursive (shuffled)"),
        _("All tracks, recursive (sorted)"),
        _("Single random track of a folder"),
        _("Random subfolder (sorted)"),
        _("Random subfolder (shuffled)"),
        _("Timed release"),
    ]


def _canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def resolve_paths(assignment, media_dir, recursion_depth):
    """Expand folder sources to a deduplicated, ordered list of relative
    file paths - no stat/hash I/O. Shared by resolve_files() and callers
    that only need paths or sizes (UI statistics)."""
    import media_library  # avoids importing file-system code for webradio
    paths, seen = [], set()
    recursive = assignment.get("play_mode") in RECURSIVE_PLAY_MODES
    for folder in assignment.get("folders", []):
        folder_paths = (media_library.list_audio_files_recursive(media_dir, folder, recursion_depth)
                        if recursive else media_library.list_audio_files_in_directory(media_dir, folder))
        if not folder_paths:
            log.warning("MediaHub: assigned folder is missing, unreadable, or contains no audio: %s", folder)
        for path in folder_paths:
            if path not in seen: paths.append(path); seen.add(path)
    for path in assignment.get("files", []):
        path = path.get("path") if isinstance(path, dict) else path
        if path not in seen: paths.append(path); seen.add(path)
    return paths


def resolve_files(assignment, media_dir, recursion_depth):
    """Expand stored folder sources only when creating a manifest.

    Assignment JSON deliberately contains paths, not a frozen file listing.
    Folders preserve their configured order, then explicit files are appended;
    an ordered set removes overlaps without changing playlist order.
    """
    import media_library
    files = []
    for path in resolve_paths(assignment, media_dir, recursion_depth):
        info = media_library.stat_and_hash(media_dir, path)
        if info is None:
            log.warning("MediaHub: assigned audio file is missing or unreadable: %s", path)
        else:
            files.append(info)
    return files


def build_manifest(card_id, card, files_base_url, media_dir=None, recursion_depth=0):
    """Builds the public manifest (with `version`) for an assigned card."""
    if card["kind"] == "webradio":
        body = {
            "schema": 1,
            "cardId": card_id,
            "playMode": WEBSTREAM_PLAY_MODE,
            "name": card.get("name") or "",
            "stream": card["stream_url"],
        }
    else:
        body = {
            "schema": 1,
            "cardId": card_id,
            "playMode": card["play_mode"],
            "name": card.get("name") or "",
            "filesBaseUrl": files_base_url,
            "files": [
                {"path": f["path"], "size": f["size"], "sha256": f["sha256"]}
                for f in (resolve_files(card, media_dir, recursion_depth) if media_dir else card.get("files", []))
            ],
        }
        if card["play_mode"] == 19:
            body["timeReleaseStart"] = card.get("time_release_start", 0)
            body["timeReleaseInterval"] = card.get("time_release_interval", 0)
            if card.get("time_release_interval_unit"):
                body["timeReleaseIntervalValue"] = card.get("time_release_interval_value", 0)
                body["timeReleaseIntervalUnit"] = card["time_release_interval_unit"]

    hashed = dict(body)
    hashed["_forceEpoch"] = card.get("force_epoch", 0)
    version = hashlib.sha256(_canonical_json(hashed)).hexdigest()

    return {"version": version, **body}
