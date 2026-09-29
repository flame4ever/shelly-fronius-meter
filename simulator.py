#!/usr/bin/env python3
"""
Fronius Smart Meter Emulator fuer Shelly Pro 3EM
=================================================

Stellt die Messwerte eines Shelly Pro 3EM ueber Modbus TCP so bereit,
wie ein Fronius Smart Meter sie liefert (SunSpec Model 213, float).

Basiert auf https://github.com/simontribus/froniussimulator_shellypro3em
(wiederum auf tichachm/fronius_smart_meter_modbus_tcp_emulator).

Gegenueber dem Original geaendert:
  1. Registerbasis 40001 statt 1   -> GEN24 ab FW 1.4x fragt bei 40000 an
  2. Slave-ID frei konfigurierbar  -> Sekundaerzaehler braucht eine eigene ID
  3. Leistungsfaktor-Register PF / PFphA..C ergaenzt
     Das Original liess sie weg, wodurch ALLES danach um 8 Register
     verschoben war. Der Wechselrichter las dadurch den Wh-Zaehlerstand
     als Leistungsfaktor (z.B. 44531.898) und verwarf die Daten.
  4. Model-213-Block vollstaendig auf 124 Register (vorher 82)
  5. Abbruchsicher: Timeouts, Fehlerbehandlung, Lock wird immer freigegeben

Version 2.0.0
"""

import asyncio
import datetime
import struct
import threading

import requests
from pymodbus.datastore import ModbusServerContext, ModbusSparseDataBlock
from pymodbus.server import ModbusTcpServer

# pymodbus hat die Klasse ab Version 3.10 von ModbusSlaveContext in
# ModbusDeviceContext umbenannt. Beide Namen abfangen, damit das Skript
# mit alten und neuen Installationen laeuft.
try:
    from pymodbus.datastore import ModbusSlaveContext as _DeviceContext
except ImportError:                               # pymodbus >= 3.10
    from pymodbus.datastore import ModbusDeviceContext as _DeviceContext

# ===========================================================================
#  KONFIGURATION  -  nur diesen Block anpassen
# ===========================================================================

# IP des Shelly Pro 3EM
SHELLY_IP = "192.168.1.100"

# Modbus-Adresse, unter der dieser Zaehler antwortet.
# Muss mit der "Modbus Adresse" im Fronius unter Komponenten uebereinstimmen.
#   Primaerzaehler / Einspeisepunkt : meist 1
#   Zusaetzlicher Zaehler           : 2, 3, ...
SLAVE_ID = 2

# TCP-Port. Der GEN24 spricht ausschliesslich 502 an.
# Ports unter 1024 brauchen root -> Dienst als User=root laufen lassen.
MODBUS_PORT = 502

# Seriennummer, die der Zaehler meldet. Bei mehreren Emulator-Instanzen
# im selben System muss jede eine eigene bekommen.
SERIAL_NUMBER = "00000002"

# Abfrageintervall in Sekunden (der GEN24 pollt etwa im Sekundentakt)
POLL_INTERVAL = 2.0

# Timeout fuer die HTTP-Abfrage des Shelly
REQUEST_TIMEOUT = 3.0

# Skalierung des Leistungsfaktors.
#   100.0 -> Prozent (SunSpec-konform, 0.98 wird als 98.0 gesendet)
#     1.0 -> roher Faktor (0.98 wird als 0.98 gesendet)
# Falls der Fronius unplausible PF-Werte meldet, hier auf 1.0 stellen.
PF_SCALE = 100.0

# Registerbasis. 40000 = SunSpec-Standard, so fragt der GEN24 an.
# Nur aendern, wenn ein Geraet die Kennung an anderer Stelle erwartet.
BASE_OFFSET = 40000

# Ausfuehrliche Ausgabe der gelesenen Werte bei jedem Zyklus
VERBOSE = False

# ===========================================================================

SHELLY_URL = f"http://{SHELLY_IP}/rpc/Shelly.GetStatus"

lock = threading.Lock()

