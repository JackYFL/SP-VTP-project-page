#!/usr/bin/env python3
"""Validate static assets, HTML, and published numbers without dependencies.

Usage: python scripts/validate_site.py --paper-root ../NeurlPS2026-SP-VTP
The optional paper root cross-checks the independent-window tables against TeX.
The bundled JSON always supplies the authoritative sequential-feedback values.
"""
from argparse import ArgumentParser
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


PAIR = re.compile(r"(\d+(?:\.\d+)?)\s*±\s*(\d+(?:\.\d+)?)")
VOID = set("area base br col embed hr img input link meta param source track wbr".split())


class SiteParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.ids, self.links, self.tables = [], set(), [], {}
        self.table, self.row, self.cell = None, None, None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if "id" in attrs:
            assert attrs["id"] not in self.ids, f"Duplicate id: {attrs['id']}"
            self.ids.add(attrs["id"])
        for name in ("href", "src"):
            if name in attrs:
                self.links.append(attrs[name])
        if tag == "img":
            assert attrs.get("alt"), f"Missing alt text: {attrs.get('src')}"
        if tag not in VOID:
            self.stack.append(tag)
        if tag == "table":
            self.table = attrs["id"]
            self.tables[self.table] = []
        elif tag == "tr" and self.table:
            self.row = []
        elif tag in ("th", "td") and self.row is not None:
            self.cell = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.table:
            if any(PAIR.search(c) for c in self.row):
                self.tables[self.table].append(self.row)
            self.row = None
        elif tag == "table":
            self.table = None
        if tag not in VOID:
            assert self.stack and self.stack.pop() == tag, f"Mismatched HTML closing tag: {tag}"

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def pairs(cells):
    return [(float(mean), float(std)) for cell in cells for mean, std in PAIR.findall(cell)]


def expect(table, label, expected):
    cells = next(row for row in table if row[0] == label)
    actual = pairs(cells[1:])
    rounded = [(round(mean, 2), round(std, 2)) for mean, std in expected]
    assert actual == rounded, f"Numbers differ for {label}: {actual} != {rounded}"


def metric(row, key):
    return row[f"{key}_mean"], row[f"{key}_std"]


def tex_rows(path):
    pattern = re.compile(r"(\d+\.\d+)(?:\\!)*\\pm(?:\\!)*(\d+\.\d+)")
    result = []
    for chunk in path.read_text().split("\\\\"):
        values = [(float(a), float(b)) for a, b in pattern.findall(chunk)]
        if values:
            label = chunk.split("&")[0].strip().splitlines()[-1].strip()
            result.append((label, values))
    return result


