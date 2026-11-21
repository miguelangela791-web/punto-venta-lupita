import tkinter as tk
from tkinter import messagebox, ttk, scrolledtext, filedialog
from datetime import datetime
import threading
import time
import mysql.connector
from mysql.connector import Error
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
import os
import re
import math
import hmac
import hashlib
import secrets
import logging
import time as _time
import tempfile

# Importaciones para impresora
try:
    import win32print
    import win32ui
    from PIL import Image, ImageDraw, ImageFont
    PRINTER_AVAILABLE = True
except ImportError:
    PRINTER_AVAILABLE = False
    print("⚠️ Para impresión, instala: py -m pip install pywin32 pillow")

# ============================= SEGURIDAD =============================
# Los detalles técnicos de los errores se guardan en un archivo de registro y NO se muestran al usuario.
logging.basicConfig(filename="lupita_errores.log", level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
logger = logging.getLogger("lupita")

PREFIJO_HASH = "pbkdf2_sha256"
ITERACIONES = 600_000
ROLES_VALIDOS = ("admin", "vendedor")
MAX_INTENTOS = 5
SEGUNDOS_BLOQUEO = 60


def hashear_password(password):
    """PBKDF2-HMAC-SHA256 con sal aleatoria. Formato: pbkdf2_sha256$iteraciones$sal$hash"""
    sal = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(sal), ITERACIONES)
    return f"{PREFIJO_HASH}${ITERACIONES}${sal}${h.hex()}"


def verificar_password(password, guardado):
    try:
        _, iteraciones, sal, h = guardado.split("$")
        calculado = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(sal), int(iteraciones))
        return hmac.compare_digest(calculado.hex(), h)
    except (ValueError, TypeError):
        return False


def validar_numero(texto, nombre, minimo, maximo):
    """Convierte texto a número y rechaza vacíos, NaN, infinitos, negativos, cero y valores fuera de rango."""
    try:
        valor = float(str(texto).strip())
    except ValueError:
        raise ValueError(f"{nombre} debe ser un número válido")
    if math.isnan(valor) or math.isinf(valor):
        raise ValueError(f"{nombre} debe ser un número válido")
    if valor < minimo or valor > maximo:
        raise ValueError(f"{nombre} debe estar entre {minimo:g} y {maximo:g}")
    return valor


def validar_credenciales_nuevas(username, password):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username or ""):
        return "El usuario debe tener de 3 a 30 caracteres (letras, números, punto, guion o guion bajo)"
    if len(password or "") < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "La contraseña debe tener al menos 8 caracteres, con letras y números"
    return None


