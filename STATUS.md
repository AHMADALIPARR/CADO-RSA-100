# STATUS — CADO-NFS RSA factorization (Devin pattern)

## Summary

CADO-NFS is built and verified on this VM. Two calibration
factorizations are complete and verified by multiplication. The
recursive DAG driver is built and validated (it reproduces the
driver's own 59-digit result exactly). The target run, RSA-100,
is launched through the DAG and is in progress.

## Build

- Source: `git clone --depth 1 https://gitlab.inria.fr/cado-nfs/cado-nfs`
  into `~/workspace/cado-factor/src`, commit
  692ecb7e62f0f3bdab88ee44cc60b8ded0ea1a1b (2026-10-02).
- Build: `make -j2` with the project venv (`cado-nfs.venv`, flask
  3.1.3) first on PATH so cmake finds venv python3. Build
  completed, exit 0, ~47 min. All key binaries present in
  `src/build/htch-runtime/`: polyselect, polyselect_ropt,
  polyselect3, makefb, freerel, las, dup1, dup2, purge, merge,
  replay, bwc.pl (+ prep/krylov/lingen/mksol), characters, sqrt.
- Dependencies: GMP runtime + headers present; MPFR/MPC headers
  missing (some CADO-NFS code disabled, per its own notes).
  cmake 3.28.3, gcc/g++ 13.3.0.
- Build failure encountered and fixed: cmake configure failed
  because flask was not found by SYSTEM python3. Fix: use the
  project venv python3 (PATH) and wipe/re-run the build.
- Calibration-run gotcha (found the hard way): cado-nfs.py only
  spawns its client automatically when NO explicit `-p` parameter
  file is given (`using_default_parameter_file`). Passing `-p`
  leaves the driver waiting forever for a client that never
  starts. Fix: omit `-p` and let the driver auto-select the
  parameter file by size. The DAG driver (dag.py) invokes the
  binaries directly and is not affected.

## Calibration

### 59-digit (README example), via cado-nfs.py

- N = 90377629292003121684002147101760858109247336549001090677693
  (59 digits; the README's example number, not 65).
- Driver auto-selected params.c60.
- Wall 66 s (factorization 61.1 s elapsed, 48.94 s CPU).
- Sieving: 54,305 relations, 31.7 s, 1802 special-q, 22 q-intervals.
- Factors: 260938498861057, 588120598053661, 760926063870977,
  773951836515617 (four 15-digit primes; the number is not a
  semiprime). Verified: product == N exactly, all four prime.
- Log: `logs/calib59.log`. Workdir: `work/calib59/`.

### 80-digit synthetic semiprime, via cado-nfs.py

- p = 3646145125719001888002753562073855374079 (40 digits),
  q = 4586315596484532772155328562582497331107 (40 digits),
  generated locally (seeded Miller-Rabin), product verified.
  N = 16722372257131115877945740911896196316950319396248402582614721355820926608175453
  (80 digits). Saved in `calib80.txt`.
- Driver auto-selected params.c80.
- Wall 517 s (8.6 min); factorization 511.8 s elapsed, 547.5 s CPU.
- Sieving: 297,388 relations, 429.6 s, 5513 special-q
  (692 relations/s). rels_wanted was 233,399; 6,625 free relations.
- Filter: dup1 3.3 s, dup2 11.1 s, purge 7.1 s, merge 5.2 s,
  replay 1.5 s. Linalg (bwc) 11.0 s. Characters 1.4 s. Sqrt 4.0 s.
- Factors recovered exactly p and q above. Verified: product == N
  exactly, both prime.
- Log: `logs/calib80.log`. Workdir: `work/calib80/`.

## Sizing (target selection)

- GNFS L-notation ratio: RSA-100 is ~36x the 80-digit effort
  (L(100)/L(80) = e^3.59). 36 x 517 s = ~18,600 s = ~5.2 h.
- Relation-count cross-check: c100 wants 3,600,000 relations
  (15.4x c80's 233,399 wanted). At a conservatively reduced
  sieving rate (250-400 rels/s vs 692 measured at c80), sieving
  is ~2.5-4 h; plus polyselect, filter, linalg, sqrt.
- RSA-110 would be ~202x the 80-digit effort (~29 h) — too big
  for the 12 h budget. RSA-100 is the largest real RSA challenge
  number that fits, and the smallest challenge number there is.
- Target: RSA-100 (100 digits, 330 bits), factored Apr 1, 1991
  by Arjen K. Lenstra (public record). This is a verified
  reproduction run at a scale this box owns, NOT a world record.
- RSA-100 = 1522605027922533360535618378132637429718068114961380688657908494580122963258952897654000350692006139
  (value from Wikipedia, RSA numbers). Its published factors
  (1991, public) are 37975227936943673922808872755445627854565536638199
  and 40094690950920881030683735292761468389214899724061
  (both 50 digits, both prime, product == RSA-100 — re-verified
  here). The run's output will be checked against these AND by
  independent multiplication.
- Expected RSA-100 wall time: ~4-6 h (sieving-dominated), within
  the ~12 h budget. Saved in `target-rsa100.txt`.

## Recursive DAG orchestration

- `dag.py` — a small DAG driver (Python). Top level: the GNFS
  pipeline as a DAG (polyselect -> factorbase -> freerel -> sieve
  -> dup1 -> dup2 -> purge -> merge -> replay -> linalg ->
  characters -> sqrt -> verify). Each stage decomposes into a
  sub-DAG: polyselect into admin-range chunks (parallel) ->
  ropt -> pick best poly; sieving into q-interval nodes
  (dynamically expanded until rels_wanted); dup2 into one node
  per dup1 slice; sqrt into one node per kernel dependency
  (batched, early stop once the factors are found); linalg is a
  single bwc.pl :complete node whose internal phases are
  prep -> krylov -> lingen -> mksol over m x n blocks.
  Topologically ordered, each node computed once, independent
  nodes in a layer run in parallel (2 workers).
- Command patterns are the exact ones cado-nfs.py itself uses,
  taken from the calibration run's `c60.cmd` and workunit
  database (`c60.db`). Derived values match the driver:
  Bf = 2**lpb1, Bg = 2**lpb0, area = 2**(2*I-1) * qmin,
  rels_wanted fallback = int(0.81 * sum(2**lpb/ln(2**lpb))).
- DAG.md documents the structure, stages, sub-DAGs, and the
  RSA-Prime nodes.
- RSA-Prime (owner's code, verbatim) is in the graph as
  cross-check nodes: gnfs-setup (verified: f(m)==N, factor
  bases, on the 30-digit test), QS cross-check (verified: the
  Factor QS factored the 30-digit semiprime
  288986332189663757016053995249 in 13.0 s, factor
  812790250833157, status "found"), BQN array kernels
  (attempted-and-blocked: this VM's CBQN rejects the nabla
  U+2207 and theta U+03B8 identifiers the owner's files use;
  files NOT rewritten; array work done by CADO-NFS + Factor QS
  instead). Logs in `logs/rsaprime-*.txt`.
- DAG driver validation: `python3 dag.py --params
  params.c60 --n <59-digit> --workdir work/dagtest59` completed
  in 58 s, exit 0, and reproduced the calibration's exact four
  factors (260938498861057, 588120598053661, 760926063870977,
  773951836515617), 10 intervals, 54,305 relations, product == N,
  all prime. The DAG therefore reproduces the driver's result
  through the recursive-DAG orchestration. Log:
  `logs/dagtest59.log`.

## Target run (RSA-100) — IN PROGRESS (third launch, shipped poly)

- Command: `python3 dag.py --params
  src/parameters/factor/params.c100 --n <RSA-100> --workdir
  work/rsa100 --bindir src/build/htch-runtime --threads 2
  --poly src/parameters/polynomials/c100.poly`
- Log: `logs/rsa100.log`. Time file: `logs/rsa100.time`.
- Derived: Bf=67108864, Bg=33554432, area=3.775e+11,
  poly_chunks=28, rels_wanted=3600000.
- First launch (~01:14 UTC): killed by SIGTERM after ~16 min
  (polyselect chunks 2-3 in progress) by a VM replacement.
  Second launch (~01:33 UTC): killed by SIGTERM after ~118 min,
  again by a VM replacement (uptime reset to 0 both times).
  Each VM swap wipes the system libhwloc.so.15 that `las` and
  `polyselect` link against (all other binaries unaffected).
  Restored each time with the Debian bookworm .deb
  libhwloc15_2.12.0-4 (dpkg -i, kept in
  `~/workspace/.tools/debs/`); full ldd sweep confirms no other
  missing libraries. Python 3.12.3, venv, GMP intact
  (~/workspace persists across VM swaps; system packages do not).
- Root cause of the slow first two launches: the c100
  polyselect (degree 5, nq=15625) is extremely slow on this
  1.5-CPU box. Chunks 2-3 (admin 120-240) ran 118 min and
  produced ~50 MB of candidate polys each without finishing;
  a single chunk takes hours, longer than the VM lifetime.
  Even nq reduced to 1024 did not finish a chunk in 6 min.
- Fix: use CADO-NFS's own shipped polynomial
  `parameters/polynomials/c100.poly` (n=RSA-100, degree 5,
  skew 19621.398, MurphyE 1.480e-05, Bf/Bg/area matching the
  c100 derived values). This is the project's supplied poly
  for the c100 parameter set, not a substitution. dag.py gained
  a `--poly` option (skips the polyselect sub-DAG) and resume
  support (skips any stage whose output already exists; the
  sieving loop resumes from existing rels.*.gz files and their
  relation count). Validated the --poly path on the 59-digit
  number: 40 s, exit 0, exact same four factors, product == N.
- Third launch (~03:35 UTC): polyselect skipped; factorbase +
  freerel done in ~23 s; sieving running. First two intervals
  (180000-185000, 185000-190000) took ~61 s each and yielded
  100,501 relations (~50,250/interval, ~1,620 rels/s) — far
  better than the conservative 250-400 rels/s estimate, because
  the shipped poly is good. At this rate sieving needs ~72
  intervals, ~37 min; total run ~1-1.5 h. The run continues
  in the background; factors and verification will be appended
  here when it completes. If a VM replacement kills it again,
  resume (`python3 dag.py ... --poly ...` on the same workdir)
  picks up from the completed sieving intervals.

## RSA-100 third launch: purge failed, relations under target (2026-11-20 ~04:19 UTC)

The third launch (session proc_c7ef8707adbe) ran 2,596 s and exited
1 — NOT a VM kill this time, a real pipeline failure at the filter
stage. Sieving COMPLETED: 108 intervals, 3,604,342 relations
(wanted 3,600,000) in 2,531 s (~1,424 rels/s). dup1 done (13.0 s,
2 slices), dup2 done (~41 s each). purge FAILED rc=2.

Root cause (from work/rsa100/logs/purge.log): the matrix is below
the percolation threshold. Purge read 2,762,542 unique relations
(76.6% of the 3,604,342 collected; the rest are duplicates) against
3,459,675 distinct primes — start excess -697,133 (relations are
only 80% of distinct primes). Singleton removal then cascaded and
collapsed the matrix to 49 rows (final excess -10,736), so purge
refused ("number of rows < number of columns + keep").

Why: the c100 params file's rels_wanted (3,600,000) is
under-calibrated for the shipped polynomial. The c80 calibration
needed 3 purge rounds itself (excess -13,249 -> -3,549 -> +160),
and c80 succeeded at relations/distinct-primes = 0.976. RSA-100
sits at 2,762,542 / 3,459,675 = 0.798 — well below that. The
driver's own fallback formula for rels_wanted (0.81 * sum
2**lpb/ln(2**lpb)) gives ~4.58M for c100, above the file's 3.6M.
The driver's request_more_relations increment (max(nunique*0.01,
-excess, 10000) = 27,625 here) would need dozens of rounds at
this deficit, so it is not the right tool for a first top-up.

Fix (dag.py, validated): Phase C is now an iterated filter loop
(dup1 -> dup2 -> purge; on failure parse the purge log, estimate
more relations, sieve, re-filter; up to 25 rounds). The estimate
is an occupancy model D(R) = Dmax*(1-exp(-c*R)) with Dmax =
pi(Bf)+pi(Bg) (~6.0M) and c fitted from the measured (R, D) point,
solved for relations/distinct-primes = 1.08. The model is
validated on the c80 calibration: fitted from c80's final purge
point it predicts the first purge point's distinct count to
within 0.5%. For RSA-100 round 1 it estimates 6,771,450 total
relations (~5.19M unique). The driver's incremental formula is
kept as a floor and each round re-measures and re-fits, so the
loop is self-correcting. Phase B sieving is now a sieve_until()
closure the filter loop calls for top-ups. Refactor validated
end-to-end on the 59-digit number (work/dagtest59loop, 42.3 s,
exit 0, same four factors, product == N, all prime; log
logs/dagtest59loop.log; "purge succeeded on round 1").

Resume launched 2026-11-20 ~04:26 UTC (session proc_4947a306cf4f,
log logs/rsa100-resume.log): same command on work/rsa100, no wipe.
It re-runs filter round 1 (~60 s, expected to fail identically),
then sieves ~3.17M more relations (~37-40 min at the recent
~1,300 rels/s; per-interval yield is slowly declining, 50k ->
33k) to ~6.77M total, then filter round 2. After purge succeeds:
merge, replay, bwc linalg, characters, sqrt. Expected remaining
~40 min sieving + filter + linalg/sqrt (bwc time on the larger
matrix is the open question). VM uptime was 54 min at resume;
if a replacement kills it, resume again on the same workdir —
sieving, bwc (checkpoints), and sqrt-dep state all resume.

## RSA-100 COMPLETE — factored 2026-11-20 ~05:10 UTC

The resume (session proc_4947a306cf4f) finished with exit 0 in
3,036 s (50.6 min). Combined with the third launch's 2,596 s, the
successful path took 5,632 s = 93.9 min (~1.57 h) of wall time.
(The two earlier launches killed by VM replacements, ~16 min and
~118 min of polyselect, were wasted work and are not part of the
successful run's compute.)

Resume timeline (log logs/rsa100-resume.log, time
logs/rsa100-resume.time: start 1795148796, end 1795151832):
- Filter round 1 failed identically (nunique 2,762,542,
  ncols_start 3,459,675, excess -10,736); new target 6,771,450.
- Top-up sieving: 108 -> 246 intervals, 3,604,342 -> 6,776,932
  relations (3,172,590 more), Phase B done at 2,801.4 s.
- Filter round 2: purge SUCCEEDED at 2,963.8 s. It read
  5,162,783 unique relations against 4,530,073 distinct primes
  (relations/distinct = 1.14, above the 1.08 target — the
  occupancy model erred on the safe side: it predicted ~4.8M
  distinct, actual 4.53M). Final matrix after singleton/clique
  removal: 351,341 rows x 351,191 cols, excess +150 (exactly the
  keep=150 target). Purge itself 50.1 s; merge 4.0 s; replay 2.7 s.
- Phase D: linalg-bwc 39.8 s, characters 3.2 s, sqrt-ab 2.2 s,
  sqrt-dep0 19.2 s, sqrt-dep1 19.8 s (factors found after 2
  dependencies). DAG COMPLETE in 3,035.6 s.

Factors (work/rsa100/factors.txt, verify.txt):
- 37975227936943673922808872755445627854565536638199
- 40094690950920881030683735292761468389214899724061
Both 50 digits. Independent verification (fresh Python, not the
driver): product == N exactly True; both prime True (deterministic
Miller-Rabin, 20 bases); they match the published 1991 Lenstra
factors exactly. This is a verified reproduction run at a scale
this box owns, NOT a world record — RSA-100 was first factored
in 1991.

Sieving dominated: of the 5,632 s successful path, ~5,330 s was
sieving (2,531 s + 2,727 s top-up) and ~300 s was filter/linalg/
sqrt. Sieving is the embarrassingly parallel part and the clear
GPU-port candidate for the Jetson.

## Verification (method)

- Independent of CADO-NFS and Factor: Python integer
  multiplication, product == N exactly; deterministic
  Miller-Rabin primality for each factor. A factor is reported
  only after this passes. For RSA-100, the result is also
  compared against the published 1991 factors.

## Scope

Synthetic keys and public RSA challenge numbers only. No real
third-party keys, certificates, or traffic. No claim of breaking
a secure deployed modulus.

## Files

- `src/` — CADO-NFS source + build (`src/build/htch-runtime/`).
- `dag.py` — the recursive DAG driver.
- `DAG.md` — DAG structure documentation.
- `STATUS.md` — this file.
- `target-rsa100.txt`, `calib80.txt` — target / calibration numbers.
- `logs/` — calib59, calib80, dagtest59, rsa100 logs + timings,
  rsaprime-*.txt.
- `work/` — calib59, calib80, dagtest59, rsa100 workdirs.
