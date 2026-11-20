# Recursive DAG — GNFS factorization orchestration

The factorization run is orchestrated as a recursive DAG, not a flat
linear script. The top level is the GNFS pipeline. Each stage is a node.
Each node depends only on its predecessors' output files. Each stage
decomposes into its own sub-DAG of independent tasks. The whole graph is
topologically ordered, each node is computed once from its predecessors,
and independent nodes in the same layer run in parallel (2 workers on
this box).

Implementation: `dag.py`, a small DAG driver (Python). A static Makefile
was considered first; the sieving layer is dynamic (the number of
q-intervals is only known at runtime, when rels_wanted is reached), so
a driver that expands the graph at runtime is the correct tool. The
task's wording allows "a Makefile or a small DAG driver". CADO-NFS
itself is NOT rewritten; the driver invokes its binaries directly, using
the exact command patterns the cado-nfs.py driver itself uses, as
recorded in the calibration run's `c60.cmd` and workunit database
(`c60.db`).

Stage order follows CADO-NFS's own task order (scripts/cadofactor/
cadotask.py, the `self.tasks` list for Computation.FACT):

    polysel -> factorbase -> freerel -> sieving
      -> dup1 -> dup2 -> purge -> merge -> replay
      -> linalg -> characters -> sqrt -> verify

## Top-level DAG (nodes and edges)

    N (target integer, public RSA challenge number)
      |
      v
    [polyselect]  -- admin-range chunks -> ropt -> pick best poly
      |  out: work/poly
      v
    [factorbase]  -- makefb (algebraic side; rational side is linear)
      |  out: work/roots1.gz
      v
    [freerel]     -- freerel, builds the renumber map
      |  out: work/freerel.gz, work/renumber.gz
      v
    [sieving]     -- las, split into q-interval sub-DAG (see below)
      |  out: work/rels.q0-q1.gz per interval
      v
    [dup1]        -- dup1 over all rels files
      |  out: work/dup1/<slice>/dup1.0.0000.gz
      v
    [dup2]        -- one node per dup1 slice
      |  out: (deduped slices)
      v
    [purge]       -- purge, keep from params
      |  out: work/purged.gz
      v
    [merge]       -- merge, target_density from params
      |  out: work/history.gz
      v
    [replay]      -- replay, builds the sparse matrix + index
      |  out: work/sparse.bin, work/index.gz
      v
    [linalg]      -- bwc.pl :complete (block sub-DAG inside, see below)
      |  out: work/bwc/W (kernel vectors)
      v
    [characters]  -- characters, needs poly + purged + index + ker
      |  out: work/kernel, work/dense.bin
      v
    [sqrt]        -- sqrt -ab, then one node per kernel dependency
      |  out: work/factors (p, q)
      v
    [verify]      -- independent check: p*q == N, p and q prime
      |  out: work/factors.txt, work/verify.txt
      v
    [done]

## Execution phases (dag.py)

Three fan-outs are dynamic (the node count is only known at runtime),
so execution is phased. Within each phase the nodes form a DAG layer
and run in parallel (2 workers).

  Phase A  polyselect (admin chunks) -> ropt -> pick -> factorbase,
           freerel. The factorbase and freerel nodes are independent
           (both need only the poly) and run in parallel.
  Phase B  sieve: q-interval nodes added in batches (one per worker)
           until the relation count reaches the current target
           (rels_wanted from the parameter file, or a larger target
           set by the filter loop when purge needs more relations).
           Relation count = non-comment lines in the rels files.
  Phase C  dup1 -> dup2 (one node per slice, count read from dup1's
           output) -> purge, ITERATED: if purge fails (matrix below
           the percolation threshold, negative excess), the loop
           estimates more relations (occupancy model, see below),
           calls Phase B to sieve them, and re-filters; up to 25
           rounds. On purge success -> merge -> replay.
  Phase D  linalg (bwc) -> characters -> sqrt -ab -> sqrt (one node
           per kernel dependency, count read from the dep files)
           -> verify.

## Sub-DAG: polynomial selection

The admin range [0, admax] is split into chunks of adrange (from the
parameter file); each chunk is an independent `polyselect` node
(parallel). Each chunk's candidates are root-optimized by an
independent `polyselect_ropt` node (parallel). `polyselect3` then
picks the best poly by Murphy-E.

    [N]
      |
      +-- [polyselect-0]  admin 0..adrange
      +-- [polyselect-1]  admin adrange..2*adrange
      +-- ...             (admax/adrange chunks)
      |      (parallel layer)
      v
    [polyselect-ropt-0] [polyselect-ropt-1] ...  (parallel layer)
      |      |      |
      v      v      v
    [polyselect-pick]  (polyselect3, best Murphy-E)
      |
      v
    [poly]  (feeds factorbase, freerel, las, characters, sqrt)

Derived values (the driver computes these the same way cado-nfs.py
does): Bf = 2**lpb1, Bg = 2**lpb0, area = 2**(2*I-1) * qmin.

## Sub-DAG: sieving (las over q-intervals)

Sieving is the parallel layer. One `las` node per q-interval:
q0 = qnext, q1 = q0 + qrange, aligned to the qrange boundary, each
producing one relations file named `rels.q0-q1.gz`. Intervals are
independent: no interval reads another interval's output. All
intervals feed the filter stage. The driver adds intervals in batches
until rels_wanted is reached, so the node count is determined by the
measured relation yield, not guessed in advance.

    [factorbase] [freerel]
         \        /
          v      v
    [sieve qmin-qmin+R] [sieve qmin+R-qmin+2R] ... [sieve qk-qk+R]
          \        |        /
           v       v       v
              [dup1]  (merge point: all rels files as inputs)

