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
    """Load the Excel file - find where actual data starts"""
    
    # First, read without headers to find where data starts
    df_raw = pd.read_excel(file_source, header=None)
    
    # Find the row that contains "Market" or similar header
    data_start_row = None
    for idx, row in df_raw.iterrows():
        # Convert row to string and check for market-like content
        row_str = ' '.join(row.astype(str).values)
        if 'Forecast' in row_str or 'forecast' in row_str:
            data_start_row = idx
            break
    
    if data_start_row is None:
        # If not found, try looking for country codes or market names
        for idx, row in df_raw.iterrows():
            first_val = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ''
            # Look for patterns like country codes or "Market"
            if len(first_val) > 0 and first_val not in ['Excel Template Version', 'V1', 'nan', '']:
                data_start_row = idx
                break
    
    if data_start_row is None:
        st.error("Could not find data start in Excel file")
        st.stop()
    
    # Now read with proper headers from the identified row
    # The data has 2 header rows (Forecast Type and Location)
    df = pd.read_excel(file_source, skiprows=data_start_row, header=[0, 1])
    
    # Get the first column name (which is the Market column)
    first_col = df.columns[0]
    
    # Separate the Market column from the rest
    markets = df[first_col].copy()
    df_values = df.drop(columns=[first_col])
    
    # Clean up column names - forward fill the level 0 (Forecast Type)
    new_level0 = []
    last_valid = None
    for col in df_values.columns.get_level_values(0):
        if 'Unnamed' not in str(col) and pd.notna(col) and str(col).strip() != '':
            last_valid = col
        new_level0.append(last_valid)
    
    # Reconstruct columns
    df_values.columns = pd.MultiIndex.from_arrays([
        new_level0,
        df_values.columns.get_level_values(1)
    ], names=['ForecastType', 'Location'])
    
    # Add markets back as index
    df_values.index = markets
    df_values.index.name = 'Market'
    
    # Remove rows where Market is null or empty
    df_values = df_values[df_values.index.notna()]
    df_values = df_values[df_values.index.astype(str).str.strip() != '']
    
    # Stack to long format
    df_long = df_values.stack([0, 1]).reset_index()
    df_long.columns = ['Market', 'ForecastType', 'Location', 'Volume']
    
    # Convert Volume to numeric
    df_long['Volume'] = pd.to_numeric(df_long['Volume'], errors='coerce')
    
    # Remove nulls and zeros
    df_long = df_long.dropna(subset=['Volume'])
    df_long = df_long[df_long['Volume'] > 0]
    
    # Map Forecast Types to Warehouse Roles (flexible matching)
    df_long['Wh_Role'] = 'Unknown'
    df_long.loc[df_long['ForecastType'].astype(str).str.contains('LDC', case=False, na=False), 'Wh_Role'] = 'LDCs'
    df_long.loc[df_long['ForecastType'].astype(str).str.contains('RDC', case=False, na=False), 'Wh_Role'] = 'RDCs'
    df_long.loc[df_long['ForecastType'].astype(str).str.contains('Factory|FW', case=False, na=False), 'Wh_Role'] = 'FWs'
    
    # Remove unknown roles
    df_long = df_long[df_long['Wh_Role'] != 'Unknown']
    
    # Aggregate
    df_agg = df_long.groupby(['Market', 'Wh_Role', 'Location'])['Volume'].sum().reset_index()
    
    return df_agg


@st.cache_data
def load_locations(file_source):
    """Load the locations table"""
    df = pd.read_excel(file_source)
    
    # Get column names - handle if first column is unnamed
    columns = df.columns.tolist()
    
    # Expected structure based on your image:
    # Col 0: Full name (like "AE01 - Goodyear Dubai")
    # Col 1: Location code (like "AE01")
    # Col 2: Street address
    # Col 3: PO Box
    # Col 4: Postal Code
    # Col 5: City
    # Col 6: Country code
    
    col_mapping = {}
    for i, col in enumerate(columns):
        if i == 0:
            col_mapping[col] = 'FullName'
        elif i == 1:
            col_mapping[col] = 'Location'
        elif i == 2:
            col_mapping[col] = 'StreetAddress'
        elif i == 3:
            col_mapping[col] = 'POBox'
        elif i == 4:
            col_mapping[col] = 'PostalCode'
        elif i == 5:
            col_mapping[col] = 'City'
        elif i == 6:
            col_mapping[col] = 'Country'
    
    df.rename(columns=col_mapping, inplace=True)
    
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
        
        time.sleep(1)
    
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
        st.error("❌ No data after processing.")
        st.stop()
    
    st.sidebar.success(f"✅ {len(df_data)} data rows")
    st.sidebar.success(f"✅ {len(df_locations)} locations")

except Exception as e:
    st.error(f"❌ Error: {str(e)}")
    
    with st.expander("🐛 Debug"):
        import traceback
        st.code(traceback.format_exc())
    
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
    st.metric("📍 Markets", len(df_data['Market'].unique()))
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
        
        st.success(f"✅ Mapped {len(df_map)} locations")
    else:
        st.warning("⚠️ No locations matched between files")
else:
    st.info("ℹ️ Map disabled. Enable in sidebar to view.")


# --- 8. DATA EXPLORER ---
st.divider()
st.header("📋 4. Data Explorer")

with st.expander("🔍 View Data Tables"):
    tab1, tab2 = st.tabs(["Volume Data", "Locations"])
    
    with tab1:
        st.dataframe(df_data.sort_values('Volume', ascending=False), use_container_width=True, height=400)
    
    with tab2:
        st.dataframe(df_locations, use_container_width=True, height=400)