class DatabaseManager:
    def __init__(self):
        # Las credenciales se leen de variables de entorno (no van escritas en el código).
        self.host = os.environ.get("LUPITA_DB_HOST", "localhost")
        self.port = int(os.environ.get("LUPITA_DB_PORT", "3306"))
        self.user = os.environ.get("LUPITA_DB_USER", "lupita_app")
        self.password = os.environ.get("LUPITA_DB_PASSWORD", "")
        self.database = os.environ.get("LUPITA_DB_NAME", "punto_venta_final")
        self.connection = None
        self.usuario_actual_id = None
        self.usuario_actual_rol = None
        
    def conectar(self):
        """Establece conexión con la base de datos"""
        try:
            self.connection = mysql.connector.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database
            )
            if self.connection.is_connected():
                print("✅ Conexión exitosa a MySQL")
                return True
        except Error as e:
            logger.error("Error al conectar a MySQL: %s", e)
            messagebox.showerror("Error de Conexión",
                                 "No se pudo conectar a la base de datos.\n"
                                 "Verifica que MySQL esté activo y que las variables LUPITA_DB_* estén configuradas.")
            return False
    
    def desconectar(self):
        """Cierra la conexión con la base de datos"""
        if self.connection and self.connection.is_connected():
            self.connection.close()
            print("🔌 Conexión cerrada")
    
    def verificar_usuario(self, username, password):
        """Verifica usuario y contraseña (hash PBKDF2). Si la contraseña estaba en texto plano, la migra a hash."""
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT id, password, rol FROM usuarios WHERE username = %s AND activo = TRUE", (username,))
            fila = cursor.fetchone()
            if not fila:
                verificar_password(password, "pbkdf2_sha256$1$00$00")   # tiempo similar si el usuario no existe
                cursor.close()
                return False

            user_id, guardado, rol = fila
            guardado = guardado or ""
            if guardado.startswith(PREFIJO_HASH + "$"):
                valido = verificar_password(password, guardado)
            else:                                                  # contraseña antigua en texto plano
                valido = hmac.compare_digest(guardado.encode("utf-8"), password.encode("utf-8"))
                if valido:
                    cursor.execute("UPDATE usuarios SET password = %s WHERE id = %s",
                                   (hashear_password(password), user_id))
                    self.connection.commit()
            cursor.close()

            if valido:
                self.usuario_actual_id = user_id
                self.usuario_actual_rol = rol if rol in ROLES_VALIDOS else "vendedor"
                return True
            return False
        except Error as e:
            logger.error("Error al verificar usuario: %s", e)
            return False

    def crear_usuario(self, username, password, rol="vendedor"):
        """Crea un usuario con la contraseña almacenada como hash. Solo puede invocarla un administrador."""
        if self.usuario_actual_rol != "admin":
            return False, "No tienes permiso para crear usuarios"
        if rol not in ROLES_VALIDOS:
            return False, "Rol no válido"
        problema = validar_credenciales_nuevas(username, password)
        if problema:
            return False, problema
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM usuarios WHERE username = %s", (username,))
            if cursor.fetchone()[0] > 0:
                cursor.close()
                return False, "El usuario ya existe"
            cursor.execute("INSERT INTO usuarios (username, password, rol) VALUES (%s, %s, %s)",
                           (username, hashear_password(password), rol))
            self.connection.commit()
            cursor.close()
            return True, "Usuario creado exitosamente"
        except Error as e:
            logger.error("Error al crear usuario: %s", e)
            return False, "No se pudo crear el usuario"

    def obtener_producto_por_codigo(self, codigo_barras):
        """Busca un producto por su código de barras - CON BÚSQUEDA PARCIAL"""
        try:
            cursor = self.connection.cursor()
            
            print(f"🔍 Buscando código: '{codigo_barras}' (longitud: {len(codigo_barras)})")
            
            # Búsqueda exacta primero
            query = """SELECT codigo_barras, nombre_producto, precio_sugerido, categoria, stock_actual 
                       FROM productos_catalogo 
                       WHERE codigo_barras = %s AND activo = 1"""
            cursor.execute(query, (codigo_barras,))
            row = cursor.fetchone()
            
            if row:
                print(f"✅ Encontrado con búsqueda exacta: {row[0]}")
            
            # Búsqueda parcial si no encuentra
            if not row and len(codigo_barras) >= 10:
                print(f"⚠️ No encontrado exacto. Buscando códigos que terminen con '{codigo_barras}'...")
                query = """SELECT codigo_barras, nombre_producto, precio_sugerido, categoria, stock_actual 
                           FROM productos_catalogo 
                           WHERE codigo_barras LIKE %s AND activo = 1
                           LIMIT 1"""
                cursor.execute(query, ('%' + codigo_barras,))
                row = cursor.fetchone()
                if row:
                    print(f"✅ Encontrado con búsqueda parcial: {row[0]}")
            
            cursor.close()
            
            if row:
                return {
                    'codigo_barras': row[0],
                    'nombre_producto': row[1],
                    'precio': float(row[2]),
                    'categoria': row[3] if row[3] else 'Sin categoría',
                    'stock': int(row[4]) if row[4] else 0
                }
            
            print(f"❌ Producto no encontrado")
            return None
            
        except Error as e:
            print(f"Error al buscar producto: {e}")
            return None
    
    def registrar_producto_nuevo(self, codigo_barras, nombre, precio, stock_inicial=0):
        """Registra un producto nuevo en el catálogo automáticamente"""
        try:
            cursor = self.connection.cursor()
            query = """INSERT INTO productos_catalogo 
                      (codigo_barras, nombre_producto, precio_sugerido, categoria, stock_actual, activo) 
                      VALUES (%s, %s, %s, %s, %s, 1)"""
            cursor.execute(query, (codigo_barras, nombre, precio, 'Sin categoría', stock_inicial))
            self.connection.commit()
            cursor.close()
            print(f"✅ Producto nuevo registrado: {nombre}")
            return True
        except Error as e:
            print(f"Error al registrar producto: {e}")
            return False
    
    def actualizar_stock(self, codigo_barras, cantidad_vendida):
        """Actualiza el stock después de una venta"""
        try:
            cursor = self.connection.cursor()
            query = """UPDATE productos_catalogo 
                      SET stock_actual = stock_actual - %s 
                      WHERE codigo_barras = %s"""
            cursor.execute(query, (cantidad_vendida, codigo_barras))
            self.connection.commit()
            cursor.close()
            print(f"✅ Stock actualizado para {codigo_barras}: -{cantidad_vendida}")
            return True
        except Error as e:
            print(f"Error al actualizar stock: {e}")
            return False
    
    def devolver_stock(self, codigo_barras, cantidad):
        """Devuelve stock al inventario (devoluciones)"""
        try:
            cursor = self.connection.cursor()
            query = """UPDATE productos_catalogo 
                      SET stock_actual = stock_actual + %s 
                      WHERE codigo_barras = %s"""
            cursor.execute(query, (cantidad, codigo_barras))
            self.connection.commit()
            cursor.close()
            print(f"✅ Stock devuelto para {codigo_barras}: +{cantidad}")
            return True
        except Error as e:
            print(f"Error al devolver stock: {e}")
            return False
    
    def registrar_venta(self, codigo_barras, producto, cantidad, precio, subtotal):
        """Registra una venta en la base de datos Y actualiza inventario"""
        try:
            cursor = self.connection.cursor()
            
            # Registrar venta
            query = """INSERT INTO ventas_registro 
                      (codigo_barras, producto, cantidad, precio, subtotal, usuario_id) 
                      VALUES (%s, %s, %s, %s, %s, %s)"""
            cursor.execute(query, (codigo_barras, producto, cantidad, precio, subtotal, self.usuario_actual_id))
            venta_id = cursor.lastrowid
            
            self.connection.commit()
            cursor.close()
            
            # Actualizar stock
            self.actualizar_stock(codigo_barras, cantidad)
            
            return True, venta_id
        except Error as e:
            print(f"Error al registrar venta: {e}")
            return False, None
    
    def registrar_devolucion(self, venta_id, codigo_barras, producto, cantidad, precio, subtotal, motivo):
        """Registra una devolución Y devuelve stock al inventario"""
        try:
            cursor = self.connection.cursor()
            
            # Registrar devolución
            query = """INSERT INTO devoluciones 
                      (venta_id, producto, cantidad, precio, subtotal, motivo, usuario_id) 
                      VALUES (%s, %s, %s, %s, %s, %s, %s)"""
            cursor.execute(query, (venta_id, producto, cantidad, precio, subtotal, motivo, self.usuario_actual_id))
            
            self.connection.commit()
            cursor.close()
            
            # Devolver stock al inventario
            self.devolver_stock(codigo_barras, cantidad)
            
            return True
        except Error as e:
            print(f"Error al registrar devolución: {e}")
            return False
    
    def obtener_ventas(self, fecha_desde=None, fecha_hasta=None):
        """Obtiene las ventas de la base de datos"""
        try:
            cursor = self.connection.cursor()
            if fecha_desde and fecha_hasta:
                query = """SELECT codigo_barras, producto, cantidad, precio, subtotal, fecha_hora 
                          FROM ventas_registro 
                          WHERE DATE(fecha_hora) BETWEEN %s AND %s 
                          ORDER BY fecha_hora DESC"""
                cursor.execute(query, (fecha_desde, fecha_hasta))
            else:
                query = """SELECT codigo_barras, producto, cantidad, precio, subtotal, fecha_hora 
                          FROM ventas_registro 
                          ORDER BY fecha_hora DESC"""
                cursor.execute(query)
            
            ventas = []
            for row in cursor.fetchall():
                ventas.append({
                    'codigo_barras': row[0] if row[0] else 'N/A',
                    'producto': row[1],
                    'cantidad': row[2],
                    'precio': float(row[3]),
                    'subtotal': float(row[4]),
                    'fecha_hora': row[5].strftime("%Y-%m-%d %H:%M:%S")
                })
            cursor.close()
            return ventas
        except Error as e:
            print(f"Error al obtener ventas: {e}")
            return []
    
    def obtener_compras(self, fecha_desde=None, fecha_hasta=None):
        """Obtiene las compras de la base de datos"""
        try:
            cursor = self.connection.cursor()
            if fecha_desde and fecha_hasta:
                query = """SELECT articulo, cantidad, precio, total, proveedor, fecha_hora 
                          FROM compras_registro 
                          WHERE DATE(fecha_hora) BETWEEN %s AND %s 
                          ORDER BY fecha_hora DESC"""
                cursor.execute(query, (fecha_desde, fecha_hasta))
            else:
                query = """SELECT articulo, cantidad, precio, total, proveedor, fecha_hora 
                          FROM compras_registro 
                          ORDER BY fecha_hora DESC"""
                cursor.execute(query)
            
            compras = []
            for row in cursor.fetchall():
                compras.append({
                    'articulo': row[0],
                    'cantidad': row[1],
                    'precio': float(row[2]),
                    'total': float(row[3]),
                    'proveedor': row[4] or 'Sin proveedor',
                    'fecha_hora': row[5].strftime("%Y-%m-%d %H:%M:%S")
                })
            cursor.close()
            return compras
        except Error as e:
            print(f"Error al obtener compras: {e}")
            return []
    
    def registrar_compra(self, articulo, cantidad, precio, total, proveedor):
        """Registra una compra en la base de datos"""
        try:
            cursor = self.connection.cursor()
            query = """INSERT INTO compras_registro (articulo, cantidad, precio, total, proveedor, usuario_id) 
                      VALUES (%s, %s, %s, %s, %s, %s)"""
            cursor.execute(query, (articulo, cantidad, precio, total, proveedor, self.usuario_actual_id))
            self.connection.commit()
            cursor.close()
            return True
        except Error as e:
            print(f"Error al registrar compra: {e}")
            return False


