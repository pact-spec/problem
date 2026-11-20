# PACT problem types, revision -01

The pages served at <https://pact-spec.github.io/problem/>.

Revision -01 of the Internet-Draft `draft-laxsharma-pact` identifies each of its problem types by an
address under that prefix, and its Section 18.5 says documentation is kept there. This repository is
that documentation: one page for each type, served at the type's own address, and an index.

Eight types are defined by the draft, in its Table 9. Twenty-two more are used by the reference
implementation of revision -01 for failures the table does not cover. Each page says which of the two
it is, which section of the draft the response names, when the type is returned, and which exchange of
the implementation's recorded run shows it.

Later revisions of the draft do not use this prefix.

## How the pages are made

`build_pages.py` writes every page. It reads three things from a checkout of
[pact-spec/spec](https://github.com/pact-spec/spec) at tag `v0.1.2`:

- Table 9 of `draft/draft-laxsharma-pact-01.txt`;
- the list that `python3 tools/facilitator.py --problems` prints;
- `evidence/trace.json` and `vectors/reject/*.json`, for the recorded exchanges.

The sentences that say when a type is returned are written in the script. It stops if they do not
cover exactly the types the implementation prints, if the implementation's table disagrees with the
draft's, or if a type it marks as never returned appears in the recorded run.

To rebuild, in an environment where the tools of that repository run:

```
python3 build_pages.py /path/to/spec
```

Do not edit a page by hand. Change the script and build again.

## License

Apache-2.0, as in the `spec` repository. See `LICENSE`.
