# Pathyam AI — Google Stitch UI Prompts & Complete Interaction Prototype Specification

This document provides screen-by-screen text prompts formatted for **Google Stitch** (and modern AI UI design generators), along with complete UI/UX interaction flows, button click logic, and mobile style guide guidelines (iOS Human Interface Guidelines & Android Material 3 design system).

---

## 🎨 Master Design Tokens & Style Guide (Universal Mobile & Web)

| Token | Value | Hex / Spec | Usage |
|---|---|---|---|
| **Canvas Background** | Cream Blush | `#FAF8F5` | Universal app background |
| **Card Surface** | Pure White | `#FFFFFF` | Glassmorphic cards with `border: 1px solid #E8E2D8` |
| **Input Background** | Soft Sand | `#F3F0EA` | Search boxes, text fields, slider tracks |
| **Primary Text** | Deep Charcoal | `#1E1B26` | Headings, dish names, primary numbers |
| **Secondary Text** | Soft Slate | `#6E687A` | Subtitles, labels, timestamps |
| **Muted Text** | Warm Gray | `#9E96A9` | Placeholder text, confidence tags |
| **Primary Accent** | Gen-Z Lavender | `#8B5CF6` | Primary buttons, active tabs, ring highlights |
| **Accent Light** | Soft Lavender | `#F3E8FF` | Badge backgrounds, highlighted cards |
| **Success / Mint** | Fresh Mint | `#10B981` | Computable tags, positive macros |
| **Warning / Honey** | Warm Amber | `#F59E0B` | Blocked template tags, pending warnings |
| **Danger / Coral** | Coral Red | `#FF6B57` | Delete buttons, missing IFCT data tags |
| **Primary Font** | Plus Jakarta Sans | `Weights: 400, 600, 700, 800` | Interface typography, body, headings |
| **Numeric Font** | Space Grotesk | `Weights: 500, 700` | Calories, macro numbers, intervals, graphs |

---

## 📱 Screen-by-Screen Google Stitch UI Prompts & Interaction Logic

---

### Screen 1: Dashboard Home (`/v1/dashboard/summary`)

#### 🤖 Google Stitch UI Prompt:
```text
A modern, minimalist, Gen-Z light pastel metabolic health dashboard for an iOS and Android app called "Pathyam AI". 
Background color is #FAF8F5 (soft cream blush). 
At the top, include a header with a user profile avatar on the left, a date picker pill showing "Today, Aug 14" in the center, and a subtle bell notification icon on the right. 
Below the header, place a prominent hero card with a circular calorie ring (1,450 / 2,000 kcal) styled in a sleek lavender gradient (#8B5CF6). Inside the ring, show "1,450 kcal" in bold Space Grotesk font and "550 kcal left" in smaller text below it. Underneath the ring, show four horizontal macro progress pills: Carbs (180g / 220g - Lavender), Protein (55g / 70g - Mint Green), Fat (42g / 50g - Amber Honey), and Fibre (28g / 35g - Coral).
Next, add a sleek CGT Telemetry Summary card titled "Predicted Peak Glucose" displaying "142 mg/dL (+47 mg/dL at t=45m)" with a small sparkline curve.
Below, include a section titled "Today's Meals" with meal entry cards (Breakfast: Idli + Sambar - 210 kcal; Lunch: Curd Rice - 340 kcal). Each meal card has selective portion adjustment (+ / -) buttons, a timestamp tag, and a subtle trash deletion icon.
At the bottom, add a fixed 5-tab navigation bar with icons: Dashboard (active, lavender), Log Meal (camera icon in prominent floating action pill), History, CGT Lab, and Recipes.
```

#### 🔄 Detailed UI/UX Interaction & Button Logic:
1. **Header Date Picker (`#history-date-picker`)**:
   - *Click Action*: Opens native calendar modal. Selecting a date updates the dashboard summary metrics for that day via `GET /v1/history?date={YYYY-MM-DD}`.
2. **`+ Log Meal` Floating Pill Button**:
   - *Click Action*: Immediately switches to **Screen 2: Log Meal** tab.
