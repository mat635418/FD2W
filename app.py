import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time
import os

# --- PAGE CONFIG ---
st.set_page_config(page_title="EMEA Distribution Network", layout="wide")

# --- 1. LOGIN MASK (Using Streamlit Secrets) ---
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    st.title("Login to EMEA FD2W App")
    
    # Fetch secure credentials from Streamlit Secrets
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


# --- 2. DATA PROCESSING ---
@st.cache_data
def load_and_process_data(file_source):
    """Load and process the pivot Excel file"""
    try:
        # Load Pivot Data from Excel
        df_pivot = pd.read_excel(file_source, skiprows=8, header=[0,1])
        
        # Debug: Show raw data shape
        st.sidebar.info(f"📊 Raw data shape: {df_pivot.shape}")
        
        # Fix pandas multi-index columns for merged header cells
        level0 = df_pivot.columns.get_level_values(0).to_series()
        level0 = level0.replace(r'^Unnamed:.*', pd.NA, regex=True).ffill()
        df_pivot.columns = pd.MultiIndex.from_arrays([level0, df_pivot.columns.get_level_values(1)])
        
        # The very first column contains the market names. Set it as the index.
        first_col = df_pivot.columns[0]
        df_pivot = df_pivot.set_index(first_col)
        df_pivot.index.name = 'Market'
        
        # Name the two levels of our column headers so stacking is clean
        df_pivot.columns.names = ['ForecastType', 'Location']
        
        # Using .stack() is the safest way to flatten a MultiIndex column in modern pandas
        df_long = df_pivot.stack(level=['ForecastType', 'Location']).reset_index(name='Volume')
        
        # Convert Volume to numeric, coercing errors to NaN
        df_long['Volume'] = pd.to_numeric(df_long['Volume'], errors='coerce')
        
        # Debug: Check data before cleaning
        st.sidebar.info(f"🔍 Before cleaning: {len(df_long)} rows")
        
        # Clean data: drop NAs and zero/negative volume
        df_long = df_long.dropna(subset=['Volume'])
        df_long = df_long[df_long['Volume'] > 0]
        
        # Debug: Check data after cleaning
        st.sidebar.info(f"✅ After cleaning: {len(df_long)} rows")
        
        # Map ForecastType to Warehouse Roles (Summing up LDC and LDC split)
        role_mapping = {
            'Forecast at LDC': 'LDCs',
            'Forecast at LDC (area split)': 'LDCs',
            'Forecast at RDC': 'RDCs',
            'Forecast at Factory Warehouse': 'FWs'
        }
        df_long['Wh_Role'] = df_long['ForecastType'].map(role_mapping)
        
        # Drop rows where role mapping failed
        df_long = df_long.dropna(subset=['Wh_Role'])
        
        # Group by Market, Role, and Location to combine "LDC" and "LDC split"
        df_agg = df_long.groupby(['Market', 'Wh_Role', 'Location'])['Volume'].sum().reset_index()
        
        st.sidebar.success(f"✅ Final dataset: {len(df_agg)} rows")
        
        return df_agg
    except Exception as e:
        st.error(f"Error processing data: {str(e)}")
        st.stop()

@st.cache_data
def load_locations_data(file_source):
    """Load location data without geocoding initially"""
    try:
        df_loc = pd.read_excel(file_source)
        
        # Fix column names
        if df_loc.columns[0].startswith('Unnamed'):
            df_loc.rename(columns={df_loc.columns[0]: 'Location'}, inplace=True)
        
        st.sidebar.info(f"📍 Loaded {len(df_loc)} locations")
        return df_loc
    except Exception as e:
        st.error(f"Error loading locations: {str(e)}")
        st.stop()

