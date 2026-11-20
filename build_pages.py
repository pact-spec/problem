#!/usr/bin/env python3
"""Build the pages served at https://pact-spec.github.io/problem/.

Usage:  python3 build_pages.py PATH_TO_SPEC_CHECKOUT

PATH_TO_SPEC_CHECKOUT is a checkout of https://github.com/pact-spec/spec at the
commit the pages describe (tag v0.1.2). Run this in an environment where that
repository's tools run; see its evidence/requirements.txt.

Three things are read and nothing is typed twice:

  * Table 9 of draft/draft-laxsharma-pact-01.txt, the eight types the draft defines;
  * the list `tools/facilitator.py --problems` prints, the thirty types the
    reference implementation defines, with status and section;
  * evidence/trace.json and vectors/reject/*.json, for the recorded exchanges
    that were answered with each type.

The sentences that say when a type is returned are written here, in DOCS. The
script stops if they do not cover exactly the types the implementation prints,
if the implementation's table disagrees with the draft's, or if a type marked
as never returned turns up in the recorded run.
"""
import html
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
PREFIX = "https://pact-spec.github.io/problem/"
DRAFT = "https://www.ietf.org/archive/id/draft-laxsharma-pact-01.html"
REPO = "https://github.com/pact-spec/spec"
TAG = "v0.1.2"

# How each type stands in the reference implementation.
RECORDED = "recorded"        # the recorded run or a test vector shows it
RETURNED = "returned"        # returned for the condition described; the record does not show it
SHADOWED = "shadowed"        # there is code that would return it, and an earlier check answers first
ABSENT = "absent"            # listed, and no code returns it
INTERNAL = "internal"        # returned only when the Facilitator itself fails

