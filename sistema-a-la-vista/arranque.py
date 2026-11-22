"""Arranque de la demostración pública: el mismo sistema, dentro del navegador (Pyodide).

No hay servidor: la API de FastAPI se atiende en la pestaña de quien visita, con una base SQLite en memoria
y datos FICTICIOS. Lo que la persona haga no sale de su dispositivo y se borra al recargar.
"""
import asyncio, json, os

os.environ.update(SAV_ENTORNO="dev", SAV_DB="sqlite:////tmp/sav.db", SAV_DIR_EVIDENCIAS="/tmp/evidencias")
os.environ.pop("ANTHROPIC_API_KEY", None)

import anyio.to_thread


async def _directo(func, *args, **_):
    return func(*args)          # en el navegador no hay hilos: las funciones síncronas corren en el mismo


anyio.to_thread.run_sync = _directo

import httpx

try:
    import js                    # solo existe dentro del navegador

    class _TransporteNavegador(httpx.BaseTransport):
        """Las consultas a fuentes abiertas (SECOP II, Copernicus) salen por el navegador de quien visita."""
        def handle_request(self, request):
            x = js.XMLHttpRequest.new()
            x.open(request.method, str(request.url), False)
            for k, v in request.headers.items():
                if k.lower() in ("accept", "content-type"):
                    x.setRequestHeader(k, v)
            try:
                x.send(request.content.decode() if request.content else None)
            except Exception as e:
                raise httpx.ConnectError(str(e), request=request)
            if x.status == 0:
                raise httpx.ConnectError("el navegador no pudo consultar la fuente", request=request)
            return httpx.Response(int(x.status), content=str(x.responseText).encode(), headers={"content-type": x.getResponseHeader("content-type") or "application/json"}, request=request)

    _init = httpx.Client.__init__

    def _con_navegador(self, *a, **k):
        k.setdefault("transport", _TransporteNavegador())
        _init(self, *a, **k)

    httpx.Client.__init__ = _con_navegador
except ImportError:
    pass

from sav import canales, demo
from sav.api.main import crear_app
from sav.db import Base, crear_motor, fabrica

_motor = crear_motor()
app = None


def sembrar():
    """Deja la base como recién abierta."""
    global app
    Base.metadata.drop_all(_motor)
    demo.cargar_amplia(_motor)
    app = crear_app(_motor)
    app.state.emisor = canales.EmisorBandeja()


sembrar()


async def atender(metodo: str, ruta: str, consulta: str, cabeceras_json: str, cuerpo: str):
    """Entrega una petición HTTP a la aplicación (ASGI) y devuelve [estado, cabeceras en JSON, cuerpo]."""
    datos, entregado, fin = cuerpo.encode(), False, asyncio.Event()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": metodo.upper(), "path": ruta, "raw_path": ruta.encode(),
             "query_string": consulta.encode(), "root_path": "", "scheme": "https", "client": ("navegador", 0), "server": ("sav.local", 443),
             "headers": [(k.lower().encode(), v.encode()) for k, v in json.loads(cabeceras_json).items()] + [(b"content-length", str(len(datos)).encode())]}
    r = {"estado": 500, "cabeceras": {}, "cuerpo": b""}

    async def receive():
        nonlocal entregado
        if not entregado:
            entregado = True
            return {"type": "http.request", "body": datos, "more_body": False}
        await fin.wait()
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            r["estado"], r["cabeceras"] = m["status"], {k.decode(): v.decode() for k, v in m.get("headers", [])}
        elif m["type"] == "http.response.body":
            r["cuerpo"] += m.get("body", b"")
            if not m.get("more_body"):
                fin.set()

    try:
        await app(scope, receive, send)
    except Exception as e:      # nunca se deja a la interfaz esperando
        r = {"estado": 500, "cabeceras": {"content-type": "application/json"}, "cuerpo": json.dumps({"detalle": f"Error interno: {type(e).__name__}"}).encode()}
    return [r["estado"], json.dumps(r["cabeceras"]), r["cuerpo"].decode("utf-8", "replace")]


_n = 0


def whatsapp(texto: str) -> str:
    """Simulador del canal: entra un mensaje como llegaría de WhatsApp y devuelve lo que el sistema respondería."""
    global _n
    _n += 1
    with fabrica(_motor)() as s:
        r = canales.atender(s, "whatsapp", "573000000001", texto, f"simulador-{_n}")
    return r["respuesta"] if r else ""
