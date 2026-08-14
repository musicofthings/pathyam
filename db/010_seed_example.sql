-- ============================================================================
-- Pathyam · 010 · Worked seed example — the dosa family
-- ============================================================================
-- A complete, runnable vertical slice: sources → nutrients → regions → ingredients
-- → composition → lexicon → parametric templates (incl. a nested sub-recipe) →
-- regional priors → serving units → GI → a consenting user → a photo log with a
-- full inference trace and a correction.
--
-- Nutrient values here are ILLUSTRATIVE PLACEHOLDERS of the right order of
-- magnitude, present so the schema and queries can be exercised. Real values load
-- from IFCT 2017 / INDB once the licences in the dossier are cleared.
-- ============================================================================

-- ---------------------------------------------------------------- sources ----
INSERT INTO ref.source (source_key, citation, doi_or_url, licence, is_commercial_cleared, notes) VALUES
 ('IFCT2017',
  'Longvah T, Ananthan R, Bhaskarachary K, Venkaiah K. Indian Food Composition Tables 2017. ICMR-NIN, Hyderabad.',
  'https://www.nin.res.in/ebooks/IFCT2017.pdf',
  'Not stated - ICMR copyright', false,
  'AWAITING WRITTEN PERMISSION. Deliberately left uncleared so it shows in ref.v_uncleared_values.'),
 ('INDB2024.11',
  'Vijayakumar A, Dubasi HB, Awasthi A, Jaacks LM. Development of an Indian Food Composition Database. Curr Dev Nutr. 2024;103790.',
  'https://www.anuvaad.org.in/indian-nutrient-databank/',
  'Described open access; no explicit licence file', false,
  'Awaiting written clarification from Anuvaad Solutions LLP.'),
 ('USDA-NRF-R6',
  'USDA Table of Nutrient Retention Factors, Release 6 (2007). USDA ARS.',
  'https://agdatacommons.nal.usda.gov/articles/dataset/24660888',
  'Public domain (US Government work)', true, NULL),
 ('USDA-FDC',
  'USDA FoodData Central. Agricultural Research Service.',
  'https://fdc.nal.usda.gov/', 'Public domain (US Government work)', true, NULL),
 ('COFID2021',
  'Composition of Foods Integrated Dataset (CoFID) 2021. Public Health England.',
  'https://www.gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid',
  'Open Government Licence v3', true, NULL),
 ('PMID:35875218',
  'Shakappa D, Naik R, Sobhana PP. Glycemic carbohydrates, glycemic index and glycemic load of commonly consumed South Indian breakfast foods. J Food Sci Technol. 2022;59(9):3619-3626.',
  'https://doi.org/10.1007/s13197-022-05368-6',
  'Journal copyright; numerical findings citable as fact', true,
  'Authors are ICMR-NIN Dept of Dietetics - the same institute that holds IFCT.'),
 ('PATHYAM-EST',
  'Pathyam internal estimate pending analytical determination.',
  NULL, 'Proprietary', true, 'Tier D placeholder values.');

-- -------------------------------------------------------------- nutrients ----
INSERT INTO ref.nutrient (infoods_tagname, name, unit, decimals, nutrient_group, is_core, display_order) VALUES
 ('ENERC_KCAL', 'Energy',                'kcal', 0, 'proximate',    true,  1),
 ('PROCNT',     'Protein',               'g',    1, 'proximate',    true,  2),
 ('FAT',        'Total fat',             'g',    1, 'lipid',        true,  3),
 ('CHOAVLDF',   'Available carbohydrate','g',    1, 'carbohydrate', true,  4),
 ('FIBTG',      'Total dietary fibre',   'g',    1, 'carbohydrate', true,  5),
 ('FASAT',      'Saturated fatty acids', 'g',    2, 'fatty_acid',   false, 6),
 ('NA',         'Sodium',                'mg',   0, 'mineral',      true,  7),
 ('K',          'Potassium',             'mg',   0, 'mineral',      true,  8),
 ('P',          'Phosphorus',            'mg',   0, 'mineral',      false, 9),
 ('FE',         'Iron',                  'mg',   2, 'mineral',      false, 10),
 ('THIA',       'Thiamine',              'mg',   3, 'vitamin',      false, 11),
 ('FOLDFE',     'Folate, DFE',           'ug',   0, 'vitamin',      false, 12);

-- ---------------------------------------------------------------- regions ----
INSERT INTO ref.region (region_key, state, sub_region, iso_3166_2) VALUES
 ('TN',           'Tamil Nadu',    NULL,         'IN-TN'),
 ('KL',           'Kerala',        NULL,         'IN-KL'),
 ('KA',           'Karnataka',     NULL,         'IN-KA'),
 ('AP',           'Andhra Pradesh',NULL,         'IN-AP'),
 ('TG',           'Telangana',     NULL,         'IN-TG');

INSERT INTO ref.region (region_key, state, sub_region, parent_region_id, iso_3166_2)
SELECT 'TN-CHETTINAD', 'Tamil Nadu', 'Chettinad', region_id, 'IN-TN'
  FROM ref.region WHERE region_key = 'TN';
