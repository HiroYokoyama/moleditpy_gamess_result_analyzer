# How molecular orbitals are evaluated

Reference for `gamess_result_analyzer/mo_engine.py`, which turns the basis set
and MO coefficients parsed out of an GAMESS output file into values on a 3-D
grid, and then into a GAMESS cube file.

The intent is that anyone changing this file can check their work against
something other than "the picture still looks like an orbital" — see
[Verifying a change](#verifying-a-change) at the end.

## The quantity being computed

A molecular orbital is a linear combination of atom-centred basis functions:

```
psi_i(r) = sum_mu  C[mu][i] * phi_mu(r)
```

`C` comes from GAMESS's `MOLECULAR ORBITALS` block (`parser.parse_mo_coeffs`),
and each `phi_mu` is a contracted GAMESS built from the `BASIS SET IN INPUT
FORMAT` block (`parser.parse_basis_set`). `BasisSetEngine.evaluate_mo_on_grid`
sums that expression over every grid point.

Everything below is about getting `phi_mu` exactly right. Getting it *nearly*
right is the failure mode this document exists to prevent: a mis-normalized
basis function still produces a plausible-looking orbital with lobes in the
right places and the right symmetry, and only reveals itself when you measure
a nodal angle.

## Primitives

A Cartesian GAMESS primitive of angular momentum `(l, m, n)` is

```
g(r) = N(alpha, l, m, n) * x^l y^m z^n * exp(-alpha * r^2)
```

`N` is `_normalization_prefactor` and makes the primitive unit-normalized
(`integral |g|^2 dV = 1`):

```
N = (2*alpha/pi)^(3/4) * sqrt( (8*alpha)^L * l! m! n! / ((2l)! (2m)! (2n)!) )
```

with `L = l + m + n`. This follows from the standard form

```
N = (2*alpha/pi)^(3/4) * (4*alpha)^(L/2) / sqrt((2l-1)!! (2m-1)!! (2n-1)!!)
```

by substituting `(2l-1)!! = (2l)! / (2^l l!)`.

**The part that matters:** `N` depends on how `L` is *distributed* across the
three axes, not just on `L`. For an f shell,

```
N(0,0,3) / N(2,0,1) = 1/sqrt(5)
```

so `z^3` and `x^2 z` are scaled differently even though both are f functions.

## Shells

A shell shares one set of exponents and contraction coefficients across all
its components. `_precompute_shells` folds the contraction coefficients, the
per-primitive `N`, and the component weight into a single coefficient array,
so evaluation is a dot product against `exp(-alpha_k r^2)` per shell.

Components per shell type, in the order GAMESS emits them:

| type | shell | count | order |
|---|---|---|---|
| 0 | S | 1 | — |
| 1 | P | 3 | px, py, pz |
| 2 | D (Cartesian, `6D`) | 6 | xx, yy, zz, xy, xz, yz |
| 2 | D (spherical) | 5 | m = 0, +1, −1, +2, −2 |
| 3 | F (Cartesian, `10F`) | 10 | xxx, yyy, zzz, xyy, xxy, xxz, xzz, yzz, yyz, xyz |
| 3 | F (spherical) | 7 | m = 0, +1, −1, +2, −2, +3, −3 |
| 4 | G (spherical) | 9 | m = 0, +1, −1, …, +4, −4 |

Which form a job used depends on its basis set and on `5D`/`6D`/`7F`/`10F`, so the
parser reads it from the function labels of the MO table and marks each shell
`cartesian`. Each Cartesian function is normalized on its own, so `xx` and `xy` both
have unit norm. `SP` shells are split into an S shell and a P shell.

## Refusing what cannot be rendered exactly

Anything beyond G (H shells in `aug-cc-pV5Z`, `def2-QZVPP` on heavy elements)
has no definitions here. `_precompute_shells` raises `UnsupportedBasisError`
rather than skipping the shell: skipping left `start_idx` unset and
`current_idx` unadvanced, so every later shell read the wrong slice of the MO
coefficient vector — a silently wrong orbital with nothing on screen to say
so.

`mo_analysis` adds a second, broader guard. GAMESS prints one coefficient per
basis function, so if `len(coeffs) != engine.n_basis` the basis set that was
rebuilt is not the one the coefficients belong to — most often a shell
`parser.parse_basis_set` did not recognise, since it only matches the letters
`S P D F G`. That mismatch used to be absorbed by `min(len(raw), n_basis)`,
which silently truncated the vector and left every coefficient after the gap
paired with the wrong basis function.

Both refusals surface as a dialog naming the cause. Prefer adding that kind
of guard over a best-effort render: a partially-correct orbital is
indistinguishable from a correct one by eye.

## Spherical components: the subtle part

Each spherical component is written as a weighted sum of Cartesian monomials.
For example `f(z^3)` should have the shape of the real solid harmonic

```
r^3 * Y_30  ~  z(5z^2 - 3r^2)  =  2z^3 - 3x^2 z - 3y^2 z
```

The trap is that `(2, -3, -3)` are the coefficients on the **raw monomials**,
while the engine evaluates **normalized** Cartesian functions
`N(l,m,n) * x^l y^m z^n`. Because `N(0,0,3) != N(2,0,1)`, feeding the raw
coefficients through produces a different polynomial:

```
0.894 z^3 - 3x^2 z - 3y^2 z        (wrong — not proportional to any Y_lm)
```

The nodal cone moves from 39.2° to 28.6°, and the lobe proportions are wrong.
It still renders as a recognisable f orbital.

`_build_sph_defs` handles both halves of the correction:

1. **Shape** — divide each raw coefficient by `N(l,m,n)`, recovering the
   intended polynomial in the normalized-Cartesian basis.
2. **Scale** — normalize the whole component so `<phi|phi> = 1`, using the
   exact angular overlap from `_angular_overlap`. This matches s/p/d, which
   are already unit-normalized, so components of different `l` mix correctly
   inside one MO.

The angular overlap is closed-form. For monomials `a` and `b` in the same
shell the radial part cancels, leaving

```
S(a,b) = 2^L * (A-1)!! (B-1)!! (C-1)!! * N(a) * N(b)
```

where `A = a_x + b_x` and so on, zero if any of `A`, `B`, `C` is odd. The
`2^L` is chosen so a single monomial has `S(a,a) = 1`.

Only the polynomials are hand-written; every constant is derived. This is
deliberate — the d table was hand-derived and correct, but the f and g tables
were hand-derived and wrong in two different ways.

## Historical bugs

Both were fixed in v3.12.0. They are recorded here because both were live for
a long time and neither was visually obvious.

- **f shells** used one hand-typed scalar per component, applied to
  components mixing different `N` topologies. Five of the seven were
  distorted; m = ±2 were correct by luck, being the only ones whose monomials
  share a topology.
- **g shells** did divide by `N`, but used hand-typed angular prefactors that
  disagreed *between* components — `g(+2)` came out with about twice the norm
  of `g(+1)`, so lobes of one component dominated others in a mixed MO.

## Grid and cube output

`CalcWorker` builds a padded bounding box around the molecule, evaluates the
MO on it, and writes a GAMESS cube. Cube geometry is in **Bohr**; the
molecule is stored in Angstrom, so the engine converts on the way in
(`mo_analysis.get_engine`) and `vis.py` converts back on the way out. A
negative voxel count on the `NX` line means the grid is already in Angstrom —
`vis._build_grid` honours that flag rather than converting unconditionally.

Cube values are written Z-fastest (C order); PyVista's `StructuredGrid`
expects points X-fastest (F order), which is why `_build_grid` reshapes and
re-flattens rather than passing the array straight through.

## Verifying a change

Anything touching the basis functions should be checked numerically, not
visually. Three independent checks, all in `tests/test_mo_engine.py`:

**1. Shape against an external reference.** A pure basis function must be
radial × `Y_lm`. This is convention-independent: a program's normalization
convention fixes an overall constant per component, it cannot make the
angular part something that is not a spherical harmonic. Compare against
`scipy.special.sph_harm_y` on random directions; cosine similarity must be
1.000000 for every component.

**2. Normalization.** Every component of every shell must integrate to 1
against itself. `TestSphericalShellNormalization` does this on a spherical
quadrature grid. Unequal norms within a shell mean the same MO coefficients
produce the wrong linear combination.

**3. Nodal angles.** These have closed forms and are the most sensitive to
the errors above:

| function | condition | angle from +z |
|---|---|---|
| f(z^3) | 5cos²θ = 3 | 39.23° |
| g(z^4) | cos²θ = (3 ± 2√(6/5))/7 | 30.56°, 70.12° |

A distorted component still has the right symmetry and sign pattern, so
these angles are usually the first thing to move.