@st.cache_data
def geocode_locations(df_loc, max_locations=None):
    """Geocode locations via Nominatim API with optional limit"""
    
    if max_locations:
        df_loc = df_loc.head(max_locations)
    
    estimated_time = len(df_loc) * 1.2  # ~1 second per location + buffer
    st.sidebar.warning(f"⏱️ Geocoding {len(df_loc)} locations (~{estimated_time:.0f}s)")
    
    headers = {'User-Agent': 'GoodYearDistributionApp/1.0'}
    latitudes, longitudes = [], []
    
    progress_bar = st.sidebar.progress(0)
    status_text = st.sidebar.empty()
    
    for idx, row in df_loc.iterrows():
        status_text.text(f"Geocoding {idx+1}/{len(df_loc)}")
        progress_bar.progress((idx + 1) / len(df_loc))
        
        street = str(row.get('street address', '')) if pd.notna(row.get('street address')) else ''
        city = str(row.get('City', '')) if pd.notna(row.get('City')) else ''
        country = str(row.get('iso Country', '')) if pd.notna(row.get('iso Country')) else ''
        
        # Build query string
        query = f"{street} {city} {country}".strip()
        url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
        
        try:
            r = requests.get(url, headers=headers, timeout=5).json()
            if r:
                latitudes.append(float(r[0]['lat']))
                longitudes.append(float(r[0]['lon']))
            else:
                # Fallback to City + Country
                query_fallback = f"{city} {country}".strip()
                url_fallback = f"https://nominatim.openstreetmap.org/search?q={query_fallback}&format=json&limit=1"
                r_fallback = requests.get(url_fallback, headers=headers, timeout=5).json()
                if r_fallback:
                    latitudes.append(float(r_fallback[0]['lat']))
                    longitudes.append(float(r_fallback[0]['lon']))
                else:
                    latitudes.append(None)
                    longitudes.append(None)
        except Exception:
            latitudes.append(None)
            longitudes.append(None)
            
        time.sleep(1)  # Delay requested by Nominatim policy
    
    progress_bar.empty()
    status_text.empty()
    
    df_loc['lat'] = latitudes
    df_loc['lon'] = longitudes
    
    valid_coords = df_loc[['lat', 'lon']].notna().all(axis=1).sum()
    st.sidebar.success(f"✅ Geocoded: {valid_coords}/{len(df_loc)} locations")
    
    return df_loc


# --- 3. SIDEBAR CONTROLS ---
st.sidebar.title("⚙️ Configuration")

# Data loading controls
st.sidebar.header("1️⃣ Data Loading")

if "data_loaded" not in st.session_state:
    st.session_state["data_loaded"] = False

# Expected Hardcoded filenames on the server
pivot_name = "Cube - EMEA SC SE - FC Distribution to Warehouse - V1.xlsx"
loc_name = "locations_info.xlsx"

# Option A: Pre-load files from Repo
if st.sidebar.button("🚀 Load Pre-selected Data"):
    if os.path.exists(pivot_name) and os.path.exists(loc_name):
        st.session_state['pivot_file'] = pivot_name
        st.session_state['loc_file'] = loc_name
        st.session_state["data_loaded"] = True
        st.rerun()
    else:
        st.sidebar.error(f"Files not found: `{pivot_name}` and `{loc_name}`")

# Option B: Manual Upload
with st.sidebar.expander("📤 Manual Upload"):
    up_pivot = st.file_uploader("Upload Pivot", type=['xlsx'])
    up_loc = st.file_uploader("Upload Locations", type=['xlsx'])
    
    if up_pivot and up_loc:
        if st.button("Process Uploaded Files"):
            st.session_state['pivot_file'] = up_pivot
            st.session_state['loc_file'] = up_loc
            st.session_state["data_loaded"] = True
            st.rerun()

# Map complexity controls
st.sidebar.header("2️⃣ Map Complexity")
enable_map = st.sidebar.checkbox("Enable Geographic Map", value=True, 
                                  help="Disable to speed up loading")

if enable_map:
    max_locations = st.sidebar.slider(
        "Max locations to geocode",
        min_value=10,
        max_value=200,
        value=50,
        step=10,
        help="Fewer locations = faster loading"
    )
    estimated_time = max_locations * 1.2
    st.sidebar.info(f"⏱️ Est. map load time: ~{estimated_time:.0f}s")
else:
    max_locations = 0


# --- 4. MAIN DASHBOARD ---
st.title("🌍 EMEA Distribution Network")

