-- Staging table matching all CSV columns
CREATE TABLE devices_staging (
    manufacturer VARCHAR(200),
    name VARCHAR(500),
    category VARCHAR(100),
    subcategory VARCHAR(100),
    gwp_total VARCHAR(50),
    gwp_use_ratio VARCHAR(50),
    yearly_tec VARCHAR(50),
    lifetime VARCHAR(50),
    use_location VARCHAR(50),
    report_date VARCHAR(200),
    sources TEXT,
    sources_hash VARCHAR(200),
    gwp_error_ratio VARCHAR(50),
    gwp_manufacturing_ratio VARCHAR(50),
    weight VARCHAR(50),
    assembly_location VARCHAR(100),
    screen_size VARCHAR(50),
    server_type VARCHAR(50),
    hard_drive VARCHAR(200),
    memory VARCHAR(50),
    number_cpu VARCHAR(50),
    height VARCHAR(50),
    added_date VARCHAR(50),
    add_method VARCHAR(100),
    gwp_transport_ratio VARCHAR(50),
    gwp_eol_ratio VARCHAR(50),
    gwp_electronics_ratio VARCHAR(50),
    gwp_battery_ratio VARCHAR(50),
    gwp_hdd_ratio VARCHAR(50),
    gwp_ssd_ratio VARCHAR(50),
    gwp_othercomponents_ratio VARCHAR(50),
    comment TEXT
);

-- Load all CSV data into staging
COPY devices_staging FROM '/docker-entrypoint-initdb.d/boavizta-data-us.csv'
DELIMITER ','
CSV HEADER;

-- Final table
CREATE TABLE devices (
    id SERIAL PRIMARY KEY,
    manufacturer VARCHAR(200),
    name VARCHAR(500),
    category VARCHAR(100),
    subcategory VARCHAR(100),
    gwp_total FLOAT,
    gwp_use_ratio FLOAT,
    gwp_manufacturing_ratio FLOAT,
    lifetime FLOAT
);

-- Insert with logic for missing values
INSERT INTO devices (manufacturer, name, category, subcategory, gwp_total, gwp_use_ratio, gwp_manufacturing_ratio, lifetime)
SELECT
    manufacturer,
    name,
    category,
    subcategory,
    -- GWP Total must exist
    gwp_total::FLOAT,
    -- Use Ratio: default to 0.2 if missing
    CASE 
        WHEN gwp_use_ratio = '' OR gwp_use_ratio IS NULL THEN 0.2 
        ELSE gwp_use_ratio::FLOAT 
    END,
    -- Manufacturing Ratio: infer from use ratio if missing
    CASE
        WHEN gwp_manufacturing_ratio != '' AND gwp_manufacturing_ratio IS NOT NULL THEN gwp_manufacturing_ratio::FLOAT
        WHEN (gwp_use_ratio != '' AND gwp_use_ratio IS NOT NULL) THEN (1.0 - gwp_use_ratio::FLOAT - 0.05) -- Subtract small transport/eol buffer
        ELSE 0.75 -- Default
    END,
    -- Lifetime: category/subcategory based defaults if missing
    CASE
        WHEN lifetime != '' AND lifetime IS NOT NULL THEN lifetime::FLOAT
        WHEN subcategory ILIKE '%Laptop%' THEN 4.0
        WHEN subcategory ILIKE '%Desktop%' THEN 5.0
        WHEN subcategory ILIKE '%Monitor%' THEN 6.0
        WHEN subcategory ILIKE '%Smartphone%' THEN 2.5
        WHEN subcategory ILIKE '%Tablet%' THEN 3.0
        WHEN subcategory ILIKE '%Server%' THEN 5.0
        ELSE 4.0
    END
FROM devices_staging
WHERE gwp_total != '' AND gwp_total IS NOT NULL;

-- Cleanup
DROP TABLE devices_staging;
