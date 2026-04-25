# Running in Google Colab

## Option 1: Direct Upload (Easiest)

1. Go to [Google Colab](https://colab.research.google.com)
2. Click **File → Upload notebook**
3. Select `Knockout_Option_Pricer_SP500.ipynb`
4. Click **Runtime → Run all** (or Ctrl+F9)
5. Wait for all cells to execute (first run takes ~2 minutes due to yfinance download)

**Note:** First execution will install QuantLib, which takes 30-40 seconds.

---

## Option 2: From GitHub (After Push)

Once the repo is on GitHub:

1. Go to [Google Colab](https://colab.research.google.com)
2. Click **File → Open notebook → GitHub**
3. Paste your repo URL: `github.com/RajaChaiban/Knockout_Option_Pricer`
4. Select `Knockout_Option_Pricer_SP500.ipynb`
5. Click **Runtime → Run all**

---

## Option 3: Mount Google Drive (For Saving Results)

```python
from google.colab import drive
drive.mount('/content/drive')

# Then save outputs like:
# plt.savefig('/content/drive/My Drive/knockout_charts.png')
```

---

## Troubleshooting

**Issue: "QuantLib not found"**
- Solution: Cell 1 runs `!pip install QuantLib -q` automatically. Just wait 30-40 seconds.

**Issue: "yfinance download fails"**
- This is due to network timeouts. Cell 3 has error handling. Try running it again.
- Alternative: Use cached data from a known S&P 500 price

**Issue: "Matplotlib fonts missing"**
- Solution: This is cosmetic. Charts will still display, just with default fonts.

**Issue: "Memory error"**
- Solution: Colab has 12GB RAM. This notebook uses ~200MB. Not an issue.

---

## Next Steps in Colab

After running all cells successfully:

1. **Modify parameters** - Change barrier levels, volatility, time to expiration in Cell 3
2. **Re-run pricing** - Cells 4-5 will recalculate with new parameters
3. **Save charts** - Right-click any chart → Save image
4. **Export results** - Cell 5 creates a Pandas DataFrame you can download

### To change parameters:
```python
# In Cell 3, modify:
barrier_call = spot_price * 0.85  # Change from 0.90
barrier_put = spot_price * 1.15   # Change from 1.10
days_to_expiration = 60           # Change from 90
risk_free_rate = 0.05             # Change from 0.045
```

Then re-run cells 4-7 to see new results.

---

## Sharing Results

**To share with your VP:**

1. Run all cells in Colab
2. Cells 6-7 produce charts - right-click and save as PNG
3. Create a PowerPoint with:
   - Slide 1: Charts from Cell 6 (payoffs and Greeks)
   - Slide 2: Comparison table from Cell 7 (manual vs QuantLib)
   - Slide 3: README explanation (copy from this repo)

---

## Environment Specs (For Reference)

| Item | Spec |
|------|------|
| Python | 3.10+ (Colab default) |
| NumPy | 1.24+ |
| SciPy | 1.10+ |
| Pandas | 1.5+ |
| Matplotlib | 3.7+ |
| yfinance | 0.2+ |
| QuantLib | 1.28+ |

All are auto-installed by Cell 1.

---

## Local Jupyter (Alternative)

If you prefer running locally:

```bash
# Clone and setup
git clone <repo-url>
cd Knockout_Option_Pricer
pip install -r requirements.txt

# Or install manually:
pip install numpy scipy pandas matplotlib yfinance QuantLib

# Run notebook
jupyter notebook Knockout_Option_Pricer_SP500.ipynb
```

---

**Questions?** See README.md or the inline notebook comments.
