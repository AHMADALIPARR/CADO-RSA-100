# Factoring RSA-100 with CADO-NFS on a 1.5-CPU Virtual Machine

## A verified reproduction run orchestrated by a recursive DAG

**Date of run:** 2026-11-20 (completion 05:17:12 UTC)
**Software:** CADO-NFS, commit 692ecb7e62f0f3bdab88ee44cc60b8ded0ea1a1b (2026-10-02)
**Orchestration:** `dag.py`, a recursive DAG driver
**Environment:** Linux virtual machine, 1.5 shared CPUs, about 6 GB RAM, no GPU

## Abstract

This paper documents the factorization of RSA-100, a 100-digit
(330-bit) semiprime from the RSA Factoring Challenge, on a
1.5-CPU virtual machine with no GPU. The factorization uses
CADO-NFS, the open-source implementation of the General Number
Field Sieve (GNFS), orchestrated by a recursive directed acyclic
graph (DAG) driver that expresses the GNFS pipeline as a DAG of
stages, each stage decomposing into its own sub-DAG of independent
tasks. The successful run takes 5,632 seconds of wall time (93.9
minutes), collects 6,776,932 relations over 246 sieving intervals,
and recovers the two 50-digit factors. Verification is independent
of the factorization software: integer multiplication confirms the
product equals RSA-100 exactly, deterministic Miller-Rabin confirms
both factors are prime, and both factors match the published 1991
factors exactly. This is a verified reproduction of a factorization
first completed in 1991, not a world record. The public record
stands at RSA-270 (895 bits, November 2026). Every number in this
paper is a measured value from the run logs, the calibration runs,
or the verification files, except where it is explicitly labelled
an estimate.

## 1. Introduction

### 1.1 The RSA Factoring Challenge

RSA Laboratories published the RSA Factoring Challenge in 1991 to
encourage research into computational number theory and the
practical difficulty of factoring large integers. The challenge
numbers are semiprimes: products of two primes of similar size.
RSA-100 is the smallest challenge number, 100 decimal digits and
330 bits. It was first factored on 1 April 1991 by Arjen K.
Lenstra. Its factors are public and have been public since 1991:

- 37975227936943673922808872755445627854565536638199
- 40094690950920881030683735292761468389214899724061

Both are 50 digits. Their product is RSA-100:

1522605027922533360535618378132637429718068114961380688657908494580122963258952897654000350692006139

A factorization of RSA-100 is therefore a reproduction, not a
discovery. Its value is methodological: it exercises the complete
GNFS pipeline end to end on a real challenge number, with every
stage timed and every output verified, on hardware far smaller
than the machines that set the records.

### 1.2 The General Number Field Sieve

The General Number Field Sieve is the standard classical method
for factoring large integers. Its asymptotic complexity is
sub-exponential, L_N[1/3, (64/9)^(1/3)], where L_N denotes the
usual L-notation. In practice GNFS is two computational
bottlenecks. Lattice sieving strides across large factor-base
arrays to accumulate smooth relations. Sparse linear algebra over
GF(2), by block Wiedemann or block Lanczos, reduces billions of
relation rows to a null-space dependency. Both stages are array
and memory-hierarchy problems: the sieve is bounded by cache and
memory bandwidth, and the linear algebra is bounded by sparse
bit-packed operations. No new number theory is involved in this
run. The algorithm is the published GNFS, implemented by CADO-NFS.

### 1.3 Background: the 2026 GPU records

The public factoring record stood at RSA-250 (829 bits) from
February 2020 until September 2026. RSA-250 was factored by
Boudot, Gaudry, Guillevic, Heninger, Thomé, and Zimmermann using
CADO-NFS on CPUs, at a cost of about 2,700 core-years. In
September and November 2026, three records fell in ten weeks, all
by porting CADO-NFS lattice sieving and block Wiedemann onto GPU
memory hierarchies, with no change to the underlying algorithm:

