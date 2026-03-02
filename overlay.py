"""
PDF Overlay Comparison Tool

Produces Bluebeam-style overlay comparisons between old and new drawing sets.
New/added lines appear in BLUE, removed lines appear in RED, unchanged lines
appear in BLACK. Automatically detects and corrects small registration offsets
between revisions using phase correlation.

Usage:
    python overlay.py --old "Rev 1" --new "Rev 2"
    python overlay.py --old "Rev 1" --new "Rev 2" --dpi 300
    python overlay.py --old "Rev 1" --new "Rev 2" --no-align
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
from numpy.fft import fft2, ifft2
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import shift as ndi_shift


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


def pad_to_match(old_gray: np.ndarray, new_gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pad the smaller image with white to match the larger image dimensions."""
    if old_gray.shape == new_gray.shape:
        return old_gray, new_gray

    max_h = max(old_gray.shape[0], new_gray.shape[0])
    max_w = max(old_gray.shape[1], new_gray.shape[1])

    def pad(arr, h, w):
        if arr.shape[0] == h and arr.shape[1] == w:
            return arr
        padded = np.full((h, w), 255, dtype=arr.dtype)
        padded[:arr.shape[0], :arr.shape[1]] = arr
        return padded

    return pad(old_gray, max_h, max_w), pad(new_gray, max_h, max_w)


# ---------------------------------------------------------------------------
# Auto-alignment via phase correlation
# ---------------------------------------------------------------------------

MAX_OFFSET_PX = 30  # Ignore offsets larger than this (indicates different layout)


def detect_offset(old_gray: np.ndarray, new_gray: np.ndarray) -> tuple[int, int]:
    """Detect sub-drawing registration offset using phase correlation.

    Compares the center region of both renderings (avoiding borders and title
    blocks) to find a translational shift. Returns (dx, dy) in pixels that
    should be applied to the new image to align it with the old.

    Returns (0, 0) if no significant offset is detected or if the offset
    exceeds MAX_OFFSET_PX (which would indicate a genuinely different layout
    rather than a registration shift).
    """
    old_bin = (old_gray < 180).astype(np.float32)
    new_bin = (new_gray < 180).astype(np.float32)

    # Use center region to avoid border/title block noise
    margin_y = old_bin.shape[0] // 6
    margin_x = old_bin.shape[1] // 6
    crop_old = old_bin[margin_y:-margin_y, margin_x:-margin_x]
    crop_new = new_bin[margin_y:-margin_y, margin_x:-margin_x]

    f_old = fft2(crop_old)
    f_new = fft2(crop_new)

    cross = (f_old * np.conj(f_new)) / (np.abs(f_old * np.conj(f_new)) + 1e-10)
    correlation = np.real(ifft2(cross))

    peak = np.unravel_index(np.argmax(correlation), correlation.shape)
    dy, dx = int(peak[0]), int(peak[1])

    # Handle wrap-around
    if dy > correlation.shape[0] // 2:
        dy -= correlation.shape[0]
    if dx > correlation.shape[1] // 2:
        dx -= correlation.shape[1]

    # Ignore large offsets
    if abs(dx) > MAX_OFFSET_PX or abs(dy) > MAX_OFFSET_PX:
        return 0, 0

    return dx, dy