3. **Meal Card Portion Adjustments (`➕` / `➖`)**:
   - *Click Action (`➕`)*: Triggers `PATCH /v1/history/{entry_id}/portion` with `portion_delta: +0.25`. Automatically recalculates calories, macros, and updates the top Calorie Ring dynamically.
   - *Click Action (`➖`)*: Triggers `PATCH /v1/history/{entry_id}/portion` with `portion_delta: -0.25`. Updates metrics immediately.
4. **Delete Item Button (`🗑️`)**:
   - *Click Action*: Prompts toast confirmation `"Delete Entry?"` and executes `DELETE /v1/history/{entry_id}`. Instantly removes card and updates macro rings.
5. **Bottom Navigation Tab Bar**:
   - *Click Actions*: Clicking any tab button smoothly transitions between panels (`dashboard`, `log`, `history`, `cgt`, `recipes`, `news`).

---

### Screen 2: Log Meal & Multimodal Perception (`/v1/resolve` & `/v1/log`)

#### 🤖 Google Stitch UI Prompt:
```text
A high-converting, intuitive meal logging screen for a mobile app in Gen-Z Light Pastel style (#FAF8F5 background).
Header has a title "Log Your Meal" with a segment selector for input modes: "📸 Photo Camera", "🎙️ Voice Note", "⌨️ Natural Text".
The central area features a camera viewfinder viewport card with rounded 24px corners. Inside the viewfinder, overlay a faint target bounding box around a dish with an indicator badge "Steel Plate & Spoon Detected". A large pill button below reads "📷 Take Photo of Meal".
Below the viewfinder, provide a text input box with placeholder text "e.g., 2 masale dose, swalpa enne (Tamil / Kannada / English)".
Below the text box, place a primary gradient action button labeled "✨ Parse & Estimate Nutrition".
Underneath, display a conditional "Resolved Candidate Card":
Top badge: "Dosa, masala (PY-T-000101) · 99% Match".
Parameter sliders: "Batter mass: 90g", "Cooking fat: 6g (0.6x prior)".
Nutrient output box: "248 kcal (191–323 kcal, 80% Credible Interval) · Dominant uncertainty: batter_g".
Two bottom action buttons: "✅ Accept & Log Meal" (primary lavender) and "✏️ Refine Parameters" (secondary outline).
```

#### 🔄 Detailed UI/UX Interaction & Button Logic:
1. **Input Mode Segment Tabs (`Photo` / `Voice` / `Text`)**:
   - *Click Action*: Toggles visibility between camera viewfinder, audio recorder visualizer, and text area.
2. **`📷 Take Photo of Meal` Button**:
   - *Click Action*: Triggers HTML5 `navigator.mediaDevices.getUserMedia` or native camera capture. Fills image preview into viewfinder card.
3. **`✨ Parse & Estimate Nutrition` Button**:
   - *Click Action*: Sends text/photo input to `POST /v1/resolve` or perception API. Parses Indic number words (`rendu`, `swalpa`), returns candidate list with confidence scores.
4. **Candidate Dish Pill Click**:
   - *Click Action*: Selects dish, executes `POST /v1/compute` with parameter hints, and populates the 80% Credible Interval box.
5. **`✅ Accept & Log Meal` Button**:
   - *Click Action*: Triggers `POST /v1/log`, assigns a unique `LOG-{uuid4()}` identifier, saves entry into history journal, shows toast `"Meal Logged!"`, and transitions user directly to **Screen 1: Dashboard**.

---

### Screen 3: History Journal (`/v1/history`)

#### 🤖 Google Stitch UI Prompt:
```text
A clean, journal-style meal history screen for an iOS and Android nutrition app in pastel aesthetic (#FAF8F5 background).
Top header has a date navigation bar with left/right arrows around "Thursday, Aug 14, 2026" and a summary badge "1,450 kcal · 4 Meals Logged".
Below header, add a horizontal filter pill list: "All", "Breakfast", "Lunch", "Dinner", "Snacks", "Late Night".
The main body contains vertical chronological meal cards:
Each card features:
- A timestamp pill: "08:30 AM · Breakfast".
- Dish Title & Method: "Idli (Steamed)" with a green confidence badge.
- Detailed macro breakdown: "210 kcal · Carbs 42g · Protein 8g · Fat 2g · Fibre 5g".
- Selective portion control: "-" button, "1.0 Serving" text, "+" button.
- Delete button: Small trash icon on top right of the card.
If no meals exist for a date, display an elegant empty state illustration with text "No meals logged for this date yet" and a button "Log First Meal".
```

