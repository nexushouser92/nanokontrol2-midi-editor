"""Interface texts in English and Spanish, chosen from the Windows display language.

Texts are written in English in the code and wrapped in tr(); in Spanish, tr() looks them up in ES.
Set KONTROL2_LANG=en or es to force a language.
"""
import ctypes
import locale
import os

ES = {
    # header and footer
    "Unofficial scene editor for the KORG nanoKONTROL 2": "Editor de escenas no oficial para el KORG nanoKONTROL 2",
    "Send to nanoKONTROL": "Enviar al nanoKONTROL",
    "Ext LEDs: {state}": "LEDs ext.: {state}",
    "ON": "SÍ",
    "OFF": "NO",
    "Searching for device...": "Buscando el equipo...",
    "Reading scene...": "Leyendo la escena...",
    "Write failed - click to retry": "Error al guardar: reintentar",
    "Writing to device...": "Guardando en el equipo...",
    "Saved to device": "Guardado en el equipo",
    "Connected": "Conectado",
    "Unofficial  ·  not affiliated with KORG  ·  v{version}": "No oficial  ·  sin relación con KORG  ·  v{version}",
    "{count} controls share a CC / Note number": "{count} controles comparten número de CC / nota",

    # sidebar
    "Global": "Global",
    "CHANNELS": "CANALES",
    "Transport": "Transporte",
    "Calibration": "Calibración",
    "LED Test": "Prueba de LEDs",
    "MIDI Monitor": "Monitor MIDI",
    "SCENE": "ESCENA",
    "Open": "Abrir",
    "Save": "Guardar",
    "Templates": "Plantillas",
    "Export CC": "Exportar CC",
    "Undo all": "Deshacer",
    "Factory reset": "Valores de fábrica",

    # dialogs and messages
    "Copy strip": "Copiar canal",
    "Copy strip {n} onto all other strips?\nTheir current settings are replaced.":
        "¿Copiar el canal {n} en todos los demás canales?\nSe reemplaza su configuración actual.",
    "Strip {n} copied to {names}.": "Canal {n} copiado a {names}.",
    "Apply template": "Aplicar plantilla",
    "Apply '{name}'?\nThis replaces the assignments of every control.":
        "¿Aplicar «{name}»?\nSe reemplazan las asignaciones de todos los controles.",
    "Template applied: {name}.": "Plantilla aplicada: {name}.",
    "Reset ranges": "Restablecer rangos",
    "Set every fader and knob back to the full 0-127 range?":
        "¿Devolver todos los faders y perillas al rango completo 0-127?",
    "All fader and knob ranges reset to 0-127.": "Rangos de faders y perillas restablecidos a 0-127.",
    "Sending the scene to the nanoKONTROL 2...": "Enviando la escena al nanoKONTROL 2...",
    "Reset ALL settings to factory defaults?\nEvery assignment and mode is replaced.":
        "¿Volver a los valores de fábrica en TODOS los ajustes?\nSe reemplazan todas las asignaciones y modos.",
    "Factory defaults applied.": "Valores de fábrica aplicados.",
    "Undo all changes": "Deshacer todos los cambios",
    "Restore the scene as it was when the device connected?":
        "¿Restaurar la escena tal como estaba al conectar el equipo?",
    "Restored the original scene.": "Escena original restaurada.",
    "Open preset": "Abrir preset",
    "Save preset": "Guardar preset",
    "Preset or SysEx": "Preset o SysEx",
    "JSON preset": "Preset JSON",
    "SysEx dump": "Volcado SysEx",
    "Could not open the file: {error}": "No se pudo abrir el archivo: {error}",
    "Opened {name}.": "Abierto: {name}.",
    "Could not save the file: {error}": "No se pudo guardar el archivo: {error}",
    "Saved {name}.": "Guardado: {name}.",
    "Export CC overview": "Exportar resumen de CC",
    "CSV spreadsheet": "Hoja de cálculo CSV",
    "Could not export: {error}": "No se pudo exportar: {error}",
    "Exported {name}.": "Exportado: {name}.",
    "{name} is disabled in the scene: it has no LED message.":
        "{name} está desactivado en la escena: no tiene mensaje de LED.",
    "Could not send the LED message: is the nanoKONTROL 2 linked?":
        "No se pudo enviar el mensaje de LED: ¿está conectado el nanoKONTROL 2?",
    "Move or press a control on the device...": "Mueve o pulsa un control del equipo...",
    "Learned {kind} {number}.": "Aprendido: {kind} {number}.",
    "Reading the scene...": "Leyendo la escena...",
    "Scene read from the device.": "Escena leída del equipo.",
    "Write failed: {error}": "Error al guardar: {error}",
    "Port busy: close the other program using the device.":
        "Puerto ocupado: cierra el otro programa que usa el equipo.",

    # waiting page
    "Looking for your nanoKONTROL 2": "Buscando tu nanoKONTROL 2",
    "Connect it with USB and close any other program that uses it.":
        "Conéctalo por USB y cierra cualquier otro programa que lo use.",
    "You can also open a saved preset from the sidebar.":
        "También puedes abrir un preset guardado desde la barra lateral.",

    # global page
    "Settings that apply to the whole scene": "Ajustes que afectan a toda la escena",
    "Global MIDI channel": "Canal MIDI global",
    "Used by every strip set to Global": "Lo usan los canales configurados como Global",
    "Control mode": "Modo de control",
    "How the nanoKONTROL 2 talks to your DAW": "Cómo se comunica el nanoKONTROL 2 con tu DAW",
    "LED mode": "Modo de LEDs",
    "Internal: the device lights its LEDs  ·  External: your DAW does":
        "Interno: el equipo enciende sus LEDs  ·  Externo: los enciende tu DAW",
    "Internal": "Interno",
    "External": "Externo",
    "Custom DAW assign": "Asignación DAW personalizada",
    "Five scene slots for custom DAW assignments": "Cinco posiciones de la escena para asignaciones DAW personalizadas",
    "Assign 1": "Asignación 1",
    "Assign 2": "Asignación 2",
    "Assign 3": "Asignación 3",
    "Assign 4": "Asignación 4",
    "Assign 5": "Asignación 5",

    # channel and transport pages
    "Disabled": "Desactivado",
    "Note": "Nota",
    "Momentary": "Momentáneo",
    "Toggle": "Alternar",
    "Learn": "Aprender",
    "Cancel": "Cancelar",
    "MIDI channel": "Canal MIDI",
    "CONTROL": "CONTROL",
    "TYPE": "TIPO",
    "CC / NOTE": "CC / NOTA",
    "MIN  /  OFF": "MÍN  /  APAG.",
    "MAX  /  ON": "MÁX  /  ENC.",
    "RANGE  /  BEHAVIOUR": "RANGO  /  MODO",
    "Slider": "Fader",
    "Knob": "Perilla",
    "Prime (Arm)": "Rec (Arm)",
    "Copy this strip to other strips": "Copiar este canal a otros canales",
    "Copies assignments and ranges; CC / Note numbers shift with the strip so they stay unique":
        "Copia asignaciones y rangos; los números de CC / nota se desplazan con el canal para no repetirse",
    "Copy to…": "Copiar a…",
    "All other channels": "Todos los demás canales",
    "Channel {n}": "Canal {n}",
    "Assignments for strip {n}": "Asignaciones del canal {n}",
    "Play, record and marker buttons": "Botones de reproducción, grabación y marcadores",
    "Prev Track": "Pista ant.",
    "Next Track": "Pista sig.",
    "Cycle": "Ciclo",
    "Set Marker": "Marcador",
    "Prev Marker": "Marcador ant.",
    "Next Marker": "Marcador sig.",
    "Rewind": "Rebobinar",
    "Fast Forward": "Avanzar",
    "Record": "Grabar",

    # calibration page
    "Min is sent at the lowest position of a fader or knob and Max at the highest; swap them to invert.":
        "Mín se envía en la posición más baja de un fader o perilla y Máx en la más alta; intercámbialos para invertir.",
    "Drag the handles or type a value. Double-click a bar to reset it. Move a control to see its live position.":
        "Arrastra los extremos o escribe un valor. Doble clic en una barra la restablece. "
        "Mueve un control para ver su posición en vivo.",
    "Reset all ranges": "Restablecer rangos",
    "FADERS": "FADERS",
    "KNOBS": "PERILLAS",
    "RANGE": "RANGO",
    "MIN": "MÍN",
    "MAX": "MÁX",
    "Fader {n}": "Fader {n}",
    "Knob {n}": "Perilla {n}",

    # LED test page
    "Light the button LEDs from here (not stored in the scene)":
        "Enciende los LEDs de los botones desde aquí (no se guarda en la escena)",
    "CHANNEL BUTTONS": "BOTONES DE CANAL",
    "TRANSPORT BUTTONS": "BOTONES DE TRANSPORTE",
    "Arm": "Rec",
    "On": "Enc.",
    "Off": "Apag.",
    "All off": "Apagar todo",
    "All on": "Encender todo",
    "External LED mode is on: LED commands are accepted.":
        "El modo de LEDs externo está activado: el equipo acepta los mensajes de LED.",
    "External LED mode is off. Switch it on in Global > LED mode, or with the Ext LEDs button.":
        "El modo de LEDs externo está desactivado. Actívalo en Global > Modo de LEDs o con el botón LEDs ext.",

    # MIDI monitor page
    "Messages sent by the device, newest first": "Mensajes enviados por el equipo, los más recientes primero",
    "Clear": "Borrar",
    "time": "tiempo",
    "ch": "can",
    "type": "tipo",
    "num": "núm",
    "val": "val",
    "raw": "bytes",

    # templates and CSV export
    "Factory Defaults": "Valores de fábrica",
    "Generic CC": "CC genérico",
    "Clear All": "Borrar todo",
    "Section": "Sección",
    "Control": "Control",
    "Type": "Tipo",
    "CC / Note": "CC / Nota",
    "Min / Off": "Mín / Apag.",
    "Max / On": "Máx / Enc.",
    "Behaviour": "Modo",
}

SPANISH = 0x0A  # primary language id of every Spanish locale


def detect_language():
    """'es' when the Windows display language (or, failing that, the locale) is Spanish, else 'en'."""
    forced = os.environ.get("KONTROL2_LANG", "").lower()
    if forced in ("en", "es"):
        return forced
    try:
        ui = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        ui = 0
    if ui:
        return "es" if ui & 0x3FF == SPANISH else "en"
    try:
        name = locale.getlocale()[0] or ""
    except ValueError:
        name = ""
    return "es" if name.lower().startswith(("es", "spanish")) else "en"


LANG = detect_language()


def tr(text, **values):
    """`text` in the interface language, with {placeholders} filled from `values`."""
    if LANG == "es":
        text = ES.get(text, text)
    return text.format(**values) if values else text


def options(texts):
    """Translated labels for a sequence of option names (index-aligned with it)."""
    return [tr(t) for t in texts]
