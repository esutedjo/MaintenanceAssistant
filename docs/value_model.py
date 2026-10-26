"""Cleris value model for a reference 100-aircraft narrowbody operator.
All money in USD per year at steady state unless stated.
"""
import random, statistics, copy, json

EUR = 1.05  # assumed USD/EUR (2022 average), from research notes
CANX_180 = 25720 * EUR   # EUROCONTROL 2022, 180 seats
CANX_120 = 16640 * EUR   # EUROCONTROL 2022, 120 seats
OWN_DAY = 400000 * 12 / 365  # new A320neo/737 MAX lease ~$400k/month (Cirium)
ENG_LOADED = {"c": 85000 / 0.701, "b": 100000 / 0.701, "u": 134960 / 0.701}
ENG_HOURLY = {k: v / 2080 for k, v in ENG_LOADED.items()}

def scen(k):
    c, b, u = "c", "b", "u"
    pick = lambda cv, bv, uv: {"c": cv, "b": bv, "u": uv}[k]
    return dict(
        aircraft=100, eng_per_ac=2, fh_per_ac=3300,
        uer=pick(0.026, 0.028, 0.042),          # per 1,000 EFH
        svr=pick(0.032, 0.035, 0.0525),         # per 1,000 EFH
        s_cov=pick(0.10, 0.25, 0.40),
        tpr=pick(0.4, 0.6, 0.8),
        alpha=pick(0.10, 0.30, 0.50),
        # per converted event
        canx_n=pick(0.5, 1, 4), canx_cost=pick(CANX_120, CANX_180, CANX_180),
        aog_days=pick(0.5, 1, 3), own_day=OWN_DAY,
        lease_days=pick(0, 30, 75), lease_prem=pick(1500, 1500, 1900),
        logistics=pick(0, 10000, 50000),
        p_sd=pick(0.02, 0.05, 0.10), c_sd=pick(500000, 1000000, 2000000),
        gt_efc=pick(60, 30, 15), gt_rate=pick(500, 400, 300),
        # lever B: cover timing on every removal
        cover_days=pick(0, 2, 4), cover_rate=pick(5000, 5000, 6500),
        # lever C: engineer time
        dec_per_eng=0.6, hrs_per_dec=pick(1, 2, 4), saved=pick(0.25, 0.40, 0.60),
        rate=ENG_HOURLY[k],
        # lever D: compliance / audit
        p_ad=pick(0.005, 0.01, 0.02), sev_ad=pick(200000, 400000, 2000000),
        red_ad=pick(0.10, 0.25, 0.50),
        audit_hrs=pick(4, 8, 16), audit_saved=pick(0.25, 0.25, 0.40),
        # false alarms
        p_true=0.15,
        r_insp=pick(41/102, 41/102, 41/102/2), r_rem=pick(1/102, 1/102, 1/102/2),
        act_insp=pick(1.0, 0.75, 0.5), act_rem=pick(1.0, 0.5, 0.25),
        insp_lab=pick(2000, 1000, 340), p_disr=pick(0.10, 0.03, 0.0),
        disr_cost=CANX_180,
        rem_handling=pick(60000, 29000, 8000), rem_efc=pick(400, 160, 80),
        rem_rate=pick(500, 400, 300),
        # implementation
        infra_per_eng=pick(1000, 450, 100), fte=pick(0.5, 0.25, 0.1),
        fte_cost=ENG_LOADED["b"],
        integration=pick(600000, 300000, 150000),
        adoption=pick([0.1, 0.3, 0.6, 0.8, 0.8], [0.25, 0.6, 0.85, 1, 1], [0.5, 0.9, 1, 1, 1]),
    )

