#!/usr/bin/env python3
"""
Alertas meteorológicas CABA
GCBA · Jefatura de Gabinete de Ministros

Cada ciclo (por defecto cada 15 minutos, desde GitHub Actions):
  1. Descarga el pronóstico horario de Open-Meteo para las 15 comunas.
  2. Descarga las alertas oficiales del SMN (formato CAP) y se queda con las que tocan CABA.
  3. Evalúa los umbrales de config.yaml (criterios tipo SMN: amarillo, naranja, rojo).
  4. Compara con el ciclo anterior y avisa por Telegram solo lo nuevo:
     alertas que aparecen, que suben o bajan de nivel, y las que finalizan.
  5. Responde comandos del bot (/estado, /umbrales, /ayuda).
  6. Escribe docs/data/alertas.json, que lee el tablero.

Uso:
  python alertas.py                    ciclo normal
  python alertas.py --dry-run          muestra los mensajes en pantalla, no envía nada
  python alertas.py --modo prueba      manda un mensaje de prueba ("bot conectado")
  python alertas.py --modo simulacro   manda una alerta ficticia marcada SIMULACRO
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
NIVELES = ["Sin alerta", "Amarillo", "Naranja", "Rojo"]
ALERTA = ["SIN ALERTA", "ALERTA AMARILLA", "ALERTA NARANJA", "ALERTA ROJA"]
ICONO = ["⚪", "🟡", "🟠", "🔴"]
NOMBRE_FENOMENO = {
    "lluvia": "Lluvias", "viento": "Viento", "tormenta": "Tormentas",
    "calor": "Temperaturas extremas · calor", "frio": "Temperaturas extremas · frío",
}
UA = {"User-Agent": "alertas-clima-caba/1.0 (+https://github.com)"}
ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------
def log(*a):
    print(datetime.now(TZ).strftime("%H:%M:%S"), *a, flush=True)


def ahora() -> datetime:
    return datetime.now(TZ)


def parse_local(s: str) -> datetime:
    """Open-Meteo devuelve horas locales sin zona: '2026-11-02T15:00'."""
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def nivel_por_umbral(valor, umbrales: dict) -> int:
    """umbrales = {amarillo: x, naranja: y, rojo: z} (pueden faltar claves)."""
    if valor is None:
        return 0
    n = 0
    for i, k in enumerate(["amarillo", "naranja", "rojo"], start=1):
        u = umbrales.get(k)
        if u is not None and valor >= u:
            n = i
    return n


def max_racha(flags: list[bool]) -> int:
    mejor = actual = 0
    for f in flags:
        actual = actual + 1 if f else 0
        mejor = max(mejor, actual)
    return mejor


def fmt_hora(dt: datetime, ref: datetime | None = None) -> str:
    ref = ref or ahora()
    d = (dt.date() - ref.date()).days
    dia = "hoy" if d == 0 else "mañana" if d == 1 else dt.strftime("%d/%m")
    return f"{dia} {dt:%H:%M}"


def lista_comunas(nums: list[int], total: int) -> str:
    if len(nums) == total:
        return "Toda la Ciudad"
    nums = sorted(nums)
    return ("Comuna " if len(nums) == 1 else "Comunas ") + ", ".join(map(str, nums))


def num(v, dec=0):
    if v is None:
        return "—"
    return f"{v:.{dec}f}".replace(".", ",")


# ---------------------------------------------------------------------------
# 1. Open-Meteo
# ---------------------------------------------------------------------------
def descargar_open_meteo(comunas: list[dict]) -> list[dict]:
    params = {
        "latitude": ",".join(str(c["lat"]) for c in comunas),
        "longitude": ",".join(str(c["lon"]) for c in comunas),
        "hourly": "temperature_2m,precipitation,precipitation_probability,weather_code,"
                  "wind_speed_10m,wind_gusts_10m,cape",
        "daily": "temperature_2m_max,temperature_2m_min",
        "past_hours": 24,
        "forecast_days": 7,
        "timezone": "America/Argentina/Buenos_Aires",
        "wind_speed_unit": "kmh",
    }
    r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, headers=UA, timeout=40)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else [data]


# ---------------------------------------------------------------------------
# 2. Evaluación con criterios tipo SMN
#    (la misma lógica está replicada en docs/index.html)
# ---------------------------------------------------------------------------
def evaluar_comuna(d: dict, U: dict, horizonte: int, dias: int) -> dict:
    """Devuelve, para una comuna, el nivel por hora de cada fenómeno y sus máximos."""
    H = d["hourly"]
    t = [parse_local(x) for x in H["time"]]
    hora_actual = ahora().replace(minute=0, second=0, microsecond=0)
    i0 = next((i for i, x in enumerate(t) if x >= hora_actual), 0)
    rng = range(i0, min(i0 + horizonte, len(t)))
    p = [x or 0 for x in H["precipitation"]]

    def suma(i, n):
        return sum(p[max(0, i - n + 1): i + 1])

    out = {"horas": [t[i] for i in rng]}

    # --- lluvia ---
    UL = U["lluvia"]
    # nivel del acumulado que termina en cada hora (12 h y 24 h hacia atrás)
    fin = min(i0 + horizonte, len(t))
    acum = {}
    for i in range(i0, min(fin + 23, len(t))):
        acum[i] = max(nivel_por_umbral(suma(i, 12), UL["acumulado_12h_mm"]),
                      nivel_por_umbral(suma(i, 24), UL["acumulado_24h_mm"]))
    niv, r12m, r24m, rhm = [], 0, 0, 0
    for i in rng:
        r12, r24, rh = suma(i, 12), suma(i, 24), p[i]
        r12m, r24m, rhm = max(r12m, r12), max(r24m, r24), max(rhm, rh)
        # una hora con lluvia queda en alerta si forma parte de un acumulado que supera el umbral
        n_acum = max((acum.get(j, 0) for j in range(i, i + 24)), default=0) if rh >= 1 else 0
        niv.append(max(n_acum, nivel_por_umbral(rh, UL.get("intensidad_horaria_mm", {}))))
    out["lluvia"] = {"niveles": niv, "max": {"lluvia_12h": r12m, "lluvia_24h": r24m, "lluvia_1h": rhm}}

    # --- viento (con duración mínima) ---
    UV = U["viento"]
    ws = [H["wind_speed_10m"][i] or 0 for i in rng]
    wg = [H["wind_gusts_10m"][i] or 0 for i in rng]
    niv = [max(nivel_por_umbral(a, UV["sostenido_kmh"]), nivel_por_umbral(b, UV["rafagas_kmh"])) for a, b in zip(ws, wg)]
    nivel_evento = 0
    for L in (3, 2, 1):
        if max_racha([n >= L for n in niv]) >= UV.get("horas_minimas", 1):
            nivel_evento = L
            break
    niv = [min(n, nivel_evento) for n in niv]
    out["viento"] = {"niveles": niv, "max": {"viento": max(ws, default=0), "rafagas": max(wg, default=0)}}

    # --- tormenta ---
    UT = U["tormenta"]
    niv, granizo = [], False
    for k, i in enumerate(rng):
        code = H["weather_code"][i] or 0
        cape = H["cape"][i] or 0
        prob = H["precipitation_probability"][i] or 0
        hay = code >= 95 or (UT.get("usar_cape") and cape >= UT["cape_minimo"] and prob >= UT["probabilidad_minima"])
        if not hay:
            niv.append(0)
            continue
        r12, g = suma(i, 12), wg[k]
        n = 1
        if (UT["naranja"].get("granizo") and code in (96, 99)) or r12 >= UT["naranja"]["lluvia_12h_mm"] or g >= UT["naranja"]["rafagas_kmh"]:
            n = 2
        if r12 >= UT["rojo"]["lluvia_12h_mm"] or g >= UT["rojo"]["rafagas_kmh"]:
            n = 3
        granizo = granizo or code in (96, 99)
        niv.append(n)
    out["tormenta"] = {"niveles": niv, "max": {"granizo": granizo}}

    # --- temperaturas extremas (diarias) ---
    D = d["daily"]
    hoy = ahora().date()
    idx = [j for j, s in enumerate(D["time"]) if datetime.fromisoformat(s).date() >= hoy][:dias]
    tx = [D["temperature_2m_max"][j] for j in idx]
    tn = [D["temperature_2m_min"][j] for j in idx]
    fechas = [datetime.fromisoformat(D["time"][j]).replace(tzinfo=TZ) for j in idx]

    UC = U["calor"]
    cal = [(a or -99) >= UC["tmax"] and (b or -99) >= UC["tmin"] for a, b in zip(tx, tn)]
    niv_dia = [0] * len(cal)
    j = 0
    while j < len(cal):
        if not cal[j]:
            j += 1
            continue
        k = j
        while k < len(cal) and cal[k]:
            k += 1
        racha = k - j
        n = 0
        if racha >= UC["dias_consecutivos"]["amarillo"]:
            n = 1
        if racha >= UC["dias_consecutivos"]["naranja"]:
            n = 2
            if max(tx[j:k]) >= UC["rojo_tmax"]:
                n = 3
        for m in range(j, k):
            niv_dia[m] = n
        j = k
    out["calor"] = {"dias": fechas, "niveles_dia": niv_dia, "max": {"tmax": max([x for x in tx if x is not None], default=None)}}

    UF = U.get("frio", {})
    if UF.get("activo", True):
        nf = [0 if b is None else (3 if b <= UF["tmin"]["rojo"] else 2 if b <= UF["tmin"]["naranja"] else 1 if b <= UF["tmin"]["amarillo"] else 0) for b in tn]
    else:
        nf = [0] * len(tn)
    out["frio"] = {"dias": fechas, "niveles_dia": nf, "max": {"tmin": min([x for x in tn if x is not None], default=None)}}
    return out


def agregar_eventos(evals: list[dict], comunas: list[dict]) -> list[dict]:
    """Junta las 15 comunas en un evento por fenómeno (nivel máximo, ventana y comunas)."""
    eventos = []
    for f in ("lluvia", "viento", "tormenta"):
        nivel, ini, fin, afectadas, mx = 0, None, None, [], {}
        for c, e in zip(comunas, evals):
            niv = e[f]["niveles"]
            if max(niv, default=0) >= 1:
                afectadas.append(c["n"])
                nivel = max(nivel, max(niv))
                horas = [h for h, n in zip(e["horas"], niv) if n >= 1]
                ini = min([ini, horas[0]]) if ini else horas[0]
                fin = max([fin, horas[-1]]) if fin else horas[-1]
            for k, v in e[f]["max"].items():
                mx[k] = (mx.get(k) or v) if isinstance(v, bool) else max(mx.get(k, 0), v or 0)
        if nivel:
            eventos.append({"id": f"int:{f}", "fuente": "pronostico", "fenomeno": f, "nivel": nivel,
                            "desde": ini.isoformat(), "hasta": (fin + timedelta(hours=1)).isoformat(),
                            "comunas": afectadas, "max": mx})
    for f in ("calor", "frio"):
        nivel, ini, fin, afectadas, mx = 0, None, None, [], {}
        for c, e in zip(comunas, evals):
            niv = e[f]["niveles_dia"]
            if max(niv, default=0) >= 1:
                afectadas.append(c["n"])
                nivel = max(nivel, max(niv))
                dias = [d for d, n in zip(e[f]["dias"], niv) if n >= 1]
                ini = min([ini, dias[0]]) if ini else dias[0]
                fin = max([fin, dias[-1]]) if fin else dias[-1]
            v = e[f]["max"].get("tmax" if f == "calor" else "tmin")
            if v is not None:
                key = "tmax" if f == "calor" else "tmin"
                mx[key] = max(mx.get(key, v), v) if f == "calor" else min(mx.get(key, v), v)
        if nivel:
            eventos.append({"id": f"int:{f}", "fuente": "pronostico", "fenomeno": f, "nivel": nivel,
                            "desde": ini.isoformat(), "hasta": (fin + timedelta(days=1)).isoformat(),
                            "comunas": afectadas, "max": mx})
    return eventos


def detalle_evento(e: dict) -> str:
    m, f = e.get("max", {}), e["fenomeno"]
    if f == "lluvia":
        return (f"acumulado hasta {num(m.get('lluvia_12h'))} mm en 12 h "
                f"(máx. {num(m.get('lluvia_1h'), 1)} mm en 1 h)")
    if f == "viento":
        return f"viento medio hasta {num(m.get('viento'))} km/h, ráfagas hasta {num(m.get('rafagas'))} km/h"
    if f == "tormenta":
        return "tormentas" + (" con posible granizo" if m.get("granizo") else "")
    if f == "calor":
        return f"temperaturas máximas de hasta {num(m.get('tmax'))} °C, con noches cálidas"
    if f == "frio":
        return f"temperaturas mínimas de hasta {num(m.get('tmin'))} °C"
    return ""


# ---------------------------------------------------------------------------
# 3. SMN · alertas oficiales CAP
# ---------------------------------------------------------------------------
CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
SEVERIDAD = {"Minor": 1, "Moderate": 1, "Severe": 2, "Extreme": 3}


def punto_en_poligono(lat, lon, poly):
    dentro = False
    j = len(poly) - 1
    for i in range(len(poly)):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            dentro = not dentro
        j = i
    return dentro


def parsear_cap(xml_text: str) -> dict | None:
    root = ET.fromstring(xml_text)
    g = lambda el, tag: (el.findtext(f"cap:{tag}", default="", namespaces=CAP_NS) or "").strip()
    if g(root, "status") != "Actual":
        return None
    infos = root.findall("cap:info", CAP_NS)
    if not infos:
        return None
    info = next((i for i in infos if g(i, "language").lower().startswith("es")), infos[0])
    polys = []
    for area in info.findall("cap:area", CAP_NS):
        for pg in area.findall("cap:polygon", CAP_NS):
            pts = []
            for par in (pg.text or "").split():
                try:
                    la, lo = par.split(",")
                    pts.append((float(la), float(lo)))
                except ValueError:
                    pass
            if len(pts) >= 3:
                polys.append(pts)
    return {
        "identificador": g(root, "identifier"), "tipo": g(root, "msgType"), "enviado": g(root, "sent"),
        "evento": g(info, "event"), "severidad": g(info, "severity"), "urgencia": g(info, "urgency"),
        "certeza": g(info, "certainty"), "desde": g(info, "onset") or g(info, "effective") or g(root, "sent"),
        "hasta": g(info, "expires"), "titulo": g(info, "headline"), "descripcion": g(info, "description"),
        "instrucciones": g(info, "instruction"), "web": g(info, "web") or "https://www.smn.gob.ar/alertas",
        "areas": [g(a, "areaDesc") for a in info.findall("cap:area", CAP_NS) if g(a, "areaDesc")],
        "poligonos": polys,
    }


def descargar_smn(cfg: dict, estado: dict) -> list[dict]:
    """Lee el feed CAP del SMN y devuelve las alertas vigentes que tocan alguna comuna."""
    feed = cfg["fuentes"]["smn_cap_feed"]
    r = requests.get(feed, headers=UA, timeout=30)
    r.raise_for_status()
    urls = sorted(set(re.findall(r"https?://[^\s<>\"']+?\.xml", r.text)))
    cache = estado.setdefault("cap_cache", {})
    for u in list(cache):          # olvidar las que ya no están en el feed
        if u not in urls:
            del cache[u]
    comunas = cfg["comunas"]
    # puntos de control: centro de cada comuna + Obelisco
    puntos = [(c["lat"], c["lon"], c["n"]) for c in comunas] + [(-34.6037, -58.3816, 1)]
    for u in urls:
        if u in cache:
            continue
        try:
            rx = requests.get(u, headers=UA, timeout=20)
            rx.raise_for_status()
            cap = parsear_cap(rx.content.decode("utf-8", errors="replace"))
        except Exception as ex:  # una alerta que no se puede leer no frena el resto
            log("CAP ilegible:", u, ex)
            cache[u] = {"caba": False}
            continue
        if not cap:
            cache[u] = {"caba": False}
            continue
        afect = sorted({n for la, lo, n in puntos for pg in cap["poligonos"] if punto_en_poligono(la, lo, pg)})
        texto_area = " ".join(cap["areas"]).lower()
        if not afect and ("ciudad autónoma" in texto_area or "c.a.b.a" in texto_area or "capital federal" in texto_area):
            afect = [c["n"] for c in comunas]
        cap.pop("poligonos")
        cache[u] = {"caba": bool(afect), "comunas": afect, **cap} if afect else {"caba": False}
    vigentes = []
    t = ahora()
    for u, c in cache.items():
        if not c.get("caba") or c.get("tipo") == "Cancel":
            continue
        try:
            if c["hasta"] and datetime.fromisoformat(c["hasta"]) < t:
                continue
        except ValueError:
            pass
        c = dict(c, url=u)
        vigentes.append(c)
    # una por tipo de evento: la de mayor severidad y más reciente
    por_evento = {}
    for c in vigentes:
        k = c["evento"] or "Alerta"
        prev = por_evento.get(k)
        if not prev or (SEVERIDAD.get(c["severidad"], 1), c["enviado"]) > (SEVERIDAD.get(prev["severidad"], 1), prev["enviado"]):
            por_evento[k] = c
    eventos = []
    for k, c in por_evento.items():
        eventos.append({
            "id": f"smn:{k.lower()}", "fuente": "smn", "fenomeno": k, "nivel": SEVERIDAD.get(c["severidad"], 1),
            "desde": c["desde"], "hasta": c["hasta"], "comunas": c["comunas"], "titulo": c["titulo"] or k,
            "descripcion": c["descripcion"], "instrucciones": c["instrucciones"], "web": c["web"], "cap": c["url"],
            "certeza": c["certeza"], "urgencia": c["urgencia"],
        })
    return eventos


# ---------------------------------------------------------------------------
# 4. Telegram
# ---------------------------------------------------------------------------
class Telegram:
    def __init__(self, token: str | None, chat_ids: list[str], dry: bool):
        self.token, self.chat_ids, self.dry = token, chat_ids, dry or not token

    def api(self, metodo: str, **datos):
        r = requests.post(f"https://api.telegram.org/bot{self.token}/{metodo}", json=datos, timeout=30)
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError(f"Telegram {metodo}: {j.get('description')}")
        return j["result"]

    def enviar(self, texto: str, chat_ids: list[str] | None = None):
        destinos = chat_ids or self.chat_ids
        if self.dry or not destinos:
            print("\n----- MENSAJE (no enviado) -----\n" + re.sub(r"<[^>]+>", "", texto) + "\n--------------------------------")
            return
        for cid in destinos:
            try:
                self.api("sendMessage", chat_id=cid, text=texto, parse_mode="HTML", disable_web_page_preview=True)
                log("Enviado a", cid)
            except Exception as ex:
                log("ERROR enviando a", cid, ex)


def e(s) -> str:
    return html.escape(str(s or ""), quote=False)


def cuando_txt(ev, desde, hasta) -> str:
    if ev["fenomeno"] in ("calor", "frio") and ev["fuente"] == "pronostico":
        dia = lambda d: fmt_hora(d).rsplit(" ", 1)[0]
        ultimo = hasta - timedelta(days=1)
        return f"🕒 <b>Cuándo:</b> {dia(desde)}" + (f" al {dia(ultimo)}" if ultimo.date() != desde.date() else "")
    return f"🕒 <b>Cuándo:</b> {fmt_hora(desde)}" + (f" a {fmt_hora(hasta)}" if hasta else "")


def msg_evento(ev: dict, cfg: dict, cambio: str, total: int, simulacro=False) -> str:
    n = ev["nivel"]
    desde, hasta = datetime.fromisoformat(ev["desde"]), datetime.fromisoformat(ev["hasta"]) if ev.get("hasta") else None
    oficial = ev["fuente"] == "smn"
    nombre = ev["titulo"] if oficial else NOMBRE_FENOMENO.get(ev["fenomeno"], ev["fenomeno"])
    encabezado = {"nueva": "", "sube": "⬆️ SUBE A ", "baja": "⬇️ BAJA A "}[cambio]
    lineas = []
    if simulacro:
        lineas.append("🧪 <b>SIMULACRO · MENSAJE DE PRUEBA</b>")
    lineas += [
        f"{ICONO[n]} <b>{encabezado}{ALERTA[n]} · {e(nombre)}</b>",
        "🏛️ <b>Oficial SMN</b>" if oficial else "📊 Pronóstico · criterios tipo SMN (no es alerta oficial)",
        "",
        cuando_txt(ev, desde, hasta),
        f"📍 <b>Dónde:</b> {lista_comunas(ev['comunas'], total)}",
    ]
    if oficial:
        if ev.get("descripcion"):
            lineas += ["", e(ev["descripcion"])]
        if ev.get("instrucciones"):
            instr = e(ev["instrucciones"])
            lineas += ["", "<b>Recomendaciones del SMN:</b>", instr[:900] + ("…" if len(instr) > 900 else "")]
        lineas += ["", f'<a href="{e(ev["web"])}">Ver alerta en el SMN</a>']
    else:
        lineas += [f"🌧️ <b>Detalle:</b> {e(detalle_evento(ev))}"]
    url = cfg["general"].get("url_tablero")
    if url and "TU-USUARIO" not in url:
        lineas += ["", f'<a href="{e(url)}">Abrir tablero de monitoreo</a>']
    return "\n".join(lineas)


def msg_fin(prev: dict) -> str:
    nombre = prev.get("nombre", "Alerta")
    return f"✅ <b>FINALIZÓ · {e(nombre)}</b>\nYa no se prevén condiciones de alerta para la Ciudad."


def msg_estado(eventos: list[dict], total: int) -> str:
    if not eventos:
        return "✅ <b>Sin alertas vigentes</b> para la Ciudad de Buenos Aires.\nNingún umbral se supera en el horizonte de pronóstico."
    lineas = ["<b>Alertas vigentes · CABA</b>", ""]
    for ev in sorted(eventos, key=lambda x: (-x["nivel"], x["fuente"] != "smn")):
        nombre = ev.get("titulo") if ev["fuente"] == "smn" else NOMBRE_FENOMENO.get(ev["fenomeno"])
        origen = "SMN oficial" if ev["fuente"] == "smn" else "pronóstico"
        desde = datetime.fromisoformat(ev["desde"])
        lineas.append(f"{ICONO[ev['nivel']]} <b>{e(nombre)}</b> · {NIVELES[ev['nivel']]} ({origen})\n"
                      f"    desde {fmt_hora(desde)} · {lista_comunas(ev['comunas'], total)}")
    return "\n".join(lineas)


def msg_umbrales(U: dict) -> str:
    L, V, T, C = U["lluvia"], U["viento"], U["tormenta"], U["calor"]
    return "\n".join([
        "<b>Umbrales configurados</b> (🟡 / 🟠 / 🔴)", "",
        f"🌧️ Lluvia 12 h: {L['acumulado_12h_mm'].get('amarillo')} / {L['acumulado_12h_mm'].get('naranja')} mm · 24 h rojo: {L['acumulado_24h_mm'].get('rojo')} mm",
        f"🌧️ Intensidad 1 h: {' / '.join(str(L.get('intensidad_horaria_mm', {}).get(k, '—')) for k in ('amarillo', 'naranja', 'rojo'))} mm",
        f"💨 Viento medio: {' / '.join(str(V['sostenido_kmh'][k]) for k in ('amarillo', 'naranja', 'rojo'))} km/h",
        f"💨 Ráfagas: {' / '.join(str(V['rafagas_kmh'][k]) for k in ('amarillo', 'naranja', 'rojo'))} km/h (mín. {V['horas_minimas']} h)",
        f"⛈️ Tormenta: naranja con ≥{T['naranja']['lluvia_12h_mm']} mm/12 h, ráfagas ≥{T['naranja']['rafagas_kmh']} km/h o granizo; rojo con ≥{T['rojo']['lluvia_12h_mm']} mm o ≥{T['rojo']['rafagas_kmh']} km/h",
        f"🌡️ Calor: Tmáx ≥{C['tmax']} °C y Tmín ≥{C['tmin']} °C durante {C['dias_consecutivos']['amarillo']} / {C['dias_consecutivos']['naranja']} días (rojo si Tmáx ≥{C['rojo_tmax']} °C)",
    ])


def responder_comandos(tg: Telegram, estado: dict, eventos: list[dict], U: dict, total: int):
    """Lee los mensajes nuevos que le escribieron al bot y responde comandos."""
    if tg.dry:
        return
    try:
        updates = tg.api("getUpdates", offset=estado.get("tg_offset", 0), timeout=0, allowed_updates=["message"])
    except Exception as ex:
        log("No se pudieron leer comandos:", ex)
        return
    for u in updates:
        estado["tg_offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        texto = (m.get("text") or "").strip().lower()
        cid = m.get("chat", {}).get("id")
        if not cid or not texto.startswith("/"):
            continue
        cmd = texto.split()[0].split("@")[0]
        if cmd in ("/estado", "/alertas"):
            tg.enviar(msg_estado(eventos, total), [cid])
        elif cmd == "/umbrales":
            tg.enviar(msg_umbrales(U), [cid])
        elif cmd in ("/start", "/ayuda", "/help"):
            tg.enviar("👋 <b>Bot de alertas meteorológicas CABA</b>\nGCBA · Jefatura de Gabinete\n\n"
                      "/estado · alertas vigentes\n/umbrales · criterios que usa el sistema\n/ayuda · este mensaje\n\n"
                      "Las respuestas pueden demorar hasta 15 minutos.", [cid])


# ---------------------------------------------------------------------------
# 5. Ciclo principal
# ---------------------------------------------------------------------------
def nombre_evento(ev):
    return ev.get("titulo") if ev["fuente"] == "smn" else NOMBRE_FENOMENO.get(ev["fenomeno"], ev["fenomeno"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--estado", default=str(ROOT / "estado" / "estado.json"))
    ap.add_argument("--salida", default=str(ROOT / "docs" / "data" / "alertas.json"))
    ap.add_argument("--modo", default="normal", choices=["normal", "prueba", "simulacro"])
    ap.add_argument("--dry-run", action="store_true", help="no envía nada, muestra los mensajes")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    G, U, comunas = cfg["general"], cfg["umbrales"], cfg["comunas"]
    total = len(comunas)
    estado_path, salida_path = Path(a.estado), Path(a.salida)
    estado_path.parent.mkdir(parents=True, exist_ok=True)
    salida_path.parent.mkdir(parents=True, exist_ok=True)
    estado = json.loads(estado_path.read_text(encoding="utf-8")) if estado_path.exists() else {}

    chat_ids = [x.strip() for x in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]
    tg = Telegram(os.environ.get("TELEGRAM_BOT_TOKEN"), chat_ids, a.dry_run)
    if tg.dry and not a.dry_run:
        log("Sin TELEGRAM_BOT_TOKEN: los mensajes se muestran en pantalla y no se envían.")

    if a.modo == "prueba":
        tg.enviar("✅ <b>Bot de alertas conectado</b>\nEste canal va a recibir las alertas meteorológicas para CABA.\n"
                  f"GCBA · Jefatura de Gabinete · {ahora():%d/%m/%Y %H:%M}")
        return

    errores, eventos = [], []
    if cfg["fuentes"].get("open_meteo", True):
        try:
            datos = descargar_open_meteo(comunas)
            evals = [evaluar_comuna(d, U, G["horizonte_horas"], G["horizonte_dias"]) for d in datos]
            eventos += agregar_eventos(evals, comunas)
            log(f"Open-Meteo OK · {len(eventos)} alerta(s) por pronóstico")
        except Exception as ex:
            errores.append(f"Open-Meteo: {ex}")
            log("ERROR Open-Meteo:", ex)
    oficiales = []
    if cfg["fuentes"].get("smn_cap", True):
        try:
            oficiales = descargar_smn(cfg, estado)
            log(f"SMN CAP OK · {len(oficiales)} alerta(s) oficial(es) sobre CABA")
        except Exception as ex:
            errores.append(f"SMN: {ex}")
            log("ERROR SMN:", ex)
    eventos += oficiales

    if a.modo == "simulacro":
        t0 = ahora().replace(minute=0, second=0, microsecond=0) + timedelta(hours=3)
        fake = {"id": "sim", "fuente": "pronostico", "fenomeno": "tormenta", "nivel": 2,
                "desde": t0.isoformat(), "hasta": (t0 + timedelta(hours=6)).isoformat(),
                "comunas": [1, 4, 8, 9], "max": {"granizo": True}}
        tg.enviar(msg_evento(fake, cfg, "nueva", total, simulacro=True))
        return

    # --- comparar con el ciclo anterior y avisar solo lo nuevo ---
    previos = estado.get("eventos", {})
    nuevos_estado = {}
    enviados = []
    minimo = G.get("nivel_minimo_envio", 1)
    fuente_ok = {"pronostico": not any(x.startswith("Open-Meteo") for x in errores),
                 "smn": not any(x.startswith("SMN") for x in errores)}
    for ev in eventos:
        prev = previos.get(ev["id"])
        nivel_prev = prev["nivel"] if prev else 0
        cambio = None
        if ev["nivel"] >= minimo and nivel_prev < minimo:
            cambio = "nueva"
        elif ev["nivel"] > nivel_prev and ev["nivel"] >= minimo:
            cambio = "sube"
        elif ev["nivel"] < nivel_prev and G.get("avisar_bajas", True) and ev["nivel"] >= minimo:
            cambio = "baja"
        if cambio:
            texto = msg_evento(ev, cfg, cambio, total)
            tg.enviar(texto)
            enviados.append({"fecha": ahora().isoformat(), "texto": re.sub(r"<[^>]+>", "", texto)})
        nuevos_estado[ev["id"]] = {"nivel": ev["nivel"], "nombre": nombre_evento(ev), "ausente": 0,
                                   "notificado": (ahora().isoformat() if cambio else (prev or {}).get("notificado"))}
    for k, prev in previos.items():
        if k in nuevos_estado:
            continue
        fuente = "smn" if k.startswith("smn:") else "pronostico"
        if not fuente_ok[fuente]:            # si la fuente falló, no damos por terminada la alerta
            nuevos_estado[k] = prev
            continue
        ausente = prev.get("ausente", 0) + 1
        if ausente >= G.get("ciclos_para_cerrar", 2):
            if prev.get("nivel", 0) >= minimo:
                texto = msg_fin(prev)
                tg.enviar(texto)
                enviados.append({"fecha": ahora().isoformat(), "texto": re.sub(r"<[^>]+>", "", texto)})
        else:
            nuevos_estado[k] = dict(prev, ausente=ausente)
    estado["eventos"] = nuevos_estado
    if enviados:
        estado["ultimo_envio"] = enviados[-1]

    if cfg["telegram"].get("responder_comandos", True):
        responder_comandos(tg, estado, eventos, U, total)

    # --- archivo para el tablero ---
    salida = {
        "generado": ahora().isoformat(),
        "errores": errores,
        "umbrales": U,
        "oficiales": [x for x in eventos if x["fuente"] == "smn"],
        "pronostico": [x for x in eventos if x["fuente"] == "pronostico"],
        "telegram": {"url_canal": cfg["telegram"].get("url_canal"), "usuario_bot": cfg["telegram"].get("usuario_bot"),
                     "ultimo_envio": estado.get("ultimo_envio")},
    }
    salida_path.write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    estado_path.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"Listo · {len(enviados)} mensaje(s) · {len(errores)} error(es)")
    if errores and not eventos:
        sys.exit(1)


if __name__ == "__main__":
    main()
