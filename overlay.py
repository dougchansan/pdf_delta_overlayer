"""
PDF Overlay Comparison Tool

Produces Bluebeam-style overlay comparisons between old and new drawing sets.
Old drawings appear in BLUE, new drawings appear in RED, unchanged lines appear BLACK.

Usage:
    python overlay.py --old "Rev 1" --new "Rev 2"
    python overlay.py --old "Rev 1" --new "Rev 2" --dpi 300
    python overlay.py --old "Rev 1" --new "Rev 2" --dry-run
"""

import argparse
import re
import shutil
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image


def extract_sheet_number(filename: str) -> str:
    """Extract the sheet number from a drawing filename.

    Handles patterns like:
        P109.1 SITE GAS PLAN.pdf       -> P109.1
        P109.1 - SITE GAS PLAN.pdf     -> P109.1
        M2.0 - MECH PLAN.pdf           -> M2.0
        A1.01 FLOOR PLAN.pdf           -> A1.01
        LT-0-020.1 LIGHTING PLAN.pdf   -> LT-0-020.1
    """
    stem = Path(filename).stem
    match = re.match(r'^([A-Za-z]+-?[\d]+(?:[.-]\d+)*)', stem)
    if match:
        return match.group(1).upper()
    # Fallback: normalize by removing dashes and extra spaces, then use full stem
    return re.sub(r'[\s\-_]+', ' ', stem).strip().upper()


def match_files(old_dir: Path, new_dir: Path) -> tuple[list[tuple[Path, Path, str]], dict[str, Path], dict[str, Path]]:
    """Match PDF files between old and new directories by sheet number.

    Searches recursively in both directories to handle nested subfolders.

    Returns:
        matched: list of (old_path, new_path, sheet_number) tuples
        old_only: dict of sheet_number -> path for unmatched old sheets
        new_only: dict of sheet_number -> path for unmatched new sheets
    """
    old_pdfs = {extract_sheet_number(f.name): f for f in old_dir.rglob('*.pdf')}
    new_pdfs = {extract_sheet_number(f.name): f for f in new_dir.rglob('*.pdf')}

    matched = []
    for sheet_num in sorted(set(old_pdfs) & set(new_pdfs)):
        matched.append((old_pdfs[sheet_num], new_pdfs[sheet_num], sheet_num))

    old_only = {k: old_pdfs[k] for k in sorted(set(old_pdfs) - set(new_pdfs))}
    new_only = {k: new_pdfs[k] for k in sorted(set(new_pdfs) - set(old_pdfs))}

    return matched, old_only, new_only


def render_page_grayscale(page: fitz.Page, dpi: int) -> np.ndarray:
    """Render a PDF page as a grayscale numpy array."""
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    return img


def create_overlay(old_gray: np.ndarray, new_gray: np.ndarray) -> Image.Image:
    """Create a blue/red/black overlay from two grayscale page renders.

    Old drawing tinted BLUE:  R=old, G=old, B=255
    New drawing tinted RED:   R=255, G=new, B=new
    Darken blend (per-channel min):
        R = old   (old ink removes red   -> lines from old are NOT red)
        G = min(old, new)  (any ink removes green)
        B = new   (new ink removes blue  -> lines from new are NOT blue)

    Result:
        Both white  -> white
        Old ink only -> (0, 0, 255) -> BLUE
        New ink only -> (255, 0, 0) -> RED
        Both ink    -> (0, 0, 0)    -> BLACK
    """
    # Handle size mismatch by padding the smaller image to match the larger
    if old_gray.shape != new_gray.shape:
        max_h = max(old_gray.shape[0], new_gray.shape[0])
        max_w = max(old_gray.shape[1], new_gray.shape[1])

        def pad_to(arr, h, w):
            if arr.shape[0] == h and arr.shape[1] == w:
                return arr
            padded = np.full((h, w), 255, dtype=np.uint8)
            padded[:arr.shape[0], :arr.shape[1]] = arr
            return padded

        old_gray = pad_to(old_gray, max_h, max_w)
        new_gray = pad_to(new_gray, max_h, max_w)

    result = np.stack([
        old_gray,                        # R channel
        np.minimum(old_gray, new_gray),  # G channel
        new_gray,                        # B channel
    ], axis=-1)

    return Image.fromarray(result, 'RGB')


