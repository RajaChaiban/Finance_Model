# StructuredFinance.AI

An AI-powered market intelligence platform for structured finance. Instantly answer questions about market conditions, pricing benchmarks, deal comparables, and issuance windows.

## Overview

StructuredFinance.AI is a Python-based system that combines:
- **Vector embeddings** for semantic document retrieval
- **Large language models** (Claude) for synthesis and analysis
- **Market data ingestion** from public sources
- **Query interface** for instant market insights

The system is designed to be:
- **Modular**: Easy to extend with new data sources, models, or query types
- **ML-Ready**: Architecture supports adding ML models for pricing, classification, and forecasting
- **Scalable**: Supports both local (Chroma) and cloud (Pinecone) vector stores
- **Extensible**: Simple interfaces for adding new document types, retrieval strategies, and LLM features

## Quick Start

### 1. Installation

```bash
# Clone or navigate to project
cd StructuredFinanceAI

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

```bash
# Copy example env file
cp .env.example .env

# Edit .env and add your Anthropic API key
# ANTHROPIC_API_KEY=sk-ant-...
```

Get your API key from: https://console.anthropic.com

### 3. Run

```bash
# Run demo with sample data
python main.py

# Or enter interactive mode
# Commands: market [asset], price [asset], query [text], quit
```

## Architecture

### Core Components

```
┌─────────────────────────────────────────────┐
│         Query Handler                       │
│  (Main user-facing interface)               │
└─────────────────┬───────────────────────────┘
                  │
      ┌───────────┴───────────┐
      │                       │
┌─────▼──────────┐   ┌───────▼──────────┐
│ Retrieval      │   │ LLM Client       │
│ Engine         │   │ (Claude)         │
│                │   │                  │
└─────┬──────────┘   └────────┬─────────┘
      │                       │
┌─────▼──────────┐   ┌───────▼──────────┐
│ Vector Store   │   │ Prompt Manager   │
│ (Chroma/       │   │                  │
│  Pinecone)     │   │                  │
└─────┬──────────┘   └──────────────────┘
      │
┌─────▼──────────────────────────┐
│  Document Storage              │
│  (Embeddings + Metadata)       │
└────────────────────────────────┘

Data Flow:
Document → Load → Chunk → Embed → Store
Query → Embed → Retrieve → Context → LLM → Answer
```

### Package Structure

```
src/
├── config.py                # Configuration management
├── llm/
│   ├── claude_client.py     # Claude API wrapper
│   └── prompts.py           # Prompt templates
├── vector_store/
│   ├── vector_db.py         # Vector store implementations
│   └── embeddings.py        # Embeddings manager
├── data_ingestion/
│   ├── data_loader.py       # File loading & parsing
│   └── sec_scraper.py       # SEC data fetching
├── query/
│   ├── query_handler.py     # Main query interface
│   └── retrieval.py         # Document retrieval
└── pipeline/                # (Future: market intelligence workflows)
```

## Usage Examples

### Basic Query

```python
from src import (
    get_settings,
    ClaudeClient,
    ChromaVectorStore,
    QueryHandler
)

# Initialize
settings = get_settings()
vector_store = ChromaVectorStore()
llm_client = ClaudeClient()
query_handler = QueryHandler(vector_store, llm_client)

# Ask a question
response = query_handler.general_query(
    query="What's the current CLO market window?",
    asset_class="CLO"
)

print(response.answer)
print(f"Confidence: {response.confidence}")
print(f"Sources: {len(response.sources)} documents")
```

### Market Window Query

```python
response = query_handler.query_market_window(
    asset_class="CLO",
    context="Given current Fed policy..."
)
```

### Pricing Query

```python
response = query_handler.query_pricing(
    asset_class="CLO",
    tranche_type="BBB",
    deal_size=400,
    collateral_info={"type": "Corporate Loans", "quality": "BB-rated"}
)
```

### Deal Analysis

```python
deal_info = {
    "name": "Tech Portfolio CLO 2025-I",
    "asset_class": "CLO",
    "size": 500,
    "collateral": "Technology sector loans",
    "tranches": [
        {"name": "AAA", "size": 250, "pricing": "+100bps"},
        {"name": "BBB", "size": 150, "pricing": "+310bps"}
    ]
}

response = query_handler.query_deal_analysis(
    deal_summary=deal_info,
    asset_class="CLO"
)
```

### Load Documents

```python
from src import DataLoader

loader = DataLoader()

# Load PDFs
docs = loader.load_pdf("path/to/prospectus.pdf", doc_type="market_window")

# Load directory
docs = loader.load_directory("./data/raw", file_patterns=["*.pdf"])

