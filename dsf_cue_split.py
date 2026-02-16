#!/usr/bin/env python3
"""
dsf_cue_split.py — Native DSF splitter for CUE sheets (including multi-FILE).

Splits DSF files at DSD block boundaries without any PCM conversion.
Preserves original DSD bitstream data.

Usage: python3 dsf_cue_split.py <cuefile>

DSF files must be in the same directory as the CUE file.
Output files are written to the same directory as: "NN - Title.dsf"
"""

import struct
import sys
import os
import re


# ---------------------------------------------------------------------------
# DSF format reader
# ---------------------------------------------------------------------------

def read_dsf_header(filepath):
    """Read DSF file headers and return dict with format info."""
    with open(filepath, 'rb') as f:
        # --- DSD chunk (28 bytes) ---
        magic = f.read(4)
        if magic != b'DSD ':
            raise ValueError(f"{filepath}: not a DSF file (magic={magic!r})")
        total_file_size = struct.unpack('<Q', f.read(8))[0]
        metadata_offset = struct.unpack('<Q', f.read(8))[0]

        # --- fmt chunk (usually 52 bytes) ---
        fmt_magic = f.read(4)
        if fmt_magic != b'fmt ':
            raise ValueError(f"{filepath}: expected 'fmt ' chunk, got {fmt_magic!r}")
        fmt_chunk_size = struct.unpack('<Q', f.read(8))[0]
        fmt_version = struct.unpack('<I', f.read(4))[0]
        format_id = struct.unpack('<I', f.read(4))[0]
        channel_type = struct.unpack('<I', f.read(4))[0]
        channel_num = struct.unpack('<I', f.read(4))[0]
        sample_rate = struct.unpack('<I', f.read(4))[0]
        bits_per_sample = struct.unpack('<I', f.read(4))[0]
        sample_count = struct.unpack('<Q', f.read(8))[0]  # per channel
        block_size = struct.unpack('<I', f.read(4))[0]

        # --- data chunk header (12 bytes) ---
        data_magic = f.read(4)
        if data_magic != b'data':
            raise ValueError(f"{filepath}: expected 'data' chunk, got {data_magic!r}")
        data_chunk_size = struct.unpack('<Q', f.read(8))[0]
        data_offset = f.tell()  # where actual DSD data begins
        data_size = data_chunk_size - 12  # minus data chunk header

    return {
        'filepath': filepath,
        'total_file_size': total_file_size,
        'metadata_offset': metadata_offset,
        'fmt_chunk_size': fmt_chunk_size,
        'fmt_version': fmt_version,
        'format_id': format_id,
        'channel_type': channel_type,
        'channel_num': channel_num,
        'sample_rate': sample_rate,
        'bits_per_sample': bits_per_sample,
        'sample_count': sample_count,
        'block_size': block_size,
        'data_offset': data_offset,
        'data_size': data_size,
    }


# ---------------------------------------------------------------------------
# ID3v2.3 tag builder (for DSF metadata)
# ---------------------------------------------------------------------------

def _id3_syncsafe(n):
    """Encode integer as 4-byte ID3v2 syncsafe integer."""
    out = bytearray(4)
    for i in range(3, -1, -1):
        out[i] = n & 0x7F
        n >>= 7
    return bytes(out)


def _id3_text_frame(frame_id, text):
    """Build a single ID3v2.3 text frame."""
    if not text:
        return b''
    encoded = text.encode('utf-8')
    # frame: encoding byte (0x03 = UTF-8) + text bytes
    payload = b'\x03' + encoded
    # ID3v2.3 frame header: 4-byte ID + 4-byte size (big-endian) + 2-byte flags
    header = frame_id.encode('ascii')
    header += struct.pack('>I', len(payload))
    header += b'\x00\x00'  # no flags
    return header + payload


def build_id3v2_tag(title='', artist='', album='', track_num='',
                    total_tracks='', genre='', date=''):
    """
    Build a minimal ID3v2.3 tag blob.
    Returns bytes ready to append to DSF file.
    """
    frames = b''
    frames += _id3_text_frame('TIT2', title)
    frames += _id3_text_frame('TPE1', artist)
    frames += _id3_text_frame('TALB', album)
    if track_num:
        trck = f"{track_num}/{total_tracks}" if total_tracks else track_num
        frames += _id3_text_frame('TRCK', trck)
    frames += _id3_text_frame('TCON', genre)
    frames += _id3_text_frame('TYER', date)

    if not frames:
        return b''

    # ID3v2.3 header: "ID3" + version 2.3 + flags + syncsafe size
    header = b'ID3'
    header += b'\x03\x00'  # version 2.3.0
    header += b'\x00'      # no flags
    header += _id3_syncsafe(len(frames))

    return header + frames


