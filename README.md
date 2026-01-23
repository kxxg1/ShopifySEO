# ShopifySEO

Streamlit app for generating and syncing SEO content to Shopify collections (CSV via Matrixify or direct Shopify API).

## Quick start

1) Create a virtualenv + install deps:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2) Configure Streamlit secrets:

```bash
mkdir -p .streamlit
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml` and set:

- `GOOGLE_API_KEY` (if using Google Gemini)
- `PERPLEXITY_API_KEY` (if using Perplexity Sonar)
- `SHOPIFY_SHOP_URL` and `SHOPIFY_ACCESS_TOKEN` (only needed for Direct Sync)

3) Run the app:

```bash
streamlit run app.py
```

## Security

- `.streamlit/secrets.toml` is intentionally git-ignored.
- Never commit API keys or Shopify admin tokens.
