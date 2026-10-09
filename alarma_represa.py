import csv
import io
import math
import struct
import time
import wave
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Simulador de Alarma Hidrostática",
    page_icon="🌧️",
    layout="wide")

st.markdown("""
<style>
.stApp{ background-color: #F4F7F6; }

[data-testid="stSidebar"] { background-color: #1E293B !important; }
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label { color: #F4F7F6 !important; }

h1, h2, h3{ color: #1E293B; font-weight: bold !important; }
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #FFFFFF !important;
    font-weight: bold !important;
}

[data-testid="stMetric"],
[data-testid="metric-container"],
.stMetric {
    background-color: #E2E8F0 !important;
    border: 1px solid #CBD5E1 !important;
    border-radius: 10px !important;
    padding: 15px !important;
    box-sizing: border-box !important;
}

/* Elementos internos */
[data-testid="stMetric"] > div,
[data-testid="metric-container"] > div {
    background-color: transparent !important;
}

/* Texto de los indicadores */
[data-testid="stMetricLabel"],
[data-testid="stMetricValue"],
[data-testid="stMetricDelta"] {
    color: #0F172A !important;
}
.stButton button,
.stButton button[kind="secondary"],
.stButton button[kind="primary"] {
    background: #455B8A !important;
    color: #FFFFFF !important;
}

.alarma-critica {
    background: #B91C1C; color: #FFFFFF; padding: 14px; border-radius: 10px;
    font-weight: bold; font-size: 1.2rem; text-align: center;
    animation: parpadeo 1s infinite;
}
@keyframes parpadeo { 50% { opacity: 0.35; } }


/* Tablas: texto oscuro y fondo claro */
[data-testid="stTable"] table {
    background-color: #FFFFFF !important;
    color: #1E293B !important;
    border-collapse: collapse !important;
}

/* Encabezados de las tablas */
[data-testid="stTable"] th {
    background-color: #DCEAF7 !important;
    color: #0F172A !important;
    font-weight: bold !important;
}

/* Celdas */
[data-testid="stTable"] td {
    background-color: #FFFFFF !important;
    color: #1E293B !important;
    border-bottom: 1px solid #CBD5E1 !important;
}

/* Filas alternadas para facilitar la lectura */
[data-testid="stTable"] tbody tr:nth-child(even) td {
    background-color: #F4F7F6 !important;
}
</style>
""", unsafe_allow_html=True)

st.title("🌊 Simulador de Alarma Hidrostática")
st.subheader("Prevención de desbordes en represas frente al Fenómeno El Niño")
st.markdown("---")

DENSIDAD_AGUA = 1000.0        # kg/m³
GRAVEDAD = 9.81               # m/s²
VELOCIDAD_SONIDO = 343.0      # m/s (aire a 20 °C)
TIEMPO_PASO = 60              # s: cada actualización = 1 minuto simulado

ALTURA_MAXIMA = 40.0          # m
H_ALERTA = 30.0               # m  (precaución: 30-35 m)
H_CRITICO = 35.0              # m  (peligro: 35-40 m)
DHDT_MAX_INICIAL = 0.50       # m/min
ANCHO_MURO_INICIAL = 30.0     # m (supuesto, editable en el sidebar)

ETIQUETAS_ESTADO = {
    "normal": "🟢 NORMAL",
    "caution": "🟠 PRECAUCIÓN",
    "danger": "🔴 PELIGRO CRÍTICO",
}

def presion_pa(h):
    """P = ρ·g·h (presión manométrica en el fondo, en Pa)."""
    return DENSIDAD_AGUA * GRAVEDAD * h

def fuerza_por_metro_n(h):
    """F/b = ½·ρ·g·h² (N por metro de ancho del muro)."""
    return 0.5 * DENSIDAD_AGUA * GRAVEDAD * h * h

def fuerza_hidrostatica_n(h, ancho):
    """F = ½·ρ·g·h²·b (fuerza total sobre el muro, en N)."""
    return fuerza_por_metro_n(h) * ancho

def calcular_dhdt(balance_m3s, area_embalse):
    """dh/dt (m/min) = (Q_ent − Q_sal) / A · 60."""
    return balance_m3s / area_embalse * 60