def write_id3_to_dsf(dsf_path, tags):
    """
    Append ID3v2 tag to a DSF file and update DSD chunk pointers.
    tags: dict with keys title, artist, album, track_num, total_tracks,
          genre, date
    """
    tag_blob = build_id3v2_tag(**tags)
    if not tag_blob:
        return

    with open(dsf_path, 'r+b') as f:
        # Read current DSD chunk to get total file size
        f.seek(0)
        magic = f.read(4)
        assert magic == b'DSD ', f"Not a DSF: {magic!r}"
        dsd_chunk_size = struct.unpack('<Q', f.read(8))[0]
        old_total = struct.unpack('<Q', f.read(8))[0]

        # metadata_offset = current end of audio data (old total if no tag)
        metadata_offset = old_total
        new_total = old_total + len(tag_blob)

        # Update total file size in DSD chunk (offset 12)
        f.seek(12)
        f.write(struct.pack('<Q', new_total))

        # Update metadata pointer in DSD chunk (offset 20)
        f.seek(20)
        f.write(struct.pack('<Q', metadata_offset))

        # Append ID3 tag at end
        f.seek(0, 2)  # seek to EOF
        f.write(tag_blob)


def time_to_samples(minutes, seconds, frames, sample_rate):
    """Convert CUE time (MM:SS:FF, 75 frames/sec) to sample count."""
    total_seconds = minutes * 60 + seconds + frames / 75.0
    return int(total_seconds * sample_rate)


def samples_to_block_index(samples, block_size):
    """
    Convert sample offset to block index.
    Each block holds block_size * 8 samples (1-bit DSD).
    """
    samples_per_block = block_size * 8
    return samples // samples_per_block


def write_dsf_track(src_path, header, start_block, end_block, out_path):
    """
    Extract blocks [start_block, end_block) from source DSF
    and write a new valid DSF file.
    """
    block_size = header['block_size']
    channel_num = header['channel_num']
    sample_rate = header['sample_rate']
    frame_size = block_size * channel_num  # one interleaved frame

    num_blocks = end_block - start_block
    if num_blocks <= 0:
        print(f"  WARNING: zero-length track, skipping: {out_path}")
        return

    new_sample_count = num_blocks * block_size * 8  # per channel
    new_data_payload = num_blocks * frame_size
    new_data_chunk_size = 12 + new_data_payload  # data header + payload
    new_total_size = 28 + header['fmt_chunk_size'] + new_data_chunk_size
    # no metadata in split tracks

    with open(src_path, 'rb') as src, open(out_path, 'wb') as dst:
        # --- Write DSD chunk (28 bytes) ---
        dst.write(b'DSD ')
        dst.write(struct.pack('<Q', 28))
        dst.write(struct.pack('<Q', new_total_size))
        dst.write(struct.pack('<Q', 0))  # no metadata

        # --- Write fmt chunk (copy from source, update sample_count) ---
        dst.write(b'fmt ')
        dst.write(struct.pack('<Q', header['fmt_chunk_size']))
        dst.write(struct.pack('<I', header['fmt_version']))
        dst.write(struct.pack('<I', header['format_id']))
        dst.write(struct.pack('<I', header['channel_type']))
        dst.write(struct.pack('<I', channel_num))
        dst.write(struct.pack('<I', sample_rate))
        dst.write(struct.pack('<I', header['bits_per_sample']))
        dst.write(struct.pack('<Q', new_sample_count))
        dst.write(struct.pack('<I', block_size))
        dst.write(struct.pack('<I', 0))  # reserved

        # If fmt_chunk_size > 52, copy remaining fmt bytes from source
        if header['fmt_chunk_size'] > 52:
            src.seek(28 + 52)  # after DSD chunk + standard fmt
            extra = header['fmt_chunk_size'] - 52
            dst.write(src.read(extra))

        # --- Write data chunk ---
        dst.write(b'data')
        dst.write(struct.pack('<Q', new_data_chunk_size))

        # Seek to start block in source data
        src_data_start = header['data_offset'] + start_block * frame_size
        src.seek(src_data_start)

        # Copy blocks in chunks to avoid huge memory usage
        remaining = new_data_payload
        buf_size = frame_size * 64  # copy 64 frames at a time
        while remaining > 0:
            to_read = min(buf_size, remaining)
            data = src.read(to_read)
            if not data:
                # pad with silence (zero) if source is shorter
                dst.write(b'\x00' * remaining)
                break
            dst.write(data)
            remaining -= len(data)

    duration_sec = new_sample_count / sample_rate
    print(f"  OK: {os.path.basename(out_path)} "
          f"({duration_sec:.1f}s, blocks {start_block}-{end_block-1})")


# ---------------------------------------------------------------------------
# CUE parser (multi-FILE aware)
# ---------------------------------------------------------------------------