- RSA-260 (862 bits), 3 September 2026, Eric Lu at Cognition.
  A GPU-accelerated GNFS based on CADO-NFS, developed and
  operated with the Devin software engineering agent, totalling
  about 4,900 GPU-days (about 13.5 GPU-years), at a cost Lu
  estimated at roughly $400,000.
- RSA-896 (896 bits), 19 September 2026, Stephen A. Weis at
  Anthropic. Claude ported CADO-NFS to GPUs and orchestrated the
  computation across a fleet of up to 2,048 GPUs over about 10
  days, totalling approximately 30 GPU-years of compute on idle
  capacity.
- RSA-270 (895 bits), 12 November 2026, Eric Lu at Cognition.

Both authors state that the work does not improve the GNFS
algorithm itself. Lu reports essentially no algorithmic
advancement; Weis reports no meaningful improvement to the GNFS
running time. Lu estimates RSA-1024 at roughly $30 million per
number at market GPU prices, and RSA-2048 at roughly a billion
times harder again. The present run follows the same pattern at a
far smaller scale: the same open sieve, the same algorithm, run on
a 1.5-CPU machine with no GPU, and the result is a verified
reproduction of a 1991 factorization.

### 1.4 Scope

The scope of this run is synthetic keys and public RSA challenge
numbers only. No real third-party keys, certificates, or traffic
are involved. No claim is made of breaking a secure deployed
modulus. RSA-2048 is not affected by this run.

## 2. Method

### 2.1 Software

CADO-NFS is the open-source implementation of GNFS maintained by
INRIA. The run uses commit
692ecb7e62f0f3bdab88ee44cc60b8ded0ea1a1b, dated 2026-10-02,
cloned from https://gitlab.inria.fr/cado-nfs/cado-nfs. The build
uses `make -j2` with the project virtual environment
(`cado-nfs.venv`, flask 3.1.3) first on PATH so that cmake finds
the virtual environment's python3. The build completes with exit
0 in about 47 minutes. All key binaries are present in
`src/build/htch-runtime/`: polyselect, polyselect_ropt,
polyselect3, makefb, freerel, las, dup1, dup2, purge, merge,
replay, bwc.pl with prep, krylov, lingen, and mksol, characters,
and sqrt. Dependencies are GMP runtime and headers, cmake 3.28.3,
and gcc/g++ 13.3.0. MPFR and MPC headers are missing on this
machine, so some CADO-NFS code is disabled, as the project's own
notes describe.

### 2.2 The GNFS stages

The pipeline follows CADO-NFS's own task order. Polynomial
selection finds a polynomial pair that makes the later search
cheaper. The factor base stage builds the rational and algebraic
factor bases. Free relations collects relations with no sieving.
Sieving (las) searches q-intervals for smooth relations. Filtering
(dup1, dup2, purge, merge, replay) removes duplicates, removes
singletons and cliques, and builds the sparse matrix. Linear
algebra (bwc.pl, block Wiedemann) finds kernel vectors. The
characters stage and the square-root stage recover the factors
from the kernel dependencies. Verification checks the factors
independently.

### 2.3 Recursive DAG orchestration

The run is orchestrated by `dag.py`, a small DAG driver. The top
level is the GNFS pipeline as a DAG: each stage is a node, each
node depends only on its predecessors' output files, and the graph
is topologically ordered. Each stage decomposes into its own
sub-DAG of independent tasks, which is the recursive part of the
design:

- Polynomial selection splits into independent admin-range chunks,
  each root-optimized independently, then a single node picks the
  best polynomial by Murphy-E.
- Sieving splits into independent q-interval nodes. Each interval
  produces one relations file. Intervals are added in batches
  until the relation count reaches the current target, so the node
  count is determined by the measured relation yield, not guessed
  in advance. A failed interval re-runs alone; no other node is
  recomputed.
- Duplicate removal splits into one node per dup1 slice, with the
  slice count read from dup1's output.
