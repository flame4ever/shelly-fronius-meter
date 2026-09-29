#!/usr/bin/env python3
"""
Prueft, ob ein Fronius-Smart-Meter-Emulator korrekt antwortet.

Stellt genau die Modbus-Anfragen, die ein Fronius GEN24 stellt, und
vergleicht die Antworten mit dem SunSpec-Model-213-Layout. Braucht
keine zusaetzlichen Pakete - nur die Python-Standardbibliothek.

Von einem ANDEREN Rechner im Netz aufrufen, nicht vom Emulator-Host:
lokal geht die Verbindung ueber localhost und verdeckt Bind- und
Netzwerkfehler.

    python3 check_registers.py 192.168.1.50
    python3 check_registers.py 192.168.1.50 --unit 2 --port 502
"""

import argparse
import socket
import struct
import sys

# Wire-Adresse = SunSpec-Registernummer minus 1.
# Der Datenblock des Model 213 beginnt bei Register 40072 = wire 40071.
DATA = 40071

HEADER = [
    (40000, "Kennung 'SunS'", "SunS"),
    (40002, "Common Model-ID", 1),
    (40003, "Common Laenge", 65),
    (40069, "Meter Model-ID", 213),
    (40070, "Meter Laenge", 124),
    (40195, "Endekennung", 65535),
]

# (Offset in Floats ab Datenblockbeginn, Bezeichnung, Einheit)
WERTE = [
    (0,  "A      Strom gesamt",     "A"),
    (1,  "AphA   Strom L1",         "A"),
    (2,  "AphB   Strom L2",         "A"),
    (3,  "AphC   Strom L3",         "A"),
    (5,  "PhVphA Spannung L1",      "V"),
    (6,  "PhVphB Spannung L2",      "V"),
    (7,  "PhVphC Spannung L3",      "V"),
    (12, "Hz     Frequenz",         "Hz"),
    (13, "W      Wirkleistung",     "W"),
    (14, "WphA   Wirkleistung L1",  "W"),
    (15, "WphB   Wirkleistung L2",  "W"),
    (16, "WphC   Wirkleistung L3",  "W"),
    (17, "VA     Scheinleistung",   "VA"),
    (21, "VAR    Blindleistung",    "VAr"),
    (25, "PF     Leistungsfaktor",  ""),
    (26, "PFphA  LF L1",            ""),
    (29, "TotWhExp Einspeisung",    "Wh"),
    (33, "TotWhImp Bezug",          "Wh"),
]


def lese(host, port, unit, adresse, anzahl):
    """Eine Modbus-TCP-Leseanfrage (Funktion 3). None bei Fehler."""
    try:
        with socket.create_connection((host, port), timeout=3) as s:
            s.send(struct.pack(">HHHBBHH", 1, 0, 6, unit, 3, adresse, anzahl))
            antwort = s.recv(512)
    except (OSError, socket.timeout):
        return None
    if len(antwort) < 9:
        return None
    if antwort[7] & 0x80:                      # Exception-Antwort
        return ("FEHLER", antwort[8])
    return antwort[9:9 + antwort[8]]


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host", help="IP des Emulators")
    p.add_argument("--port", type=int, default=502, help="Modbus-Port (Standard 502)")
    p.add_argument("--unit", type=int, default=1,
                   help="Modbus-/Slave-Adresse, wie im Fronius eingetragen (Standard 1)")
    args = p.parse_args()

    probe = lese(args.host, args.port, args.unit, 40000, 2)
    if probe is None:
        print(f"Keine Antwort von {args.host}:{args.port}.")
        print()
        print("Moegliche Ursachen:")
        print("  - Emulator laeuft nicht        -> systemctl status pruefen")
        print("  - Falscher Port                -> der GEN24 spricht nur 502 an")
        print("  - Nur an localhost gebunden    -> muss 0.0.0.0 sein")
        print(f"  - Falsche Modbus-Adresse       -> aktuell {args.unit} probiert")
        return 1

    fehler = 0
    print(f"Ziel: {args.host}:{args.port}, Modbus-Adresse {args.unit}")
    print()
    print("--- Geraeteerkennung (Reihenfolge wie beim GEN24) ---")
    for wire, name, soll in HEADER:
        if soll == "SunS":
            ist = lese(args.host, args.port, args.unit, wire, 2)
            passt = ist == b"SunS"
            zeige = ist
        else:
            roh = lese(args.host, args.port, args.unit, wire, 1)
            ist = struct.unpack(">H", roh)[0] if isinstance(roh, bytes) else roh
            passt = ist == soll
            zeige = ist
        fehler += 0 if passt else 1
        print(f"  {'OK ' if passt else '!! '}{name:<20} wire {wire}: {zeige!r:<12} (Soll {soll!r})")

    print()
    print("--- Messwerte ---")
    for offset, name, einheit in WERTE:
        roh = lese(args.host, args.port, args.unit, DATA + offset * 2, 2)
        if isinstance(roh, bytes) and len(roh) == 4:
            wert = struct.unpack(">f", roh)[0]
            print(f"  {name:<24} {wert:>14.3f} {einheit}")
        else:
            fehler += 1
            print(f"  {name:<24} {'nicht lesbar':>14}")

    print()
    if fehler:
        print(f"{fehler} Abweichung(en). Haeufigste Ursachen:")
        print("  - Registerbasis falsch: 'SunS' muss auf wire 40000 liegen,")
        print("    nicht auf wire 0. Im Skript BASE_OFFSET pruefen.")
        print("  - Leistungsfaktor-Register fehlen: dann sind alle Werte ab")
        print("    'PF' um 8 Register verschoben und der Wh-Zaehlerstand")
        print("    erscheint als Leistungsfaktor.")
        return 1

    print("Alles in Ordnung. Der Zaehler kann im Fronius angelegt werden:")
    print("  Geraetekonfiguration -> Komponenten -> Komponenten hinzufuegen -> Zaehler")
    print(f"  IP {args.host}, Port {args.port}, Modbus-Adresse {args.unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
