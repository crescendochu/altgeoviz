from flask import Flask, render_template, jsonify, request, session
import duckdb
import geopandas as gpd
import json
import logging
import time  

import utils

app = Flask(__name__)
app.secret_key = 'abc'

# Database connection
con = duckdb.connect(database='data/my_spatial_db.duckdb', read_only=True)
con.execute("INSTALL 'spatial';")
con.execute("LOAD 'spatial';")


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
    

    start_time = time.time()
    query_result = con.execute(query).fetchdf()

    end_time = time.time()
    
    load_time = end_time - start_time
    app.logger.debug(f"Data load time: {load_time:.3f} seconds")

    gdf = gpd.GeoDataFrame(query_result, geometry=gpd.GeoSeries.from_wkt(query_result['geom_wkt']))
    gdf.drop(columns=['geom_wkt'], inplace=True)
    
    geojson_data = json.loads(gdf.to_json())
    
    return jsonify(geojson_data)


@app.route('/state_density_data')
def state_density_data():
    accuracy = 0.001
    return fetch_density_data('state_ppl_density', accuracy)

@app.route('/county_density_data')
def county_density_data():
    accuracy = 0.0001
    return fetch_density_data('w_county_ppl_density', accuracy)

@app.route('/tract_density_data')
def tract_density_data():
    accuracy = 0.00001
    return fetch_density_data('wa_tract_ppl_density', accuracy)

@app.route('/stats_in_view')
def stats_in_view():
    minLon = request.args.get('minLon', type=float)
    minLat = request.args.get('minLat', type=float)
    maxLon = request.args.get('maxLon', type=float)
    maxLat = request.args.get('maxLat', type=float)
    
    # print(f"minLon: {minLon}, minLat: {minLat}, maxLon: {maxLon}, maxLat: {maxLat}")
    # logging.error(f"minLon: {minLon}, minLat: {minLat}, maxLon: {maxLon}, maxLat: {maxLat}")
    
    table_name = session.get('global_table_name', None)
    stats_query = f"""
    SELECT 
        GEOID, ppl_densit, c_lat, c_lon, ST_AsGeoJSON(ST_Simplify(tn.geom, 0.001)) AS geom
    FROM {table_name} AS tn
    WHERE ST_Intersects(tn.geom, ST_MakeEnvelope({minLon}, {minLat}, {maxLon}, {maxLat}));
    """
    
    result = con.execute(stats_query).fetchdf()
    
    map = utils.Map(minLon, minLat, maxLon, maxLat)
    polygons = []
    for index, row in result.iterrows():
        polygon = utils.Polygon(
            row['GEOID'], 
            float(row['ppl_densit']), 
            (float(row['c_lon']), float(row['c_lat'])),
            row['geom'])
        polygons.append(polygon)
        
    map.set_polygons(polygons)
    map.calculate_section_densities()
    map.rank_sections()
    map.find_high_density_clusters()

    print("logging ...")
    print(map.polygons)
    
    map_min = map.find_min()
    map_max = map.find_max()

    return jsonify({
        "trends": map.trends,
        "min": map_min['ppl_densit'],
        "max": map_max['ppl_densit'],
        "average": map.calculate_mean(),
        "median": map.calculate_median(),
        "highlights": {
            "min": {
                "type": "Feature",
                "properties": {
                    "geoid": map_min['geoid'],
                    "ppl_densit": map_min['ppl_densit']
                },
                "geometry": map_min['geom']
            },
            "max": {
                "type": "Feature",
                "properties": {
                    "geoid": map_max['geoid'],
                    "ppl_densit": map_max['ppl_densit']
                },
                "geometry": map_max['geom']
            }
        }
    })
    
    # return jsonify(map.trends)
   
if __name__ == '__main__':
    app.run(debug=True)
