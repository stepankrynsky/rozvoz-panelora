import streamlit as st
import pandas as pd
import requests
import io
import re

st.set_page_config(page_title="Panelora Logistics Planner", layout="wide", page_icon="🚚")

# --- KONFIGURACE CENTRÁLY PANELORA ---
SKLAD_LAT = 49.810870930095795
SKLAD_LON = 18.44091600719715
# -------------------------------------

st.title("🚚 PANELORA Logistics Planner")
st.write("Verze 10.1 - Opravené čtení českých CSV souborů (UTF-8 / CP1250).")

# Boční panel s klíči
st.sidebar.header("Nastavení API")
MAPY_API_KEY = st.sidebar.text_input("1. Mapy.cz API Klíč", value="UDc2V4K-B7UO5LBy3KQt8Fh9CmEIpp-mQTkuXJZNuzE", type="password")
ORS_API_KEY = st.sidebar.text_input("2. OpenRouteService Klíč", value="eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6IjUwMDIzODBhNTFlMDRmOGM5ZTdiMDQxMDVmYWViZTE1IiwiaCI6Im11cm11cjY0In0=", type="password")

st.sidebar.subheader("Zdroj dat")
source_type = st.sidebar.radio("Jak chceš načíst objednávky?", ["Nahrát CSV soubor (test.csv)", "Permanentní URL odkaz z Shoptetu"])

df = pd.DataFrame()

if source_type == "Permanentní URL odkaz z Shoptetu":
    SHOPTET_URL = st.sidebar.text_input("Vlož permanentní odkaz (URL)")
    if SHOPTET_URL and st.sidebar.button("Stáhnout data"):
        with st.spinner("Stahuji data..."):
            try:
                response = requests.get(SHOPTET_URL)
                # Zkusíme automatickou detekci, případně UTF-8 / CP1250 fallback
                try:
                    df = pd.read_csv(io.StringIO(response.text), sep=';')
                except Exception:
                    response.encoding = 'cp1250'
                    df = pd.read_csv(io.StringIO(response.text), sep=';')
            except Exception as e:
                st.sidebar.error(f"Chyba stahování z URL: {e}")
else:
    uploaded_file = st.sidebar.file_uploader("Přetáhni CSV soubor", type=["csv"])
    if uploaded_file is not None:
        try:
            # Pokus 1: Načíst jako UTF-8 (včetně souborů s BOM z Excelu)
            df = pd.read_csv(uploaded_file, sep=';', encoding='utf-8-sig')
        except Exception:
            try:
                # Pokus 2: Pokud to selže, vrátíme se na začátek a zkusíme české kódování Windows
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, sep=';', encoding='cp1250')
            except Exception as e:
                st.sidebar.error(f"Soubor nelze přečíst (zkontrolujte středníky): {e}")