DOCS = {
    # ---- the eight types of Table 9 ----------------------------------------
    "assurance-constraint-unsatisfied": dict(
        when=["The bond a contract offers is too small for the detection probability it declares. "
              "Section 7.2 admits a contract only if <code>B &gt;= P(1 - q)/q + E</code>, where P is the "
              "price, B the Seller's bond, E the amount released before a Verdict is recorded, and q the "
              "contract's <code>assurance.q_min</code>. A Facilitator evaluates this before it locks funds "
              "and refuses a contract that fails it."],
        impl="The implementation supports release on verification only, so E is zero. Its response adds "
             "four members to the problem details: <code>required_bond</code>, <code>declared_bond</code>, "
             "<code>q_min</code> and <code>price</code>."),
    "evidence-nonconformant": dict(
        when=["A Delivery's <code>evidence</code> member is absent, or does not conform to the verification "
              "profile the contract names. Section 6 has the Facilitator reject the Delivery and apply the "
              "remedy of Section 7.4 as though a FAIL Verdict had been recorded.",
              "The check is one of shape. It is not a judgment on the work."],
        impl="The implementation applies the remedy in the same step, so the contract is already settled "
             "when this response is returned. The response carries the contract's <code>state</code> and a "
             "<code>remedy</code> member."),
    "parent-unresolvable": dict(
        when=["A contract names a parent contract that the Facilitator cannot resolve and verify. "
              "Section 10.1 requires a Facilitator to reject such a child."],
        impl="The implementation does not implement subcontracts. It returns this type for every contract "
             "that names a parent, whether or not the parent exists."),
    "finality-ordering-violation": dict(
        when=["A child contract could become final later than its parent's challenge window can close. "
              "Section 10.3 requires a Facilitator to reject such a child, so that a parent is never "
              "attested before its children."],
        impl="The implementation lists this type and no code returns it. Every contract that names a "
             "parent is refused as <a href=\"parent-unresolvable\">parent-unresolvable</a> before this "
             "rule is reached.",
        standing=ABSENT),
    "parties-not-distinct": dict(
        when=["The contract's Buyer and Seller are the same party once their identifiers are normalized "
              "as Section 9.1 prescribes. Section 13.2 requires the two to be distinct."]),
    "algorithm-not-permitted": dict(
        when=["A signature's protected header names an algorithm outside the permitted set. Section 13.1 "
              "permits ES256, ES384 and EdDSA with an Ed25519 key, lets a verifier also accept Ed448, and "
              "requires every other value to be rejected, <code>none</code> included."],
        impl="The implementation accepts EdDSA, ES256 and ES384."),
    "verifier-not-independent": dict(
        when=["The party that attests to a Delivery is not independent of the contract's parties under "
              "Section 9.1, or is not the Verifier the contract names. Section 12.4 requires a Facilitator "
              "to reject a Verdict whose signer fails that test."],
        impl="The implementation returns it in five cases: a contract that names its Buyer or its Seller "
             "as Verifier; a Verdict not signed by the Verifier the contract names; a Verdict signed by "
             "the contract's Seller; a Verdict signed by its Buyer; and a Verdict on a disputed contract "
             "signed by the party that challenged it."),
    "release-exceeds-bond": dict(
        when=["A release before a recorded Verdict would take the total released above the Seller's bond. "
              "Section 7.1 forbids a Facilitator to permit that."],
        impl="The implementation lists this type and no code returns it. It supports release on "
             "verification only, under which nothing is released before a Verdict, and it answers a "
             "contract that asks for another release mode with "
             "<a href=\"release-mode-unsupported\">release-mode-unsupported</a>.",
        standing=ABSENT),
    # ---- the implementation's own ------------------------------------------
    "amount-invalid": dict(
        when=["An amount in the contract is one the Facilitator cannot represent. The implementation "
              "settles in whole cents and refuses an amount that is not a whole number of cents."],
        standing=RETURNED),
    "assurance-unsupported": dict(
        when=["The contract declares open challenge as its only source of assurance, which Section 7.2 "
              "forbids, or names an assurance mode the Facilitator does not implement.",
              "The implementation supports the mode <code>certain</code> only. For a mode other than "
              "<code>open</code>, its response lists the modes it supports in a <code>supported</code> "
              "member."],
        standing=RETURNED),
    "challenge-window-closed": dict(
        when=["A Challenge arrives when no challenge window is open for the contract. Under release on "
              "verification the window opens when a PASS Verdict is recorded and closes after the number "
              "of seconds the contract sets."]),
    "deadline-invalid": dict(
        when=["The contract's <code>task.deadline</code> is not an RFC 3339 timestamp, carries no time "
              "zone, or is already past on the Facilitator's clock."],
        standing=RETURNED),
    "facilitator-cannot-verify": dict(
        when=["A Verdict is signed by the Facilitator that settles the contract. Section 3 keeps the two "
              "roles apart: a Facilitator must not act as Verifier for a contract it settles."]),
    "facilitator-mismatch": dict(
        when=["The contract names a Facilitator other than the one it was sent to. Accepting it would let "
              "one signed contract be replayed at a second venue, the attack Section 16.7 describes."]),
    "internal-error": dict(
        when=["The Facilitator failed while handling the request. The detail names the kind of failure "
              "and nothing more."],
        standing=INTERNAL),
    "liability-missing": dict(
        when=["The contract has no <code>liability</code> member. Section 5.3 makes that member a required "
              "part of the contract."],
        impl="The implementation lists this type and does not return it. Its schema check runs first and "
             "answers a contract whose <code>liability</code> member is absent or empty as "
             "<a href=\"schema-invalid\">schema-invalid</a>.",
        standing=SHADOWED),
    "no-recorded-delivery": dict(
        when=["A Verdict names a contract that has no recorded Delivery. Section 12.4 forbids a "
              "Facilitator to accept such a Verdict."]),
    "object-conflict": dict(
        when=["The object conflicts with one the Facilitator already holds. There are four cases: a "
              "contract posted under an identifier that already holds a different contract; a Delivery "
              "whose <code>vtc_hash</code> does not commit to the contract; and a Verdict or a Challenge "
              "whose <code>delivery_hash</code> does not commit to the recorded Delivery.",
              "The response names Section 12.2, the section on idempotency, in all four cases. Only the "
              "first is a rule of that section. In the last three the response also carries the digest "
              "the Facilitator expected and the one it received."]),
    "payload-too-large": dict(
        when=["The request body is larger than the Facilitator accepts. The implementation's limit is "
              "1,048,576 bytes."],
        standing=RETURNED),
    "proof-nonconformant": dict(
        when=["A Challenge's proof does not conform to the contract. It names a verification profile "
              "other than the contract's, does not commit to the instrument the contract committed to, "
              "or lacks <code>results_hash</code> under the acceptance profile."]),
    "release-mode-unsupported": dict(
        when=["The contract asks for a release mode the Facilitator does not implement. The "
              "implementation supports <code>on-verification</code> only, and its response lists the "
              "modes it supports."]),
    "schema-invalid": dict(
        when=["The body is not JSON, is not a JSON object, does not validate against the schema for its "
              "type, or carries a <code>pact</code> version or a <code>type</code> the Facilitator does "
              "not implement.",
              "When a schema fails, the response names the schema and the path of the failing member."]),
    "settlement-unsupported": dict(
        when=["The contract asks for something the Facilitator's capability document does not advertise: "
              "a settlement binding, a network or a currency; a verification profile; or a price above "
              "its <code>max_contract_value</code>."],
        standing=RETURNED),
    "signature-invalid": dict(
        when=["A signature fails a rule of Section 13.1 other than the rule on algorithms. The cases "
              "are: the signature does not verify; a party that must sign has not, or has signed more "
              "than once; the protected header is not valid, or lacks <code>alg</code>, <code>kid</code> "
              "or <code>typ</code>; <code>kid</code> is carried beside the protected header; "
              "<code>typ</code> is not the media type of the object; the key cannot be resolved; or the "
              "algorithm is not that of the resolved key.",
              "An ECDSA signature whose s is in the high half of the curve order is refused with this "
              "type too. That rule is the implementation's own."]),
    "signature-missing": dict(
        when=["A Verdict carries no signature."],
        impl="The implementation lists this type and does not return it. Its schema check runs first and "
             "answers a Verdict whose <code>signature</code> member is absent or empty as "
             "<a href=\"schema-invalid\">schema-invalid</a>.",
        standing=SHADOWED),
    "signatures-unordered": dict(
        when=["The contract's <code>signatures</code> array is not sorted by normalized key identifier.",
              "This rule is the implementation's own. Revision -01 does not order the array, so one "
              "agreement signed in two orders would have two digests."]),
    "unexpected-signer": dict(
        when=["An object is signed by a party that may not sign it. There are two cases: a contract that "
              "carries a signature from a party that is neither its Buyer nor its Seller, and a Challenge "
              "signed by the contract's Seller.",
              "The second case is a choice of the implementation. Section 7.5 of revision -01 lets any "
              "party submit a Challenge."]),
    "unknown-contract": dict(
        when=["No contract has the identifier in the request, or the path is not one of the "
              "Facilitator's endpoints."],
        standing=RETURNED),
    "verdict-nonconformant": dict(
        when=["A Verdict does not conform to the contract. Its <code>instrument_hash</code> is not the "
              "instrument the contract committed to, its <code>profile</code> is not the contract's, or "
              "its <code>outcome</code> is neither PASS nor FAIL."]),
    "wrong-state": dict(
        when=["The request is not accepted in the contract's current state. The cases are a Delivery for "
              "a contract that is not FUNDED, a Verdict for a contract that is neither DELIVERED nor "
              "DISPUTED, and a request for the Work Attestation of a contract that is not yet terminal."]),
}