def paso_nivel(nivel, balance_m3s, area_embalse, dt=TIEMPO_PASO):
    """h' = h + (Q_ent − Q_sal)·Δt / A, acotado entre 0 y H."""
    nuevo = nivel + balance_m3s * dt / area_embalse
    return max(0.0, min(nuevo, ALTURA_MAXIMA))

def clasificar_estado(nivel, dhdt, dhdt_max):
    """Igual que la sección 4.1 de la monografía.

    Peligro:    h >= h_crítico  O  dh/dt > Δh_máx
    Precaución: h_alerta <= h < h_crítico
    Normal:     el resto (P < P_alerta equivale a h < h_alerta)
    """
    if nivel >= H_CRITICO or dhdt > dhdt_max:
        return "danger"
    if nivel >= H_ALERTA:
        return "caution"
    return "normal"


def construir_fila(t, nivel, q_ent, q_sal, dhdt, clase, ancho_muro,
                   compuertas_abiertas):
    """Una fila del historial / CSV."""
    return {
        "Tiempo (min)": t,
        "Nivel h (m)": round(nivel, 2),
        "Distancia d (m)": round(ALTURA_MAXIMA - nivel, 2),
        "Q entrada (m³/s)": round(q_ent, 2),
        "Q salida (m³/s)": round(q_sal, 2),
        "Presión P (Pa)": round(presion_pa(nivel), 2),
        "Presión (kPa)": round(presion_pa(nivel) / 1000, 2),
        "Fuerza total (MN)": round(
            fuerza_hidrostatica_n(nivel, ancho_muro) / 1e6, 3),
        "dh/dt (m/min)": round(dhdt, 4),
        "Llenado (%)": round(nivel / ALTURA_MAXIMA * 100, 2),
        "Estado": ETIQUETAS_ESTADO[clase],
        "Compuertas": "Abiertas" if compuertas_abiertas else "Cerradas",
    }


