from flask import Flask, render_template, jsonify, request, session,g
import duckdb
import geopandas as gpd
import json
import utils
import reverse_geocoder as rg
from functools import lru_cache


app = Flask(__name__)
app.secret_key = 'abc'
# app.logger.setLevel(logging.DEBUG)

# Database connection
con = duckdb.connect(database='data/my_spatial_db.duckdb', read_only=True)
con.execute("INSTALL 'spatial';")
con.execute("LOAD 'spatial';")


def get_viewport_params():
    if 'viewport' not in g:
        g.viewport = {
            'screen_left': request.args.get('screenLeft', type=float),
            'screen_right': request.args.get('screenRight', type=float),
            'screen_top': request.args.get('screenTop', type=float),
            'screen_bottom': request.args.get('screenBottom', type=float),
            'zoom_level': request.args.get('zoom', type=float)
        }
    return g.viewport


# Helper function to construct location strings based on zoom level
def construct_location(location, zoom_level):
    table = {
        "CA": "Canada",
        "PR": "Puerto Rico",
        "MX": "Mexico",
        "BS": "The Bahamas",
        "TC": "Turks and Caicos Islands",
        "VG": "British Virgin Islands",
        "CU": "Cuba",
        "": "undefined"
    }
    if zoom_level >= 7:
        return f"{location['name']}, {location['admin1']}"
    elif zoom_level >= 5:
        if location['cc'] != 'US':
            return f"{location['name']}, {table[location['cc']]}"
        return f"{location['admin2']}, {location['admin1']}"
    else:
        if location['cc'] != 'US':
            return f"{location['admin1']}, {table[location['cc']]}"
        return f"{location['admin1']}, {location['cc']}"


@app.route('/')
def index():
    # Serve the main page with the Mapbox GL JS map
    return render_template('index.html')

def fetch_density_data(table_name,accuracy):
    session["global_table_name"] = table_name
    # Get bounding box parameters from the request
    bbox = request.args.get('bbox', '')
    if bbox:
        bbox = [float(coord) for coord in bbox.split(',')]
        bbox_polygon = f"POLYGON(({bbox[0]} {bbox[1]}, {bbox[2]} {bbox[1]}, {bbox[2]} {bbox[3]}, {bbox[0]} {bbox[3]}, {bbox[0]} {bbox[1]}))"
    else:
        # Default bounding box that covers the whole world if not specified
        bbox_polygon = "POLYGON((-180 -90, 180 -90, 180 90, -180 90, -180 -90))"
    


    query = f"""
    SELECT GEOID, ppl_densit, ST_AsText(ST_Simplify(geom, {accuracy} )) AS geom_wkt
    FROM {table_name}
    WHERE ST_Intersects(geom, ST_GeomFromText('{bbox_polygon}'));
    """
    

    # start_time = time.time()
    query_result = con.execute(query).fetchdf()

    # end_time = time.time()
    
    # load_time = end_time - start_time
    # app.logger.debug(f"Data load time: {load_time:.3f} seconds")

    gdf = gpd.GeoDataFrame(query_result, geometry=gpd.GeoSeries.from_wkt(query_result['geom_wkt']))
    gdf.drop(columns=['geom_wkt'], inplace=True)
    
    geojson_data = json.loads(gdf.to_json())
    
    return jsonify(geojson_data)


@app.route('/state_density_data')
def state_density_data():
    accuracy = 0.01
    return fetch_density_data('state_ppl_density', accuracy)

@app.route('/county_density_data')
def county_density_data():
    accuracy = 0.01
    # return fetch_density_data('w_county_ppl_density', accuracy)
    return fetch_density_data('county', accuracy)

@app.route('/tract_density_data')
def tract_density_data():
    accuracy = 0.001
    return fetch_density_data('wa_tract_ppl_density', accuracy)

# @lru_cache(maxsize=1000)
def reverse_helper(lon, lat): 
    result = rg.search((lat, lon))
    return result[0]

