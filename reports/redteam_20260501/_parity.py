"""Parity probe: KO+KI=EU. Writes parity_table.csv and appends to curl_log.jsonl."""
import csv, json, time, urllib.request, urllib.error, os, sys

BASE = "http://localhost:8002"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "curl_log.jsonl")
PARITY = os.path.join(HERE, "parity_table.csv")


def post(step, path, body):
    url = BASE + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
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
        d = json.loads(raw)
    except Exception:
        d = {"_error": raw[:300]}
    return status, d


def run_parity(tid, cp, S, K, B, T, sig, r, q, smile, underlying="AAPL"):
    suffix = "_call" if cp == "call" else "_put"
    base_body = {
        "underlying": underlying,
        "spot_price": S,
        "strike_price": K,
        "days_to_expiration": T,
        "volatility": sig,
        "risk_free_rate": r,
        "dividend_yield": q,
    }
    if smile:
        base_body["use_vol_surface"] = True

    eu_body = dict(base_body, option_type="european" + suffix)
    ko_body = dict(base_body, option_type="knockout" + suffix, barrier_level=B)
    ki_body = dict(base_body, option_type="knockin" + suffix, barrier_level=B)

    s, eu = post(f"2.{tid}-{cp}-eu", "/api/price", eu_body)
    s, ko = post(f"2.{tid}-{cp}-ko", "/api/price", ko_body)
    s, ki = post(f"2.{tid}-{cp}-ki", "/api/price", ki_body)
    eu_p = eu.get("price")
    ko_p = ko.get("price")
    ki_p = ki.get("price")
    if eu_p is None or ko_p is None or ki_p is None:
        err = float("nan")
    else:
        err = abs(ko_p + ki_p - eu_p)
    return [tid + "-" + cp, cp, S, K, B, T, sig, r, q, ko_p, ki_p, eu_p, err, smile]


def main():
    rows = []
    rows.append(run_parity("a", "call", 100, 100, 120, 180, 0.20, 0.05, 0.02, False))
    rows.append(run_parity("b", "call", 100, 110, 90, 180, 0.20, 0.05, 0.02, False))
    rows.append(run_parity("c", "call", 100, 95, 115, 365, 0.30, 0.05, 0.02, False))
    rows.append(run_parity("a", "put", 100, 100, 120, 180, 0.20, 0.05, 0.02, False))
    rows.append(run_parity("b", "put", 100, 110, 90, 180, 0.20, 0.05, 0.02, False))
    rows.append(run_parity("c", "put", 100, 95, 115, 365, 0.30, 0.05, 0.02, False))
    rows.append(run_parity("a-smile", "call", 100, 100, 120, 180, 0.20, 0.05, 0.02, True))

    with open(PARITY, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "tuple_id", "call_or_put", "S", "K", "B", "T_days",
            "sigma", "r", "q", "ko_price", "ki_price", "eu_price",
            "abs_err", "smile_on",
        ])
        for r_ in rows:
            w.writerow(r_)

    for r_ in rows:
        print("|".join(str(x) for x in r_))


if __name__ == "__main__":
    main()
