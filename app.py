import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time

# --- 1. LOGIN MASK ---
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False

if not st.session_state["logged_in"]:
    st.title("Login to EMEA Distribution Network App")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    if st.button("Login"):
        if username == "admin" and password == "goodyear":
            st.session_state["logged_in"] = True
            st.rerun()
        else:
            st.error("Invalid credentials. Please try again.")
    st.stop()

# --- 2. DATA PROCESSING ---
@st.cache_data
def load_and_process_data():
    # Load Pivot Data
    df_pivot = pd.read_csv('Cube - EMEA SC SE - FC Distribution to Warehouse - V1.xlsx - Pivot.csv', skiprows=8, header=[0,1])
    
    # Fix pandas multi-index columns for merged header cells
    level0 = df_pivot.columns.get_level_values(0).to_series()
    level0 = level0.replace(r'^Unnamed:.*', pd.NA, regex=True).ffill()
    df_pivot.columns = pd.MultiIndex.from_arrays([level0, df_pivot.columns.get_level_values(1)])
    
    # Rename the first column which contains the market names
    cols = list(df_pivot.columns)
    cols[0] = ('Market', 'Market')
    df_pivot.columns = pd.MultiIndex.from_tuples(cols)
    
    # Melt dataframe to long format
    df_long = df_pivot.melt(id_vars=[('Market', 'Market')], var_name=['ForecastType', 'Location'], value_name='Volume')
    df_long.columns = ['Market', 'ForecastType', 'Location', 'Volume']
    
    # Clean data
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
def load_locations_and_geocode():
    # Load Location Data
    df_loc = pd.read_csv('locations_info.xlsx - Sheet1.csv')
    df_loc.rename(columns={'Unnamed: 0': 'Location'}, inplace=True)
    
    # Geocode locations via Nominatim API using the address fields
    headers = {'User-Agent': 'GoodYearDistributionApp/1.0'}
    latitudes, longitudes = [], []
    
    for _, row in df_loc.iterrows():
        street = str(row['street address']) if pd.notna(row['street address']) else ''
        city = str(row['City']) if pd.notna(row['City']) else ''
        country = str(row['iso Country']) if pd.notna(row['iso Country']) else ''
        
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

# --- 3. DASHBOARD ---
st.title("EMEA Distribution Network")

# Load Datasets
with st.spinner("Loading and processing data. Fetching geographic coordinates (this takes a few seconds)..."):
    df_data = load_and_process_data()
    df_locations = load_locations_and_geocode()

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

fig_map = px.scatter_mapbox(df_map, lat='lat', lon='lon', color='Wh_Role', size='Volume',
                            hover_name='Location', hover_data=['City', 'Volume'],
                            color_discrete_map=color_map, zoom=3, height=600,
                            mapbox_style='carto-positron',
                            title='Geographical Network (Bubble Size = Volume)')
st.plotly_chart(fig_map, use_container_width=True)