- Linear algebra runs as a single bwc.pl invocation whose internal
  phases are prep, krylov, lingen, and mksol over m-by-n blocks.
- Square root splits into one node per kernel dependency, with the
  dependency count read from the dependency files after the
  sqrt -ab step. The run stops early once the factors are found.

Independent nodes in the same layer run in parallel, 2 workers on
this box. A node re-runs only if its output is missing, so the
driver resumes from completed stages. The driver invokes the
CADO-NFS binaries directly and does not rewrite CADO-NFS. Its
command patterns are the exact ones the project's own driver uses,
as recorded in the calibration run's command file and workunit
database. Derived values match the project's driver: Bf =
2**lpb1, Bg = 2**lpb0, area = 2**(2*I-1) * qmin, and the
rels_wanted fallback is int(0.81 * sum(2**lpb/ln(2**lpb))).
`DAG.md` documents the structure, the stages, and the sub-DAGs.

### 2.4 Cross-checks from the RSA-Prime engine

The owner's RSA-Prime engine runs alongside CADO-NFS as
cross-check nodes in the DAG, taken verbatim. Factor 0.99 runs 61
unit tests with 0 failures. Its quadratic sieve factors a
30-digit semiprime (288986332189663757016053995249) in 13.0
seconds and recovers the known factor 812790250833157, with 638
relations, 188 blocks, and 21,339 candidates. Its GNFS setup
verifies the base-m polynomial identity f(m) == N and builds
rational and algebraic factor bases of sizes 168 and 157 at
degree 5 with bounds 1000 and 1000. The BQN array kernels
(fused-kernel.bqn, mobius-bridge.bqn) are attempted and blocked on
this machine: this build of CBQN (commit
588753c83963f5b1e9247bfba9c16758da22a348) rejects the nabla
(U+2207) and theta (U+03B8) identifiers the files use, with the
error "Unknown characters". The files are the owner's, taken
verbatim, and are not rewritten to fit the tool. The array work in
the run is done by CADO-NFS sieving and linear algebra and by the
Factor quadratic sieve instead.

## 3. Calibration

Two calibration factorizations run before the target run. Both
verify by multiplication.

### 3.1 59-digit calibration (CADO-NFS README example)

The target is the CADO-NFS README example number, 59 digits:
90377629292003121684002147101760858109247336549001090677693.
The driver auto-selects the c60 parameter file. Wall time is 66
seconds (factorization 61.1 seconds elapsed, 48.94 seconds CPU).
Sieving produces 54,305 relations in 31.7 seconds over 1,802
special-q values and 22 q-intervals. The factors are four 15-digit
primes: 260938498861057, 588120598053661, 760926063870977, and
773951836515617. The product equals N exactly and all four are
prime. The log is `logs/calib59.log`.

### 3.2 80-digit calibration (synthetic semiprime)

The target is a synthetic semiprime generated locally, with p and
q each 40 digits:

- p = 3646145125719001888002753562073855374079
- q = 4586315596484532772155328562582497331107
- N = 16722372257131115877945740911896196316950319396248402582614721355820926608175453

The driver auto-selects the c80 parameter file. Wall time is 517
seconds (8.6 minutes); factorization is 511.8 seconds elapsed and
547.5 seconds CPU. Sieving produces 297,388 relations in 429.6
seconds over 5,513 special-q values, 692 relations per second.
The parameter file wants 233,399 relations, and 6,625 free
relations are collected. Filter timings are dup1 3.3 seconds,
dup2 11.1 seconds, purge 7.1 seconds, merge 5.2 seconds, replay
1.5 seconds. Linear algebra (bwc) takes 11.0 seconds, characters
1.4 seconds, square root 4.0 seconds. The factors recovered are
exactly p and q above. The product equals N exactly and both are
prime. The log is `logs/calib80.log`.

### 3.3 DAG driver validation

