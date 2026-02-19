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


# --- 2. DATA PROCESSING ---
@st.cache_data
def load_and_process_data(file_source):
    # Load Pivot Data from Excel
    df_pivot = pd.read_excel(file_source, skiprows=8, header=[0,1])
    
    # Fix multi-index columns
    level0 = df_pivot.columns.get_level_values(0).to_series()
    level0 = level0.replace(r'^Unnamed:.*', pd.NA, regex=True).ffill()
    df_pivot.columns = pd.MultiIndex.from_arrays([level0, df_pivot.columns.get_level_values(1)])
    
    # Set first column as Market
    first_col = df_pivot.columns[0]
    df_pivot = df_pivot.set_index(first_col)
    df_pivot.index.name = 'Market'
    df_pivot.columns.names = ['ForecastType', 'Location']
    
    # Flatten MultiIndex
    df_long = df_pivot.stack(level=['ForecastType', 'Location'], future_stack=True).reset_index(name='Volume')
    
    # FIX: Convert Volume to numeric, forcing errors (like spaces or strings) to NaN
    df_long['Volume'] = pd.to_numeric(df_long['Volume'], errors='coerce')
    
    # Clean data: drop NAs and zero volume
    df_long = df_long.dropna(subset=['Volume'])
    df_long = df_long[df_long['Volume'] > 0]
    
    # Map ForecastType to Warehouse Roles
    role_mapping = {
        'Forecast at LDC': 'LDCs',
        'Forecast at LDC (area split)': 'LDCs',
        'Forecast at RDC': 'RDCs',
        'Forecast at Factory Warehouse': 'FWs'
    }
    df_long['Wh_Role'] = df_long['ForecastType'].map(role_mapping)
    
    # Combine LDC + LDC Split
    df_agg = df_long.groupby(['Market', 'Wh_Role', 'Location'])['Volume'].sum().reset_index()
    return df_agg

@st.cache_data
def load_locations_and_geocode(file_source):
    df_loc = pd.read_excel(file_source)
    
    # Robust column naming
    if 'location' in df_loc.columns:
        df_loc.rename(columns={'location': 'Location'}, inplace=True)
    else:
        df_loc.rename(columns={df_loc.columns[0]: 'Location'}, inplace=True)
    
    headers = {'User-Agent': 'GoodYearDistributionApp/1.1'}
    latitudes, longitudes = [], []
    
    for _, row in df_loc.iterrows():
        # Get address parts safely
        street = str(row.get('street address', '')) if pd.notna(row.get('street address')) else ''
        city = str(row.get('City', '')) if pd.notna(row.get('City')) else ''
        country = str(row.get('iso Country', '')) if pd.notna(row.get('iso Country')) else ''
        
        query = f"{street} {city} {country}".strip()
        url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
        
        try:
            r = requests.get(url, headers=headers).json()
            if r:
                latitudes.append(float(r[0]['lat']))
                longitudes.append(float(r[0]['lon']))
            else:
                # Fallback to just City/Country
                query_fb = f"{city} {country}".strip()
                r_fb = requests.get(f"https://nominatim.openstreetmap.org/search?q={query_fb}&format=json&limit=1", headers=headers).json()
                if r_fb:
                    latitudes.append(float(r_fb[0]['lat']))
                    longitudes.append(float(r_fb[0]['lon']))
                else:
                    latitudes.append(None); longitudes.append(None)
        except:
            latitudes.append(None); longitudes.append(None)
            
        time.sleep(1) # Required by Nominatim
        
    df_loc['lat'] = latitudes
    df_loc['lon'] = longitudes
    return df_loc


# --- 3. UI LAYOUT ---
st.set_page_config(page_title="EMEA SC SE Network", layout="wide")
st.title("EMEA Distribution Network")

if "data_loaded" not in st.session_state:
    st.session_state["data_loaded"] = False

# Repo filenames
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
    st.info("Please select a data source to begin.")
    st.stop()

# --- 4. DASHBOARD ---
with st.spinner("Processing data..."):
    df_data = load_and_process_data(st.session_state['pivot_file'])
    df_locations = load_locations_and_geocode(st.session_state['loc_file'])

color_map = {'FWs': '#D8BFD8', 'RDCs': '#FFCCCB', 'LDCs': '#FFFFE0'}

# Section 1: Overall Network
st.header("1. EMEA Network View (Market & Wh Role)")
df_market_role = df_data.groupby(['Market', 'Wh_Role'])['Volume'].sum().reset_index()
fig1 = px.bar(df_market_role, x='Market', y='Volume', color='Wh_Role', 
              color_discrete_map=color_map, barmode='stack')
st.plotly_chart(fig1, use_container_width=True)

# Section 2: Market Selection
st.divider()
st.header("2. Specific Market Drill-down")
selected_market = st.selectbox("Select a Market", sorted(df_data['Market'].unique()))
df_market = df_data[df_data['Market'] == selected_market]
fig2 = px.bar(df_market, x='Location', y='Volume', color='Wh_Role',
              color_discrete_map=color_map, title=f"Location Detail: {selected_market}")
st.plotly_chart(fig2, use_container_width=True)

# Section 3: Geographic Map
st.divider()
st.header("3. Geographical Network Map")
df_map_agg = df_data.groupby(['Location', 'Wh_Role'])['Volume'].sum().reset_index()
df_map = pd.merge(df_locations, df_map_agg, on='Location', how='inner').dropna(subset=['lat', 'lon'])

if not df_map.empty:
    fig_map = px.scatter_mapbox(df_map, lat='lat', lon='lon', color='Wh_Role', size='Volume',
                                hover_name='Location', color_discrete_map=color_map,
                                zoom=3, height=700, mapbox_style='carto-positron')
    st.plotly_chart(fig_map, use_container_width=True)
else:
    st.warning("Could not geocode any locations. Check your address data.")