STANDING_LINE = {
    RETURNED: "The recorded run and the test vectors do not show this type.",
    SHADOWED: "The recorded run and the test vectors do not show this type.",
    ABSENT: "The recorded run and the test vectors do not show this type.",
    INTERNAL: "The recorded run and the test vectors do not show this type.",
}
INDEX_STANDING = {RETURNED: "not recorded", SHADOWED: "not returned", ABSENT: "not returned",
                  INTERNAL: "not recorded"}

CSS = """:root { color-scheme: light dark; --fg: #1a1a1a; --muted: #595959; --bg: #ffffff; --rule: #d9d9d9;
  --code: #f3f3f3; --link: #0b57a4; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e9e9e9; --muted: #a8a8a8; --bg: #121212; --rule: #333333; --code: #1e1e1e; --link: #86bdf6; }
}
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 46rem; margin: 0 auto; padding: 2rem 1rem 4rem; }
h1 { font-size: 1.6rem; line-height: 1.25; margin: 0.5rem 0 0.75rem; overflow-wrap: anywhere; }
h2 { font-size: 1.1rem; margin: 2rem 0 0.5rem; }
a { color: var(--link); }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.9em; }
code { background: var(--code); padding: 0.1em 0.3em; border-radius: 3px; overflow-wrap: anywhere; }
pre { background: var(--code); padding: 0.9rem 1rem; border-radius: 6px; overflow-x: auto; }
pre code { background: none; padding: 0; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.35rem 1.25rem; margin: 1rem 0; }
dt { color: var(--muted); }
dd { margin: 0; }
table { border-collapse: collapse; width: 100%; font-size: 0.95rem; }
th, td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
.wrap { overflow-x: auto; }
td:first-child code { white-space: nowrap; overflow-wrap: normal; }
.crumb { font-size: 0.95rem; margin: 0; }
.note, footer { color: var(--muted); font-size: 0.95rem; }
footer { margin-top: 3rem; border-top: 1px solid var(--rule); padding-top: 1rem; }
"""


