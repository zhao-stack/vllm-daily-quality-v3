# Main2Main analysis contract

## Contents

- Classification
- Strict action gate
- Exact-contract rules
- Expanded review

## Classification

- `introduced_break`: the dependency is compatible at `old` and incompatible at `new`, or it did not exist at `old` but a newly added upstream contract conflicts with a verified downstream patch/override. This is the only default repair-list class.
- `compatibility_warning`: the exact contract changed but the downstream endpoint still accepts it. Review for feature gaps without claiming a break.
- `preexisting`: both endpoints are incompatible. Report separately and do not attribute it to the selected range.
- `fixed`: old is incompatible and new is compatible. Review whether compatibility code can be removed.
- `analysis_unresolved`: a target, MRO, binding, signature, runtime path, or version lane is not statically provable. Never guess.

## Strict action gate

Promote a candidate to `modify` only when all values are true:

```text
relationship_verified
AND contract_changed
AND runtime_reachable
AND version_lane_matches
```

A changed file, same-name method, stale mapping, or copied-code marker cannot pass the gate by itself. When any gate is false or unknown, use `review` or `dismiss`.

For this interface-only pass, `runtime_reachable` means that source proves a concrete downstream callsite or a patch/override installation relation. Full model, device, process, and future-call-path reachability is intentionally not required: a proven interface mismatch remains reportable even when the current workload has not exercised it.

## Exact-contract rules

### Patch, override, and inheritance

- Generate relationships consumer-first from the pinned vllm-ascend source.
- Resolve override owners through the complete statically provable MRO, including imported and external bases.
- Record patch target, replacement, installer scope, guards, assignment line, descriptor, and every proven occurrence.
- Treat a missing or moved target as a contract delta even when no new signature exists.
- Compare owner-scoped AST parameter shapes, not signature strings, annotations, or formatting.
- Require a unique implementation-identical destination before reporting a callable rename.
- Treat `__init__` specially: require a proven construction binding or `super().__init__` forwarding. Optional-only upstream additions forwarded through `**kwargs` remain compatible.
- Do not treat bare `**kwargs` as semantic proof for renamed keywords.
- Keep incomplete or ambiguous MRO and patch bindings unresolved.

### Direct imports and adapted functions

- Report an executable import when its old module or symbol exists and its exact new path no longer resolves.
- Resolve file relocation only from Git rename evidence; do not guess from a same-name symbol.
- Exclude imports and attribute chains inside exact `vllm_version_is(...)` compatibility branches from the default profile.
- Treat `import vllm` plus a `vllm.x.y.symbol` attribute chain as an exact reference by resolving the longest old-tree module prefix.
- Promote an adapted free function only when source proves that the local callable is bound back to the exact upstream module attribute. A comment alone is insufficient.

### Exact downstream calls and return protocols

- Resolve each downstream call to one exact vLLM callable before comparing it. Bind that callsite's concrete positional and keyword shape at old and new; do not use replacement-signature substitutability for this direction.
- Re-resolve imported, annotated, or constructed vLLM receiver members independently at old and new only through a unique statically provable single-inheritance chain. For downstream `self`/`super`, first prove one effective upstream owner from the pinned vllm-ascend MRO and validate that exact owner in both snapshots; do not guess an owner move.
- Treat literal `*tuple`/`*list` and literal-string-key `**dict` expansions as exact. Keep runtime `*args`, `**kwargs`, dynamic dispatch, ambiguous aliases, and incomplete receiver/MRO resolution unresolved.
- A call that cannot be uniquely resolved is omitted at discovery. Once the dependency is proven, an unknown old/new endpoint, runtime signature, or constrained return contract is `analysis_unresolved`. Unused, forwarded, escaping, or unconstrained results do not create a return-use finding.
- Check downstream return consumption only when source proves the use, such as fixed unpacking, literal subscript, iteration, context-manager use, or `await`. An unused or merely forwarded result is not a return break by itself.
- Check patch/override returns with conservative structural covariance: every proven replacement return variant must satisfy an allowed upstream variant. This is not arbitrary nominal subtype inference. A return-forwarding `super()` path follows the endpoint contract.
- Keep raise-only/abstract bodies distinct from implicit `None`. Prefer a precise return annotation for abstract stubs; annotation/body conflicts and dynamic return expressions remain unresolved.
- Emit parameter, downstream return-use, and replacement-return findings separately. An `old=True -> new=False` transition, or an old-missing/new-conflicting patch/override contract, enters the repair list only when all four gates are true.

### Priority

- Verified runtime patch break: P0 when every action gate passes.
- Verified override/import break: P1 when every action gate passes.
- Compatible changes, pure inheritance, unresolved analysis, and historical issues: P2 review/dismiss.

## Expanded review

Enable `expanded` only on explicit request. Legacy static field/call-protocol, registration, inheritance-only, and broad tables remain supplemental. Exact dynamically resolved callsites and return protocols belong to the default profile. Registration is review-only closure and broad matches are appendix-only; neither independently creates a repair item.
