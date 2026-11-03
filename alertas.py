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
  python alertas.py --modo perspectiva manda ya el resumen de perspectiva (días 3 a 7)
  python alertas.py --modo seguimiento manda ya un parte de seguimiento (próximas horas por comuna)
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
def descargar_open_meteo(comunas: list[dict], dias: int = 7) -> list[dict]:
    params = {
        "latitude": ",".join(str(c["lat"]) for c in comunas),
        "longitude": ",".join(str(c["lon"]) for c in comunas),
        "hourly": "temperature_2m,precipitation,precipitation_probability,weather_code,"
                  "wind_speed_10m,wind_gusts_10m,cape",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,"
                 "wind_speed_10m_max,wind_gusts_10m_max,weather_code",
        "current": "temperature_2m,precipitation,wind_gusts_10m,weather_code",
        "past_hours": 24,
        "forecast_days": dias,
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

    # las rachas de calor se cuentan con todos los días disponibles y después se recorta al horizonte
    D_all = [j for j, s_ in enumerate(D["time"]) if datetime.fromisoformat(s_).date() >= hoy]
    niv_dia = rachas_calor([D["temperature_2m_max"][j] for j in D_all], [D["temperature_2m_min"][j] for j in D_all], U["calor"])[:len(idx)]
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
# 2b. Perspectiva extendida (días 3 a 7) · valores diarios, menor certeza
#     (la misma lógica está replicada en docs/index.html)
# ---------------------------------------------------------------------------
def rachas_calor(tx, tn, UC) -> list[int]:
    cal = [(a if a is not None else -99) >= UC["tmax"] and (b if b is not None else -99) >= UC["tmin"] for a, b in zip(tx, tn)]
    niv, j = [0] * len(cal), 0
    while j < len(cal):
        if not cal[j]:
            j += 1
            continue
        k = j
        while k < len(cal) and cal[k]:
            k += 1
        n = 1 if k - j >= UC["dias_consecutivos"]["amarillo"] else 0
        if k - j >= UC["dias_consecutivos"]["naranja"]:
            n = 3 if max(tx[j:k]) >= UC["rojo_tmax"] else 2
        for m in range(j, k):
            niv[m] = n
        j = k
    return niv


def calcular_perspectiva(datos: list[dict], comunas: list[dict], U: dict, P: dict) -> list[dict]:
    hoy = ahora().date()
    D0 = datos[0]["daily"]
    fechas = [datetime.fromisoformat(x).date() for x in D0["time"]]
    UL, UV, UT, UF = U["lluvia"], U["viento"], U["tormenta"], U.get("frio", {})
    u_lluvia = {"amarillo": UL["acumulado_12h_mm"].get("amarillo"), "naranja": UL["acumulado_12h_mm"].get("naranja"),
                "rojo": UL["acumulado_24h_mm"].get("rojo")}
    calor = []
    for d in datos:
        D = d["daily"]
        calor.append(rachas_calor(D["temperature_2m_max"], D["temperature_2m_min"], U["calor"]))
    dias = []
    for j, f in enumerate(fechas):
        n = (f - hoy).days + 1
        if n < P.get("dias_desde", 3) or n > P.get("dias_hasta", 7):
            continue
        senales = {}

        def marcar(fen, nivel, valor, c):
            if not nivel:
                return
            s_ = senales.setdefault(fen, {"fenomeno": fen, "nivel": 0, "comunas": [], "valor": None})
            s_["nivel"] = max(s_["nivel"], nivel)
            s_["comunas"].append(c["n"])
            if valor is not None:
                s_["valor"] = valor if s_["valor"] is None else (min(s_["valor"], valor) if fen == "frio" else max(s_["valor"], valor))

        res = {"tmax": None, "tmin": None, "lluvia": 0, "prob": 0, "rafagas": 0, "codigo": 0}
        for ci, (c, d) in enumerate(zip(comunas, datos)):
            D = d["daily"]
            tx, tn = D["temperature_2m_max"][j], D["temperature_2m_min"][j]
            ps, pp = D["precipitation_sum"][j] or 0, D["precipitation_probability_max"][j] or 0
            ws, wg, code = D["wind_speed_10m_max"][j] or 0, D["wind_gusts_10m_max"][j] or 0, D["weather_code"][j] or 0
            res["tmax"] = tx if res["tmax"] is None or (tx is not None and tx > res["tmax"]) else res["tmax"]
            res["tmin"] = tn if res["tmin"] is None or (tn is not None and tn < res["tmin"]) else res["tmin"]
            res["lluvia"], res["prob"] = max(res["lluvia"], ps), max(res["prob"], pp)
            res["rafagas"], res["codigo"] = max(res["rafagas"], wg), max(res["codigo"], code)
            marcar("lluvia", nivel_por_umbral(ps, u_lluvia), ps, c)
            marcar("viento", max(nivel_por_umbral(ws, UV["sostenido_kmh"]), nivel_por_umbral(wg, UV["rafagas_kmh"])), wg, c)
            if code >= 95 and pp >= P.get("prob_min_tormenta", 50):
                nt = 1
                if (UT["naranja"].get("granizo") and code in (96, 99)) or ps >= UT["naranja"]["lluvia_12h_mm"] or wg >= UT["naranja"]["rafagas_kmh"]:
                    nt = 2
                if ps >= UT["rojo"]["lluvia_12h_mm"] or wg >= UT["rojo"]["rafagas_kmh"]:
                    nt = 3
                marcar("tormenta", nt, pp, c)
            marcar("calor", calor[ci][j], tx, c)
            if UF.get("activo", True) and tn is not None:
                marcar("frio", 3 if tn <= UF["tmin"]["rojo"] else 2 if tn <= UF["tmin"]["naranja"] else 1 if tn <= UF["tmin"]["amarillo"] else 0, tn, c)
        dias.append({"fecha": f.isoformat(), "dia": n, "confianza": "media" if n <= 4 else "baja",
                     "resumen": res, "senales": sorted(senales.values(), key=lambda x: -x["nivel"])})
    return dias


DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def texto_senal(s_: dict) -> str:
    v, f = s_.get("valor"), s_["fenomeno"]
    return {"lluvia": f"lluvia de hasta {num(v)} mm en el día", "viento": f"ráfagas de hasta {num(v)} km/h",
            "tormenta": f"tormentas (probabilidad {num(v)} %)", "calor": f"máximas de hasta {num(v)} °C",
            "frio": f"mínimas de hasta {num(v)} °C"}.get(f, f)


def msg_perspectiva(dias: list[dict], total: int) -> str:
    lineas = ["🗓️ <b>Perspectiva · próximos días</b>",
              "<i>Pronóstico extendido de menor certeza. No es una alerta: sirve para anticipar.</i>", ""]
    hay = any(d["senales"] for d in dias)
    for d in dias:
        f = datetime.fromisoformat(d["fecha"])
        r = d["resumen"]
        cab = f"<b>{DIAS_SEMANA[f.weekday()]} {f:%d/%m}</b> · {num(r['tmin'])}° / {num(r['tmax'])}°"
        if r["prob"] >= 30:
            cab += f" · 💧 {num(r['prob'])} %"
        lineas.append(cab)
        if d["senales"]:
            for s_ in d["senales"]:
                lineas.append(f"   {ICONO[s_['nivel']]} {NOMBRE_FENOMENO.get(s_['fenomeno'], s_['fenomeno'])}: "
                              f"{texto_senal(s_)} · {lista_comunas(s_['comunas'], total)}")
            lineas.append(f"   <i>confianza {d['confianza']}</i>")
    if not hay:
        lineas += ["", "✅ Sin señales de eventos significativos en estos días."]
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# 2c. Seguimiento · próximas horas por comuna (para partes cada 30 minutos)
# ---------------------------------------------------------------------------
def resumen_proximas_horas(datos: list[dict], comunas: list[dict], horas: int) -> dict:
    filas = []
    hora_actual = ahora().replace(minute=0, second=0, microsecond=0)
    for c, d in zip(comunas, datos):
        H = d["hourly"]
        t = [parse_local(x) for x in H["time"]]
        i0 = next((i for i, x in enumerate(t) if x >= hora_actual), 0)
        rng = list(range(i0, min(i0 + horas, len(t))))
        hs = [{"t": t[i], "p": H["precipitation"][i] or 0, "g": H["wind_gusts_10m"][i] or 0,
               "pr": H["precipitation_probability"][i] or 0, "c": H["weather_code"][i] or 0,
               "temp": H["temperature_2m"][i]} for i in rng]
        kp = max(range(len(hs)), key=lambda k: hs[k]["p"])
        kg = max(range(len(hs)), key=lambda k: hs[k]["g"])
        tormenta = [h["t"] for h in hs if h["c"] >= 95]
        temps = [h["temp"] for h in hs if h["temp"] is not None]
        filas.append({
            "comuna": c["n"], "lluvia": sum(h["p"] for h in hs), "lluvia_max": hs[kp]["p"], "hora_lluvia_max": hs[kp]["t"],
            "rafaga": hs[kg]["g"], "hora_rafaga": hs[kg]["t"], "prob": max(h["pr"] for h in hs),
            "tormenta": tormenta, "granizo": any(h["c"] in (96, 99) for h in hs),
            "ahora_lluvia": hs[0]["p"], "ahora_rafaga": hs[0]["g"],
            "temp_ahora": temps[0] if temps else None, "temp_min": min(temps) if temps else None,
            "temp_max": max(temps) if temps else None, "horas": hs,
        })
    ciudad = []
    for k in range(min(len(f["horas"]) for f in filas)):
        hk = [f["horas"][k] for f in filas]
        ciudad.append({"t": hk[0]["t"], "p": max(h["p"] for h in hk), "g": max(h["g"] for h in hk),
                       "pr": max(h["pr"] for h in hk), "tormenta": any(h["c"] >= 95 for h in hk),
                       "comunas_lluvia": sum(1 for h in hk if h["p"] >= 0.5)})
    return {"filas": filas, "ciudad": ciudad}


def msg_seguimiento(res: dict, motivo: str, U: dict, total: int, prev: dict | None, cfg: dict, prueba=False) -> str:
    F, C = res["filas"], res["ciudad"]
    hh = lambda d: f"{d:%H} h"
    UI, UG = U["lluvia"].get("intensidad_horaria_mm", {}), U["viento"]["rafagas_kmh"]
    L = []
    if prueba:
        L.append("🧪 <b>PRUEBA DE SEGUIMIENTO</b>")
    L += [f"📡 <b>Seguimiento · {e(motivo)}</b>",
          f"<i>Parte de las {ahora():%H:%M} · próximas {len(C)} h · pronóstico por comuna (Open-Meteo)</i>", ""]

    # esta hora
    llueve = [f for f in F if f["ahora_lluvia"] >= 0.5]
    g_ahora = max(f["ahora_rafaga"] for f in F)
    if llueve:
        m = max(llueve, key=lambda f: f["ahora_lluvia"])
        L.append(f"🕒 <b>Esta hora:</b> lluvia en {len(llueve)} de {total} comunas "
                 f"(máx. {num(m['ahora_lluvia'], 1)} mm en Comuna {m['comuna']}) · ráfagas hasta {num(g_ahora)} km/h")
    else:
        L.append(f"🕒 <b>Esta hora:</b> sin lluvia significativa · ráfagas hasta {num(g_ahora)} km/h")

    # lluvia
    con = sorted([f for f in F if f["lluvia"] >= 2], key=lambda f: -f["lluvia"])
    if con:
        pico = max(C, key=lambda h: h["p"])
        L.append(f"🌧️ <b>Lluvia:</b> {len(con)} comuna(s) con lluvia; lo más intenso cerca de las {hh(pico['t'])}.")
        for f in con[:5]:
            n = nivel_por_umbral(f["lluvia_max"], UI)
            L.append(f"   {ICONO[n] if n else '•'} Comuna {f['comuna']}: {num(f['lluvia'])} mm "
                     f"(máx. {num(f['lluvia_max'], 1)} mm/h a las {hh(f['hora_lluvia_max'])})")
        if len(con) > 5:
            resto = con[5:]
            a_, b_ = num(min(f['lluvia'] for f in resto)), num(max(f['lluvia'] for f in resto))
            L.append(f"   • Otras {len(resto)} comunas: " + (f"alrededor de {a_} mm" if a_ == b_ else f"entre {a_} y {b_} mm"))
    else:
        L.append("🌧️ <b>Lluvia:</b> no se espera lluvia significativa.")

    # viento
    gmax = max(F, key=lambda f: f["rafaga"])
    fuertes = sorted([f["comuna"] for f in F if nivel_por_umbral(f["rafaga"], UG) >= 1])
    nv = nivel_por_umbral(gmax["rafaga"], UG)
    if fuertes:
        L.append(f"💨 <b>Viento:</b> {ICONO[nv]} ráfagas de hasta {num(gmax['rafaga'])} km/h cerca de las {hh(gmax['hora_rafaga'])} · "
                 f"{lista_comunas(fuertes, total)}")
    else:
        L.append(f"💨 <b>Viento:</b> ráfagas de hasta {num(gmax['rafaga'])} km/h, por debajo de los umbrales.")

    # tormenta
    st = [f for f in F if f["tormenta"]]
    if st:
        h_ini = min(min(f["tormenta"]) for f in st)
        h_fin = max(max(f["tormenta"]) for f in st)
        franja = hh(h_ini) if h_ini == h_fin else f"entre las {h_ini:%H} y las {(h_fin + timedelta(hours=1)):%H} h"
        gr = " · posible granizo" if any(f["granizo"] for f in st) else ""
        L.append(f"⛈️ <b>Tormentas:</b> probables {franja} · {lista_comunas([f['comuna'] for f in st], total)}{gr}")
    else:
        L.append("⛈️ <b>Tormentas:</b> el modelo no pronostica tormentas en este lapso.")

    # temperatura
    ta = [f["temp_ahora"] for f in F if f["temp_ahora"] is not None]
    tm = [f["temp_min"] for f in F if f["temp_min"] is not None]
    if ta and tm:
        L.append(f"🌡️ <b>Temperatura:</b> {num(sum(ta) / len(ta))}° ahora, mínima de {num(min(tm))}° en las próximas horas")

    # tendencia
    lluvia_top = con[0]["lluvia"] if con else 0
    if prev:
        cambios = []
        dl = lluvia_top - prev.get("lluvia_max", 0)
        if abs(dl) >= 3:
            cambios.append(("⬆️ más" if dl > 0 else "⬇️ menos") + f" lluvia prevista ({'+' if dl > 0 else ''}{num(dl)} mm en la comuna más afectada)")
        dg = gmax["rafaga"] - prev.get("rafaga_max", 0)
        if abs(dg) >= 10:
            cambios.append(("⬆️ ráfagas más fuertes" if dg > 0 else "⬇️ ráfagas más débiles") + f" ({'+' if dg > 0 else ''}{num(dg)} km/h)")
        L.append("📈 <b>Respecto del parte anterior:</b> " + ("; ".join(cambios) if cambios else "sin cambios importantes."))

    # hora por hora
    L += ["", "<b>Hora por hora</b> (máximo en la Ciudad):"]
    for h in C:
        ic = "⛈️" if h["tormenta"] else "🌧️" if h["p"] >= 0.5 else "☁️" if h["pr"] >= 30 else "🌤️"
        extra = f" · {h['comunas_lluvia']} com." if h["p"] >= 0.5 else ""
        L.append(f"<code>{h['t']:%H}h</code> {ic} {num(h['p'], 1)} mm · 💨 {num(h['g'])} km/h{extra}")
    url = cfg["general"].get("url_tablero")
    if url and "TU-USUARIO" not in url:
        L += ["", f'<a href="{e(url)}">Ver mapa y radar en el tablero</a>']
    return "\n".join(L)


def motivo_seguimiento(oficiales: list[dict], eventos: list[dict], S_: dict) -> str | None:
    """Devuelve el motivo del seguimiento si corresponde activarlo, o None."""
    niveles = set(S_.get("niveles_smn", [1, 2, 3]))
    ant = timedelta(hours=float(S_.get("inicio_anticipado_horas", 3)))
    t = ahora()
    vig = []
    for o in oficiales:
        if o["nivel"] not in niveles:
            continue
        try:
            d0 = datetime.fromisoformat(o["desde"]) if o.get("desde") else t
            d1 = datetime.fromisoformat(o["hasta"]) if o.get("hasta") else None
        except ValueError:
            d0, d1 = t, None
        if d0 - ant <= t and (d1 is None or t <= d1):
            vig.append((o["nivel"], f"{ALERTA[o['nivel']].capitalize()} del SMN por {o.get('titulo') or o['fenomeno']}"
                        + (f" (hasta {fmt_hora(d1)})" if d1 else "")))
    if S_.get("tambien_por_pronostico", False):
        for ev in eventos:
            if ev["fuente"] == "pronostico" and ev["fenomeno"] in ("lluvia", "viento", "tormenta") \
                    and ev["nivel"] >= S_.get("nivel_minimo_pronostico", 2) and datetime.fromisoformat(ev["desde"]) - ant <= t:
                vig.append((ev["nivel"], f"{ALERTA[ev['nivel']].capitalize()} por pronóstico · {NOMBRE_FENOMENO[ev['fenomeno']]}"))
    if not vig:
        return None
    return max(vig, key=lambda x: x[0])[1]


# ---------------------------------------------------------------------------
# 2d. Aviso de lluvia (sin alerta) · METAR observado + pronóstico por comuna
# ---------------------------------------------------------------------------
NOMBRE_ESTACION = {"SABE": "Aeroparque", "SADF": "San Fernando", "SADP": "El Palomar", "SAEZ": "Ezeiza", "SADM": "Morón"}


def texto_wx(wx: str) -> str:
    """Traduce el código de tiempo presente de un METAR (ej. '-SHRA', 'TSRA', 'VCSH')."""
    if not wx:
        return ""
    partes = []
    for tok in wx.split():
        inten = "débil" if tok.startswith("-") else "fuerte" if tok.startswith("+") else ""
        t = tok.lstrip("+-")
        cerca = t.startswith("VC")
        t = t[2:] if cerca else t
        base = ("tormenta con lluvia" if "TS" in t and "RA" in t else "tormenta" if "TS" in t else
                "granizo" if "GR" in t or "GS" in t else "chaparrones" if "SH" in t else
                "lluvia" if "RA" in t else "llovizna" if "DZ" in t else "niebla" if "FG" in t else
                "neblina" if "BR" in t else "bruma" if "HZ" in t else "")
        if base:
            partes.append(base + (f" {inten}" if inten and base not in ("niebla", "neblina", "bruma") else "") + (" en las cercanías" if cerca else ""))
    return ", ".join(partes)


def descargar_metar(estaciones: list[str]) -> list[dict]:
    """Observaciones aeronáuticas (METAR) de las estaciones cercanas, vía aviationweather.gov."""
    r = requests.get("https://aviationweather.gov/api/data/metar",
                     params={"ids": ",".join(estaciones), "format": "json", "hours": 2}, headers=UA, timeout=20)
    r.raise_for_status()
    datos = r.json() if r.text.strip() else []
    out = {}
    for m in datos:
        icao = m.get("icaoId") or (m.get("rawOb") or "").split(" ")[0]
        raw = m.get("rawOb") or ""
        t = m.get("obsTime")
        try:
            obs = datetime.fromtimestamp(int(t), tz=TZ) if t is not None else None
        except (TypeError, ValueError):
            obs = None
        wx = m.get("wxString")
        if wx is None:  # si el campo no viene, lo sacamos del texto crudo
            wx = " ".join(re.findall(r"(?<=\s)([-+]?(?:VC)?(?:TS|SH)?(?:RA|DZ|GR|GS|BR|FG|HZ)+)(?=\s)", f" {raw} "))
        if icao and (icao not in out or (obs and out[icao]["obs"] and obs > out[icao]["obs"])):
            out[icao] = {"estacion": icao, "nombre": NOMBRE_ESTACION.get(icao, icao), "obs": obs, "wx": wx or "",
                         "texto": texto_wx(wx or ""), "temp": m.get("temp"), "viento": m.get("wspd"),
                         "rafaga": m.get("wgst"), "raw": raw}
    for v in out.values():
        v["llueve"] = bool(re.search(r"(RA|DZ|TS|GR|GS)", re.sub(r"VC\w+", "", v["wx"])))
        v["vigente"] = bool(v["obs"] and ahora() - v["obs"] <= timedelta(minutes=90))
    return [out[k] for k in estaciones if k in out]


def intensidad_txt(mm: float) -> str:
    return "débil" if mm < 2.5 else "moderada" if mm < 7.6 else "fuerte"


def situacion_lluvia(datos: list[dict], comunas: list[dict], metar: list[dict], A: dict) -> dict:
    """Determina si llueve ahora y si se espera lluvia en la próxima hora."""
    hora_actual = ahora().replace(minute=0, second=0, microsecond=0)
    umbral = float(A.get("mm_hora_minimo", 0.5))
    ahora_c, prox_c, horas_fin, mas_mm = [], [], [], {}
    for c, d in zip(comunas, datos):
        H = d["hourly"]
        t = [parse_local(x) for x in H["time"]]
        i0 = next((i for i, x in enumerate(t) if x >= hora_actual), 0)
        cur = (d.get("current") or {}).get("precipitation") or 0     # últimos 15 min (modelo)
        p_hora = H["precipitation"][i0] or 0
        if p_hora >= umbral or cur * 4 >= umbral:
            ahora_c.append((c["n"], max(p_hora, cur * 4)))
        if i0 + 1 < len(t):
            p1, pr1 = H["precipitation"][i0 + 1] or 0, H["precipitation_probability"][i0 + 1] or 0
            if p1 >= umbral and pr1 >= A.get("prob_anticipado", 60):
                prox_c.append((c["n"], p1))
        # hasta cuándo sigue lloviendo y cuánto más cae (próximas 6 h)
        fin, suma = None, 0
        for k in range(i0, min(i0 + 6, len(t))):
            pk = H["precipitation"][k] or 0
            suma += pk
            if pk >= umbral:
                fin = t[k] + timedelta(hours=1)
        if fin:
            horas_fin.append(fin)
        mas_mm[c["n"]] = suma
    obs = [m for m in metar if m["vigente"] and m["llueve"]]
    sabe = next((m for m in metar if m["estacion"] == "SABE" and m["vigente"]), None)
    llueve = len(ahora_c) >= int(A.get("comunas_minimas", 2)) or bool(sabe and sabe["llueve"])
    return {"llueve": llueve, "comunas": sorted(ahora_c), "proxima_hora": sorted(prox_c), "observado": obs,
            "aeroparque": sabe, "hasta": max(horas_fin) if horas_fin else None, "mas_mm": mas_mm}


def msg_lluvia(sit: dict, tipo: str, total: int, cfg: dict, desde: str | None = None) -> str:
    """tipo: 'empieza' | 'sigue' | 'anticipado' | 'para'."""
    L = []
    if tipo == "para":
        L += ["🌤️ <b>AVISO · Dejó de llover en la Ciudad</b>"]
        if desde:
            L.append(f"Llovió desde las {datetime.fromisoformat(desde):%H:%M}.")
        L.append("<i>Información, no es una alerta.</i>")
        return "\n".join(L)
    titulo = {"empieza": "🌦️ <b>AVISO · Está lloviendo en la Ciudad</b>",
              "sigue": "🌧️ <b>AVISO · Sigue lloviendo en la Ciudad</b>",
              "anticipado": "🌂 <b>AVISO · Se espera lluvia en la próxima hora</b>"}[tipo]
    L += [titulo, "<i>Información, no es una alerta.</i>", ""]
    lista = sit["proxima_hora"] if tipo == "anticipado" else sit["comunas"]
    if lista:
        n_max, mm_max = max(lista, key=lambda x: x[1])
        L.append(f"📍 <b>Dónde:</b> {lista_comunas([n for n, _ in lista], total)}")
        L.append(f"💧 <b>Intensidad:</b> {intensidad_txt(mm_max)} (hasta {num(mm_max, 1)} mm/h en Comuna {n_max})")
    for m in sit["observado"][:3]:
        L.append(f"🛩️ <b>{m['nombre']}</b> ({m['obs']:%H:%M}, observado): {m['texto'] or 'lluvia'}")
    if tipo != "anticipado" and sit.get("hasta"):
        top = max(sit["mas_mm"].items(), key=lambda x: x[1])
        L.append(f"⏳ <b>Próximas horas:</b> lluvia hasta las {sit['hasta']:%H} h aprox. · "
                 f"hasta {num(top[1])} mm más (Comuna {top[0]})")
    url = cfg["general"].get("url_tablero")
    if url and "TU-USUARIO" not in url:
        L += ["", f'<a href="{e(url)}">Ver el radar en el tablero</a>']
    return "\n".join(L)


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


def msg_fin(prev: dict, clave: str = "") -> str:
    nombre = prev.get("nombre", "Alerta")
    if clave.startswith("smn:"):
        return f"✅ <b>FINALIZÓ · {e(nombre)}</b> · alerta oficial SMN\nEl SMN ya no tiene esta alerta vigente para la Ciudad."
    return f"✅ <b>FINALIZÓ · {e(nombre)}</b> · por pronóstico\nYa no se prevén condiciones de alerta para la Ciudad."


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


def responder_comandos(tg: Telegram, estado: dict, eventos: list[dict], U: dict, total: int, persp: list[dict] | None = None, parte=None):
    """Lee los mensajes nuevos que le escribieron al bot y responde comandos."""
    if tg.dry:
        return
    try:
        updates = tg.api("getUpdates", offset=estado.get("tg_offset", 0), timeout=0, allowed_updates=["message", "channel_post"])
    except Exception as ex:
        log("No se pudieron leer comandos:", ex)
        return
    for u in updates:
        estado["tg_offset"] = u["update_id"] + 1
        m = u.get("message") or u.get("channel_post") or {}
        texto = (m.get("text") or "").strip().lower()
        cid = m.get("chat", {}).get("id")
        if not cid or not texto.startswith("/"):
            continue
        cmd = texto.split()[0].split("@")[0]
        if cmd in ("/estado", "/alertas"):
            tg.enviar(msg_estado(eventos, total), [cid])
        elif cmd == "/umbrales":
            tg.enviar(msg_umbrales(U), [cid])
        elif cmd == "/ahora":
            tg.enviar(parte() if parte else "El pronóstico por comuna no está disponible en este momento.", [cid])
        elif cmd == "/perspectiva":
            tg.enviar(msg_perspectiva(persp, total) if persp else "La perspectiva extendida no está disponible en este momento.", [cid])
        elif cmd in ("/start", "/ayuda", "/help"):
            tg.enviar("👋 <b>Bot de alertas meteorológicas CABA</b>\nGCBA · Jefatura de Gabinete\n\n"
                      "/estado · alertas vigentes\n/ahora · próximas horas por comuna\n/perspectiva · próximos días (3 a 7)\n/umbrales · criterios que usa el sistema\n/ayuda · este mensaje\n\n"
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
    ap.add_argument("--modo", default="normal", choices=["normal", "prueba", "simulacro", "perspectiva", "seguimiento"])
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

    P = cfg.get("perspectiva", {}) or {}
    S_ = cfg.get("seguimiento", {}) or {}
    A_ = cfg.get("aviso_lluvia", {}) or {}
    errores, eventos, persp, datos = [], [], [], None
    if cfg["fuentes"].get("open_meteo", True):
        try:
            dias_pedidos = max(7, min(16, int(P.get("dias_hasta", 7)) if P.get("activa", True) else 7))
            datos = descargar_open_meteo(comunas, dias_pedidos)
            evals = [evaluar_comuna(d, U, G["horizonte_horas"], G["horizonte_dias"]) for d in datos]
            eventos += agregar_eventos(evals, comunas)
            log(f"Open-Meteo OK · {len(eventos)} alerta(s) por pronóstico")
            if P.get("activa", True):
                persp = calcular_perspectiva(datos, comunas, U, P)
                log(f"Perspectiva OK · {sum(1 for d in persp if d['senales'])} día(s) con señales")
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
    metar = []
    if A_.get("activo", True):
        try:
            metar = descargar_metar(A_.get("estaciones_metar", ["SABE"]))
            log("METAR OK · " + ", ".join(f"{m['estacion']} {m['wx'] or 'sin fenómenos'}" for m in metar))
        except Exception as ex:   # no es crítico: seguimos con el pronóstico
            log("METAR no disponible:", ex)

    if a.modo == "perspectiva":
        if persp:
            tg.enviar(msg_perspectiva(persp, total))
        else:
            log("No hay datos de perspectiva (¿falló Open-Meteo o está desactivada?)")
        return

    if a.modo == "seguimiento":
        if not datos:
            log("No hay datos de Open-Meteo para armar el parte.")
            return
        motivo = motivo_seguimiento(oficiales, eventos, S_) or "sin alerta vigente (parte de prueba)"
        res = resumen_proximas_horas(datos, comunas, int(S_.get("horas_adelante", 6)))
        tg.enviar(msg_seguimiento(res, motivo, U, total, None, cfg, prueba=True))
        return

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
                texto = msg_fin(prev, k)
                tg.enviar(texto)
                enviados.append({"fecha": ahora().isoformat(), "texto": re.sub(r"<[^>]+>", "", texto)})
        else:
            nuevos_estado[k] = dict(prev, ausente=ausente)
    estado["eventos"] = nuevos_estado
    if enviados:
        estado["ultimo_envio"] = enviados[-1]

    # --- seguimiento: partes cada N minutos mientras haya alerta del SMN vigente ---
    seg = estado.setdefault("seguimiento", {})
    motivo = motivo_seguimiento(oficiales, eventos, S_) if S_.get("activo", True) else None
    if motivo:
        ultimo = seg.get("ultimo")
        intervalo = max(15, int(S_.get("cada_minutos", 30)))
        toca = not ultimo or ahora() - datetime.fromisoformat(ultimo) >= timedelta(minutes=intervalo - 3)
        if toca and datos:
            res = resumen_proximas_horas(datos, comunas, int(S_.get("horas_adelante", 6)))
            texto = msg_seguimiento(res, motivo, U, total, seg.get("prev"), cfg)
            tg.enviar(texto)
            enviados.append({"fecha": ahora().isoformat(), "texto": re.sub(r"<[^>]+>", "", texto)})
            estado["ultimo_envio"] = enviados[-1]
            con = [f["lluvia"] for f in res["filas"]]
            seg.update(ultimo=ahora().isoformat(),
                       prev={"lluvia_max": max(con) if max(con) >= 2 else 0, "rafaga_max": max(f["rafaga"] for f in res["filas"])})
        seg.update(activo=True, motivo=motivo, ausente=0)
    elif seg.get("activo") and fuente_ok["smn"] and (fuente_ok["pronostico"] or not S_.get("tambien_por_pronostico")) \
            and seg.get("ausente", 0) + 1 < G.get("ciclos_para_cerrar", 2):
        seg["ausente"] = seg.get("ausente", 0) + 1     # igual que las alertas: esperamos antes de darlo por terminado
    elif seg.get("activo") and fuente_ok["smn"] and (fuente_ok["pronostico"] or not S_.get("tambien_por_pronostico")):
        texto = ("📡 <b>Seguimiento finalizado</b>\nYa no hay alertas vigentes para la Ciudad. "
                 "Se dejan de enviar los partes cada " + str(S_.get("cada_minutos", 30)) + " minutos.")
        tg.enviar(texto)
        enviados.append({"fecha": ahora().isoformat(), "texto": re.sub(r"<[^>]+>", "", texto)})
        estado["ultimo_envio"] = enviados[-1]
        estado["seguimiento"] = {"activo": False}

    # --- aviso de lluvia (información, aunque no haya alertas) ---
    LL = estado.setdefault("lluvia", {"estado": "seco"})
    sit = None
    if A_.get("activo", True) and datos:
        sit = situacion_lluvia(datos, comunas, metar, A_)
        silenciar = A_.get("silenciar_con_seguimiento", True) and estado.get("seguimiento", {}).get("activo")

        def mandar(texto):
            if silenciar:
                log("Aviso de lluvia omitido (hay seguimiento activo)")
                return
            tg.enviar(texto)
            enviados.append({"fecha": ahora().isoformat(), "texto": re.sub(r"<[^>]+>", "", texto)})
            estado["ultimo_envio"] = enviados[-1]

        if sit["llueve"]:
            if LL.get("estado") != "lloviendo":
                mandar(msg_lluvia(sit, "empieza", total, cfg))
                LL.update(estado="lloviendo", desde=ahora().isoformat(), ultimo=ahora().isoformat())
            else:
                cada = int(A_.get("actualizar_cada_minutos", 60))
                if cada and ahora() - datetime.fromisoformat(LL.get("ultimo", ahora().isoformat())) >= timedelta(minutes=cada - 3):
                    mandar(msg_lluvia(sit, "sigue", total, cfg))
                    LL["ultimo"] = ahora().isoformat()
            LL["secos"] = 0
        else:
            if LL.get("estado") == "lloviendo":
                LL["secos"] = LL.get("secos", 0) + 1
                if LL["secos"] >= G.get("ciclos_para_cerrar", 2):
                    if A_.get("avisar_cuando_para", True):
                        mandar(msg_lluvia(sit, "para", total, cfg, LL.get("desde")))
                    estado["lluvia"] = LL = {"estado": "seco", "seco_desde": ahora().isoformat()}
            elif sit["proxima_hora"] and A_.get("avisar_anticipado", True) and LL.get("estado") != "anticipado" \
                    and len(sit["proxima_hora"]) >= int(A_.get("comunas_minimas", 2)):
                mandar(msg_lluvia(sit, "anticipado", total, cfg))
                LL.update(estado="anticipado", anticipado=ahora().isoformat())
            elif LL.get("estado") == "anticipado" and not sit["proxima_hora"] and \
                    ahora() - datetime.fromisoformat(LL.get("anticipado")) >= timedelta(hours=2):
                estado["lluvia"] = LL = {"estado": "seco"}   # la lluvia esperada no llegó: se rearma el aviso

    # --- resumen diario de perspectiva (una vez por día, desde la hora configurada) ---
    hoy_txt = ahora().date().isoformat()
    if (persp and P.get("resumen_diario", True) and ahora().hour >= int(P.get("hora_resumen", 8))
            and estado.get("resumen_fecha") != hoy_txt):
        if any(d["senales"] for d in persp) or not P.get("solo_si_hay_senales", False):
            texto = msg_perspectiva(persp, total)
            tg.enviar(texto)
            enviados.append({"fecha": ahora().isoformat(), "texto": re.sub(r"<[^>]+>", "", texto)})
            estado["ultimo_envio"] = enviados[-1]
        estado["resumen_fecha"] = hoy_txt

    if cfg["telegram"].get("responder_comandos", True):
        parte = (lambda: msg_seguimiento(resumen_proximas_horas(datos, comunas, int(S_.get("horas_adelante", 6))),
                                         motivo or "consulta a pedido", U, total, None, cfg)) if datos else None
        responder_comandos(tg, estado, eventos, U, total, persp, parte)

    # --- archivo para el tablero ---
    salida = {
        "generado": ahora().isoformat(),
        "errores": errores,
        "umbrales": U,
        "oficiales": [x for x in eventos if x["fuente"] == "smn"],
        "pronostico": [x for x in eventos if x["fuente"] == "pronostico"],
        "perspectiva": persp,
        "perspectiva_config": P,
        "lluvia": {"estado": estado.get("lluvia", {}).get("estado", "seco"), "desde": estado.get("lluvia", {}).get("desde"),
                   "comunas": [n for n, _ in sit["comunas"]] if sit else []},
        "observado": [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in m.items()} for m in metar],
        "seguimiento": {"activo": bool(estado.get("seguimiento", {}).get("activo")),
                        "motivo": estado.get("seguimiento", {}).get("motivo"),
                        "ultimo": estado.get("seguimiento", {}).get("ultimo"),
                        "cada_minutos": S_.get("cada_minutos", 30), "horas": S_.get("horas_adelante", 6),
                        "configurado": bool(S_.get("activo", True))},
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