def fail(message):
    sys.exit("build_pages.py: " + message)


def read_inputs(spec):
    draft_txt = (spec / "draft" / "draft-laxsharma-pact-01.txt").read_text()
    table9 = {}
    for ident, status, section in re.findall(r"^\s+\| ([a-z][a-z-]+)\s+\| (\d{3})\s+\| (Section [\d.]+)\s+\|$",
                                             draft_txt, re.M):
        table9[ident] = (int(status), section)
    if len(table9) != 8:
        fail(f"expected the eight rows of Table 9, found {len(table9)}")

    out = subprocess.run([sys.executable, "tools/facilitator.py", "--problems"], cwd=spec,
                         capture_output=True, text=True, check=True).stdout
    impl = {}
    for line in out.strip().split("\n"):
        uri, status, section = line.split("\t")
        if not uri.startswith(PREFIX):
            fail(f"unexpected address {uri}")
        impl[uri[len(PREFIX):]] = (int(status), section)
    for ident, row in table9.items():
        if impl.get(ident) != row:
            fail(f"{ident}: the draft says {row}, the implementation {impl.get(ident)}")

    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=spec, capture_output=True, text=True,
                            check=True).stdout.strip()
    tags = subprocess.run(["git", "tag", "--points-at", "HEAD"], cwd=spec, capture_output=True, text=True,
                          check=True).stdout.split()
    if TAG not in tags:
        fail(f"the checkout is not at tag {TAG}")

    trace = json.loads((spec / "evidence" / "trace.json").read_text())
    examples = {}
    for event in trace["events"]:
        body = event.get("response")
        if isinstance(body, dict) and str(body.get("type", "")).startswith(PREFIX):
            examples.setdefault(body["type"][len(PREFIX):], []).append(
                dict(kind="event", n=event["n"], status=event["status"], detail=body.get("detail", ""),
                     body=body))
    for path in sorted((spec / "vectors" / "reject").glob("*.json")):
        vector = json.loads(path.read_text())
        last = vector["events"][-1]
        body = last["response"]
        ident = body["type"][len(PREFIX):]
        if vector["expect"] != {"status": last["status"], "type": ident}:
            fail(f"{path.name}: the last exchange is not what the file expects")
        examples.setdefault(ident, []).append(
            dict(kind="vector", name=f"reject/{path.stem}", file=f"vectors/reject/{path.name}",
                 status=last["status"], detail=body.get("detail", "")))
    return table9, impl, commit, examples


