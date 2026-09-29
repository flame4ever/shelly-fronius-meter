# shelly-fronius-meter

Stellt die Messwerte eines **Shelly Pro 3EM** über Modbus TCP so bereit, dass ein
**Fronius GEN24** sie als Smart Meter akzeptiert — auch als **zusätzlicher Zähler**
neben einem bereits vorhandenen Primärzähler.

Getestet mit Fronius Symo GEN24 12.0 SC Plus, Firmware 1.41.11-1, als Erzeugerzähler
auf Modbus-Adresse 2.

---

## Warum es dieses Repo gibt

Die vorhandenen Emulatoren funktionieren auf aktuellen GEN24-Firmwareständen nicht
mehr zuverlässig, und als Sekundärzähler hat es im
[Photovoltaikforum](https://www.photovoltaikforum.com/thread/185108-fronius-smart-meter-tcp-protokoll/)
bisher niemand berichtet. Zwei Fehler stecken dahinter:

### 1. Die SunSpec-Kennung lag an der falschen Stelle

Der GEN24 fragt bei der Geräteerkennung nacheinander mehrere Adressen ab und erwartet
die Kennung `SunS` auf **Wire-Adresse 40000**. Fronius beschreibt das selbst so: „Um
Register 40001 auszulesen, die Adresse 40000 (0x9C40) verwenden."

Die bisherigen Skripte legen den Datenblock ab Adresse 0 an. Der Wechselrichter
bekommt dort nur `IllegalAddress` und verwirft das Gerät kommentarlos — der Zähler
wird schlicht nicht erkannt.

### 2. Die Leistungsfaktor-Register fehlten

Im SunSpec Model 213 folgen auf die vier Blindleistungs-Register vier Register für
den Leistungsfaktor, erst danach kommen die Wh-Zählerstände:

```
Register 40115  VAR       Blindleistung gesamt
Register 40117  VARphA
Register 40119  VARphB
Register 40121  VARphC
Register 40123  PF        Leistungsfaktor gesamt   <-- fehlte
Register 40125  PFphA                              <-- fehlte
Register 40127  PFphB                              <-- fehlte
Register 40129  PFphC                              <-- fehlte
Register 40131  TotWhExp  Einspeisung
```

Ohne diese vier Werte ist **alles danach um acht Register verschoben**. Der
Wechselrichter liest dann den Wh-Zählerstand als Leistungsfaktor. Der Fronius-Support
beschrieb das Symptom so:

> Dadurch wird der Wert 44531,898 als Leistungsfaktor übermittelt, was auf eine
> fehlerhafte Interpretation der Messdaten hindeutet. Aufgrund dieser inkorrekten
> Werte können die Daten nicht ordnungsgemäß aggregiert und folglich auch nicht als
> Erzeugerzähler-Daten (DevWork) erkannt bzw. verarbeitet werden.

Der Zähler erscheint dabei durchaus grün in der Oberfläche — der Verbrauch bleibt
aber auf 0.

Der Model-213-Block ist hier vollständig auf 124 Register ausgebaut (vorher 82).

---

## Voraussetzungen

* Shelly Pro 3EM mit aktivierter **Modbus-Funktion** oder erreichbarer REST-API
  (dieses Skript nutzt die REST-API, Modbus am Shelly ist nicht nötig)
* Ein Gerät, das dauerhaft läuft: Raspberry Pi, NAS, kleiner Server
* Python 3.9 oder neuer
* Fronius GEN24 mit Techniker-Passwort

Der Rechner muss dauerhaft laufen. Ist er aus, verliert der Wechselrichter seinen
Zähler.

---

## Installation

```bash
sudo mkdir -p /opt/shelly-fronius-meter
sudo git clone https://github.com/flame4ever/shelly-fronius-meter.git /opt/shelly-fronius-meter
cd /opt/shelly-fronius-meter

python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

In `simulator.py` den Konfigurationsblock ganz oben anpassen:

```python
SHELLY_IP = "192.168.1.100"   # IP des Shelly Pro 3EM
SLAVE_ID  = 2                 # Modbus-Adresse, muss zum Fronius-Eintrag passen
MODBUS_PORT = 502             # der GEN24 spricht ausschliesslich 502 an
```

Testlauf im Vordergrund:

```bash
sudo ./venv/bin/python simulator.py
```

Erwartete Ausgabe:

```
### Fronius Smart Meter Emulator 2.0.0
### Shelly        : 192.168.1.100
### Modbus        : 0.0.0.0:502, Slave-ID 2
### Registerbasis : 40000 (SunS auf Register 40001)
```

---

## Prüfen, bevor der Fronius ins Spiel kommt

Von einem **anderen Rechner im Netz** — nicht vom Emulator-Host, dort geht die
Verbindung über localhost und verdeckt Bind- und Netzwerkfehler:

```bash
python3 tools/check_registers.py 192.168.1.50 --unit 2
```

Das Werkzeug stellt genau die Anfragen, die der GEN24 stellt, und braucht nur die
Python-Standardbibliothek:

```
--- Geraeteerkennung (Reihenfolge wie beim GEN24) ---
  OK Kennung 'SunS'       wire 40000: b'SunS'      (Soll 'SunS')
  OK Common Model-ID      wire 40002: 1            (Soll 1)
  OK Meter Model-ID       wire 40069: 213          (Soll 213)
  OK Meter Laenge         wire 40070: 124          (Soll 124)

--- Messwerte ---
  W      Wirkleistung             233.649 W
  PF     Leistungsfaktor           92.878
  TotWhExp Einspeisung               8.200 Wh
  TotWhImp Bezug                 32485.000 Wh
```

Erst wenn das sauber durchläuft, lohnt sich der nächste Schritt.

---

## Als Dienst einrichten

```bash
sudo cp fronius-emulator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fronius-emulator
sudo systemctl status fronius-emulator
```

Pfade in der Unit gegebenenfalls anpassen. Anschließend einmal neu starten und
prüfen, ob der Zähler von allein wiederkommt — sonst merkt man es erst beim nächsten
Stromausfall.

---

## Im Fronius eintragen

Techniker-Passwort erforderlich.

1. Weboberfläche des GEN24 aufrufen
2. **Gerätekonfiguration → Komponenten → Komponenten hinzufügen → Zähler**
3. Als IP die **Adresse des Emulator-Rechners** eintragen, nicht die des Shelly
4. Modbus-Adresse identisch zu `SLAVE_ID` im Skript
5. Anwendung passend wählen: Einspeisepunkt, Verbrauchszweig oder Erzeugerzähler

---

## Fehlersuche

| Symptom | Ursache |
|---|---|
| Keine Verbindung zum Emulator | Dienst läuft nicht, oder Bind auf 127.0.0.1 statt 0.0.0.0 |
| Verbindung wird angenommen, keine Antwort | Slave-ID stimmt nicht mit dem Fronius-Eintrag überein |
| `IllegalAddress` auf wire 40000 | Registerbasis falsch, `BASE_OFFSET` prüfen |
| Zähler grün, Verbrauch bleibt 0 | PF-Register fehlen — genau der Fehler, den dieses Repo behebt |
| Werte da, Vorzeichen verkehrt | Stromwandler am Shelly verkehrt herum; in der Shelly-Konfiguration pro Phase umkehrbar |
| Nach Neustart weg | Autostart fehlt, oder der Rechner hat eine neue IP bekommen |

Mitschnitt der tatsächlichen Anfragen:

```bash
sudo tcpdump -i any -n -X port 502 -c 30
```

Byte 9 und 10 nach dem Modbus-Header sind die angefragte Adresse.

---

## Konfiguration im Detail

| Einstellung | Bedeutung |
|---|---|
| `SHELLY_IP` | IP des Shelly Pro 3EM |
| `SLAVE_ID` | Modbus-Adresse dieses Zählers, muss zum Fronius-Eintrag passen |
| `MODBUS_PORT` | 502, vom GEN24 fest erwartet |
| `SERIAL_NUMBER` | Bei mehreren Instanzen je eine eigene vergeben |
| `POLL_INTERVAL` | Abfrageintervall in Sekunden |
| `PF_SCALE` | `100.0` = Prozent (SunSpec-konform), `1.0` = roher Faktor |
| `BASE_OFFSET` | `40000` = SunSpec-Standard, nur in Sonderfällen ändern |
| `VERBOSE` | Gelesene Werte bei jedem Zyklus ausgeben |

---

## Mehrere Zähler

Zwei Wege:

**Eigene Slave-ID auf derselben IP** — im Fronius denselben Rechner mit anderer
Modbus-Adresse eintragen. Elegant, aber es gibt Berichte, dass manche GEN24 pro
Zähler eine eigene IP erwarten.

**Eigene Instanz auf zusätzlicher IP** — dem Rechner eine weitere IP geben, das
Repo ein zweites Mal klonen, dort `SHELLY_IP`, `SLAVE_ID` und `SERIAL_NUMBER`
anpassen und einen zweiten Dienst anlegen. Näher an dem, was der Wechselrichter
erwartet.

---

## Grenzen

Dies ist keine Fronius-Hardware und wird von Fronius nicht unterstützt. Die
Aktualisierung hängt am Abfrageintervall und an der Antwortzeit des Shelly — für
harte Nulleinspeisungsregelung ist das zu träge. Für Eigenverbrauchserfassung,
asymmetrische Erzeugung und zusätzliche Zähler reicht es.

Fällt der Shelly aus, liefert der Emulator weiter den zuletzt gelesenen Wert und
protokolliert den Fehler. Für einen Zähler, an dem eine Regelung hängt, sollte man
das Verhalten bewusst prüfen.

---

## Herkunft

Aufbauend auf:

* [simontribus/froniussimulator_shellypro3em](https://github.com/simontribus/froniussimulator_shellypro3em)
* [tichachm/fronius_smart_meter_modbus_tcp_emulator](https://github.com/tichachm/fronius_smart_meter_modbus_tcp_emulator)

Weitere Quellen:

* [Fronius GEN24 — Modbus TCP & RTU, Bedienungsanleitung](https://manuals.fronius.com/html/4204102649/de.html)
* [Fronius Power Meter — Modbus Registers Map](https://www.aggsoft.com/modbus-data-logging/fronius-power-meter.htm)
* [Shelly Gen2 EM — Modbus-Register](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/EM/)

MIT-Lizenz, siehe [LICENSE](LICENSE).

---

## English summary

Exposes a **Shelly Pro 3EM** over Modbus TCP so a **Fronius GEN24** accepts it as a
Smart Meter, including as a **secondary meter** alongside an existing primary one.

Two fixes over the existing emulators:

1. **Register base.** The GEN24 probes for the `SunS` marker at wire address 40000.
   Earlier scripts place their data block at address 0, so the inverter only gets
   `IllegalAddress` and silently discards the device.
2. **Missing power-factor registers.** SunSpec model 213 has four PF registers
   between reactive power and the Wh counters. Leaving them out shifts everything
   after by eight registers, so the inverter reads the Wh counter as the power
   factor. The meter then shows up green but consumption stays at 0.

The model 213 block is now complete at 124 registers (was 82). Verify your setup
from another machine with `tools/check_registers.py <ip> --unit <n>` before adding
the meter in the inverter.

Tested against a Fronius Symo GEN24 12.0 SC Plus, firmware 1.41.11-1.