The DAG driver reproduces the 59-digit calibration exactly. The
command `python3 dag.py --params params.c60 --n <59-digit>
--workdir work/dagtest59` completes in 58 seconds with exit 0 and
recovers the exact same four factors over 10 intervals and 54,305
relations, product equals N, all prime. After the filter-loop
refactor described in Section 5.3, the driver is validated again
on the 59-digit number in 42.3 seconds with exit 0 and the same
four factors. The log is `logs/dagtest59loop.log`.

## 4. Target and sizing

### 4.1 Target selection

The target is RSA-100, 100 digits and 330 bits, the smallest RSA
challenge number and the largest that fits this box. RSA-110 is
about 202 times the 80-digit effort (about 29 hours), which
exceeds the 12-hour budget for this run. RSA-100 is therefore the
largest real RSA challenge number that fits, and the smallest
challenge number there is.

### 4.2 L-notation sizing

GNFS L-notation gives the sizing ratio. RSA-100 is about 36 times
the 80-digit effort (L(100)/L(80) = e^3.59). At the measured
80-digit wall time of 517 seconds, 36 times 517 seconds is about
18,600 seconds, about 5.2 hours. A relation-count cross-check
agrees in order: the c100 parameter file wants 3,600,000
relations, 15.4 times the c80 file's 233,399. At a conservatively
reduced sieving rate of 250 to 400 relations per second, against
692 measured at c80, sieving is about 2.5 to 4 hours, plus
polynomial selection, filtering, linear algebra, and square root.
The expected RSA-100 wall time is therefore about 4 to 6 hours,
within the 12-hour budget, and sieving-dominated.

The measured run comes in faster than the estimate, 93.9 minutes
for the successful path, because the shipped polynomial sieves at
about 1,424 relations per second, well above the conservative
250 to 400 relations per second assumed in the sizing. The
polynomial selection stage is also skipped: the run uses
CADO-NFS's own shipped polynomial for the c100 parameter set
(`parameters/polynomials/c100.poly`, degree 5, skew 19621.398,
Murphy-E 1.480e-05), which is the project's supplied polynomial
for this parameter set, not a substitution. The derived values
are Bf = 67,108,864, Bg = 33,554,432, area = 3.775e+11,
poly_chunks = 28, and rels_wanted = 3,600,000.

Two earlier launches stop early when the virtual machine is
replaced during polynomial selection, after about 16 minutes and
about 118 minutes. Those launches are wasted work and are not
part of the successful run's compute. Each virtual-machine
replacement also wipes the system libhwloc.so.15 library that
`las` and `polyselect` link against; it is restored each time
from the Debian bookworm package libhwloc15_2.12.0-4, and a full
library sweep confirms no other missing libraries.

## 5. Results

### 5.1 Third launch: sieving completes, purge fails

The third launch starts 2026-11-20 03:37:51 UTC and runs 2,596
seconds. Polynomial selection is skipped. Factor base takes 0.6
seconds and free relations 23.1 seconds. Sieving runs 108
q-intervals and collects 3,604,342 relations in 2,531 seconds,
about 1,424 relations per second, against the wanted 3,600,000.
The first two intervals take about 61 seconds each and yield
100,501 relations, about 50,250 per interval and about 1,620
relations per second. Duplicate removal takes 13.0 seconds for
dup1 (2 slices) and 41.2 and 41.7 seconds for the two dup2 slices.
Purge then fails with return code 2.

The purge log gives the cause. Purge reads 2,762,542 unique
relations, 76.6 percent of the 3,604,342 collected, against
3,459,675 distinct primes, a start excess of -697,133. Relations
are only 80 percent of distinct primes. Singleton removal then
cascades and collapses the matrix to 49 rows, final excess
-10,736, so purge refuses with "number of rows < number of
columns + keep". The matrix is below the percolation threshold.