# Add to vector store
vector_store.add_documents(docs)
```

## Data Ingestion

### Supported Formats

- **PDF**: Prospectuses, legal docs, reports
- **JSON**: Structured market data, deal information
- **Text**: Market commentary, analyses
- **Directories**: Batch load multiple files

### Example: Seeding with Deal Data

```python
from src.data_ingestion import DataLoader
from src.vector_store import Document

loader = DataLoader()

deal_data = {
    "name": "CLO 2025-I",
    "asset_class": "CLO",
    "size": 500,
    "date": "2025-04-15",
    "collateral": "Corporate loans",
    "tranches": [...],
    "rating_agency": "Moody's"
}

# Convert to document
doc = loader.load_deal_data(deal_data)

# Add to store
vector_store.add_documents([doc])
```

## Configuration

All settings are in `src/config.py` and can be overridden via `.env`:

```python
from src import get_settings

settings = get_settings()
print(settings.claude_model)  # claude-3-sonnet-20240229
print(settings.chunk_size)    # 1000
```

Key settings:
- `LLM_PROVIDER`: anthropic or openai
- `CLAUDE_MODEL`: Which Claude model to use
- `VECTOR_STORE_TYPE`: chroma or pinecone
- `INCLUDE_ASSET_CLASSES`: List of asset classes to track
- `CHUNK_SIZE`: Document chunk size for embeddings

## Adding ML Models

The architecture is ready for ML extensions:

### 1. Pricing Model Integration

```python
from sklearn.ensemble import RandomForestRegressor
import pickle

# Train your model
model = RandomForestRegressor()
model.fit(X_train, y_train)

# Save
pickle.dump(model, open("models/pricing_model.pkl", "wb"))

# Use in pipeline
class PricingPipeline:
    def __init__(self):
        self.model = pickle.load(open("models/pricing_model.pkl", "rb"))
    
    def predict_pricing(self, deal_features):
        return self.model.predict([deal_features])
```

### 2. Deal Classification

```python
from transformers import pipeline

# Use huggingface for deal classification
classifier = pipeline("zero-shot-classification")

deal_text = "..."
result = classifier(deal_text, ["CLO", "RMBS", "CMBS"])
```

### 3. Market Sentiment Analysis

```python
from transformers import pipeline

sentiment = pipeline("sentiment-analysis")
news_text = "..."
result = sentiment(news_text)
```

## API Server (Optional)

To add a web API:

```python
# api/app.py
from fastapi import FastAPI
from src import QueryHandler

app = FastAPI()
query_handler = QueryHandler(vector_store, llm_client)

@app.post("/query")
async def query(query_text: str, asset_class: str = "CLO"):
    response = query_handler.general_query(query_text, asset_class)
    return response.to_dict()

@app.post("/market-window")
async def market_window(asset_class: str):
    response = query_handler.query_market_window(asset_class)
    return response.to_dict()
```

Run with:
```bash
uvicorn src.api.app:app --reload
```

## Performance & Scaling

### Local Development (Chroma)
- Suitable for < 100k documents
- Fast prototyping
- No external dependencies

### Production (Pinecone)
- Suitable for > 1M documents
- Cloud-hosted with uptime SLA
- Automatic scaling
- Requires API key

## Troubleshooting

### "ANTHROPIC_API_KEY not found"
```bash
# Make sure .env file exists and has the key
cat .env | grep ANTHROPIC_API_KEY

# Or set directly
export ANTHROPIC_API_KEY=sk-ant-...
```

### "No relevant documents found"
- Seed with more sample data
- Check vector store has documents: `vector_store.count()`
- Adjust retrieval threshold in `query_handler.py`

### Slow queries
- Reduce `chunk_size` to increase retrieval speed
- Use Pinecone instead of local Chroma
- Add indexing to frequently searched fields

## Roadmap

- [ ] **Data Pipeline**: Automated daily ingestion from Bloomberg, SEC, dealers
- [ ] **ML Models**: Pricing prediction, deal classification, market sentiment
- [ ] **Monitoring**: Alert system for market changes
- [ ] **Dashboard**: Web UI for browsing market intelligence
- [ ] **Slack Integration**: Query via Slack `/market-window` commands
- [ ] **Email Reports**: Automated daily market briefs
- [ ] **Fine-tuning**: Custom LLM models trained on SF data

## Contributing

```bash
# Run tests
pytest tests/

# Format code
black src/

# Lint
flake8 src/
```

## License

MIT

## Support

For issues or questions:
1. Check the [troubleshooting section](#troubleshooting)
2. Review example usage in `main.py`
3. Check config defaults in `src/config.py`

## Next Steps

1. **Configure**: Add your ANTHROPIC_API_KEY to `.env`
2. **Run**: `python main.py` to test with sample data
3. **Load Data**: Add your own documents with `DataLoader`
4. **Extend**: Add ML models for pricing/classification
5. **Deploy**: Set up API server and integrate with your systems