class ThermalPrinterManager:
    """Gestor para impresora térmica POS58 usando comandos ESC/POS directos"""
    
    def __init__(self):
        self.printer_name = None
        self.detectar_impresora()
    
    def detectar_impresora(self):
        """Detecta automáticamente la impresora POS58"""
        if not PRINTER_AVAILABLE:
            print("⚠️ win32print no disponible")
            return False
            
        try:
            # Buscar impresoras disponibles
            printers = [printer[2] for printer in win32print.EnumPrinters(2)]
            
            print(f"🖨️ Impresoras detectadas: {printers}")
            
            # Buscar POS58 o impresora térmica
            for printer in printers:
                if any(keyword in printer.upper() for keyword in ['POS58', 'POS-58', 'THERMAL', 'RECEIPT', 'XPRINTER', '58MM', 'POS']):
                    self.printer_name = printer
                    print(f"✅ Impresora térmica detectada: {printer}")
                    return True
            
            # Si no encuentra, usar impresora predeterminada
            self.printer_name = win32print.GetDefaultPrinter()
            print(f"⚠️ Usando impresora predeterminada: {self.printer_name}")
            return True
            
        except Exception as e:
            print(f"⚠️ Error al detectar impresora: {e}")
            return False
    
    def imprimir_ticket(self, lineas_ticket, total, paga_con, cambio):
        """Imprime ticket usando comandos ESC/POS RAW - TÍTULO EN DOS LÍNEAS"""
        if not self.printer_name:
            messagebox.showwarning("Impresora no disponible", 
                                 "No se detectó ninguna impresora.")
            return False
        
        if not PRINTER_AVAILABLE:
            messagebox.showwarning("Librerías no disponibles",
                                 "Instala: py -m pip install pywin32 pillow")
            return False
        
        try:
            # Comandos ESC/POS
            ESC = b'\x1B'
            GS = b'\x1D'
            
            # Inicializar
            INIT = ESC + b'@'
            
            # Alineación
            ALIGN_CENTER = ESC + b'a' + b'\x01'
            ALIGN_LEFT = ESC + b'a' + b'\x00'
            ALIGN_RIGHT = ESC + b'a' + b'\x02'
            
            # Tamaño de texto
            TEXT_NORMAL = ESC + b'!' + b'\x00'
            TEXT_BOLD = ESC + b'!' + b'\x08'
            TEXT_DOUBLE = ESC + b'!' + b'\x30'
            TEXT_MEDIUM = ESC + b'!' + b'\x10'
            
            # Cortar papel
            CUT = GS + b'V' + b'\x41' + b'\x03'
            
            # Construir ticket
            ticket_data = INIT
            
            # ⭐ TÍTULO EN DOS LÍNEAS - CENTRADO
            ticket_data += ALIGN_CENTER + TEXT_MEDIUM
            ticket_data += "  MISCELANEA\n".encode('cp437', errors='replace')
            ticket_data += "    LUPITA\n".encode('cp437', errors='replace')
            ticket_data += TEXT_NORMAL
            
            # Línea separadora
            ticket_data += "================================\n".encode('cp437')
            
            # Fecha
            fecha = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ticket_data += f"Fecha: {fecha}\n".encode('cp437', errors='replace')
            ticket_data += "================================\n\n".encode('cp437')
            
            # Productos
            ticket_data += ALIGN_LEFT + TEXT_NORMAL
            for linea in lineas_ticket:
                if linea.strip():
                    # Limitar a 42 caracteres para impresora de 58mm
                    ticket_data += linea[:42].encode('cp437', errors='replace') + b'\n'
            
            ticket_data += "\n--------------------------------\n".encode('cp437')
            
            # Total
            ticket_data += ALIGN_RIGHT + TEXT_BOLD
            ticket_data += f"TOTAL: ${total:.2f}\n".encode('cp437', errors='replace')
            
            if paga_con:
                ticket_data += TEXT_NORMAL
                ticket_data += f"Paga con: ${paga_con:.2f}\n".encode('cp437', errors='replace')
                ticket_data += f"Cambio: ${cambio:.2f}\n".encode('cp437', errors='replace')
            
            ticket_data += b"\n"
            ticket_data += ALIGN_CENTER
            ticket_data += "================================\n".encode('cp437')
            ticket_data += "GRACIAS POR SU COMPRA\n".encode('cp437', errors='replace')
            ticket_data += "================================\n\n\n".encode('cp437')
            
            # Cortar papel
            ticket_data += CUT
            
            # Enviar a impresora
            hPrinter = win32print.OpenPrinter(self.printer_name)
            try:
                hJob = win32print.StartDocPrinter(hPrinter, 1, ("Ticket", None, "RAW"))
                try:
                    win32print.StartPagePrinter(hPrinter)
                    win32print.WritePrinter(hPrinter, ticket_data)
                    win32print.EndPagePrinter(hPrinter)
                finally:
                    win32print.EndDocPrinter(hPrinter)
            finally:
                win32print.ClosePrinter(hPrinter)
            
            print("✅ Ticket enviado a la impresora")
            return True
            
        except Exception as e:
            logger.error("Error al imprimir: %s", e)
            messagebox.showerror("Error de Impresión",
                               "No se pudo imprimir el ticket.\n\n"
                               f"Verifica que la impresora '{self.printer_name}' esté encendida y conectada.")
            return False


class PuntoVentaApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Punto de Venta - Inicio de Sesión")
        self.root.geometry("400x350")
        self.root.config(bg="#E6F7FF")

        # Inicializar base de datos
        self.db = DatabaseManager()
        if not self.db.conectar():
            self.root.quit()
            return
        
        # Inicializar impresora térmica
        self.thermal_printer = ThermalPrinterManager()
        
        # Variable para el timer de búsqueda
        self.search_timer = None
        
        # Lista temporal de ventas (para el ticket)
        self.ventas_temporales = []
        
        # Etiqueta y entrada para usuario
        tk.Label(root, text="Usuario:", bg="#E6F7FF", font=("Arial", 12)).pack(pady=5)
        self.entry_usuario = tk.Entry(root, font=("Arial", 12))
        self.entry_usuario.pack(pady=5)

        # Etiqueta y entrada para contraseña
        tk.Label(root, text="Contraseña:", bg="#E6F7FF", font=("Arial", 12)).pack(pady=5)
        self.entry_contrasena = tk.Entry(root, show="*", font=("Arial", 12))
        self.entry_contrasena.pack(pady=5)

        # Botón de inicio de sesión
        btn_iniciar = tk.Button(root, text="Iniciar sesión", command=self.iniciar_sesion, bg="#4CAF50", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_iniciar.pack(pady=10)

        # Botón de recuperación de contraseña
        btn_olvido = tk.Button(root, text="¿Olvidó su contraseña?", command=self.recuperar_contrasena, bg="#f44336", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_olvido.pack(pady=5)
        
        # La creación de usuarios solo está disponible para administradores dentro del menú principal.

        # Enlace la tecla Enter al evento de inicio de sesión
        self.root.bind('<Return>', lambda event: self.iniciar_sesion())
        
        # Manejar cierre de ventana
        self.root.protocol("WM_DELETE_WINDOW", self.cerrar_aplicacion)

    def cerrar_aplicacion(self):
        """Cierra la conexión a la base de datos antes de cerrar la aplicación"""
        self.db.desconectar()
        self.root.quit()

    def iniciar_sesion(self):
        usuario = self.entry_usuario.get()
        contrasena = self.entry_contrasena.get()
        
        ahora = _time.time()
        bloqueado_hasta = getattr(self, "bloqueado_hasta", 0)
        if ahora < bloqueado_hasta:
            messagebox.showerror("Acceso bloqueado",
                                 f"Demasiados intentos fallidos. Espera {int(bloqueado_hasta - ahora) + 1} segundos.")
            return

        if self.db.verificar_usuario(usuario, contrasena):
            self.intentos_fallidos = 0
            self.mostrar_menu_principal()
        else:
            self.intentos_fallidos = getattr(self, "intentos_fallidos", 0) + 1
            if self.intentos_fallidos >= MAX_INTENTOS:
                self.bloqueado_hasta = ahora + SEGUNDOS_BLOQUEO
                self.intentos_fallidos = 0
                logger.warning("Acceso bloqueado temporalmente tras %d intentos fallidos (usuario: %s)", MAX_INTENTOS, usuario)
            messagebox.showerror("Error", "Usuario o contraseña incorrectos")

    def recuperar_contrasena(self):
        messagebox.showinfo("Recuperar contraseña", "Por favor, contacte al administrador.")
        
    def mostrar_crear_usuario(self):
        if self.db.usuario_actual_rol != "admin":
            messagebox.showerror("Acceso denegado", "Solo un administrador puede crear usuarios")
            return
        # Crear ventana para nuevo usuario
        self.ventana_nuevo_usuario = tk.Toplevel(self.root)
        self.ventana_nuevo_usuario.title("Crear Nuevo Usuario")
        self.ventana_nuevo_usuario.geometry("300x270")
        self.ventana_nuevo_usuario.config(bg="#E6F7FF")
        
        # Campos para nuevo usuario
        tk.Label(self.ventana_nuevo_usuario, text="Nuevo Usuario:", bg="#E6F7FF", font=("Arial", 12)).pack(pady=5)
        self.entry_nuevo_usuario = tk.Entry(self.ventana_nuevo_usuario, font=("Arial", 12))
        self.entry_nuevo_usuario.pack(pady=5)
        
        tk.Label(self.ventana_nuevo_usuario, text="Nueva Contraseña:", bg="#E6F7FF", font=("Arial", 12)).pack(pady=5)
        self.entry_nueva_contrasena = tk.Entry(self.ventana_nuevo_usuario, show="*", font=("Arial", 12))
        self.entry_nueva_contrasena.pack(pady=5)

        tk.Label(self.ventana_nuevo_usuario, text="Rol:", bg="#E6F7FF", font=("Arial", 12)).pack(pady=5)
        self.combo_rol = ttk.Combobox(self.ventana_nuevo_usuario, values=list(ROLES_VALIDOS), state="readonly", font=("Arial", 12))
        self.combo_rol.set("vendedor")
        self.combo_rol.pack(pady=5)
        
        # Botón para guardar nuevo usuario
        btn_guardar = tk.Button(self.ventana_nuevo_usuario, text="Guardar", command=self.crear_nuevo_usuario, bg="#4CAF50", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_guardar.pack(pady=10)
    
    def crear_nuevo_usuario(self):
        nuevo_usuario = self.entry_nuevo_usuario.get()
        nueva_contrasena = self.entry_nueva_contrasena.get()
        
        if nuevo_usuario and nueva_contrasena:
            exito, mensaje = self.db.crear_usuario(nuevo_usuario, nueva_contrasena, self.combo_rol.get())
            if exito:
                messagebox.showinfo("Éxito", f"Usuario '{nuevo_usuario}' creado correctamente")
                self.ventana_nuevo_usuario.destroy()
            else:
                messagebox.showerror("Error", mensaje)
        else:
            messagebox.showerror("Error", "Todos los campos son obligatorios")

    def mostrar_menu_principal(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        self.root.title("Punto de Venta - Inicio")
        self.root.geometry("400x360")
        self.root.config(bg="#E6F7FF")

        # Etiqueta de título
        titulo = tk.Label(self.root, text="Inicio", font=("Arial", 16, "bold"), bg="#E6F7FF")
        titulo.pack(pady=10)

        # Contenedor para los botones
        frame_botones = tk.Frame(self.root, bg="#E6F7FF")
        frame_botones.pack(pady=10)

        btn_ventas = tk.Button(frame_botones, text="Ventas", command=self.abrir_ventas, bg="#2196F3", fg="white", font=("Arial", 12), bd=2, relief="solid", width=15)
        btn_ventas.pack(pady=5)

        btn_devoluciones = tk.Button(frame_botones, text="Devoluciones", command=self.abrir_devoluciones, bg="#FF9800", fg="white", font=("Arial", 12), bd=2, relief="solid", width=15)
        btn_devoluciones.pack(pady=5)

        btn_reportes = tk.Button(frame_botones, text="Reportes", command=self.abrir_reportes, bg="#2196F3", fg="white", font=("Arial", 12), bd=2, relief="solid", width=15)
        btn_reportes.pack(pady=5)

        btn_compras = tk.Button(frame_botones, text="Compras", command=self.abrir_compras, bg="#2196F3", fg="white", font=("Arial", 12), bd=2, relief="solid", width=15)
        btn_compras.pack(pady=5)

        if self.db.usuario_actual_rol == "admin":
            btn_usuarios = tk.Button(frame_botones, text="Usuarios", command=self.mostrar_crear_usuario, bg="#9C27B0", fg="white", font=("Arial", 12), bd=2, relief="solid", width=15)
            btn_usuarios.pack(pady=5)
        else:
            btn_reportes.config(state="disabled")
            btn_compras.config(state="disabled")

    def abrir_ventas(self):
        self.mostrar_interfaz_ventas()

    def mostrar_interfaz_ventas(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        self.root.title("Ventas")
        self.root.geometry("1000x600")
        self.root.config(bg="#E6F7FF")
        
        # Limpiar lista temporal de ventas
        self.ventas_temporales = []

        # Frame principal
        main_frame = tk.Frame(self.root, bg="#E6F7FF")
        main_frame.pack(fill="both", expand=True)

        # Frame izquierdo
        left_frame = tk.Frame(main_frame, bg="#E6F7FF")
        left_frame.pack(side="left", fill="y", padx=10, pady=10)

        # Botón de volver
        btn_volver = tk.Button(left_frame, text="Volver", command=self.mostrar_menu_principal, bg="#f44336", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_volver.pack(anchor="n", pady=5)

        # Frame para los campos de entrada
        frame_entrada = tk.Frame(left_frame, bg="#E6F7FF")
        frame_entrada.pack(pady=10)

        # Campo de entrada para el producto (código)
        tk.Label(frame_entrada, text="Código:", bg="#E6F7FF", font=("Arial", 12)).grid(row=0, column=0, padx=5, pady=5)
        self.entry_codigo = tk.Entry(frame_entrada, font=("Arial", 14), width=20)
        self.entry_codigo.grid(row=0, column=1, padx=5, pady=5)
        self.entry_codigo.bind("<KeyRelease>", self.on_codigo_key_release)
        
        # Campo para nombre del producto (solo si es nuevo)
        tk.Label(frame_entrada, text="Producto:", bg="#E6F7FF", font=("Arial", 12)).grid(row=1, column=0, padx=5, pady=5)
        self.entry_producto = tk.Entry(frame_entrada, font=("Arial", 12), width=20, state='readonly')
        self.entry_producto.grid(row=1, column=1, padx=5, pady=5)

        # Campo de entrada para la cantidad
        tk.Label(frame_entrada, text="Cantidad:", bg="#E6F7FF", font=("Arial", 12)).grid(row=2, column=0, padx=5, pady=5)
        self.entry_cantidad = tk.Entry(frame_entrada, font=("Arial", 12))
        self.entry_cantidad.grid(row=2, column=1, padx=5, pady=5)

        # Campo de entrada para el precio
        tk.Label(frame_entrada, text="Precio:", bg="#E6F7FF", font=("Arial", 12)).grid(row=3, column=0, padx=5, pady=5)
        self.entry_precio = tk.Entry(frame_entrada, font=("Arial", 12))
        self.entry_precio.grid(row=3, column=1, padx=5, pady=5)
        
        # Label de stock
        self.label_stock = tk.Label(frame_entrada, text="Stock: --", bg="#E6F7FF", font=("Arial", 10), fg="blue")
        self.label_stock.grid(row=4, column=1, padx=5, pady=5)

        # Instrucciones
        tk.Label(left_frame, text="📌 Instrucciones:", bg="#E6F7FF", font=("Arial", 10, "bold")).pack(pady=5)
        tk.Label(left_frame, text="1. Escanea el código", bg="#E6F7FF", font=("Arial", 9), fg="#333").pack(anchor="w", padx=20)
        tk.Label(left_frame, text="2. Si no existe, se registra", bg="#E6F7FF", font=("Arial", 9), fg="#333").pack(anchor="w", padx=20)
        tk.Label(left_frame, text="3. Confirma cantidad y precio", bg="#E6F7FF", font=("Arial", 9), fg="#333").pack(anchor="w", padx=20)

        # Botón de aceptar venta
        btn_aceptar = tk.Button(left_frame, text="Agregar al Carrito", command=self.agregar_al_carrito, bg="#4CAF50", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_aceptar.pack(pady=10)

        # Botón eliminar del carrito
        btn_eliminar = tk.Button(left_frame, text="Eliminar del Carrito", command=self.eliminar_venta, bg="#f44336", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_eliminar.pack(pady=5)
        
        # Botón finalizar venta
        btn_finalizar = tk.Button(left_frame, text="💰 FINALIZAR VENTA", command=self.finalizar_venta, bg="#FF5722", fg="white", font=("Arial", 14, "bold"), bd=3, relief="solid")
        btn_finalizar.pack(pady=15)

        # Frame derecho para la tabla de productos
        right_frame = tk.Frame(main_frame, bg="#E6F7FF")
        right_frame.pack(side="right", fill="both", expand=True, padx=10, pady=10)

        # Tabla para mostrar los productos
        frame_tabla = tk.Frame(right_frame, bg="#E6F7FF")
        frame_tabla.pack(fill="both", expand=True)

        # Crear el Treeview
        self.tree = ttk.Treeview(frame_tabla, columns=("Código", "Producto", "Cantidad", "Precio", "Subtotal"), show="headings")
        self.tree.heading("Código", text="Código")
        self.tree.heading("Producto", text="Producto")
        self.tree.heading("Cantidad", text="Cantidad")
        self.tree.heading("Precio", text="Precio")
        self.tree.heading("Subtotal", text="Subtotal")
        self.tree.column("Código", width=120)
        self.tree.column("Producto", width=200)
        self.tree.column("Cantidad", width=80)
        self.tree.column("Precio", width=80)
        self.tree.column("Subtotal", width=100)
        self.tree.pack(expand=True, fill="both")

        # Campos para el total
        frame_total = tk.Frame(right_frame, bg="#E6F7FF")
        frame_total.pack(pady=10)

        tk.Label(frame_total, text="TOTAL A PAGAR:", bg="#E6F7FF", font=("Arial", 14, "bold")).grid(row=0, column=0, padx=10)
        self.label_total = tk.Label(frame_total, text="$0.00", bg="#E6F7FF", font=("Arial", 16, "bold"), fg="green")
        self.label_total.grid(row=0, column=1)

        # Enfocar el campo código
        self.entry_codigo.focus()

    def on_codigo_key_release(self, event):
        """Se ejecuta cada vez que se suelta una tecla en el campo código"""
        if self.search_timer:
            self.root.after_cancel(self.search_timer)
        
        codigo = self.entry_codigo.get().strip()
        
        if len(codigo) >= 8:
            self.search_timer = self.root.after(600, self.buscar_producto_o_pedir_datos)

    def buscar_producto_o_pedir_datos(self):
        """Busca el producto o pide datos para registrarlo"""
        codigo = self.entry_codigo.get().strip()
        
        if not codigo or len(codigo) < 8:
            return
        
        print(f"🔍 Buscando código: '{codigo}'")
        
        # Buscar producto
        producto = self.db.obtener_producto_por_codigo(codigo)
        
        if producto:
            # Producto encontrado
            print(f"✅ Producto encontrado: {producto['nombre_producto']}")
            
            # Habilitar campo producto para mostrar nombre (readonly)
            self.entry_producto.config(state='normal')
            self.entry_producto.delete(0, tk.END)
            self.entry_producto.insert(0, producto['nombre_producto'])
            self.entry_producto.config(state='readonly')
            
            # Autocompletar precio
            self.entry_precio.delete(0, tk.END)
            self.entry_precio.insert(0, str(producto['precio']))
            
            # Poner cantidad en 1
            self.entry_cantidad.delete(0, tk.END)
            self.entry_cantidad.insert(0, "1")
            
            # Mostrar stock
            stock_color = "green" if producto['stock'] > 10 else "orange" if producto['stock'] > 0 else "red"
            self.label_stock.config(text=f"Stock: {producto['stock']}", fg=stock_color)
            
            # Enfocar cantidad
            self.entry_cantidad.focus()
            self.entry_cantidad.select_range(0, tk.END)
            
        else:
            # Producto NO encontrado - Pedir datos para registrarlo
            print(f"⚠️ Producto no encontrado. Solicitando datos...")
            self.solicitar_datos_producto_nuevo(codigo)

    def solicitar_datos_producto_nuevo(self, codigo):
        """Abre ventana para registrar un producto nuevo"""
        ventana_nuevo = tk.Toplevel(self.root)
        ventana_nuevo.title("Registrar Producto Nuevo")
        ventana_nuevo.geometry("400x250")
        ventana_nuevo.config(bg="#FFF3E0")
        ventana_nuevo.grab_set()  # Modal
        
        tk.Label(ventana_nuevo, text="⚠️ Producto no encontrado en catálogo", bg="#FFF3E0", font=("Arial", 12, "bold"), fg="#FF5722").pack(pady=10)
        tk.Label(ventana_nuevo, text=f"Código: {codigo}", bg="#FFF3E0", font=("Arial", 11)).pack(pady=5)
        
        # Nombre del producto
        tk.Label(ventana_nuevo, text="Nombre del producto:", bg="#FFF3E0", font=("Arial", 10)).pack(pady=5)
        entry_nombre = tk.Entry(ventana_nuevo, font=("Arial", 12), width=30)
        entry_nombre.pack(pady=5)
        entry_nombre.focus()
        
        # Precio
        tk.Label(ventana_nuevo, text="Precio de venta:", bg="#FFF3E0", font=("Arial", 10)).pack(pady=5)
        entry_precio_nuevo = tk.Entry(ventana_nuevo, font=("Arial", 12), width=30)
        entry_precio_nuevo.pack(pady=5)
        
        def guardar_y_continuar():
            nombre = entry_nombre.get().strip()
            precio_str = entry_precio_nuevo.get().strip()
            
            if not nombre or not precio_str:
                messagebox.showerror("Error", "Completa todos los campos")
                return
            
            try:
                precio = validar_numero(precio_str, "El precio", 0.01, 99999.99)
                
                # Registrar producto en catálogo
                if self.db.registrar_producto_nuevo(codigo, nombre, precio, stock_inicial=0):
                    messagebox.showinfo("Éxito", f"✅ Producto '{nombre}' registrado correctamente")
                    
                    # Llenar campos en ventana de ventas
                    self.entry_producto.config(state='normal')
                    self.entry_producto.delete(0, tk.END)
                    self.entry_producto.insert(0, nombre)
                    self.entry_producto.config(state='readonly')
                    
                    self.entry_precio.delete(0, tk.END)
                    self.entry_precio.insert(0, str(precio))
                    
                    self.entry_cantidad.delete(0, tk.END)
                    self.entry_cantidad.insert(0, "1")
                    
                    self.label_stock.config(text="Stock: 0 (NUEVO)", fg="blue")
                    
                    ventana_nuevo.destroy()
                    self.entry_cantidad.focus()
                else:
                    messagebox.showerror("Error", "No se pudo registrar el producto")
                    
            except ValueError as e:
                messagebox.showerror("Error", str(e))
        
        # Botones
        frame_btns = tk.Frame(ventana_nuevo, bg="#FFF3E0")
        frame_btns.pack(pady=10)
        
        tk.Button(frame_btns, text="Guardar y Continuar", command=guardar_y_continuar, bg="#4CAF50", fg="white", font=("Arial", 11)).pack(side="left", padx=5)
        tk.Button(frame_btns, text="Cancelar", command=ventana_nuevo.destroy, bg="#f44336", fg="white", font=("Arial", 11)).pack(side="left", padx=5)

    def agregar_al_carrito(self):
        """Agrega producto al carrito (lista temporal) sin guardarlo en BD todavía"""
        codigo = self.entry_codigo.get().strip()
        producto = self.entry_producto.get().strip()
        cantidad_str = self.entry_cantidad.get().strip()
        precio_str = self.entry_precio.get().strip()
        
        if not codigo or not producto or not cantidad_str or not precio_str:
            messagebox.showerror("Error", "Completa todos los campos")
            return
        
        try:
            cantidad = validar_numero(cantidad_str, "La cantidad", 0.001, 9999)
            precio = validar_numero(precio_str, "El precio", 0.01, 99999.99)
            subtotal = cantidad * precio
            
            # Verificar stock disponible
            prod_info = self.db.obtener_producto_por_codigo(codigo)
            if prod_info and prod_info['stock'] < cantidad:
                respuesta = messagebox.askyesno("Stock Insuficiente", 
                                               f"Solo hay {prod_info['stock']} unidades en stock.\n"
                                               f"¿Deseas vender {cantidad} de todos modos?\n\n"
                                               "El stock quedará negativo.")
                if not respuesta:
                    return
            
            # Agregar a lista temporal y a la tabla visual
            self.ventas_temporales.append({
                'codigo': codigo,
                'producto': producto,
                'cantidad': cantidad,
                'precio': precio,
                'subtotal': subtotal
            })
            
            self.tree.insert("", "end", values=(codigo, producto, cantidad, f"${precio:.2f}", f"${subtotal:.2f}"))
            
            # Limpiar campos
            self.entry_codigo.delete(0, tk.END)
            self.entry_producto.config(state='normal')
            self.entry_producto.delete(0, tk.END)
            self.entry_producto.config(state='readonly')
            self.entry_cantidad.delete(0, tk.END)
            self.entry_precio.delete(0, tk.END)
            self.label_stock.config(text="Stock: --")
            
            # Actualizar total
            self.actualizar_total()
            
            # Enfocar código para siguiente producto
            self.entry_codigo.focus()
            
        except ValueError as e:
            messagebox.showerror("Error", str(e))

    def actualizar_total(self):
        """Calcula el total de la venta actual"""
        total = sum(venta['subtotal'] for venta in self.ventas_temporales)
        self.label_total.config(text=f"${total:.2f}")

    def eliminar_venta(self):
        """Elimina producto del carrito temporal"""
        selected_item = self.tree.selection()
        if selected_item:
            # Obtener índice del item seleccionado
            idx = self.tree.index(selected_item[0])
            
            # Eliminar de lista temporal
            if 0 <= idx < len(self.ventas_temporales):
                self.ventas_temporales.pop(idx)
            
            # Eliminar de tabla visual
            self.tree.delete(selected_item)
            
            # Actualizar total
            self.actualizar_total()
        else:
            messagebox.showerror("Error", "Selecciona un producto para eliminar")

    def finalizar_venta(self):
        """Finaliza la venta, guarda en BD, actualiza inventario e imprime"""
        if not self.ventas_temporales:
            messagebox.showerror("Error", "No hay productos en el carrito")
            return
        
        # Calcular total
        total = sum(venta['subtotal'] for venta in self.ventas_temporales)
        
        # Pedir pago
        ventana_pago = tk.Toplevel(self.root)
        ventana_pago.title("Finalizar Venta")
        ventana_pago.geometry("300x200")
        ventana_pago.config(bg="#E8F5E9")
        ventana_pago.grab_set()
        
        tk.Label(ventana_pago, text=f"TOTAL: ${total:.2f}", bg="#E8F5E9", font=("Arial", 16, "bold"), fg="green").pack(pady=10)
        
        tk.Label(ventana_pago, text="Paga con:", bg="#E8F5E9", font=("Arial", 12)).pack(pady=5)
        entry_paga = tk.Entry(ventana_pago, font=("Arial", 14), width=15)
        entry_paga.pack(pady=5)
        entry_paga.focus()
        
        label_cambio = tk.Label(ventana_pago, text="Cambio: $0.00", bg="#E8F5E9", font=("Arial", 12))
        label_cambio.pack(pady=5)
        
        def calcular_cambio(event=None):
            try:
                paga_con = float(entry_paga.get())
                cambio = paga_con - total
                label_cambio.config(text=f"Cambio: ${cambio:.2f}", fg="blue" if cambio >= 0 else "red")
            except ValueError:
                label_cambio.config(text="Cambio: $0.00")
        
        entry_paga.bind("<KeyRelease>", calcular_cambio)
        
        def confirmar_venta():
            try:
                paga_con = validar_numero(entry_paga.get(), "El pago", 0.01, 999999.99)
                if paga_con < total:
                    messagebox.showerror("Error", "El pago es insuficiente")
                    return
                
                cambio = paga_con - total
                
                # Guardar todas las ventas en BD y actualizar inventario
                for venta in self.ventas_temporales:
                    exito, venta_id = self.db.registrar_venta(
                        venta['codigo'],
                        venta['producto'],
                        venta['cantidad'],
                        venta['precio'],
                        venta['subtotal']
                    )
                    
                    if not exito:
                        messagebox.showerror("Error", f"No se pudo registrar: {venta['producto']}")
                        return
                
                # Preparar ticket
                lineas_ticket = []
                for venta in self.ventas_temporales:
                    # Formato: Producto (max 20 chars) | Cant | Precio | Subtotal
                    nombre = venta['producto'][:18]
                    linea = f"{nombre:<20} {venta['cantidad']:<4} ${venta['precio']:<6.2f} ${venta['subtotal']:.2f}"
                    lineas_ticket.append(linea)
                
                # Imprimir en impresora térmica
                impreso = self.thermal_printer.imprimir_ticket(lineas_ticket, total, paga_con, cambio)
                
                # Mostrar vista previa
                self.mostrar_vista_previa_ticket(lineas_ticket, total, paga_con, cambio)
                
                if impreso:
                    messagebox.showinfo("Venta Exitosa", 
                                      f"✅ Venta finalizada e impresa\n\n"
                                      f"Total: ${total:.2f}\n"
                                      f"Paga con: ${paga_con:.2f}\n"
                                      f"Cambio: ${cambio:.2f}\n\n"
                                      f"Stock actualizado automáticamente")
                else:
                    messagebox.showinfo("Venta Exitosa (sin impresión)", 
                                      f"✅ Venta finalizada\n\n"
                                      f"Total: ${total:.2f}\n"
                                      f"Paga con: ${paga_con:.2f}\n"
                                      f"Cambio: ${cambio:.2f}\n\n"
                                      f"⚠️ No se pudo imprimir\n"
                                      f"Stock actualizado automáticamente")
                
                ventana_pago.destroy()
                
                # Limpiar carrito y volver a ventas
                self.ventas_temporales = []
                for item in self.tree.get_children():
                    self.tree.delete(item)
                self.actualizar_total()
                self.entry_codigo.focus()
                
            except ValueError:
                messagebox.showerror("Error", "Ingresa un monto válido")
        
        tk.Button(ventana_pago, text="Confirmar Venta", command=confirmar_venta, bg="#4CAF50", fg="white", font=("Arial", 12, "bold")).pack(pady=10)

    def mostrar_vista_previa_ticket(self, lineas, total, paga_con, cambio):
        """Muestra vista previa del ticket - TÍTULO EN DOS LÍNEAS"""
        ventana_ticket = tk.Toplevel(self.root)
        ventana_ticket.title("Vista previa del Ticket")
        ventana_ticket.geometry("400x550")
        ventana_ticket.config(bg="white")
        
        texto_ticket = scrolledtext.ScrolledText(ventana_ticket, font=("Courier", 9), width=48)
        texto_ticket.pack(fill="both", expand=True, padx=10, pady=10)
        
        ticket_completo = []
        ticket_completo.append("=" * 42)
        ticket_completo.append("        MISCELANEA")
        ticket_completo.append("          LUPITA")
        ticket_completo.append("=" * 42)
        ticket_completo.append(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        ticket_completo.append("-" * 42)
        ticket_completo.extend(lineas)
        ticket_completo.append("-" * 42)
        ticket_completo.append(f"TOTAL: ${total:.2f}")
        ticket_completo.append(f"Paga con: ${paga_con:.2f}")
        ticket_completo.append(f"Cambio: ${cambio:.2f}")
        ticket_completo.append("=" * 42)
        ticket_completo.append("   GRACIAS POR SU COMPRA")
        ticket_completo.append("=" * 42)
        
        for linea in ticket_completo:
            texto_ticket.insert(tk.END, linea + "\n")

    def abrir_devoluciones(self):
        """Módulo de devoluciones"""
        for widget in self.root.winfo_children():
            widget.destroy()

        self.root.title("Devoluciones")
        self.root.geometry("800x600")
        self.root.config(bg="#E6F7FF")

        tk.Label(self.root, text="Módulo de Devoluciones", font=("Arial", 16, "bold"), bg="#E6F7FF").pack(pady=10)

        btn_volver = tk.Button(self.root, text="Volver", command=self.mostrar_menu_principal, bg="#f44336", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_volver.pack(anchor="ne", padx=10, pady=5)

        # Frame para datos de devolución
        frame_dev = tk.Frame(self.root, bg="#E6F7FF")
        frame_dev.pack(pady=20)

        tk.Label(frame_dev, text="Código del producto:", bg="#E6F7FF", font=("Arial", 12)).grid(row=0, column=0, padx=5, pady=5)
        entry_codigo_dev = tk.Entry(frame_dev, font=("Arial", 12), width=20)
        entry_codigo_dev.grid(row=0, column=1, padx=5, pady=5)

        tk.Label(frame_dev, text="Cantidad a devolver:", bg="#E6F7FF", font=("Arial", 12)).grid(row=1, column=0, padx=5, pady=5)
        entry_cantidad_dev = tk.Entry(frame_dev, font=("Arial", 12), width=20)
        entry_cantidad_dev.grid(row=1, column=1, padx=5, pady=5)

        tk.Label(frame_dev, text="Motivo:", bg="#E6F7FF", font=("Arial", 12)).grid(row=2, column=0, padx=5, pady=5)
        entry_motivo = tk.Entry(frame_dev, font=("Arial", 12), width=40)
        entry_motivo.grid(row=2, column=1, padx=5, pady=5)

        def procesar_devolucion():
            codigo = entry_codigo_dev.get().strip()
            cantidad_str = entry_cantidad_dev.get().strip()
            motivo = entry_motivo.get().strip()

            if not codigo or not cantidad_str:
                messagebox.showerror("Error", "Completa código y cantidad")
                return

            try:
                cantidad = validar_numero(cantidad_str, "La cantidad", 0.001, 9999)

                # Buscar producto
                producto = self.db.obtener_producto_por_codigo(codigo)
                if not producto:
                    messagebox.showerror("Error", "Producto no encontrado en catálogo")
                    return

                # Registrar devolución
                if self.db.registrar_devolucion(
                    venta_id=None,
                    codigo_barras=codigo,
                    producto=producto['nombre_producto'],
                    cantidad=cantidad,
                    precio=producto['precio'],
                    subtotal=cantidad * producto['precio'],
                    motivo=motivo or "Sin motivo"
                ):
                    messagebox.showinfo("Éxito", 
                                      f"✅ Devolución registrada\n\n"
                                      f"Producto: {producto['nombre_producto']}\n"
                                      f"Cantidad: {cantidad}\n"
                                      f"Stock devuelto al inventario")
                    
                    entry_codigo_dev.delete(0, tk.END)
                    entry_cantidad_dev.delete(0, tk.END)
                    entry_motivo.delete(0, tk.END)
                else:
                    messagebox.showerror("Error", "No se pudo registrar la devolución")

            except ValueError as e:
                messagebox.showerror("Error", str(e))

        tk.Button(self.root, text="Procesar Devolución", command=procesar_devolucion, bg="#4CAF50", fg="white", font=("Arial", 14), bd=2, relief="solid").pack(pady=20)

    def abrir_reportes(self):
        if self.db.usuario_actual_rol != "admin":
            messagebox.showerror("Acceso denegado", "Solo un administrador puede consultar reportes")
            return
        self.mostrar_interfaz_reportes()

    def mostrar_interfaz_reportes(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        self.root.title("Reportes")
        self.root.geometry("900x650")
        self.root.config(bg="#E6F7FF")

        tk.Label(self.root, text="Reportes", font=("Arial", 16, "bold"), bg="#E6F7FF").pack(pady=10)

        btn_volver = tk.Button(self.root, text="Volver", command=self.mostrar_menu_principal, bg="#f44336", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_volver.pack(anchor="ne", padx=10, pady=5)

        # Frame para opciones
        frame_opciones = tk.Frame(self.root, bg="#E6F7FF")
        frame_opciones.pack(pady=10)

        self.opcion_reportes = tk.StringVar(value="Ventas")
        tk.Radiobutton(frame_opciones, text="Ventas", variable=self.opcion_reportes, value="Ventas", bg="#E6F7FF", font=("Arial", 12), command=self.actualizar_reporte).pack(anchor="w")
        tk.Radiobutton(frame_opciones, text="Compras", variable=self.opcion_reportes, value="Compras", bg="#E6F7FF", font=("Arial", 12), command=self.actualizar_reporte).pack(anchor="w")

        # Frame para fechas
        frame_fechas = tk.Frame(self.root, bg="#E6F7FF")
        frame_fechas.pack(pady=10)

        tk.Label(frame_fechas, text="Desde:", bg="#E6F7FF", font=("Arial", 12)).grid(row=0, column=0, padx=5, pady=5)
        self.entry_desde = tk.Entry(frame_fechas, font=("Arial", 12))
        self.entry_desde.grid(row=0, column=1, padx=5, pady=5)
        self.entry_desde.insert(0, datetime.now().strftime("%Y-%m-%d"))

        tk.Label(frame_fechas, text="Hasta:", bg="#E6F7FF", font=("Arial", 12)).grid(row=0, column=2, padx=5, pady=5)
        self.entry_hasta = tk.Entry(frame_fechas, font=("Arial", 12))
        self.entry_hasta.grid(row=0, column=3, padx=5, pady=5)
        self.entry_hasta.insert(0, datetime.now().strftime("%Y-%m-%d"))

        # Botones
        frame_botones_rep = tk.Frame(self.root, bg="#E6F7FF")
        frame_botones_rep.pack(pady=10)

        btn_generar = tk.Button(frame_botones_rep, text="Generar Reporte", command=self.generar_reporte, bg="#4CAF50", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_generar.pack(side="left", padx=5)

        btn_exportar = tk.Button(frame_botones_rep, text="📊 Exportar a Excel", command=self.exportar_a_excel, bg="#FF9800", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_exportar.pack(side="left", padx=5)

        # Cuadro de texto
        frame_reporte = tk.Frame(self.root, bg="#E6F7FF")
        frame_reporte.pack(fill="both", expand=True, padx=10, pady=10)

        self.texto_reporte = scrolledtext.ScrolledText(frame_reporte, width=100, height=20)
        self.texto_reporte.pack(fill="both", expand=True)

        self.actualizar_reporte()

    def actualizar_reporte(self):
        self.texto_reporte.delete(1.0, tk.END)

        tipo_reporte = self.opcion_reportes.get()

        if tipo_reporte == "Ventas":
            self.texto_reporte.insert(tk.END, "=== REPORTE DE VENTAS ===\n")
            self.texto_reporte.insert(tk.END, "📊 Con actualización automática de inventario\n\n")

            ventas = self.db.obtener_ventas()

            if not ventas:
                self.texto_reporte.insert(tk.END, "No hay ventas registradas.\n")
            else:
                self.texto_reporte.insert(tk.END, f"{'Código':<15}{'Producto':<25}{'Cantidad':<10}{'Precio':<10}{'Subtotal':<10}{'Fecha':<20}\n")
                self.texto_reporte.insert(tk.END, "-" * 90 + "\n")

                total_ventas = 0
                for venta in ventas:
                    self.texto_reporte.insert(tk.END, f"{venta['codigo_barras']:<15}{venta['producto']:<25}{venta['cantidad']:<10}${venta['precio']:<9.2f}${venta['subtotal']:<9.2f}{venta['fecha_hora']:<20}\n")
                    total_ventas += venta['subtotal']

                self.texto_reporte.insert(tk.END, "-" * 90 + "\n")
                self.texto_reporte.insert(tk.END, f"TOTAL GENERAL: ${total_ventas:.2f}\n")
                self.texto_reporte.insert(tk.END, f"TRANSACCIONES: {len(ventas)}\n")

        else:  # Compras
            self.texto_reporte.insert(tk.END, "=== REPORTE DE COMPRAS ===\n\n")

            compras = self.db.obtener_compras()

            if not compras:
                self.texto_reporte.insert(tk.END, "No hay compras registradas.\n")
            else:
                self.texto_reporte.insert(tk.END, f"{'Artículo':<25}{'Cantidad':<10}{'Precio':<10}{'Total':<10}{'Proveedor':<15}{'Fecha':<20}\n")
                self.texto_reporte.insert(tk.END, "-" * 90 + "\n")

                total_compras = 0
                for compra in compras:
                    self.texto_reporte.insert(tk.END, f"{compra['articulo']:<25}{compra['cantidad']:<10}${compra['precio']:<9.2f}${compra['total']:<9.2f}{compra['proveedor']:<15}{compra['fecha_hora']:<20}\n")
                    total_compras += compra['total']

                self.texto_reporte.insert(tk.END, "-" * 90 + "\n")
                self.texto_reporte.insert(tk.END, f"TOTAL GENERAL: ${total_compras:.2f}\n")
                self.texto_reporte.insert(tk.END, f"NÚMERO DE COMPRAS: {len(compras)}\n")

    def generar_reporte(self):
        opcion = self.opcion_reportes.get()
        desde = self.entry_desde.get()
        hasta = self.entry_hasta.get()

        if opcion == "Ventas":
            datos = self.db.obtener_ventas(desde, hasta)
        else:
            datos = self.db.obtener_compras(desde, hasta)

        messagebox.showinfo("Reporte Generado",
                          f"✅ Reporte de {opcion} generado\n"
                          f"📅 Período: {desde} a {hasta}\n"
                          f"📊 Registros: {len(datos)}")

        self.actualizar_reporte()

    def exportar_a_excel(self):
        """Exporta el reporte actual a Excel usando openpyxl"""
        try:
            opcion = self.opcion_reportes.get()

            # Obtener datos
            if opcion == "Ventas":
                datos = self.db.obtener_ventas()
            else:
                datos = self.db.obtener_compras()

            if not datos:
                messagebox.showwarning("Sin datos", "No hay datos para exportar")
                return

            # Crear archivo Excel
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = f"Reporte {opcion}"

            # Estilos
            header_fill = PatternFill(start_color="4CAF50", end_color="4CAF50", fill_type="solid")
            header_font = Font(bold=True, color="FFFFFF", size=12)
            center_alignment = Alignment(horizontal="center", vertical="center")

            # Encabezados
            if opcion == "Ventas":
                headers = ["Código", "Producto", "Cantidad", "Precio", "Subtotal", "Fecha"]
                ws.append(headers)
                
                for cell in ws[1]:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = center_alignment

                # Datos
                for venta in datos:
                    ws.append([
                        venta['codigo_barras'],
                        venta['producto'],
                        venta['cantidad'],
                        venta['precio'],
                        venta['subtotal'],
                        venta['fecha_hora']
                    ])

                # Total
                total_row = ws.max_row + 1
                ws[f'E{total_row}'] = f"=SUM(E2:E{ws.max_row})"
                ws[f'E{total_row}'].font = Font(bold=True)

            else:  # Compras
                headers = ["Artículo", "Cantidad", "Precio", "Total", "Proveedor", "Fecha"]
                ws.append(headers)

                for cell in ws[1]:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = center_alignment

                for compra in datos:
                    ws.append([
                        compra['articulo'],
                        compra['cantidad'],
                        compra['precio'],
                        compra['total'],
                        compra['proveedor'],
                        compra['fecha_hora']
                    ])

                total_row = ws.max_row + 1
                ws[f'D{total_row}'] = f"=SUM(D2:D{ws.max_row})"
                ws[f'D{total_row}'].font = Font(bold=True)

            # Ajustar anchos
            for column in ws.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(cell.value)
                    except TypeError:
                        pass
                adjusted_width = (max_length + 2)
                ws.column_dimensions[column_letter].width = adjusted_width

            # Guardar archivo
            filename = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Excel files", "*.xlsx")],
                initialfile=f"Reporte_{opcion}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            )

            if filename:
                wb.save(filename)
                messagebox.showinfo("Éxito", f"✅ Reporte exportado correctamente:\n{filename}")
                
                # Abrir archivo
                if os.name == 'nt':  # Windows
                    os.startfile(filename)

        except Exception:
            logger.exception("Error al exportar el reporte")
            messagebox.showerror("Error", "No se pudo exportar el archivo. Revisa lupita_errores.log")

    def abrir_compras(self):
        if self.db.usuario_actual_rol != "admin":
            messagebox.showerror("Acceso denegado", "Solo un administrador puede registrar compras")
            return
        self.mostrar_interfaz_compras()

    def mostrar_interfaz_compras(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        self.root.title("Compras")
        self.root.geometry("800x600")
        self.root.config(bg="#E6F7FF")

        tk.Label(self.root, text="Compras", font=("Arial", 16, "bold"), bg="#E6F7FF").pack(pady=10)

        btn_volver = tk.Button(self.root, text="Volver", command=self.mostrar_menu_principal, bg="#f44336", fg="white", font=("Arial", 12), bd=2, relief="solid")
        btn_volver.pack(anchor="ne", padx=10, pady=5)

        frame_superior = tk.Frame(self.root, bg="#E6F7FF")
        frame_superior.pack(pady=10, fill="x")

        frame_articulo = tk.Frame(frame_superior, bg="#E6F7FF")
        frame_articulo.pack(pady=10)

        tk.Label(frame_articulo, text="Artículo:", bg="#E6F7FF", font=("Arial", 12)).grid(row=0, column=0, padx=5, pady=5)
        self.entry_articulo = tk.Entry(frame_articulo, font=("Arial", 12))
        self.entry_articulo.grid(row=0, column=1, padx=5, pady=5)

        tk.Label(frame_articulo, text="Cantidad:", bg="#E6F7FF", font=("Arial", 12)).grid(row=1, column=0, padx=5, pady=5)
        self.entry_cantidad_compra = tk.Entry(frame_articulo, font=("Arial", 12))
        self.entry_cantidad_compra.grid(row=1, column=1, padx=5, pady=5)

        tk.Label(frame_articulo, text="Precio:", bg="#E6F7FF", font=("Arial", 12)).grid(row=1, column=2, padx=5, pady=5)
        self.entry_precio_compra = tk.Entry(frame_articulo, font=("Arial", 12))
        self.entry_precio_compra.grid(row=1, column=3, padx=5, pady=5)

        tk.Label(frame_articulo, text="Proveedor:", bg="#E6F7FF", font=("Arial", 12)).grid(row=1, column=4, padx=5, pady=5)
        self.entry_proveedor = tk.Entry(frame_articulo, font=("Arial", 12))
        self.entry_proveedor.grid(row=1, column=5, padx=5, pady=5)

        frame_total = tk.Frame(frame_superior, bg="#E6F7FF")
        frame_total.pack(pady=10)

        tk.Label(frame_total, text="Total:", bg="#E6F7FF", font=("Arial", 12)).grid(row=0, column=0)
        self.label_total_compras = tk.Label(frame_total, text="$0.00", bg="#E6F7FF", font=("Arial", 12))
        self.label_total_compras.grid(row=0, column=1)

        frame_inferior = tk.Frame(self.root, bg="#E6F7FF")
        frame_inferior.pack(pady=10, fill="both", expand=True)

        tk.Label(frame_inferior, text="📋 Registro de Compras:", bg="#E6F7FF", font=("Arial", 12, "bold")).pack(anchor="w", padx=10)

        self.texto_compras = scrolledtext.ScrolledText(frame_inferior, width=80, height=15)
        self.texto_compras.pack(fill="both", expand=True, padx=10, pady=5)

        self.cargar_compras_desde_db()

        frame_botones = tk.Frame(self.root, bg="#E6F7FF")
        frame_botones.pack(pady=10, side="bottom")
        tk.Button(frame_botones, text="Registrar Compra", command=self.registrar_compra, bg="#2196F3", fg="white", font=("Arial", 12), bd=2, relief="solid").pack(side="left", padx=5)

    def cargar_compras_desde_db(self):
        compras = self.db.obtener_compras()

        self.texto_compras.delete(1.0, tk.END)

        if compras:
            self.texto_compras.insert(tk.END, "=== COMPRAS REGISTRADAS ===\n\n")
            for compra in compras:
                self.texto_compras.insert(tk.END, f"🛒 Artículo: {compra['articulo']}\n")
                self.texto_compras.insert(tk.END, f"   Cantidad: {compra['cantidad']}\n")
                self.texto_compras.insert(tk.END, f"   Precio: ${compra['precio']:.2f}\n")
                self.texto_compras.insert(tk.END, f"   Total: ${compra['total']:.2f}\n")
                self.texto_compras.insert(tk.END, f"   Proveedor: {compra['proveedor']}\n")
                self.texto_compras.insert(tk.END, f"   Fecha: {compra['fecha_hora']}\n")
                self.texto_compras.insert(tk.END, "-" * 50 + "\n\n")
        else:
            self.texto_compras.insert(tk.END, "No hay compras registradas.\n")

    def registrar_compra(self):
        articulo = self.entry_articulo.get()
        cantidad_str = self.entry_cantidad_compra.get()
        precio_str = self.entry_precio_compra.get()
        proveedor = self.entry_proveedor.get() if hasattr(self, 'entry_proveedor') else "Sin proveedor"

        if articulo and cantidad_str and precio_str:
            try:
                cantidad = validar_numero(cantidad_str, "La cantidad", 0.001, 9999)
                precio = validar_numero(precio_str, "El precio", 0.01, 99999.99)
                total = cantidad * precio

                if self.db.registrar_compra(articulo, cantidad, precio, total, proveedor):
                    self.cargar_compras_desde_db()

                    self.entry_articulo.delete(0, tk.END)
                    self.entry_cantidad_compra.delete(0, tk.END)
                    self.entry_precio_compra.delete(0, tk.END)
                    self.entry_proveedor.delete(0, tk.END)

                    self.label_total_compras.config(text="$0.00")

                    messagebox.showinfo("Compra Registrada", f"✅ Compra guardada:\n{articulo}, {cantidad}, ${precio:.2f}")
                else:
                    messagebox.showerror("Error", "No se pudo guardar la compra")

            except ValueError as e:
                messagebox.showerror("Error", str(e))
        else:
            messagebox.showerror("Error", "Completa todos los campos")


# Ejecutar aplicación
if __name__ == "__main__":
    root = tk.Tk()
    app = PuntoVentaApp(root)
    root.mainloop()