The c100 parameter file's rels_wanted of 3,600,000 is
under-calibrated for the shipped polynomial. The c80 calibration
itself needs 3 purge rounds (excess -13,249, then -3,549, then
+160) and succeeds at relations over distinct primes of 0.976.
RSA-100 sits at 2,762,542 / 3,459,675 = 0.798, well below that.
The driver's own fallback formula for rels_wanted (0.81 * sum
2**lpb/ln(2**lpb)) gives about 4.58 million for c100, above the
file's 3.6 million. The driver's incremental formula
(max(nunique * 0.01, -excess, 10000) = 27,625 more unique
relations here) would need dozens of rounds at this deficit, so
it is not the right tool for a first top-up.

### 5.2 The iterated filter loop and the occupancy model

The filter stage is restructured as an iterated loop: dup1,
dup2, purge; on failure the driver parses the purge log, estimates
the total relations needed, sieves the shortfall, and re-filters,
for up to 25 rounds. The estimate is an occupancy model.
Distinct primes grow as D(R) = Dmax * (1 - exp(-c * R)), with
Dmax = pi(Bf) + pi(Bg), about 6.0 million, and c fitted from the
measured (R, D) point, solved for relations over distinct primes
equal to 1.08. The model is validated on the c80 calibration:
fitted from c80's final purge point, it predicts the first purge
point's distinct count to within 0.5 percent. For RSA-100 round 1
it estimates 6,771,450 total relations, about 5.19 million
unique. The driver's incremental formula is kept as a floor and
each round re-measures and re-fits, so the loop is
self-correcting. Phase B sieving becomes a sieve_until closure
the filter loop calls for top-ups. The refactor is validated
end to end on the 59-digit number in 42.3 seconds with exit 0 and
the same four factors.

### 5.3 Resume: purge succeeds, factors recovered

The resume starts 2026-11-20 04:26:36 UTC on the same workdir,
with no wipe, and runs 3,036 seconds. Filter round 1 fails
identically (unique 2,762,542, distinct 3,459,675, excess
-10,736) and sets the new target at 6,771,450 relations. Top-up
sieving runs 108 to 246 intervals and 3,604,342 to 6,776,932
relations, 3,172,590 more, with Phase B done at 2,801.4 seconds.
Per-interval yield declines slowly over the run, from about
50,000 to about 33,000 relations.

Filter round 2 succeeds at 2,963.8 seconds. Purge reads
5,162,783 unique relations against 4,530,073 distinct primes, a
ratio of 1.14, above the 1.08 target. The occupancy model errs on
the safe side: it predicts about 4.8 million distinct, and the
actual count is 4.53 million. The final matrix after singleton and
clique removal is 351,341 rows by 351,191 columns, excess +150,
exactly the keep = 150 target. Purge itself takes 50.1 seconds,
merge 4.0 seconds, replay 2.7 seconds.

Phase D follows. Linear algebra (bwc) takes 39.8 seconds,
characters 3.2 seconds, sqrt -ab 2.2 seconds, sqrt dependency 0
19.2 seconds, sqrt dependency 1 19.8 seconds. There are 27 kernel
dependencies; the factors are found after 2 dependencies. The DAG
completes in 3,035.6 seconds. The run ends 2026-11-20 05:17:12 UTC.

### 5.4 Combined timings

The successful path is the third launch (2,596 seconds) plus the
resume (3,036 seconds), 5,632 seconds of wall time, 93.9 minutes.
Sieving dominates: about 5,300 seconds of the 5,632 seconds,
roughly 94 percent (2,531 seconds in the third launch and about
2,730 seconds of top-up sieving in the resume). Filtering,
linear algebra, characters, and square root together take about
300 seconds. The full stage timings are:

| Stage | Third launch (s) | Resume (s) |
|---|---:|---:|
| Factor base | 0.6 | skipped (resumed) |
| Free relations | 23.1 | skipped (resumed) |
| Sieving | 2,531 (108 intervals, 3,604,342 relations) | about 2,730 (108 to 246 intervals, to 6,776,932 relations) |
| dup1 | 13.0 | 25.5 |
| dup2 | 41.2 / 41.7 | 77.1 / 78.4 |
| purge | fail (rc 2) | 50.1 (succeeds round 2) |
| merge | — | 4.0 |
| replay | — | 2.7 |
| Linear algebra (bwc) | — | 39.8 |
| Characters | — | 3.2 |
| sqrt -ab | — | 2.2 |
| sqrt dependencies 0, 1 | — | 19.2 / 19.8 |
| Total | 2,596 | 3,036 |
| Combined successful path | 5,632 s (93.9 min) | |

The average relation yield is about 27,549 relations per
q-interval over the 246 intervals.

## 6. Verification

Verification is independent of CADO-NFS and of Factor. It uses
integer multiplication and deterministic primality testing in a
fresh process, not the driver. Three checks pass:

1. The product of the two recovered factors equals RSA-100
   exactly.
2. Both factors are prime, by deterministic Miller-Rabin with 20
   bases.
3. Both factors match the published 1991 Lenstra factors exactly.

The factors are:

- 37975227936943673922808872755445627854565536638199
- 40094690950920881030683735292761468389214899724061

Both are 50 digits. The verification is recorded in
`work/rsa100/factors.txt` and `work/rsa100/verify.txt`. A factor
is reported only after this verification passes. The calibration
runs are verified the same way: the 59-digit run recovers four
15-digit primes whose product equals N, and the 80-digit run
recovers exactly the locally generated p and q.

## 7. Reproducibility

### 7.1 Environment

- Machine: Linux virtual machine, 1.5 shared CPUs, about 6 GB
  RAM, no GPU. Hostname htch-runtime.
- Python 3.12.3. cmake 3.28.3. gcc and g++ 13.3.0. GMP runtime
  and headers present. MPFR and MPC headers missing.
- CADO-NFS commit 692ecb7e62f0f3bdab88ee44cc60b8ded0ea1a1b,
  dated 2026-10-02, from
  https://gitlab.inria.fr/cado-nfs/cado-nfs.
- Factor 0.99 for the RSA-Prime cross-checks. CBQN commit
  588753c83963f5b1e9247bfba9c16758da22a348 for the BQN attempt.

### 7.2 Commands

Fetch and build CADO-NFS:

    git clone https://gitlab.inria.fr/cado-nfs/cado-nfs
    cd cado-nfs
    make -j2

The build takes about 47 minutes on this box with the project
virtual environment (flask 3.1.3) first on PATH so cmake finds
the virtual environment's python3. The binaries land in
`build/<hostname>/`.

Run the RSA-100 factorization through the DAG driver:

    python3 dag.py --params <path>/parameters/factor/params.c100 \
        --n 1522605027922533360535618378132637429718068114961380688657908494580122963258952897654000350692006139 \
        --workdir work/rsa100 \
        --bindir <path>/build/<hostname> --threads 2 \
        --poly <path>/parameters/polynomials/c100.poly

The `--poly` option skips the polynomial-selection sub-DAG and
uses CADO-NFS's shipped c100 polynomial. Without `--poly`, the
driver runs its own polynomial selection, which on this box takes
hours per admin-range chunk and exceeds the virtual machine's
lifetime between replacements.

The driver resumes from completed stages. A stage whose output
already exists is skipped, and sieving resumes from the existing
relation files. If the virtual machine is replaced mid-run, the
same command on the same workdir picks up where the previous run
stopped. One environment note: a virtual-machine replacement
wipes the system libhwloc.so.15 library that `las` and
`polyselect` link against. It is restored from the Debian
bookworm package libhwloc15_2.12.0-4; a full library sweep
confirms no other missing libraries.

### 7.3 Files

