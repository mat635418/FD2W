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
    
    # Read with multi-index columns (2 header rows after skipping 8 rows)
    df = pd.read_excel(file_source, skiprows=8, header=[0, 1])
    
    # Get column levels
    level0 = df.columns.get_level_values(0)
    level1 = df.columns.get_level_values(1)
    
    # First column pair contains the market names - extract it
    first_col_tuple = df.columns[0]
    
    # Rename first column to 'Market' by creating new columns
    new_columns = [('Market', 'Market') if i == 0 else col for i, col in enumerate(df.columns)]
    df.columns = pd.MultiIndex.from_tuples(new_columns)
    
    # Set Market as index
    df = df.set_index(('Market', 'Market'))
    df.index.name = 'Market'
    
    # Now stack the multi-level columns
    df_long = df.stack([0, 1]).reset_index()
    df_long.columns = ['Market', 'ForecastType', 'Location', 'Volume']
    
    # Convert Volume to numeric
    df_long['Volume'] = pd.to_numeric(df_long['Volume'], errors='coerce')
    
    # Remove nulls and zeros
    df_long = df_long.dropna(subset=['Volume'])
    df_long = df_long[df_long['Volume'] > 0]
    
    # Clean up ForecastType names (remove "Unnamed" entries)
    df_long['ForecastType'] = df_long['ForecastType'].str.replace(r'Unnamed.*', '', regex=True).str.strip()
    
    # Map Forecast Types to Warehouse Roles
    df_long['Wh_Role'] = 'Unknown'
    df_long.loc[df_long['ForecastType'].str.contains('LDC', case=False, na=False), 'Wh_Role'] = 'LDCs'
    df_long.loc[df_long['ForecastType'].str.contains('RDC', case=False, na=False), 'Wh_Role'] = 'RDCs'
    df_long.loc[df_long['ForecastType'].str.contains('Factory|FW', case=False, na=False), 'Wh_Role'] = 'FWs'
    
    # Remove unknown roles
    df_long = df_long[df_long['Wh_Role'] != 'Unknown']
    
    # Aggregate
    df_agg = df_long.groupby(['Market', 'Wh_Role', 'Location'])['Volume'].sum().reset_index()
    
    return df_agg


@st.cache_data
def load_locations(file_source):
    """Load the locations table"""
    df = pd.read_excel(file_source)
    
    # Rename columns to what we expect
    # Column 0: Full name, Column 1: Code, etc.
    col_names = ['FullName', 'Location', 'StreetAddress', 'POBox', 'PostalCode', 'City', 'Country']
    
    # Only rename if we have enough columns
    if len(df.columns) >= len(col_names):
        df.columns = col_names + list(df.columns[len(col_names):])
    else:
        # Try to identify key columns
        for i, col in enumerate(df.columns):
            if i == 0:
                df.rename(columns={col: 'FullName'}, inplace=True)
            elif i == 1:
                df.rename(columns={col: 'Location'}, inplace=True)
    
    return df


@st.cache_data
def geocode_locations(df_loc, max_locations=50):
    """Geocode locations using City and Country"""
    
    if 'City' not in df_loc.columns or 'Country' not in df_loc.columns:
        st.warning("City or Country column not found in locations file")
        return df_loc
    
    df_loc = df_loc.head(max_locations).copy()
    
    headers = {'User-Agent': 'GoodYearDistributionApp/1.0'}
    latitudes, longitudes = [], []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total = len(df_loc)
    for idx, row in df_loc.iterrows():
        city = str(row.get('City', '')) if pd.notna(row.get('City')) else ''
        country = str(row.get('Country', '')) if pd.notna(row.get('Country')) else ''
        
        status_text.text(f"🌐 Geocoding {idx+1}/{total}: {city}, {country}")
        progress_bar.progress((idx + 1) / total)
        
        query = f"{city}, {country}".strip(', ')
        
        if not query:
            latitudes.append(None)
            longitudes.append(None)
            continue
        
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
    
    valid = df_loc[['lat', 'lon']].notna().all(axis=1).sum()
    st.sidebar.success(f"✅ Geocoded: {valid}/{len(df_loc)} locations")
    
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
enable_map = st.sidebar.checkbox("Enable Interactive Map", value=False)

if enable_map:
    max_locations = st.sidebar.slider(
        "Locations to geocode",
        min_value=10,
        max_value=100,
        value=30,
        step=5,
        help="More locations = longer load time (~1s each)"
    )
    
    est_time = max_locations * 1.2
    st.sidebar.info(f"⏱️ Est. time: ~{int(est_time)}s")
else:
    max_locations = 0


# --- 4. LOAD DATA ---
st.title("🌍 EMEA Distribution Network")

try:
    with st.spinner("📊 Loading data..."):
        df_data = load_and_process_data(pivot_file)
        df_locations = load_locations(locations_file)
    
    if df_data.empty:
        st.error("❌ No data after processing. Please check the Excel file structure.")
        
        # Debug: show raw file structure
        with st.expander("🔍 Debug: Raw File Structure"):
            raw_df = pd.read_excel(pivot_file, skiprows=8, header=[0, 1], nrows=5)
            st.write("**Column structure:**")
            st.write(raw_df.columns.tolist())
            st.write("**First few rows:**")
            st.dataframe(raw_df)
        st.stop()
    
    st.sidebar.success(f"✅ Loaded {len(df_data)} data rows")
    st.sidebar.success(f"✅ Loaded {len(df_locations)} locations")

except Exception as e:
    st.error(f"❌ Error loading data: {str(e)}")
    
    # Show more debug info
    with st.expander("🐛 Debug Information"):
        st.write("**Error details:**")
        st.code(str(e))
        
        try:
            st.write("**Trying to read first few rows of data file:**")
            debug_df = pd.read_excel(pivot_file, nrows=15)
            st.dataframe(debug_df)
        except:
            st.write("Could not read data file at all")
        
        try:
            st.write("**Trying to read locations file:**")
            debug_loc = pd.read_excel(locations_file, nrows=10)
            st.dataframe(debug_loc)
        except:
            st.write("Could not read locations file at all")
    
    st.stop()


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
        
        with st.expander("🔍 Debug: Location Matching"):
            st.write("**Locations in volume data:**")
            st.write(sorted(df_data['Location'].unique())[:20])
            st.write("**Locations in location file:**")
            st.write(sorted(df_locations['Location'].unique())[:20])
else:
    st.info("ℹ️ Map disabled. Enable in sidebar to view.")


# --- 8. DATA EXPLORER ---
st.divider()
st.header("📋 4. Data Explorer")

with st.expander("🔍 View Raw Data"):
    tab1, tab2 = st.tabs(["Volume Data", "Locations"])
    
    with tab1:
        st.dataframe(df_data.sort_values('Volume', ascending=False), use_container_width=True, height=400)
    
    with tab2:
        st.dataframe(df_locations, use_container_width=True, height=400)
