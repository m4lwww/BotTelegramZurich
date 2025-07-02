# Bot de Monitoreo de Red con Telegram

## Descripción

Este proyecto es un bot de Telegram que monitorea hosts de red (IPv4/IPv6) mediante ping, envía alertas en caso de fallos y permite la gestión dinámica de hosts y usuarios. Está diseñado para ejecutarse en Windows como un ejecutable único generado con `auto-py-to-exe` (PyInstaller), utilizando un entorno virtual.

El bot soporta múltiples grupos de hosts, autenticación de usuarios con roles (admin y user), y una interfaz de gestión directamente en Telegram.

---

## Características

### Monitoreo de hosts
- Realiza pings a hosts en grupos definidos:
  - `cctv`, `servers`, `switches`, `corporativo`, `torniquetes_comedor`, `ip_publicas`
- Timeout: **2000 ms**
- Máximo de fallos antes de alerta: **5** (`MAX_FALLOS=5`)
- Intervalo:
  - `switches`: cada **15 segundos**
  - otros grupos: cada **5 segundos**
- Alertas con formato:
  ```
  🚨 [GRUPO] ¡ALERTA! <nombre> (<ip>) NO responde. ---
  ```
- Alertas persistentes cada 5 minutos si el host sigue sin responder

### Gestión de hosts
- Agregar/eliminar hosts dinámicamente
- Persistencia en `hosts_persistentes.json`
- Soporte para IPv4 e IPv6

### Gestión de usuarios (solo para admin)
- Listar usuarios registrados (identificador y rol)
- Agregar nuevos usuarios con rol (`admin` o `user`)
- Eliminar usuarios (excepto a sí mismos)

### Autenticación
- Requiere identificador válido desde `credenciales.json`
- Roles:
  - `admin`: acceso completo
  - `user`: acceso limitado

### Interfaz en Telegram
- Menú interactivo con botones para:
  - iniciar/detener monitoreo
  - ver estados
  - gestionar hosts y usuarios
- Opción "🛠 Gestión de Usuarios" exclusiva para administradores

### Persistencia
- Hosts:
  - `hosts_persistentes.json`
  - respaldo: `hosts_persistentes_backup.json`
- Usuarios:
  - `credenciales.json`
  - respaldo: `credenciales_backup.json`

### Logs
- Solo en consola, con formato:
  ```
  %(asctime)s - %(levelname)s - %(message)s
  ```

### Ejecutable
- Generado como un solo archivo (`main.exe`) con `auto-py-to-exe`
- Incluye `hosts.py` y `credenciales.json`

---

## Requisitos

- Python: **3.11.7**
- Sistema Operativo: **Windows**
- Dependencias:
  - `python-telegram-bot==20.7`

### Archivos necesarios:
- `main.py`: Lógica principal del bot
- `hosts.py`: Definiciones de hosts por grupo
- `credenciales.json`: Lista de usuarios autorizados
- `requirements.txt`: Dependencias del proyecto

### Opcional:
- Icono para el ejecutable (e.g., `ip-address_18234239.ico`)

---

## Instalación

### Clonar o descargar el proyecto:
```bash
git clone <URL_REPOSITORIO>
cd <DIRECTORIO_PROYECTO>
```

### Crear y activar entorno virtual:
```bash
python -m venv venv
venv\Scripts\activate  # En Windows
```

### Instalar dependencias:
```bash
pip install -r requirements.txt
```

### Configurar el token:
Edita `main.py` y reemplaza `TU_TOKEN_AQUI`:
```python
TOKEN = "token"
```

### Configurar credenciales:
Edita `credenciales.json` con al menos un usuario administrador:
```json
[
  {
    "identificador": "clave123",
    "rol": "admin"
  }
]
```

---

## Uso

### Ejecutar el script:
```bash
python main.py
```

- Inicia el bot y comienza el polling.
- Usa `/start` en Telegram e ingresa un identificador válido.

### Interfaz de Telegram

#### Autenticación:
Usa `/start` y proporciona un identificador de `credenciales.json`.

#### Menú principal (para todos los usuarios):
- 🟢 Iniciar todo
- 🔴 Detener todo
- 📊 Estado general
- 🟢 Hosts activos
- 🔴 Hosts inactivos
- 📋 Listar sesiones
- ➕ Agregar Host
- 🗑 Eliminar Host
- ⚙ Control por grupo
- 🚪 Cerrar sesión

