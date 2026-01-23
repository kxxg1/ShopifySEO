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
    """Safely read from Streamlit secrets.

    Locally, Streamlit reads `.streamlit/secrets.toml`. In some contexts (or if the
    file is missing/malformed), accessing `st.secrets` can raise.
    """
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Shopify SEO AI Manager", layout="wide", page_icon="🛍️")

# --- CSS FOR STYLING ---
st.markdown("""
<style>
    .reportview-container {
        background: #f0f2f6;
    }
    .main-header {
        font-size: 2.5rem; 
        color: #1E1E1E; 
        text-align: center; 
        font-weight: 700;
        margin-bottom: 20px;
    }
    .stStatus {
        font-size: 1.1em;
    }
</style>
""", unsafe_allow_html=True)

# --- HEADER ---
st.markdown('<div class="main-header">🛍️ Shopify AI SEO Automation</div>', unsafe_allow_html=True)

# --- SIDEBAR: CONFIGURATION ---
st.sidebar.header("⚙️ Settings")

# 1. Try to load from secrets file first, otherwise default to empty
default_google_key = get_secret("GOOGLE_API_KEY", "")
default_shop_url = get_secret("SHOPIFY_SHOP_URL", "")
default_shop_token = get_secret("SHOPIFY_ACCESS_TOKEN", "")
default_perplexity_key = get_secret("PERPLEXITY_API_KEY", "")

st.sidebar.subheader("1. AI Configuration")
api_provider = st.sidebar.radio("AI Provider", ["Google Gemini", "Perplexity (Sonar)"], horizontal=True)

if api_provider == "Google Gemini":
    api_key = st.sidebar.text_input(
        "Google Gemini API Key",
        value=default_google_key,
        type="password",
        help="Get this from Google AI Studio"
    )

    if default_google_key:
        st.sidebar.caption("Using default from `.streamlit/secrets.toml` (key: `GOOGLE_API_KEY`).")
    elif api_key:
        st.sidebar.caption("Using manually entered API key (no `GOOGLE_API_KEY` found in secrets).")
    else:
        st.sidebar.caption("No API key provided yet.")

    # Multi-Model Support (Dropdown)
    model_option = st.sidebar.selectbox(
        "Select AI Model",
        (
            "gemini-2.5-pro",         # High reasoning, long context
            "gemini-2.5-flash",       # Fast + strong reasoning
            "gemini-pro-latest",      # Latest Gemini Pro
            "gemini-flash-latest",    # Latest Gemini Flash
            "gemini-2.0-flash",       # Stable fast model
            "gemini-2.0-flash-lite"   # Cost effective
        ),
        help="Use only models returned by list_models() for your API key."
    )

    if st.sidebar.button("🔍 Check Available Google Models"):
        if not api_key:
            st.sidebar.error("Enter API Key first.")
        else:
            try:
                configure = getattr(genai, "configure", None)
                if not callable(configure):
                    raise RuntimeError("google.generativeai.configure is not available in this SDK version")
                configure(api_key=api_key)
                list_models = getattr(genai, "list_models", None)
                if not callable(list_models):
                    raise RuntimeError("google.generativeai.list_models is not available in this SDK version")
                _ = list(cast(Any, list_models)())  # simple call to validate key
                st.sidebar.success("Client configured successfully!")
            except Exception as e:
                msg = str(e)
                # Common failure mode: expired/invalid key (HTTP 400 API_KEY_INVALID)
                if "API_KEY_INVALID" in msg or "API key expired" in msg or "key expired" in msg.lower():
                    st.sidebar.error(
                        "Google API key is invalid/expired. Generate a new key in Google AI Studio, "
                        "update `.streamlit/secrets.toml` (`GOOGLE_API_KEY`), then restart Streamlit."
                    )
                else:
                    st.sidebar.error(f"Error: {e}")

else: # Perplexity
    api_key = st.sidebar.text_input(
        "Perplexity API Key", 
        value=default_perplexity_key,
        type="password", 
        help="Get this from Perplexity.ai settings"
    )
    model_option = st.sidebar.selectbox(
        "Select AI Model",
        ("sonar-pro-reasoning", "sonar-pro", "sonar"),
        help="Sonar-pro is the reasoning model."
    )

# Rate Limit Slider
rpm = st.sidebar.slider(
    "Requests Per Minute (RPM)",
    min_value=1,
    max_value=60,
    value=15,
    help="Controls the speed of API calls to avoid hitting limits."
)
# Calculate delay dynamically
request_delay = 60.0 / rpm