def process_pair(old_path: Path, new_path: Path, output_path: Path, dpi: int):
    """Process a single pair of PDFs into an overlay comparison."""
    old_doc = fitz.open(str(old_path))
    new_doc = fitz.open(str(new_path))

    max_pages = max(len(old_doc), len(new_doc))
    overlay_pages = []

    for i in range(max_pages):
        # Render or create blank for missing pages
        if i < len(old_doc):
            old_gray = render_page_grayscale(old_doc[i], dpi)
        else:
            # New doc has extra pages — old is blank white
            new_gray_temp = render_page_grayscale(new_doc[i], dpi)
            old_gray = np.full_like(new_gray_temp, 255)

        if i < len(new_doc):
            new_gray = render_page_grayscale(new_doc[i], dpi)
        else:
            # Old doc has extra pages — new is blank white
            new_gray = np.full_like(old_gray, 255)

        overlay_img = create_overlay(old_gray, new_gray)
        overlay_pages.append(overlay_img)

    old_doc.close()
    new_doc.close()

    # Save all pages as a single PDF
    if len(overlay_pages) == 1:
        overlay_pages[0].save(str(output_path), 'PDF', resolution=dpi)
    else:
        overlay_pages[0].save(
            str(output_path), 'PDF', resolution=dpi, save_all=True,
            append_images=overlay_pages[1:]
        )


def format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as a human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"


def main():
    parser = argparse.ArgumentParser(
        description='PDF Overlay Comparison Tool — Old=BLUE, New=RED, Unchanged=BLACK'
    )
    parser.add_argument('--old', required=True,
                        help='Folder with old/previous drawing set (searched recursively)')
    parser.add_argument('--new', required=True,
                        help='Folder with new/revised drawing set')
    parser.add_argument('--output-dir', default=None,
                        help='Base output directory (default: inside --new folder)')
    parser.add_argument('--dpi', type=int, default=150,
                        help='Render resolution (default: 150, use 300 for sharper output)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show match plan without processing any files')
    args = parser.parse_args()

    old_dir = Path(args.old)
    new_dir = Path(args.new)

    if not old_dir.is_dir():
        print(f"Error: Old directory not found: {old_dir}")
        sys.exit(1)
    if not new_dir.is_dir():
        print(f"Error: New directory not found: {new_dir}")
        sys.exit(1)

    # Output directories default to inside --new folder
    base_dir = Path(args.output_dir) if args.output_dir else new_dir
    overlay_dir = base_dir / "Overlays"
    newsheet_dir = base_dir / "NewSheet"

    print(f"Old drawings: {old_dir}")
    print(f"New drawings: {new_dir}")
    print(f"Overlays:     {overlay_dir}")
    print(f"New sheets:   {newsheet_dir}")
    print(f"DPI:          {args.dpi}")
    print()

    # Match files between folders
    print("Matching sheets...")
    matched, old_only, new_only = match_files(old_dir, new_dir)

    print(f"  Matched pairs:    {len(matched)}")
    if old_only:
        print(f"  Old only (removed): {len(old_only)} — {', '.join(old_only)}")
    if new_only:
        print(f"  New only (added):   {len(new_only)} — {', '.join(new_only)}")
    print()

    if not matched and not new_only:
        print("No matching sheets found and no new sheets to copy.")
        sys.exit(1)

    # Dry-run: show plan and exit
    if args.dry_run:
        print("=== DRY RUN — no files will be written ===")
        print()
        if matched:
            print("OVERLAYS:")
            for old_path, new_path, sheet_num in matched:
                print(f"  {sheet_num}")
                print(f"    Old: {old_path.name}")
                print(f"    New: {new_path.name}")
                print(f"    Out: {overlay_dir / f'{sheet_num} OVERLAY.pdf'}")
        if new_only:
            print()
            print("NEW SHEETS (will be copied):")
            for sheet_num, path in new_only.items():
                print(f"  {sheet_num} — {path.name}")
                print(f"    -> {newsheet_dir / path.name}")
        if old_only:
            print()
            print("OLD ONLY (no action):")
            for sheet_num, path in old_only.items():
                print(f"  {sheet_num} — {path.name}")
        print()
        total = len(matched) + len(new_only)
        print(f"Total: {len(matched)} overlay(s) + {len(new_only)} new sheet(s) = {total} output file(s)")
        return

    # Create output directories
    overlay_dir.mkdir(parents=True, exist_ok=True)
    if new_only:
        newsheet_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    total_tasks = len(matched) + len(new_only)
    current = 0

    # Process matched pairs
    for old_path, new_path, sheet_num in matched:
        current += 1
        output_path = overlay_dir / f"{sheet_num} OVERLAY.pdf"
        print(f"[{current}/{total_tasks}] {sheet_num}")
        print(f"  Old: {old_path.name}")
        print(f"  New: {new_path.name}")
        process_pair(old_path, new_path, output_path, args.dpi)
        print(f"  -> {output_path}")

    # Copy new-only sheets
    for sheet_num, src_path in new_only.items():
        current += 1
        dst_path = newsheet_dir / src_path.name
        print(f"[{current}/{total_tasks}] {sheet_num} (new sheet)")
        shutil.copy2(src_path, dst_path)
        print(f"  Copied -> {dst_path}")

    elapsed = time.time() - start_time
    print()
    print(f"Done! {len(matched)} overlay(s) + {len(new_only)} new sheet(s) "
          f"in {format_elapsed(elapsed)}")
    print(f"  Overlays:   {overlay_dir}")
    if new_only:
        print(f"  New sheets: {newsheet_dir}")


if __name__ == '__main__':
    main()
