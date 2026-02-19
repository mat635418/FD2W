import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time
import os

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

if st.sidebar.button("🔄 Clear Cache & Reload Data"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.divider()
st.sidebar.subheader("Map Complexity")
enable_map = st.sidebar.checkbox("Enable Geographical Map", value=True, help="Disable to skip geocoding and load instantly.")

max_locations = 40
if enable_map:
    max_locations = st.sidebar.slider("Locations to Geocode", min_value=5, max_value=100, value=30, step=5)
    est_time = int(max_locations * 1.2)
    st.sidebar.info(f"⏱️ Estimated loading time: ~{est_time} seconds")


# --- 2. DATA PROCESSING ---
@st.cache_data(show_spinner=False)
def load_and_process_data(file_source):
    df_pivot = pd.read_excel(file_source, skiprows=8, header=[0,1])
    
    # Forward fill top header level
    level0 = df_pivot.columns.get_level_values(0).to_series()
    level0 = level0.replace(r'^Unnamed:.*', pd.NA, regex=True).ffill()
    df_pivot.columns = pd.MultiIndex.from_arrays([level0, df_pivot.columns.get_level_values(1)])
    
    # Set the first column as the index (Market)
    first_col = df_pivot.columns[0]
    df_pivot = df_pivot.set_index(first_col)
    df_pivot.index.name = 'Market'
    
    # FIX: Strip trailing spaces from all multi-index column headers
    df_pivot.columns = pd.MultiIndex.from_tuples([
        (str(c[0]).strip(), str(c[1]).strip()) for c in df_pivot.columns
    ])
    df_pivot.columns.names = ['ForecastType', 'Location']
    
    # Flatten structure
    df_long = df_pivot.stack(level=['ForecastType', 'Location'], future_stack=True).reset_index(name='Volume')
    
    # FIX: Ensure Market and Volume are clean
    df_long = df_long.dropna(subset=['Market'])
    df_long['Market'] = df_long['Market'].astype(str).str.strip()
    df_long['Volume'] = pd.to_numeric(df_long['Volume'], errors='coerce').fillna(0)
    df_long = df_long[df_long['Volume'] > 0]
    
    # FIX: Fuzzy mapping to avoid exact string matching errors
    def map_role(val):
        val = str(val).lower()
        if 'ldc' in val: return 'LDCs'
        if 'rdc' in val: return 'RDCs'
        if 'factory' in val or 'fw' in val: return 'FWs'
        return 'Other'
        
    df_long['Wh_Role'] = df_long['ForecastType'].apply(map_role)
    df_long = df_long[df_long['Wh_Role'] != 'Other'] # Filter out non-matching columns
    
    # Group and sum
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
    
    # Limit rows to speed up rendering based on user slider
    df_loc = df_loc.head(limit).copy()
    
    headers = {'User-Agent': 'GoodYearDistributionApp/1.2'}
    latitudes, longitudes = [], []
    
    for _, row in df_loc.iterrows():
        street = str(row.get('street address', '')) if pd.notna(row.get('street address')) else ''
        city = str(row.get('City', '')) if pd.notna(row.get('City')) else ''
        country = str(row.get('iso Country', '')) if pd.notna(row.get('iso Country')) else ''
        
        query = f"{street} {city} {country}".strip()
        try:
            r = requests.get(f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1", headers=headers).json()
            if r:
                latitudes.append(float(r[0]['lat']))
                longitudes.append(float(r[0]['lon']))
            else:
                # Fallback to City + Country
                query_fb = f"{city} {country}".strip()
                r_fb = requests.get(f"https://nominatim.openstreetmap.org/search?q={query_fb}&format=json&limit=1", headers=headers).json()
                if r_fb:
                    latitudes.append(float(r_fb[0]['lat']))
                    longitudes.append(float(r_fb[0]['lon']))
                else:
                    latitudes.append(None); longitudes.append(None)
        except Exception:
            latitudes.append(None); longitudes.append(None)
            
        time.sleep(1.1) # Strict Nominatim rate limiting
        
    df_loc['lat'] = latitudes
    df_loc['lon'] = longitudes
    return df_loc


# --- 3. DATA LOADING DASHBOARD ---
st.title("EMEA Distribution Network")

if "data_loaded" not in st.session_state:
    st.session_state["data_loaded"] = False

pivot_name = "Cube - EMEA SC SE - FC Distribution to Warehouse - V1.xlsx"
loc_name = "locations_info.xlsx"

col1, col2 = st.columns(2)
with col1:
    st.subheader("Automated Mode")
    if st.button("Load pre-selected FD2W data"):
        if os.path.exists(pivot_name) and os.path.exists(loc_name):
            st.session_state['pivot_file'] = pivot_name
            st.session_state['loc_file'] = loc_name
            st.session_state["data_loaded"] = True
        else:
            st.error("Pre-selected files not found on server.")

with col2:
    st.subheader("Manual Mode")
    up_pivot = st.file_uploader("Upload Pivot XLSX", type=['xlsx'])
    up_loc = st.file_uploader("Upload Locations XLSX", type=['xlsx'])
    if up_pivot and up_loc:
        if st.button("Process Uploaded Files"):
            st.session_state['pivot_file'] = up_pivot
            st.session_state['loc_file'] = up_loc
            st.session_state["data_loaded"] = True

if not st.session_state.get("data_loaded"):
    st.info("👈 Please select a data source to begin.")
    st.stop()


# --- 4. VISUALIZATION DASHBOARD ---
with st.spinner("Processing data & rendering charts..."):
    df_data = load_and_process_data(st.session_state['pivot_file'])

    if enable_map:
        with st.spinner(f"Geocoding up to {max_locations} locations... this will take ~{est_time}s"):
            df_locations = load_locations_and_geocode(st.session_state['loc_file'], max_locations)

color_map = {'FWs': '#D8BFD8', 'RDCs': '#FFCCCB', 'LDCs': '#FFFFE0'}

# Section 1: Overall Network
st.header("1. EMEA Network View (Market & Wh Role)")
if not df_data.empty:
    df_market_role = df_data.groupby(['Market', 'Wh_Role'])['Volume'].sum().reset_index()
    fig1 = px.bar(df_market_role, x='Market', y='Volume', color='Wh_Role', 
                  color_discrete_map=color_map, barmode='stack')
    st.plotly_chart(fig1, use_container_width=True)
else:
    st.warning("No data found after processing. Please check the source file.")

# Section 2: Market Selection
st.divider()
st.header("2. Specific Market Drill-down")
if not df_data.empty:
    markets = [m for m in sorted(df_data['Market'].unique()) if m.lower() != 'nan']
    selected_market = st.selectbox("Select a Market", markets)
    df_market = df_data[df_data['Market'] == selected_market]
    fig2 = px.bar(df_market, x='Location', y='Volume', color='Wh_Role',
                  color_discrete_map=color_map, title=f"Location Detail: {selected_market}")
    st.plotly_chart(fig2, use_container_width=True)

# Section 3: Geographic Map
st.divider()
st.header("3. Geographical Network Map")
if enable_map and not df_data.empty:
    # Ensure keys match by stripping strings
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
        st.warning("Could not map the locations. The locations in the Pivot file might not match the names in the Locations file, or geocoding failed.")
else:
    st.info("Map is disabled via the sidebar. Enable it to view the geographic distribution.")
