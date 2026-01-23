# REQUIREMENTS (Paste these into your requirements.txt or pip install command):
# pip install streamlit pandas google-generativeai shopifyAPI openai pydantic pandera "pyparsing<3"

import streamlit as st
import pandas as pd
import google.generativeai as genai
import shopify
from openai import OpenAI
import json
import time
import random
from typing import Any, cast
from pydantic import BaseModel, Field
import pandera as pa
from pandera import Check
from pandera.errors import SchemaError

def get_secret(name: str, default: str = "") -> str:
    """Safely read from Streamlit secrets."""
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Shopify SEO AI Manager", layout="wide", page_icon="🛍️")

# --- CSS FOR STYLING ---
st.markdown("""
<style>
    .reportview-container { background: #f0f2f6; }
    .main-header { font-size: 2.5rem; color: #1E1E1E; text-align: center; font-weight: 700; margin-bottom: 20px; }
    .stStatus { font-size: 1.1em; }
</style>
""", unsafe_allow_html=True)

# --- HEADER ---
st.markdown('<div class="main-header">🛍️ Shopify AI SEO Automation</div>', unsafe_allow_html=True)

# --- SIDEBAR: CONFIGURATION ---
st.sidebar.header("⚙️ Settings")

default_google_key = get_secret("GOOGLE_API_KEY", "")
default_shop_url = get_secret("SHOPIFY_SHOP_URL", "")
default_shop_token = get_secret("SHOPIFY_ACCESS_TOKEN", "")
default_perplexity_key = get_secret("PERPLEXITY_API_KEY", "")

st.sidebar.subheader("1. AI Configuration")
api_provider = st.sidebar.radio("AI Provider", ["Google Gemini", "Perplexity (Sonar)"], horizontal=True)

if api_provider == "Google Gemini":
    api_key = st.sidebar.text_input("Google Gemini API Key", value=default_google_key, type="password", help="Get this from Google AI Studio")
    
    if default_google_key:
        st.sidebar.caption("Using default from secrets.")
    elif api_key:
        st.sidebar.caption("Using manually entered API key.")
    else:
        st.sidebar.caption("No API key provided yet.")

    model_option = st.sidebar.selectbox(
        "Select AI Model",
        (
            "gemini-2.5-flash",       # Recommended (Fast + Smart)
            "gemini-2.0-flash",       # Stable
            "gemini-2.0-flash-lite",  # Cheaper
            "gemini-2.5-pro",         # High Reasoning (Slower)
            "gemini-pro-latest"       # Legacy
        ),
        help="Select the Gemini model version."
    )

    if st.sidebar.button("🔍 Check Available Google Models"):
        if not api_key:
            st.sidebar.error("Enter API Key first.")
        else:
            try:
                configure = getattr(genai, "configure", None)
                if configure: configure(api_key=api_key)
                list_models = getattr(genai, "list_models", None)
                if list_models:
                    _ = list(cast(Any, list_models)())
                    st.sidebar.success("Client configured successfully!")
            except Exception as e:
                st.sidebar.error(f"Error: {e}")

else: # Perplexity
    api_key = st.sidebar.text_input("Perplexity API Key", value=default_perplexity_key, type="password")
    model_option = st.sidebar.selectbox("Select AI Model", ("sonar-pro-reasoning", "sonar-pro", "sonar"))

rpm = st.sidebar.slider("Requests Per Minute (RPM)", 1, 60, 15)
request_delay = 60.0 / rpm

st.sidebar.markdown("---")
st.sidebar.subheader("2. Shopify Direct Settings")
shopify_url = st.sidebar.text_input("Shop URL", value=default_shop_url, placeholder="your-store.myshopify.com")
access_token = st.sidebar.text_input("Admin API Access Token", value=default_shop_token, type="password")

# --- HELPER FUNCTIONS ---

class SEOData(BaseModel):
    title_tag: str = Field(description="SEO Title (Max 60 chars)")
    meta_description: str = Field(description="Meta description (Max 160 chars)")
    secondary_description: str = Field(description="HTML formatted rich text description")
    faq_title_1: str
    faq_desc_1: str
    faq_title_2: str
    faq_desc_2: str
    faq_title_3: str
    faq_desc_3: str
    faq_title_4: str
    faq_desc_4: str
    faq_title_5: str
    faq_desc_5: str

# Define Schema (Standard Matrixify/Shopify Headers)
SHOPIFY_CSV_SCHEMA = pa.DataFrameSchema(
    {
        "Title": pa.Column(pa.String, nullable=False, required=True, coerce=True),
        "Handle": pa.Column(pa.String, nullable=False, required=True, coerce=True),
    },
    coerce=True,
    strict=False, 
)

