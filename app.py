import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time
import os
import traceback

st.set_page_config(page_title="EMEA SC SE Network", layout="wide")

# --- 1. LOGIN MASK ---
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    st.title("Login to EMEA Distribution Network App")
    
    try:
        SECURE_USER = st.secrets["credentials"]["username"]
        SECURE_PASS = st.secrets["credentials"]["password"]
    except KeyError:
        st.error("⚠️ Secrets not found! Please configure your Streamlit Secrets in the Cloud Settings.")
        st.stop()

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    
    if st.button("Login"):
        if username == SECURE_USER and password == SECURE_PASS:
            st.session_state["logged_in"] = True
            st.rerun()
        else:
            st.error("Invalid credentials. Please try again.")
    st.stop()

# --- SIDEBAR & COMPLEXITY SETTINGS ---
st.sidebar.header("⚙️ Dashboard Settings")

if st.sidebar.button("📂 Change Data Source"):
    st.session_state["data_loaded"] = False
    st.rerun()

if st.sidebar.button("🔄 Clear Cache & Reload Data"):
    st.cache_data.clear()
    st.session_state["data_loaded"] = False
    st.rerun()

st.sidebar.divider()
st.sidebar.subheader("Map Complexity")
enable_map = st.sidebar.checkbox("Enable Geographical Map", value=True, help="Disable to skip geocoding and load instantly.")

max_locations = 40
if enable_map:
    max_locations = st.sidebar.slider("Locations to Geocode", min_value=5, max_value=100, value=30, step=5)
    est_time = int(max_locations * 1.2)
    st.sidebar.info(f"⏱️ Estimated loading time: ~{est_time} seconds")


# --- 2. DATA PROCESSING (THE BULLETPROOF WAY) ---
@st.cache_data(show_spinner=False)
def load_and_process_data(file_source):
    # Read the file as a raw grid without headers
    df_raw = pd.read_excel(file_source, skiprows=8, header=None)
    
    # Extract the two header rows
    # Row 0: Forecast Types (Merged cells, so we forward-fill the empty spaces)
    forecast_types = df_raw.iloc[0].ffill()
    
    # Row 1: Warehouse Locations
    locations = df_raw.iloc[1]
    
    # Extract the actual data (Row 2 onwards)
    df_data = df_raw.iloc[2:].copy()
    
    # Build a single flat list of unique column names: "ForecastType||Location"
    new_columns = ['Market'] # The first column is always the Market
    for i in range(1, len(df_raw.columns)):
        ft = str(forecast_types[i]).strip()
        loc = str(locations[i]).strip()
        new_columns.append(f"{ft}||{loc}")
        
    df_data.columns = new_columns
    
    # Now we melt it down! Single header melting is completely stable.
    df_long = df_data.melt(id_vars=['Market'], var_name='RawCol', value_name='Volume')
    
    # Split the "RawCol" back into our two categories
    df_long[['ForecastType', 'Location']] = df_long['RawCol'].str.split('||', expand=True)
    df_long.drop(columns=['RawCol'], inplace=True)
    
    # Clean up the data
    df_long = df_long.dropna(subset=['Market'])
    df_long['Market'] = df_long['Market'].astype(str).str.strip()
    
    # Force Volume to numeric (turns empty spaces, dashes, etc., into NaNs, then 0)
    df_long['Volume'] = pd.to_numeric(df_long['Volume'], errors='coerce').fillna(0)
    df_long = df_long[df_long['Volume'] > 0] # Drop empty lines
    
    # Role Mapping
    def map_role(val):
        val = str(val).lower()
        if 'ldc' in val: return 'LDCs'
        if 'rdc' in val: return 'RDCs'
        if 'factory' in val or 'fw' in val: return 'FWs'
        return 'Other'
        
    df_long['Wh_Role'] = df_long['ForecastType'].apply(map_role)
    df_long = df_long[df_long['Wh_Role'] != 'Other']
    
    # Aggregate to sum up LDC and LDC split
    df_agg = df_long.groupby(['Market', 'Wh_Role', 'Location'])['Volume'].sum().reset_index()
    return df_agg

