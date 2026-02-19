import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time
import os

# --- PAGE CONFIG ---
st.set_page_config(page_title="EMEA Distribution Network", layout="wide")

# --- 1. LOGIN MASK ---
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    st.title("Login to EMEA FD2W App")
    
    try:
        SECURE_USER = st.secrets["credentials"]["username"]
        SECURE_PASS = st.secrets["credentials"]["password"]
    except KeyError:
        st.error("⚠️ Secrets not found! Please configure your Streamlit Secrets.")
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
    """Load the multi-level header Excel file"""
    # Read Excel - the first 8 rows are metadata, then 2 header rows
    df = pd.read_excel(file_source, header=[8, 9])
    
    # The first column is the Market name (has a merged header)
    # All other columns are (ForecastType, Location) pairs
    market_col = df.columns[0]
    df = df.rename(columns={market_col: 'Market'})
    
    # Convert from wide to long format
    # Stack all columns except the first one (Market)
    df_long = df.melt(id_vars='Market', var_name=['ForecastType', 'Location'], value_name='Volume')
    
    # Convert Volume to numeric
    df_long['Volume'] = pd.to_numeric(df_long['Volume'], errors='coerce')
    
    # Remove nulls and zeros
    df_long = df_long.dropna(subset=['Volume'])
    df_long = df_long[df_long['Volume'] > 0]
    
    # Map Forecast Types to Warehouse Roles
    role_mapping = {
        'Forecast at LDC': 'LDCs',
        'Forecast at LDC (area split)': 'LDCs',
        'Forecast at RDC': 'RDCs',
        'Forecast at Factory Warehouse': 'FWs'
    }
    df_long['Wh_Role'] = df_long['ForecastType'].map(role_mapping)
    
    # Remove unmapped roles
    df_long = df_long.dropna(subset=['Wh_Role'])
    
    # Aggregate (to combine LDC + LDC split)
    df_agg = df_long.groupby(['Market', 'Wh_Role', 'Location'])['Volume'].sum().reset_index()
    
    return df_agg


@st.cache_data
def load_locations(file_source):
    """Load the locations table"""
    df = pd.read_excel(file_source)
    
    # The first column (index 0) has the full location name like "AE01 - Goodyear Dubai"
    # The second column is the location code like "AE01"
    df.columns = ['FullName', 'Location', 'StreetAddress', 'POBox', 'PostalCode', 'City', 'Country']
    
    return df


@st.cache_data
def geocode_locations(df_loc, max_locations=50):
    """Geocode locations using City and Country"""
    
    df_loc = df_loc.head(max_locations).copy()
    
    headers = {'User-Agent': 'GoodYearDistributionApp/1.0'}
    latitudes, longitudes = [], []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total = len(df_loc)
    for idx, row in df_loc.iterrows():
        status_text.text(f"🌐 Geocoding {idx+1}/{total}: {row['City']}, {row['Country']}")
        progress_bar.progress((idx + 1) / total)
        
        city = str(row['City']) if pd.notna(row['City']) else ''
        country = str(row['Country']) if pd.notna(row['Country']) else ''
        
        query = f"{city}, {country}".strip()
        url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
        
        try:
            r = requests.get(url, headers=headers, timeout=5).json()
            if r:
                latitudes.append(float(r[0]['lat']))
                longitudes.append(float(r[0]['lon']))
            else:
                latitudes.append(None)
                longitudes.append(None)
        except:
            latitudes.append(None)
            longitudes.append(None)
        
        time.sleep(1)  # Respect API rate limit
    
    progress_bar.empty()
    status_text.empty()
    
    df_loc['lat'] = latitudes
    df_loc['lon'] = longitudes
    
    return df_loc


# --- 3. SIDEBAR CONTROLS ---
st.sidebar.title("⚙️ Settings")

# File paths
pivot_file = "Cube - EMEA SC SE - FC Distribution to Warehouse - V1.xlsx"
locations_file = "locations_info.xlsx"

# Check if files exist
if not os.path.exists(pivot_file):
    st.error(f"❌ Data file not found: {pivot_file}")
    st.stop()

if not os.path.exists(locations_file):
    st.error(f"❌ Locations file not found: {locations_file}")
    st.stop()

# Map settings
st.sidebar.header("🗺️ Map Settings")
enable_map = st.sidebar.checkbox("Enable Interactive Map", value=True)

