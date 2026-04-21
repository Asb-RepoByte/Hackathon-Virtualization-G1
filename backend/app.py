import os
import time
import psycopg2
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)

DB_CONFIG = {
    'host': os.getenv('DB_HOST', "localhost"),
    'port': os.getenv('DB_PORT', "5432"),
    'dbname': os.getenv('POSTGRES_DB', "carbon"),
    'user': os.getenv('POSTGRES_USER', "carbon"),
    'password': os.getenv('POSTGRES_PASSWORD', "carbon")
}

print(DB_CONFIG)


def get_db_connection():
    """Connect to PostgreSQL with retry logic (up to 5 attempts, exponential backoff)."""
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            conn = psycopg2.connect(**DB_CONFIG)
            return conn
        except psycopg2.OperationalError as e:
            if attempt == max_retries:
                raise e
            wait = 2 ** attempt
            print(f"[Retry {attempt}/{max_retries}] DB connection failed, retrying in {wait}s...")
            time.sleep(wait)


@app.route('/api/categories', methods=['GET'])
def get_categories():
    """Return distinct category list."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT category FROM devices WHERE category IS NOT NULL AND category != '' ORDER BY category")
    categories = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(categories)


@app.route('/api/manufacturers', methods=['GET'])
def get_manufacturers():
    """Return distinct manufacturer list, optionally filtered by category."""
    category = request.args.get('category', '')
    conn = get_db_connection()
    cur = conn.cursor()
    if category:
        cur.execute(
            "SELECT DISTINCT manufacturer FROM devices WHERE category = %s ORDER BY manufacturer",
            (category,)
        )
    else:
        cur.execute("SELECT DISTINCT manufacturer FROM devices ORDER BY manufacturer")
    manufacturers = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(manufacturers)


@app.route('/api/devices', methods=['GET'])
def get_devices():
    """Return devices filtered by manufacturer and/or category."""
    manufacturer = request.args.get('manufacturer', '')
    category = request.args.get('category', '')
    conn = get_db_connection()
    cur = conn.cursor()

    conditions = []
    params = []
    if manufacturer:
        conditions.append("manufacturer = %s")
        params.append(manufacturer)
    if category:
        conditions.append("category = %s")
        params.append(category)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    query = f"""
        SELECT id, manufacturer, name, category, subcategory,
               gwp_total, gwp_use_ratio, gwp_manufacturing_ratio, lifetime
        FROM devices
        {where_clause}
        ORDER BY manufacturer, name
    """
    cur.execute(query, params)
    columns = ['id', 'manufacturer', 'name', 'category', 'subcategory',
               'gwp_total', 'gwp_use_ratio', 'gwp_manufacturing_ratio', 'lifetime']
    devices = [dict(zip(columns, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify(devices)


@app.route('/api/calculate', methods=['POST'])
def calculate():
    """Calculate carbon impact for a device.

    Formula:
      manufacturing_impact = gwp_total × gwp_manufacturing_ratio
      use_impact = (gwp_total × gwp_use_ratio / lifetime) × years_of_use
      total_impact = manufacturing_impact + use_impact

    Equivalences (scientifically sourced):
      - Car km: 1 km ≈ 0.21 kg CO₂ (EU average petrol, ADEME 2023)
      - Smartphone charges: 1 charge ≈ 0.008 kg CO₂ (5Wh @ EU grid)
      - Flight km: 1 km ≈ 0.255 kg CO₂ (economy, ADEME)
      - Tree-years: 1 tree absorbs ~22 kg CO₂/year
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Request body required'}), 400

    device_id = data.get('device_id')
    years_of_use = data.get('years_of_use')

    if device_id is None or years_of_use is None:
        return jsonify({'error': 'device_id and years_of_use are required'}), 400

    try:
        years_of_use = float(years_of_use)
    except (ValueError, TypeError):
        return jsonify({'error': 'years_of_use must be a number'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT manufacturer, name, category, subcategory, gwp_total, gwp_use_ratio, gwp_manufacturing_ratio, lifetime FROM devices WHERE id = %s",
        (device_id,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({'error': 'Device not found'}), 404

    manufacturer, name, category, subcategory, gwp_total, gwp_use_ratio, gwp_manufacturing_ratio, lifetime = row

    manufacturing_impact = gwp_total * gwp_manufacturing_ratio
    use_impact = (gwp_total * gwp_use_ratio / lifetime) * years_of_use
    total_impact = manufacturing_impact + use_impact

    # Equivalences
    car_km = total_impact / 0.21           # EU average car: 0.21 kg CO₂/km
    smartphone_charges = total_impact / 0.008  # ~5Wh charge at EU grid mix
    flight_km = total_impact / 0.255       # Economy flight ADEME
    tree_years = total_impact / 22.0       # Tree absorbs ~22 kg CO₂/year

    return jsonify({
        'device': {
            'manufacturer': manufacturer,
            'name': name,
            'category': category,
            'subcategory': subcategory,
            'gwp_total': gwp_total,
            'gwp_use_ratio': gwp_use_ratio,
            'gwp_manufacturing_ratio': gwp_manufacturing_ratio,
            'lifetime': lifetime
        },
        'years_of_use': years_of_use,
        'manufacturing_impact': round(manufacturing_impact, 2),
        'use_impact': round(use_impact, 2),
        'total_impact': round(total_impact, 2),
        'equivalences': {
            'car_km': round(car_km, 0),
            'smartphone_charges': round(smartphone_charges, 0),
            'flight_km': round(flight_km, 0),
            'tree_years': round(tree_years, 1)
        }
    })


@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