# Zuletzt gelesene Messwerte. Bleiben bei einem Abfragefehler stehen,
# damit der Wechselrichter keine Nullen serviert bekommt.
messwerte = {
    "leistung": 0.0,        # Gesamtwirkleistung [W]
    "netzbezug": 0.0,       # Zaehlerstand Bezug [Wh]
    "einspeisung": 0.0,     # Zaehlerstand Einspeisung [Wh]
    "l1": 0.0, "l2": 0.0, "l3": 0.0,                    # Wirkleistung je Phase [W]
    "l1_v": 0.0, "l2_v": 0.0, "l3_v": 0.0,              # Spannung je Phase [V]
    "current": 0.0,                                      # Gesamtstrom [A]
    "l1_c": 0.0, "l2_c": 0.0, "l3_c": 0.0,              # Strom je Phase [A]
    "avg_freq": 0.0,                                     # Frequenz [Hz]
    "aparent_power": 0.0,                                # Scheinleistung gesamt [VA]
    "l1_aprt_pw": 0.0, "l2_aprt_pw": 0.0, "l3_aprt_pw": 0.0,   # Scheinleistung je Phase [VA]
    "pf": 0.0, "l1_pf": 0.0, "l2_pf": 0.0, "l3_pf": 0.0,       # Leistungsfaktor
}


def log(text):
    print(f"{datetime.datetime.now()}: {text}", flush=True)


# ---------------------------------------------------------------------------
#  Shelly auslesen
# ---------------------------------------------------------------------------