- `dag.py` — the recursive DAG driver
- `DAG.md` — DAG structure, stages, sub-DAGs, cross-check nodes
- `STATUS.md` — build, calibration, sizing, run timings, verification
- `calib80.txt`, `target-rsa100.txt` — the calibration and target numbers
- `logs/` — calibration, DAG validation, and RSA-100 run logs and timings
- `logs-rsaprime-unit-tests.txt` — the Factor unit test output
- `work/rsa100/factors.txt`, `work/rsa100/verify.txt` — the factors and verification

The vendored CADO-NFS source and the intermediate work data
(relations, matrices) are not in this repository. CADO-NFS is
referenced by its commit hash above. The relation files and
matrices for the RSA-100 run total about 990 MB and are retained
locally with the run, not published here.

## 8. Limitations

- This is a verified reproduction, not a world record. RSA-100
  was first factored in 1991. The public record stands at
  RSA-270 (895 bits, November 2026). Nothing in this run changes
  the security of any deployed RSA key size. RSA-2048 is not
  affected.
- The run covers one target, RSA-100, on one machine. The
  timings are specific to this 1.5-CPU virtual machine with no
  GPU. Sieving dominates at about 94 percent of wall time and is
  the clear candidate for a GPU port. RSA-110 is about 200 times
  the 80-digit effort, about 29 hours on this box, so it belongs
  on a GPU machine, not here.
- The BQN array kernels are blocked on this machine's CBQN build,
  which rejects the nabla and theta identifiers the owner's files
  use. The files are taken verbatim and are not rewritten. The
  array work in the run is done by CADO-NFS sieving and linear
  algebra and by the Factor quadratic sieve instead.
- The RSA-Prime nodes are documented in DAG.md. The GNFS-setup
  and QS cross-checks run as documented nodes; the BQN node is
  recorded as attempted-and-blocked. The RSA-Prime nodes are not
  yet wired into `dag.py` as executable nodes.
- Two earlier launches stop early when the virtual machine is
  replaced during polynomial selection. The driver resumes from
  completed stages, so no sieving work is lost to a replacement
  after sieving starts, but polynomial selection on this box is
  slower than the virtual machine's lifetime between replacements,
  which is why the run uses the shipped polynomial.
- The occupancy model is a fitted estimate, validated on the
  c80 calibration to within 0.5 percent. It errs on the safe side
  for RSA-100, predicting about 4.8 million distinct primes
  against an actual 4.53 million. The sizing in Section 4.2 is an
  estimate, labelled as such; every other number in this paper is
  a measured value from the logs, the calibration runs, or the
  verification files.

## 9. References

1. CADO-NFS. INRIA. https://gitlab.inria.fr/cado-nfs/cado-nfs.
   Commit 692ecb7e62f0f3bdab88ee44cc60b8ded0ea1a1b (2026-10-02).
2. RSA Factoring Challenge. RSA Laboratories, 1991.
   https://en.wikipedia.org/wiki/RSA_Factoring_Challenge
3. Integer factorization records.
   https://en.wikipedia.org/wiki/Integer_factorization_records
4. Lu, E. "Factoring RSA-260." Cognition, 3 September 2026 and
   9 September 2026. https://cognition.com/blog/factoring-rsa-260
5. Weis, S. A. "RSA-896." 19 September 2026. https://saweis.net
6. Boudot, F., Gaudry, P., Guillevic, A., Heninger, N., Thomé,
   E., and Zimmermann, P. RSA-250 (829 bits), February 2020,
   about 2,700 core-years, CADO-NFS on CPUs. Record as listed in
   the integer factorization records (reference 3). The same
   team reports the 240-digit experiment in "Comparing the
   difficulty of factorization and discrete logarithm: a
   240-digit experiment," Advances in Cryptology, CRYPTO 2020.
7. RSA-Prime. Owner's RSA research engine (Factor, BQN, Lean).
   https://github.com/AHMADALIPARR/RSA-Prime
8. Lenstra, A. K. Factorization of RSA-100. 1 April 1991.
   Published factors as recorded in the RSA Factoring Challenge.