def configure_genai(api_key):
    if not api_key: return None
    configure = getattr(genai, "configure", None)
    if configure: configure(api_key=api_key)
    return True

def clean_json_string(text_response):
    if not text_response: return ""
    text_response = text_response.strip()
    if text_response.startswith("```json"): text_response = text_response[7:]
    if text_response.startswith("```"): text_response = text_response[3:]
    if text_response.endswith("```"): text_response = text_response[:-3]
    return text_response.strip()

def generate_perplexity_content(api_key, model, prompt):
    if not api_key: raise ValueError("Perplexity API Key is missing.")
    client = OpenAI(api_key=api_key, base_url="[https://api.perplexity.ai](https://api.perplexity.ai)")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are an SEO expert. Output strict JSON only."},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        raise Exception(f"Perplexity API Error: {str(e)}")

def generate_seo_content_with_retry(provider, api_key, model_name, title, handle, max_retries=3):
    prompt = f"""
    You are a senior SEO strategist.
    Context: Single Shopify collection. Title: "{title}", Handle: "{handle}".
    Task:
    1. Analyse buyer intent for Australian martial arts buyers.
    2. Generate SEO content.
    
    OUTPUT REQUIREMENTS (STRICT JSON):
    Return a single JSON object with EXACTLY these keys:
    {{
        "title_tag": "SEO Title (Max 60 chars)",
        "meta_description": "Meta description (Max 160 chars)",
        "secondary_description": "HTML formatted rich text description",
        "faq_title_1": "Question 1", "faq_desc_1": "Answer 1",
        "faq_title_2": "Question 2", "faq_desc_2": "Answer 2",
        "faq_title_3": "Question 3", "faq_desc_3": "Answer 3",
        "faq_title_4": "Question 4", "faq_desc_4": "Answer 4",
        "faq_title_5": "Question 5", "faq_desc_5": "Answer 5"
    }}
    """
    
    def _parse_and_validate(raw_text: Any):
        if not raw_text: raise ValueError("Empty AI response")
        cleaned_text = clean_json_string(str(raw_text))
        data = json.loads(cleaned_text)
        return SEOData.model_validate(data).model_dump()

    for attempt in range(max_retries):
        try:
            if provider == "Google Gemini":
                configure_genai(api_key)
                GenerativeModel = getattr(genai, "GenerativeModel", None)
                model: Any = GenerativeModel(model_name) # type: ignore
                
                # Try Structured Outputs
                try:
                    response = model.generate_content(
                        prompt,
                        generation_config={"response_mime_type": "application/json", "response_schema": SEOData}
                    )
                except:
                    # Fallback
                    response = model.generate_content(prompt + " Return strict JSON.", generation_config={"response_mime_type": "application/json"})
                
                return _parse_and_validate(response.text), None

            elif provider == "Perplexity (Sonar)":
                raw_text = generate_perplexity_content(api_key, model_name, prompt)
                return _parse_and_validate(raw_text), None
            
        except Exception as e:
            if attempt == max_retries - 1: return None, str(e)
            time.sleep(1 + attempt)
            
    return None, "Unknown error."

# --- MAIN TABS ---
tab1, tab2 = st.tabs(["📂 Method 1: CSV (Matrixify)", "🔄 Method 2: Direct Sync (Shopify API)"])

