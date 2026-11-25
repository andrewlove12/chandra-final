"""Compare the OCR results of two runs (e.g. bf16 vs int8) of process_pdfs.py.

Reads the .ocr_cache folders inside two output folders. The console summary
contains ONLY numbers (no notebook text), so it is safe to paste anywhere.
The detailed side-by-side diff is written to a local HTML file - it contains
notebook text and must be treated as confidential.

Usage:
    python compare_runs.py OUTPUT_DIR_A OUTPUT_DIR_B [--report diff_report.html]
"""
import argparse
import difflib
import html
import json
import os
import re

from bs4 import BeautifulSoup

CACHE_DIR_NAME = ".ocr_cache"


def page_text(chunks):
    lines = []
    for c in chunks:
        soup = BeautifulSoup(c.get("content") or "", "html.parser")
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for tag in soup.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"]):
            tag.insert_after("\n")
        for cell in soup.find_all(["td", "th"]):
            cell.insert_after(" | ")
        text = soup.get_text()
        lines.extend(re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n"))
    return [ln for ln in lines if ln]


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def load_pages(output_dir):
    root = os.path.join(output_dir, CACHE_DIR_NAME)
    pages = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            if f.startswith("page_") and f.endswith(".json"):
                key = os.path.relpath(os.path.join(dirpath, f), root)
                with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                    pages[key] = json.load(fh)
    return pages


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_a")
    ap.add_argument("run_b")
    ap.add_argument("--report", default=None, help="HTML diff path (default: <run_b>/comparison_report.html)")
    args = ap.parse_args()

    a_pages, b_pages = load_pages(args.run_a), load_pages(args.run_b)
    common = sorted(set(a_pages) & set(b_pages))
    if not common:
        print("No pages in common between the two runs.")
        return
    a0, b0 = a_pages[common[0]], b_pages[common[0]]
    label_a = f"A ({a0.get('quantization', 'none')})"
    label_b = f"B ({b0.get('quantization', 'none')})"

    tot = dict(chars_a=0, chars_b=0, words_a=0, words_b=0, w_same=0, w_changed=0, w_ins=0, w_del=0,
               sec_a=0.0, sec_b=0.0, tok_a=0, tok_b=0, identical=0)
    sections = []

    # Console shows "PDF n / page m" rather than file names (names can be sensitive too).
    pdf_ids = {}
    for key in common:
        pdf_ids.setdefault(os.path.dirname(key), len(pdf_ids) + 1)

    def short(key):
        return f"PDF {pdf_ids[os.path.dirname(key)]} / {os.path.basename(key)[5:-5].lstrip('0')}"

    print(f"{'page':<16} {'char sim':>8} {'word sim':>8} {'words A':>7} {'words B':>7} "
          f"{'chg':>4} {'+B':>4} {'-B':>4} {'blocks A/B':>10} {'box IoU':>7} {'sec A':>6} {'sec B':>6}")
    for key in common:
        pa, pb = a_pages[key], b_pages[key]
        la, lb = page_text(pa["chunks"]), page_text(pb["chunks"])
        ta, tb = "\n".join(la), "\n".join(lb)
        wa, wb = ta.split(), tb.split()

        char_sim = difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio()
        sm = difflib.SequenceMatcher(None, wa, wb, autojunk=False)
        word_sim = sm.ratio()
        same = chg = ins = dele = 0
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                same += i2 - i1
            elif op == "replace":
                chg += max(i2 - i1, j2 - j1)
            elif op == "insert":
                ins += j2 - j1
            elif op == "delete":
                dele += i2 - i1

        # Layout agreement: best-IoU match for each block in A.
        boxes_b = [c["bbox"] for c in pb["chunks"]]
        ious = [max((iou(c["bbox"], bb) for bb in boxes_b), default=0.0) for c in pa["chunks"]]
        mean_iou = sum(ious) / len(ious) if ious else 1.0

        sa, sb = pa.get("seconds") or 0, pb.get("seconds") or 0
        print(f"{short(key):<16} {char_sim:8.2%} {word_sim:8.2%} {len(wa):7d} {len(wb):7d} {chg:4d} {ins:4d} {dele:4d} "
              f"{len(pa['chunks']):>4}/{len(pb['chunks']):<5} {mean_iou:7.2f} {sa:6.0f} {sb:6.0f}")

        tot["chars_a"] += len(ta); tot["chars_b"] += len(tb)
        tot["words_a"] += len(wa); tot["words_b"] += len(wb)
        tot["w_same"] += same; tot["w_changed"] += chg; tot["w_ins"] += ins; tot["w_del"] += dele
        tot["sec_a"] += sa; tot["sec_b"] += sb
        tot["tok_a"] += pa.get("token_count") or 0; tot["tok_b"] += pb.get("token_count") or 0
        tot["identical"] += ta == tb

        sections.append(f"<h2>{html.escape(key)} &mdash; char similarity {char_sim:.2%}, word similarity {word_sim:.2%}</h2>"
                        + difflib.HtmlDiff(wrapcolumn=80).make_table(la, lb, label_a, label_b, context=False))

    n = len(common)
    diff_words = tot["w_changed"] + tot["w_ins"] + tot["w_del"]
    print()
    print(f"Pages compared: {n}   identical text: {tot['identical']}/{n}")
    print(f"Words: A={tot['words_a']}  B={tot['words_b']}  matching={tot['w_same']}  "
          f"changed={tot['w_changed']}  only in B={tot['w_ins']}  only in A={tot['w_del']}")
    if tot["words_a"]:
        print(f"Word-level difference: {diff_words / tot['words_a']:.2%} of A's words")
    if tot["sec_a"] and tot["sec_b"]:
        print(f"OCR time: A={tot['sec_a']:.0f}s  B={tot['sec_b']:.0f}s  (B/A = {tot['sec_b'] / tot['sec_a']:.2f}x)")
        print(f"Tokens:   A={tot['tok_a']}  B={tot['tok_b']}")

    report = args.report or os.path.join(args.run_b, "comparison_report.html")
    with open(report, "w", encoding="utf-8") as f:
        f.write("<!doctype html><meta charset='utf-8'><title>OCR comparison</title>"
                "<style>body{font-family:sans-serif} table.diff{font-family:monospace;font-size:12px;border:1px solid #ccc}"
                ".diff_add{background:#cfc}.diff_chg{background:#ff8}.diff_sub{background:#fcc}</style>"
                f"<h1>OCR comparison: {html.escape(label_a)} vs {html.escape(label_b)}</h1>"
                "<p><b>Confidential: contains notebook text.</b> Yellow = changed, green = only in B, red = only in A.</p>"
                + "".join(sections))
    print(f"\nDetailed diff (contains notebook text, keep local): {report}")


if __name__ == "__main__":
    main()