#### Opciones para administradores:
- 🛡️ Cerrar sesiones no admin
- 🛠 Gestión de Usuarios:
  - 📋 Listar Usuarios
  - ➕ Agregar Usuario
  - 🗑 Eliminar Usuario
  - 🔑 Menú principal

---

## Ejemplo de uso

### Agregar usuario:
1. Selecciona `🛠 Gestión de Usuarios`.
2. Selecciona `➕ Agregar Usuario`.
3. Ingresa identificador (e.g., `nuevo123`).
4. Selecciona rol (`👑 Admin` o `👤 User`).
5. Confirma con `✅ Confirmar`.

### Eliminar usuario:
1. Selecciona `🛠 Gestión de Usuarios`.
2. Selecciona `🗑 Eliminar Usuario`.
3. Ingresa identificador.
4. Confirma con `✅ Confirmar`.

### Agregar host:
1. Selecciona `➕ Agregar Host`.
2. Selecciona grupo (e.g., `🚶 Torniquetes y Comedor`).
3. Ingresa IP (e.g., `192.168.100.200`).
4. Ingresa nombre (e.g., `Torniquete Nuevo`).
5. Confirma con `✅ Confirmar`.

### Recibir alerta:
```
🚨 [TORNIQUETES_COMEDOR] ¡ALERTA!
Tarjeta Controladora de Plumas Vehiculares y Puerta (192.168.100.91) NO responde.
---
```

---

## Generar Ejecutable

### Instalar auto-py-to-exe:
```bash
pip install auto-py-to-exe
```

### Ejecutar auto-py-to-exe:
```bash
auto-py-to-exe
```

### Configuración:
- **Script Location**: Selecciona `main.py`
- **Onefile**: "One File" (`--onefile`)
- **Console Window**: "Console Based" para ver logs
- **Additional Files**:
  - `<RUTA_PROYECTO>\hosts.py`
  - `<RUTA_PROYECTO>\credenciales.json`
- **Icon (opcional)**:
  - `ip-address_18234239.ico`
- **Output Directory**:
  - `<RUTA_PROYECTO>\output`
- **Advanced Options**:
  - `--hidden-import=telegram`

### Comando de ejemplo:
```bash
pyinstaller --noconfirm --onefile --console --icon <RUTA>\ip-address_18234239.ico --add-data "<RUTA_PROYECTO>\credenciales.json;." --add-data "<RUTA_PROYECTO>\hosts.py;." --hidden-import=telegram <RUTA_PROYECTO>\main.py
```

---

## Ejecutar el ejecutable

1. Copia `credenciales.json` al mismo directorio que el ejecutable.
2. Ejecuta:
```bash
cd output
main.exe > debug.log 2>&1
```

3. Revisa `debug.log` para depurar errores.

---

## Estructura de Archivos

```
<PROYECTO>/
├── main.py                         # Lógica principal
├── hosts.py                        # Hosts por grupo
├── credenciales.json               # Usuarios autorizados
├── requirements.txt                # Dependencias
├── output/
│   ├── main.exe                    # Ejecutable
│   ├── credenciales.json          # Copia del original
├── hosts_persistentes.json             # (Generado) Hosts dinámicos
├── hosts_persistentes_backup.json     # (Generado) Respaldo de hosts
├── credenciales_backup.json           # (Generado) Respaldo de credenciales
```

---

## Notas

- **Token**: Asegúrate de configurar un token válido en `main.py` antes de compilar.
- **Permisos**: El directorio de salida debe tener permisos de escritura.
- **Depuración**:
  - Revisa logs en consola o `debug.log`
  - Verifica conectividad a Telegram:
    ```bash
    ping api.telegram.org
    ```

---

## Errores comunes

- `ModuleNotFoundError: No module named 'telegram'`:  
  ➤ Solución: Añade `--hidden-import=telegram` en `auto-py-to-exe`.

- **Credenciales inválidas**:  
  ➤ Asegúrate de que `credenciales.json` esté en el directorio del ejecutable.

---

## Ejemplo de `credenciales.json`
```json
[
  {
    "identificador": "clave123",
    "rol": "admin"
  },
  {
    "identificador": "usuario456",
    "rol": "user"
  }
]
```

## Ejemplo de `requirements.txt`
```
python-telegram-bot==20.7
```
