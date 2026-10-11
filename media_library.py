"""Read-only access to the media library under MEDIA_DIR.

MediaHub never copies or uploads audio files — the admin mounts their own
existing library into the container (see docker-compose.yml) and only
*points* cards at files/folders already there (concept: the hub is the
source of truth for playMode and file list, not for the bytes themselves).
This module resolves browser-supplied relative paths safely against the
library root and lists/hashes files on demand.
"""

import hashlib
from collections import OrderedDict
import json
import os


def _hash_cache_path():
    # Cache lives under MEDIAHUB_DATA (writable), not the read-only media
    # mount. Resolved lazily so importing this module never touches the FS.
    import os as _os
    data_dir = _os.environ.get("MEDIAHUB_DATA", "/data")
    return _os.path.join(data_dir, "hash-cache.json")

# Extensions the ESPuino firmware actually recognizes as playable audio
# (see `fileValid()` / `audioFileSufix` in src/SdCard.cpp). Playlist formats
# (.m3u, .pls, ...) are deliberately excluded — MediaHub doesn't support
# LOCAL_M3U (see concept §7.2); selecting a whole folder already covers the
# "multiple tracks" case.
AUDIO_EXTENSIONS = {".mp3", ".aac", ".m4a", ".wav", ".flac", ".ogg", ".oga", ".opus"}


def is_audio_file(name):
    return os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS


def resolve(media_root, relpath):
    """Resolves a browser-supplied relative path against media_root.

    Returns the absolute path, or None if it would escape media_root
    (traversal attempt) or media_root itself doesn't exist.
    """
    media_root = os.path.realpath(media_root)
    relpath = (relpath or "").strip().strip("/")
    candidate = os.path.realpath(os.path.join(media_root, relpath))
    if candidate != media_root and not candidate.startswith(media_root + os.sep):
        return None
    return candidate


def list_directory(media_root, relpath):
    """Lists one directory level: subfolders and audio files, dirs first,
    both natural-sorted case-insensitively. Returns None if relpath doesn't
    resolve to a directory inside media_root.
    """
    abs_path = resolve(media_root, relpath)
    if abs_path is None or not os.path.isdir(abs_path):
        return None

    dirs, files = [], []
    try:
        with os.scandir(abs_path) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(entry.name)
                    elif entry.is_file(follow_symlinks=False) and is_audio_file(entry.name):
                        files.append(entry.name)
                except OSError:
                    # Individual entry unreadable (permissions, broken symlink, ...) — skip it,
                    # don't fail the whole listing over one bad entry.
                    continue
    except OSError:
        # Directory itself not readable/traversable by the container's user.
        return None

    dirs.sort(key=str.casefold)
    files.sort(key=str.casefold)
    return {"dirs": dirs, "files": files}


def list_audio_files_in_directory(media_root, relpath):
    """Relative paths (media_root-rooted) of all direct audio file children
    of relpath, sorted. Used for the "use this whole folder" selection.
    """
    listing = list_directory(media_root, relpath)
    if listing is None:
        return []
    relpath = (relpath or "").strip().strip("/")
    return [f"{relpath}/{name}" if relpath else name for name in listing["files"]]


def list_audio_files_recursive(media_root, relpath, max_depth):
    """All audio files under relpath, descending up to max_depth levels of
    subfolders (0 = relpath's own files only). Used for "use folder" when
    the card's play mode is one of the recursive ones (manifest.py
    RECURSIVE_PLAY_MODES) — mirrors what the ESPuino itself would recurse
    into, capped so a huge library can't produce an unbounded manifest.
    """
    results = []

    def walk(rel, depth_left):
        listing = list_directory(media_root, rel)
        if listing is None:
            return
        base = rel.strip("/")
        for name in listing["files"]:
            results.append(f"{base}/{name}" if base else name)
        if depth_left <= 0:
            return
        for name in listing["dirs"]:
            child = f"{base}/{name}" if base else name
            walk(child, depth_left - 1)

    walk(relpath, max_depth)
    results.sort()
    return results


# Process-wide SHA-256 cache: (path, size, mtime_ns) -> digest. A cached
# entry is only reused while both the size and the nanosecond mtime are
# unchanged, so replacing a file in the library invalidates it immediately.
# Backed by a JSON file under MEDIAHUB_DATA so a container restart doesn't
# force a full re-hash of the library on the next manifest build. Bounded LRU:
# the oldest entries are evicted first instead of dropping the whole cache.
_HASH_CACHE = OrderedDict()
_HASH_CACHE_MAX = 4096
_HASH_CACHE_DIRTY = False

def _load_hash_cache():
    global _HASH_CACHE_DIRTY
    if _HASH_CACHE:
        return
    path = _hash_cache_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        if isinstance(entries, dict):
            for key, digest in entries.items():
                if isinstance(key, str) and isinstance(digest, str):
                    try:
                        _HASH_CACHE[tuple(json.loads(key))] = digest
                    except (ValueError, TypeError):
                        continue
    except (OSError, ValueError):
        pass
    _HASH_CACHE_DIRTY = False

def save_hash_cache():
    """Persist the hash cache; call after manifest builds / at shutdown."""
    global _HASH_CACHE_DIRTY
    if not _HASH_CACHE_DIRTY or not _HASH_CACHE:
        return
    path = _hash_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({json.dumps(list(k)): v for k, v in _HASH_CACHE.items()}, f)
        os.replace(tmp, path)
        _HASH_CACHE_DIRTY = False
    except OSError:
        pass

def _cached_hash(abs_path, size, mtime_ns):
    global _HASH_CACHE_DIRTY
    _load_hash_cache()
    key = (abs_path, size, mtime_ns)
    digest = _HASH_CACHE.get(key)
    if digest is not None:
        try:
            _HASH_CACHE.move_to_end(key)
        except AttributeError:
            pass
        return digest
    hasher = hashlib.sha256()
    with open(abs_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    digest = hasher.hexdigest()
    while len(_HASH_CACHE) >= _HASH_CACHE_MAX:
        _HASH_CACHE.popitem(last=False)
    _HASH_CACHE[key] = digest
    _HASH_CACHE_DIRTY = True
    return digest

def stat_size(media_root, relpath):
    """Like stat_and_hash but only validates and returns the byte size -
    no SHA-256 computation. Used for aggregate statistics (index page,
    media overview) where the digest is irrelevant and hashing every
    file of every assignment would dominate the request time."""
    abs_path = resolve(media_root, relpath)
    if abs_path is None or not os.path.isfile(abs_path) or not is_audio_file(abs_path):
        return None
    try:
        return os.stat(abs_path).st_size
    except OSError:
        return None

def stat_and_hash(media_root, relpath):
    """Validates relpath is a real, readable audio file inside media_root and
    returns {"path", "size", "sha256"} (path normalized with forward
    slashes), or None if it doesn't qualify — including when the container's
    user lacks read permission on the file (directory listing and file
    reading are separate Unix permissions; a file can be visible in the
    browser yet unreadable, e.g. mode 600 owned by someone else).
    """
    abs_path = resolve(media_root, relpath)
    if abs_path is None or not os.path.isfile(abs_path) or not is_audio_file(abs_path):
        return None

    try:
        st = os.stat(abs_path)
        digest = _cached_hash(abs_path, st.st_size, st.st_mtime_ns)
    except OSError:
        return None
    normalized = os.path.relpath(abs_path, os.path.realpath(media_root)).replace(os.sep, "/")
    return {"path": normalized, "size": st.st_size, "sha256": digest}