st.sidebar.markdown("---")
st.sidebar.subheader("2. Shopify Direct Settings")
st.sidebar.info("Only required if using 'Direct Sync' tab.")
shopify_url = st.sidebar.text_input("Shop URL", value=default_shop_url, placeholder="your-store.myshopify.com")
access_token = st.sidebar.text_input("Admin API Access Token", value=default_shop_token, type="password")


# --- HELPER FUNCTIONS ---


class SEOData(BaseModel):
    title_tag: str = Field(description="SEO Title (Max 60 chars)")
    meta_description: str = Field(description="Meta description (Max 160 chars)")
    secondary_description: str = Field(
        description="HTML formatted rich text description for bottom of page"
    )
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


SHOPIFY_CSV_SCHEMA = pa.DataFrameSchema(
    {
        "Title": pa.Column(
            pa.String,
            nullable=False,
            required=True,
            coerce=True,
            checks=Check(
                lambda s: s.fillna("").astype(str).str.strip().str.len() > 0,
                error="Column 'Title' contains empty values",
            ),
        ),
        "Handle": pa.Column(
            pa.String,
            nullable=False,
            required=True,
            coerce=True,
            checks=Check(
                lambda s: s.fillna("").astype(str).str.strip().str.len() > 0,
                error="Column 'Handle' contains empty values",
            ),
        ),
    },
    coerce=True,
    strict=False,  # allow extra columns from Shopify/Matrixify exports
)

def configure_genai(api_key):
    """Configure the Gemini SDK with the provided API key."""
    if not api_key:
        return None
    configure = getattr(genai, "configure", None)
    if not callable(configure):
        raise RuntimeError("google.generativeai.configure is not available in this SDK version")
    configure(api_key=api_key)
    return True

def clean_json_string(text_response):
    """Cleans the response text to ensure valid JSON."""
    if not text_response:
        return ""
    text_response = text_response.strip()
    if text_response.startswith("```json"):
        text_response = text_response[7:]
    if text_response.startswith("```"):
        text_response = text_response[3:]
    if text_response.endswith("```"):
        text_response = text_response[:-3]
    return text_response.strip()

