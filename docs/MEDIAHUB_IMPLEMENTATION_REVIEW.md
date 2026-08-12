# MediaHub Implementation Review

Stand: 2026-08-11

Dieses Review vergleicht `README.md`, `docs/mediahub-konzept.md` und die aktuelle Implementierung im MediaHub-Repository. Der Fokus liegt auf der Server-/Web-UI-Seite; Firmware-Arbeiten gehoeren ins ESPuino-Repository.

## Kurzfazit

Der MediaHub ist als Flask-Anwendung konzeptnah umgesetzt. Die fuehrenden Architekturentscheidungen sind im Code wiederzufinden: lokale Flask-App ohne Frameworkwechsel, Medienbibliothek read-only unter `/media`, Zuweisungen per `(espId, cardId)`, Pending-Registrierung beim Manifest-Abruf, opake `version` inklusive `force_epoch`, optionaler Web-UI-Passwortschutz und offene ESPuino-Endpunkte.

Die groessten urspruenglichen offenen Punkte auf MediaHub-Seite waren fehlende automatisierte Tests, eine fehlende harte Card-ID-Validierung am Manifest-Endpunkt, kleinere veraltete UI-Texte zur frueheren Upload-Idee und noch nicht dokumentierte Beispielrequests fuer die Firmware/API-Verifikation. Diese Punkte wurden in Phase C/D bearbeitet; verbleibende Risiken liegen vor allem bei Hardware-/Integrationsfaellen, die ohne echten ESPuino nicht verifiziert werden koennen.

## Systematischer Soll-Ist-Abgleich

| Funktion | Status | Nachweis / Bemerkung |
| --- | --- | --- |
| Lokaler Hub, keine Cloud | vollstaendig umgesetzt | `Dockerfile`, `docker-compose.yml`, README Quick Start |
| Flask statt neuer Architektur | vollstaendig umgesetzt | `app.py`, `requirements.txt`; kein FastAPI/Django/React/Vue |
| Medienbibliothek statt Upload | vollstaendig umgesetzt | `MEDIAHUB_MEDIA`, `/media/browse`, `media_library.py`, read-only Compose-Mount |
| Geraeteerkennung | vollstaendig umgesetzt | `card_manifest()` ruft `store.touch_device(esp_id, ip, card_id, ts)` |
| Geraete-Alias | vollstaendig umgesetzt | `Store.set_device_alias()`, `templates/devices.html` |
| Geraeteloeschung inkl. lokaler Cascade im Hub | vollstaendig umgesetzt | `Store.delete_device()` loescht betroffene Hub-Zuweisungen |
| `(espId, cardId)` als Identitaet | vollstaendig umgesetzt | `store._card_key()`, alle Card-Routen enthalten `esp_id` und `card_id` |
| Legacy-Migration von cardId-only | vollstaendig umgesetzt | `Store._migrate_legacy_cards()` |
| Pending-Karten | vollstaendig umgesetzt | unbekannte Karte wird `pending` und mit 404 beantwortet |
| Karten-Zuweisung | vollstaendig umgesetzt | `assign_card()`, `Store.save_assignment()` |
| Manuelles Anlegen fuer bekanntes ESPuino | vollstaendig umgesetzt | `/cards/add` verlangt vorhandenes Geraet |
| Duplicate auf anderes ESPuino | vollstaendig umgesetzt | `duplicate_card()`, `static/js/duplicate-card.js` |
| Dateiauswahl | vollstaendig umgesetzt | `media-browser.js`, `/media/browse`, `stat_and_hash()` |
| Ordnerauswahl | vollstaendig umgesetzt | "Use folder" im Browser |
| Rekursive PlayModes | vollstaendig umgesetzt | `RECURSIVE_PLAY_MODES`, `/media/browse-recursive` |
| Rekursionstiefe | vollstaendig umgesetzt | Setting `recursion_depth`, Bereich 0-20, Default 3 |
| Webradio | vollstaendig umgesetzt | Content type `webradio`, Manifest mit `playMode` 8 und `stream` |
| LOCAL_M3U nicht unterstuetzt | vollstaendig umgesetzt | PlayMode-Liste schliesst M3U aus |
| Manifest-Erzeugung | vollstaendig umgesetzt | `manifest.build_manifest()` |
| `schema` | vollstaendig umgesetzt | `schema: 1` in Files- und Webradio-Manifest |
| `version` | vollstaendig umgesetzt | SHA-256 ueber kanonisches Manifest plus `_forceEpoch` |
| `force_epoch` | vollstaendig umgesetzt | `bump_force_epoch()`, `bump_force_epoch_all()` |
| `filesBaseUrl` | vollstaendig umgesetzt | `url_for("serve_media", filename="", _external=True)` |
| Reverse Proxy / HTTPS fuer `filesBaseUrl` | vollstaendig umgesetzt | `ProxyFix(... x_proto, x_host, x_port ...)` |
| SHA-256 je Datei | vollstaendig umgesetzt | `media_library.stat_and_hash()` |
| Dateigroesse je Datei | vollstaendig umgesetzt | `media_library.stat_and_hash()` |
| Medienbereitstellung | vollstaendig umgesetzt | `GET /media/<path:filename>` via `send_from_directory` |
| Media-Browse-API | vollstaendig umgesetzt | `/media/browse`, `/media/browse-recursive` |
| Path Traversal im Browser/Hashing | vollstaendig umgesetzt | `media_library.resolve()` prueft realpath unter Medienwurzel |
| Path Traversal beim Medien-GET | teilweise umgesetzt | `send_from_directory()` schuetzt den Pfad; manueller/Hardware-naher Downloadtest bleibt offen |
| Lazy Delete | vollstaendig umgesetzt | `delete_card()` loescht nur Hub-Eintrag |
| Secure Delete | vollstaendig umgesetzt | `delete_card()` ruft `delete_rfid_on_device()` vor Hub-Loeschung |
| Secure Delete Timeout | vollstaendig umgesetzt | `REQUEST_TIMEOUT = 3` |
| Quellbibliothek nie loeschen | vollstaendig umgesetzt | Delete-Pfade entfernen nur Store-Eintraege |
| Passwortschutz Web-UI | vollstaendig umgesetzt | `PUBLIC_ENDPOINTS`, Login, Hash in Settings |
| Oeffentliche ESPuino-Endpunkte | vollstaendig umgesetzt | Manifest, Media, Health sind public |
| Mehrsprachigkeit | vollstaendig umgesetzt | Flask-Babel, DE/EN/FR Kataloge, Language Switcher |
| Manifest-Vorschau | vollstaendig umgesetzt | `/manifest-preview`, JS Toggle ohne Pending-Side-Effects |
| Fehlerverhalten unbekannte Karte | vollstaendig umgesetzt | 404 JSON `{error,status}` plus Pending-Registrierung |
| Fehlerverhalten pending/nicht assigned | vollstaendig umgesetzt | 404 JSON `{error:"not_assigned",status:"pending"}` |
| Card-ID-Validierung am UI | vollstaendig umgesetzt | Add-Form und `add_card()` verlangen 12 Ziffern |
| Card-ID-Validierung am Manifest-Endpunkt | vollstaendig umgesetzt | `card_manifest()` gibt `400 {"error":"invalid_card_id"}` ohne Pending-Side-Effect |
| espId-Format | vollstaendig umgesetzt | Firmware-Vertrag legt MAC ohne Doppelpunkte, uppercase, fest; Server behandelt `espId` als opaken URL-/Store-Schluessel |
| Tests | teilweise umgesetzt | `tests/test_mediahub_api.py` deckt zentrale API-Vertragsfaelle ab; echte Hardwarefaelle bleiben manuell |
| API-Verifikationsdokument | vollstaendig umgesetzt | `docs/MEDIAHUB_API_VERIFICATION.md` |
| Firmware-Schnittstellendokument | vollstaendig umgesetzt | `docs/MEDIAHUB_FIRMWARE_INTERFACE.md` |
| UI-Texte ohne Upload-Bezug | vollstaendig umgesetzt | Media-Ansichten sprechen von gemounteter Medienbibliothek |
| Sicherheitsannahmen dokumentiert | teilweise umgesetzt | README/Konzept gut, aber API-Verifikation fehlt |

