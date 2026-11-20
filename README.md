# CADO-NFS RUN

CADO-NFS RUN factors RSA challenge numbers on a small box. It builds
CADO-NFS from source, drives the General Number Field Sieve through a
recursive DAG, and verifies the factors by independent multiplication.
The run it documents is RSA-100, factored on a 1.5-CPU virtual machine
with no GPU in 5,632 seconds of wall time.

## The result

RSA-100 is a 100-digit (330-bit) semiprime from the RSA Factoring
Challenge, first factored in 1991. This run reproduces that
factorization and verifies it:

- 37975227936943673922808872755445627854565536638199
- 40094690950920881030683735292761468389214899724061

Both factors are 50 digits. Their product equals RSA-100 exactly,
both are prime, and both match the published 1991 factors. This is a
verified reproduction at a scale this box owns, not a world record.
The full timings, relation counts, and verification are in
`work/rsa100/factors.txt`, `work/rsa100/verify.txt`, and `STATUS.md`.
The technical paper is `PAPER.md`.

## How the run works

`dag.py` is a small recursive DAG driver. The top level is the GNFS
pipeline as a DAG: polynomial selection, factor base, free relations,
sieving, filtering, linear algebra, square root, verification. Each
stage decomposes into its own sub-DAG of independent tasks. Sieving
splits into independent q-interval nodes. Polynomial selection splits
into independent admin-range chunks. Duplicate removal splits into one
node per slice. Square root splits into one node per kernel
dependency. The graph is topologically ordered, each node is computed
once from its predecessors, and independent nodes in the same layer
run in parallel (2 workers on this box). `DAG.md` documents the
structure, the stages, and the sub-DAGs.

The filter stage runs as an iterated loop. Purge reports the relation
count and the distinct-prime count after each pass. When the matrix
sits below the percolation threshold, the driver estimates the total
relations the matrix needs with an occupancy model, sieves the
shortfall, and re-filters. The loop re-measures and re-fits each
round, up to 25 rounds, until purge succeeds. For RSA-100 the first
filter pass reads 2,762,542 unique relations against 3,459,675
distinct primes and does not reach the threshold; the loop estimates
6,771,450 total relations, sieves to 6,776,932, and the second pass
succeeds with a 351,341 by 351,191 matrix at excess +150.

CADO-NFS itself is not rewritten. The driver invokes its binaries
directly, using the exact command patterns the project's own driver
uses. The polynomial for RSA-100 is CADO-NFS's own shipped
polynomial for the c100 parameter set.

## Calibration

Two calibration factorizations run first and verify by
multiplication:

- 59 digits (the CADO-NFS README example): 66 seconds wall, 54,305
  relations, four 15-digit prime factors, product equals N, all prime.
- 80 digits (a synthetic semiprime generated locally): 517 seconds
  wall, 297,388 relations at 692 relations per second, exact factors
  recovered, product equals N, both prime.

The DAG driver reproduces the 59-digit calibration exactly (58
seconds, the same four factors). The RSA-100 sizing follows from the
80-digit run: L-notation puts RSA-100 at about 36 times the 80-digit
effort, about 5.2 hours at the calibration rate. The measured run
comes in faster, 93.9 minutes, because the shipped polynomial sieves
at about 1,424 relations per second.

## Cross-checks
A second  RSA-Prime engine runs alongside CADO-NFS as cross-check
nodes in the DAG. Factor 0.99 runs 61 unit tests with 0 failures. Its
quadratic sieve factors a 30-digit semiprime in 13.0 seconds and
recovers the known factor. Its GNFS setup verifies the base-m
polynomial identity f(m) == N and builds the rational and algebraic
factor bases. The BQN array kernels are not ran on this run.

## Reproduction

Environment: Linux, 1.5 shared CPUs, about 6 GB RAM, no GPU,
Python 3.12, cmake 3.28, gcc and g++ 13.3, GMP runtime and headers.

1. Fetch CADO-NFS at the pinned commit:
   `git clone https://gitlab.inria.fr/cado-nfs/cado-nfs`, commit
   692ecb7e62f0f3bdab88ee44cc60b8ded0ea1a1b (2026-10-02).
2. Build it: `make -j2` from the CADO-NFS source. The build takes
   about 47 minutes on this box. The binaries land in
   `build/<hostname>/`.
3. Run the DAG driver:
   `python3 dag.py --params <path>/parameters/factor/params.c100
   --n <RSA-100> --workdir work/rsa100
   --bindir <path>/build/<hostname> --threads 2
   --poly <path>/parameters/polynomials/c100.poly`
4. Verify: `work/rsa100/factors.txt` holds N and the two factors;
   `work/rsa100/verify.txt` holds the product check and the
   primality check.

The driver resumes from completed stages. A stage whose output
already exists is skipped, and sieving resumes from the existing
relation files.

## Limits

- This is a verified reproduction, not a world record. RSA-100 was
  first factored in 1991. The public record stands at RSA-270 (895
  bits, November 2026).
- Scope is synthetic keys and public RSA challenge numbers only. No
  real third-party keys, certificates, or traffic. No claim of
  breaking a secure deployed modulus. RSA-2048 is not affected.
- Sieving dominates the run: about 5,300 seconds of the 5,632
  seconds, roughly 94 percent. It is the embarrassingly parallel
  part and the clear candidate for a GPU port. RSA-110 is about 200
  times the 80-digit effort, about 29 hours on this box, so it
  belongs on a GPU machine, not here.
- The BQN array kernels are not ran which is the main thing powering the DAG.
## Licensing

The driver (`dag.py`), the documentation (`README.md`, `PAPER.md`,
`DAG.md`, `STATUS.md`), and the run evidence (logs, factors,
verification) are the owner's work, under the SNAPKITTY OPAQUE SOURCE
LICENSE v1.0 in `LICENSE` (SPDX
`LicenseRef-Sovereign-Leviathan-SL-AGPL3-001`, Copyright © 2026
SnapKitty West). CADO-NFS itself is the work of INRIA, licensed under
the GNU Lesser General Public License version 2.1, and it is referenced
here by its commit hash, not vendored: fetch it from the project's
repository and build it with the steps above. The RSA-Prime cross-check
code is the owner's work, referenced from its own repository, not copied
in.

## Files

- `dag.py` — the recursive DAG driver
- `DAG.md` — DAG structure, stages, sub-DAGs, cross-check nodes
- `STATUS.md` — build, calibration, sizing, run timings, verification
- `PAPER.md` — the technical paper documenting the run
- `calib80.txt`, `target-rsa100.txt` — the calibration and target numbers
- `logs/` — calibration, DAG validation, and RSA-100 run logs and timings
- `logs-rsaprime-unit-tests.txt` — the Factor unit test output
- `work/rsa100/factors.txt`, `work/rsa100/verify.txt` — the factors and verification