if enable_map:
    max_locations = st.sidebar.slider(
        "Locations to geocode",
        min_value=10,
        max_value=100,
        value=40,
        step=5,
        help="More locations = longer load time"
    )
    
    est_time = max_locations * 1.2
    st.sidebar.info(f"⏱️ Est. time: ~{int(est_time)}s")
else:
    max_locations = 0


# --- 4. LOAD DATA ---
st.title("🌍 EMEA Distribution Network")

with st.spinner("📊 Loading data..."):
    df_data = load_and_process_data(pivot_file)
    df_locations = load_locations(locations_file)

if df_data.empty:
    st.error("❌ No data loaded. Check Excel file format.")
    st.stop()

st.sidebar.success(f"✅ Loaded {len(df_data)} data rows")
st.sidebar.success(f"✅ Loaded {len(df_locations)} locations")

# Color scheme
color_map = {'FWs': '#D8BFD8', 'RDCs': '#FFCCCB', 'LDCs': '#FFFFE0'}


# --- 5. CHART 1: Market Overview ---
st.header("📊 1. Volume by Market and Warehouse Role")

df_market_role = df_data.groupby(['Market', 'Wh_Role'])['Volume'].sum().reset_index()

fig1 = px.bar(
    df_market_role, 
    x='Market', 
    y='Volume', 
    color='Wh_Role',
    color_discrete_map=color_map,
    title='Distribution Overview by Market',
    barmode='stack'
)
fig1.update_layout(height=500, xaxis_tickangle=-45)
st.plotly_chart(fig1, use_container_width=True)

# Summary metrics
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("📍 Total Markets", len(df_data['Market'].unique()))
with col2:
    st.metric("📦 Total Volume", f"{df_data['Volume'].sum():,.0f}")
with col3:
    st.metric("🏭 Warehouse Types", len(df_data['Wh_Role'].unique()))

st.divider()


# --- 6. CHART 2: Market Drill-down ---
st.header("🔍 2. Detailed View by Market")

markets = sorted(df_data['Market'].unique())
selected_market = st.selectbox("Select a Market", markets)

df_market = df_data[df_data['Market'] == selected_market]

fig2 = px.bar(
    df_market,
    x='Location',
    y='Volume',
    color='Wh_Role',
    color_discrete_map=color_map,
    title=f'{selected_market} - Distribution by Location'
)
fig2.update_layout(height=500, xaxis_tickangle=-45)
st.plotly_chart(fig2, use_container_width=True)

st.divider()


# --- 7. MAP ---
if enable_map:
    st.header("🗺️ 3. Interactive Geographic Map")
    
    with st.spinner(f"🌐 Geocoding {max_locations} locations..."):
        df_locations_geo = geocode_locations(df_locations, max_locations)
    
    # Merge volume data with coordinates
    # Match on Location code
    df_map_data = df_data.groupby(['Location', 'Wh_Role'])['Volume'].sum().reset_index()
    
    df_map = pd.merge(
        df_locations_geo,
        df_map_data,
        on='Location',
        how='inner'
    )
    df_map = df_map.dropna(subset=['lat', 'lon'])
    
    if not df_map.empty:
        fig_map = px.scatter_mapbox(
            df_map,
            lat='lat',
            lon='lon',
            color='Wh_Role',
            size='Volume',
            hover_name='FullName',
            hover_data={'City': True, 'Country': True, 'Volume': ':,.0f', 'lat': False, 'lon': False},
            color_discrete_map=color_map,
            zoom=3,
            height=600,
            mapbox_style='carto-positron',
            title=f'Geographic Distribution ({len(df_map)} locations)'
        )
        st.plotly_chart(fig_map, use_container_width=True)
        
        st.success(f"✅ Mapped {len(df_map)} locations with volume data")
    else:
        st.warning("⚠️ No locations could be mapped. Location codes may not match between files.")
else:
    st.info("ℹ️ Map disabled. Enable in sidebar to view.")


# --- 8. DATA EXPLORER ---
st.divider()
st.header("📋 4. Data Explorer")

with st.expander("🔍 View Raw Data"):
    tab1, tab2 = st.tabs(["Volume Data", "Locations"])
    
    with tab1:
        st.dataframe(df_data.sort_values('Volume', ascending=False), use_container_width=True)
    
    with tab2:
        st.dataframe(df_locations, use_container_width=True)