def apply_offset(gray: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Shift a grayscale image by (dx, dy) pixels, filling edges with white."""
    if dx == 0 and dy == 0:
        return gray
    return ndi_shift(gray.astype(np.float32), (dy, dx), cval=255.0, order=1).astype(np.uint8)


# ---------------------------------------------------------------------------
# Overlay creation
# ---------------------------------------------------------------------------

def create_overlay(old_gray: np.ndarray, new_gray: np.ndarray) -> Image.Image:
    """Create a blue/red/black overlay from two grayscale page renders.

    Color mapping (darken blend):
        R = new   (new ink removes red   -> lines from new are NOT red)
        G = min(old, new)  (any ink removes green)
        B = old   (old ink removes blue  -> lines from old are NOT blue)

    Result:
        Both white  -> white
        Old ink only -> (255, 0, 0) -> RED   (removed / "dead")
        New ink only -> (0, 0, 255) -> BLUE  (added / new)
        Both ink    -> (0, 0, 0)    -> BLACK (unchanged)
    """
    old_gray, new_gray = pad_to_match(old_gray, new_gray)

    result = np.stack([
        new_gray,                        # R channel (new ink darkens R)
        np.minimum(old_gray, new_gray),  # G channel (any ink darkens G)
        old_gray,                        # B channel (old ink darkens B)
    ], axis=-1)

    return Image.fromarray(result, 'RGB')


def draw_legend(img: Image.Image, sheet_num: str) -> None:
    """Draw a color legend box in the top-left corner of the overlay."""
    draw = ImageDraw.Draw(img)
    font_size = max(20, img.height // 80)
    small_size = max(16, img.height // 100)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
        small_font = ImageFont.truetype("arial.ttf", small_size)
        bold_font = ImageFont.truetype("arialbd.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
        small_font = font
        bold_font = font

    pad = 12
    line_h = font_size + 6
    box_w = font_size * 18
    box_h = line_h * 5 + pad * 2
    x0, y0 = 20, 20

    draw.rectangle([x0, y0, x0 + box_w, y0 + box_h],
                   fill=(255, 255, 255), outline=(0, 0, 0), width=2)

    cy = y0 + pad
    draw.text((x0 + pad, cy), f"OVERLAY - {sheet_num}", fill=(0, 0, 0), font=bold_font)
    cy += line_h + 4
    draw.line([(x0 + pad, cy), (x0 + box_w - pad, cy)], fill=(0, 0, 0), width=1)
    cy += 8

    sw = font_size * 2
    sh = font_size - 2

    draw.rectangle([x0 + pad, cy, x0 + pad + sw, cy + sh], fill=(0, 0, 255))
    draw.text((x0 + pad + sw + 10, cy), "NEW (added)", fill=(0, 0, 0), font=small_font)
    cy += line_h

    draw.rectangle([x0 + pad, cy, x0 + pad + sw, cy + sh], fill=(255, 0, 0))
    draw.text((x0 + pad + sw + 10, cy), "REMOVED (dead)", fill=(0, 0, 0), font=small_font)
    cy += line_h

    draw.rectangle([x0 + pad, cy, x0 + pad + sw, cy + sh], fill=(0, 0, 0))
    draw.text((x0 + pad + sw + 10, cy), "UNCHANGED", fill=(0, 0, 0), font=small_font)


def process_pair(old_path: Path, new_path: Path, output_path: Path,
                 sheet_num: str, dpi: int, align: bool) -> tuple[int, int]:
    """Process a single pair of PDFs into an overlay comparison.

    Returns the (dx, dy) offset that was corrected, or (0, 0) if none.
    """
    old_doc = fitz.open(str(old_path))
    new_doc = fitz.open(str(new_path))

    max_pages = max(len(old_doc), len(new_doc))
    overlay_pages = []
    offset = (0, 0)

    for i in range(max_pages):
        if i < len(old_doc):
            old_gray = render_page_grayscale(old_doc[i], dpi)
        else:
            new_gray_temp = render_page_grayscale(new_doc[i], dpi)
            old_gray = np.full_like(new_gray_temp, 255)

        if i < len(new_doc):
            new_gray = render_page_grayscale(new_doc[i], dpi)
        else:
            new_gray = np.full_like(old_gray, 255)

        old_gray, new_gray = pad_to_match(old_gray, new_gray)

        # Auto-align on the first page
        if align and i == 0:
            dx, dy = detect_offset(old_gray, new_gray)
            offset = (dx, dy)

        if align and offset != (0, 0):
            new_gray = apply_offset(new_gray, *offset)

        overlay_img = create_overlay(old_gray, new_gray)
        draw_legend(overlay_img, sheet_num)
        overlay_pages.append(overlay_img)

    old_doc.close()
    new_doc.close()

    if len(overlay_pages) == 1:
        overlay_pages[0].save(str(output_path), 'PDF', resolution=dpi)
    else:
        overlay_pages[0].save(
            str(output_path), 'PDF', resolution=dpi, save_all=True,
            append_images=overlay_pages[1:]
        )

    return offset


def format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as a human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m {secs:.0f}s"


def main():
    parser = argparse.ArgumentParser(
        description='PDF Overlay Comparison Tool — Blue=NEW, Red=REMOVED, Black=UNCHANGED'
    )
    parser.add_argument('--old', required=True,
                        help='Folder with old/previous drawing set (searched recursively)')
    parser.add_argument('--new', required=True,
                        help='Folder with new/revised drawing set')
    parser.add_argument('--output-dir', default=None,
                        help='Base output directory (default: inside --new folder)')
    parser.add_argument('--dpi', type=int, default=150,
                        help='Render resolution (default: 150, use 300 for sharper output)')
    parser.add_argument('--no-align', action='store_true',
                        help='Disable auto-alignment (skip offset detection)')
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

    base_dir = Path(args.output_dir) if args.output_dir else new_dir
    overlay_dir = base_dir / "Overlays"
    newsheet_dir = base_dir / "NewSheet"

    print(f"Old drawings: {old_dir}")
    print(f"New drawings: {new_dir}")
    print(f"Overlays:     {overlay_dir}")
    print(f"New sheets:   {newsheet_dir}")
    print(f"DPI:          {args.dpi}")
    print(f"Auto-align:   {'off' if args.no_align else 'on'}")
    print()

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

    overlay_dir.mkdir(parents=True, exist_ok=True)
    if new_only:
        newsheet_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    total_tasks = len(matched) + len(new_only)
    current = 0
    aligned_count = 0

    for old_path, new_path, sheet_num in matched:
        current += 1
        output_path = overlay_dir / f"{sheet_num} OVERLAY.pdf"
        offset = process_pair(old_path, new_path, output_path, sheet_num,
                              args.dpi, align=not args.no_align)
        if offset != (0, 0):
            aligned_count += 1
            print(f"[{current}/{total_tasks}] {sheet_num}  (aligned: dx={offset[0]}, dy={offset[1]})")
        else:
            print(f"[{current}/{total_tasks}] {sheet_num}")

    for sheet_num, src_path in new_only.items():
        current += 1
        dst_path = newsheet_dir / src_path.name
        print(f"[{current}/{total_tasks}] {sheet_num} (new sheet)")
        shutil.copy2(src_path, dst_path)

    elapsed = time.time() - start_time
    print()
    print(f"Done! {len(matched)} overlay(s) + {len(new_only)} new sheet(s) "
          f"in {format_elapsed(elapsed)}")
    if aligned_count:
        print(f"  Auto-aligned: {aligned_count} sheet(s) had offset correction applied")
    print(f"  Overlays:   {overlay_dir}")
    if new_only:
        print(f"  New sheets: {newsheet_dir}")


if __name__ == '__main__':
    main()
