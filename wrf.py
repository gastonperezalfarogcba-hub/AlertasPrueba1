#!/usr/bin/env python3
"""
WRF-SMN (4 km) · resumen por comuna
GCBA · Jefatura de Gabinete de Ministros

El SMN publica en AWS (gratis y sin clave) su modelo WRF de alta resolución: celdas de 4 km,
4 ciclos por día (00, 06, 12 y 18 UTC), pronóstico hasta 72 horas, lluvia y viento cada hora.
Documentación: https://odp-aws-smn.github.io/documentation_wrf_det/

Este script (lo corre GitHub Actions una vez por hora):
  1. Busca el ciclo más reciente que el SMN ya terminó de publicar.
  2. Si es nuevo, lee de cada archivo horario solo el recorte de CABA (no baja el mapa completo).
  3. Promedia las celdas cercanas al centro de cada comuna y guarda wrf/wrf.json.
  alertas.py usa ese archivo para la lluvia por comuna, las alertas y los partes de seguimiento.

Uso:
  python wrf.py              procesa el ciclo nuevo, si hay
  python wrf.py --forzar     vuelve a procesar el último ciclo aunque ya esté
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests
import yaml

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "alertas-clima-caba/1.0"}
CAJA_CABA = {"lat_min": -34.72, "lat_max": -34.52, "lon_min": -58.54, "lon_max": -58.33}


def log(*a):
    print(datetime.now(TZ).strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------------------
# Lectura remota parcial: los NetCDF4 son archivos HDF5, y h5py puede leer solo
# los bloques que necesita si le damos un "archivo" que pide rangos por HTTP.
# ---------------------------------------------------------------------------
class ArchivoRemoto(io.RawIOBase):
    BLOQUE = 512 * 1024

    def __init__(self, url: str, sesion: requests.Session):
        self.url, self.s, self.pos, self.cache = url, sesion, 0, {}
        r = self.s.head(url, headers=UA, timeout=30)
        r.raise_for_status()
        self.size = int(r.headers["Content-Length"])
        self.bytes_leidos = 0

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, off, whence=io.SEEK_SET):
        self.pos = off if whence == io.SEEK_SET else self.pos + off if whence == io.SEEK_CUR else self.size + off
        return self.pos

    def _bloque(self, n):
        if n not in self.cache:
            ini, fin = n * self.BLOQUE, min(self.size, (n + 1) * self.BLOQUE) - 1
            for intento in range(3):
                try:
                    r = self.s.get(self.url, headers={**UA, "Range": f"bytes={ini}-{fin}"}, timeout=60)
                    r.raise_for_status()
                    break
                except requests.RequestException:
                    if intento == 2:
                        raise
            self.cache[n] = r.content
            self.bytes_leidos += len(r.content)
        return self.cache[n]

    def readinto(self, b):
        if self.pos >= self.size:
            return 0
        n = min(len(b), self.size - self.pos)
        out, hecho = memoryview(b), 0
        while hecho < n:
            k, off = divmod(self.pos + hecho, self.BLOQUE)
            datos = self._bloque(k)[off: off + n - hecho]
            out[hecho: hecho + len(datos)] = datos
            hecho += len(datos)
        self.pos += n
        return n


class Archivo:
    """Abre un NetCDF remoto: primero con lectura parcial (h5py); si no se puede, lo descarga entero."""

    def __init__(self, url: str, sesion: requests.Session):
        self.url, self.bytes = url, 0
        try:
            import h5py
            self.raw = ArchivoRemoto(url, sesion)
            self.f = h5py.File(self.raw, "r")
            self.tipo = "h5"
        except Exception:
            import netCDF4
            r = sesion.get(url, headers=UA, timeout=180)
            r.raise_for_status()
            self.bytes = len(r.content)
            self.f = netCDF4.Dataset("wrf", memory=r.content)
            self.tipo = "nc"

    def leer(self, var: str, ventana=None) -> np.ndarray:
        v = self.f[var] if self.tipo == "h5" else self.f.variables[var]
        sl = (slice(ventana[0], ventana[1]), slice(ventana[2], ventana[3])) if ventana else (slice(None), slice(None))
        if len(v.shape) == 3:
            datos = v[(0, *sl)]
        elif len(v.shape) == 2:
            datos = v[sl]
        else:                       # coordenada 1D (lat o lon de grilla regular)
            datos = v[:]
        datos = np.ma.filled(np.ma.asarray(datos, dtype="float64"), np.nan)
        if self.tipo == "h5":
            fill = v.attrs.get("_FillValue")
            if fill is not None:
                datos[datos == float(np.ravel(fill)[0])] = np.nan
            esc, off = v.attrs.get("scale_factor"), v.attrs.get("add_offset")
            if esc is not None:
                datos = datos * float(np.ravel(esc)[0])
            if off is not None:
                datos = datos + float(np.ravel(off)[0])
        return datos

    @property
    def leidos(self):
        return self.raw.bytes_leidos if self.tipo == "h5" else self.bytes

    def cerrar(self):
        try:
            self.f.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
def url_archivo(base: str, ciclo: datetime, plazo: int) -> str:
    return f"{base}/{ciclo:%Y/%m/%d/%H}/WRFDETAR_01H_{ciclo:%Y%m%d}_{ciclo:%H}_{plazo:03d}.nc"


def existe(url: str, s: requests.Session) -> bool:
    try:
        return s.head(url, headers=UA, timeout=20).status_code == 200
    except requests.RequestException:
        return False


def ultimo_ciclo(base: str, plazo_final: int, s: requests.Session) -> datetime | None:
    """El ciclo más reciente cuyo último archivo ya está publicado (busca hasta 2 días atrás)."""
    t = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    c = t.replace(hour=t.hour - t.hour % 6)
    for _ in range(8):
        if existe(url_archivo(base, c, plazo_final), s):
            return c
        c -= timedelta(hours=6)
    return None


def distancia_km(lat1, lon1, lat2, lon2):
    r = math.pi / 180
    x = (lon2 - lon1) * r * np.cos((lat1 + lat2) / 2 * r)
    y = (lat2 - lat1) * r
    return 6371 * np.sqrt(x * x + y * y)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--salida", default=str(ROOT / "wrf" / "wrf.json"))
    ap.add_argument("--forzar", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    W = cfg.get("wrf", {}) or {}
    if not W.get("activo", True):
        log("WRF desactivado en config.yaml")
        return
    base = W.get("url_base", "https://smn-ar-wrf.s3.us-west-2.amazonaws.com/DATA/WRF/DET").rstrip("/")
    plazo_final = int(W.get("plazo_horas", 72))
    radio = float(W.get("radio_km", 3))
    comunas = cfg["comunas"]
    salida = Path(a.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    previo = json.loads(salida.read_text(encoding="utf-8")) if salida.exists() else {}

    s = requests.Session()
    ciclo = ultimo_ciclo(base, plazo_final, s)
    if not ciclo:
        log("No se encontró ningún ciclo completo del WRF-SMN en las últimas 48 horas.")
        sys.exit(1)
    ciclo_txt = ciclo.strftime("%Y-%m-%dT%H:%MZ")
    if previo.get("ciclo") == ciclo_txt and not a.forzar:
        log(f"Sin ciclo nuevo (último procesado: {ciclo_txt}).")
        return
    log(f"Procesando ciclo {ciclo_txt}")

    # --- recorte de CABA y celdas de cada comuna (con las coordenadas del primer archivo) ---
    f0 = Archivo(url_archivo(base, ciclo, 1), s)
    lat, lon = f0.leer("lat"), f0.leer("lon")
    if lat.ndim == 1:
        lon, lat = np.meshgrid(lon, lat)
    caja = W.get("caja") or CAJA_CABA
    dentro = (lat >= caja["lat_min"] - .05) & (lat <= caja["lat_max"] + .05) & (lon >= caja["lon_min"] - .05) & (lon <= caja["lon_max"] + .05)
    filas, cols = np.where(dentro)
    if not len(filas):
        raise RuntimeError("La grilla del WRF no cubre CABA (¿cambió el formato?)")
    ven = (int(filas.min()), int(filas.max()) + 1, int(cols.min()), int(cols.max()) + 1)
    la, lo = lat[ven[0]:ven[1], ven[2]:ven[3]], lon[ven[0]:ven[1], ven[2]:ven[3]]
    en_caba = (la >= caja["lat_min"]) & (la <= caja["lat_max"]) & (lo >= caja["lon_min"]) & (lo <= caja["lon_max"])
    celdas = {}
    for c in comunas:
        d = distancia_km(c["lat"], c["lon"], la, lo)
        m = d <= radio
        if not m.any():
            m = d == d.min()
        celdas[c["n"]] = m
    log(f"Recorte {ven[1]-ven[0]} x {ven[3]-ven[2]} celdas · {int(en_caba.sum())} sobre CABA · "
        f"celdas por comuna: {', '.join(f'{k}:{int(v.sum())}' for k, v in celdas.items())}")
    f0.cerrar()

    # --- lectura de cada plazo (en paralelo) ---
    def leer_plazo(p):
        f = None
        try:
            f = Archivo(url_archivo(base, ciclo, p), requests.Session())
            pp = f.leer("PP", ven)
            vi = f.leer("magViento10", ven) * 3.6          # m/s → km/h
            return p, pp, vi, f.leidos, None
        except Exception as ex:
            return p, None, None, 0, str(ex)
        finally:
            if f:
                f.cerrar()

    with ThreadPoolExecutor(max_workers=int(W.get("descargas_simultaneas", 8))) as ex:
        resultados = sorted(ex.map(leer_plazo, range(1, plazo_final + 1)), key=lambda r: r[0])

    fallas = [(p, err) for p, pp, _, _, err in resultados if err]
    if len(fallas) > plazo_final * 0.1:
        raise RuntimeError(f"Fallaron {len(fallas)} de {plazo_final} archivos. Ej.: plazo {fallas[0][0]}: {fallas[0][1]}")
    for p, err in fallas[:3]:
        log(f"Plazo {p} no disponible: {err}")

    r1 = lambda x: None if x is None or not np.isfinite(x) else round(float(x), 1)
    tiempos, por_comuna, ciudad_max = [], {c["n"]: {"pp": [], "viento": []} for c in comunas}, []
    total_mb = 0
    for p, pp, vi, leidos, err in resultados:
        tiempos.append((ciclo + timedelta(hours=p)).astimezone(TZ).strftime("%Y-%m-%dT%H:%M"))
        total_mb += leidos / 1e6
        for n, m in celdas.items():
            por_comuna[n]["pp"].append(None if pp is None else r1(np.nanmean(pp[m])))
            por_comuna[n]["viento"].append(None if vi is None else r1(np.nanmean(vi[m])))
        ciudad_max.append(None if pp is None else r1(np.nanmax(pp[en_caba])))

    out = {
        "fuente": "WRF-SMN 4 km (Servicio Meteorológico Nacional, CC BY 2.5 AR)",
        "ciclo": ciclo_txt, "procesado": datetime.now(TZ).isoformat(),
        "tiempos": tiempos, "comunas": {str(k): v for k, v in por_comuna.items()},
        "ciudad": {"pp_max_celda": ciudad_max},
        "celdas_por_comuna": {str(k): int(v.sum()) for k, v in celdas.items()},
        "plazos_faltantes": [p for p, _ in fallas],
    }
    salida.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"Listo · {len(tiempos)} horas · {total_mb:.0f} MB descargados · {salida}")


if __name__ == "__main__":
    main()