@st.cache_data(show_spinner=False)
def load_locations_and_geocode(file_source, limit):
    df_loc = pd.read_excel(file_source)
    
    # Clean location column name
    if 'location' in df_loc.columns:
        df_loc.rename(columns={'location': 'Location'}, inplace=True)
    else:
        df_loc.rename(columns={df_loc.columns[0]: 'Location'}, inplace=True)
        
    df_loc['Location'] = df_loc['Location'].astype(str).str.strip()
    df_loc = df_loc.head(limit).copy()
    
    headers = {'User-Agent': 'GoodYearDistributionApp/2.0'}
    latitudes, longitudes = [], []
    
    for _, row in df_loc.iterrows():
        street = str(row.get('street address', '')) if pd.notna(row.get('street address')) else ''
        city = str(row.get('City', '')) if pd.notna(row.get('City')) else ''
        country = str(row.get('iso Country', '')) if pd.notna(row.get('iso Country')) else ''
        
        query = f"{street} {city} {country}".strip()
        try:
            r = requests.get(f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1", headers=headers, timeout=5).json()
            if r:
                latitudes.append(float(r[0]['lat']))
                longitudes.append(float(r[0]['lon']))
            else:
                query_fb = f"{city} {country}".strip()
                r_fb = requests.get(f"https://nominatim.openstreetmap.org/search?q={query_fb}&format=json&limit=1", headers=headers, timeout=5).json()
                if r_fb:
                    latitudes.append(float(r_fb[0]['lat']))
                    longitudes.append(float(r_fb[0]['lon']))
                else:
                    latitudes.append(None); longitudes.append(None)
        except Exception:
            latitudes.append(None); longitudes.append(None)
            
        time.sleep(1.1)
        
    df_loc['lat'] = latitudes
    df_loc['lon'] = longitudes
    return df_loc


# --- 3. DATA LOADING DASHBOARD ---
st.title("EMEA Distribution Network")

if "data_loaded" not in st.session_state:
    st.session_state["data_loaded"] = False

pivot_name = "Cube - EMEA SC SE - FC Distribution to Warehouse - V1.xlsx"
loc_name = "locations_info.xlsx"

if not st.session_state.get("data_loaded"):
    st.info("👈 Please select a data source below to begin.")
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Automated Mode")
        if st.button("Load pre-selected FD2W data"):
            if os.path.exists(pivot_name) and os.path.exists(loc_name):
                st.session_state['pivot_file'] = pivot_name
                st.session_state['loc_file'] = loc_name
                st.session_state["data_loaded"] = True
                st.rerun() 
            else:
                st.error(f"Files not found on server. Ensure exact names: `{pivot_name}`")

    with col2:
        st.subheader("Manual Mode")
        up_pivot = st.file_uploader("Upload Pivot XLSX", type=['xlsx'])
        up_loc = st.file_uploader("Upload Locations XLSX", type=['xlsx'])
        if up_pivot and up_loc:
            if st.button("Process Uploaded Files"):
                st.session_state['pivot_file'] = up_pivot
                st.session_state['loc_file'] = up_loc
                st.session_state["data_loaded"] = True
                st.rerun() 
    st.stop()


# --- 4. VISUALIZATION DASHBOARD ---
try:
    with st.spinner("Decoding pivot and rendering charts..."):
        df_data = load_and_process_data(st.session_state['pivot_file'])
    
        if enable_map:
            with st.spinner(f"Geocoding up to {max_locations} locations... this will take ~{est_time}s"):
                df_locations = load_locations_and_geocode(st.session_state['loc_file'], max_locations)

    color_map = {'FWs': '#D8BFD8', 'RDCs': '#FFCCCB', 'LDCs': '#FFFFE0'}

    st.header("1. EMEA Network View (Market & Wh Role)")
    if not df_data.empty:
        df_market_role = df_data.groupby(['Market', 'Wh_Role'])['Volume'].sum().reset_index()
        fig1 = px.bar(df_market_role, x='Market', y='Volume', color='Wh_Role', 
                      color_discrete_map=color_map, barmode='stack')
        st.plotly_chart(fig1, use_container_width=True)
    else:
        st.error("Data was processed, but the final table is empty. Please verify the structure of the Pivot file.")
        st.stop()

    st.divider()
    st.header("2. Specific Market Drill-down")
    markets = [m for m in sorted(df_data['Market'].unique()) if m.lower() != 'nan']
    selected_market = st.selectbox("Select a Market", markets)
    df_market = df_data[df_data['Market'] == selected_market]
    fig2 = px.bar(df_market, x='Location', y='Volume', color='Wh_Role',
                  color_discrete_map=color_map, title=f"Location Detail: {selected_market}")
    st.plotly_chart(fig2, use_container_width=True)

    st.divider()
    st.header("3. Geographical Network Map")
    if enable_map:
        df_data['Location'] = df_data['Location'].astype(str).str.strip()
        df_map_agg = df_data.groupby(['Location', 'Wh_Role'])['Volume'].sum().reset_index()
        df_map = pd.merge(df_locations, df_map_agg, on='Location', how='inner').dropna(subset=['lat', 'lon'])

        if not df_map.empty:
            fig_map = px.scatter_mapbox(df_map, lat='lat', lon='lon', color='Wh_Role', size='Volume',
                                        hover_name='Location', hover_data=['City', 'Volume'],
                                        color_discrete_map=color_map, zoom=3, height=700,
                                        mapbox_style='carto-positron')
            st.plotly_chart(fig_map, use_container_width=True)
        else:
            st.warning("Locations could not be plotted. Either the geocoding API reached its limit, or the location names in the two files don't perfectly match.")
    else:
        st.info("Map is disabled via the sidebar. Enable it to view the geographic distribution.")

except Exception as e:
    st.error("🚨 An error occurred while generating the dashboard:")
    st.code(traceback.format_exc())
    st.warning("Please click 'Change Data Source' in the sidebar to try again.")