INSERT INTO ref.region (region_key, state, sub_region, parent_region_id, iso_3166_2)
SELECT 'KL-MALABAR', 'Kerala', 'Malabar', region_id, 'IN-KL'
  FROM ref.region WHERE region_key = 'KL';

-- ------------------------------------------------------------ food items ----
INSERT INTO ref.food_item
  (pathyam_id, canonical_name_en, scientific_name, food_group, is_recipe,
   ifct_code, density_g_per_ml, edible_portion_pct, foodon_iri)
VALUES
 ('PY-F-000001','Rice, parboiled, milled','Oryza sativa','cereal',  false,'A007',0.850,100,
  'http://purl.obolibrary.org/obo/FOODON_03301017'),
 ('PY-F-000002','Rice, raw, milled',      'Oryza sativa','cereal',  false,'A006',0.850,100,NULL),
 ('PY-F-000003','Black gram dhal, dehusked','Vigna mungo','pulse',  false,'B015',0.820,100,NULL),
 ('PY-F-000004','Fenugreek seeds',        'Trigonella foenum-graecum','spice',false,'S018',0.780,100,NULL),
 ('PY-F-000005','Gingelly (sesame) oil',  'Sesamum indicum','fat',   false,'V004',0.920,100,NULL),
 ('PY-F-000006','Coconut oil',            'Cocos nucifera','fat',    false,'V002',0.918,100,NULL),
 ('PY-F-000007','Ghee, cow',              NULL,           'fat',     false,'V009',0.911,100,NULL),
 ('PY-F-000008','Potato, boiled',         'Solanum tuberosum','vegetable',false,'D010',0.940,100,NULL),
 ('PY-F-000009','Onion, big',             'Allium cepa',  'vegetable',false,'D021',0.960,88,NULL),
 ('PY-F-000010','Salt, iodised',          NULL,           'condiment',false,NULL, 1.200,100,NULL),
 -- dishes (is_recipe = true)
 ('PY-F-000100','Dosa, plain',            NULL,'prepared_dish', true, NULL,0.720,100,NULL),
 ('PY-F-000101','Dosa, masala',           NULL,'prepared_dish', true, NULL,0.760,100,NULL),
 ('PY-F-000102','Masala dosa filling (potato masala)', NULL,'prepared_dish',true,NULL,0.880,100,NULL);

-- ------------------------------------------------- composition (per 100 g) ----
-- Illustrative values. Tier A = from IFCT; tier C = borrowed; tier D = estimate.
INSERT INTO ref.composition_value
  (food_id, nutrient_id, value, basis, sd, n_samples, confidence, source_id, analytical_method)
SELECT f.food_id, n.nutrient_id, v.value, 'per_100g', v.sd, v.n, v.tier::ref.confidence_tier,
       s.source_id, v.method