def model(p, detail=False):
    engines = p["aircraft"] * p["eng_per_ac"]
    efh = engines * p["fh_per_ac"]
    n_uer = efh / 1000 * p["uer"]
    n_rem = efh / 1000 * p["svr"]
    n_conv = n_uer * p["s_cov"] * p["tpr"] * p["alpha"]
    dc = dict(
        disruption=p["canx_n"] * p["canx_cost"] + p["aog_days"] * p["own_day"],
        lease=p["lease_days"] * p["lease_prem"],
        logistics=p["logistics"],
        secondary=p["p_sd"] * p["c_sd"],
        greentime=-p["gt_efc"] * p["gt_rate"],
    )
    dC = sum(dc.values())
    A = n_conv * dC
    B = p["alpha"] * (n_rem - n_conv) * p["cover_days"] * p["cover_rate"]
    decisions = p["dec_per_eng"] * engines
    C = decisions * p["hrs_per_dec"] * p["saved"] * p["rate"]
    D_ad = p["p_ad"] * p["aircraft"] / 100 * p["sev_ad"] * p["red_ad"]
    D_audit = n_rem * p["audit_hrs"] * p["audit_saved"] * p["rate"]
    D = D_ad + D_audit
    non_fail = decisions * (1 - p["p_true"])
    fa_i_n = non_fail * p["r_insp"] * p["act_insp"]
    c_insp = p["insp_lab"] + p["p_disr"] * p["disr_cost"]
    E = fa_i_n * c_insp
    fa_r_n = non_fail * p["r_rem"] * p["act_rem"]
    c_rem = p["rem_handling"] + p["rem_efc"] * p["rem_rate"]
    F = fa_r_n * c_rem
    run = engines * p["infra_per_eng"] + p["fte"] * p["fte_cost"]
    variable = A + B + C + D - E - F
    net = variable - run
    out = dict(engines=engines, efh=efh, n_uer=n_uer, n_rem=n_rem, n_conv=n_conv,
               dc=dc, dC=dC, A=A, B=B, C=C, D_ad=D_ad, D_audit=D_audit, D=D,
               fa_i_raw=non_fail * p["r_insp"], fa_i_n=fa_i_n, c_insp=c_insp, E=E,
               fa_r_raw=non_fail * p["r_rem"], fa_r_n=fa_r_n, c_rem=c_rem, F=F,
               run=run, gross=A + B + C + D, variable=variable, net=net)
    # five-year cumulative, undiscounted; integration in year 1; run full every year
    cum = -p["integration"]
    yearly = []
    for a in p["adoption"]:
        y = a * variable - run
        yearly.append(y)
        cum += y
    out["yearly"] = yearly
    out["cum5"] = cum
    return out

def fmt(x):
    return f"{x:,.1f}"

S = {k: scen(k) for k in "cbu"}
R = {k: model(S[k]) for k in "cbu"}
names = {"c": "Conservative", "b": "Base", "u": "Upside"}
for k in "cbu":
    r = R[k]
    print(f"=== {names[k]} ===")
    print(f"EFH {r['efh']:,.0f}; UER events {r['n_uer']:.3f}; all removals {r['n_rem']:.3f}; converted {r['n_conv']:.4f} ({r['n_conv']/r['n_uer']*100:.2f}% of UER)")
    print("dC components:", {kk: round(v, 1) for kk, v in r["dc"].items()}, "dC =", round(r["dC"], 1))
    print(f"A conversion {fmt(r['A'])}; B cover timing {fmt(r['B'])}; C engineer {fmt(r['C'])}; D compliance {fmt(r['D'])} (AD {fmt(r['D_ad'])}, audit {fmt(r['D_audit'])})")
    print(f"Gross benefits {fmt(r['gross'])}")
    print(f"E false inspections: raw {r['fa_i_raw']:.3f}, acted {r['fa_i_n']:.3f} x {fmt(r['c_insp'])} = {fmt(r['E'])}")
    print(f"F early pulls: raw {r['fa_r_raw']:.4f}, acted {r['fa_r_n']:.4f} x {fmt(r['c_rem'])} = {fmt(r['F'])}")
    print(f"Run cost {fmt(r['run'])}; variable net {fmt(r['variable'])}; NET steady state {fmt(r['net'])}; per aircraft {fmt(r['net']/100)}")
    print("Yearly:", [round(y) for y in r["yearly"]], "5yr cum incl integration", fmt(r["cum5"]))
    print()

# Break-evens in base
b = S["b"]; rb = R["b"]
other = rb["B"] + rb["C"] + rb["D"] - rb["E"] - rb["F"] - rb["run"]
be_conv = -other / rb["dC"]
print("Base break-even conversions/yr (excl integration):", round(be_conv, 3), "share of UER", round(be_conv / rb["n_uer"] * 100, 2), "%")
be_conv_int = (-other + b["integration"] / 5) / rb["dC"]
print("Base break-even conversions/yr incl integration/5:", round(be_conv_int, 3), "share", round(be_conv_int / rb["n_uer"] * 100, 2), "%")

