# MediaHub Firmware Interface

Stand: 2026-08-11

Dieses Dokument ist die verbindliche technische Schnittstelle zwischen ESPuino-Firmware und ESPuino MediaHub. Fachlich fuehrend bleibt `docs/mediahub-konzept.md`; dieses Dokument uebersetzt das Konzept in Firmware-relevante Vertrage.

## B1 Grundprinzip

- ESPuino pullt Manifest und Medien bei Bedarf vom Hub.
- Der Hub pusht keine Medien und keinen globalen RFID-Zustand.
- Local-first: vorhandene, plausible lokale Inhalte duerfen ohne Netzwerk starten.
- Offline-first: ein nicht erreichbarer Hub darf vorhandene lokale Wiedergabe nicht blockieren.
- Es gibt keine lang blockierenden Lookups. Manifest- und Medienrequests muessen kurze Timeouts nutzen.
- Der Hub ist Quelle der Wahrheit fuer Dateiliste, Webradio-Stream, `playMode` und `version`.
- Die Firmware interpretiert keine semantische Bedeutung in `version`.

## B2 RFID/NVS-Vertrag

Das bestehende RFID-NVS-Format bleibt unveraendert. Eine MediaHub-Karte wird durch diese Kombination erkannt:

```text
path     = mediahub://<host:port>
playMode = MEDIAHUB
```

`MEDIAHUB` ist nur der persistierte Marker im NVS. Der tatsaechliche Wiedergabe-PlayMode kommt immer aus dem Manifest.

`mediahub://` darf nicht als Webradio behandelt werden. Bestehendes Webradio beginnt weiter mit `http`/`https` und bleibt unveraendert.

## B3 Identitaeten

`cardId`:

- exakt 12 Dezimalstellen
- entsteht aus 4 RFID-UID-Bytes, je Byte als dreistellige Dezimalzahl formatiert
- Beispiel: `004123087255`

`espId`:

- kanonisch: WLAN-MAC-Adresse ohne Doppelpunkte, uppercase
- Format: 12 Hex-Zeichen `[0-9A-F]{12}`
- Beispiel: `AABBCCDDEEFF`
- stabil ueber Neustarts
- unabhaengig vom Hostnamen und vom ESPuino-Anzeigenamen
- URL-sicher ohne zusaetzliches Encoding
- darf sich im Normalbetrieb nicht aendern, sonst sieht der Hub ein neues Geraet

Die Hub-Zuweisung ist eindeutig durch `(espId, cardId)`. Dieselbe physische Karte kann pro ESPuino eine eigene Zuweisung haben.
Das Geraete-Alias im Hub ist reine UI-Ergonomie und hat keine Wirkung auf `espId`, Protokoll oder Cache.

## B4 Manifest-Endpoint

```text
GET http://<hub-host:port>/<espId>/card/<cardId>/manifest.json
GET https://<hub-host>/<espId>/card/<cardId>/manifest.json
```

Der Hub-Basiswert stammt aus dem NVS-Pfad `mediahub://<host:port>`. Die Firmware ersetzt nur das Schema durch `http://` oder, falls im Enrollment eindeutig so vorgesehen, durch `https://`. In der aktuellen Hub-Implementierung wird kein separater Register-Endpoint verwendet.

## B5 Pending

Trigger:

- ESPuino fragt ein Manifest fuer ein dem Hub unbekanntes `(espId, cardId)`-Paar ab.
- Oder das Paar existiert, ist aber noch nicht zugewiesen.

HTTP-Status:

```text
404 Not Found
```

Response fuer unbekannte Karte:

```json
{
  "error": "unknown_card",
  "status": "pending"
}
```

Response fuer bekannte, aber nicht zugewiesene Karte:

```json
{
  "error": "not_assigned",
  "status": "pending"
}
```

Serverseitige Nebenwirkung:

- Bei unbekannter Karte legt der Hub einen Pending-Eintrag fuer `(espId, cardId)` an.
- Bei bekannter Pending-Karte aktualisiert der Hub `last_seen`.
- In beiden Faellen wird das Geraet gesehen/aktualisiert.

Erwartetes ESPuino-Verhalten:

- kurze Fehler-/Pending-Anzeige
- keine lokalen Medien loeschen
- NVS-Eintrag nicht entfernen
- kein langer Retry-Loop im Vordergrund

## B6 Manifest-Schema

Gemeinsame Pflichtfelder:

| Feld | Pflicht | Bedeutung |
| --- | --- | --- |
| `schema` | ja | Manifest-Schema, aktuell `1` |
| `version` | ja | opaker Aenderungsstempel |
| `cardId` | ja | muss zur aufgelegten Karte passen |
| `playMode` | ja | echter ESPuino-PlayMode fuer die Wiedergabe |
| `name` | optional | Anzeige/Logging |

Files-Manifest:

```json
{
  "version": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "schema": 1,
  "cardId": "004123087255",
  "playMode": 3,
  "name": "Hoerbuch Folge 12",
  "filesBaseUrl": "http://192.168.1.50:8080/media/",
  "files": [
    {
      "path": "Hoerbuecher/Folge 12/01.mp3",
      "size": 5432101,
      "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    }
  ]
}
```

Files-spezifische Felder:

| Feld | Pflicht | Bedeutung |
| --- | --- | --- |
| `filesBaseUrl` | ja bei Files | Download-Basis-URL, endet mit `/` |
| `files` | ja bei Files | Liste der zu cachenden Dateien |
| `files[].path` | ja | relativer Pfad unter der Hub-Medienbibliothek |
| `files[].size` | ja | erwartete Dateigroesse in Bytes |
| `files[].sha256` | ja | SHA-256 fuer Download-Verifikation |

Webradio-Manifest:

```json
{
  "version": "a1c8f78460b7b499e476dc43237ea7fbec1f1fa7bb6e8d2a1d8f21a844b4d782",
  "schema": 1,
  "cardId": "004123087255",
  "playMode": 8,
  "name": "Radio Beispiel",
  "stream": "http://radio.example/stream.mp3"
}
```

Webradio-spezifische Felder:

| Feld | Pflicht | Bedeutung |
| --- | --- | --- |
| `stream` | ja bei Webradio | URL, die an den vorhandenen Webradio-Pfad des AudioPlayers uebergeben wird |

Bei Webradio gibt es kein `filesBaseUrl` und kein `files`.

## B7 Versionierung

- `version` ist opaque.
- ESPuino vergleicht nur alte/neue `version`.
- ESPuino hasht das Manifest nicht selbst.
- `force_epoch` wird vom Hub intern in die `version` eingerechnet, erscheint aber nicht im Manifest.
- Force Refresh braucht kein spezielles semantisches Wissen in der Firmware. Eine andere `version` bedeutet: lokaler Zustand ist veraltet.

## B8 Medien-Endpunkt

Download-URL:

```text
<filesBaseUrl><urlencoded files[].path>
```

Hub-Route:

```text
GET /media/<path>
```

Pfadregeln:

- `files[].path` ist relativ zur Medienbibliotheks-Wurzel des Hubs.
- Die Firmware legt Dateien lokal unter `/.mediahub/media/<cardId>/<path>` ab.
- `..`, absolute Pfade, leere Segmente und Pfade ausserhalb der lokalen MediaHub-Zone muessen firmwareseitig verworfen werden.
- Pfadbestandteile mit Leerzeichen, Umlauten und Sonderzeichen muessen fuer HTTP korrekt URL-encoded werden.

## B9 Integritaet

- Beim Download wird SHA-256 inkrementell ueber den empfangenen Stream berechnet.
- Nach Download muss `sha256` exakt passen.
- Die Dateigroesse muss ebenfalls exakt passen.
- Vorhandene lokale Dateien werden nicht vollstaendig neu gehasht.
- Vorhandene lokale Dateien werden nur ueber Groesse plausibilisiert.

## B10 Download

Pro Datei:

1. sicheren lokalen Zielpfad unter `/.mediahub/media/<cardId>/` bilden
2. Zielverzeichnis anlegen
3. vorhandene alte `.tmp` entfernen
4. HTTP-GET starten
5. Stream bis `streamend` direkt auf SD schreiben
6. SHA-256 waehrend des Schreibens berechnen
7. empfangene Groesse gegen `files[].size` pruefen
8. SHA-256 gegen `files[].sha256` pruefen
9. `.tmp` atomar auf finalen Pfad umbenennen

Ein Abbruch darf keine defekte finale Datei hinterlassen. Bei Fehlern wird die `.tmp` entfernt oder als nicht nutzbar behandelt.

## B11 Cache/Offline

Manifest-Cache:

```text
/.mediahub/manifests/<cardId>.json
```

Medien-Cache:

```text
/.mediahub/media/<cardId>/<files[].path>
```

Hub online:

- Manifest holen, validieren, `version` vergleichen.
- Bei neuer Version lokalen Zustand nach Konzept aktualisieren.
- Fehlende oder groessenfalsche Dateien laden.

Hub offline:

- Wenn valider Manifest-Cache und plausible Mediendateien vorhanden sind, abspielen.
- Wenn kein valider lokaler Zustand vorhanden ist, kontrollierter Fehler ohne langes Blockieren.

Ohne lokalen Cache:

- Files-Karte kann offline nicht spielen.
- Webradio kann offline nicht spielen.

## B12 Webradio

Webradio ist ein Sonderfall:

- `playMode` ist `WEBSTREAM` (`8`).
- `stream` ist Pflicht.
- Es gibt keine Dateien, keinen Download, keine SHA-Pruefung.
- Der Stream wird an den bestehenden ESPuino-Webradio-Pfad uebergeben.
- Offline ist Webradio nicht abspielbar.
- Normale, nicht-MediaHub-Webradio-Karten bleiben unveraendert.

## B13 Delete

Firmwareseitig relevanter Endpoint:

```text
DELETE /rfid?id=<cardId>
```

Vertrag fuer MediaHub-Karten:

- Wenn der geloeschte RFID-Eintrag `playMode = MEDIAHUB` hat, entfernt die Firmware zusaetzlich:
  - `/.mediahub/manifests/<cardId>.json`
  - `/.mediahub/media/<cardId>/`
- `200` nur, wenn NVS-Loeschung und MediaHub-Cache-Loeschung erfolgreich waren.
- `500` bei unvollstaendiger Loeschung, damit der Hub Secure Delete wiederholen kann.
- Es duerfen keine anderen Karten, keine anderen ESPuino-Zuweisungen und keine Hub-Quellbibliothek betroffen sein.

Hub-Verhalten:

- Lazy Delete: Hub loescht nur seinen eigenen Eintrag.
- Secure Delete: Hub ruft `DELETE /rfid?id=<cardId>` am betroffenen ESPuino auf und loescht den Hub-Eintrag erst nach `200`.

## B14 HTTP- und Fehlerfaelle

| Fall | HTTP/Signal | Erwartetes Firmware-Verhalten |
| --- | --- | --- |
| Manifest OK Files | `200` + valides Files-Manifest | validieren, cachen, Medien pruefen/laden |
| Manifest OK Webradio | `200` + valides Webradio-Manifest | Stream an AudioPlayer-Webradio uebergeben |
| Unbekannte Karte | `404`, `status=pending` | Pending/Fehler anzeigen, nichts loeschen |
| Bekannte Pending-Karte | `404`, `status=pending` | Pending/Fehler anzeigen, nichts loeschen |
| Ungueltige Card-ID | `400` oder `404` | kontrollierter Fehler, nichts loeschen |
| Hub offline/DNS-Fehler | Timeout/Connection error | lokalen Cache nutzen, sonst Fehler |
| Timeout | Client-Timeout | lokalen Cache nutzen, sonst Fehler |
| HTTP 5xx | Serverfehler | lokalen Cache nutzen, sonst Fehler |
| Ungueltiges JSON | Parsefehler | Cache nicht ueberschreiben, lokalen Cache nutzen |
| Falsches `schema` | Validierungsfehler | Cache nicht ueberschreiben, Fehler/alter Cache |
| Falsche `cardId` | Validierungsfehler | Manifest verwerfen |
| Fehlende Pflichtfelder | Validierungsfehler | Manifest verwerfen |
| Ungueltiger `playMode` | Validierungsfehler | Manifest verwerfen |
| Medien-404 | `404` beim Datei-GET | Download abbrechen, `.tmp` entfernen |
| Groesse falsch | Content-Length/gezaehlte Bytes abweichend | Download verwerfen |
| SHA falsch | Hash abweichend | Download verwerfen |
| SD voll/Schreibfehler | FS-Fehler | Download abbrechen, keine finale Datei |

## B15 Kompatibilitaet

- Nicht-MediaHub-Karten bleiben unveraendert.
- Bestehendes Webradio bleibt unveraendert.
- Lokale SD-Wiedergabe bleibt unveraendert.
- Bestehende PlayModes, Resume/Audiobook, RFID-Anlernen, WLAN, Webinterface, OTA und SD-Funktionen duerfen nicht unnoetig veraendert werden.
- MediaHub wird nur aktiv, wenn NVS-Pfad `mediahub://...` und PlayMode-Marker `MEDIAHUB` zusammenpassen.
