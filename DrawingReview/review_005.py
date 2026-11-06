"""005 training exercise: derive a depth correction from the reference PDF."""
import argparse
from pathlib import Path
from review_depth import review_depth

def review(pdf, source, output, approve, report=print):
    return review_depth(pdf, source, output, approve, report,
                        pdf_prefix="C/BØ11 HOLE DP", dxf_prefix="C/B %%c11 HOLE DP",
                        label_prefix="11 HOLE DP", dim_handle="33E", label_handle="34C")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="005번 카운터보어 깊이 표기 비교·수정")
    for name in ("pdf", "source", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    review(args.pdf, args.source, args.output,
           lambda: input("승인하려면 APPLY를 입력하세요: ").strip().upper() == "APPLY")
