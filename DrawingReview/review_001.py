"""001 training exercise: conservative depth comparison and correction."""
import argparse
from pathlib import Path
from review_depth import review_depth


def review(pdf, source, output, approve, report=print):
    return review_depth(
        pdf, source, output, approve, report,
        pdf_prefix="C/BØ9.5 HOLE DP", dxf_prefix="C/B %%c9.5 HOLE DP",
        label_prefix="9.5 HOLE DP", dim_handle="2E1", label_handle="2EC",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="001번 카운터보어 깊이 표기 비교·수정")
    for name in ("pdf", "source", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    review(args.pdf, args.source, args.output,
           lambda: input("승인하려면 APPLY를 입력하세요: ").strip().upper() == "APPLY")