#### 🔄 Detailed UI/UX Interaction & Button Logic:
1. **Date Navigator Arrows (`◀` / `▶`)**:
   - *Click Action*: Decrements/increments date by 1 day and fetches history entries via `GET /v1/history?date={selected_date}`.
2. **Meal Category Filter Pills (`Breakfast`, `Lunch`, etc.)**:
   - *Click Action*: Filters displayed meal cards by `meal_type` timestamp tag.
3. **Portion Increment/Decrement (`➕` / `➖`)**:
   - *Click Action*: Invokes `PATCH /v1/history/{entry_id}/portion` with `portion_delta`. Instantly scales calories, carbs, protein, fat, fibre, and peak glucose spike on the card.
4. **Trash Delete Button (`🗑️`)**:
   - *Click Action*: Removes log entry via `DELETE /v1/history/{entry_id}` with smooth slide-out transition.

---

### Screen 4: CGT Telemetry & Glucose Spike Simulator (`/v1/cgt/predict_spike`)

#### 🤖 Google Stitch UI Prompt:
```text
A scientific yet beautiful Continuous Glucose Telemetry (CGT) laboratory screen in Gen-Z Light Pastel style (#FAF8F5 background).
Header title: "📈 Continuous Glucose Telemetry (CGT) Lab".
Subheading: "Simulate postprandial glycemic response & peak glucose spike based on macro composition."
Top section contains four interactive slider cards:
1. "Carbohydrates (g)": Slider from 0 to 150g (Value readout: "50 g").
2. "Dietary Fibre (g)": Slider from 0 to 40g (Value readout: "8 g").
3. "Cooking Fat (g)": Slider from 0 to 50g (Value readout: "10 g").
4. "Glycemic Index (GI)": Slider from 20 to 100 (Value readout: "68").
Below sliders, place a wide graph card featuring an interactive canvas line chart:
Y-axis: Glucose (mg/dL) from 80 to 180.
X-axis: Time post-meal (0 to 180 minutes).
Chart displays a baseline reference line at 95 mg/dL and a smooth pastel curve peaking at t=45m.
Under the chart, show three key calculated metric boxes:
- "Peak Spike": "142 mg/dL (+47 mg/dL)"
- "Glycemic Load (GL)": "34 (High)"
- "iAUC (Incremental Area)": "4,200 mg/dL·min"
Bottom card: A clinical insight alert box explaining "Fat and fibre delay gastric emptying, damping Delta G_max by 28%."
```

#### 🔄 Detailed UI/UX Interaction & Button Logic:
1. **Slider Drag/Change (`Carbs`, `Fibre`, `Fat`, `GI`)**:
   - *Interaction*: Moving any slider updates live text readouts and triggers an asynchronous payload request to `POST /v1/cgt/predict_spike`.
2. **Graph Re-render**:
   - *Logic*: Computes $\text{GL} = \frac{\text{carbs\_g} \times \text{gi}}{100}$ and $\Delta G_{\text{max}} = \text{GL} \times 1.8 \times e^{-0.02 \times \text{fat\_g} - 0.04 \times \text{fibre\_g}}$. Redraws canvas line graph smoothly in real-time.
3. **Metric Box Updates**:
   - *Logic*: Displays peak glucose level, time to peak, and total incremental area under curve (iAUC).

---

### Screen 5: Recipe Template Catalog (`/v1/templates`)