FROM (VALUES
  -- parboiled rice
  ('PY-F-000001','ENERC_KCAL', 346.0,  6.2, 18,'A','IFCT2017','bomb calorimetry / calculated'),
  ('PY-F-000001','PROCNT',       7.81, 0.42,18,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000001','FAT',          0.52, 0.09,18,'A','IFCT2017','Soxhlet'),
  ('PY-F-000001','CHOAVLDF',    74.80, 1.10,18,'A','IFCT2017','difference'),
  ('PY-F-000001','FIBTG',        2.81, 0.31,18,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000001','K',          115.00,12.00,18,'A','IFCT2017','ICP-OES'),
  ('PY-F-000001','P',          125.00,10.00,18,'A','IFCT2017','ICP-OES'),
  ('PY-F-000001','THIA',         0.28, 0.03,18,'A','IFCT2017','HPLC'),
  ('PY-F-000001','NA',           2.00, 0.50,18,'A','IFCT2017','ICP-OES'),
  -- raw milled rice (lower thiamine - the parboiling difference)
  ('PY-F-000002','ENERC_KCAL', 356.0,  5.8, 18,'A','IFCT2017','calculated'),
  ('PY-F-000002','PROCNT',       7.94, 0.38,18,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000002','FAT',          0.52, 0.08,18,'A','IFCT2017','Soxhlet'),
  ('PY-F-000002','CHOAVLDF',    78.20, 0.95,18,'A','IFCT2017','difference'),
  ('PY-F-000002','FIBTG',        2.02, 0.22,18,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000002','K',           90.00,10.00,18,'A','IFCT2017','ICP-OES'),
  ('PY-F-000002','THIA',         0.05, 0.01,18,'A','IFCT2017','HPLC'),
  ('PY-F-000002','NA',           2.00, 0.40,18,'A','IFCT2017','ICP-OES'),
  -- black gram dhal
  ('PY-F-000003','ENERC_KCAL', 341.0,  7.1, 12,'A','IFCT2017','calculated'),
  ('PY-F-000003','PROCNT',      23.02, 0.90,12,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000003','FAT',          1.64, 0.18,12,'A','IFCT2017','Soxhlet'),
  ('PY-F-000003','CHOAVLDF',    52.10, 1.40,12,'A','IFCT2017','difference'),
  ('PY-F-000003','FIBTG',       15.60, 1.20,12,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000003','K',          983.00,45.00,12,'A','IFCT2017','ICP-OES'),
  ('PY-F-000003','P',          320.00,22.00,12,'A','IFCT2017','ICP-OES'),
  ('PY-F-000003','FE',           3.65, 0.40,12,'A','IFCT2017','ICP-OES'),
  ('PY-F-000003','FOLDFE',     216.00,18.00,12,'A','IFCT2017','microbiological'),
  ('PY-F-000003','NA',           8.00, 1.50,12,'A','IFCT2017','ICP-OES'),
  -- fenugreek
  ('PY-F-000004','ENERC_KCAL', 328.0,  9.0,  6,'A','IFCT2017','calculated'),
  ('PY-F-000004','PROCNT',      25.40, 1.10, 6,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000004','FAT',          5.90, 0.60, 6,'A','IFCT2017','Soxhlet'),
  ('PY-F-000004','CHOAVLDF',    33.80, 2.00, 6,'A','IFCT2017','difference'),
  ('PY-F-000004','FIBTG',       48.00, 3.00, 6,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000004','K',          770.00,50.00, 6,'A','IFCT2017','ICP-OES'),
  ('PY-F-000004','NA',          67.00, 6.00, 6,'A','IFCT2017','ICP-OES'),
  -- fats
  ('PY-F-000005','ENERC_KCAL', 900.0,  0.0,  6,'A','IFCT2017','calculated'),
  ('PY-F-000005','FAT',        100.00, 0.00, 6,'A','IFCT2017','gravimetric'),
  ('PY-F-000005','FASAT',       14.20, 0.60, 6,'A','IFCT2017','GC-FID'),
  ('PY-F-000005','NA',           0.00, 0.00, 6,'A','IFCT2017','ICP-OES'),
  ('PY-F-000006','ENERC_KCAL', 900.0,  0.0,  6,'A','IFCT2017','calculated'),
  ('PY-F-000006','FAT',        100.00, 0.00, 6,'A','IFCT2017','gravimetric'),
  ('PY-F-000006','FASAT',       87.50, 1.20, 6,'A','IFCT2017','GC-FID'),
  ('PY-F-000006','NA',           0.00, 0.00, 6,'A','IFCT2017','ICP-OES'),
  ('PY-F-000007','ENERC_KCAL', 900.0,  0.0,  6,'A','IFCT2017','calculated'),
  ('PY-F-000007','FAT',        100.00, 0.00, 6,'A','IFCT2017','gravimetric'),
  ('PY-F-000007','FASAT',       62.00, 1.80, 6,'A','IFCT2017','GC-FID'),
  ('PY-F-000007','NA',           0.00, 0.00, 6,'A','IFCT2017','ICP-OES'),
  -- vegetables
  ('PY-F-000008','ENERC_KCAL',  87.0,  3.0, 10,'A','IFCT2017','calculated'),
  ('PY-F-000008','PROCNT',       1.87, 0.20,10,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000008','FAT',          0.10, 0.03,10,'A','IFCT2017','Soxhlet'),
  ('PY-F-000008','CHOAVLDF',    18.40, 0.90,10,'A','IFCT2017','difference'),
  ('PY-F-000008','FIBTG',        1.80, 0.20,10,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000008','K',          380.00,30.00,10,'A','IFCT2017','ICP-OES'),
  ('PY-F-000008','P',           57.00, 6.00,10,'A','IFCT2017','ICP-OES'),
  ('PY-F-000008','NA',           6.00, 1.00,10,'A','IFCT2017','ICP-OES'),
  ('PY-F-000009','ENERC_KCAL',  46.0,  2.5, 10,'A','IFCT2017','calculated'),
  ('PY-F-000009','PROCNT',       1.20, 0.15,10,'A','IFCT2017','Kjeldahl'),
  ('PY-F-000009','FAT',          0.10, 0.02,10,'A','IFCT2017','Soxhlet'),
  ('PY-F-000009','CHOAVLDF',     9.34, 0.60,10,'A','IFCT2017','difference'),
  ('PY-F-000009','FIBTG',        2.10, 0.25,10,'A','IFCT2017','AOAC 991.43'),
  ('PY-F-000009','K',          146.00,14.00,10,'A','IFCT2017','ICP-OES'),
  ('PY-F-000009','NA',           4.00, 1.00,10,'A','IFCT2017','ICP-OES'),
  ('PY-F-000010','NA',       38758.00, 0.00, 3,'C','COFID2021','borrowed - stoichiometric')
) AS v(pid, tag, value, sd, n, tier, src, method)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.nutrient  n ON n.infoods_tagname = v.tag
JOIN ref.source    s ON s.source_key = v.src;

-- A genuine BORROWED value, to exercise the provenance chain and the tier-C rules.
-- Seed the parboiled-rice folate figure first...
INSERT INTO ref.composition_value
  (food_id, nutrient_id, value, basis, sd, n_samples, confidence, source_id, analytical_method)
SELECT f.food_id, n.nutrient_id, 8.00, 'per_100g', 1.20, 18, 'A', s.source_id, 'microbiological'
  FROM ref.food_item f, ref.nutrient n, ref.source s
 WHERE f.pathyam_id = 'PY-F-000001'
   AND n.infoods_tagname = 'FOLDFE'
   AND s.source_key = 'IFCT2017';

INSERT INTO ref.composition_value
  (food_id, nutrient_id, value, basis, confidence, source_id, is_borrowed,
   borrowed_from_food_id, analytical_method, notes)
SELECT raw.food_id, n.nutrient_id, 8.00, 'per_100g', 'C', s.source_id, true,
       par.food_id, 'borrowed - no analytical value for raw milled rice',
       'Flagged in the UI as estimated. Excluded from clinical outputs by default.'
  FROM ref.food_item raw, ref.food_item par, ref.nutrient n, ref.source s
 WHERE raw.pathyam_id = 'PY-F-000002'
   AND par.pathyam_id = 'PY-F-000001'
   AND n.infoods_tagname = 'FOLDFE'
   AND s.source_key = 'PATHYAM-EST';

-- ---------------------------------------------------------------- lexicon ----
INSERT INTO ref.food_name (food_id, lang, name_native, name_roman, is_primary)
SELECT f.food_id, v.lang, v.native, v.roman, v.prim
FROM (VALUES
 ('PY-F-000100','en',NULL,          'Dosa, plain',  true),
 ('PY-F-000100','ta','தோசை',        'dosai',        true),
 ('PY-F-000100','ta',NULL,          'thosai',       false),
 ('PY-F-000100','te','దోస',          'dosa',         true),
 ('PY-F-000100','ml','ദോശ',         'dosha',        true),
 ('PY-F-000100','kn','ದೋಸೆ',        'dose',         true),
 ('PY-F-000100','kn',NULL,          'dosey',        false),
 ('PY-F-000101','en',NULL,          'Masala dosa',  true),
 ('PY-F-000101','ta','மசாலா தோசை',  'masala dosai', true),
 ('PY-F-000101','kn','ಮಸಾಲೆ ದೋಸೆ',  'masale dose',  true),
 ('PY-F-000101','ml','മസാല ദോശ',    'masala dosha', true),
 ('PY-F-000101','te','మసాలా దోస',    'masala dosa',  true),
 ('PY-F-000001','en',NULL,          'Parboiled rice', true),
 ('PY-F-000001','ta','புழுங்கல் அரிசி','puzhungal arisi', true),
 ('PY-F-000001','ml','പുഴുങ്ങലരി',   'puzhungalari', true),
 ('PY-F-000003','en',NULL,          'Urad dal',     true),
 ('PY-F-000003','ta','உளுந்து',      'ulundhu',      true),
 ('PY-F-000003','kn','ಉದ್ದಿನ ಬೇಳೆ',  'uddina bele',  true),
 ('PY-F-000005','en',NULL,          'Gingelly oil', true),
 ('PY-F-000005','ta','நல்லெண்ணெய்',  'nallennai',    true)
) AS v(pid, lang, native, roman, prim)
JOIN ref.food_item f ON f.pathyam_id = v.pid;

-- ------------------------------------------- retention and yield factors ----
INSERT INTO ref.retention_factor (food_group, cooking_method, nutrient_id, pct_retained, source_id)
SELECT v.grp, v.method, n.nutrient_id, v.pct, s.source_id
FROM (VALUES
 ('cereal','griddled','THIA',   70.0),
 ('cereal','griddled','FOLDFE', 75.0),
 ('cereal','griddled','K',      95.0),
 ('pulse', 'griddled','THIA',   75.0),
 ('pulse', 'griddled','FOLDFE', 70.0),
 ('pulse', 'griddled','FE',     95.0),
 ('vegetable','boiled','K',     70.0),
 ('vegetable','boiled','THIA',  65.0),
 ('vegetable','boiled','FOLDFE',60.0)
) AS v(grp, method, tag, pct)
JOIN ref.nutrient n ON n.infoods_tagname = v.tag
JOIN ref.source   s ON s.source_key = 'USDA-NRF-R6';

INSERT INTO ref.yield_factor (food_group, cooking_method, factor, source_id)
SELECT v.grp, v.method, v.f, s.source_id
FROM (VALUES ('cereal','griddled',0.82),('vegetable','boiled',0.95),('pulse','griddled',0.85))
     AS v(grp, method, f)
JOIN ref.source s ON s.source_key = 'USDA-NRF-R6';

-- ============================================================================
-- PARAMETRIC TEMPLATES — the worked example
-- ============================================================================

INSERT INTO ref.recipe_template (pathyam_id, food_id, base_method, default_servings, yield_factor, source_id, notes)
SELECT 'PY-T-000100', f.food_id, 'griddled', 1, 0.8200, s.source_id,
       'Plain dosa. Batter is rice:urad ~3:1 by dry weight, fermented 8-16 h.'
  FROM ref.food_item f, ref.source s
 WHERE f.pathyam_id = 'PY-F-000100' AND s.source_key = 'INDB2024.11';

INSERT INTO ref.recipe_template (pathyam_id, food_id, base_method, default_servings, yield_factor, source_id, notes)
SELECT 'PY-T-000102', f.food_id, 'boiled', 1, 0.9500, s.source_id,
       'Potato masala filling. Used as a SUB-RECIPE inside masala dosa.'
  FROM ref.food_item f, ref.source s
 WHERE f.pathyam_id = 'PY-F-000102' AND s.source_key = 'INDB2024.11';

INSERT INTO ref.recipe_template (pathyam_id, food_id, base_method, default_servings, yield_factor, source_id, notes)
SELECT 'PY-T-000101', f.food_id, 'griddled', 1, 0.8400, s.source_id,
       'Masala dosa = plain dosa parameters + nested potato masala sub-recipe.'
  FROM ref.food_item f, ref.source s
 WHERE f.pathyam_id = 'PY-F-000101' AND s.source_key = 'INDB2024.11';

-- --------------------------------------------------- parameters: plain dosa ----
INSERT INTO ref.template_parameter
  (template_id, param_name, dtype, unit, prior_dist, prior_params,
   observable_from_image, elicitation_question, elicitation_options, display_order)
SELECT t.template_id, v.pname, v.dt::ref.param_dtype, v.unit,
       v.dist::ref.prior_dist, v.params::jsonb, v.obs, v.q, v.opts::jsonb, v.ord
FROM (VALUES
 ('batter_g','continuous','g','lognormal','{"mu": 4.50, "sigma": 0.25}', true,
  NULL, NULL, 1),
 ('rice_fraction','continuous','ratio','normal','{"mu": 0.75, "sigma": 0.04}', false,
  NULL, NULL, 2),
 -- fat_g is unobservable and dominates energy variance. This is the one question to ask.
 ('fat_g','continuous','g','lognormal','{"mu": 2.08, "sigma": 0.55}', false,
  'How much oil or ghee was used?',
  '[{"label":"little","value":4},{"label":"medium","value":8},{"label":"generous","value":15}]', 3),
 ('fat_type','categorical',NULL,'categorical',
  '{"categories": ["gingelly","coconut","ghee","sunflower"], "weights": [0.45,0.30,0.15,0.10]}',
  false, 'Which cooking fat?',
  '[{"label":"Gingelly","value":"gingelly"},{"label":"Coconut","value":"coconut"},{"label":"Ghee","value":"ghee"}]', 4),
 ('rice_type','categorical',NULL,'categorical',
  '{"categories": ["parboiled","raw"], "weights": [0.70,0.30]}', false, NULL, NULL, 5),
 ('fermentation_h','continuous','h','normal','{"mu": 12, "sigma": 3}', false, NULL, NULL, 6)
) AS v(pname, dt, unit, dist, params, obs, q, opts, ord)
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000100';

-- ------------------------------------------------ parameters: potato masala ----
INSERT INTO ref.template_parameter
  (template_id, param_name, dtype, unit, prior_dist, prior_params, observable_from_image, display_order)
SELECT t.template_id, v.pname, v.dt::ref.param_dtype, v.unit,
       v.dist::ref.prior_dist, v.params::jsonb, v.obs, v.ord
FROM (VALUES
 ('potato_g','continuous','g','lognormal','{"mu": 4.25, "sigma": 0.30}', true,  1),
 ('onion_g', 'continuous','g','lognormal','{"mu": 3.00, "sigma": 0.35}', true,  2),
 ('masala_fat_g','continuous','g','lognormal','{"mu": 1.61, "sigma": 0.45}', false, 3)
) AS v(pname, dt, unit, dist, params, obs, ord)
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000102';

-- ------------------------------------------------ parameters: masala dosa ----
INSERT INTO ref.template_parameter
  (template_id, param_name, dtype, unit, prior_dist, prior_params,
   observable_from_image, elicitation_question, elicitation_options, display_order)
SELECT t.template_id, v.pname, v.dt::ref.param_dtype, v.unit,
       v.dist::ref.prior_dist, v.params::jsonb, v.obs, v.q, v.opts::jsonb, v.ord
FROM (VALUES
 ('batter_g','continuous','g','lognormal','{"mu": 4.62, "sigma": 0.25}', true, NULL, NULL, 1),
 ('rice_fraction','continuous','ratio','normal','{"mu": 0.75, "sigma": 0.04}', false, NULL, NULL, 2),
 ('fat_g','continuous','g','lognormal','{"mu": 2.30, "sigma": 0.55}', false,
  'How much oil or ghee was used?',
  '[{"label":"little","value":6},{"label":"medium","value":11},{"label":"generous","value":20}]', 3),
 ('fat_type','categorical',NULL,'categorical',
  '{"categories": ["gingelly","coconut","ghee","sunflower"], "weights": [0.40,0.25,0.25,0.10]}',
  false, NULL, NULL, 4),
 ('rice_type','categorical',NULL,'categorical',
  '{"categories": ["parboiled","raw"], "weights": [0.70,0.30]}', false, NULL, NULL, 5),
 ('filling_g','continuous','g','lognormal','{"mu": 4.25, "sigma": 0.30}', true, NULL, NULL, 6)
) AS v(pname, dt, unit, dist, params, obs, q, opts, ord)
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000101';

-- --------------------------------------------------------- ingredient lists ----
-- Plain dosa: quantity expressions over the declared parameters.
INSERT INTO ref.template_ingredient
  (template_id, food_id, qty_expr, unit, preparation_state, cooking_method, display_order)
SELECT t.template_id, f.food_id, v.expr, 'g', v.prep, 'griddled', v.ord
FROM (VALUES
 ('PY-F-000001','batter_g * rice_fraction * (rice_type == "parboiled")','soaked_ground',1),
 ('PY-F-000002','batter_g * rice_fraction * (rice_type == "raw")',      'soaked_ground',2),
 ('PY-F-000003','batter_g * (1 - rice_fraction) * 0.95','soaked_ground',3),
 ('PY-F-000004','batter_g * (1 - rice_fraction) * 0.05','soaked_ground',4),
 ('PY-F-000005','fat_g * (fat_type == "gingelly")','raw',5),
 ('PY-F-000006','fat_g * (fat_type == "coconut")', 'raw',6),
 ('PY-F-000007','fat_g * (fat_type == "ghee")',    'raw',7),
 ('PY-F-000010','batter_g * 0.012','raw',8)
) AS v(pid, expr, prep, ord)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000100';

-- Potato masala sub-recipe.
INSERT INTO ref.template_ingredient
  (template_id, food_id, qty_expr, unit, preparation_state, cooking_method, display_order)
SELECT t.template_id, f.food_id, v.expr, 'g', v.prep, 'boiled', v.ord
FROM (VALUES
 ('PY-F-000008','potato_g','boiled',1),
 ('PY-F-000009','onion_g','raw',2),
 ('PY-F-000005','masala_fat_g','raw',3),
 ('PY-F-000010','(potato_g + onion_g) * 0.010','raw',4)
) AS v(pid, expr, prep, ord)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000102';

-- Masala dosa: batter ingredients PLUS a nested sub-recipe reference.
INSERT INTO ref.template_ingredient
  (template_id, food_id, qty_expr, unit, preparation_state, cooking_method, display_order)
SELECT t.template_id, f.food_id, v.expr, 'g', v.prep, 'griddled', v.ord
FROM (VALUES
 ('PY-F-000001','batter_g * rice_fraction * (rice_type == "parboiled")','soaked_ground',1),
 ('PY-F-000002','batter_g * rice_fraction * (rice_type == "raw")',      'soaked_ground',2),
 ('PY-F-000003','batter_g * (1 - rice_fraction) * 0.95','soaked_ground',3),
 ('PY-F-000005','fat_g * (fat_type == "gingelly")','raw',4),
 ('PY-F-000006','fat_g * (fat_type == "coconut")', 'raw',5),
 ('PY-F-000007','fat_g * (fat_type == "ghee")',    'raw',6)
) AS v(pid, expr, prep, ord)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000101';

-- THE NESTED SUB-RECIPE. filling_g scales the whole potato-masala template.
INSERT INTO ref.template_ingredient
  (template_id, sub_template_id, qty_expr, unit, display_order)
SELECT parent.template_id, child.template_id, 'filling_g', 'g', 7
  FROM ref.recipe_template parent, ref.recipe_template child
 WHERE parent.pathyam_id = 'PY-T-000101' AND child.pathyam_id = 'PY-T-000102';

-- -------------------------------------------------------- regional priors ----
-- Kerala is not 288 extra rows. It is a shifted prior.
INSERT INTO ref.regional_prior (template_id, region_id, param_name, prior_dist, prior_params, notes)
SELECT t.template_id, r.region_id, v.pname, v.dist::ref.prior_dist, v.params::jsonb, v.note
FROM (VALUES
 ('KL','fat_type','categorical',
  '{"categories": ["gingelly","coconut","ghee","sunflower"], "weights": [0.10,0.75,0.10,0.05]}',
  'Kerala: coconut oil dominant - materially different fatty acid profile.'),
 ('KL','rice_type','categorical','{"categories": ["parboiled","raw"], "weights": [0.92,0.08]}',
  'Kerala: parboiled rice near-universal; affects thiamine retention and GI.'),
 ('TN','fat_type','categorical',
  '{"categories": ["gingelly","coconut","ghee","sunflower"], "weights": [0.60,0.15,0.15,0.10]}',
  'Tamil Nadu: gingelly (sesame) oil dominant.'),
 ('TN','rice_type','categorical','{"categories": ["parboiled","raw"], "weights": [0.80,0.20]}', NULL),
 ('KA','fat_type','categorical',
  '{"categories": ["gingelly","coconut","ghee","sunflower"], "weights": [0.25,0.15,0.30,0.30]}',
  'Karnataka: ghee and sunflower more common; Bangalore masala dosa is ghee-heavy.'),
 ('KA','rice_type','categorical','{"categories": ["parboiled","raw"], "weights": [0.45,0.55]}', NULL)
) AS v(rkey, pname, dist, params, note)
JOIN ref.region r ON r.region_key = v.rkey
JOIN ref.recipe_template t ON t.pathyam_id = 'PY-T-000100';

-- ---------------------------------------------------------- serving units ----
INSERT INTO ref.serving_unit (unit_key, name_en, name_i18n, volume_ml, typical_g, typical_g_sd, applies_to_group, is_count_based, region_id)
SELECT v.k, v.n, v.i18n::jsonb, v.ml, v.g, v.sd, v.grp, v.cnt, r.region_id
FROM (VALUES
 ('katori_south','Katori (South Indian)','{"ta":"கிண்ணம்","ml":"കിണ്ണം","kn":"ಬಟ್ಟಲು","te":"గిన్నె"}',
  130.0, NULL, NULL, NULL, false, 'TN'),
 ('katori_generic','Katori (survey standard 150 ml)','{}', 150.0, NULL, NULL, NULL, false, NULL),
 ('dosa_medium','Dosa (medium)','{"ta":"தோசை","kn":"ದೋಸೆ"}', NULL, 85.0, 22.0,'prepared_dish', true, NULL),
 ('dosa_large','Dosa (large / paper)','{}', NULL, 120.0, 30.0,'prepared_dish', true, NULL),
 ('idli_piece','Idli (1 piece)','{"ta":"இட்லி"}', NULL, 45.0, 9.0, 'prepared_dish', true, NULL),
 ('tumbler','Tumbler','{"ta":"டம்ளர்"}', 180.0, NULL, NULL, NULL, false, NULL)
) AS v(k, n, i18n, ml, g, sd, grp, cnt, rkey)
LEFT JOIN ref.region r ON r.region_key = v.rkey;

INSERT INTO ref.food_serving (food_id, unit_id, grams, grams_sd, region_id, is_default, source_id)
SELECT f.food_id, u.unit_id, v.g, v.sd, r.region_id, v.def, s.source_id
FROM (VALUES
 ('PY-F-000100','dosa_medium', 85.0, 22.0, NULL, true),
 ('PY-F-000100','dosa_large', 120.0, 30.0, NULL, false),
 ('PY-F-000101','dosa_medium',160.0, 38.0, NULL, true),
 ('PY-F-000101','dosa_large', 215.0, 48.0, 'KA', false)
) AS v(pid, ukey, g, sd, rkey, def)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.serving_unit u ON u.unit_key = v.ukey
LEFT JOIN ref.region r ON r.region_key = v.rkey
JOIN ref.source s ON s.source_key = 'PATHYAM-EST';

-- ----------------------------------------------------------- glycemic data ----
INSERT INTO ref.glycemic_value
  (food_id, gi, gi_sem, gi_reference, gl_per_serving, n_subjects, population, method, pmid, source_id, confidence)
SELECT f.food_id, v.gi, v.sem, 'glucose', v.gl, 10, 'healthy adults, South India',
       'FAO/WHO 1998', '35875218', s.source_id, 'A'
FROM (VALUES
 ('PY-F-000100', 76.30, 3.10, 39.69),   -- plain dosa: highest GL in the study
 ('PY-F-000101', 71.40, 2.80, 34.20)
) AS v(pid, gi, sem, gl)
JOIN ref.food_item f ON f.pathyam_id = v.pid
JOIN ref.source s ON s.source_key = 'PMID:35875218';

-- ============================================================================
-- A CONSENTING USER, A PHOTO LOG, AND A FULL INFERENCE TRACE
-- ============================================================================

INSERT INTO app.app_user (user_id, region_id, preferred_lang, year_of_birth, sex_at_birth)
SELECT '11111111-1111-1111-1111-111111111111', region_id, 'ta', 1979, 'male'
  FROM ref.region WHERE region_key = 'TN';

-- Core service consent (required) + model training consent (separate and optional).
INSERT INTO app.user_consent (user_id, purpose_id, notice_version, notice_lang, evidence)
SELECT '11111111-1111-1111-1111-111111111111', p.purpose_id, 'notice-v1.2', 'ta',
       jsonb_build_object('ui','onboarding_step_3','ts', now()::text, 'method','explicit_toggle')
  FROM app.consent_purpose p
 WHERE p.purpose_key IN ('core_service','model_training');

INSERT INTO app.meal_log (meal_log_id, user_id, meal_slot, method, region_id, image_ref, image_sha256)
SELECT '22222222-2222-2222-2222-222222222222',
       '11111111-1111-1111-1111-111111111111',
       'breakfast', 'photo', region_id,
       's3://pathyam-media/2026/08/12/abc123.jpg',
       decode('a1b2c3d4','hex')
  FROM ref.region WHERE region_key = 'TN';

INSERT INTO app.meal_log_item
  (item_id, meal_log_id, template_id, food_id, param_bindings, servings, unit_id,
   energy_kcal_p50, energy_kcal_p10, energy_kcal_p90, nutrients,
   dominant_uncertainty_param, engine_version, computed_at, is_user_confirmed, confirmed_at)
SELECT '33333333-3333-3333-3333-333333333333',
       '22222222-2222-2222-2222-222222222222',
       t.template_id, f.food_id,
       '{"batter_g": 101.5, "rice_fraction": 0.75, "fat_g": 11.0, "fat_type": "ghee", "rice_type": "parboiled", "filling_g": 72.0}'::jsonb,
       1, u.unit_id,
       385.00, 291.00, 518.00,
       '{"CHOAVLDF": {"p10":44.1,"p50":52.3,"p90":61.4},
         "PROCNT":   {"p10":7.2, "p50":8.6, "p90":10.1},
         "FAT":      {"p10":6.4, "p50":12.1,"p90":21.8},
         "K":        {"p10":410, "p50":498, "p90":602}}'::jsonb,
       'fat_g', 'engine-0.3.1', now(), true, now()
  FROM ref.recipe_template t
  JOIN ref.food_item f ON f.pathyam_id = 'PY-F-000101'
  JOIN ref.serving_unit u ON u.unit_key = 'dosa_medium'
 WHERE t.pathyam_id = 'PY-T-000101';

-- The inference trace: what the VLM saw, what retrieval proposed, what won.
INSERT INTO ml.inference_run (run_id, item_id, stage, model_name, model_version, prompt_version, raw_output, latency_ms)
VALUES
 ('44444444-4444-4444-4444-444444444444','33333333-3333-3333-3333-333333333333',
  'vlm','claude-vision','2026-06','dish-extract-v4',
  '{"dish_candidates":[{"name":"masala dosa","confidence":0.81},
                       {"name":"plain dosa","confidence":0.12},
                       {"name":"ghee roast","confidence":0.05}],
    "visible_ingredients":["potato","onion","curry leaf"],
    "vessel":"steel plate","count":1,
    "attributes":{"browning":"high","oil_sheen":"high","folded":true},
    "reference_objects":["steel tumbler"]}'::jsonb, 1180),
 ('55555555-5555-5555-5555-555555555555','33333333-3333-3333-3333-333333333333',
  'retrieval','bge-m3-indic','v1.2',NULL,'{"k":10,"index":"dish_ontology_v3"}'::jsonb, 24);

INSERT INTO ml.inference_candidate (run_id, rank, template_id, score)
SELECT '55555555-5555-5555-5555-555555555555', v.rank, t.template_id, v.score
FROM (VALUES (1,'PY-T-000101',0.902341),(2,'PY-T-000100',0.64312),(3,'PY-T-000102',0.318772))
     AS v(rank, tpl, score)
JOIN ref.recipe_template t ON t.pathyam_id = v.tpl;

-- Parameter estimates with provenance. Note fat_g arrived from THREE sources;
-- user_stated won the precedence contest.
INSERT INTO ml.parameter_estimate (item_id, param_name, source, dist, dist_params, was_used)
VALUES
 ('33333333-3333-3333-3333-333333333333','batter_g','vlm','normal','{"mu":101.5,"sigma":18.0}',true),
 ('33333333-3333-3333-3333-333333333333','fat_g','population_prior','lognormal','{"mu":2.30,"sigma":0.55}',false),
 ('33333333-3333-3333-3333-333333333333','fat_g','regional_prior','lognormal','{"mu":2.35,"sigma":0.50}',false),
 ('33333333-3333-3333-3333-333333333333','fat_g','user_stated','point','{"value":11.0}',true),
 ('33333333-3333-3333-3333-333333333333','fat_type','regional_prior','categorical',
  '{"categories":["gingelly","coconut","ghee","sunflower"],"weights":[0.60,0.15,0.15,0.10]}',false),
 ('33333333-3333-3333-3333-333333333333','fat_type','user_stated','point','{"value":"ghee"}',true);

-- The correction: proposed + corrected + which run produced the proposal.
INSERT INTO ml.user_correction (item_id, field, proposed_value, corrected_value, run_id)
VALUES
 ('33333333-3333-3333-3333-333333333333','param:fat_type',
  '{"value":"gingelly"}','{"value":"ghee"}','44444444-4444-4444-4444-444444444444'),
 ('33333333-3333-3333-3333-333333333333','param:fat_g',
  '{"value":9.8}','{"value":11.0,"energy_kcal":396}','44444444-4444-4444-4444-444444444444');

-- The learned personal prior, after this correction.
INSERT INTO app.user_parameter_prior (user_id, template_id, param_name, dist, dist_params, n_observations)
SELECT '11111111-1111-1111-1111-111111111111', t.template_id, 'fat_g',
       'lognormal', '{"mu": 2.36, "sigma": 0.31}'::jsonb, 7
  FROM ref.recipe_template t WHERE t.pathyam_id = 'PY-T-000101';

REFRESH MATERIALIZED VIEW app.mv_dish_card;
