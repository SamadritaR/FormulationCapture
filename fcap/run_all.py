"""One command for the whole study.

    python -m fcap.run_all

Extracts every PDF or text file in papers/ (cached, so reruns are free),
then writes the gap audit, and the accuracy report if ground_truth/ has
labels. Results land in out/ and reports/.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import audit, evaluate
from .pipeline import run


def main() -> None:
    papers = sorted(p for p in Path("papers").glob("*") if p.suffix.lower() in (".pdf", ".txt"))
    if not papers:
        print("No papers found. Download the PDFs listed in data/papers.csv into papers/, "
              "named by paper_id (for example papers/li2021_posaconazole.pdf).")
        sys.exit(1)
    uncached = [p for p in papers if not Path(f"out/raw/{p.stem}.json").exists()]
    if uncached and not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"{len(uncached)} papers need extraction. Set ANTHROPIC_API_KEY first.")
        sys.exit(1)

    Path("out").mkdir(exist_ok=True)
    failed = []
    for p in papers:
        try:
            result = run(p, p.stem, raw_cache=f"out/raw/{p.stem}.json")
        except Exception as e:  # keep going; report every failure at the end
            failed.append((p.name, str(e)))
            print(f"FAILED {p.name}: {e}")
            continue
        Path(f"out/{p.stem}.json").write_text(json.dumps(result, indent=2))
        recs = result["records"]
        flags = sum(1 for r in recs for f in r["fields"].values() if f["status"] == "flagged")
        print(f"{p.stem}: {len(recs)} formulations, {flags} flagged fields")

    print("\n" + "=" * 60)
    audit.main()
    if any(g.stem != "TEMPLATE" for g in Path("ground_truth").glob("*.json")):
        print("\n" + "=" * 60)
        evaluate.main()
    if failed:
        print(f"\n{len(failed)} papers failed: " + ", ".join(n for n, _ in failed))
    print("\nNext: streamlit run app.py, open 'Saved extraction', and resolve the flags.")


if __name__ == "__main__":
    main()