#### 🤖 Google Stitch UI Prompt:
```text
An architectural recipe catalog screen for South Indian cuisine in light pastel design (#FAF8F5 background).
Top header: Search bar "Search 20+ South Indian Recipe Templates..." with filter pills below: "All", "Steamed", "Griddled", "Simmered", "Deep Fried".
Below header, display a 2-column statistical audit summary:
- Box 1: "Active Templates: 20" (Lavender accent)
- Box 2: "Computable Audit: 1 Computable / 19 Worklist" (Mint accent)
Below audit, render a 2-column grid of Recipe Template Cards:
Each card features:
- Title: "Idli (PY-T-000110)"
- Method Pill: "steamed"
- Computability Badge: "COMPUTABLE" (Mint green) or "BLOCKED" (Honey amber with "Missing 3 IFCT items")
- Metrics: "54 kcal/serving · 121 kcal/100g"
- Parameter details: "Dominant: solids_g · 4 Parameters · 5 Ingredients"
- Action Button: Full-width button "Log Idli" (Lavender primary).
```

#### 🔄 Detailed UI/UX Interaction & Button Logic:
1. **Search Bar Input**:
   - *Interaction*: Filters visible recipe cards by dish name (`Idli`, `Dosa`, `Sambar`, etc.).
2. **Category Filter Pills**:
   - *Click Action*: Filters recipes by `base_method` (`steamed`, `griddled`, `simmered`, `deep_fried`).
3. **`Log [Dish]` Button**:
   - *Click Action*: Automatically selects recipe template, computes default serving nutrients, logs entry via `POST /v1/log`, and transitions to **Screen 1: Dashboard**.

---

### Screen 6: Clinical Nutrition RSS News Feed (`/v1/news/rss`)

#### 🤖 Google Stitch UI Prompt:
```text
A modern news and evidence feed screen for a clinical nutrition mobile app (#FAF8F5 background).
Top header title: "📰 Clinical Research Feed".
Subheading: "Peer-reviewed literature & ICMR-NIN evidence updates."
Filter pills below header: "All", "Clinical Nutrition", "CGT Telemetry", "Algorithmic AI".
Main feed consists of vertical news cards:
Each news card features:
- Top bar: Category badge ("Clinical Nutrition" - Lavender) and publication date ("Aug 14, 2026" - Muted gray).
- Title in bold 18px Plus Jakarta Sans: "ICMR-NIN IFCT 2017: Precision Nutrient Profiling in South Indian Diets".
- Source tag: "Source: ICMR National Institute of Nutrition".
- Summary paragraph: "New clinical evidence reveals significant attenuation of glycemic spikes when combining fermented rice batter with high-fibre pulses and native seeds."
- Action link button: "Read Full Research Article →" (Secondary outline pill button).
```

#### 🔄 Detailed UI/UX Interaction & Button Logic:
1. **Category Filter Pills (`Clinical Nutrition`, `CGT Telemetry`, etc.)**:
   - *Click Action*: Filters article list by `category` field.
2. **`Read Full Research Article →` Link**:
   - *Click Action*: Opens official research paper (ICMR-NIN, PubMed, Nature, arXiv) in native browser tab or in-app Safari/Chrome WebView.

---

## 🛠️ Summary of Prototype Endpoints & Data Wiring

| Action / Button | API Endpoint | Method | Payload / Response |
|---|---|---|---|
| Load Dashboard Summary | `/v1/dashboard/summary` | `GET` | Returns daily totals, calorie ring, macros, and history items |
| Parse Text / Photo | `/v1/resolve` | `POST` | `{ text: "2 masale dose" }` → Candidates & confidence |
| Calculate Nutrients & Interval | `/v1/compute` | `POST` | `{ template_ref: 3, param_overrides: {...} }` → Nutrients + 80% CI |
| Log Meal Entry | `/v1/log` | `POST` | `{ raw_text, dish_name, calories, ... }` → Creates `LOG-{uuid4()}` entry |
| Increment/Decrement Portion | `/v1/history/{entry_id}/portion` | `PATCH` | `{ portion_delta: +0.25 }` → Dynamically updates entry |
| Delete Journal Entry | `/v1/history/{entry_id}` | `DELETE` | Removes log entry |
| CGT Spike Predictor | `/v1/cgt/predict_spike` | `POST` | `{ carbs_g, fibre_g, fat_g, gi }` → Peak spike & curve |
| Load Recipe Catalog | `/v1/templates` | `GET` | Returns 20 active recipe templates with computability |
| Load RSS News Feed | `/v1/news/rss` | `GET` | Returns aggregated clinical nutrition articles |