if not df.empty:
    st.subheader("📋 Načtené objednávky")
    # Zobrazíme jen sloupce, které v souboru reálně jsou, abychom předešli dalším chybám
    dostupne_sloupce = [col for col in ['code', 'deliveryFullName', 'deliveryStreetWithHouseNumber', 'deliveryCity'] if col in df.columns]
    st.dataframe(df[dostupne_sloupce], use_container_width=True)
    
    if st.button("🚀 Spustit výpočet trasy", type="primary"):
        
        if not MAPY_API_KEY:
            st.error("❌ Zadej do levého panelu API klíč z developer.mapy.cz!")
            st.stop()
            
        # 1. Hledání adres přes MAPY.CZ s detekcí přesnosti
        with st.spinner("Hledám přesná čísla domů přes Mapy.cz..."):
            coordinates = [[SKLAD_LON, SKLAD_LAT]]
            valid_orders = []
            
            for idx, row in df.iterrows():
                ulice = str(row.get('deliveryStreetWithHouseNumber', '')).strip()
                mesto = str(row.get('deliveryCity', '')).strip()
                
                if ulice == 'nan' or mesto == 'nan' or not ulice:
                    continue
                
                vyhledavany_text = f"{ulice}, {mesto}"
                
                try:
                    mapy_url = "https://api.mapy.cz/v1/geocode"
                    params = {
                        "query": vyhledavany_text,
                        "limit": 1,
                        "apikey": MAPY_API_KEY
                    }
                    
                    res_mapy = requests.get(mapy_url, params=params)
                    
                    if res_mapy.status_code in [401, 403]:
                        st.error("❌ Mapy.cz odmítly přístup. Zkontroluj API klíč.")
                        st.stop()
                        
                    geo_res = res_mapy.json()
                    
                    if 'items' in geo_res and len(geo_res['items']) > 0:
                        item = geo_res['items'][0]
                        lon = float(item['position']['lon'])
                        lat = float(item['position']['lat'])
                        
                        item_type = item.get('type', '').lower()
                        if 'address' in item_type:
                            presnost = "✅ Dům"
                        elif 'street' in item_type:
                            presnost = "⚠️ Ulice (odchylka)"
                        else:
                            presnost = "❌ Město (velká odchylka)"
                        
                        coordinates.append([lon, lat])
                        valid_orders.append({
                            'Kód': row.get('code', 'Neznámé'),
                            'Jméno': row.get('deliveryFullName', 'Neznámé'),
                            'Adresa': f"{ulice}, {mesto}",
                            'Telefon': row.get('phone', ''),
                            'Přesnost': presnost,
                            'lat': lat,
                            'lon': lon
                        })
                    else:
                        st.warning(f"⚠️ Adresa na mapě neexistuje: {vyhledavany_text}")
                except Exception as e:
                    st.error(f"Chyba Mapy.cz u {vyhledavany_text}: {e}")

        if not valid_orders:
            st.error("Nepodařilo se najít adresy.")
            st.stop()

        # 2. Matematická optimalizace pořadí (ORS)
        with st.spinner("Počítám nejkratší okruh..."):
            opt_url = "https://api.openrouteservice.org/optimization"
            headers_ors = {'Authorization': ORS_API_KEY, 'Content-Type': 'application/json'}
            
            body = {
                "jobs": [{"id": i, "location": loc} for i, loc in enumerate(coordinates[1:], 1)],
                "vehicles": [{
                    "id": 1,
                    "profile": "driving-car",
                    "start": coordinates[0],
                    "end": coordinates[0]
                }]
            }
            
            response = requests.post(opt_url, json=body, headers=headers_ors)
            
            if response.status_code == 200:
                opt_res = response.json()
                steps = opt_res['routes'][0]['steps']
                
                ordered_route = []
                
                # Sestavení standardního Google Maps odkazu
                gmaps_url = "https://www.google.com/maps/dir"
                gmaps_url += f"/{SKLAD_LAT},{SKLAD_LON}"
                
                for step in steps:
                    if step['type'] == 'job':
                        job_idx = step['id'] - 1
                        order = valid_orders[job_idx]
                        ordered_route.append(order)
                        gmaps_url += f"/{order['lat']},{order['lon']}"
                
                gmaps_url += f"/{SKLAD_LAT},{SKLAD_LON}"
                
                st.balloons()
                st.success("Hotovo! Trasa je naplánována.")
                st.markdown(f"### 📱 [KLIKNI ZDE PRO SPUŠTĚNÍ NAVIGACE V GOOGLE MAPÁCH]({gmaps_url})")
                
                st.subheader("📋 Přesný harmonogram rozvozu")
                df_final = pd.DataFrame(ordered_route)
                
                def highlight_precision(val):
                    if '✅' in str(val): return 'color: #00b300; font-weight: bold;'
                    elif '⚠️' in str(val): return 'color: #ff9900; font-weight: bold;'
                    elif '❌' in str(val): return 'color: #e60000; font-weight: bold;'
                    return ''
                
                st.dataframe(df_final[['Kód', 'Jméno', 'Adresa', 'Telefon', 'Přesnost']].style.map(highlight_precision, subset=['Přesnost']), use_container_width=True)
                
                st.subheader("🗺️ Reálná mapa zastávek")
                map_df = pd.DataFrame(ordered_route)[['lat', 'lon']]
                sklad_df = pd.DataFrame([{'lat': SKLAD_LAT, 'lon': SKLAD_LON}])
                st.map(pd.concat([sklad_df, map_df]))
            else:
                st.error(f"Chyba optimalizace: {response.text}")
else:
    st.info("💡 Čekám na nahrání souboru nebo zadání URL ze Shoptetu v levém panelu.")