def parse_cue(cue_path):
    """
    Parse CUE sheet. Returns:
      album_meta: dict with TITLE, PERFORMER, GENRE, DATE
      tracks: list of dicts with keys:
        file, track_num, title, performer, index_m, index_s, index_f
    """
    with open(cue_path, 'r', encoding='utf-8-sig') as f:
        lines = f.readlines()

    album_meta = {}
    tracks = []
    current_file = None
    current_track = None

    for raw_line in lines:
        line = raw_line.strip().replace('\r', '')
        if not line:
            continue

        # FILE directive
        m = re.match(r'FILE\s+"(.+?)"\s+\w+', line)
        if m:
            current_file = m.group(1)
            continue

        # REM fields
        m = re.match(r'REM\s+GENRE\s+(.+)', line)
        if m:
            album_meta['GENRE'] = m.group(1).strip().strip('"')
            continue
        m = re.match(r'REM\s+DATE\s+(.+)', line)
        if m:
            album_meta['DATE'] = m.group(1).strip().strip('"')
            continue

        # TRACK
        m = re.match(r'TRACK\s+(\d+)\s+AUDIO', line)
        if m:
            current_track = {
                'file': current_file,
                'track_num': m.group(1),
                'title': f'Track {m.group(1)}',
                'performer': album_meta.get('PERFORMER', ''),
            }
            tracks.append(current_track)
            continue

        # TITLE (album-level if no current track, otherwise track-level)
        m = re.match(r'TITLE\s+"(.+?)"', line)
        if m:
            if current_track is not None:
                current_track['title'] = m.group(1)
            else:
                album_meta['TITLE'] = m.group(1)
            continue

        # PERFORMER (album-level if no current track, otherwise track-level)
        m = re.match(r'PERFORMER\s+"(.+?)"', line)
        if m:
            if current_track is not None:
                current_track['performer'] = m.group(1)
            else:
                album_meta['PERFORMER'] = m.group(1)
            continue

        # INDEX 01
        m = re.match(r'INDEX\s+01\s+(\d+):(\d+):(\d+)', line)
        if m and current_track is not None:
            current_track['index_m'] = int(m.group(1))
            current_track['index_s'] = int(m.group(2))
            current_track['index_f'] = int(m.group(3))
            current_track = None  # done with this track's essentials
            continue

    return album_meta, tracks


# ---------------------------------------------------------------------------
# Main splitter logic
# ---------------------------------------------------------------------------

def split_dsf_cue(cue_path):
    cue_dir = os.path.dirname(os.path.abspath(cue_path))
    album_meta, tracks = parse_cue(cue_path)

    if not tracks:
        print("ERROR: no tracks found in CUE file")
        sys.exit(1)

    print(f"Album:  {album_meta.get('TITLE', '?')}")
    print(f"Artist: {album_meta.get('PERFORMER', '?')}")
    print(f"Tracks: {len(tracks)}")
    print()

    # Cache DSF headers per source file
    dsf_headers = {}

    for i, track in enumerate(tracks):
        src_filename = track['file']
        if not src_filename:
            print(f"ERROR: track {track['track_num']} has no FILE reference")
            continue

        src_path = os.path.join(cue_dir, src_filename)

        if src_filename not in dsf_headers:
            if not os.path.isfile(src_path):
                print(f"ERROR: source file not found: {src_path}")
                sys.exit(1)
            print(f"Reading: {src_filename}")
            dsf_headers[src_filename] = read_dsf_header(src_path)

        hdr = dsf_headers[src_filename]
        sample_rate = hdr['sample_rate']
        block_size = hdr['block_size']
        channel_num = hdr['channel_num']
        frame_size = block_size * channel_num

        # Total blocks in source
        total_blocks = hdr['data_size'] // frame_size

        # Start block for this track
        start_samples = time_to_samples(
            track['index_m'], track['index_s'], track['index_f'], sample_rate)
        start_block = samples_to_block_index(start_samples, block_size)

        # End block: either start of next track (if same file) or end of file
        end_block = total_blocks
        if i + 1 < len(tracks) and tracks[i + 1]['file'] == src_filename:
            next_t = tracks[i + 1]
            end_samples = time_to_samples(
                next_t['index_m'], next_t['index_s'], next_t['index_f'],
                sample_rate)
            end_block = samples_to_block_index(end_samples, block_size)

        # Sanitize title for filename
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', track['title'])
        out_name = f"{track['track_num']} - {safe_title}.dsf"
        out_path = os.path.join(cue_dir, out_name)

        print(f"Track {track['track_num']}: {track['title']}")
        write_dsf_track(src_path, hdr, start_block, end_block, out_path)

        # Tag the output file
        write_id3_to_dsf(out_path, {
            'title': track['title'],
            'artist': track.get('performer', album_meta.get('PERFORMER', '')),
            'album': album_meta.get('TITLE', ''),
            'track_num': track['track_num'],
            'total_tracks': str(len(tracks)),
            'genre': album_meta.get('GENRE', ''),
            'date': album_meta.get('DATE', ''),
        })

    print()
    print("Done!")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <cuefile>")
        sys.exit(1)
    split_dsf_cue(sys.argv[1])