def check_paper(tables, paper):
    prompt_rows = dict(tex_rows(paper / "tabs/baseline_results.tex"))
    for web, tex in (("No Prompt", "No prompt"), ("Target Only", "Target only"),
                     ("Visual Prompt", "Visual"), ("BBox Prompt", "BBox"),
                     ("BBox+Visual", "BBox + visual")):
        expect(tables["prompting-results"], web,
               [prompt_rows[tex][i] for i in (0, 1, 4, 5, 6)])
    external = tex_rows(paper / "tabs/external_baselines.tex")
    for web, needle in (("SmolVLA", "SmolVLA"), ("OpenVLA-OFT", "OpenVLA-OFT"),
                        ("GR00T N1.6", "GR00T N1.6"), ("SPOT", "SPOT"),
                        ("π0.5*", "pmlr-v305-black25a")):
        overall = next(values for label, values in external if needle in label and len(values) == 7)
        scene = next(values for label, values in external if needle in label and len(values) == 9)
        expect(tables["vla-results"], web, [overall[i] for i in (0, 1, 2, 6)])
        expect(tables["scene-results"], web, scene)
    print("PASS: prompting, external-VLA, and three-scene tables match the manuscript.")


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument("--paper-root", type=Path)
    args = cli.parse_args()
    root = Path(__file__).resolve().parents[1]
    html = (root / "index.html").read_text()
    parsed = SiteParser()
    parsed.feed(html)
    assert not parsed.stack, f"Unclosed HTML elements: {parsed.stack}"
    for link in parsed.links:
        target = urlsplit(link)
        if target.scheme or target.netloc:
            continue
        if not target.path:
            assert not target.fragment or target.fragment in parsed.ids, f"Missing anchor: {link}"
            continue
        asset = (root / unquote(target.path)).resolve()
        assert asset.is_relative_to(root), f"Out-of-site path: {link}"
        assert asset.is_file(), f"Missing local asset: {link}"
    for stale in ("We evaluate scene-level generalization", "strict scene-level validation splits",
                  "sensitive to cross-scene generalization", "scene-level splits</span>"):
        assert stale not in html, f"Stale protocol claim: {stale}"
    text = " ".join(html.split())
    assert "not a language-only baseline" in text
    assert "without GT resets" in text and "not robot task success" in text
    assert "Accepted to WACV 2027" in text
    assert "@inproceedings{li2027spvtp," in text
    assert "booktitle={Winter Conference on Applications of Computer Vision (WACV)}" in text
    assert "year={2027}" in text and "@misc{li2026spvtp" not in text
    assert "https://www.egr.msu.edu/~xbtan/" in parsed.links
    assert "Tan, Xiaobo" in text
    assert "https://github.com/JackYFL/SP-VTP" in parsed.links
    assert "<span>Code</span>" in html and "private" not in text.lower()
    assert "manuscript source" not in text.lower()
    assert not any("github.com/JackYFL/NeurlPS2026-SP-VTP" in link for link in parsed.links)
    for table in ("vla-results", "scene-results"):
        assert [row[0] for row in parsed.tables[table]][-2:] == ["π0.5*", "SPOT"]
    print(f"PASS: balanced HTML, unique anchors, alt text, and {len(parsed.links)} asset/link references.")
    print("PASS: WACV 2027 acceptance label, conference citation, and Xiaobo Tan author link.")
    print("PASS: Code label, no private/source-link text, and π0.5 before SPOT in both tables.")

    summary = json.loads((root / "static/results/rollout_summary.json").read_text())
    assert summary["complete"] and summary["seeds"] == [0, 1, 42]
    audit = summary["audit"]
    assert audit["evaluated_cases"] == 36 and audit["paired_chunks_per_case"] == 986
    assert audit["max_first_chunk_metric_difference"] == 0
    assert all(audit[key] for key in ("paired_query_noise_identity_passed",
                                      "tail_coverage_passed", "no_gt_origin_reset_in_cumulative_passed"))
    rows = summary["aggregate"]

    def select(condition, mode="cumulative", scope="all"):
        return next(r for r in rows if r["condition"] == condition
                    and r["mode"] == mode and r["scope"] == scope)

    for label, mode in (("Recorded / recorded", "gt_history"),
                        ("Predicted / recorded", "history_feedback"),
                        ("Predicted / predicted", "cumulative")):
        expect(parsed.tables["rollout-results"], label,
               [metric(select(c, mode), "episode_fde_cm") for c in ("bbox_visual", "no_prompt")])
    conditions = (("BBox+Visual", "bbox_visual"), ("No Prompt", "no_prompt"),
                  ("Shuffled Object", "shuffled_object"), ("Shuffled Target", "shuffled_target"))
    for label, condition in conditions:
        row = select(condition)
        expect(parsed.tables["rollout-interventions"], label,
               [metric(row, key) for key in ("ade_cm", "episode_fde_cm",
                                            "endpoint_s10_percent", "endpoint_combined10_percent")])
        phase_pairs = []
        for phase in ("approach", "pre_grasp", "grasp_occlusion", "post_grasp_transport", "placement"):
            phase_row = next(r for r in summary["phase_aggregate"] if r["scope"] == "all"
                             and r["condition"] == condition and r["mode"] == "cumulative"
                             and r["phase"] == phase)
            phase_pairs.append(metric(phase_row, "ade_cm"))
        expect(parsed.tables["rollout-phases"], label, phase_pairs)
    for scene in (1, 2, 3):
        expect(parsed.tables["rollout-scenes"], f"Scene {scene}",
               [metric(select(c, scope=f"scene{scene}"), "episode_fde_cm")
                for c in ("bbox_visual", "no_prompt")])
    print("PASS: feedback, prompt intervention, phase, and per-scene tables match audited raw summaries.")
    if args.paper_root:
        check_paper(parsed.tables, args.paper_root)
    print(f"Validated {sum(len(rows) for rows in parsed.tables.values())} result rows across {len(parsed.tables)} tables.")


if __name__ == "__main__":
    main()
