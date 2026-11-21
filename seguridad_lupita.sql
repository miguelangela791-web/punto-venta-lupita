-- Cambios en la base de datos para la versión corregida de Lupita (ejecutar una sola vez, como administrador de MySQL)
USE punto_venta_final;

-- 1) La columna de contraseña debe alojar el hash (≈120 caracteres)
ALTER TABLE usuarios MODIFY COLUMN password VARCHAR(255) NOT NULL;

-- 2) Roles de usuario (admin / vendedor). Los usuarios existentes quedan como vendedor...
ALTER TABLE usuarios ADD COLUMN rol VARCHAR(20) NOT NULL DEFAULT 'vendedor';
-- ...excepto el primero que se registró, que queda como administrador
UPDATE usuarios SET rol = 'admin' WHERE id = (SELECT m FROM (SELECT MIN(id) AS m FROM usuarios) t);

-- 3) Usuario de MySQL con permisos mínimos para la aplicación (ya no se usa root)
--    La aplicación solo ejecuta SELECT, INSERT y UPDATE.
CREATE USER IF NOT EXISTS 'lupita_app'@'localhost' IDENTIFIED BY 'CAMBIA_ESTA_CLAVE';
GRANT SELECT, INSERT, UPDATE ON punto_venta_final.* TO 'lupita_app'@'localhost';
FLUSH PRIVILEGES;