## Relevante Dateien

- `app.py`: Flask-Routen, Auth-Gate, Manifest-Endpunkt, Media-Browse, Settings.
- `manifest.py`: PlayMode-Listen, Card-ID-Validierung, Manifest-Schema und `version`.
- `store.py`: JSON-Persistenz, `(espId, cardId)`-Schluessel, Pending, Settings, Force Refresh.
- `media_library.py`: sicherer Zugriff auf die read-only Medienbibliothek, rekursive Audioauflistung, SHA-256.
- `espuino_client.py`: Secure-Delete-Aufruf an `DELETE /rfid?id=<cardId>`.
- `templates/*.html`, `static/js/*.js`: Web-UI, Medienbrowser, Manifest-Preview, Duplicate.
- `Dockerfile`, `docker-compose.yml`: lokale Container-Bereitstellung, `/data` RW und `/media` RO.

## Serverseitige Luecken fuer Phase C/D

1. Secure Delete kann serverseitig nur gegen einen echten ESPuino vollstaendig verifiziert werden.
2. Grosse Mediendateien, Sonderzeichenpfade, HTTPS-Reverse-Proxy und Netzwerkabbrueche brauchen Integrations-/Hardwaretests.

## Nicht als Luecke bewertet

- Kein Datenbankserver: Die JSON-Datei ist fuer den erwarteten Umfang angemessen und passt zum Konzept.
- Keine ESPuino-Authentifizierung: Laut Konzept bleiben Manifest- und Medien-Endpunkte offen.
- Kein Upload: Die Medienbibliothek wird bewusst read-only eingebunden.
- Kein globaler RFID-Sollzustand, kein Bulk-Sync, kein Server-Wins-Modell: Das Konzept verbietet diese Erweiterungen.
- Kein neues Manifestformat: Das vorhandene `schema: 1` deckt Files und Webradio ab.