def page(title, body):
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{html.escape(title)}</title>\n<style>\n{CSS}</style>\n</head>\n"
            f"<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n")


def section_link(section):
    number = section.split()[1]
    return f"<a href=\"{DRAFT}#section-{number}\">{html.escape(section)}</a>"


def footer(commit):
    return ("<footer>\n<p>These identifiers belong to revision -01 of "
            f"<a href=\"{DRAFT}\">draft-laxsharma-pact</a>, an individual Internet-Draft that later revisions "
            "have superseded. Later revisions do not use this prefix.</p>\n"
            f"<p>The implementation described is the reference implementation at tag {TAG} of "
            f"<a href=\"{REPO}/tree/{commit}\">pact-spec/spec</a>.</p>\n</footer>")


def recorded_cell(found):
    events = [str(x["n"]) for x in found if x["kind"] == "event"]
    vectors = [x["name"].split("-")[0] for x in found if x["kind"] == "vector"]   # reject/03
    parts = []
    if events:
        parts.append(("event " if len(events) == 1 else "events ") + ", ".join(events))
    if vectors:
        parts.append(("vector " if len(vectors) == 1 else "vectors ") + ", ".join(vectors))
    return "; ".join(parts)


def build(spec):
    table9, impl, commit, examples = read_inputs(spec)
    if set(DOCS) != set(impl):
        fail(f"DOCS and the implementation differ: {sorted(set(DOCS) ^ set(impl))}")
    blob = f"{REPO}/blob/{commit}"

    source = (spec / "tools" / "facilitator.py").read_text()
    for ident, doc in DOCS.items():
        standing = doc.get("standing", RECORDED)
        found = examples.get(ident, [])
        if standing == RECORDED and not found:
            fail(f"{ident}: marked as recorded, and the record does not show it")
        if standing != RECORDED and found:
            fail(f"{ident}: marked as {standing}, and the record shows it")
        # The identifier is quoted once in the implementation's table. Any second
        # quotation is code that returns it.
        raised = source.count(f'"{ident}"') > 1
        if (standing == ABSENT) == raised:
            fail(f"{ident}: marked as {standing}, and a raise site {'exists' if raised else 'is missing'}")

    written = []
    for ident in sorted(impl):
        status, section = impl[ident]
        doc = DOCS[ident]
        standing = doc.get("standing", RECORDED)
        by_draft = ident in table9
        parts = [f"<p class=\"crumb\"><a href=\"./\">PACT problem types, revision -01</a></p>",
                 f"<h1>{ident}</h1>",
                 f"<p><code>{PREFIX}{ident}</code></p>",
                 "<dl>",
                 f"<dt>HTTP status</dt><dd>{status}</dd>",
                 "<dt>Defined by</dt><dd>" + (f"Revision -01 of the draft, in its "
                                              f"<a href=\"{DRAFT}#section-18.5\">Table 9</a>" if by_draft else
                                              "The reference implementation. Revision -01 does not list it.")
                 + "</dd>",
                 f"<dt>Section named</dt><dd>{section_link(section)} of revision -01</dd>",
                 "</dl>",
                 "<h2>When it is returned</h2>"]
        parts += [f"<p>{text}</p>" for text in doc["when"]]
        if doc.get("impl"):
            parts += ["<h2>In the reference implementation</h2>", f"<p>{doc['impl']}</p>"]
        parts.append("<h2>Recorded examples</h2>")
        found = examples.get(ident, [])
        if found:
            parts.append("<ul>")
            for x in found:
                detail = html.escape(x["detail"])
                if x["kind"] == "event":
                    parts.append(f"<li>Event {x['n']} of the <a href=\"{blob}/evidence/trace.json\">recorded "
                                 f"run</a>, status {x['status']}: &ldquo;{detail}&rdquo;</li>")
                else:
                    parts.append(f"<li>Test vector <a href=\"{blob}/{x['file']}\">{x['name']}</a>, status "
                                 f"{x['status']}: &ldquo;{detail}&rdquo;</li>")
            parts.append("</ul>")
        else:
            parts.append(f"<p>{STANDING_LINE[standing]}</p>")
        parts.append(footer(commit))
        (HERE / f"{ident}.html").write_text(page(f"{ident}: PACT problem type", "\n".join(parts)))
        written.append(f"{ident}.html")

    sample = examples["assurance-constraint-unsatisfied"][0]
    rows = []
    for ident in sorted(impl):
        status, section = impl[ident]
        standing = DOCS[ident].get("standing", RECORDED)
        cell = recorded_cell(examples.get(ident, [])) if standing == RECORDED else INDEX_STANDING[standing]
        rows.append(f"<tr><td><a href=\"{ident}\"><code>{ident}</code></a></td><td>{status}</td>"
                    f"<td>{section_link(section)}</td><td>{'Draft' if ident in table9 else 'Implementation'}</td>"
                    f"<td>{html.escape(cell)}</td></tr>")
    n_draft = len(table9)
    n_impl = len(impl) - n_draft
    words = {8: "eight", 22: "twenty-two"}
    body = "\n".join([
        "<h1>PACT problem types, revision -01</h1>",
        f"<p>Revision -01 of the Internet-Draft <a href=\"{DRAFT}\">draft-laxsharma-pact</a> reports failures as "
        "problem details in the format of <a href=\"https://www.rfc-editor.org/rfc/rfc9457\">RFC 9457</a>. Each "
        f"kind of failure has a type, and each type is identified by an address under <code>{PREFIX}</code>. "
        f"<a href=\"{DRAFT}#section-18.5\">Section 18.5</a> of that revision says documentation is kept at this "
        "prefix. These pages are that documentation.</p>",
        f"<p>The draft defines {words[n_draft]} types, in its Table 9. The reference implementation of revision "
        f"-01 uses {words[n_impl]} more for failures that table does not cover. Each page says which of the two "
        "it is, which section of the draft the response names, and where the recorded run of the implementation "
        "shows it.</p>",
        "<p>A program should treat a type as an identifier and compare it as a string. It does not need to "
        "fetch the address.</p>",
        "<h2>What a response looks like</h2>",
        f"<p>This is the body of event {sample['n']} of the recorded run, sent with status {sample['status']} "
        "and the media type <code>application/problem+json</code>. The <code>section</code> member carries the "
        "number of the section of revision -01 that states the rule.</p>",
        "<pre><code>" + html.escape(json.dumps(sample["body"], indent=2)) + "</code></pre>",
        "<h2>The types</h2>",
        "<div class=\"wrap\">\n<table>\n<thead><tr><th>Type</th><th>Status</th><th>Section</th>"
        "<th>Defined by</th><th>Recorded</th></tr></thead>\n<tbody>",
        "\n".join(rows),
        "</tbody>\n</table>\n</div>",
        "<p class=\"note\">Recorded: the exchange of the recorded run, or the test vector, that was answered "
        "with the type. Not recorded: the implementation returns the type, and neither shows it. Not returned: "
        "the implementation lists the type and no request is answered with it.</p>",
        footer(commit),
    ])
    (HERE / "index.html").write_text(page("PACT problem types, revision -01", body))
    (HERE / ".nojekyll").write_text("")
    written += ["index.html", ".nojekyll"]

    for name in written:
        text = (HERE / name).read_text()
        for bad in (chr(0x2014), chr(0x2013)):   # the two long dashes
            if bad in text:
                fail(f"{name}: a dash that the house style does not use")
    print(f"{len(impl)} type pages and the index written; spec commit {commit[:7]}, tag {TAG}; "
          f"{n_draft} types from the draft, {n_impl} from the implementation; "
          f"{sum(1 for d in DOCS.values() if d.get('standing', RECORDED) == RECORDED)} recorded")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        fail("usage: python3 build_pages.py PATH_TO_SPEC_CHECKOUT")
    build(pathlib.Path(sys.argv[1]).resolve())