def fetch_shelly_data():
    """Liest den Shelly per REST-API. Bei Fehler bleiben die alten Werte stehen."""
    try:
        response = requests.get(SHELLY_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        em = data["em:0"]
        emdata = data["emdata:0"]

        messwerte["leistung"] = em["total_act_power"]
        messwerte["netzbezug"] = emdata["total_act"]
        messwerte["einspeisung"] = emdata["total_act_ret"]

        messwerte["l1"] = em["a_act_power"]
        messwerte["l2"] = em["b_act_power"]
        messwerte["l3"] = em["c_act_power"]

        messwerte["l1_v"] = em["a_voltage"]
        messwerte["l2_v"] = em["b_voltage"]
        messwerte["l3_v"] = em["c_voltage"]

        messwerte["current"] = em["total_current"]
        messwerte["l1_c"] = em["a_current"]
        messwerte["l2_c"] = em["b_current"]
        messwerte["l3_c"] = em["c_current"]

        messwerte["l1_aprt_pw"] = em["a_aprt_power"]
        messwerte["l2_aprt_pw"] = em["b_aprt_power"]
        messwerte["l3_aprt_pw"] = em["c_aprt_power"]
        messwerte["aparent_power"] = em["total_aprt_power"]

        messwerte["avg_freq"] = (
            em["a_freq"] + em["b_freq"] + em["c_freq"]
        ) / 3.0

        # Leistungsfaktor je Phase liefert der Shelly direkt mit.
        messwerte["l1_pf"] = em.get("a_pf") or 0.0
        messwerte["l2_pf"] = em.get("b_pf") or 0.0
        messwerte["l3_pf"] = em.get("c_pf") or 0.0

        # Gesamt-Leistungsfaktor = Wirkleistung / Scheinleistung
        aprt = messwerte["aparent_power"]
        messwerte["pf"] = (messwerte["leistung"] / aprt) if aprt else 0.0

        if VERBOSE:
            log(
                f"Shelly: {messwerte['leistung']:.1f} W  "
                f"L1 {messwerte['l1']:.1f}  L2 {messwerte['l2']:.1f}  "
                f"L3 {messwerte['l3']:.1f}  PF {messwerte['pf']:.3f}"
            )
        return True

    except (requests.RequestException, KeyError, ValueError) as err:
        log(f"FEHLER beim Lesen des Shelly ({SHELLY_IP}): {err}")
        return False


# ---------------------------------------------------------------------------
#  Float -> zwei Modbus-Register
# ---------------------------------------------------------------------------

def f32(value):
    """Wandelt einen Float in zwei 16-Bit-Register (big endian)."""
    try:
        raw = struct.unpack(">HH", struct.pack(">f", float(value)))
        return [raw[0], raw[1]]
    except (struct.error, TypeError, ValueError, OverflowError):
        return [0, 0]


def u32(value):
    """Wandelt eine vorzeichenlose 32-Bit-Zahl in zwei Register."""
    v = int(value) & 0xFFFFFFFF
    return [(v >> 16) & 0xFFFF, v & 0xFFFF]


def text_register(text, length):
    """ASCII-Text, ein Zeichen pro Register, auf feste Laenge aufgefuellt.

    Diese Kodierung stammt aus dem Original und wird vom Fronius
    akzeptiert - bitte nicht 'korrigieren', sonst wird der Zaehler
    nicht mehr erkannt.
    """
    werte = [ord(c) for c in text[:length]]
    werte += [0] * (length - len(werte))
    return werte


# ---------------------------------------------------------------------------
#  SunSpec Model 213 - Messwerte schreiben
# ---------------------------------------------------------------------------

def build_model_213():
    """Baut den kompletten 124-Register-Datenblock des Model 213 (float).

    Reihenfolge exakt nach SunSpec-Spezifikation. Die vier PF-Register
    zwischen Blindleistung und Wh-Zaehlern sind der Grund, warum das
    Original nicht funktioniert hat - fehlen sie, ist alles danach
    um acht Register verschoben.
    """
    m = messwerte
    pf = PF_SCALE

    floats = [
        m["current"],                 # A       - Strom gesamt
        m["l1_c"],                    # AphA
        m["l2_c"],                    # AphB
        m["l3_c"],                    # AphC

        0.0,                          # PhV     - Spannung L-N Mittel
        m["l1_v"],                    # PhVphA
        m["l2_v"],                    # PhVphB
        m["l3_v"],                    # PhVphC

        0.0,                          # PPV     - Spannung L-L Mittel
        0.0,                          # PhVphAB
        0.0,                          # PhVphBC
        0.0,                          # PhVphCA

        m["avg_freq"],                # Hz      - Frequenz

        m["leistung"],                # W       - Wirkleistung gesamt
        m["l1"],                      # WphA
        m["l2"],                      # WphB
        m["l3"],                      # WphC

        m["aparent_power"],           # VA      - Scheinleistung gesamt
        m["l1_aprt_pw"],              # VAphA
        m["l2_aprt_pw"],              # VAphB
        m["l3_aprt_pw"],              # VAphC

        0.0,                          # VAR     - Blindleistung gesamt
        0.0,                          # VARphA
        0.0,                          # VARphB
        0.0,                          # VARphC

        m["pf"] * pf,                 # PF      - Leistungsfaktor gesamt  <-- ergaenzt
        m["l1_pf"] * pf,              # PFphA                             <-- ergaenzt
        m["l2_pf"] * pf,              # PFphB                             <-- ergaenzt
        m["l3_pf"] * pf,              # PFphC                             <-- ergaenzt

        m["einspeisung"],             # TotWhExp - Zaehlerstand Einspeisung
        0.0,                          # TotWhExpPhA
        0.0,                          # TotWhExpPhB
        0.0,                          # TotWhExpPhC

        m["netzbezug"],               # TotWhImp - Zaehlerstand Bezug
        0.0,                          # TotWhImpPhA
        0.0,                          # TotWhImpPhB
        0.0,                          # TotWhImpPhC

        0.0,                          # TotVAhExp
        0.0,                          # TotVAhExpPhA
        0.0,                          # TotVAhExpPhB
        0.0,                          # TotVAhExpPhC

        0.0,                          # TotVAhImp
        0.0,                          # TotVAhImpPhA
        0.0,                          # TotVAhImpPhB
        0.0,                          # TotVAhImpPhC

        0.0,                          # TotVArhImpQ1
        0.0,                          # TotVArhImpQ1PhA
        0.0,                          # TotVArhImpQ1PhB
        0.0,                          # TotVArhImpQ1PhC

        0.0,                          # TotVArhImpQ2
        0.0,                          # TotVArhImpQ2PhA
        0.0,                          # TotVArhImpQ2PhB
        0.0,                          # TotVArhImpQ2PhC

        0.0,                          # TotVArhExpQ3
        0.0,                          # TotVArhExpQ3PhA
        0.0,                          # TotVArhExpQ3PhB
        0.0,                          # TotVArhExpQ3PhC

        0.0,                          # TotVArhExpQ4
        0.0,                          # TotVArhExpQ4PhA
        0.0,                          # TotVArhExpQ4PhB
        0.0,                          # TotVArhExpQ4PhC
    ]

    werte = []
    for f in floats:
        werte += f32(f)
    werte += u32(0)                   # Evt - Ereignis-Flags, 0 = keine Stoerung

    assert len(werte) == 124, f"Model 213 muss 124 Register haben, hat {len(werte)}"
    return werte


def updating_writer(a_context):
    """Holt die Shelly-Werte und schreibt sie in den Modbus-Datenblock."""
    if not lock.acquire(timeout=5):
        log("WARNUNG: Lock nicht bekommen, Zyklus wird uebersprungen")
        return
    try:
        fetch_shelly_data()

        context = a_context[SLAVE_ID]

        # setValues() rechnet intern +1, deshalb 0x0047 (=71) fuer
        # den ersten Datenregister-Slot des Model 213.
        context.setValues(3, 0x0047 + BASE_OFFSET, build_model_213())

    except Exception as err:                      # noqa: BLE001
        log(f"FEHLER im Schreibzyklus: {err}")
    finally:
        lock.release()


# ---------------------------------------------------------------------------
#  Datenblock aufbauen
# ---------------------------------------------------------------------------

def build_datablock():
    """SunSpec-Registerkarte. Schluessel N entspricht Register 40000+N."""
    roh = {
        # --- SunSpec-Kennung -------------------------------------------------
        1: [21365, 28243],            # "SunS"

        # --- Common Model (ID 1, Laenge 65) ----------------------------------
        3: [1],                       # Model-ID 1
        4: [65],                      # Laenge
        5: (
            text_register("Fronius", 16)            # Mn - Hersteller
            + text_register("Smart Meter 63A", 16)  # Md - Modell
            + [0] * 8                               # Opt
            + [0] * 8                               # Vr
            + text_register(SERIAL_NUMBER, 16)      # SN
            + [240]                                 # DA - Modbus-Adresse
        ),

        # --- Meter Model (ID 213, Laenge 124) --------------------------------
        70: [213],
        71: [124],
        72: [0] * 124,                # wird von updating_writer gefuellt

        # --- Ende der Registerkarte ------------------------------------------
        196: [65535, 0],
    }

    # Alles um BASE_OFFSET nach oben schieben, damit "SunS" auf
    # Register 40001 liegt (Wire-Adresse 40000, wie der GEN24 anfragt).
    return ModbusSparseDataBlock(
        {schluessel + BASE_OFFSET: wert for schluessel, wert in roh.items()}
    )


# ---------------------------------------------------------------------------
#  Server
# ---------------------------------------------------------------------------

class RepeatedTimer:
    def __init__(self, interval, function, *args, **kwargs):
        self._timer = None
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.is_running = False
        self.start()

    def _run(self):
        self.is_running = False
        self.start()
        try:
            self.function(*self.args, **self.kwargs)
        except Exception as err:                   # noqa: BLE001
            log(f"FEHLER im Timer: {err}")

    def start(self):
        if not self.is_running:
            self._timer = threading.Timer(self.interval, self._run)
            self._timer.daemon = True
            self._timer.start()
            self.is_running = True

    def stop(self):
        if self._timer:
            self._timer.cancel()
        self.is_running = False


async def run_updating_server():
    slave_store = _DeviceContext(
        di=build_datablock(),
        co=build_datablock(),
        hr=build_datablock(),
        ir=build_datablock(),
    )
    a_context = ModbusServerContext(slaves={SLAVE_ID: slave_store}, single=False)

    # Erste Werte sofort holen, damit der Zaehler nicht mit Nullen startet
    updating_writer(a_context)

    timer = RepeatedTimer(POLL_INTERVAL, updating_writer, a_context)

    log("### Fronius Smart Meter Emulator 2.0.0")
    log(f"### Shelly        : {SHELLY_IP}")
    log(f"### Modbus        : 0.0.0.0:{MODBUS_PORT}, Slave-ID {SLAVE_ID}")
    log(f"### Registerbasis : {BASE_OFFSET} (SunS auf Register {BASE_OFFSET + 1})")

    server = ModbusTcpServer(context=a_context, address=("0.0.0.0", MODBUS_PORT))
    try:
        await server.serve_forever()
    finally:
        timer.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run_updating_server())
    except KeyboardInterrupt:
        log("### Beendet")
