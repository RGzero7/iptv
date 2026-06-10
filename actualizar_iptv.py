import os
import gzip
import xml.etree.ElementTree as ET
import requests
import re

# --- CONFIGURACIÓN ---
URLS_EPG = {
    "LATAM_Y_ESP": "https://epgshare01.online/epgshare01/epg_ripper_AR1.xml.gz",
    "ESP_GLOBAL": "https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz",
    "US_SPA": "https://epgshare01.online/epgshare01/epg_ripper_US-SPA1.xml.gz"
}
URL_M3U_ORIGINAL = "https://iptv-org.github.io/iptv/languages/spa.m3u"

FICHERO_MIS_CANALES = "mis_canales.txt"
OUTPUT_M3U = "lista_filtrada.m3u"
OUTPUT_XML = "guia_filtrada.xml"

def normalizar(texto):
    """Limpia el texto para facilitar coincidencias (ej: 'Antena 3 HD' -> 'antena3')"""
    if not texto:
        return ""
    texto = texto.lower()
    texto = re.sub(r'[^a-z0-8]', '', texto) # Quita espacios, puntos, guiones y símbolos
    return texto.replace('hd', '').replace('sd', '') # Ignora si uno dice HD y el otro no

def cargar_canales_deseados():
    if not os.path.exists(FICHERO_MIS_CANALES):
        print(f"[-] No se encontró {FICHERO_MIS_CANALES}.")
        return set()
    with open(FICHERO_MIS_CANALES, "r", encoding="utf-8") as f:
        # Cargamos los nombres y los normalizamos
        return set(normalizar(line) for line in f if line.strip() and not line.startswith("#"))

def filtrar_m3u(canales_deseados_normalizados):
    print("[+] Descargando y filtrando lista M3U...")
    try:
        response = requests.get(URL_M3U_ORIGINAL, timeout=30)
        response.raise_for_status()
    except Exception as e:
        print(f"[-] Error al descargar el M3U: {e}")
        return {}

    lineas = response.text.splitlines()
    lineas_filtradas = ["#EXTM3U"]
    
    # Mapeo para recordar qué tvg-id real quedó en nuestro M3U filtrado
    dict_ids_guardados = {} 
    guardar_siguiente = False
    info_canal = ""
    id_actual = ""

    for linea in lineas:
        if linea.startswith("#EXTINF"):
            tvg_id = linea.split('tvg-id="')[1].split('"')[0] if 'tvg-id="' in linea else ""
            tvg_name = linea.split('tvg-name="')[1].split('"')[0] if 'tvg-name="' in linea else ""
            display_name = linea.split(",")[-1] if "," in linea else ""

            # Verificamos si coincide por ID o por Nombre comercial
            if normalizar(tvg_id) in canales_deseados_normalizados or normalizar(tvg_name) in canales_deseados_normalizados or normalizar(display_name) in canales_deseados_normalizados:
                info_canal = linea
                id_actual = tvg_id
                guardar_siguiente = True
        
        elif guardar_siguiente and (linea.startswith("http://") or linea.startswith("https://")):
            lineas_filtradas.append(info_canal)
            lineas_filtradas.append(linea)
            if id_actual:
                # Guardamos las distintas formas del nombre para buscarlo en el XML de la guía
                dict_ids_guardados[normalizar(id_actual)] = id_actual
            guardar_siguiente = False

    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas_filtradas))
    print(f"[+] M3U filtrado guardado con éxito ({len(lineas_filtradas) // 2} canales).")
    return dict_ids_guardados

def generar_epg_filtrado(dict_ids_guardados):
    print("[+] Iniciando procesamiento de guías de TV (XML)...")
    
    root_nuevo = ET.Element("tv")
    canales_agregados = set()
    map_id_guia_a_id_m3u = {} # Para renombrar los canales de la guía y que Jellyfin los empareje solo

    for nombre_guia, url in URLS_EPG.items():
        print(f"  -> Descargando y procesando: {nombre_guia}...")
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            
            xml_data = gzip.decompress(resp.content)
            root_original = ET.fromstring(xml_data)
            
            # 1. Buscar canales en la guía que coincidan con nuestra lista
            for canal in root_original.findall("channel"):
                canal_id_guia = canal.get("id")
                display_name = canal.find("display-name").text if canal.find("display-name") is not None else ""
                
                norm_id_guia = normalizar(canal_id_guia)
                norm_name_guia = normalizar(display_name)

                id_m3u_correcto = None
                # ¿El canal de la guía coincide con algo de lo que guardamos en el M3U?
                if norm_id_guia in dict_ids_guardados:
                    id_m3u_correcto = dict_ids_guardados[norm_id_guia]
                elif norm_name_guia in dict_ids_guardados:
                    id_m3u_correcto = dict_ids_guardados[norm_name_guia]

                if id_m3u_correcto and id_m3u_correcto not in canales_agregados:
                    # Clonamos el nodo y le forzamos el ID del M3U para que Jellyfin no se confunda
                    canal.set("id", id_m3u_correcto)
                    root_nuevo.append(canal)
                    canales_agregados.add(id_m3u_correcto)
                    map_id_guia_a_id_m3u[canal_id_guia] = id_m3u_correcto
            
            # 2. Extraer la programación de esos canales hallados
            for programa in root_original.findall("programme"):
                canal_id_guia = programa.get("channel")
                if canal_id_guia in map_id_guia_a_id_m3u:
                    # Le asignamos el ID corregido que entiende el M3U
                    programa.set("channel", map_id_guia_a_id_m3u[canal_id_guia])
                    root_nuevo.append(programa)
                    
        except Exception as e:
            print(f"  [-] Error en {nombre_guia}: {e}")

    arbol = ET.ElementTree(root_nuevo)
    ET.indent(arbol, space="  ", level=0)
    arbol.write(OUTPUT_XML, encoding="utf-8", xml_declaration=True)
    print(f"[+] Guía XML filtrada generada correctamente con {len(canales_agregados)} canales programados.")

if __name__ == "__main__":
    canales_norm = cargar_canales_deseados()
    if canales_norm:
        dict_ids = filtrar_m3u(canales_norm)
        if dict_ids:
            generar_epg_filtrado(dict_ids)
            print("[+] Proceso finalizado con éxito.")
        else:
            print("[-] No se encontraron canales coincidentes en el M3U original.")
    else:
        print("[-] Operación cancelada: 'mis_canales.txt' está vacío.")