## What and why

<!-- One paragraph. Link the issue this implements (open one first for
     anything non-trivial). -->

Closes #

## Checklist

- [ ] `python -m pytest tests/ -q` passes in full (state the count)
- [ ] New behavior has new tests
- [ ] If this adds an Oracle/Driver/Resource: it passes the
      `cadaques.testing` conformance suites
- [ ] If this touches a design decision: the relevant ADR is referenced,
      or a new ADR is proposed in this PR
- [ ] No new dependencies (or: an ADR proposing them is included)
- [ ] The diff contains no reformatting of untouched lines
- [ ] Nothing public breaks without a deprecation shim (ADR-0010)
