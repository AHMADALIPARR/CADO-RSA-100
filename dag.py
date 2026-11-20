#!/usr/bin/env python3
"""Recursive DAG driver for GNFS factorization (CADO-NFS orchestration).

Top level: the GNFS pipeline is a DAG of stages, each depending on the
previous stage's output:
    polyselect -> factorbase -> freerel -> sieve -> dup1 -> dup2 -> purge
    -> merge -> replay -> linalg -> characters -> sqrt -> verify

Recursive: each stage decomposes into its own sub-DAG of independent
tasks. Sieving splits into independent q-interval nodes that all feed
filtering; polynomial selection splits into independent admin-range
nodes that feed root optimization; duplicate removal splits into
independent slice nodes; square root splits into one node per kernel
dependency; linear algebra (bwc) splits into independent m x n blocks
that feed the generator (inside bwc.pl: prep -> krylov -> lingen ->
mksol). The whole graph is topologically ordered, each node is computed
once from its predecessors, and independent nodes in the same layer run
in parallel (thread pool).

The owner layer (RSA-Prime, Factor) is part of the graph as nodes:
    rsaprime-gnfs-setup  (cross-check of the GNFS setup, runs early,
                          in parallel with polyselect)
    rsaprime-qs          (QS cross-check on the calibration target)
    rsaprime-bqn         (BQN array kernels -- attempted, blocked on
                          this VM's CBQN rejecting the owner's nabla/
                          theta identifiers; recorded, not faked)

This driver invokes the CADO-NFS binaries directly (it does NOT rewrite
CADO-NFS). Command patterns are the exact ones the cado-nfs.py driver
itself uses, as recorded in the calibration run's c60.cmd and workunit
database (see DAG.md). Parameters come from the CADO-NFS parameter file
(params.cXXX).

Three fan-outs are dynamic (the node count is only known at runtime),
so execution is phased; within each phase the nodes form a DAG layer
and run in parallel:
  Phase A  polyselect (admin chunks) -> ropt -> pick -> factorbase,
           freerel
  Phase B  sieve: q-interval nodes added in batches until the relation
           count reaches rels_wanted (or qmax)
  Phase C  dup1 -> dup2 (one node per slice, count from dup1 output)
           -> purge -> merge -> replay
  Phase D  linalg (bwc) -> characters -> sqrt (one node per kernel
           dependency) -> verify

Usage:
    python3 dag.py --params <params.cXXX> --n <number>
                   --workdir <dir> --bindir <build/htch-runtime>
                   --threads 2
"""

import argparse
import concurrent.futures
import gzip
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time

START = time.time()


