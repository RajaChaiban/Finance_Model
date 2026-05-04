"""Greeks FD verification. Centered differences:
- vega: h_sigma = 0.005, FD = (P(sigma+h) - P(sigma-h)) / (2*h*100)  [per 1% absolute]
- theta: h_T_days = 1, FD = (P(T-1) - P(T+1)) / 2  [per calendar day, negative usually]
- rho: h_r = 0.005, FD = (P(r+h) - P(r-h)) / (2*h*100)  [per 1% absolute]
"""
import csv, json, time, urllib.request, urllib.error, os, math

BASE = "http://localhost:8002"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "curl_log.jsonl")
OUT = os.path.join(HERE, "greeks_fd_table.csv")


def post(step, body):
    url = BASE + "/api/price"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    except Exception as e:
        raw = "EXC " + str(e)
        status = 0
    ms = int((time.time() - t0) * 1000)
    rec = {
        "step": step,
        "method": "POST",
        "url": url,
        "status": status,
        "ms": ms,
        "body_excerpt": raw[:600].replace("\n", " "),
    }
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    try:
        return json.loads(raw)
    except Exception:
        return {"_error": raw[:200]}


def probe_one(option_type, S, K, sigma, T_days, r, q, extra=None):
    body = {
        "option_type": option_type,
        "underlying": "AAPL",
        "spot_price": S,
        "strike_price": K,
        "days_to_expiration": T_days,
        "risk_free_rate": r,
        "volatility": sigma,
        "dividend_yield": q,
    }
    if extra:
        body.update(extra)
    base = post(f"5.{option_type}-base", body)
    P0 = base.get("price")
    g = base.get("greeks") or {}
    vega_r = g.get("vega")
    theta_r = g.get("theta")
    rho_r = g.get("rho")

    h_sig = 0.005
    h_T = 1
    h_r = 0.005

    # vega FD
    body_up = dict(body, volatility=sigma + h_sig)
    body_dn = dict(body, volatility=sigma - h_sig)
    p_up = post(f"5.{option_type}-vega-up", body_up).get("price")
    p_dn = post(f"5.{option_type}-vega-dn", body_dn).get("price")
    if None in (P0, p_up, p_dn):
        vega_fd = float("nan")
    else:
        # per 1% absolute: divide by 2*h_sig*100 i.e. centered FD per absolute, scaled to per 1%
        vega_fd = (p_up - p_dn) / (2 * h_sig) / 100.0

    # theta FD: centered around T_days
    body_T_up = dict(body, days_to_expiration=T_days + h_T)
    body_T_dn = dict(body, days_to_expiration=T_days - h_T)
    p_T_up = post(f"5.{option_type}-theta-up", body_T_up).get("price")
    p_T_dn = post(f"5.{option_type}-theta-dn", body_T_dn).get("price")
    if None in (p_T_up, p_T_dn):
        theta_fd = float("nan")
    else:
        # As days_to_expiration grows, T increases — option price grows.
        # Theta = -dP/dt = -(P(T+1) - P(T-1)) / 2 (per calendar day moving toward expiry).
        theta_fd = -(p_T_up - p_T_dn) / 2.0

    # rho FD
    body_r_up = dict(body, risk_free_rate=r + h_r)
    body_r_dn = dict(body, risk_free_rate=r - h_r)
    p_r_up = post(f"5.{option_type}-rho-up", body_r_up).get("price")
    p_r_dn = post(f"5.{option_type}-rho-dn", body_r_dn).get("price")
    if None in (p_r_up, p_r_dn):
        rho_fd = float("nan")
    else:
        rho_fd = (p_r_up - p_r_dn) / (2 * h_r) / 100.0

    return [option_type, S, K, sigma, T_days, r, q, vega_r, vega_fd, theta_r, theta_fd, rho_r, rho_fd]


def main():
    rows = []
    rows.append(probe_one("european_call", 100, 100, 0.20, 90, 0.05, 0.02))
    rows.append(probe_one("american_put", 100, 100, 0.20, 90, 0.05, 0.02))
    rows.append(probe_one("knockout_call", 100, 100, 0.20, 90, 0.05, 0.02, {"barrier_level": 120}))

    with open(OUT, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "option_type", "S", "K", "sigma", "T_days", "r", "q",
            "vega_reported", "vega_fd",
            "theta_reported", "theta_fd",
            "rho_reported", "rho_fd",
        ])
        for r_ in rows:
            w.writerow(r_)
    for r_ in rows:
        print("|".join(str(x) for x in r_))


if __name__ == "__main__":
    main()