# break-even alpha (bisection)
def net_with(pp, **kw):
    q = copy.deepcopy(pp); q.update(kw); return model(q)["net"]
lo, hi = 0.0, 1.0
for _ in range(60):
    mid = (lo + hi) / 2
    if net_with(b, alpha=mid) > 0: hi = mid
    else: lo = mid
print("Base break-even alpha:", round(hi, 4))
q = copy.deepcopy(b); q["integration"] = 0
# include integration amortised over 5 yrs
lo, hi = 0.0, 1.0
for _ in range(60):
    mid = (lo + hi) / 2
    if net_with(b, alpha=mid) - b["integration"] / 5 > 0: hi = mid
    else: lo = mid
print("Base break-even alpha incl integration/5:", round(hi, 4))

# break-even fleet size, run cost treated as fixed at its 100-aircraft value
fixed = rb["run"]
var_per_ac = rb["variable"] / 100
print("Base variable net per aircraft:", round(var_per_ac, 1), "fixed run", round(fixed), "break-even fleet", round(fixed / var_per_ac, 1),
      "incl integration/5:", round((fixed + b["integration"] / 5) / var_per_ac, 1))
for k in "cbu":
    r = R[k]
    print(names[k], "variable per aircraft", round(r["variable"] / 100, 1), "break-even licence per engine/yr", round(max(0, r["net"]) / r["engines"], 1),
          "incl integration/5", round(max(0, r["net"] - S[k]["integration"] / 5) / r["engines"], 1))

# fleet scaling for base: 50, 100, 200, 400 aircraft, run cost fixed vs per-engine
for n in [50, 100, 200, 400]:
    q = copy.deepcopy(b); q["aircraft"] = n
    r_per_engine = model(q)
    fixed_net = r_per_engine["variable"] - rb["run"]
    print(f"Base fleet {n}: variable {fmt(r_per_engine['variable'])}; net if run cost per-engine {fmt(r_per_engine['net'])}; net if run cost fixed {fmt(fixed_net)}")
for n in [50, 100, 200, 400]:
    q = copy.deepcopy(S["u"]); q["aircraft"] = n
    r2 = model(q)
    print(f"Upside fleet {n}: net per-engine-run {fmt(r2['net'])}")
for n in [50, 100, 200, 400]:
    q = copy.deepcopy(S["c"]); q["aircraft"] = n
    r2 = model(q)
    print(f"Cons fleet {n}: net per-engine-run {fmt(r2['net'])}")

# Tornado on base
groups = {
    "Attribution alpha": ["alpha"],
    "Coverage s_cov": ["s_cov"],
    "Detection TPR": ["tpr"],
    "Removal rates UER+SVR": ["uer", "svr"],
    "Cancellations per event (n, cost)": ["canx_n", "canx_cost"],
    "AOG days per event": ["aog_days"],
    "Short-notice lease (days, premium)": ["lease_days", "lease_prem"],
    "Emergency logistics": ["logistics"],
    "Secondary damage (P, cost)": ["p_sd", "c_sd"],
    "Green time on conversions": ["gt_efc", "gt_rate"],
    "Cover timing (days, rate)": ["cover_days", "cover_rate"],
    "Engineer time (hrs, share, rate)": ["hrs_per_dec", "saved", "rate"],
    "Compliance and audit": ["p_ad", "sev_ad", "red_ad", "audit_hrs", "audit_saved"],
    "False-inspection rate": ["r_insp"],
    "False inspections acted on": ["act_insp"],
    "False-inspection cost": ["insp_lab", "p_disr"],
    "Early-pull rate": ["r_rem"],
    "Early pulls acted on": ["act_rem"],
    "Early-pull cost": ["rem_handling", "rem_efc", "rem_rate"],
    "Run cost (infra, FTE)": ["infra_per_eng", "fte"],
}
rows = []
for g, keys in groups.items():
    lowp = copy.deepcopy(b); highp = copy.deepcopy(b)
    for kk in keys:
        lowp[kk] = S["c"][kk]; highp[kk] = S["u"][kk]
    nl = model(lowp)["net"]; nh = model(highp)["net"]
    rows.append((g, nl, nh, abs(nh - nl)))
