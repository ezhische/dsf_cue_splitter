# dsf_cue_split.py

Native DSD splitter for CUE sheets. Splits DSF files at block boundaries without PCM conversion, preserving the original DSD bitstream. Outputs tagged individual track files.

## Features

- **Native DSD splitting** — copies raw DSD data blocks, no transcoding or quality loss
- **Multi-FILE CUE support** — handles CUE sheets referencing multiple DSF files (e.g. vinyl rips with one file per side)
- **ID3v2.3 tagging** — embeds title, artist, album, track number, genre, and year from CUE metadata
- **Track-level PERFORMER** — respects per-track artist overrides in the CUE sheet
- **UTF-8 BOM handling** — reads CUE files with or without BOM
- **No dependencies** — pure Python 3, uses only the standard library

## Usage

```
python3 dsf_cue_split.py <cuefile>
```

The DSF files referenced in the CUE must be in the same directory as the CUE file. Output files are written to the same directory as `NN - Title.dsf`.

### Example

```
$ python3 dsf_cue_split.py "U.D.O. - Steelhammer.cue"

Album:  Steelhammer
Artist: U.D.O.
Tracks: 15

Reading: U.D.O. - Steelhammer S.1.dsf
Track 01: Steelhammer
  OK: 01 - Steelhammer.dsf (206.2s, blocks 0-17733)
Track 02: A Cry Of A Nation
  OK: 02 - A Cry Of A Nation.dsf (342.1s, blocks 17734-47152)
...

Done!
```

## Supported CUE layout

Both single-FILE and multi-FILE CUE sheets are supported:

```
REM GENRE Heavy Metal
REM DATE 2013
PERFORMER "U.D.O."
TITLE "Steelhammer"
FILE "Side1.dsf" WAVE
  TRACK 01 AUDIO
    TITLE "Steelhammer"
    INDEX 01 00:00:00
  TRACK 02 AUDIO
    TITLE "A Cry Of A Nation"
    INDEX 01 03:26:15
FILE "Side2.dsf" WAVE
  TRACK 03 AUDIO
    TITLE "Heavy Rain"
    INDEX 01 00:00:00
```

Timestamps reset to `00:00:00` at each new FILE directive, as expected.

## Embedded tags

Each output file receives an ID3v2.3 tag appended at the end of the file per the Sony DSF specification. The following frames are written:

| Frame | Content | Source |
|-------|---------|--------|
| TIT2 | Track title | TITLE under TRACK |
| TPE1 | Artist | Track PERFORMER, falls back to album PERFORMER |
| TALB | Album title | Top-level TITLE |
| TRCK | Track number / total | TRACK number / total count |
| TCON | Genre | REM GENRE |
| TYER | Year | REM DATE |

Tags are recognized by foobar2000, JRiver, Roon, Mp3tag, Neutron, HQPlayer, and other players with DSF/ID3v2 support.

## How it works

The DSF format stores DSD audio in fixed-size blocks (4096 bytes per channel). The splitter:

1. Parses the CUE sheet, associating each track with its source DSF file
2. Reads the DSF header (DSD chunk → fmt chunk → data chunk) of each source file
3. Converts CUE timestamps (MM:SS:FF at 75 fps) to DSD sample offsets, then to block indices
4. Copies the raw data blocks for each track range into a new DSF file with valid headers
5. Appends an ID3v2.3 tag and updates the DSD chunk's metadata pointer and total file size

Split points are aligned to DSD block boundaries, which introduces a maximum timing offset of one block (~11.6 ms at DSD64, ~5.8 ms at DSD128). This is inherent to the format and the same tradeoff all native DSD splitters make.

## Supported formats

- **DSD64** (2.8 MHz)
- **DSD128** (5.6 MHz)
- **DSD256** (11.2 MHz)
- **DSD512** (22.6 MHz)
- Stereo and multichannel

## Requirements

- Python 3.6+
- No external packages

## License

Apache License 2.0.