A failed interval can be re-run alone: its output file is missing, so
only that node re-runs. No other node is recomputed.

## Sub-DAG: filtering (dup1 -> dup2 slices -> purge, iterated)

dup1 splits the relations into slices (one file per slice). dup2 runs
one node per slice, in parallel. The slice count is read from dup1's
output directory after dup1 completes, so the fan-out is dynamic.

Purge is iterated, not once: it fails while the relation matrix is
below the percolation threshold (negative excess after singleton
removal). On failure the driver parses the purge log (unique
relations read, distinct primes before singleton removal, final
excess), estimates the total relations needed, sieves the shortfall
(Phase B, resumed from qnext), and re-runs dup1/dup2/purge. The
estimate is an occupancy model: distinct primes grow as
D(R) = Dmax*(1-exp(-c*R)) with Dmax = pi(Bf)+pi(Bg) and c fitted
from the measured (R, D) point, solved for relations/distinct =
1.08 (c80 succeeded at 0.976). The cado-nfs.py driver's own
incremental formula (max(nunique*0.01, -excess, 10000) more unique
relations) is kept as a floor; each round re-measures and re-fits.
This mirrors the driver's request_more_relations mechanism, which
is what the c80 calibration exercised (3 purge rounds there).
For RSA-100 the params file's rels_wanted (3.6M) proved
under-calibrated for the shipped poly (round 1: 2,762,542 unique
relations vs 3,459,675 distinct primes, start excess -697,133),
and the model estimated 6,771,450 total relations for round 2.

## Sub-DAG: linear algebra (bwc block Wiedemann)

bwc.pl runs the block Wiedemann method in phases, each a node inside
bwc.pl (prep -> krylov -> lingen -> mksol). The matrix is split into
m x n blocks (m, n from the parameter file); blocks are independent
and feed the generator. The DAG node is a single `bwc.pl :complete`
invocation; the block decomposition is expressed inside bwc.pl and
documented here. Splitting bwc.pl into separate Makefile targets
would require re-implementing its internal phase wiring, which is
exactly the kind of driver-logic replication this orchestration
avoids.

    [merge] (matrix)
      |
      v
    [bwc prep]     -- prepare matrix, split into m x n blocks
      |
      v
    [bwc krylov]   -- Krylov sequence, block-parallel
      |  (blocks are independent; each block is a node)
      v
    [bwc lingen]   -- linear generator (Berlekamp-Massey over blocks)
      |
      v
    [bwc mksol]    -- build kernel vectors
      |
      v
    [ker]  (kernel vectors, feed characters + sqrt)

## Sub-DAG: square root (one node per kernel dependency)

sqrt -ab produces the dependency files (dep.000.gz, dep.001.gz, ...).
One sqrt node runs per dependency, in parallel. The dependency count
is read from the dep files after sqrt -ab completes.

## RSA-Prime nodes (owner's code, verbatim, in the graph)

The owner's RSA-Prime engine (~/workspace/RSA-Prime) is part of the
DAG as cross-check and array nodes. It is used verbatim; nothing is
rewritten.

    [N]
      |
      +-- [rsaprime-gnfs-setup]  -- Factor gnfs-setup: base-m polynomial
      |      selection, rational + algebraic factor bases, verifies
      |      f(m) == N exactly. Cross-checks the CADO polyselect node:
      |      same N, independent implementation, same invariant.
      |      Depends on: N only. Runs in parallel with polyselect.
      |
      +-- [rsaprime-qs-crosscheck] -- Factor QS engine factors the
      |      CALIBRATION composite independently (single-polynomial
      |      QS: Tonelli-Shanks, log-sieve, GF(2) elimination, sqrt
      |      step). Cross-checks the calibration result. Depends on:
      |      the calibration target only. Runs in parallel with the
      |      CADO calibration run.
      |
      +-- [rsaprime-bqn-array] -- BQN array kernels (fused-kernel.bqn,
             mobius-bridge.bqn) run under CBQN. ATTEMPTED on this VM:
             both files fail to load because this CBQN build rejects
             the identifier characters the files use (nabla U+2207 in
             fused-kernel, theta U+03B8 in mobius-bridge) with
             "Error: Unknown characters". The files are the owner's,
             taken verbatim, and are NOT rewritten to fit the tool.
             The node is recorded as attempted-and-blocked with the
             exact error. The array work in the run is done by
             CADO-NFS (las sieving, bwc linear algebra) and the
             Factor QS engine instead.

## Verification node (independent)

    [sqrt] -> factors p, q
      |
      v
    [verify]  -- Python (independent of CADO-NFS and Factor):
                p * q == N exactly (integer multiplication),
                p and q are prime (deterministic Miller-Rabin).
                A factor is reported only after this node passes.

## Execution

    python3 dag.py --params src/parameters/factor/params.c100 \
        --n <target> --workdir work/<name> \
        --bindir src/build/htch-runtime --threads 2

Each node writes its stdout/stderr to logs/<node>.log with wall time
printed to the console. A node re-runs only if its output is missing.
Logs go to work/<name>/logs/ with wall time recorded.

## Scope

Synthetic keys and public RSA challenge numbers only. No real
third-party keys, certificates, or traffic. No claim of breaking a
secure deployed modulus. The CADO-NFS target is a public RSA
challenge number. This matches the scope in the RSA-Prime README.
