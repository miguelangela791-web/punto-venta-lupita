# Punto de Venta Lupita

Sistema de punto de venta de escritorio para **Miscelánea Lupita** (Tlapa de Comonfort, Guerrero). Sustituye el registro manual de ventas y el control visual del inventario por un sistema con lector de código de barras, impresión de tickets y reportes.

Proyecto académico de Ingeniería en Sistemas Computacionales, Instituto Tecnológico Superior de la Montaña (ITSM).

## Qué hace

| Módulo | Función |
|---|---|
| Inicio de sesión | Acceso con usuario y contraseña (hash PBKDF2), bloqueo de 60 s tras 5 intentos fallidos |
| Usuarios y roles | El administrador crea usuarios `admin` o `vendedor` |
| Ventas | Captura por lector de código de barras, carrito, cálculo de total y cambio, actualización automática del stock, ticket |
| Productos | Alta de productos nuevos al escanear un código que no existe |
| Devoluciones | Registro de devoluciones y regreso de piezas al inventario |
| Compras | Registro de compras a proveedores (solo administrador) |
| Reportes | Reportes de ventas y compras por rango de fechas, con exportación a Excel (solo administrador) |
| Impresión | Tickets en impresora térmica POS58 de 58 mm |

## Tecnologías

Python 3 · Tkinter · MySQL (`mysql-connector-python`) · openpyxl · pywin32 y Pillow para la impresión (Windows).

## Instalación

1. Instala Python 3 y MySQL.
2. Instala las dependencias:
   ```
   py -m pip install -r requirements.txt
   ```
3. Crea la base de datos `punto_venta_final` e importa `esquema_punto_venta.sql`.
4. Ejecuta `seguridad_lupita.sql` en MySQL (roles, tamaño de la columna de contraseña y usuario `lupita_app` con permisos mínimos). Antes, cambia `CAMBIA_ESTA_CLAVE` por una contraseña propia.
5. Define las variables de entorno (en Windows, cmd) y vuelve a abrir la terminal:
   ```
   setx LUPITA_DB_USER lupita_app
   setx LUPITA_DB_PASSWORD "tu_clave"
   ```
   Opcionales: `LUPITA_DB_HOST`, `LUPITA_DB_PORT`, `LUPITA_DB_NAME`.
6. Ejecuta:
   ```
   py punto_venta.py
   ```

Las credenciales de la base de datos **no están en el código**.

## Pruebas

`pruebas_seguridad.py` ejecuta 18 pruebas de seguridad y regresión con una base de datos simulada (no requiere MySQL):

```
py -I pruebas_seguridad.py punto_venta.py
```

## Seguridad

Se hizo una revisión con SonarQube, Bandit y revisión manual. Se corrigieron contraseñas en texto plano, creación de usuarios sin autenticación, falta de roles, credenciales en el código, validación de números, límite de intentos y mensajes de error con datos técnicos. Los detalles están en el reporte de pruebas de seguridad.

## Autor

Simon Arias Miguel Angel · 7.º semestre, grupo “A” · ITSM
