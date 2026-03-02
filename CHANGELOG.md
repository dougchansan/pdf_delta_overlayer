# Changelog

## [0.2.0] - 2026-03-02

### Added
- **Auto-alignment via phase correlation**: Automatically detects and corrects small registration offsets between old and new drawing revisions using FFT-based phase correlation. Eliminates false-positive red/blue ghosting caused by print driver or export setting differences.
- **`--no-align` flag**: Disables auto-alignment for cases where offset detection is not desired.
- **Color legend**: Each overlay sheet now includes a labeled color legend in the top-left corner showing Blue = NEW, Red = REMOVED, Black = UNCHANGED.
- New dependency: `scipy` (for `scipy.ndimage.shift`).

### Changed
- **Color scheme corrected**: Blue now means NEW/added, Red now means REMOVED/dead. Previously the colors were reversed.

## [0.1.0] - 2026-02-26

### Added
- Initial release.
- Bluebeam-style overlay comparison between old and new drawing sets.
- Sheet matching by extracted sheet number from filenames.
- Recursive search in old directory for nested discipline folders.
- `--dry-run` mode to preview matches without writing files.
- `--dpi` flag to control render resolution.
- `--output-dir` flag for custom output location.
- NewSheet copying for unmatched new sheets.
