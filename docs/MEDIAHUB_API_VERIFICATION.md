# MediaHub API Verification

Stand: 2026-08-11

Diese Datei dokumentiert reproduzierbare API-Checks fuer den Vertrag zwischen ESPuino und MediaHub. Automatisierte Basisabdeckung liegt in `tests/test_mediahub_api.py`.

## Testumgebung

Lokaler Start:

```bash
python -m pip install -r requirements.txt
python app.py
```

Container-Start:

```bash
docker compose up --build
```

In den Beispielen steht `http://localhost:8080` fuer die Hub-Basisadresse.

## Manifest: unbekannte Karte

Request:

```bash
curl -i http://localhost:8080/esp-1/card/004123087255/manifest.json
```

Erwartung:

```http
HTTP/1.1 404 NOT FOUND
Content-Type: application/json
```

```json
{
  "error": "unknown_card",
  "status": "pending"
}
```

Serverseitige Nebenwirkung:

- Geraet `esp-1` wird angelegt/aktualisiert.
- `(esp-1, 004123087255)` wird als Pending-Karte angelegt.

## Manifest: bekannte Pending-Karte

Request erneut nach der ersten Pending-Registrierung:

```bash
curl -i http://localhost:8080/esp-1/card/004123087255/manifest.json
```

Erwartung:

```http
HTTP/1.1 404 NOT FOUND
Content-Type: application/json
```

```json
{
  "error": "not_assigned",
  "status": "pending"
}
```

## Manifest: ungueltige Card-ID

Request:

```bash
curl -i http://localhost:8080/esp-1/card/not-a-card/manifest.json
```

Erwartung:

```http
HTTP/1.1 400 BAD REQUEST
Content-Type: application/json
```

```json
{
  "error": "invalid_card_id"
}
```

Serverseitige Nebenwirkung:

- Keine Pending-Karte wird angelegt.
- Kein Geraet wird nur wegen dieser ungueltigen Card-ID angelegt.

## Manifest: Files-Zuweisung

Voraussetzung: Im Web-UI ist fuer `(esp-1, 004123087255)` eine Files-Zuweisung mit `playMode = 3` und mindestens einer Datei gespeichert.

Request:

```bash
curl -s http://localhost:8080/esp-1/card/004123087255/manifest.json
```

Erwartetes Schema:

```json
{
  "version": "64-hex-characters",
  "schema": 1,
  "cardId": "004123087255",
  "playMode": 3,
  "name": "Story",
  "filesBaseUrl": "http://localhost:8080/media/",
  "files": [
    {
      "path": "Folder/01.mp3",
      "size": 123,
      "sha256": "64-hex-characters"
    }
  ]
}
```

Checks:

- `schema` ist `1`.
- `cardId` entspricht dem URL-Parameter.
- `version` ist ein opaker 64-stelliger SHA-256-Hexstring.
- `filesBaseUrl` endet mit `/`.
- Jeder Datei-Eintrag hat `path`, `size`, `sha256`.
- `path` ist relativ zur Hub-Medienbibliothek.

## Manifest: Webradio-Zuweisung

Voraussetzung: Im Web-UI ist fuer `(esp-1, 004123087255)` eine Webradio-Zuweisung gespeichert.

Request:

```bash
curl -s http://localhost:8080/esp-1/card/004123087255/manifest.json
```

Erwartetes Schema:

```json
{
  "version": "64-hex-characters",
  "schema": 1,
  "cardId": "004123087255",
  "playMode": 8,
  "name": "Radio Beispiel",
  "stream": "http://radio.example/stream.mp3"
}
```

Checks:

- `stream` ist vorhanden.
- `filesBaseUrl` fehlt.
- `files` fehlt.
- Kein Download ist firmwareseitig erforderlich.

## Force Refresh

Voraussetzung: Eine zugewiesene Karte existiert.

Schritt 1:

```bash
curl -s http://localhost:8080/esp-1/card/004123087255/manifest.json
```

`version` merken.

Schritt 2: Im Web-UI fuer die Karte "Force refresh" ausloesen oder:

```text
POST /cards/esp-1/004123087255/force-refresh
```

Schritt 3:

```bash
curl -s http://localhost:8080/esp-1/card/004123087255/manifest.json
```

Erwartung:

- `version` hat sich geaendert.
- Alle Inhaltsfelder koennen unveraendert bleiben.
- Firmware behandelt die neue `version` als Aenderung, ohne den Grund zu interpretieren.

## Reverse Proxy / HTTPS

Request mit Proxy-Header:

```bash
curl -s \
  -H "Host: hub.example" \
  -H "X-Forwarded-Proto: https" \
  http://localhost:8080/esp-1/card/004123087255/manifest.json
```

Erwartung bei Files-Manifest:

```json
{
  "filesBaseUrl": "https://hub.example/media/"
}
```

Der Hub nutzt `ProxyFix`, damit die Download-Basis fuer ESPuino hinter TLS-terminierenden Proxies korrekt bleibt.

## Media Browse

Request:

```bash
curl -s "http://localhost:8080/media/browse?path="
```

Erwartung:

```json
{
  "path": "",
  "dirs": [],
  "files": []
}
```

Die Arrays haengen vom Inhalt der gemounteten Medienbibliothek ab. Es werden nur Audio-Dateien mit unterstuetzten Endungen gelistet.

Path-Traversal-Check:

```bash
curl -i "http://localhost:8080/media/browse?path=../"
```

Erwartung:

```http
HTTP/1.1 404 NOT FOUND
```

## Medien-Download

Request:

```bash
curl -i "http://localhost:8080/media/Folder/01.mp3"
```

Erwartung:

- `200` und Dateiinhalt, wenn die Datei unter der gemounteten Medienbibliothek existiert.
- `404`, wenn sie nicht existiert oder der Pfad nicht erlaubt ist.

Firmware-Erwartung:

- Download streamen, nicht komplett in RAM laden.
- Groesse und SHA-256 gegen Manifest pruefen.
- Erst nach erfolgreicher Verifikation final umbenennen.

## Secure Delete

Hub-seitiger Request nach UI-Aktion:

```text
DELETE http://<device-ip>/rfid?id=004123087255
```

Erwartung:

- Bei `200` loescht der Hub seine Zuweisung.
- Bei Timeout, Netzwerkfehler oder nicht-`200` behaelt der Hub die Zuweisung.

Dieser Call betrifft nur das in der Zuweisung gespeicherte ESPuino. Es gibt kein "zuletzt gesehenes Geraet"-Raten.

## Automatisierte Pruefung

Aktueller Testlauf:

```text
python -m pytest
6 passed
```

Abgedeckt:

- unbekannte Karte -> Pending
- ungueltige Card-ID -> 400 ohne Side Effects
- Files-Manifest inkl. Reverse-Proxy-`filesBaseUrl`
- Force Refresh aendert nur `version`
- Webradio-Manifest ohne Files-Felder
- Media-Browse blockiert Path Traversal

Noch manuell zu pruefen:

- echte grosse Dateien und Sonderzeichenpfade im Browser
- Secure Delete gegen echte ESPuino-Firmware
- HTTPS-Reverse-Proxy mit realem Proxy
- Download-Abbruch und SD-Fehler auf Hardware