def log(msg):
    print(f"[{time.time() - START:9.1f}s] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Parameter file parsing (CADO-NFS params.cXXX format: "key = value")
# ---------------------------------------------------------------------------

def parse_params(path):
    params = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if "#" in v:
                v = v.split("#", 1)[0].strip()
            try:
                if "." in v or "e" in v.lower():
                    params[k] = float(v)
                else:
                    params[k] = int(v)
            except ValueError:
                params[k] = v
    return params


def derived(params):
    """Derived values the cado-nfs.py driver computes from the params."""
    d = {}
    # Bf/Bg for Murphy-E scoring (driver: 2**lpb1 / 2**lpb0)
    d["Bf"] = float(2 ** int(params["tasks.lpb1"]))
    d["Bg"] = float(2 ** int(params["tasks.lpb0"]))
    # sieving area (driver: 2**(2*I-1) * qmin)
    I = int(params["tasks.I"])
    qmin = int(params["tasks.qmin"])
    d["area"] = float(2 ** (2 * I - 1) * qmin)
    # polyselect chunking: total admin range / chunk size
    admax = int(params["tasks.polyselect.admax"])
    adrange = int(params["tasks.polyselect.adrange"])
    d["poly_chunks"] = (admax + adrange - 1) // adrange
    d["admax"] = admax
    d["adrange"] = adrange
    # rels_wanted: explicit in the parameter file, else the driver's
    # fallback (cadotask.py SievingTask.__init__): guess_factor 0.81
    # times sum over sides of 2**lpb / ln(2**lpb).
    rw = int(params.get("tasks.sieve.rels_wanted", 0))
    if rw == 0:
        s = 0.0
        for side in (0, 1):
            n = 2 ** int(params[f"tasks.lpb{side}"])
            s += n / math.log(n)
        rw = int(0.81 * s)
    d["rels_wanted"] = rw
    return d


# ---------------------------------------------------------------------------
# Node execution (one node = one subprocess; stdout+stderr -> its log)
# ---------------------------------------------------------------------------

def run_cmd(name, argv, logpath, cwd, shell=False):
    """Run one DAG node. Returns (rc, wall_seconds)."""
    t0 = time.time()
    log(f"START {name}")
    if shell:
        p = subprocess.Popen(argv, shell=True, stdout=open(logpath, "wb"),
                             stderr=subprocess.STDOUT, cwd=cwd)
    else:
        p = subprocess.Popen(argv, stdout=open(logpath, "wb"),
                             stderr=subprocess.STDOUT, cwd=cwd)
    rc = p.wait()
    wall = time.time() - t0
    if rc == 0:
        log(f"DONE  {name} ({wall:.1f}s)")
    else:
        log(f"FAIL  {name} rc={rc} (see {logpath})")
    return rc, wall


def run_layer(nodes, threads, workdir):
    """Run a list of independent nodes in parallel.
    nodes: list of (name, argv, logpath, shell). Raises on any failure."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as ex:
        futs = {ex.submit(run_cmd, name, argv, logpath, workdir, shell): name
                for (name, argv, logpath, shell) in nodes}
        for fut in concurrent.futures.as_completed(futs):
            rc, _ = fut.result()
            if rc != 0:
                raise RuntimeError(f"DAG node failed: {futs[fut]}")


def exists_nonempty(path):
    """True if path exists and has nonzero size (resume check)."""
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def count_relations(rels_files):
    """Relations = non-comment lines in the (possibly gzipped) rels files."""
    total = 0
    for f in rels_files:
        if not os.path.exists(f):
            continue
        opener = gzip.open(f, "rt", errors="ignore") if f.endswith(".gz") \
            else open(f, "rt", errors="ignore")
        with opener as fh:
            for line in fh:
                if not line.startswith("#"):
                    total += 1
    return total


def parse_purge_stats(logpath):
    """Parse a purge log for the numbers the filter loop needs.
    Returns dict(nunique, ncols_start, excess_final), each None if
    the corresponding line is absent. nunique = relations read by
    purge (after dup1/dup2); ncols_start = distinct primes in the
    matrix before singleton removal ('begin with' line); excess_final
    = excess after singleton removal ('Final values' line)."""
    nunique = ncols_start = excess_final = None
    try:
        with open(logpath, "rt", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return {"nunique": None, "ncols_start": None, "excess_final": None}
    m = re.findall(r"# Done: Read (\d+) relations", text)
    if m:
        nunique = int(m[-1])
    m = re.search(r"begin with: nrows=\d+ ncols=(\d+) excess=(-?\d+)",
                  text)
    if m:
        ncols_start = int(m.group(1))
    m = re.findall(r"nrows=(\d+) ncols=(\d+) excess=(-?\d+)", text)
    if m:
        # last occurrence is the 'Final values' line
        excess_final = int(m[-1][2])
    return {"nunique": nunique, "ncols_start": ncols_start,
            "excess_final": excess_final}


def estimate_rels_target(nunique, ncols_start, Bf, Bg, total_rels,
                         target_ratio=1.08):
    """Estimate the TOTAL relations to sieve so purge succeeds.
    The matrix needs relations/distinct-primes above ~0.97 (c80
    succeeded at 0.976); we target 1.08 for margin. Distinct primes
    grow with relations as an occupancy curve D(R) =
    Dmax*(1-exp(-c*R)), with Dmax = pi(Bf)+pi(Bg) (large-prime
    bounds) and c fitted from the current measured (R, D) point.
    The model is validated on the c80 calibration: fitted from its
    final purge point it predicts the first purge point's distinct
    count to within 0.5%. Returns the new total-relations target.
    A floor from the driver's own request_more_relations formula
    (max(nunique*0.01, -excess, 10000) more unique relations) is
    applied by the caller; here we return the occupancy estimate."""
    def pi_approx(x):
        # x / (ln x - 1); within ~1% of pi(x) at 2^25..2^26
        return x / (math.log(x) - 1.0)
    dmax = pi_approx(Bf) + pi_approx(Bg)
    if nunique is None or ncols_start is None or nunique <= 0:
        return None
    if ncols_start >= dmax:
        return None
    # fit c from the current (R, D) point
    c = -math.log(1.0 - ncols_start / dmax) / nunique
    # solve R' - target_ratio * D(R') = 0 by bisection; D(R') is
    # increasing concave, so the root is unique and bracketed.
    lo, hi = float(nunique), float(nunique) * 4.0
    f_lo = lo - target_ratio * dmax * (1.0 - math.exp(-c * lo))
    f_hi = hi - target_ratio * dmax * (1.0 - math.exp(-c * hi))
    if f_lo >= 0:
        return total_rels  # already at/above target ratio
    if f_hi < 0:
        hi = float(nunique) * 8.0  # widen once; still bounded
        f_hi = hi - target_ratio * dmax * (1.0 - math.exp(-c * hi))
        if f_hi < 0:
            return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        f_mid = mid - target_ratio * dmax * (1.0 - math.exp(-c * mid))
        if f_mid < 0:
            lo = mid
        else:
            hi = mid
    r_target = 0.5 * (lo + hi)
    dup_rate = nunique / total_rels if total_rels > 0 else 1.0
    dup_rate = min(max(dup_rate, 0.1), 1.0)
    return int(r_target / dup_rate)


def extract_best_poly(ropt_logs, out_prefix):
    """Parse polyselect_ropt stdout files and write the best poly
    (highest MurphyE) to <out_prefix>.0 in clean poly-file format.
    ropt prints one optimized poly block per input poly; each block
    starts with 'n: ' and carries Y0, Y1, c0..c4, skew, and a
    '# side 1 MurphyE(...)=<value>' line. Returns (n_polys, best_E)."""
    best = None       # (murphyE, fields dict)
    n_polys = 0
    for logpath in ropt_logs:
        with open(logpath, "rt", errors="ignore") as f:
            cur = None
            for line in f:
                line = line.rstrip("\n")
                if line.startswith("n: "):
                    cur = {"n": line[3:].strip()}
                    n_polys += 1
                elif cur is not None:
                    if line.startswith("Y0: "):
                        cur["Y0"] = line[4:].strip()
                    elif line.startswith("Y1: "):
                        cur["Y1"] = line[4:].strip()
                    elif line.startswith("c0: "):
                        cur["c0"] = line[4:].strip()
                    elif line.startswith("c1: "):
                        cur["c1"] = line[4:].strip()
                    elif line.startswith("c2: "):
                        cur["c2"] = line[4:].strip()
                    elif line.startswith("c3: "):
                        cur["c3"] = line[4:].strip()
                    elif line.startswith("c4: "):
                        cur["c4"] = line[4:].strip()
                    elif line.startswith("skew: "):
                        cur["skew"] = line[6:].strip()
                    elif "MurphyE" in line and "=" in line:
                        m = re.search(r"=\s*([0-9.eE+-]+)\s*$", line)
                        if m:
                            cur["E"] = float(m.group(1))
                            cur["Eline"] = line.strip()
                    elif line.startswith("###") or line == "":
                        # end of this poly block
                        if cur is not None and "E" in cur \
                                and "c0" in cur and "Y0" in cur:
                            if best is None or cur["E"] > best[0]:
                                best = (cur["E"], dict(cur))
                        cur = None
            # trailing block without a ### terminator
            if cur is not None and "E" in cur and "c0" in cur \
                    and "Y0" in cur:
                if best is None or cur["E"] > best[0]:
                    best = (cur["E"], dict(cur))
    if best is None:
        raise RuntimeError("no optimized poly found in ropt output")
    E, f = best
    with open(out_prefix + ".0", "w") as out:
        out.write(f"n: {f['n']}\n")
        out.write(f"skew: {f['skew']}\n")
        for c in ("c0", "c1", "c2", "c3", "c4"):
            out.write(f"{c}: {f[c]}\n")
        out.write(f"Y0: {f['Y0']}\n")
        out.write(f"Y1: {f['Y1']}\n")
        out.write(f"# MurphyE = {E}\n")
    return n_polys, E


def parse_sqrt_pair(logpath):
    """Each sqrt -dep run prints two bare numbers at the end (just
    before the 'Total usage' line); their product is N. Return the
    pair (a, b), or None if the log has no such pair yet."""
    nums = []
    with open(logpath, "rb") as f:
        data = f.read().decode("utf-8", "ignore")
    for line in data.splitlines():
        if re.fullmatch(r"[0-9]+", line.strip()):
            nums.append(int(line.strip()))
    if len(nums) >= 2:
        return nums[-2], nums[-1]
    return None


def is_prime(n):
    if n < 2:
        return False
    for p in [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37]:
        if n % p == 0:
            return n == p
    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for a in [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37]:
        if a >= n:
            continue
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def split_with_divisors(N, divisors):
    """Split N into prime factors using the divisors collected from
    the sqrt -dep runs. Each divisor d (1 < d < N) splits every
    current factor f it shares a nontrivial gcd with. Returns the
    sorted list of prime factors, or None if the split is incomplete."""
    import math as _math
    factors = [N]
    for d in divisors:
        if d <= 1 or d >= N:
            continue
        new_factors = []
        changed = False
        for f in factors:
            g = _math.gcd(d, f)
            if 1 < g < f:
                new_factors.append(g)
                new_factors.append(f // g)
                changed = True
            else:
                new_factors.append(f)
        factors = new_factors
        if changed and all(is_prime(f) for f in factors):
            break
    if all(is_prime(f) for f in factors):
        prod = 1
        for f in factors:
            prod *= f
        if prod == N:
            return sorted(factors)
    return None


# ---------------------------------------------------------------------------
# Main: phased DAG execution
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", required=True)
    ap.add_argument("--n", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--bindir", required=True)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--qmax", type=int, default=0,
                    help="stop sieving at this q (0 = no explicit cap)")
    ap.add_argument("--poly", default=None,
                    help="precomputed polynomial file to use instead of "
                    "running polyselect (e.g. CADO-NFS's shipped "
                    "parameters/polynomials/c100.poly). The polyselect "
                    "phase is skipped and the file is copied to the "
                    "workdir as the polynomial.")
    args = ap.parse_args()

    # Absolute paths: subprocesses run with cwd=W, so every path the
    # shell commands reference must be absolute, not relative to W.
    B = os.path.abspath(args.bindir)
    W = os.path.abspath(args.workdir)
    T = str(args.threads)
    N = args.n
    os.makedirs(W, exist_ok=True)
    logs = os.path.join(W, "logs")
    os.makedirs(logs, exist_ok=True)

    P = parse_params(args.params)
    D = derived(P)

    # Already complete? If a previous run finished and verified, do
    # not redo anything; just report the factors.
    factors_path = os.path.join(W, "factors.txt")
    verify_path = os.path.join(W, "verify.txt")
    if exists_nonempty(factors_path) and exists_nonempty(verify_path):
        with open(verify_path) as vf:
            vtxt = vf.read()
        if "product == N: True" in vtxt and "all factors prime: True" in vtxt:
            with open(factors_path) as ff:
                print(ff.read(), end="")
            log("run already complete and verified; nothing to do")
            return

    log(f"target N = {N} ({len(N)} digits)")
    log(f"params = {args.params}")
    log(f"derived: Bf={D['Bf']:.0f} Bg={D['Bg']:.0f} area={D['area']:.3e} "
        f"poly_chunks={D['poly_chunks']} rels_wanted={D['rels_wanted']}")

    poly = os.path.join(W, "poly")
    fb1 = os.path.join(W, "roots1.gz")
    freerel = os.path.join(W, "freerel.gz")
    renumber = os.path.join(W, "renumber.gz")

    # ============================ PHASE A: polyselect =======================
    # Resume: if the polynomial already exists in the workdir (from a
    # previous run, or supplied via --poly), the polyselect sub-DAG
    # is skipped. --poly uses a precomputed polynomial (CADO-NFS
    # ships one per parameter set under parameters/polynomials/);
    # this is the project's own supplied poly for the parameter
    # set, not a substitution. It avoids the polyselect phase,
    # which for c100 (degree 5, nq=15625) takes hours per admin
    # chunk on this 1.5-CPU box -- longer than the VM lifetime.
    log("PHASE A: polynomial selection (admin-range sub-DAG) + "
        "factor base + free relations")
    if args.poly:
        import shutil
        shutil.copyfile(os.path.abspath(args.poly), poly)
        log(f"using supplied polynomial {args.poly} -> {poly} "
            f"(polyselect skipped)")
    elif exists_nonempty(poly):
        log(f"resume: polynomial already exists at {poly} "
            f"(polyselect skipped)")
    else:
        nch = D["poly_chunks"]
        adrange = D["adrange"]
        # A1: polyselect admin chunks (parallel)
        layer = []
        for i in range(nch):
            admin = i * adrange
            raw = os.path.join(W, f"poly.raw.{i}")
            cmd = (f"{B}/polyselect/polyselect "
                   f"-P {int(P['tasks.polyselect.P'])} -N {N} "
                   f"-degree {int(P['tasks.polyselect.degree'])} -t {T} "
                   f"-admin {admin} -admax {adrange} "
                   f"-incr {int(P['tasks.polyselect.incr'])} "
                   f"-nq {int(P['tasks.polyselect.nq'])} > {raw}")
            layer.append((f"polyselect-{i}", cmd,
                          os.path.join(logs, f"polyselect-{i}.log"), True))
        run_layer(layer, args.threads, W)
        # A2: root optimization per chunk (parallel)
        layer = []
        for i in range(nch):
            opt = os.path.join(W, f"poly.opt.{i}")
            cmd = (f"{B}/polyselect/polyselect_ropt -t {T} "
                   f"-inputpolys {W}/poly.raw.{i} "
                   f"-ropteffort {float(P['tasks.polyselect.ropteffort'])} "
                   f"-area {D['area']:.1f} -Bf {D['Bf']:.1f} "
                   f"-Bg {D['Bg']:.1f} > {opt}")
            layer.append((f"polyselect-ropt-{i}", cmd,
                          os.path.join(logs, f"polyselect-ropt-{i}.log"), True))
        run_layer(layer, args.threads, W)
        # A3: pick the best poly. ropt's stdout mixes log lines with the
        # optimized poly blocks, so parse out the clean blocks, keep the
        # highest-MurphyE poly, stage it as <poly>.0, and let polyselect3
        # finalize it into <poly> (same as the driver's own flow).
        ropt_logs = [os.path.join(W, f"poly.opt.{i}") for i in range(nch)]
        n_polys, best_E = extract_best_poly(ropt_logs, poly)
        log(f"polyselect: {n_polys} optimized polys, best MurphyE={best_E:.3e}")
        # polyselect3 reads <poly>.0 and finalizes it in place (appends
        # the MurphyF line); it does NOT create a separate <poly> file.
        # The driver then uses <poly> as the polynomial file, so copy
        # the finalized <poly>.0 to <poly> (the extra MurphyF line is a
        # comment and is ignored by the readers, exactly as in the
        # calibration run where c60.poly and c60.poly.0 differ only by
        # that comment line).
        cmd = (f"{B}/polyselect/polyselect3 -t {T} -num 1 "
               f"-poly {poly} -Bf {D['Bf']:.1f} -Bg {D['Bg']:.1f} "
               f"-area {D['area']:.1f} && cp {poly}.0 {poly}")
        rc, _ = run_cmd("polyselect-pick", cmd,
                        os.path.join(logs, "polyselect-pick.log"), W, shell=True)
        if rc != 0:
            raise RuntimeError("polyselect-pick failed")
    # A4: factor base + free relations (independent, parallel).
    # Resume: skip each if its output already exists.
    layer = []
    if not exists_nonempty(fb1):
        layer.append(("factorbase",
                      [f"{B}/sieve/makefb", "-poly", poly,
                       "-lim", str(int(P["tasks.lim1"])), "-maxbits", "10",
                       "-out", fb1, "-side", "1", "-t", T],
                      os.path.join(logs, "factorbase.log"), False))
    else:
        log("resume: factor base already exists, skipping makefb")
    if not (exists_nonempty(freerel) and exists_nonempty(renumber)):
        layer.append(("freerel",
                      [f"{B}/sieve/freerel", "-poly", poly,
                       "-renumber", renumber,
                       "-lpb0", str(int(P["tasks.lpb0"])),
                       "-lpb1", str(int(P["tasks.lpb1"])),
                       "-out", freerel, "-t", T],
                      os.path.join(logs, "freerel.log"), False))
    else:
        log("resume: free relations already exist, skipping freerel")
    if layer:
        run_layer(layer, args.threads, W)

    # ============================ PHASE B: sieving ==========================
    # Dynamic expansion: q-interval nodes added in batches until the
    # relation count reaches the target (or qmax). Each interval is
    # independent. Sieving is a closure so the filter loop (Phase C)
    # can request more relations and resume from qnext when purge
    # finds the matrix below the percolation threshold.
    rels_wanted = D["rels_wanted"]
    qmin = int(P["tasks.qmin"])
    qrange = int(P["tasks.sieve.qrange"])
    # Resume: scan for rels files from a previous (killed) run.
    # Each is named rels.<q0>-<q1>.gz; resume qnext after the
    # highest q1 seen, and count the relations already collected.
    # A rels file is only trusted if it is non-empty (a killed las
    # leaves a partial file; las writes it via a temp + rename in
    # the driver, but our direct invocation writes it in place, so
    # a zero-size file is the only reliable partial marker -- and
    # count_relations would undercount a truncated file, so we
    # re-sieve any interval whose file is missing; a truncated
    # non-empty file is possible in principle, but las flushes
    # relations as it finds them and the count is only used as a
    # stopping heuristic -- the filter stage is what ultimately
    # decides if there are enough relations).
    rels_files = []
    n_intervals = 0
    total_rels = 0
    qnext = qmin
    existing = sorted(
        f for f in os.listdir(W)
        if re.match(r"rels\.\d+-\d+\.gz$", f))
    for ef in existing:
        m = re.match(r"rels\.(\d+)-(\d+)\.gz$", ef)
        q0, q1 = int(m.group(1)), int(m.group(2))
        path = os.path.join(W, ef)
        if exists_nonempty(path):
            rels_files.append(path)
            n_intervals += 1
            total_rels += count_relations([path])
            qnext = max(qnext, q1)
    if existing:
        log(f"resume: {n_intervals} interval(s) already sieved, "
            f"{total_rels} relations, qnext={qnext}")

    def sieve_until(target):
        """Sieve q-intervals (one per thread, parallel layer) until
        total_rels >= target. Updates the shared sieving state
        (total_rels, n_intervals, qnext, rels_files)."""
        nonlocal total_rels, n_intervals, qnext
        log(f"PHASE B: sieving (q-interval sub-DAG) until {target} "
            f"relations (have {total_rels}, qnext={qnext})")
        while total_rels < target:
            if args.qmax and qnext >= args.qmax:
                log(f"qmax {args.qmax} reached with {total_rels} relations")
                break
            # one batch = one interval per thread (parallel layer)
            layer = []
            batch_q = []
            for _ in range(args.threads):
                q0 = qnext
                q1 = q0 + qrange
                # align q1 to qrange boundary (driver behaviour)
                q1 = ((q1 + qrange - 1) // qrange) * qrange
                rels = os.path.join(W, f"rels.{q0}-{q1}.gz")
                cmd = (f"{B}/sieve/las -poly {poly} -q0 {q0} -I {int(P['tasks.I'])} "
                       f"-q1 {q1} -lim0 {int(P['tasks.lim0'])} "
                       f"-lim1 {int(P['tasks.lim1'])} "
                       f"-lpb0 {int(P['tasks.lpb0'])} -lpb1 {int(P['tasks.lpb1'])} "
                       f"-mfb0 {int(P['tasks.sieve.mfb0'])} "
                       f"-mfb1 {int(P['tasks.sieve.mfb1'])} "
                       f"-ncurves0 {int(P['tasks.sieve.ncurves0'])} "
                       f"-ncurves1 {int(P['tasks.sieve.ncurves1'])} "
                       f"-fb1 {fb1} -out {rels} -t {T} -stats-stderr")
                layer.append((f"sieve-{q0}-{q1}", cmd,
                              os.path.join(logs, f"sieve-{q0}-{q1}.log"), True))
                batch_q.append((q0, q1, rels))
                qnext = q1
            run_layer(layer, args.threads, W)
            # count only the new batch's files (incremental; re-reading
            # all rels files every batch would dominate at c100 scale)
            batch_rels = 0
            for (q0, q1, rels) in batch_q:
                rels_files.append(rels)
                n_intervals += 1
                batch_rels += count_relations([rels])
            total_rels += batch_rels
            log(f"sieving: {n_intervals} interval(s), {total_rels} relations "
                f"(wanted {target}), qnext={qnext}")
        log(f"PHASE B done: {n_intervals} intervals, {total_rels} relations")

    sieve_until(rels_wanted)

    # ============================ PHASE C: filtering ========================
    # dup1 -> dup2 slices -> purge, ITERATED: purge fails while the
    # matrix is below the percolation threshold (negative excess
    # after singleton removal). This is exactly how the cado-nfs.py
    # driver handles it (PurgeTask.request_more_relations -> sieve
    # more -> re-filter). The c100 params file's rels_wanted (3.6M)
    # is under-calibrated for the shipped poly: purge round 1 read
    # 2,762,542 unique relations against 3,459,675 distinct primes
    # (start excess -697,133), so singleton removal collapsed the
    # matrix to 49 rows. The driver's 1% increment would need
    # dozens of rounds here, so the first top-ups use an
    # occupancy-model estimate of the relations needed for
    # relations/distinct-primes ~= 1.08 (c80 succeeded at 0.976),
    # with the driver's incremental formula as a floor; each round
    # re-measures and re-fits, so the loop is self-correcting.
    log("PHASE C: filtering (dup1 -> dup2 slices -> purge -> merge -> replay)")
    dup1_dir = os.path.join(W, "dup1")
    os.makedirs(dup1_dir, exist_ok=True)
    # dup1 writes one file per slice into dup1/<slice>/ but does not
    # create the slice subdirectories itself; pre-create a generous
    # range (unused ones stay empty and are ignored).
    for s in range(64):
        os.makedirs(os.path.join(dup1_dir, str(s)), exist_ok=True)
    purged = os.path.join(W, "purged.gz")
    MAX_FILTER_ROUNDS = 25
    purge_ok = False
    for fround in range(MAX_FILTER_ROUNDS):
        # Wipe the previous round's dup1 slices so a round that
        # produces fewer slices cannot leave stale files behind.
        shutil.rmtree(dup1_dir, ignore_errors=True)
        for s in range(64):
            os.makedirs(os.path.join(dup1_dir, str(s)), exist_ok=True)
        # dup1: rebuild the filelist from the current rels files
        # (it grows when the filter loop requests more relations).
        filelist = os.path.join(W, "dup1.filelist")
        with open(filelist, "w") as f:
            for r in rels_files:
                f.write(r + "\n")
        rc, _ = run_cmd("dup1",
                        [f"{B}/filter/dup1", "-prefix", "dup1.0",
                         "-out", dup1_dir, "-n", "1",
                         "-filelist", filelist],
                        os.path.join(logs, "dup1.log"), W)
        if rc != 0:
            raise RuntimeError("dup1 failed")
        # dup2: one node per slice produced by dup1 (dynamic fan-out)
        slices = []
        for root, _, files in os.walk(dup1_dir):
            for fn in files:
                if fn.startswith("dup1.0."):
                    slices.append(os.path.join(root, fn))
        slices.sort()
        log(f"dup1 produced {len(slices)} slice(s)")
        layer = []
        for s_i, s in enumerate(slices):
            nrels = count_relations([s])
            cmd = (f"{B}/filter/dup2 -poly {poly} -nrels {nrels} "
                   f"-renumber {renumber} {s}")
            layer.append((f"dup2-slice{s_i}", cmd,
                          os.path.join(logs, f"dup2-slice{s_i}.log"), True))
        run_layer(layer, args.threads, W)
        # purge
        purge_inputs = " ".join(slices + [freerel])
        cmd = (f"{B}/filter/purge -out {purged} "
               f"-keep {int(P['tasks.filter.purge.keep'])} -t {T} "
               f"-required_excess 0.0 {purge_inputs}")
        rc, _ = run_cmd("purge", cmd, os.path.join(logs, "purge.log"), W,
                        shell=True)
        if rc == 0:
            purge_ok = True
            log(f"filter: purge succeeded on round {fround + 1}")
            break
        # purge failed: measure, estimate, sieve more, re-filter.
        stats = parse_purge_stats(os.path.join(logs, "purge.log"))
        log(f"filter: purge round {fround + 1} failed: "
            f"nunique={stats['nunique']} "
            f"ncols_start={stats['ncols_start']} "
            f"excess_final={stats['excess_final']}")
        nunique = stats["nunique"]
        excess = stats["excess_final"]
        # driver's minimum increment, in unique relations
        add_min = 10000
        if nunique is not None:
            add_min = max(add_min, int(nunique * 0.01))
        if excess is not None and excess < 0:
            add_min = max(add_min, -excess)
        dup_rate = 0.77
        if nunique is not None and total_rels > 0:
            dup_rate = nunique / total_rels
        dup_rate = min(max(dup_rate, 0.1), 1.0)
        new_target = total_rels + int(add_min / dup_rate)
        # occupancy-model estimate (dominant when the deficit is large)
        est = estimate_rels_target(nunique, stats["ncols_start"],
                                   D["Bf"], D["Bg"], total_rels)
        if est is not None:
            new_target = max(new_target, est)
        # cap a single jump at 2x the current total (model safety)
        new_target = min(new_target, total_rels * 2)
        if new_target <= total_rels:
            new_target = total_rels + int(add_min / dup_rate)
        log(f"filter: requesting more relations, new total target "
            f"{new_target} (have {total_rels}, dup_rate={dup_rate:.3f})")
        sieve_until(new_target)
    if not purge_ok:
        raise RuntimeError("purge failed after "
                           f"{MAX_FILTER_ROUNDS} filter rounds")
    # merge
    history = os.path.join(W, "history.gz")
    cmd = (f"{B}/filter/merge -mat {purged} -out {history} -skip 32 "
           f"-target_density {float(P['tasks.filter.target_density'])} "
           f"-t {T}")
    rc, _ = run_cmd("merge", cmd, os.path.join(logs, "merge.log"), W,
                    shell=True)
    if rc != 0:
        raise RuntimeError("merge failed")
    # replay. The -out basename controls the dense filename: replay
    # writes the sparse matrix to <out> and the dense (heavy) part to
    # <out> with ".sparse" replaced by ".dense". Using
    # "matrix.sparse.bin" therefore yields "matrix.dense.bin", which
    # is what characters wants as its -heavyblock.
    index = os.path.join(W, "index.gz")
    sparse = os.path.join(W, "matrix.sparse.bin")
    dense = os.path.join(W, "matrix.dense.bin")
    cmd = (f"{B}/filter/replay -purged {purged} -his {history} "
           f"-index {index} -out {sparse}")
    rc, _ = run_cmd("replay", cmd, os.path.join(logs, "replay.log"), W,
                    shell=True)
    if rc != 0:
        raise RuntimeError("replay failed")

    # ============================ PHASE D: linalg + sqrt ====================
    log("PHASE D: linear algebra (bwc block sub-DAG) + characters + sqrt")
    bwc_wdir = os.path.join(W, "bwc")
    os.makedirs(bwc_wdir, exist_ok=True)
    # Resume: skip bwc if its kernel vectors (W) already exist, and
    # skip characters if the kernel already exists. bwc.pl itself
    # checkpoints, so re-running it after a kill is also safe, but
    # skipping a completed one is cheaper.
    bwc_W = os.path.join(bwc_wdir, "W")
    if exists_nonempty(bwc_W):
        log("resume: bwc kernel vectors already exist, skipping linalg")
    else:
        cpubinding = os.path.join(
            os.path.dirname(B), "..", "parameters", "misc",
            "cpubinding.conf")
        cpubinding = os.path.normpath(cpubinding)
        cmd = (f"{B}/linalg/bwc/bwc.pl :complete 'thr={T}' "
               f"'m={int(P['tasks.linalg.m'])}' 'n={int(P['tasks.linalg.n'])}' "
               f"'nullspace=left' 'matrix={sparse}' 'wdir={bwc_wdir}' "
               f"'interleaving={int(P['tasks.linalg.bwc.interleaving'])}' "
               f"'cpubinding={cpubinding}'")
        rc, _ = run_cmd("linalg-bwc", cmd, os.path.join(logs, "linalg-bwc.log"),
                        W, shell=True)
        if rc != 0:
            raise RuntimeError("linalg (bwc) failed")
    # characters (dense.bin is produced by replay, see above)
    kernel = os.path.join(W, "kernel")
    if exists_nonempty(kernel):
        log("resume: kernel already exists, skipping characters")
    else:
        cmd = (f"{B}/linalg/characters -poly {poly} -purged {purged} "
               f"-index {index} -heavyblock {dense} -out {kernel} "
               f"-ker {bwc_wdir}/W -lpb0 {int(P['tasks.lpb0'])} "
               f"-lpb1 {int(P['tasks.lpb1'])} "
               f"-nchar {int(P['tasks.linalg.characters.nchar'])} -t {T}")
        rc, _ = run_cmd("characters", cmd,
                        os.path.join(logs, "characters.log"), W, shell=True)
        if rc != 0:
            raise RuntimeError("characters failed")
    # sqrt: one node per kernel dependency. The kernel file count is the
    # number of dependencies; bwc writes dep files <prefix>.dep.NNN.gz.
    # We run sqrt -ab first (produces the dep files), then one -dep node
    # per dependency.
    dep_prefix = os.path.join(W, "dep.gz")
    # Resume: skip sqrt -ab if the dep files already exist.
    existing_deps = sorted(
        f for f in os.listdir(W)
        if re.match(r"dep\.0\d+\.gz$", f))
    if existing_deps:
        log(f"resume: {len(existing_deps)} dep file(s) already exist, "
            f"skipping sqrt -ab")
    else:
        cmd = (f"{B}/sqrt/sqrt -poly {poly} -prefix {dep_prefix} "
               f"-purged {purged} -index {index} -ker {kernel} -t 1 -ab")
        rc, _ = run_cmd("sqrt-ab", cmd, os.path.join(logs, "sqrt-ab.log"), W,
                        shell=True)
        if rc != 0:
            raise RuntimeError("sqrt -ab failed")
    # count dependencies from the dep files produced
    dep_files = sorted(
        f for f in os.listdir(W)
        if re.match(r"dep\.0\d+\.gz$", f))
    ndep = len(dep_files)
    log(f"sqrt: {ndep} kernel dependenc(ies)")
    # Run the sqrt -dep nodes in parallel batches. After each batch,
    # try to split N from the factor pairs collected so far; stop
    # early once the full prime factorization is found (the driver
    # does the same -- it stops after the first dependencies that
    # yield the factors).
    N_int = int(N)
    divisors = []
    factors = None
    d_i = 0
    while d_i < ndep and factors is None:
        batch = list(range(d_i, min(d_i + args.threads, ndep)))
        layer = []
        for b in batch:
            cmd = (f"{B}/sqrt/sqrt -poly {poly} -prefix {dep_prefix} "
                   f"-dep {b} -t 1 -side0 -side1 -gcd")
            layer.append((f"sqrt-dep{b}", cmd,
                          os.path.join(logs, f"sqrt-dep{b}.log"), True))
        run_layer(layer, args.threads, W)
        for b in batch:
            pair = parse_sqrt_pair(os.path.join(logs, f"sqrt-dep{b}.log"))
            if pair:
                divisors.extend(pair)
        factors = split_with_divisors(N_int, divisors)
        d_i += args.threads
        log(f"sqrt: tried deps 0..{d_i - 1}, "
            f"factors={'found' if factors else 'not yet'}")
    if factors is None:
        factors = split_with_divisors(N_int, divisors)
    ndep_tried = d_i

    # ============================ VERIFY ====================================
    log("VERIFY: factors from the sqrt logs")
    if factors is None:
        factors = []
    with open(os.path.join(W, "factors.txt"), "w") as f:
        f.write(f"N = {N}\n")
        for fac in factors:
            f.write(f"{fac}\n")
    log(f"factors: {' '.join(str(x) for x in factors)}")
    # independent verification: product == N, each factor prime
    prod = 1
    for fac in factors:
        prod *= fac
    ok_prod = (prod == int(N))
    ok_prime = all(is_prime(f) for f in factors)
    with open(os.path.join(W, "verify.txt"), "w") as f:
        f.write(f"product == N: {ok_prod}\n")
        f.write(f"all factors prime: {ok_prime}\n")
        f.write(f"factors: {factors}\n")
        f.write(f"intervals: {n_intervals}\n")
        f.write(f"relations: {total_rels}\n")
    log(f"VERIFY: product==N: {ok_prod}; all prime: {ok_prime}")
    if not (ok_prod and ok_prime):
        raise RuntimeError("verification FAILED")
    log(f"DAG COMPLETE in {time.time() - START:.1f}s")


if __name__ == "__main__":
    main()