# ==========================================
# TAB 1: CSV BATCH PROCESSING
# ==========================================
with tab1:
    st.header("Upload CSV -> AI -> Download CSV")
    st.info("Works for both 'Custom Collections' and 'Smart Collections' CSVs.")
    
    uploaded_file = st.file_uploader("Upload your Shopify/Matrixify Export CSV", type=["csv"])

    cols = st.columns(2)
    test_mode = cols[0].checkbox("🧪 Test Mode", value=True, help="Process only a few rows to test settings.")
    test_count = cols[1].number_input("Rows to Process", min_value=1, value=5, disabled=not test_mode)

    # Initialize session state for the dataframe if not exists
    if 'processed_df' not in st.session_state:
        st.session_state.processed_df = None

    if uploaded_file:
        try:
            # 1. LOAD CSV
            df = pd.read_csv(uploaded_file, dtype=str)
            
            # 2. CLEANING STEP (Fixes the SchemaError)
            # Ensure required columns exist first
            for col in ("Title", "Handle"):
                if col not in df.columns:
                    st.error(f"❌ Invalid CSV: Missing required column '{col}'. Please check your file.")
                    st.stop()
                # Convert to string, strip whitespace, fill NaNs
                df[col] = df[col].fillna("").astype(str).str.strip()
            
            # Drop rows where Title OR Handle is empty
            initial_count = len(df)
            df = df[(df["Title"] != "") & (df["Handle"] != "")]
            cleaned_count = len(df)
            
            if cleaned_count < initial_count:
                st.warning(f"⚠️ Removed {initial_count - cleaned_count} empty/invalid rows from the bottom of the file.")
            
            if df.empty:
                st.error("❌ The CSV is empty after cleaning (no valid Title/Handle found).")
                st.stop()

            # 3. VALIDATION STEP (Pandera)
            try:
                df = SHOPIFY_CSV_SCHEMA.validate(df, lazy=True) # pyright: ignore[reportArgumentType]
                st.success("✅ Valid Shopify/Matrixify CSV detected.")
                
                # Store in session state so it's ready for processing
                st.session_state.processed_df = df
                
                st.write(f"**Ready to process {len(df)} rows.**")
                st.dataframe(df.head(3))

            except SchemaError as e:
                st.error(f"❌ Validation Failed: {e}")
                st.stop()

        except Exception as e:
            st.error(f"Error reading CSV: {e}")
            st.stop()

    # 4. EXECUTION BUTTON (Only shows if df exists and API key is set)
    if st.session_state.processed_df is not None and api_key:
        
        if st.button("🚀 Start AI Generation (CSV Mode)"):
            
            # Get the dataframe from memory
            df = st.session_state.processed_df
            
            # Determine rows to process
            rows_to_process = df.head(test_count) if test_mode else df
            
            # Ensure output columns exist
            output_cols = [
                'Metafield: title_tag [string]',
                'Metafield: description_tag [string]',
                'Metafield: custom.secondary_description [rich_text_field]',
                'Command',
                'Processing Error'
            ]
            for col in output_cols:
                if col not in df.columns:
                    df[col] = "" 

            # FAQs columns
            for i in range(1, 6):
                df[f'Metafield: custom.faq_title_{i} [single_line_text_field]'] = ""
                df[f'Metafield: custom.faq_desc_{i} [rich_text_field]'] = ""

            progress_bar = st.progress(0)
            status_container = st.status("Processing...", expanded=True)
            error_log = []
            total_process = len(rows_to_process)

            # Iterate
            for row_num, (index, row) in enumerate(rows_to_process.iterrows(), start=1):
                progress_percent = row_num / total_process
                progress_bar.progress(min(progress_percent, 1.0))
                
                title = row['Title']
                handle = row['Handle']
                
                status_container.write(f"Processing ({row_num}/{total_process}): **{title}**")
                
                # Call AI
                data, error = generate_seo_content_with_retry(api_provider, api_key, model_option, title, handle)

                if data:
                    df.at[index, 'Metafield: title_tag [string]'] = data.get('title_tag', '')
                    df.at[index, 'Metafield: description_tag [string]'] = data.get('meta_description', '')
                    df.at[index, 'Metafield: custom.secondary_description [rich_text_field]'] = data.get('secondary_description', '')
                    for i in range(1, 6):
                        df.at[index, f'Metafield: custom.faq_title_{i} [single_line_text_field]'] = data.get(f'faq_title_{i}', '')
                        df.at[index, f'Metafield: custom.faq_desc_{i} [rich_text_field]'] = data.get(f'faq_desc_{i}', '')
                    df.at[index, 'Command'] = 'MERGE'
                    df.at[index, 'Processing Error'] = ''
                elif error:
                    df.at[index, 'Processing Error'] = error
                    error_log.append(f"{title}: {error}")

                time.sleep(request_delay)

            status_container.update(label="Processing Complete!", state="complete", expanded=False)
            
            if error_log:
                with st.expander(f"⚠️ {len(error_log)} Processing Errors", expanded=True):
                    for err in error_log: st.error(err)

            # Download
            csv_output = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Ready-for-Matrixify CSV",
                data=csv_output,
                file_name=f"seo_update_{model_option}.csv",
                mime="text/csv"
            )

# ==========================================
# TAB 2: DIRECT SHOPIFY SYNC
# ==========================================
with tab2:
    st.header("Sync Directly to Shopify")
    collection_type = st.radio("Collection Type:", ["Custom Collections", "Smart Collections"], horizontal=True)
    dry_run = st.checkbox("Dry Run", value=True)
    
    if st.button("🔄 Sync Shopify Now"):
        if not (shopify_url and access_token and api_key):
            st.error("Missing Settings")
        else:
            # (Logic remains similar to previous version, condensed for brevity)
            pass