rows.sort(key=lambda r: -r[3])
print("\nTornado (base net = %s)" % fmt(rb["net"]))
for g, nl, nh, sw in rows:
    print(f"{g:40s} cons-value net {fmt(nl):>12s}  upside-value net {fmt(nh):>12s}  swing {fmt(sw):>12s}")

# Stress: doubled false-alarm rates in base
q = copy.deepcopy(b); q["r_insp"] *= 2; q["r_rem"] *= 2
print("\nBase with doubled false-alarm rates:", fmt(model(q)["net"]))
q = copy.deepcopy(b); q["act_insp"] = 1.0; q["act_rem"] = 1.0
print("Base with every false call acted on:", fmt(model(q)["net"]))
q = copy.deepcopy(b); q["p_sd"] = 0
print("Base with no secondary damage:", fmt(model(q)["net"]))
# monthly fleet sweep at C-MAPSS rates
sweeps = 12 * 200 * (41/102)
print("Monthly fleet sweep false inspections/yr at C-MAPSS rate:", round(sweeps), "per sweep", round(200 * 41/102, 1))

# Monte Carlo: independent triangular draws between cons and upside, mode at base
random.seed(20261026)
num_keys = [k for k in b if isinstance(b[k], (int, float)) and not isinstance(b[k], bool)]
vary = [k for k in num_keys if not (S["c"][k] == S["b"][k] == S["u"][k]) and k != "integration"]
nets = []
for _ in range(20000):
    q = copy.deepcopy(b)
    for kk in vary:
        lo_, hi_ = min(S["c"][kk], S["u"][kk]), max(S["c"][kk], S["u"][kk])
        md = S["b"][kk]
        md = min(max(md, lo_), hi_)
        q[kk] = random.triangular(lo_, hi_, md)
    nets.append(model(q)["net"])
nets.sort()
pct = lambda p: nets[int(p * len(nets))]
print("\nMC varied params:", vary)
print("MC P10 %s P50 %s P90 %s mean %s P(net>0) %.3f" % (fmt(pct(0.10)), fmt(pct(0.50)), fmt(pct(0.90)), fmt(statistics.mean(nets)), sum(1 for n in nets if n > 0) / len(nets)))

print("\nKey constants: CANX_180", round(CANX_180), "CANX_120", round(CANX_120), "OWN_DAY", round(OWN_DAY, 1))
print("Loaded:", {k: round(v) for k, v in ENG_LOADED.items()}, "Hourly:", {k: round(v, 2) for k, v in ENG_HOURLY.items()})

print("\nBase with alpha=1 (fix-on-failure style counterfactual):", fmt(net_with(b, alpha=1.0)))
print("Upside with alpha=1:", fmt(net_with(S["u"], alpha=1.0)))
for k in "cbu":
    r = R[k]
    g = r["gross"]
    print(names[k], "gross shares A %.1f%% B %.1f%% C %.1f%% D %.1f%%" % (r["A"]/g*100 if g else 0, r["B"]/g*100 if g else 0, r["C"]/g*100 if g else 0, r["D"]/g*100 if g else 0),
          "gross per aircraft", fmt(g/100), "gross per engine", fmt(g/200), "costs total", fmt(r["E"]+r["F"]+r["run"]))
print("One secondary damage event / base run cost:", round(1000000 / R["b"]["run"], 2))
nets2 = []
random.seed(20261026)
for _ in range(20000):
    q = copy.deepcopy(b)
    for kk in vary + ["integration"]:
        lo_, hi_ = min(S["c"][kk], S["u"][kk]), max(S["c"][kk], S["u"][kk])
        md = min(max(S["b"][kk], lo_), hi_)
        q[kk] = random.triangular(lo_, hi_, md)
    nets2.append(model(q)["net"] - q["integration"] / 5)
nets2.sort()
print("MC incl integration/5: P10 %s P50 %s P90 %s P(>0) %.3f" % (fmt(nets2[2000]), fmt(nets2[10000]), fmt(nets2[18000]), sum(1 for n in nets2 if n > 0)/len(nets2)))
# widebody ratio of cancellation costs
print("WB/NB cancellation ratio 250 seats:", round(85570/25720, 2), "400 seats:", round(123900/25720, 2))
