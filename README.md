# PDF Overlay Comparison Tool

Produces **Bluebeam-style overlay comparisons** between old and new drawing sets. Each matched sheet pair is rendered into a color-coded overlay PDF showing exactly what changed.

## Color Legend

| Color   | Meaning                        |
|---------|--------------------------------|
| **Blue**  | Lines only in the **new** drawing (added) |
| **Red**   | Lines only in the **old** drawing (removed / "dead") |
| **Black** | Lines in **both** drawings (unchanged)      |
| White   | Background (no ink in either)  |

## Auto-Alignment

Drawing revisions sometimes have small registration offsets (a few pixels) between the old and new sheets, caused by differences in print drivers, export settings, or page scaling. These offsets produce false-positive red/blue ghosting on lines that haven't actually changed.

The tool automatically detects and corrects these offsets using **phase correlation** (FFT-based). For each sheet pair it:

1. Binarizes both renderings and crops to the center region (avoiding borders and title blocks)
2. Computes the cross-power spectrum between the two images
3. Finds the peak in the inverse FFT to determine the translational shift
4. Applies the shift to the new image before compositing

Offsets larger than 30 px are ignored (indicating a genuinely different layout rather than a registration shift). Auto-alignment can be disabled with `--no-align`.

## Prerequisites

- Python 3.10+
- [PyMuPDF](https://pymupdf.readthedocs.io/) (PDF rendering)
- [Pillow](https://pillow.readthedocs.io/) (image compositing)
- NumPy
- [SciPy](https://scipy.org/) (image shifting for auto-alignment)

## Installation

```bash
pip install PyMuPDF Pillow numpy scipy
```

## Folder Prep

Split your drawing sets into individual single-sheet PDFs, organized into two folders:

```
old/                        # Previous revision
  A1.01 FLOOR PLAN.pdf
  M2.0 MECH PLAN.pdf
  ...

new/                        # Current revision
  A1.01 FLOOR PLAN.pdf
  M2.0 MECH PLAN.pdf
  P3.1 NEW PLUMBING.pdf    # New sheet (no match in old)
  ...
```

The old folder can contain nested subfolders (e.g. discipline-based organization) — the tool searches recursively.

## Usage

### Basic run

```bash
python overlay.py --old "Bulletin 1" --new "Addendum B"
```

Output goes into the `--new` folder by default:
- `Addendum B/Overlays/` — overlay comparison PDFs
- `Addendum B/NewSheet/` — copies of sheets only in the new set

### Preview matches first (dry run)

```bash
python overlay.py --old "Bulletin 1" --new "Addendum B" --dry-run
```

Shows what will be paired, what's new, and what's old-only — without writing any files.

### Higher resolution

```bash
python overlay.py --old "Bulletin 1" --new "Addendum B" --dpi 300
```

### Disable auto-alignment

```bash
python overlay.py --old "Bulletin 1" --new "Addendum B" --no-align
```

### Custom output location

```bash
python overlay.py --old "Bulletin 1" --new "Addendum B" --output-dir "Results"
```

Puts `Overlays/` and `NewSheet/` inside `Results/` instead of inside the `--new` folder.

## CLI Flags

| Flag            | Default          | Description                                      |
|-----------------|------------------|--------------------------------------------------|
| `--old`         | *(required)*     | Folder with old/previous drawing set (searched recursively) |
| `--new`         | *(required)*     | Folder with new/revised drawing set              |
| `--output-dir`  | inside `--new`   | Base output directory for `Overlays/` and `NewSheet/` |
| `--dpi`         | `150`            | Render resolution in dots per inch               |
| `--no-align`    | off              | Disable auto-alignment (skip offset detection)   |
| `--dry-run`     | off              | Show match plan without processing               |

## DPI Guidance

| DPI | Speed     | File Size | Use Case                          |
|-----|-----------|-----------|-----------------------------------|
| 150 | Fast      | Small     | Quick checks, most reviews        |
| 300 | ~4x slower | ~4x larger | Final deliverables, fine detail   |

150 DPI is sufficient for most overlay reviews. Use 300 DPI when you need to zoom in on fine text or thin linework.

## How Sheet Matching Works

The tool extracts a **sheet number** from each PDF filename and matches old-to-new by that number.

**Supported patterns:**

| Filename                        | Extracted Sheet Number |
|---------------------------------|------------------------|
| `A1.01 FLOOR PLAN.pdf`         | `A1.01`                |
| `P109.1 - SITE GAS PLAN.pdf`   | `P109.1`               |
| `M2.0 MECH PLAN.pdf`           | `M2.0`                 |
| `LT-0-020.1 LIGHTING PLAN.pdf` | `LT-0-020.1`          |

The regex matches a letter prefix, optional hyphen, digits, then any number of `.N` or `-N` suffixes. Filenames that don't match this pattern fall back to using the full filename stem.

Sheet numbers are compared case-insensitively. The description text after the number can differ between old and new — only the number matters for matching.

**Unmatched sheets:**
- Sheets only in **new** are copied to `NewSheet/` (likely new additions)
- Sheets only in **old** are reported but no action is taken (likely removed sheets)