def reverse_geocode(screen_left, screen_right, screen_top, screen_bottom, zoom_level):

    # viewport = get_viewport_params()

    # top_left_res = reverse_helper(viewport['screen_left'], viewport['screen_top'])
    # top_right_res = reverse_helper(viewport['screen_right'], viewport['screen_top'])
    # bottom_left_res = reverse_helper(viewport['screen_left'], viewport['screen_bottom'])
    # bottom_right_res = reverse_helper(viewport['screen_right'], viewport['screen_bottom'])
    top_left = (screen_top, screen_left)
    top_right = (screen_top, screen_right)
    bottom_left = (screen_bottom, screen_left)
    bottom_right = (screen_bottom, screen_right)
    
    top_left_res, top_right_res, bottom_left_res, bottom_right_res = rg.search([top_left, top_right, bottom_left, bottom_right])
    
    # Constructing location description for each corner using the helper
    top_left_location = construct_location(top_left_res, zoom_level)
    top_right_location = construct_location(top_right_res, zoom_level)
    bottom_left_location = construct_location(bottom_left_res, zoom_level)
    bottom_right_location = construct_location(bottom_right_res, zoom_level)
    
    # Construct the response
    response = f"The current view is bounded by {top_left_location} on the top-left, {top_right_location} on the top-right, {bottom_left_location} on the bottom-left, and {bottom_right_location} on the bottom-right."
    
    return response


@app.route('/stats_in_view')
def stats_in_view():

    viewport = get_viewport_params()
    min_lon = viewport['screen_left']
    min_lat = viewport['screen_bottom']
    max_lon = viewport['screen_right']
    max_lat = viewport['screen_top']
    zoom_level = viewport['zoom_level']

    # Take out the geom from this query 
    # geocode text 
    geotext = reverse_geocode(min_lon, max_lon, max_lat, min_lat, zoom_level)
    

    # Fetch the data from the map that is bounded by the min/max of longitude and latitude
    table_name = session.get('global_table_name', None)
    stats_query = f"""
    SELECT 
        GEOID, ppl_densit, c_lat, c_lon
    FROM {table_name}
    WHERE ST_Intersects(geom, ST_MakeEnvelope({min_lon}, {min_lat}, {max_lon}, {max_lat}));
    """
    
    result = con.execute(stats_query).fetchdf()

    map_instance = utils.Map(min_lon, min_lat, max_lon, max_lat)
    polygons = []
    for index, row in result.iterrows():
        polygon = utils.Polygon(
            row['GEOID'], 
            float(row['ppl_densit']), 
            (float(row['c_lon']), float(row['c_lat'])))
        polygons.append(polygon)
        
    map_instance.set_polygons(polygons)
    map_instance.calculate_section_densities()
    map_instance.rank_sections()
    map_instance.find_high_density_clusters()    
    map_min = map_instance.find_min()
    map_max = map_instance.find_max()

    return jsonify({
        "geocode": geotext, 
        "trends": map_instance.trends,
        "min": {
            "value": map_min['ppl_densit'],
            "text": construct_location(reverse_helper(map_min['centroid'][0], map_min['centroid'][1]), zoom_level),
            "section": map_min['section']
        },
        "max": {
            "value": map_max['ppl_densit'],
            "text": construct_location(reverse_helper(map_max['centroid'][0], map_max['centroid'][1]), zoom_level),
            "section": map_max['section']
        },
        "average": map_instance.calculate_mean(),
        "median": map_instance.calculate_median()
    })
    
# @app.route('/reverse_geocode')
# def reverse_geocode():

#     viewport = get_viewport_params()

#     top_left_res = reverse_helper(viewport['screen_left'], viewport['screen_top'])
#     top_right_res = reverse_helper(viewport['screen_right'], viewport['screen_top'])
#     bottom_left_res = reverse_helper(viewport['screen_left'], viewport['screen_bottom'])
#     bottom_right_res = reverse_helper(viewport['screen_right'], viewport['screen_bottom'])
    
#     # Constructing location description for each corner using the helper
#     top_left_location = construct_location(top_left_res, viewport['zoom_level'])
#     top_right_location = construct_location(top_right_res, viewport['zoom_level'])
#     bottom_left_location = construct_location(bottom_left_res, viewport['zoom_level'])
#     bottom_right_location = construct_location(bottom_right_res, viewport['zoom_level'])
    
#     # Construct the response
#     response = f"The current view is bounded by {top_left_location} on the top-left, {top_right_location} on the top-right, {bottom_left_location} on the bottom-left, and {bottom_right_location} on the bottom-right."
    
#     return jsonify({
#         "response": response
#     })


if __name__ == '__main__':
    app.run(debug=True, port=5007)
