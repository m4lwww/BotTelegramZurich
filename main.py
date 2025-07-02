import subprocess
import time
import os
import asyncio
import json
import ipaddress
import logging
import threading
import queue
import sys
from concurrent.futures import ThreadPoolExecutor
from telegram import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler

# Manejo de rutas para ejecutable generado por PyInstaller
def get_resource_path(relative_path):
    """Obtiene la ruta correcta para archivos empaquetados en modo --onefile."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    else:
        return os.path.join(os.path.dirname(__file__), relative_path)

# Token de Telegram directamente en el archivo
TOKEN = "8028421146:AAELXcV_yvl1HS3A---XdByN93s3UW6HsTs" 

# Importa tus hosts personalizados
from hosts import (
    hosts_cctv,
    hosts_servers,
    hosts_switches,
    hosts_corporativo,
    hosts_torniquetes_comedor,
    hosts_ip_publicas,
)

# ================= CONFIGURACIÓN =================
# Configuración de logging (solo consola, sin bot.log)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Validar token
if not TOKEN or TOKEN == "TU_TOKEN_AQUI":
    logger.error("No se configuró un TOKEN válido en main.py")
    raise ValueError("TOKEN no configurado. Reemplaza TU_TOKEN_AQUI con tu token real.")

# Archivos de configuración
CREDENCIALES_FILE = get_resource_path("credenciales.json")
ARCHIVO_PERSISTENCIA = "hosts_persistentes.json"
ARCHIVO_RESPALDO = "hosts_persistentes_backup.json"
CREDENCIALES_RESPALDO = "credenciales_backup.json"

# Intervalo de ping por grupo (en segundos)
INTERVALOS_PING = {
    "cctv": 5,
    "servers": 5,
    "switches": 15,
    "corporativo": 5,
    "torniquetes_comedor": 5,
    "ip_publicas": 5,
}

# Intervalo para alertas persistentes (5 minutos)
INTERVALO_ALERTA_PERSISTENTE = 300

# Estados para ConversationHandler
LOGIN = 0
GRUPO, IP, NOMBRE, CONFIRMAR_AGREGAR = range(4)
ELIMINAR_GRUPO, ELIMINAR_IP, CONFIRMAR_ELIMINAR = range(3)
GESTION_USUARIOS, AGREGAR_USUARIO_ID, AGREGAR_USUARIO_ROL, CONFIRMAR_AGREGAR_USUARIO = range(4, 8)
ELIMINAR_USUARIO_ID, CONFIRMAR_ELIMINAR_USUARIO = range(8, 10)

# ================= ESTRUCTURA DE DATOS =================
hosts = {
    "cctv": dict(hosts_cctv),
    "servers": dict(hosts_servers),
    "switches": dict(hosts_switches),
    "corporativo": dict(hosts_corporativo),
    "torniquetes_comedor": dict(hosts_torniquetes_comedor),
    "ip_publicas": dict(hosts_ip_publicas),
}

estados = {
    grupo: {
        "activo": True,
        "estado_hosts": {ip: {"activo": True, "fallos": 0, "ultima_alerta": 0} for ip in datos_hosts}
    } for grupo, datos_hosts in hosts.items()
}

sesiones_activas = {}
monitoreo_global = False
data_lock = threading.Lock()
alert_queue = queue.Queue()
MAX_FALLOS = 5

# ================= FUNCIONES BÁSICAS =================
def ping(ip):
    """Ejecuta un ping a una IP con timeout extendido."""
    try:
        ip_addr = ipaddress.ip_address(ip)
        param = "-n" if os.name == "nt" else "-c"
        command = ["ping6" if ip_addr.version == 6 else "ping", param, "1", "-w", "2000", ip]
        logger.debug(f"Ejecutando comando: {' '.join(command)}")
        result = subprocess.call(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info(f"Ping a {ip}: {'Éxito' if result == 0 else 'Fallo'}")
        return result == 0
    except (subprocess.SubprocessError, ValueError) as e:
        logger.error(f"Error en ping a {ip}: {e}")
        return False

async def enviar_mensaje(app, chat_id, texto, parse_mode="Markdown", **kwargs):
    """Envía un mensaje a Telegram con formato."""
    try:
        max_length = 4096
        if len(texto) > max_length:
            for i in range(0, len(texto), max_length):
                await app.bot.send_message(chat_id=chat_id, text=texto[i:i + max_length], parse_mode=parse_mode, **kwargs)
        else:
            await app.bot.send_message(chat_id=chat_id, text=texto, parse_mode=parse_mode, **kwargs)
        logger.info(f"Mensaje enviado a {chat_id}: {texto[:50]}...")
    except Exception as e:
        logger.error(f"Error enviando mensaje a {chat_id}: {e}")

async def procesar_alertas(app):
    """Procesa alertas y las envía a todos los usuarios autenticados."""
    while True:
        try:
            mensaje = alert_queue.get_nowait()
            with data_lock:
                for chat_id in sesiones_activas:
                    asyncio.create_task(enviar_mensaje(app, chat_id, mensaje))
            alert_queue.task_done()
        except queue.Empty:
            await asyncio.sleep(0.1)

# ================= PERSISTENCIA =================
def cargar_credenciales():
    """Carga credenciales desde el archivo JSON."""
    try:
        with open(CREDENCIALES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Error cargando credenciales: {e}")
        raise FileNotFoundError("Crea un archivo credenciales.json con identificadores válidos en el directorio del ejecutable")

def guardar_credenciales(credenciales):
    """Guarda credenciales en el archivo JSON con respaldo."""
    try:
        with data_lock:
            if os.path.exists(CREDENCIALES_FILE):
                with open(CREDENCIALES_FILE, "rb") as src, open(CREDENCIALES_RESPALDO, "wb") as dst:
                    dst.write(src.read())
                logger.debug(f"Respaldo de credenciales creado en {CREDENCIALES_RESPALDO}")
            with open(CREDENCIALES_FILE, "w", encoding="utf-8") as f:
                json.dump(credenciales, f, indent=2)
            logger.info("Credenciales guardadas correctamente")
    except Exception as e:
        logger.error(f"Error guardando credenciales: {e}")
        raise

def guardar_hosts():
    """Guarda hosts en el archivo JSON con respaldo."""
    try:
        with data_lock:
            if os.path.exists(ARCHIVO_PERSISTENCIA):
                with open(ARCHIVO_PERSISTENCIA, "rb") as src, open(ARCHIVO_RESPALDO, "wb") as dst:
                    dst.write(src.read())
                logger.debug(f"Respaldo creado en {ARCHIVO_RESPALDO}")
            with open(ARCHIVO_PERSISTENCIA, "w", encoding="utf-8") as f:
                json.dump(hosts, f, indent=2)
            logger.info("Hosts guardados correctamente")
    except Exception as e:
        logger.error(f"Error guardando hosts: {e}")
        raise

def cargar_hosts():
    """Carga hosts desde el archivo JSON."""
    try:
        with data_lock:
            with open(ARCHIVO_PERSISTENCIA, "r", encoding="utf-8") as f:
                datos = json.load(f)
                for grupo in hosts:
                    if grupo in datos:
                        hosts[grupo].update(datos[grupo])
                        estados[grupo]["estado_hosts"].update({ip: {"activo": True, "fallos": 0, "ultima_alerta": 0} for ip in datos[grupo]})
            logger.info("Hosts cargados desde persistencia")
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("Usando hosts iniciales de hosts.py")

# ================= MONITOREO CON HILOS =================
def monitoreo_host(ip, nombre, grupo):
    """Monitorea un host individual."""
    try:
        logger.debug(f"Iniciando monitoreo de host {ip} ({nombre}) en {grupo}")
        estado_actual = ping(ip)
        current_time = time.time()
        with data_lock:
            estado_anterior = estados[grupo]["estado_hosts"][ip]["activo"]
            fallos = estados[grupo]["estado_hosts"][ip]["fallos"]
            ultima_alerta = estados[grupo]["estado_hosts"][ip]["ultima_alerta"]
            
            if not estado_actual:
                fallos += 1
                if fallos >= MAX_FALLOS:
                    if estado_anterior:
                        estados[grupo]["estado_hosts"][ip]["activo"] = False
                        estados[grupo]["estado_hosts"][ip]["ultima_alerta"] = current_time
                        mensaje = f"🚨 *[{grupo.upper()}] ¡ALERTA!*\n`{nombre}` ({ip}) NO responde.\n---"
                        alert_queue.put(mensaje)
                        logger.warning(f"Host {nombre} ({ip}) en {grupo} no responde tras {fallos} intentos")
                    elif current_time - ultima_alerta >= INTERVALO_ALERTA_PERSISTENTE:
                        estados[grupo]["estado_hosts"][ip]["ultima_alerta"] = current_time
                        mensaje = f"🚨 *[{grupo.upper()}] ¡ALERTA PERSISTENTE!*\n`{nombre}` ({ip}) sigue sin responder.\n---"
                        alert_queue.put(mensaje)
                        logger.warning(f"Host {nombre} ({ip}) en {grupo} sigue sin responder")
            else:
                if not estado_anterior:
                    estados[grupo]["estado_hosts"][ip]["activo"] = True
                    estados[grupo]["estado_hosts"][ip]["ultima_alerta"] = current_time
                    mensaje = f"✅ *[{grupo.upper()}] RECUPERADO*\n`{nombre}` ({ip}) ha vuelto a responder.\n---"
                    alert_queue.put(mensaje)
                    logger.info(f"Host {nombre} ({ip}) en {grupo} volvió a responder")
                fallos = 0
            
            estados[grupo]["estado_hosts"][ip]["fallos"] = fallos
    except Exception as e:
        logger.error(f"Error en monitoreo_host para {ip} ({nombre}) en {grupo}: {e}")

def monitoreo_grupo_thread(grupo, executor):
    """Monitorea un grupo de hosts usando un ThreadPoolExecutor."""
    try:
        logger.info(f"Iniciando monitoreo para {grupo}")
        while True:
            with data_lock:
                if not estados[grupo]["activo"] or not monitoreo_global:
                    logger.info(f"Deteniendo monitoreo de {grupo}")
                    break
                hosts_copy = hosts[grupo].copy()
            futures = []
            for ip, nombre in hosts_copy.items():
                logger.debug(f"Programando ping para {ip} ({nombre}) en {grupo}")
                futures.append(executor.submit(monitoreo_host, ip, nombre, grupo))
            
            for future in futures:
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Error en future para grupo {grupo}: {e}")
            
            logger.debug(f"Ciclo de monitoreo para {grupo} completado, esperando {INTERVALOS_PING[grupo]} segundos")
            time.sleep(INTERVALOS_PING[grupo])
    except Exception as e:
        logger.error(f"Error en monitoreo_grupo_thread para {grupo}: {e}")

# ================= TECLADOS =================
def teclado_principal(es_admin=False):
    """Teclado principal del bot, con opciones adicionales para admins."""
    botones = [
        ["🟢 Iniciar todo", "🔴 Detener todo"],
        ["📊 Estado general", "📋 Listar sesiones"],
        ["🟢 Hosts activos", "🔴 Hosts inactivos"],
        ["➕ Agregar Host", "🗑 Eliminar Host"],
        ["⚙ Control por grupo", "🚪 Cerrar sesión"]
    ]
    if es_admin:
        botones.insert(4, ["🛡️ Cerrar sesiones no admin", "🛠 Gestión de Usuarios"])
    return ReplyKeyboardMarkup(botones, resize_keyboard=True)

def teclado_grupos():
    """Teclado para seleccionar grupos."""
    return ReplyKeyboardMarkup([
        ["📷 CCTV", "💻 Servidores"],
        ["🔌 Switches", "🏢 Corporativo"],
        ["🚶 Torniquetes y Comedor", "🌐 IPs Públicas"],
        ["🔑 Menú principal"]
    ], resize_keyboard=True)

def teclado_confirmar():
    """Teclado para confirmar o cancelar una acción."""
    return ReplyKeyboardMarkup([["✅ Confirmar", "❌ Cancelar"]], resize_keyboard=True)

def teclado_gestion_usuarios():
    """Teclado para gestión de usuarios."""
    return ReplyKeyboardMarkup([
        ["📋 Listar Usuarios", "➕ Agregar Usuario"],
        ["🗑 Eliminar Usuario", "🔑 Menú principal"]
    ], resize_keyboard=True)

def teclado_rol():
    """Teclado para seleccionar rol."""
    return ReplyKeyboardMarkup([["👑 Admin", "👤 User"], ["❌ Cancelar"]], resize_keyboard=True)

# ================= HANDLERS DE LOGIN =================
async def start(update: Update, context):
    """Inicia el flujo de login solicitando el identificador."""
    chat_id = update.message.chat_id
    logger.info(f"Recibido /start de chat_id {chat_id}")
    if chat_id in sesiones_activas:
        es_admin = sesiones_activas[chat_id].get("rol") == "admin"
        await update.message.reply_text(
            "✅ *Ya estás autenticado*\nUsa los botones para interactuar con el bot.",
            reply_markup=teclado_principal(es_admin),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    await update.message.reply_text(
        "🔐 *Ingresa tu identificador:*\nEjemplo: `clave123`",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    logger.info(f"Inicio de login para chat_id {chat_id}")
    return LOGIN

async def verificar_identificador(update: Update, context):
    """Verifica el identificador ingresado."""
    chat_id = update.message.chat_id
    identificador = update.message.text.strip()
    logger.info(f"Verificando identificador para chat_id {chat_id}: {identificador}")
    credenciales = cargar_credenciales()
    
    for cred in credenciales:
        if identificador == cred["identificador"]:
            sesiones_activas[chat_id] = {
                "identificador": identificador,
                "rol": cred.get("rol", "user"),
                "timestamp": time.time()
            }
            es_admin = cred.get("rol", "user") == "admin"
            await update.message.reply_text(
                "✅ *Autenticación exitosa*\nUsa los botones para gestionar el monitoreo:\n- 🟢 Iniciar/detener\n- 📊 Ver estado\n- ➕ Agregar/eliminar hosts",
                reply_markup=teclado_principal(es_admin),
                parse_mode="Markdown"
            )
            logger.info(f"Login exitoso para chat_id {chat_id} con identificador {identificador}, rol: {cred.get('rol', 'user')}")
            return ConversationHandler.END
    
    await update.message.reply_text(
        "❌ *Identificador incorrecto*\nIngresa un identificador válido.\nEjemplo: `clave123`",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    logger.warning(f"Intento de login fallido para chat_id {chat_id}")
    return LOGIN

async def cancelar_login(update: Update, context):
    """Cancela el flujo de login."""
    await update.message.reply_text("❌ *Login cancelado*", parse_mode="Markdown")
    logger.info(f"Login cancelado para chat_id {update.message.chat_id}")
    return ConversationHandler.END

# ================= HANDLERS DE GESTIÓN DE USUARIOS =================
async def gestion_usuarios(update: Update, context):
    """Inicia el flujo de gestión de usuarios."""
    chat_id = update.message.chat_id
    if chat_id not in sesiones_activas:
        await update.message.reply_text("❌ *Usa /start para autenticarte*", parse_mode="Markdown")
        return ConversationHandler.END
    if sesiones_activas[chat_id].get("rol") != "admin":
        await update.message.reply_text("❌ *Acceso denegado: Solo administradores pueden gestionar usuarios*", parse_mode="Markdown")
        return ConversationHandler.END
    logger.info(f"Botón 'Gestión de Usuarios' presionado por {chat_id}")
    await update.message.reply_text(
        "🛠 *Gestión de Usuarios*\nSelecciona una opción:\n- 📋 Listar Usuarios\n- ➕ Agregar Usuario\n- 🗑 Eliminar Usuario\n\nUsa /cancel o '🔑 Menú principal' para salir.",
        reply_markup=teclado_gestion_usuarios(),
        parse_mode="Markdown"
    )
    return GESTION_USUARIOS

async def manejar_gestion_usuarios(update: Update, context):
    """Maneja las opciones del menú de gestión de usuarios."""
    texto = update.message.text
    chat_id = update.message.chat_id
    logger.info(f"Opción de gestión de usuarios recibida por {chat_id}: {texto}")
    
    if texto == "🔑 Menú principal":
        es_admin = sesiones_activas[chat_id].get("rol") == "admin"
        await update.message.reply_text(
            "*Menú Principal:*",
            reply_markup=teclado_principal(es_admin),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    if texto == "📋 Listar Usuarios":
        credenciales = cargar_credenciales()
        if not credenciales:
            await update.message.reply_text(
                "📋 *No hay usuarios registrados*",
                reply_markup=teclado_gestion_usuarios(),
                parse_mode="Markdown"
            )
        else:
            mensaje = "*📋 Lista de Usuarios*\n\n"
            for cred in credenciales:
                mensaje += f"- *Identificador*: `{cred['identificador']}`\n  *Rol*: {cred.get('rol', 'user')}\n"
            await update.message.reply_text(mensaje, reply_markup=teclado_gestion_usuarios(), parse_mode="Markdown")
        logger.info(f"Lista de usuarios solicitada por {chat_id}")
        return GESTION_USUARIOS
    
    if texto == "➕ Agregar Usuario":
        await update.message.reply_text(
            "➕ *Agregar Usuario*\nIngresa el identificador del nuevo usuario (máx. 50 caracteres).\nEjemplo: `nuevo123`\nUsa /cancel para salir.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        return AGREGAR_USUARIO_ID
    
    if texto == "🗑 Eliminar Usuario":
        credenciales = cargar_credenciales()
        if not credenciales:
            await update.message.reply_text(
                "❌ *No hay usuarios para eliminar*",
                reply_markup=teclado_gestion_usuarios(),
                parse_mode="Markdown"
            )
            return GESTION_USUARIOS
        mensaje = "*🗑 Eliminar Usuario*\nIngresa el identificador del usuario a eliminar:\n\n*Usuarios disponibles*:\n"
        for cred in credenciales:
            mensaje += f"- `{cred['identificador']}` ({cred.get('rol', 'user')})\n"
        mensaje += "\nEjemplo: `clave123`\nUsa /cancel para salir."
        await update.message.reply_text(mensaje, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        return ELIMINAR_USUARIO_ID
    
    await update.message.reply_text(
        "❌ *Opción inválida*\nSelecciona una opción válida:",
        reply_markup=teclado_gestion_usuarios(),
        parse_mode="Markdown"
    )
    return GESTION_USUARIOS

async def recibir_identificador_usuario(update: Update, context):
    """Recibe el identificador del nuevo usuario."""
    chat_id = update.message.chat_id
    identificador = update.message.text.strip()
    logger.info(f"Identificador recibido para agregar usuario por {chat_id}: {identificador}")
    
    if not identificador or len(identificador) > 50:
        await update.message.reply_text(
            "❌ *Identificador inválido*\nIngresa un identificador entre 1 y 50 caracteres.\nEjemplo: `nuevo123`",
            parse_mode="Markdown"
        )
        return AGREGAR_USUARIO_ID
    
    credenciales = cargar_credenciales()
    if any(cred["identificador"] == identificador for cred in credenciales):
        await update.message.reply_text(
            f"❌ *Identificador `{identificador}` ya existe*\nIngresa un identificador diferente.",
            parse_mode="Markdown"
        )
        return AGREGAR_USUARIO_ID
    
    context.user_data["nuevo_identificador"] = identificador
    await update.message.reply_text(
        f"📌 *Selecciona el rol para `{identificador}`*",
        reply_markup=teclado_rol(),
        parse_mode="Markdown"
    )
    return AGREGAR_USUARIO_ROL

async def recibir_rol_usuario(update: Update, context):
    """Recibe el rol del nuevo usuario."""
    texto = update.message.text
    chat_id = update.message.chat_id
    logger.info(f"Rol recibido para nuevo usuario por {chat_id}: {texto}")
    
    if texto == "❌ Cancelar":
        await update.message.reply_text(
            "❌ *Operación cancelada*",
            reply_markup=teclado_gestion_usuarios(),
            parse_mode="Markdown"
        )
        return GESTION_USUARIOS
    
    rol_map = {"👑 Admin": "admin", "👤 User": "user"}
    if texto not in rol_map:
        await update.message.reply_text(
            "❌ *Rol inválido*\nSelecciona un rol válido:",
            reply_markup=teclado_rol(),
            parse_mode="Markdown"
        )
        return AGREGAR_USUARIO_ROL
    
    context.user_data["nuevo_rol"] = rol_map[texto]
    identificador = context.user_data.get("nuevo_identificador")
    await update.message.reply_text(
        f"*Confirmación*\n\nVas a agregar:\n- *Identificador*: `{identificador}`\n- *Rol*: {context.user_data['nuevo_rol']}\n\n¿Confirmas?",
        reply_markup=teclado_confirmar(),
        parse_mode="Markdown"
    )
    return CONFIRMAR_AGREGAR_USUARIO

async def confirmar_agregar_usuario(update: Update, context):
    """Confirma o cancela la adición de un usuario."""
    texto = update.message.text
    chat_id = update.message.chat_id
    logger.info(f"Confirmación de agregar usuario recibida por {chat_id}: {texto}")
    
    if texto == "❌ Cancelar":
        await update.message.reply_text(
            "❌ *Operación cancelada*",
            reply_markup=teclado_gestion_usuarios(),
            parse_mode="Markdown"
        )
        logger.info(f"Adición de usuario cancelada por {chat_id}")
        return GESTION_USUARIOS
    
    if texto == "✅ Confirmar":
        identificador = context.user_data.get("nuevo_identificador")
        rol = context.user_data.get("nuevo_rol")
        if not identificador or not rol:
            await update.message.reply_text(
                "❌ *Error: Datos incompletos*\nPor favor, inicia el proceso de nuevo.",
                reply_markup=teclado_gestion_usuarios(),
                parse_mode="Markdown"
            )
            return GESTION_USUARIOS
        
        credenciales = cargar_credenciales()
        credenciales.append({"identificador": identificador, "rol": rol})
        guardar_credenciales(credenciales)
        await update.message.reply_text(
            f"✅ *Usuario `{identificador}` ({rol}) agregado correctamente*",
            reply_markup=teclado_gestion_usuarios(),
            parse_mode="Markdown"
        )
        logger.info(f"Usuario {identificador} ({rol}) agregado por {chat_id}")
        return GESTION_USUARIOS
    
    await update.message.reply_text(
        "❌ *Opción inválida*\nSelecciona *✅ Confirmar* o *❌ Cancelar*:",
        reply_markup=teclado_confirmar(),
        parse_mode="Markdown"
    )
    return CONFIRMAR_AGREGAR_USUARIO

async def recibir_identificador_eliminar_usuario(update: Update, context):
    """Recibe el identificador del usuario a eliminar."""
    chat_id = update.message.chat_id
    identificador = update.message.text.strip()
    logger.info(f"Identificador recibido para eliminar usuario por {chat_id}: {identificador}")
    
    credenciales = cargar_credenciales()
    if identificador == sesiones_activas[chat_id]["identificador"]:
        await update.message.reply_text(
            "❌ *No puedes eliminarte a ti mismo*\nIngresa otro identificador:",
            parse_mode="Markdown"
        )
        return ELIMINAR_USUARIO_ID
    
    usuario = next((cred for cred in credenciales if cred["identificador"] == identificador), None)
    if not usuario:
        mensaje = "*🗑 Eliminar Usuario*\n❌ *Identificador no encontrado*\n\n*Usuarios disponibles*:\n"
        for cred in credenciales:
            mensaje += f"- `{cred['identificador']}` ({cred.get('rol', 'user')})\n"
        mensaje += "\nIngresa un identificador válido.\nEjemplo: `clave123`\nUsa /cancel para salir."
        await update.message.reply_text(mensaje, parse_mode="Markdown")
        return ELIMINAR_USUARIO_ID
    
    context.user_data["eliminar_identificador"] = identificador
    context.user_data["eliminar_rol"] = usuario.get("rol", "user")
    await update.message.reply_text(
        f"*Confirmación*\n\nVas a eliminar:\n- *Identificador*: `{identificador}`\n- *Rol*: {usuario.get('rol', 'user')}\n\n¿Confirmas?",
        reply_markup=teclado_confirmar(),
        parse_mode="Markdown"
    )
    return CONFIRMAR_ELIMINAR_USUARIO

async def confirmar_eliminar_usuario(update: Update, context):
    """Confirma o cancela la eliminación de un usuario."""
    texto = update.message.text
    chat_id = update.message.chat_id
    logger.info(f"Confirmación de eliminar usuario recibida por {chat_id}: {texto}")
    
    if texto == "❌ Cancelar":
        await update.message.reply_text(
            "❌ *Operación cancelada*",
            reply_markup=teclado_gestion_usuarios(),
            parse_mode="Markdown"
        )
        logger.info(f"Eliminación de usuario cancelada por {chat_id}")
        return GESTION_USUARIOS
    
    if texto == "✅ Confirmar":
        identificador = context.user_data.get("eliminar_identificador")
        rol = context.user_data.get("eliminar_rol")
        if not identificador:
            await update.message.reply_text(
                "❌ *Error: Datos incompletos*\nPor favor, inicia el proceso de nuevo.",
                reply_markup=teclado_gestion_usuarios(),
                parse_mode="Markdown"
            )
            return GESTION_USUARIOS
        
        credenciales = cargar_credenciales()
        credenciales = [cred for cred in credenciales if cred["identificador"] != identificador]
        guardar_credenciales(credenciales)
        
        # Cerrar sesiones activas del usuario eliminado
        with data_lock:
            sesiones_a_cerrar = [sid for sid, sesion in sesiones_activas.items() if sesion["identificador"] == identificador]
            for sid in sesiones_a_cerrar:
                del sesiones_activas[sid]
                await enviar_mensaje(context.application, sid, f"⚠️ *Tu cuenta `{identificador}` ha sido eliminada por un administrador.*", parse_mode="Markdown")
                logger.info(f"Sesión {sid} cerrada tras eliminar usuario {identificador}")
        
        await update.message.reply_text(
            f"✅ *Usuario `{identificador}` ({rol}) eliminado correctamente*",
            reply_markup=teclado_gestion_usuarios(),
            parse_mode="Markdown"
        )
        logger.info(f"Usuario {identificador} ({rol}) eliminado por {chat_id}")
        return GESTION_USUARIOS
    
    await update.message.reply_text(
        "❌ *Opción inválida*\nSelecciona *✅ Confirmar* o *❌ Cancelar*:",
        reply_markup=teclado_confirmar(),
        parse_mode="Markdown"
    )
    return CONFIRMAR_ELIMINAR_USUARIO

# ================= HANDLERS DE MENSAJES =================
async def manejar_mensaje(update: Update, context):
    """Maneja los mensajes de texto recibidos."""
    global monitoreo_global
    texto = update.message.text
    chat_id = update.message.chat_id
    app = context.application
    logger.info(f"Mensaje recibido de chat_id {chat_id}: {texto}")
    
    if chat_id not in sesiones_activas:
        await update.message.reply_text("❌ *Usa /start para autenticarte*", parse_mode="Markdown")
        return

    es_admin = sesiones_activas[chat_id].get("rol") == "admin"
    executor = context.user_data.get("executor", ThreadPoolExecutor(max_workers=50))
    context.user_data["executor"] = executor

    if texto == "🟢 Iniciar todo":
        logger.info(f"Iniciando monitoreo global por {chat_id}")
        with data_lock:
            monitoreo_global = True
        for grupo in estados:
            with data_lock:
                if estados[grupo]["activo"]:
                    logger.info(f"Creando hilo de monitoreo para {grupo}")
                    threading.Thread(target=monitoreo_grupo_thread, args=(grupo, executor), daemon=True).start()
        await update.message.reply_text("✅ *Monitoreo iniciado*", parse_mode="Markdown")
        logger.info(f"Monitoreo global iniciado por {chat_id}")

    elif texto == "🔴 Detener todo":
        with data_lock:
            monitoreo_global = False
        await update.message.reply_text("🛑 *Monitoreo global DETENIDO*", parse_mode="Markdown")
        logger.info(f"Monitoreo global detenido por {chat_id}")

    elif texto == "📊 Estado general":
        mensaje = "*🌐 Estado del Bot*\n\n"
        with data_lock:
            for grupo in estados:
                activos = sum(1 for ip in estados[grupo]["estado_hosts"] if estados[grupo]["estado_hosts"][ip]["activo"])
                total = len(estados[grupo]["estado_hosts"])
                estado = "🟢 Activado" if estados[grupo]["activo"] else "🔴 Desactivado"
                mensaje += f"*{grupo.upper()}* ({estado})\n  - Hosts: {activos}/{total} activos\n"
        mensaje += f"\n*Monitoreo global*: {'🟢 Activado' if monitoreo_global else '🔴 Desactivado'}"
        await update.message.reply_text(mensaje, parse_mode="Markdown")
        logger.info(f"Estado general solicitado por {chat_id}")

    elif texto == "🟢 Hosts activos":
        mensaje = "*🟢 Hosts Activos*\n\n"
        with data_lock:
            for grupo in estados:
                mensaje += f"*{grupo.upper()}*\n"
                activos = [f"  `{name}` ({ip})" for ip, name in hosts[grupo].items() if estados[grupo]["estado_hosts"][ip]["activo"]]
                mensaje += "\n".join(activos) + "\n\n" if activos else "  - Ninguno\n\n"
        await update.message.reply_text(mensaje, parse_mode="Markdown")
        logger.info(f"Hosts activos solicitados por {chat_id}")

    elif texto == "🔴 Hosts inactivos":
        mensaje = "*🔴 Hosts Inactivos*\n\n"
        with data_lock:
            for grupo in estados:
                mensaje += f"*{grupo.upper()}*\n"
                inactivos = [f"  `{name}` ({ip})" for ip, name in hosts[grupo].items() if not estados[grupo]["estado_hosts"][ip]["activo"]]
                mensaje += "\n".join(inactivos) + "\n\n" if inactivos else "  - Ninguno\n\n"
        await update.message.reply_text(mensaje, parse_mode="Markdown")
        logger.info(f"Hosts inactivos solicitados por {chat_id}")

    elif texto == "📋 Listar sesiones":
        with data_lock:
            if not sesiones_activas:
                await update.message.reply_text("*📋 Sesiones*\n\nNinguna sesión activa", parse_mode="Markdown")
                return
            mensaje = "*📋 Sesiones Activas*\n\n"
            for sid, sesion in sesiones_activas.items():
                tiempo = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sesion["timestamp"]))
                rol = sesion.get("rol", "user")
                mensaje += f"*ID*: {sid}\n*Identificador*: `{sesion['identificador']}`\n*Rol*: {rol}\n*Conexión*: {tiempo}\n\n"
        await update.message.reply_text(mensaje, parse_mode="Markdown")
        logger.info(f"Sesiones activas solicitadas por {chat_id}")

    elif texto == "🛠 Gestión de Usuarios" and es_admin:
        await gestion_usuarios(update, context)
        return

    elif texto == "🛡️ Cerrar sesiones no admin" and es_admin:
        with data_lock:
            sesiones_a_cerrar = [sid for sid, sesion in sesiones_activas.items() if sesion.get("rol", "user") != "admin"]
            for sid in sesiones_a_cerrar:
                del sesiones_activas[sid]
                await enviar_mensaje(app, sid, "⚠️ *Tu sesión ha sido cerrada por un administrador.*", parse_mode="Markdown")
                logger.info(f"Sesión {sid} cerrada por admin {chat_id}")
        await update.message.reply_text(f"✅ *{len(sesiones_a_cerrar)} sesiones no admin cerradas*", parse_mode="Markdown")
        logger.info(f"{len(sesiones_a_cerrar)} sesiones no admin eliminadas por {chat_id}")

    elif texto == "⚙ Control por grupo":
        await update.message.reply_text("Selecciona grupo:", reply_markup=teclado_grupos(), parse_mode="Markdown")
        logger.info(f"Control por grupo solicitado por {chat_id}")

    elif texto in ["📷 CCTV", "💻 Servidores", "🔌 Switches", "🏢 Corporativo", "🚶 Torniquetes y Comedor", "🌐 IPs Públicas"]:
        grupo_map = {
            "📷 CCTV": "cctv",
            "💻 Servidores": "servers",
            "🔌 Switches": "switches",
            "🏢 Corporativo": "corporativo",
            "🚶 Torniquetes y Comedor": "torniquetes_comedor",
            "🌐 IPs Públicas": "ip_publicas",
        }
        grupo = grupo_map.get(texto, "").lower()
        if grupo in estados:
            with data_lock:
                estados[grupo]["activo"] = not estados[grupo]["activo"]
                estado = "ACTIVADO" if estados[grupo]["activo"] else "DESACTIVADO"
                if estados[grupo]["activo"] and monitoreo_global:
                    logger.info(f"Creando hilo de monitoreo para {grupo}")
                    threading.Thread(target=monitoreo_grupo_thread, args=(grupo, executor), daemon=True).start()
            await update.message.reply_text(f"✅ *Monitoreo de {grupo.upper()} {estado}*", parse_mode="Markdown")
            logger.info(f"Monitoreo de {grupo} {estado.lower()} por {chat_id}")

    elif texto == "🚪 Cerrar sesión":
        with data_lock:
            if chat_id in sesiones_activas:
                del sesiones_activas[chat_id]
        await update.message.reply_text("✅ *Sesión cerrada*", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        logger.info(f"Sesión cerrada por {chat_id}")

    elif texto == "🔑 Menú principal":
        await update.message.reply_text(
            "*Menú Principal:*",
            reply_markup=teclado_principal(es_admin),
            parse_mode="Markdown"
        )
        logger.info(f"Menú principal solicitado por {chat_id}")

# ================= CONVERSATION HANDLERS =================
async def agregar_host(update: Update, context):
    """Inicia el flujo para agregar un host."""
    chat_id = update.message.chat_id
    if chat_id not in sesiones_activas:
        await update.message.reply_text("❌ *Usa /start para autenticarte*", parse_mode="Markdown")
        return ConversationHandler.END
    logger.info(f"Botón 'Agregar Host' presionado por {chat_id}")
    await update.message.reply_text(
        "➕ *Agregar Host*\nSelecciona un grupo para continuar:\n(e.g., CCTV, Servidores)\n\n"
        "Usa /cancel o '🔑 Menú principal' para salir.\n\n*Instrucciones completas*:\n"
        "1. Selecciona un grupo.\n2. Ingresa la IP (e.g., `172.168.2.67`).\n"
        "3. Ingresa el nombre (e.g., `Salida Desarrollo`).",
        reply_markup=teclado_grupos(),
        parse_mode="Markdown"
    )
    return GRUPO

async def recibir_grupo(update: Update, context):
    """Recibe el grupo seleccionado para agregar un host."""
    texto = update.message.text.lower().replace("📷 ", "").replace("💻 ", "").replace("🔌 ", "").replace("🏢 ", "").replace("🚶 ", "").replace("🌐 ", "").strip()
    chat_id = update.message.chat_id
    logger.info(f"Grupo recibido por {chat_id}: {texto}")
    grupo_map = {
        "cctv": "cctv",
        "servidores": "servers",
        "switches": "switches",
        "corporativo": "corporativo",
        "torniquetes y comedor": "torniquetes_comedor",
        "ips públicas": "ip_publicas",
    }
    if texto == "menú principal":
        es_admin = sesiones_activas.get(chat_id, {}).get("rol") == "admin"
        await update.message.reply_text(
            "*Menú Principal:*",
            reply_markup=teclado_principal(es_admin),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    if texto not in grupo_map:
        await update.message.reply_text(
            "❌ *Grupo inválido*\nSelecciona un grupo válido (CCTV, Servidores, Switches, Corporativo, Torniquetes y Comedor, IPs Públicas):",
            reply_markup=teclado_grupos(),
            parse_mode="Markdown"
        )
        return GRUPO
    context.user_data["grupo"] = grupo_map[texto]
    await update.message.reply_text(
        f"📌 *Ingresa la IP para {texto.upper()}*\nEjemplo: `172.168.2.67` o `2001:db8::1`\nUsa /cancel para salir.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    return IP

async def recibir_ip(update: Update, context):
    """Recibe y valida la IP del host."""
    ip = update.message.text.strip()
    grupo = context.user_data.get("grupo")
    chat_id = update.message.chat_id
    logger.info(f"IP recibida por {chat_id} para {grupo}: {ip}")
    try:
        ip_addr = ipaddress.ip_address(ip)
        if ip in hosts[grupo]:
            await update.message.reply_text(
                f"❌ *IP `{ip}` ya existe en {grupo.upper()}*\nIngresa una IP diferente.\nEjemplo: `172.168.2.67`",
                parse_mode="Markdown"
            )
            return IP
        context.user_data["ip"] = str(ip_addr)
        await update.message.reply_text(
            f"📌 *Ingresa el nombre para `{ip}`*\nMáx. 50 caracteres. Ejemplo: `Salida Desarrollo`\nUsa /cancel para salir.",
            parse_mode="Markdown"
        )
        return NOMBRE
    except ValueError:
        await update.message.reply_text(
            "❌ *IP inválida*\nIngresa una IP válida (e.g., `172.168.2.67` o `2001:db8::1`):",
            parse_mode="Markdown"
        )
        return IP

async def recibir_nombre(update: Update, context):
    """Recibe el nombre del host y pide confirmación."""
    nombre = update.message.text.strip()
    chat_id = update.message.chat_id
    logger.info(f"Nombre recibido por {chat_id}: {nombre}")
    if not nombre or len(nombre) > 50:
        await update.message.reply_text(
            "❌ *Nombre inválido*\nIngresa un nombre entre 1 y 50 caracteres.\nEjemplo: `Salida Desarrollo`",
            parse_mode="Markdown"
        )
        return NOMBRE
    context.user_data["nombre"] = nombre
    grupo = context.user_data.get("grupo")
    ip = context.user_data.get("ip")
    await update.message.reply_text(
        f"*Confirmación*\n\nVas a agregar:\n- *Grupo*: {grupo.upper()}\n- *IP*: `{ip}`\n- *Nombre*: `{nombre}`\n\n¿Confirmas?",
        reply_markup=teclado_confirmar(),
        parse_mode="Markdown"
    )
    return CONFIRMAR_AGREGAR

async def confirmar_agregar(update: Update, context):
    """Confirma o cancela la adición del host."""
    texto = update.message.text
    chat_id = update.message.chat_id
    logger.info(f"Confirmación de agregar recibida por {chat_id}: {texto}")
    
    es_admin = sesiones_activas.get(chat_id, {}).get("rol") == "admin"
    if texto == "❌ Cancelar":
        logger.info(f"Adición de host cancelada por {chat_id}")
        await update.message.reply_text(
            "❌ *Operación cancelada*",
            reply_markup=teclado_principal(es_admin),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    if texto == "✅ Confirmar":
        grupo = context.user_data.get("grupo")
        ip = context.user_data.get("ip")
        nombre = context.user_data.get("nombre")
        
        if not all([grupo, ip, nombre]):
            logger.error(f"Datos incompletos en confirmar_agregar por {chat_id}: grupo={grupo}, ip={ip}, nombre={nombre}")
            await update.message.reply_text(
                "❌ *Error: Datos incompletos*\nPor favor, inicia el proceso de nuevo.",
                reply_markup=teclado_principal(es_admin),
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        
        with data_lock:
            hosts[grupo][ip] = nombre
            estados[grupo]["estado_hosts"][ip] = {"activo": True, "fallos": 0, "ultima_alerta": 0}
        await asyncio.to_thread(guardar_hosts)
        
        await update.message.reply_text(
            f"✅ *Host `{nombre}` ({ip}) agregado a {grupo.upper()}*",
            reply_markup=teclado_principal(es_admin),
            parse_mode="Markdown"
        )
        logger.info(f"Host {nombre} ({ip}) agregado a {grupo} por {chat_id}")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "❌ *Opción inválida*\nSelecciona *✅ Confirmar* o *❌ Cancelar*:",
        reply_markup=teclado_confirmar(),
        parse_mode="Markdown"
    )
    return CONFIRMAR_AGREGAR

async def eliminar_host(update: Update, context):
    """Inicia el flujo para eliminar un host."""
    chat_id = update.message.chat_id
    if chat_id not in sesiones_activas:
        await update.message.reply_text("❌ *Usa /start para autenticarte*", parse_mode="Markdown")
        return ConversationHandler.END
    logger.info(f"Botón 'Eliminar Host' presionado por {chat_id}")
    await update.message.reply_text(
        "🗑 *Eliminar Host*\nSelecciona un grupo para continuar:\n(e.g., CCTV, Servidores)\n\n"
        "Usa /cancel o '🔑 Menú principal' para salir.\n\n*Instrucciones completas*:\n"
        "1. Selecciona un grupo.\n2. Ingresa la IP a eliminar (e.g., `172.168.2.67`).",
        reply_markup=teclado_grupos(),
        parse_mode="Markdown"
    )
    return ELIMINAR_GRUPO

async def recibir_grupo_eliminar(update: Update, context):
    """Recibe el grupo para eliminar un host."""
    texto = update.message.text.lower().replace("📷 ", "").replace("💻 ", "").replace("🔌 ", "").replace("🏢 ", "").replace("🚶 ", "").replace("🌐 ", "").strip()
    chat_id = update.message.chat_id
    logger.info(f"Grupo recibido para eliminar por {chat_id}: {texto}")
    grupo_map = {
        "cctv": "cctv",
        "servidores": "servers",
        "switches": "switches",
        "corporativo": "corporativo",
        "torniquetes y comedor": "torniquetes_comedor",
        "ips públicas": "ip_publicas",
    }
    if texto == "menú principal":
        es_admin = sesiones_activas.get(chat_id, {}).get("rol") == "admin"
        await update.message.reply_text(
            "*Menú Principal:*",
            reply_markup=teclado_principal(es_admin),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    if texto not in grupo_map:
        await update.message.reply_text(
            "❌ *Grupo inválido*\nSelecciona un grupo válido (CCTV, Servidores, Switches, Corporativo, Torniquetes y Comedor, IPs Públicas):",
            reply_markup=teclado_grupos(),
            parse_mode="Markdown"
        )
        return ELIMINAR_GRUPO
    context.user_data["grupo"] = grupo_map[texto]
    mensaje = f"📌 *Ingresa la IP para eliminar de {texto.upper()}*\n\n*Hosts disponibles*:\n"
    with data_lock:
        if not hosts[grupo_map[texto]]:
            await update.message.reply_text(
                f"❌ *No hay hosts en {texto.upper()}*\nSelecciona otro grupo:",
                reply_markup=teclado_grupos(),
                parse_mode="Markdown"
            )
            return ELIMINAR_GRUPO
        for ip, nombre in hosts[grupo_map[texto]].items():
            mensaje += f"- `{ip}` ({nombre})\n"
    mensaje += "\nEjemplo: `172.168.2.67`\nUsa /cancel para salir."
    await update.message.reply_text(mensaje, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    return ELIMINAR_IP

async def recibir_ip_eliminar(update: Update, context):
    """Recibe la IP del host y pide confirmación."""
    ip = update.message.text.strip()
    grupo = context.user_data.get("grupo")
    chat_id = update.message.chat_id
    logger.info(f"IP recibida para eliminar por {chat_id} en {grupo}: {ip}")
    with data_lock:
        if ip not in hosts[grupo]:
            mensaje = f"❌ *IP `{ip}` no encontrada en {grupo.upper()}*\n\n*Hosts disponibles*:\n"
            for host_ip, nombre in hosts[grupo].items():
                mensaje += f"- `{host_ip}` ({nombre})\n"
            mensaje += "\nIngresa una IP válida.\nEjemplo: `172.168.2.67`\nUsa /cancel para salir."
            await update.message.reply_text(mensaje, parse_mode="Markdown")
            return ELIMINAR_IP
        nombre = hosts[grupo][ip]
    context.user_data["ip"] = ip
    context.user_data["nombre"] = nombre
    await update.message.reply_text(
        f"*Confirmación*\n\nVas a eliminar:\n- *Grupo*: {grupo.upper()}\n- *IP*: `{ip}`\n- *Nombre*: `{nombre}`\n\n¿Confirmas?",
        reply_markup=teclado_confirmar(),
        parse_mode="Markdown"
    )
    return CONFIRMAR_ELIMINAR

async def confirmar_eliminar(update: Update, context):
    """Confirma o cancela la eliminación del host."""
    texto = update.message.text
    chat_id = update.message.chat_id
    logger.info(f"Confirmación de eliminar recibida por {chat_id}: {texto}")
    
    es_admin = sesiones_activas.get(chat_id, {}).get("rol") == "admin"
    if texto == "❌ Cancelar":
        logger.info(f"Eliminación de host cancelada por {chat_id}")
        await update.message.reply_text(
            "❌ *Operación cancelada*",
            reply_markup=teclado_principal(es_admin),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    if texto == "✅ Confirmar":
        grupo = context.user_data.get("grupo")
        ip = context.user_data.get("ip")
        nombre = context.user_data.get("nombre")
        
        if not all([grupo, ip, nombre]):
            logger.error(f"Datos incompletos en confirmar_eliminar por {chat_id}: grupo={grupo}, ip={ip}, nombre={nombre}")
            await update.message.reply_text(
                "❌ *Error: Datos incompletos*\nPor favor, inicia el proceso de nuevo.",
                reply_markup=teclado_principal(es_admin),
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        
        with data_lock:
            if ip in hosts[grupo]:
                del hosts[grupo][ip]
                del estados[grupo]["estado_hosts"][ip]
            else:
                logger.warning(f"IP {ip} no encontrada en {grupo} al intentar eliminar por {chat_id}")
                await update.message.reply_text(
                    f"❌ *Error: IP `{ip}` no encontrada en {grupo.upper()}*",
                    reply_markup=teclado_principal(es_admin),
                    parse_mode="Markdown"
                )
                return ConversationHandler.END
        await asyncio.to_thread(guardar_hosts)
        
        await update.message.reply_text(
            f"✅ *Host `{nombre}` ({ip}) eliminado de {grupo.upper()}*",
            reply_markup=teclado_principal(es_admin),
            parse_mode="Markdown"
        )
        logger.info(f"Host {nombre} ({ip}) eliminado de {grupo} por {chat_id}")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "❌ *Opción inválida*\nSelecciona *✅ Confirmar* o *❌ Cancelar*:",
        reply_markup=teclado_confirmar(),
        parse_mode="Markdown"
    )
    return CONFIRMAR_ELIMINAR

async def cancelar(update: Update, context):
    """Cancela una operación de conversación."""
    chat_id = update.message.chat_id
    es_admin = sesiones_activas.get(chat_id, {}).get("rol") == "admin"
    await update.message.reply_text(
        "❌ *Operación cancelada*",
        reply_markup=teclado_principal(es_admin),
        parse_mode="Markdown"
    )
    logger.info(f"Operación cancelada por {chat_id}")
    return ConversationHandler.END

# ================= INICIALIZACIÓN =================
async def main():
    """Función principal para iniciar el bot."""
    logger.info("Iniciando bot...")
    cargar_hosts()
    
    app = Application.builder().token(TOKEN).build()
    
    # Handler para login
    login_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, verificar_identificador)],
        },
        fallbacks=[CommandHandler("cancel", cancelar_login)],
    )
    
    # Handlers para agregar host
    conv_handler_agregar = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"➕ Agregar Host"), agregar_host)],
        states={
            GRUPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_grupo)],
            IP: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_ip)],
            NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_nombre)],
            CONFIRMAR_AGREGAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_agregar)],
        },
        fallbacks=[CommandHandler("cancel", cancelar)],
    )
    
    # Handlers para eliminar host
    conv_handler_eliminar = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"🗑 Eliminar Host"), eliminar_host)],
        states={
            ELIMINAR_GRUPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_grupo_eliminar)],
            ELIMINAR_IP: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_ip_eliminar)],
            CONFIRMAR_ELIMINAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_eliminar)],
        },
        fallbacks=[CommandHandler("cancel", cancelar)],
    )
    
    # Handlers para gestión de usuarios
    conv_handler_usuarios = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"🛠 Gestión de Usuarios"), gestion_usuarios)],
        states={
            GESTION_USUARIOS: [MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_gestion_usuarios)],
            AGREGAR_USUARIO_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_identificador_usuario)],
            AGREGAR_USUARIO_ROL: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_rol_usuario)],
            CONFIRMAR_AGREGAR_USUARIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_agregar_usuario)],
            ELIMINAR_USUARIO_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_identificador_eliminar_usuario)],
            CONFIRMAR_ELIMINAR_USUARIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_eliminar_usuario)],
        },
        fallbacks=[CommandHandler("cancel", cancelar)],
    )
    
    # Registrar handlers
    app.add_handler(login_handler)
    app.add_handler(conv_handler_agregar)
    app.add_handler(conv_handler_eliminar)
    app.add_handler(conv_handler_usuarios)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_mensaje))
    
    logger.info("Bot iniciado correctamente, comenzando polling...")
    
    # Iniciar tarea para procesar alertas
    asyncio.create_task(procesar_alertas(app))
    
    # Iniciar el bot
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    try:
        while True:
            await asyncio.sleep(30)
    except KeyboardInterrupt:
        logger.info("Deteniendo bot...")
        with data_lock:
            monitoreo_global = False
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logger.info("Bot detenido correctamente")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except Exception as e:
        logger.error(f"Error en la ejecución del bot: {e}", exc_info=True)
    finally:
        loop.close()