# Block execution until data is loaded
if not st.session_state.get("data_loaded"):
    st.info("👈 Choose a method in the sidebar to load the operational data.")
    st.stop()


# --- 5. LOAD AND PROCESS DATA ---
with st.spinner("📊 Processing volume data..."):
    df_data = load_and_process_data(st.session_state['pivot_file'])

# Check if data is empty
if df_data.empty:
    st.error("❌ No data found after processing. Please check your Excel file format.")
    st.write("**Debug Info:**")
    st.write("- Ensure the Excel file has the correct structure")
    st.write("- Check that skiprows=8 is correct for your file")
    st.write("- Verify that volume data exists and is numeric")
    st.stop()

# Load location data
df_locations_raw = load_locations_data(st.session_state['loc_file'])

# Set Color coding
color_map = {'FWs': '#D8BFD8', 'RDCs': '#FFCCCB', 'LDCs': '#FFFFE0'}

# --- 6. VISUAL 1: Market and Role Overview ---
st.header("📊 1. Volume by Market and Warehouse Role")
df_market_role = df_data.groupby(['Market', 'Wh_Role'])['Volume'].sum().reset_index()

if not df_market_role.empty:
    fig1 = px.bar(df_market_role, x='Market', y='Volume', color='Wh_Role', 
                  color_discrete_map=color_map, title='Market Distribution Overview',
                  barmode='stack')
    fig1.update_layout(height=500)
    st.plotly_chart(fig1, use_container_width=True)
else:
    st.warning("No data available for Market/Role visualization")

st.divider()

# --- 7. VISUAL 2: Market Drill-down ---
st.header("🔍 2. Detailed Split by Specific Market")

markets = sorted(df_data['Market'].unique())
if len(markets) > 0:
    selected_market = st.selectbox("Select a Market", markets, key='market_selector')
    
    df_market = df_data[df_data['Market'] == selected_market]
    
    if not df_market.empty:
        fig2 = px.bar(df_market, x='Location', y='Volume', color='Wh_Role',
                      color_discrete_map=color_map, 
                      title=f'Location vs Role Distribution for {selected_market}')
        fig2.update_layout(height=500, xaxis_tickangle=-45)
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.warning(f"No data available for {selected_market}")
else:
    st.warning("No markets found in the data")

st.divider()

# --- 8. VISUAL 3: Geographic Map ---
if enable_map:
    st.header("🗺️ 3. Interactive Distribution Map")
    
    with st.spinner(f"🌐 Geocoding up to {max_locations} locations... This may take a few minutes."):
        df_locations = geocode_locations(df_locations_raw, max_locations)
    
    # Merge volumes with geocoded locations
    df_map_data = df_data.groupby(['Location', 'Wh_Role'])['Volume'].sum().reset_index()
    df_map = pd.merge(df_locations, df_map_data, on='Location', how='inner')
    df_map = df_map.dropna(subset=['lat', 'lon'])
    
    if not df_map.empty:
        fig_map = px.scatter_mapbox(df_map, lat='lat', lon='lon', color='Wh_Role', size='Volume',
                                    hover_name='Location', hover_data=['City', 'Volume'],
                                    color_discrete_map=color_map, zoom=3, height=600,
                                    mapbox_style='carto-positron',
                                    title=f'Geographical Network ({len(df_map)} locations shown)')
        st.plotly_chart(fig_map, use_container_width=True)
    else:
        st.warning("⚠️ No geographical data could be mapped. Possible reasons:")
        st.write("- Location names in pivot don't match location names in locations file")
        st.write("- Geocoding failed for all locations")
        st.write("- Try increasing the max locations slider")
else:
    st.info("ℹ️ Geographic map is disabled. Enable it in the sidebar to view the map.")

# --- 9. DATA PREVIEW (Debug) ---
with st.expander("🔍 View Raw Data (Debug)"):
    st.subheader("Processed Volume Data")
    st.dataframe(df_data.head(50))
    
    st.subheader("Location Data")
    st.dataframe(df_locations_raw.head(20))