def generar_sirena_wav(duracion=2.0, fs=22050):
    """Sirena de barrido 550-1050 Hz en formato WAV (bytes), hecha por código."""
    n = int(duracion * fs)
    fase = 0.0
    datos = bytearray()
    for i in range(n):
        frecuencia = 800 + 250 * math.sin(2 * math.pi * (i / fs) / (duracion / 2))
        fase += 2 * math.pi * frecuencia / fs
        datos += struct.pack("<h", int(0.6 * 32767 * math.sin(fase)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(bytes(datos))
    return buffer.getvalue()

P_ALERTA_KPA = DENSIDAD_AGUA * GRAVEDAD * H_ALERTA / 1000
P_CRITICA_KPA = DENSIDAD_AGUA * GRAVEDAD * H_CRITICO / 1000

PAUSA_VISUAL = 0.5

# MEMORIA DE LA SIMULACIÓN
valores_iniciales = {
    "nivel_agua": 15.0,
    "simulando_llenado": False,
    "compuertas_abiertas": False,
    "minutos_simulados": 0,
    "historial": [],
    "bitacora": [],
    "ultimo_estado": None,
    "clase_previa": None,
    "mensaje_fin": None,
}
for clave, valor in valores_iniciales.items():
    if clave not in st.session_state:
        st.session_state[clave] = valor


@st.cache_resource
def obtener_sirena():
    return generar_sirena_wav()

def iniciar_simulacion():
    st.session_state.simulando_llenado = True
    st.session_state.mensaje_fin = None

def pausar_simulacion():
    st.session_state.simulando_llenado = False

def alternar_compuertas():
    st.session_state.compuertas_abiertas = not st.session_state.compuertas_abiertas

def aplicar_nivel_inicial():
    st.session_state.nivel_agua = st.session_state.nivel_inicial_slider

def reiniciar_simulacion():
    st.session_state.nivel_agua = st.session_state.nivel_inicial_slider
    st.session_state.simulando_llenado = False
    st.session_state.compuertas_abiertas = False
    st.session_state.minutos_simulados = 0
    st.session_state.historial = []
    st.session_state.bitacora = []
    st.session_state.ultimo_estado = None
    st.session_state.clase_previa = None
    st.session_state.mensaje_fin = None


st.sidebar.header("⚙️ Configuración de la Represa")
st.sidebar.caption(
    f"Altura total del recipiente H = {ALTURA_MAXIMA:.0f} m "
    f"(modelo de referencia)"
)


st.sidebar.slider(
    "Nivel inicial del agua h (m):",
    min_value=0.0,
    max_value=ALTURA_MAXIMA,
    value=15.0,
    step=0.5,
    key="nivel_inicial_slider",
    on_change=aplicar_nivel_inicial,
    disabled=st.session_state.simulando_llenado,
)

area_embalse = st.sidebar.number_input(
    "Área superficial del embalse (m²):",
    min_value=1000.0, max_value=10000000.0,
    value=100000.0, step=10000.0
)

ancho_muro = st.sidebar.number_input(
    "Ancho del muro de contención b (m):",
    min_value=1.0, max_value=2000.0, value=ANCHO_MURO_INICIAL, step=5.0
)

st.sidebar.markdown("---")
st.sidebar.subheader("💧 Caudal de ingreso")


area_entrada = st.sidebar.number_input(
    "Área de entrada (m²):",
    min_value=0.1, max_value=10000.0, value=100.0, step=1.0
)
velocidad_entrada = st.sidebar.number_input(
    "Velocidad de entrada (m/s):",
    min_value=0.0, max_value=100.0, value=4.0, step=0.5
)
q_ingreso_normal = area_entrada * velocidad_entrada
st.sidebar.caption(
    f"Q = A·v = {area_entrada:.1f} · {velocidad_entrada:.1f} "
    f"= {q_ingreso_normal:.1f} m³/s"
)

st.sidebar.markdown("---")
st.sidebar.subheader("🌧️ Fenómeno El Niño")

lluvia = st.sidebar.checkbox("Activar lluvias intensas", value=False)
area_lluvia = st.sidebar.number_input(
    "Área adicional por lluvia (m²):",
    min_value=0.0, max_value=10000.0, value=100.0, step=1.0
)
velocidad_lluvia = st.sidebar.number_input(
    "Velocidad del agua por lluvias (m/s):",
    min_value=0.0, max_value=100.0, value=4.0, step=0.5
)
q_lluvia = area_lluvia * velocidad_lluvia
if lluvia:
    st.sidebar.caption(
        f"Q lluvia = A·v = {area_lluvia:.1f} · {velocidad_lluvia:.1f} "
        f"= {q_lluvia:.1f} m³/s"
    )
else:
    st.sidebar.caption("Q lluvia = 0 m³/s")

st.sidebar.markdown("---")
st.sidebar.subheader("🚪 Compuertas")

area_compuerta = st.sidebar.number_input(
    "Área de la compuerta (m²):",
    min_value=0.1, max_value=10000.0, value=40.0, step=1.0
)
velocidad_salida_normal = st.sidebar.number_input(
    "Velocidad normal de salida (m/s):",
    min_value=0.0, max_value=100.0, value=10.0, step=0.5
)
velocidad_desfogue = st.sidebar.number_input(
    "Velocidad de desfogue (m/s):",
    min_value=0.0, max_value=100.0, value=25.0, step=0.5
)

apertura_automatica = st.sidebar.checkbox(
    "Apertura automática de compuertas en PELIGRO", value=True
)
if (
    apertura_automatica
    and st.session_state.simulando_llenado
    and st.session_state.clase_previa == "danger"
):
    st.session_state.compuertas_abiertas = True

velocidad_salida_actual = (
    velocidad_desfogue
    if st.session_state.compuertas_abiertas
    else velocidad_salida_normal
)
q_salida = area_compuerta * velocidad_salida_actual
st.sidebar.caption(
    f"Q salida = A·v = {area_compuerta:.1f} · {velocidad_salida_actual:.1f} "
    f"= {q_salida:.1f} m³/s"
)

st.sidebar.markdown("---")
st.sidebar.subheader("🚨 Parámetros de alarma")
dhdt_max = st.sidebar.number_input(
    "Δh máximo permitido (m/min):",
    min_value=0.01, max_value=10.0, value=DHDT_MAX_INICIAL, step=0.05
)

sonido_activado = st.sidebar.checkbox("🔊 Sirena automática en PELIGRO", value=True)

st.sidebar.markdown("---")
st.sidebar.subheader("🎮 Control")

col1, col2 = st.sidebar.columns(2)
with col1:
    st.button("▶️ Iniciar", use_container_width=True, on_click=iniciar_simulacion)
with col2:
    st.button("⏸️ Pausar", use_container_width=True, on_click=pausar_simulacion)

st.sidebar.button(
    "🔒 Cerrar compuertas"
    if st.session_state.compuertas_abiertas
    else "🚪 Abrir compuertas",
    use_container_width=True,
    on_click=alternar_compuertas
)
st.sidebar.button(
    "🔄 Reiniciar", use_container_width=True, on_click=reiniciar_simulacion
)

# BALANCE DE CAUDALES
q_entrada_total = q_ingreso_normal + (q_lluvia if lluvia else 0.0)
balance_caudal = q_entrada_total - q_salida
dhdt_m_min = calcular_dhdt(balance_caudal, area_embalse)

avanzo = False
if st.session_state.simulando_llenado:
    nivel_previo = st.session_state.nivel_agua
    en_limite = (
        (nivel_previo >= ALTURA_MAXIMA and balance_caudal >= 0)
        or (nivel_previo <= 0 and balance_caudal <= 0)
    )
    if en_limite:
        st.session_state.simulando_llenado = False
    else:
        st.session_state.nivel_agua = paso_nivel(
            nivel_previo, balance_caudal, area_embalse
        )
        st.session_state.minutos_simulados += 1
        avanzo = True
        if st.session_state.nivel_agua >= ALTURA_MAXIMA:
            st.session_state.simulando_llenado = False
            st.session_state.mensaje_fin = "desborde"
        elif st.session_state.nivel_agua <= 0:
            st.session_state.simulando_llenado = False
            st.session_state.mensaje_fin = "vaciado"

# SENSORES
nivel_actual = st.session_state.nivel_agua
porcentaje_llenado = nivel_actual / ALTURA_MAXIMA * 100

# Sensor de presión: P = ρ·g·h  (presión manométrica en el fondo)
presion_pascales = DENSIDAD_AGUA * GRAVEDAD * nivel_actual
presion_kPa = presion_pascales / 1000
nivel_por_presion = presion_pascales / (DENSIDAD_AGUA * GRAVEDAD)

# Sensor ultrasónico: t = 2d/v, d = H - h
distancia_real = ALTURA_MAXIMA - nivel_actual
tiempo_eco = 2 * distancia_real / VELOCIDAD_SONIDO
nivel_por_ultrasonido = nivel_actual
distancia_sensor = ALTURA_MAXIMA - nivel_por_ultrasonido

nivel_alarma = max(nivel_por_ultrasonido, nivel_por_presion)
discrepancia_sensores = abs(nivel_por_ultrasonido - nivel_por_presion) > 1.0

# ESTADO DE LA ALARMA
clase_estado = clasificar_estado(nivel_alarma, dhdt_m_min, dhdt_max)
estado = ETIQUETAS_ESTADO[clase_estado]

if clase_estado == "danger":
    if nivel_alarma >= ALTURA_MAXIMA:
        recomendacion = (
            "Nivel máximo alcanzado: riesgo de desborde. Activar la alarma, "
            "abrir compuertas y evacuar zonas aguas abajo."
        )
    else:
        recomendacion = (
            "Activar la alarma y revisar inmediatamente el desfogue."
        )
elif clase_estado == "caution":
    recomendacion = (
        "Aumentar el monitoreo y preparar la apertura de las compuertas."
    )
else:
    recomendacion = "Continuar con el monitoreo del embalse."

if nivel_alarma >= H_CRITICO:
    texto_tiempo_critico = "Ya alcanzado"
elif dhdt_m_min > 0:
    minutos_a_critico = (H_CRITICO - nivel_alarma) / dhdt_m_min
    texto_tiempo_critico = f"{minutos_a_critico:.0f} min"
else:
    texto_tiempo_critico = "No sube"

muestra = construir_fila(
    st.session_state.minutos_simulados, nivel_actual, q_entrada_total,
    q_salida, dhdt_m_min, clase_estado, ancho_muro,
    st.session_state.compuertas_abiertas,
)
if not st.session_state.historial or avanzo:
    st.session_state.historial.append(muestra)

if (
    st.session_state.ultimo_estado is None
    or (avanzo and estado != st.session_state.ultimo_estado)
):
    st.session_state.bitacora.append({
        "Tiempo (min)": st.session_state.minutos_simulados,
        "Estado": estado,
        "Nivel h (m)": round(nivel_actual, 2),
        "Presión (kPa)": round(presion_kPa, 2),
    })
    st.session_state.ultimo_estado = estado

st.session_state.clase_previa = clase_estado

if (
    avanzo
    and apertura_automatica
    and st.session_state.compuertas_abiertas
    and clase_estado == "normal"
    and dhdt_m_min < 0
):
    st.session_state.compuertas_abiertas = False
    st.session_state.simulando_llenado = False
    st.session_state.mensaje_fin = "seguro"
    st.rerun()

# PANEL DE MONITOREO
st.header("📊 Panel de monitoreo")
col01, col02, col03, col04 = st.columns(4)
with col01:
    st.metric("Nivel del agua", f"{nivel_actual:.2f} m")
with col02:
    st.metric("Distancia d", f"{distancia_sensor:.2f} m")
with col03:
    st.metric("Presión hidrostática", f"{presion_kPa:.2f} kPa")
with col04:
    st.metric("dh/dt", f"{dhdt_m_min:.3f} m/min")

col5, col6, col7, col8 = st.columns(4)
with col5:
    st.metric("Capacidad", f"{porcentaje_llenado:.1f} %")
with col6:
    st.metric("Q entrada", f"{q_entrada_total:.1f} m³/s")
with col7:
    st.metric("Q salida", f"{q_salida:.1f} m³/s")
with col8:
    st.metric("Tiempo hasta nivel crítico", texto_tiempo_critico)

col9, col10 = st.columns(2)
with col9:
    st.metric(
        "Fuerza total sobre el muro",
        f"{fuerza_hidrostatica_n(nivel_actual, ancho_muro) / 1e6:.2f} MN"
    )
with col10:
    st.metric(
        "Fuerza por metro de ancho",
        f"{fuerza_por_metro_n(nivel_actual) / 1000:.0f} kN/m"
    )

st.progress(min(100, max(0, int(porcentaje_llenado))))
st.caption(f"Tiempo simulado: {st.session_state.minutos_simulados} min")

st.caption(
    f"Umbrales — Precaución: {H_ALERTA:.0f} m ({P_ALERTA_KPA:.1f} kPa) · "
    f"Crítico: {H_CRITICO:.0f} m ({P_CRITICA_KPA:.1f} kPa) · "
    f"Presión manométrica en el fondo (P = ρ·g·h)"
)

st.markdown("---")
st.subheader("📢 Estado automático del sistema")

if clase_estado == "normal":
    st.success(f"{estado}: {recomendacion}")
elif clase_estado == "caution":
    st.warning(f"{estado}: {recomendacion}")
else:
    st.error(f"🚨 {estado}: {recomendacion}")

if clase_estado == "danger":
    st.markdown(
        f'<div class="alarma-critica">🚨 ALARMA ACTIVA: {estado} — '
        f'nivel {nivel_alarma:.2f} m, dh/dt {dhdt_m_min:.3f} m/min</div>',
        unsafe_allow_html=True
    )
    if sonido_activado:
        ruta_audio = Path(__file__).parent / "alarma.mp3"
               if ruta_audio.is_file():
            st.audio(str(ruta_audio), format="audio/mp3", autoplay=True, loop=True)
    else:
            st.audio(obtener_sirena(), format="audio/wav", autoplay=True, loop=True)

        
        

if st.session_state.mensaje_fin == "seguro":
    st.success(
        "✅ Condiciones seguras recuperadas: el desfogue bajó el nivel por "
        "debajo del umbral de precaución. Compuertas cerradas."
    )
elif st.session_state.mensaje_fin == "desborde":
    st.error(
        "⛔ Se alcanzó el nivel máximo (40 m) del modelo: desborde. "
        "El desfogue no fue suficiente para contener el ingreso."
    )
elif st.session_state.mensaje_fin == "vaciado":
    st.info("El embalse llegó a 0 m: simulación detenida.")

if lluvia:
    st.info(
        f"🌧️ Lluvias activas: se agregan {q_lluvia:.1f} m³/s al ingreso."
    )
if st.session_state.compuertas_abiertas:
    st.info(
        "🚪 Compuertas abiertas: el sistema está utilizando el caudal "
        "de desfogue."
    )

# VISTA 3D
with st.expander("📋 Tabla de referencia: nivel – presión – fuerza – estado"):
    filas_ref = []
    for h_ref in (5, 10, 15, 20, 25, 30, 35, 40):
        filas_ref.append({
            "Nivel h (m)": h_ref,
            "Presión (kPa)": round(presion_pa(h_ref) / 1000, 2),
            "Fuerza por metro (kN/m)": round(fuerza_por_metro_n(h_ref) / 1000, 1),
            f"Fuerza total, b = {ancho_muro:.0f} m (MN)": round(
                fuerza_hidrostatica_n(h_ref, ancho_muro) / 1e6, 2),
            "Estado (solo por nivel)": ETIQUETAS_ESTADO[
                clasificar_estado(h_ref, 0.0, dhdt_max)],
        })
    st.table(filas_ref)

st.markdown("---")
st.subheader("🏞️ Vista 3D del embalse")

fraccion_altura = min(1.0, max(0.0, nivel_actual / ALTURA_MAXIMA))

if clase_estado == "danger":
    color_nivel = "#d62728"
elif clase_estado == "caution":
    color_nivel = "#ff7f0e"
else:
    color_nivel = "#1f77b4"

PLANTILLA_3D = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<style>
body { margin: 0; overflow: hidden; background-color: #0e1117; font-family: sans-serif; }
#canvas-container { width: 100%; height: 430px; }
.legend {
    position: absolute; top: 12px; left: 12px; z-index: 10; color: white;
    background: rgba(0,0,0,0.75); padding: 10px 14px; border-radius: 8px;
    font-size: 14px; line-height: 1.5; border: 1px solid #444;
}
</style>
</head>
<body>
<div class="legend">
<b>🌊 Vista del Embalse</b><br>
Nivel actual: <b>__NIVEL__ m / __ALTURA__ m</b><br>
Capacidad: <b>__CAPACIDAD__%</b><br>
Estado: <b>__ESTADO__</b><br>
Lluvia: <b>__LLUVIA_TXT__</b><br>
Compuertas: <b>__COMPUERTAS_TXT__</b>
</div>
<div id="canvas-container"></div>
<script>
const container = document.getElementById('canvas-container');
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0e1117);

const width = container.clientWidth;
const camera = new THREE.PerspectiveCamera(45, width / 430, 0.1, 1000);
camera.position.set(0, 25, 75);
camera.lookAt(0, 8, 0);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(width, 430);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
container.appendChild(renderer.domElement);

scene.add(new THREE.AmbientLight(0xffffff, 0.8));
const dirLight = new THREE.DirectionalLight(0xffffff, 0.7);
dirLight.position.set(20, 50, 30);
scene.add(dirLight);

const ground = new THREE.Mesh(
    new THREE.BoxGeometry(70, 2, 40),
    new THREE.MeshStandardMaterial({ color: 0x2e7d32 })
);
ground.position.set(0, -1, 0);
scene.add(ground);

const dam = new THREE.Mesh(
    new THREE.BoxGeometry(6, 24, 30),
    new THREE.MeshStandardMaterial({ color: 0x7f8c8d, roughness: 0.4 })
);
dam.position.set(15, 12, 0);
scene.add(dam);

const waterHeight = Math.max(0.2, 22 * __FRACCION__);
const waterGeo = new THREE.BoxGeometry(32, waterHeight, 28, 16, 1, 16);
const water = new THREE.Mesh(
    waterGeo,
    new THREE.MeshStandardMaterial({
        color: '__COLOR__', transparent: true, opacity: 0.8, roughness: 0.1
    })
);
water.position.set(-4, waterHeight / 2, 0);
scene.add(water);

const outlet = new THREE.Mesh(
    new THREE.BoxGeometry(20, 0.4, 5),
    new THREE.MeshStandardMaterial({ color: 0x1565c0, transparent: true, opacity: 0.85 })
);
outlet.position.set(26, 0.25, 0);
outlet.visible = __COMPUERTAS_JS__;
scene.add(outlet);

const rainCount = 500;
const rainGeo = new THREE.BufferGeometry();
const rainPositions = new Float32Array(rainCount * 3);
for (let i = 0; i < rainCount * 3; i += 3) {
    rainPositions[i] = (Math.random() - 0.5) * 60;
    rainPositions[i + 1] = Math.random() * 40;
    rainPositions[i + 2] = (Math.random() - 0.5) * 30;
}
rainGeo.setAttribute('position', new THREE.BufferAttribute(rainPositions, 3));
const rain = new THREE.Points(
    rainGeo,
    new THREE.PointsMaterial({ color: 0xaaccee, size: 0.5, transparent: true, opacity: 0.7 })
);
rain.visible = __LLUVIA_JS__;
scene.add(rain);

const clock = new THREE.Clock();
function animate() {
    requestAnimationFrame(animate);
    const elapsed = clock.getElapsedTime();

    const pos = waterGeo.getAttribute('position');
    for (let i = 0; i < pos.count; i++) {
        if (pos.getY(i) > waterHeight * 0.35) {
            const wave = Math.sin(elapsed * 4 + pos.getX(i) * 0.4 + pos.getZ(i) * 0.3) * 0.25;
            pos.setY(i, waterHeight / 2 + wave);
        }
    }
    pos.needsUpdate = true;

    if (rain.visible) {
        const p = rainGeo.attributes.position.array;
        for (let i = 1; i < rainCount * 3; i += 3) {
            p[i] -= 0.8;
            if (p[i] < 0) { p[i] = 40; }
        }
        rainGeo.attributes.position.needsUpdate = true;
    }
    renderer.render(scene, camera);
}
animate();

window.addEventListener('resize', () => {
    const w = container.clientWidth;
    camera.aspect = w / 430;
    camera.updateProjectionMatrix();
    renderer.setSize(w, 430);
});
</script>
</body>
</html>
"""

html_represa_3d = (
    PLANTILLA_3D
    .replace("__NIVEL__", f"{nivel_actual:.2f}")
    .replace("__ALTURA__", f"{ALTURA_MAXIMA:.1f}")
    .replace("__CAPACIDAD__", f"{porcentaje_llenado:.1f}")
    .replace("__ESTADO__", estado)
    .replace("__LLUVIA_TXT__", "🌧️ Activa" if lluvia else "☀️ Sin lluvia")
    .replace(
        "__COMPUERTAS_TXT__",
        "🚪 Abiertas" if st.session_state.compuertas_abiertas else "🔒 Cerradas"
    )
    .replace("__FRACCION__", f"{fraccion_altura}")
    .replace("__COLOR__", color_nivel)
    .replace("__LLUVIA_JS__", "true" if lluvia else "false")
    .replace(
        "__COMPUERTAS_JS__",
        "true" if st.session_state.compuertas_abiertas else "false"
    )
)
components.html(html_represa_3d, height=450)

# RESULTADOS
st.markdown("---")
st.subheader("📈 Evolución del nivel y umbrales de alarma")

niveles_hist = [fila["Nivel h (m)"] for fila in st.session_state.historial]
st.line_chart({
    "Nivel h (m)": niveles_hist,
    "Umbral precaución (m)": [H_ALERTA] * len(niveles_hist),
    "Umbral crítico (m)": [H_CRITICO] * len(niveles_hist),
})
st.caption("Eje horizontal: tiempo simulado (min)")

st.subheader("🕒 Bitácora de eventos de la alarma")
st.table(st.session_state.bitacora)

st.markdown("---")
st.subheader("📋 Resultados generados por la simulación")
st.table(st.session_state.historial[-10:])

archivo_csv = io.StringIO()
escritor = csv.DictWriter(
    archivo_csv, fieldnames=list(st.session_state.historial[0].keys())
)
escritor.writeheader()
escritor.writerows(st.session_state.historial)

st.download_button(
    "📥 Descargar resultados en CSV",
    data=archivo_csv.getvalue(),
    file_name="resultados_simulador_embalse.csv",
    mime="text/csv",
    use_container_width=True
)

if st.session_state.simulando_llenado:
    time.sleep(PAUSA_VISUAL)
    st.rerun()