def generate_perplexity_content(api_key, model, prompt):
    """Generates content using Perplexity API (Sonar models)."""
    client = OpenAI(api_key=api_key, base_url="https://api.perplexity.ai")
    
    messages = [
        {
            "role": "system",
            "content": (
                "You are an SEO expert. You output strictly valid JSON only. "
                "No markdown formatting like ```json ... ```. Just the raw JSON string."
            ),
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return response.choices[0].message.content
    except Exception as e:
        raise Exception(f"Perplexity API Error: {str(e)}")


def generate_seo_content_with_retry(provider, api_key, model_name, title, handle, max_retries=3):
    """
    Generates SEO content.

    For Gemini, attempts Structured Outputs using a Pydantic schema and validates
    the returned JSON. For Perplexity, keeps the legacy JSON-cleaning approach.

    Returns a tuple: (data_dict, error_message)
    """
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
            "secondary_description": "HTML formatted rich text description for bottom of page",
            "faq_title_1": "Question 1",
            "faq_desc_1": "Answer 1 (HTML formatted)",
            "faq_title_2": "Question 2",
            "faq_desc_2": "Answer 2 (HTML formatted)",
            "faq_title_3": "Question 3",
            "faq_desc_3": "Answer 3 (HTML formatted)",
            "faq_title_4": "Question 4",
            "faq_desc_4": "Answer 4 (HTML formatted)",
            "faq_title_5": "Question 5",
            "faq_desc_5": "Answer 5 (HTML formatted)"
        }}

        Only output the raw JSON. No markdown, no prose.
        """

    def _parse_and_validate(raw_text: Any):
        if raw_text is None:
            raise ValueError("Empty AI response")
        cleaned_text = clean_json_string(str(raw_text))
        data = json.loads(cleaned_text)
        return SEOData.model_validate(data).model_dump()

    for attempt in range(max_retries):
        try:
            if provider == "Google Gemini":
                configure_genai(api_key)
                GenerativeModel = getattr(genai, "GenerativeModel", None)
                if not callable(GenerativeModel):
                    raise RuntimeError("google.generativeai.GenerativeModel is not available in this SDK version")
                model: Any = GenerativeModel(model_name)

                # Prefer Structured Outputs (if supported by the installed SDK).
                try:
                    response = model.generate_content(
                        prompt,
                        generation_config=cast(Any, {
                            "response_mime_type": "application/json",
                            "response_schema": SEOData,
                        }),
                    )
                except Exception:
                    # Fallback: request JSON only and validate locally with Pydantic.
                    response = model.generate_content(
                        prompt
                        + "\n\nReturn ONLY valid JSON (no markdown, no prose).",
                        generation_config={"response_mime_type": "application/json"},
                    )

                data = _parse_and_validate(response.text)
                return data, None

            elif provider == "Perplexity (Sonar)":
                raw_text = generate_perplexity_content(api_key, model_name, prompt)
                data = _parse_and_validate(raw_text)
                return data, None

            return None, f"Unknown provider: {provider}"
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:
                # Rate limit hit, backoff
                wait_time = (attempt + 1) * 2 + random.uniform(0, 1)
                time.sleep(wait_time)
            else:
                pass  # Other error
            
            if attempt == max_retries - 1:
                return None, f"AI Generation Error: {error_msg}"
            
            time.sleep(1) # Base sleep between retries

    return None, "Unknown error occurred."

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

    if uploaded_file and api_key:
        # Load CSV as string to preserve IDs
        try:
            df = pd.read_csv(uploaded_file, dtype=str)

            try:
                df = SHOPIFY_CSV_SCHEMA.validate(df, lazy=True)
                st.success("✅ Valid Shopify/Matrixify CSV detected.")
            except SchemaError as e:
                # Prefer simple, actionable messages.
                missing = [
                    col for col in ("Title", "Handle") if col not in df.columns
                ]
                if missing:
                    st.error(f"Invalid CSV: Missing column(s): {', '.join(missing)}")
                else:
                    failure_summary = getattr(e, "failure_cases", None)
                    if failure_summary is not None and hasattr(failure_summary, "head"):
                        st.error(
                            "Invalid CSV: One or more required fields are empty or invalid. "
                            "Fix the highlighted column(s) and re-upload."
                        )
                    else:
                        st.error(f"Invalid CSV: {e}")
                st.stop()

            st.write(f"**Loaded {len(df)} rows.**")
            st.dataframe(df.head(3))
        except Exception as e:
            st.error(f"Error reading CSV: {e}")
            st.stop()

        if st.button("🚀 Start AI Generation (CSV Mode)"):
            if not api_key:
                st.error("Invalid API Key.")
                st.stop()
            
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
                    df[col] = "" # Initialize if missing

            # FAQs columns
            for i in range(1, 6):
                df[f'Metafield: custom.faq_title_{i} [single_line_text_field]'] = ""
                df[f'Metafield: custom.faq_desc_{i} [rich_text_field]'] = ""

            progress_bar = st.progress(0)
            status_container = st.status("Processing...", expanded=True)
            error_log = []

            total_process = len(rows_to_process)

            for row_num, (index, row) in enumerate(rows_to_process.iterrows(), start=1):
                progress_percent = row_num / total_process
                progress_bar.progress(min(progress_percent, 1.0))
                
                title = str(row.get('Title', ''))
                handle = str(row.get('Handle', ''))
                
                status_container.write(f"Processing: **{title}**")

                # Skip invalid or empty rows
                if pd.isna(title) or pd.isna(handle) or title == "":
                    continue
                
                # CALL AI
                data, error = generate_seo_content_with_retry(api_provider, api_key, model_option, title, handle)

                if data:
                    # MAPPING TO MATRIXIFY HEADERS
                    df.at[index, 'Metafield: title_tag [string]'] = data.get('title_tag', '')
                    df.at[index, 'Metafield: description_tag [string]'] = data.get('meta_description', '')
                    df.at[index, 'Metafield: custom.secondary_description [rich_text_field]'] = data.get('secondary_description', '')
                    
                    # Map FAQs
                    for i in range(1, 6):
                        df.at[index, f'Metafield: custom.faq_title_{i} [single_line_text_field]'] = data.get(f'faq_title_{i}', '')
                        df.at[index, f'Metafield: custom.faq_desc_{i} [rich_text_field]'] = data.get(f'faq_desc_{i}', '')

                    # Set Command to MERGE
                    df.at[index, 'Command'] = 'MERGE'
                    df.at[index, 'Processing Error'] = '' # Clear any previous error
                
                elif error:
                    df.at[index, 'Processing Error'] = error
                    error_log.append(f"Row {index} ({title}): {error}")

                time.sleep(request_delay) # Rate limit protection

            status_container.update(label="Processing Complete!", state="complete", expanded=False)
            st.success("✅ Batch Processing Complete!")
            
            if error_log:
                with st.expander("⚠️ Processing Errors (These rows were not updated)", expanded=True):
                    for err in error_log:
                        st.error(err)

            # Download Button
            csv_output = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Ready-for-Matrixify CSV",
                data=csv_output,
                file_name="matrixify_ready_seo_update.csv",
                mime="text/csv"
            )

# ==========================================
# TAB 2: DIRECT SHOPIFY SYNC
# ==========================================
with tab2:
    st.header("Sync Directly to Shopify")
    
    collection_type = st.radio("Select Collection Type to Sync:", ["Custom Collections", "Smart Collections"], horizontal=True)
    dry_run = st.checkbox("Dry Run (Generate Only, Do Not Save)", value=True, help="Preview the API payloads without modifying your store.")
    
    if not dry_run:
        st.warning("⚠️ You have disabled Dry Run. Changes will be pushed LIVE to Shopify.")

    if st.button("🔄 Sync Shopify Now"):
        if not (shopify_url and access_token and api_key):
            st.error("Please fill in API Key, Shop URL, and Access Token in the Sidebar.")
        else:
            status_container = st.status("Connecting to Shopify...", expanded=True)
            
            # 1. Setup Shopify Connection
            try:
                session = shopify.Session(shopify_url, "2024-01", access_token)
                shopify.ShopifyResource.activate_session(session)
                
                # 2. Fetch Collections
                status_container.write("Fetching collections...")
                
                if collection_type == "Custom Collections":
                    collections = shopify.CustomCollection.find()
                else:
                    collections = shopify.SmartCollection.find()
                
                status_container.write(f"Found {len(collections)} collections.")
                
                # 3. Process Loop
                sync_progress = st.progress(0)
                
                processed_count = 0
                
                for i, collection in enumerate(collections):
                    # Progress logic
                    sync_progress.progress((i + 1) / len(collections))
                    status_container.write(f"Analyzing: **{collection.title}**")
                    
                    # Generate AI Content
                    data, error = generate_seo_content_with_retry(api_provider, api_key, model_option, collection.title, collection.handle)
                    
                    if data:
                        if dry_run:
                            st.subheader(f"Dry Run: {collection.title}")
                            st.json(data)
                        else:
                            # SAVE TO SHOPIFY
                            # Helper to add/update metafield
                            def update_metafield(resource, namespace, key, value, type_name):
                                if not value: return
                                m = shopify.Metafield()
                                m.namespace = namespace
                                m.key = key
                                m.value = value
                                m.type = type_name
                                resource.add_metafield(m)

                            try:
                                # Standard SEO Metafields (usually namespace 'global' or 'seo')
                                # Matrixify uses 'title_tag' and 'description_tag' which maps to 'global' namespace often.
                                # Let's use 'global' for title/desc and 'custom' for the others.
                                update_metafield(collection, 'global', 'title_tag', data.get('title_tag'), 'string')
                                update_metafield(collection, 'global', 'description_tag', data.get('meta_description'), 'string')
                                
                                # Custom Data
                                update_metafield(collection, 'custom', 'secondary_description', data.get('secondary_description'), 'rich_text_field')
                                
                                for j in range(1, 6):
                                    update_metafield(collection, 'custom', f'faq_title_{j}', data.get(f'faq_title_{j}'), 'single_line_text_field')
                                    update_metafield(collection, 'custom', f'faq_desc_{j}', data.get(f'faq_desc_{j}'), 'rich_text_field')

                                collection.save()
                                st.success(f"Updated: {collection.title}")
                                
                            except Exception as save_err:
                                st.error(f"Failed to save {collection.title}: {save_err}")
                                
                    elif error:
                        st.error(f"AI Error for {collection.title}: {error}")

                    processed_count += 1
                    time.sleep(request_delay)
                
                status_container.update(label=f"Completed {processed_count} collections.", state="complete", expanded=False)
                
            except Exception as e:
                st.error(f"Shopify Connection Error: {e}")
                status_container.update(label="Connection Failed", state="error")
