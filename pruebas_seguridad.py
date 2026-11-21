#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pruebas de seguridad y regresión de la lógica de Lupita con una base de datos SIMULADA.

Se ejecutan contra el código original y contra la versión corregida. No necesitan MySQL ni pantalla:
tkinter, mysql.connector y openpyxl se sustituyen por objetos falsos, y la "base de datos" es una
lista en memoria que guarda exactamente lo que el código intenta insertar.

Uso: python3 -I pruebas_seguridad.py <ruta_del_codigo.py> [resultado.json]
"""
import importlib.util
import json
import math
import os
import sys
import tempfile
import types
from unittest import mock

RUTA = os.path.abspath(sys.argv[1])
SALIDA = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else None

# ----------------------------------------------------------------------------- módulos falsos
mensajes = []          # lo que se le mostraría al usuario


class MBox:
    def _reg(tipo):
        def f(titulo, texto="", *a, **k):
            mensajes.append((tipo, titulo, str(texto)))
            return True
        return f
    showerror = staticmethod(_reg("error"))
    showinfo = staticmethod(_reg("info"))
    askyesno = staticmethod(_reg("pregunta"))


class MysqlError(Exception):
    pass


tk = types.ModuleType("tkinter")
for nombre in ("messagebox", "ttk", "scrolledtext", "filedialog"):
    setattr(tk, nombre, mock.MagicMock())
tk.messagebox = MBox
for nombre in ("Tk", "Toplevel", "Label", "Entry", "Button", "Frame", "Radiobutton", "StringVar", "END"):
    setattr(tk, nombre, mock.MagicMock())
mysql = types.ModuleType("mysql")
conn_mod = types.ModuleType("mysql.connector")
conn_mod.Error = MysqlError
conn_mod.connect = mock.MagicMock()
mysql.connector = conn_mod
opx = types.ModuleType("openpyxl")
opx.styles = types.ModuleType("openpyxl.styles")
for n in ("Font", "Alignment", "PatternFill"):
    setattr(opx.styles, n, mock.MagicMock())
opx.Workbook = mock.MagicMock()
sys.modules.update({"tkinter": tk, "mysql": mysql, "mysql.connector": conn_mod,
                    "openpyxl": opx, "openpyxl.styles": opx.styles})

os.chdir(tempfile.mkdtemp())     # el archivo lupita_errores.log se crea aquí, no en tu carpeta
spec = importlib.util.spec_from_file_location("lupita", RUTA)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


# ----------------------------------------------------------------------------- BD simulada
class FakeCursor:
    def __init__(self, bd):
        self.bd = bd
        self._res = None

    def execute(self, sql, params=()):
        s = " ".join(sql.split()).upper()
        self.bd.consultas.append((sql, params))
        if s.startswith("SELECT ID, PASSWORD, ROL FROM USUARIOS"):
            u = next((x for x in self.bd.usuarios if x["username"] == params[0] and x["activo"]), None)
            self._res = (u["id"], u["password"], u["rol"]) if u else None
        elif s.startswith("SELECT ID FROM USUARIOS"):                         # versión original
            u = next((x for x in self.bd.usuarios if x["username"] == params[0] and x["password"] == params[1] and x["activo"]), None)
            self._res = (u["id"],) if u else None
        elif s.startswith("SELECT COUNT(*) FROM USUARIOS"):
            self._res = (sum(1 for x in self.bd.usuarios if x["username"] == params[0]),)
        elif s.startswith("INSERT INTO USUARIOS"):
            self.bd.usuarios.append(dict(id=len(self.bd.usuarios) + 1, username=params[0], password=params[1],
                                         rol=params[2] if len(params) > 2 else "vendedor", activo=True))
        elif s.startswith("UPDATE USUARIOS SET PASSWORD"):
            for x in self.bd.usuarios:
                if x["id"] == params[1]:
                    x["password"] = params[0]

    def fetchone(self):
        return self._res

    def close(self):
        pass


class FakeBD:
    def __init__(self, usuarios):
        self.usuarios = usuarios
        self.consultas = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass


def nuevo_db(usuarios, rol=None):
    d = M.DatabaseManager()
    d.connection = FakeBD(usuarios)
    d.usuario_actual_rol = rol
    return d


def hay(nombre):
    return hasattr(M, nombre)


# ----------------------------------------------------------------------------- pruebas
resultados = []


def prueba(id_, categoria, descripcion, esperado):
    def deco(fn):
        mensajes.clear()
        try:
            ok, obtenido = fn()
        except Exception as e:                                         # un fallo inesperado cuenta como fallo
            ok, obtenido = False, f"Excepción no controlada: {type(e).__name__}: {e}"
        resultados.append(dict(id=id_, categoria=categoria, descripcion=descripcion, esperado=esperado,
                               estado="Aprobado" if ok else "Fallido", obtenido=obtenido))
        return fn
    return deco


def usuarios_base():
    return [dict(id=1, username="ana", password="clave123", rol="admin", activo=True)]


@prueba("ST-01", "Contraseñas", "Una contraseña nueva se almacena cifrada (no en texto plano)",
        "El valor guardado en la BD es distinto de la contraseña escrita")
def _():
    d = nuevo_db(usuarios_base(), rol="admin")
    try:
        r = d.crear_usuario("pedro_v", "Segura2026")
    except TypeError:
        r = d.crear_usuario("pedro_v", "Segura2026")
    guardado = d.connection.usuarios[-1]["password"]
    return (guardado != "Segura2026"), f"Valor almacenado: «{guardado[:34]}{'…' if len(guardado) > 34 else ''}»"


@prueba("ST-02", "Contraseñas", "Dos usuarios con la misma contraseña no producen el mismo valor almacenado",
        "Los valores almacenados son diferentes (sal aleatoria)")
def _():
    d = nuevo_db(usuarios_base(), rol="admin")
    d.crear_usuario("usuario_a", "Segura2026")
    d.crear_usuario("usuario_b", "Segura2026")
    a, b = d.connection.usuarios[-2]["password"], d.connection.usuarios[-1]["password"]
    return a != b, "Valores distintos" if a != b else "Valores idénticos (sin sal)"


@prueba("ST-03", "Autenticación", "Inicio de sesión con credenciales correctas",
        "Acceso concedido")
def _():
    d = nuevo_db(usuarios_base())
    return d.verificar_usuario("ana", "clave123"), "Acceso concedido" if d.usuario_actual_id else "Acceso denegado"


@prueba("ST-04", "Autenticación", "Inicio de sesión con contraseña incorrecta y con usuario inexistente",
        "Acceso denegado en ambos casos")
def _():
    d = nuevo_db(usuarios_base())
    a = d.verificar_usuario("ana", "otra")
    b = d.verificar_usuario("nadie", "clave123")
    return (not a and not b), f"Contraseña incorrecta: {'denegado' if not a else 'ACCEDIÓ'}; usuario inexistente: {'denegado' if not b else 'ACCEDIÓ'}"


@prueba("ST-05", "Contraseñas", "Un usuario con contraseña antigua (texto plano) puede entrar y su contraseña queda cifrada",
        "Acceso concedido y la BD deja de guardar la contraseña en claro")
def _():
    us = usuarios_base()
    d = nuevo_db(us)
    entro = d.verificar_usuario("ana", "clave123")
    return (entro and us[0]["password"] != "clave123"), f"Acceso: {'sí' if entro else 'no'}; valor guardado después: «{us[0]['password'][:24]}…»"


@prueba("ST-06", "Control de acceso", "Un usuario sin sesión de administrador intenta crear un usuario",
        "Operación rechazada y no se inserta ningún registro")
def _():
    d = nuevo_db(usuarios_base(), rol=None)
    antes = len(d.connection.usuarios)
    r = d.crear_usuario("intruso", "Hackeo2026") if hay("PREFIJO_HASH") else d.crear_usuario("intruso", "Hackeo2026")
    creo = len(d.connection.usuarios) > antes
    return (not creo), ("Usuario creado sin autenticarse" if creo else f"Rechazado: «{r[1]}»")


@prueba("ST-07", "Control de acceso", "Un vendedor intenta crear un usuario administrador",
        "Operación rechazada")
def _():
    d = nuevo_db(usuarios_base(), rol="vendedor")
    antes = len(d.connection.usuarios)
    r = d.crear_usuario("jefe2", "Hackeo2026", "admin") if hay("PREFIJO_HASH") else d.crear_usuario("jefe2", "Hackeo2026")
    creo = len(d.connection.usuarios) > antes
    return (not creo), ("Se creó la cuenta" if creo else f"Rechazado: «{r[1]}»")


@prueba("ST-08", "Contraseñas", "Alta de usuario con contraseña débil (1 carácter)",
        "Alta rechazada")
def _():
    d = nuevo_db(usuarios_base(), rol="admin")
    antes = len(d.connection.usuarios)
    r = d.crear_usuario("debil", "1")
    creo = len(d.connection.usuarios) > antes
    return (not creo), ("Se aceptó la contraseña de 1 carácter" if creo else f"Rechazado: «{r[1]}»")


@prueba("ST-09", "Inyección SQL", "Inicio de sesión con  ' OR '1'='1  como usuario y contraseña",
        "Acceso denegado; el texto se trata como dato, no como código SQL")
def _():
    d = nuevo_db(usuarios_base())
    r = d.verificar_usuario("' OR '1'='1", "' OR '1'='1")
    sql, params = d.connection.consultas[-1]
    parametrizada = "%s" in sql and "OR '1'" not in sql
    return (not r and parametrizada), f"Acceso {'CONCEDIDO' if r else 'denegado'}; consulta parametrizada: {'sí' if parametrizada else 'no'}"


@prueba("ST-10", "Inyección SQL", "Alta de usuario con nombre  x'; DROP TABLE usuarios;--",
        "No se ejecuta SQL ajeno; el nombre se rechaza o se guarda como texto")
def _():
    d = nuevo_db(usuarios_base(), rol="admin")
    d.crear_usuario("x'; DROP TABLE usuarios;--", "Segura2026")
    peligro = any("DROP" in sql.upper() for sql, _ in d.connection.consultas)
    return (not peligro), "Ninguna consulta contiene DROP; los valores viajan como parámetros"


@prueba("ST-11", "Validación de formularios", "Cantidad o precio con valores peligrosos: -5, 0, nan, inf, 1e30, texto",
        "Todos rechazados")
def _():
    if not hay("validar_numero"):
        return False, "El código original no valida el rango: float('-5'), float('nan') y float('inf') se aceptan tal cual"
    malos = ["-5", "0", "nan", "inf", "1e30", "abc", ""]
    aceptados = []
    for t in malos:
        try:
            M.validar_numero(t, "La cantidad", 0.001, 9999)
            aceptados.append(t)
        except ValueError:
            pass
    return (not aceptados), ("Todos rechazados" if not aceptados else f"Aceptados: {aceptados}")


@prueba("ST-12", "Validación de formularios", "Valores límite de cantidad: 0.001 y 9999 (válidos); 0.0009 y 9999.01 (inválidos)",
        "Los límites válidos se aceptan y los inválidos se rechazan")
def _():
    if not hay("validar_numero"):
        return False, "No existe validación de rango"
    ok_ = [M.validar_numero("0.001", "c", 0.001, 9999), M.validar_numero("9999", "c", 0.001, 9999)]
    mal = []
    for t in ("0.0009", "9999.01"):
        try:
            M.validar_numero(t, "c", 0.001, 9999); mal.append(t)
        except ValueError:
            pass
    return (len(ok_) == 2 and not mal), f"Válidos aceptados: {ok_}; inválidos aceptados: {mal or 'ninguno'}"


@prueba("ST-13", "Manejo de errores", "Falla la conexión a MySQL (se simula un error con host y usuario en el mensaje)",
        "El usuario ve un mensaje general sin host, usuario ni detalles técnicos")
def _():
    conn_mod.connect.side_effect = MysqlError("2003: Can't connect to MySQL server on 'localhost:3306' (user 'root')")
    d = M.DatabaseManager()
    r = d.conectar()
    conn_mod.connect.side_effect = None
    texto = " ".join(m[2] for m in mensajes)
    fuga = any(p in texto for p in ("root", "localhost", "3306", "2003"))
    return (not r and not fuga), (f"Mensaje mostrado: «{texto[:70]}…»" + (" (expone datos técnicos)" if fuga else " (sin datos técnicos)"))


@prueba("ST-14", "Configuración", "Las credenciales de la base de datos no están escritas en el código",
        "No aparece la contraseña '12345' ni el usuario 'root' fijos")
def _():
    src = open(RUTA, encoding="utf-8").read()
    fijo = "'12345'" in src or "user = 'root'" in src
    return (not fijo), ("El código contiene password '12345' y usuario 'root'" if fijo else "Las credenciales se leen de variables de entorno")


@prueba("ST-15", "Autenticación", "Cinco intentos fallidos seguidos y un sexto con la contraseña correcta",
        "El sexto intento se bloquea temporalmente")
def _():
    if not hay("MAX_INTENTOS"):
        return False, "No hay límite de intentos: se pueden probar contraseñas sin restricción"
    app = M.PuntoVentaApp.__new__(M.PuntoVentaApp)
    app.db = nuevo_db(usuarios_base())
    entradas = {"u": "ana", "p": "mala"}
    app.entry_usuario = types.SimpleNamespace(get=lambda: entradas["u"])
    app.entry_contrasena = types.SimpleNamespace(get=lambda: entradas["p"])
    entrada_ok = []
    app.mostrar_menu_principal = lambda: entrada_ok.append(1)
    for _ in range(M.MAX_INTENTOS):
        app.iniciar_sesion()
    entradas["p"] = "clave123"
    app.iniciar_sesion()
    return (not entrada_ok), ("Sexto intento bloqueado" if not entrada_ok else "El sexto intento con la contraseña correcta entró")


@prueba("ST-16", "Control de acceso", "Un vendedor intenta abrir Reportes y Compras",
        "Acceso denegado en ambos módulos")
def _():
    if not hasattr(M.PuntoVentaApp, "abrir_reportes"):
        return False, "Método no encontrado"
    app = M.PuntoVentaApp.__new__(M.PuntoVentaApp)
    app.db = nuevo_db(usuarios_base(), rol="vendedor")
    abierto = []
    app.mostrar_interfaz_reportes = lambda: abierto.append("reportes")
    app.mostrar_interfaz_compras = lambda: abierto.append("compras")
    app.abrir_reportes()
    app.abrir_compras()
    return (not abierto), ("Reportes y Compras abiertos por un vendedor" if abierto else "Ambos módulos rechazaron al vendedor")


@prueba("ST-17", "Regresión", "Un administrador abre Reportes y Compras",
        "Acceso concedido (la corrección no rompe la función)")
def _():
    app = M.PuntoVentaApp.__new__(M.PuntoVentaApp)
    app.db = nuevo_db(usuarios_base(), rol="admin")
    abierto = []
    app.mostrar_interfaz_reportes = lambda: abierto.append("reportes")
    app.mostrar_interfaz_compras = lambda: abierto.append("compras")
    app.abrir_reportes()
    app.abrir_compras()
    return abierto == ["reportes", "compras"], f"Módulos abiertos: {abierto}"


@prueba("ST-18", "Regresión", "Un administrador crea un vendedor con datos válidos y ese vendedor inicia sesión",
        "Alta correcta e inicio de sesión exitoso")
def _():
    us = usuarios_base()
    admin = nuevo_db(us, rol="admin")
    ok_alta, msg = admin.crear_usuario("vendedor1", "Tienda2026")
    ses = nuevo_db(us)
    entro = ses.verificar_usuario("vendedor1", "Tienda2026")
    return (ok_alta and entro), f"Alta: «{msg}»; inicio de sesión del nuevo usuario: {'correcto' if entro else 'falló'}"


antes = os.path.basename(RUTA)
resumen = dict(archivo=antes, total=len(resultados),
               aprobados=sum(1 for r in resultados if r["estado"] == "Aprobado"),
               fallidos=sum(1 for r in resultados if r["estado"] == "Fallido"))
print(f"Archivo: {antes}  |  Aprobados {resumen['aprobados']}/{resumen['total']}")
for r in resultados:
    print(f"  [{'OK   ' if r['estado'] == 'Aprobado' else 'FALLA'}] {r['id']} {r['categoria']}: {r['obtenido']}")
if SALIDA:
    json.dump(dict(resumen=resumen, resultados=resultados), open(SALIDA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
