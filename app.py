import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time
import os

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
    # Load Pivot Data from Excel
    df_pivot = pd.read_excel(file_source, skiprows=8, header=[0,1])
    
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
    
    # Clean data: drop NAs and zero volume
    df_long = df_long.dropna(subset=['Volume'])
    df_long = df_long[df_long['Volume'] > 0]
    
    # Map ForecastType to Warehouse Roles (Summing up LDC and LDC split)
    role_mapping = {
        'Forecast at LDC': 'LDCs',
        'Forecast at LDC (area split)': 'LDCs',
        'Forecast at RDC': 'RDCs',
        'Forecast at Factory Warehouse': 'FWs'
    }
    df_long['Wh_Role'] = df_long['ForecastType'].map(role_mapping)
    
    # Group by Market, Role, and Location to combine "LDC" and "LDC split"
    df_agg = df_long.groupby(['Market', 'Wh_Role', 'Location'])['Volume'].sum().reset_index()
    return df_agg

@st.cache_data
def load_locations_and_geocode(file_source):
    # Load Location Data from Excel
    df_loc = pd.read_excel(file_source)
    df_loc.rename(columns={'Unnamed: 0': 'Location', df_loc.columns[0]: 'Location'}, inplace=True)
    
    # Geocode locations via Nominatim API using the address fields
    headers = {'User-Agent': 'GoodYearDistributionApp/1.0'}
    latitudes, longitudes = [], []
    
    for _, row in df_loc.iterrows():
        street = str(row.get('street address', '')) if pd.notna(row.get('street address')) else ''
        city = str(row.get('City', '')) if pd.notna(row.get('City')) else ''
        country = str(row.get('iso Country', '')) if pd.notna(row.get('iso Country')) else ''
        
        # Build query string
        query = f"{street} {city} {country}".strip()
        url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
        
        try:
            r = requests.get(url, headers=headers).json()
            if r:
                latitudes.append(float(r[0]['lat']))
                longitudes.append(float(r[0]['lon']))
            else:
                # Fallback to City + Country
                query_fallback = f"{city} {country}".strip()
                url_fallback = f"https://nominatim.openstreetmap.org/search?q={query_fallback}&format=json&limit=1"
                r_fallback = requests.get(url_fallback, headers=headers).json()
                if r_fallback:
                    latitudes.append(float(r_fallback[0]['lat']))
                    longitudes.append(float(r_fallback[0]['lon']))
                else:
                    latitudes.append(None)
                    longitudes.append(None)
        except Exception:
            latitudes.append(None)
            longitudes.append(None)
            
        time.sleep(1) # Delay requested by Nominatim policy
        
    df_loc['lat'] = latitudes
    df_loc['lon'] = longitudes
    return df_loc


# --- 3. DATA LOADING DASHBOARD ---
st.title("EMEA Distribution Network")

if "data_loaded" not in st.session_state:
    st.session_state["data_loaded"] = False

# Expected Hardcoded filenames on the server
pivot_name = "Cube - EMEA SC SE - FC Distribution to Warehouse - V1.xlsx"
loc_name = "locations_info.xlsx"

col1, col2 = st.columns(2)

# Option A: Pre-load files from Repo
with col1:
    st.subheader("Automated Mode")
    if st.button("Load pre-selected FD2W data"):
        if os.path.exists(pivot_name) and os.path.exists(loc_name):
            st.session_state['pivot_file'] = pivot_name
            st.session_state['loc_file'] = loc_name
            st.session_state["data_loaded"] = True
        else:
            st.error(f"Files not found on the server. Please check your GitHub repo to ensure they match exactly: `{pivot_name}` and `{loc_name}`.")

# Option B: Manual Upload
with col2:
    st.subheader("Manual Mode")
    up_pivot = st.file_uploader("Upload Pivot", type=['xlsx'])
    up_loc = st.file_uploader("Upload Locations", type=['xlsx'])
    
    if up_pivot and up_loc:
        if st.button("Process Uploaded Files"):
            st.session_state['pivot_file'] = up_pivot
            st.session_state['loc_file'] = up_loc
            st.session_state["data_loaded"] = True

# Block execution until data is loaded
if not st.session_state.get("data_loaded"):
    st.info("👈 Choose a method above to load the operational data.")
    st.stop()


# --- 4. VISUALIZATION DASHBOARD ---
with st.spinner("Processing data & fetching geographic coordinates (this takes a few seconds)..."):
    df_data = load_and_process_data(st.session_state['pivot_file'])
    df_locations = load_locations_and_geocode(st.session_state['loc_file'])

# Set Color coding
color_map = {'FWs': '#D8BFD8', 'RDCs': '#FFCCCB', 'LDCs': '#FFFFE0'}

# Visual 1: View by Market and Wh.Role level
st.header("1. Volume by Market and Warehouse Role")
df_market_role = df_data.groupby(['Market', 'Wh_Role'])['Volume'].sum().reset_index()

fig1 = px.bar(df_market_role, x='Market', y='Volume', color='Wh_Role', 
              color_discrete_map=color_map, title='Market Distribution Overview',
              barmode='stack')
st.plotly_chart(fig1, use_container_width=True)

st.divider()

# Visual 2: Drill down into a specific market
st.header("2. Detailed Split by Specific Market")
markets = df_data['Market'].unique()
selected_market = st.selectbox("Select a Market", sorted(markets))

df_market = df_data[df_data['Market'] == selected_market]
fig2 = px.bar(df_market, x='Location', y='Volume', color='Wh_Role',
              color_discrete_map=color_map, 
              title=f'Location vs Role Distribution for {selected_market}')
st.plotly_chart(fig2, use_container_width=True)

st.divider()

# Visual 3: Geographic Map
st.header("3. Interactive Distribution Map")
# Merge volumes with geocoded locations
df_map_data = df_data.groupby(['Location', 'Wh_Role'])['Volume'].sum().reset_index()
df_map = pd.merge(df_locations, df_map_data, on='Location', how='inner')
df_map = df_map.dropna(subset=['lat', 'lon'])

if not df_map.empty:
    fig_map = px.scatter_mapbox(df_map, lat='lat', lon='lon', color='Wh_Role', size='Volume',
                                hover_name='Location', hover_data=['City', 'Volume'],
                                color_discrete_map=color_map, zoom=3, height=600,
                                mapbox_style='carto-positron',
                                title='Geographical Network (Bubble Size = Volume)')
    st.plotly_chart(fig_map, use_container_width=True)
else:
    st.warning("No geographical data could be mapped. Please verify the city/country spellings in the locations file.")
