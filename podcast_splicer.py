#!/usr/bin/env python3
"""
Podcast Splicer: Hybrid Documentary Podcast Generator
Automates converting multi-hour YouTube podcasts into condensed, coherent MP3s.
Preserves authentic voices, removes dead-air/filler, and inserts AI narrator bridges.
"""

import os
import sys
import json
import argparse
import subprocess
import shutil
import tempfile
import re
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional

# Optional imports
try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False


def find_tool(name: str) -> Optional[str]:
    """Finds an executable in PATH or common Windows installation directories."""
    path = shutil.which(name)
    if path:
        return path

    # Check python Scripts directory
    py_dir = Path(sys.executable).parent
    candidate = py_dir / "Scripts" / f"{name}.exe"
    if candidate.exists():
        return str(candidate)

    # Check user WinGet packages (common for Gyan.FFmpeg)
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_dir.exists():
            for match in winget_dir.glob(f"**/{name}.exe"):
                if match.is_file():
                    return str(match)

    # Check KeyShot or other Program Files fallback
    for base in [Path("C:/Program Files"), Path("C:/Program Files (x86)")]:
        if base.exists():
            for match in base.glob(f"**/{name}.exe"):
                if match.is_file():
                    return str(match)

    return None


def get_ffmpeg_path() -> str:
    path = find_tool("ffmpeg")
    if not path:
        raise FileNotFoundError(
            "FFmpeg executable not found. Please install FFmpeg or add it to PATH."
        )
    return path


def get_ytdlp_cmd() -> List[str]:
    path = find_tool("yt-dlp")
    if path:
        return [path]
    # Check if python -m yt_dlp works
    try:
        subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                       capture_output=True, check=True)
        return [sys.executable, "-m", "yt_dlp"]
    except Exception:
        raise FileNotFoundError(
            "yt-dlp executable or Python module not found. Run: pip install yt-dlp"
        )


def parse_time_to_seconds(time_str: str) -> float:
    """Converts HH:MM:SS or MM:SS or SSS.ms string to seconds."""
    parts = time_str.strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    elif len(parts) == 1:
        return float(parts[0])
    raise ValueError(f"Invalid timestamp format: {time_str}")


def format_seconds(seconds: float) -> str:
    """Converts seconds into HH:MM:SS string."""
    seconds = int(round(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def clean_vtt_transcript(vtt_content: str) -> str:
    """Parses and deduplicates VTT subtitle tracks into structured timestamped text."""
    lines = vtt_content.splitlines()
    cleaned = []
    current_time = ""
    seen_texts = set()

    time_pattern = re.compile(
        r"^(\d{2}:\d{2}:\d{2}[\.,]\d{3}|\d{2}:\d{2}[\.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[\.,]\d{3}|\d{2}:\d{2}[\.,]\d{3})"
    )

    for line in lines:
        line = line.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
            continue

        match = time_pattern.match(line)
        if match:
            raw_start = match.group(1).replace(",", ".")
            parts = raw_start.split(":")
            if len(parts) == 3:
                current_time = f"{int(parts[0]):02d}:{int(parts[1]):02d}:{int(float(parts[2])):02d}"
            elif len(parts) == 2:
                current_time = f"00:{int(parts[0]):02d}:{int(float(parts[1])):02d}"
            continue

        # Strip inline markup like <c>...</c> or <00:00:00.000>
        text = re.sub(r"<[^>]+>", "", line).strip()
        if text and text not in seen_texts:
            seen_texts.add(text)
            cleaned.append(f"[{current_time}] {text}")

    return "\n".join(cleaned)


def extract_transcript_from_url(url: str, work_dir: Path) -> tuple[str, str, str]:
    """Downloads VTT auto-subtitles using yt-dlp and returns (clean_text, video_title, video_id)."""
    ytdlp = get_ytdlp_cmd()
    print(f"[*] Fetching metadata and subtitles for: {url}")

    # Fetch metadata
    meta_cmd = ytdlp + ["--no-check-certificate", "--dump-json", "--skip-download", url]
    meta_res = subprocess.run(meta_cmd, capture_output=True, text=True, check=True)
    meta = json.loads(meta_res.stdout)
    video_id = meta.get("id", "podcast")
    video_title = meta.get("title", "YouTube Podcast")

    # Download subtitles
    sub_prefix = work_dir / f"subs_{video_id}"
    sub_cmd = ytdlp + [
        "--no-check-certificate",
        "--skip-download",
        "--write-auto-sub",
        "--write-sub",
        "--sub-lang", "en,en-orig,en-US,en-IN",
        "--sub-format", "vtt",
        "-o", str(sub_prefix),
        url
    ]
    subprocess.run(sub_cmd, capture_output=True, text=True)

    # Locate generated VTT
    vtt_files = list(work_dir.glob(f"subs_{video_id}*.vtt"))
    if not vtt_files:
        raise RuntimeError("No English subtitle track could be downloaded for this video.")

    vtt_path = vtt_files[0]
    with open(vtt_path, "r", encoding="utf-8", errors="ignore") as f:
        raw_vtt = f.read()

    cleaned_text = clean_vtt_transcript(raw_vtt)
    return cleaned_text, video_title, video_id


def generate_edl_with_gemini(transcript: str, video_title: str, video_id: str, url: str, model_name: str = "gemini-2.5-flash") -> Dict[str, Any]:
    """Uses Gemini to analyze transcript and generate an Edit Decision List (EDL JSON)."""
    if not HAS_GENAI:
        raise ImportError("google-genai package is required. Run: pip install google-genai")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required to generate EDL from URL.")

    client = genai.Client(api_key=api_key)

    prompt = f"""
You are an expert executive audio producer and documentary editor.
Your task is to take a full transcript of a long-form YouTube podcast and construct an Edit Decision List (EDL) in JSON format.

The goal is to produce a high-signal documentary audio cut that:
1. Retains all core insights, first-principles explanations, technical specifics, and authentic guest stories.
2. Aggressively prunes banter, small talk, sponsor plugs, intro music, long-winded setup tangents, and repetitive arguments.
3. Groups discussions into coherent chapters/segments with accurate start and end timestamps.
4. Marks segments for two profiles:
   - "include_in_tight_cut": true for essential, high-density segments (totaling ~40-60 minutes).
   - "include_in_tight_cut": false for deep-dive or bonus discussions suitable for the full cut (totaling ~70-90 minutes).
5. Writes a concise, natural AI narrator transition bridge (8-10 seconds of spoken audio, ~20-30 words) for each segment. The bridge must orient the listener to the topic starting right after skipped discussions.

Podcast Title: {video_title}
Video URL: {url}

Transcript excerpt with timestamps:
{transcript[:300000]}

Respond ONLY with valid JSON conforming to this schema:
{{
  "video_id": "{video_id}",
  "title": "{video_title}",
  "url": "{url}",
  "segments": [
    {{
      "id": 1,
      "title": "Segment Title",
      "start_time": "HH:MM:SS",
      "end_time": "HH:MM:SS",
      "include_in_tight_cut": true,
      "narrator_bridge": "Short 1-2 sentence narrator setup."
    }}
  ]
}}
"""

    print(f"[*] Prompting Gemini ({model_name}) to generate Edit Decision List...")
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )

    edl_data = json.loads(response.text)
    return edl_data


async def synthesize_speech_edge_tts(text: str, output_path: Path, voice: str = "en-IN-PrabhatNeural") -> bool:
    """Synthesizes speech using edge-tts as a reliable fallback."""
    if not HAS_EDGE_TTS:
        return False
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_path))
        return True
    except Exception as e:
        print(f"[-] edge-tts synthesis failed: {e}")
        return False


def synthesize_speech_gemini(text: str, output_path: Path, voice: str = "Puck") -> bool:
    """Synthesizes speech using Gemini Preview TTS if available."""
    if not HAS_GENAI or not os.environ.get("GEMINI_API_KEY"):
        return False
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        # Check preview TTS model
        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=text,
            config=types.GenerateContentConfig(
                response_mime_type="audio/mp3",
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                    )
                )
            )
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                with open(output_path, "wb") as f:
                    f.write(part.inline_data.data)
                return True
        return False
    except Exception as e:
        # Expected if preview TTS endpoint is restricted or quota exceeded
        return False


def generate_narrator_bridge(text: str, output_path: Path, preferred_voice: str) -> bool:
    """Tries Gemini TTS first, falls back automatically to edge-tts."""
    if synthesize_speech_gemini(text, output_path, voice=preferred_voice or "Puck"):
        return True

    # Fallback to edge-tts
    edge_voice = "en-IN-PrabhatNeural"
    if preferred_voice and "Neural" in preferred_voice:
        edge_voice = preferred_voice

    return asyncio.run(synthesize_speech_edge_tts(text, output_path, voice=edge_voice))


def process_audio_slice(
    ffmpeg_bin: str,
    input_wav: Path,
    output_wav: Path,
    keep_pauses: bool = False,
    silence_duration: float = 0.45,
    silence_threshold: float = -35.0
):
    """
    Applies silence removal and loudness normalization to keep speech punchy and volume-balanced.
    """
    filters = []
    if not keep_pauses:
        filters.append(
            f"silenceremove=stop_periods=-1:stop_duration={silence_duration}:stop_threshold={silence_threshold}dB:window=0.02"
        )
    filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    filter_str = ",".join(filters)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(input_wav),
        "-af", filter_str,
        "-ar", "44100",
        "-ac", "2",
        str(output_wav)
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def process_bridge_audio(
    ffmpeg_bin: str,
    input_audio: Path,
    output_wav: Path,
    trailing_padding_sec: float = 0.35
):
    """Normalizes bridge voice loudness and adds breathing space before the interview clip."""
    filter_str = f"loudnorm=I=-16:TP=-1.5:LRA=11,apad=pad_dur={trailing_padding_sec}"
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(input_audio),
        "-af", filter_str,
        "-ar", "44100",
        "-ac", "2",
        str(output_wav)
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def download_audio_section(
    ytdlp_cmd: List[str],
    ffmpeg_bin: str,
    url: str,
    start_time: str,
    end_time: str,
    output_wav: Path
):
    """Downloads only the specified audio slice directly from YouTube via yt-dlp."""
    section_arg = f"*{start_time}-{end_time}"
    ffmpeg_dir = str(Path(ffmpeg_bin).parent)
    cmd = ytdlp_cmd + [
        "--ffmpeg-location", ffmpeg_dir,
        "--download-sections", section_arg,
        "--force-keyframes-at-cuts",
        "--no-check-certificate",
        "-f", "ba/b",
        "-x",
        "--audio-format", "wav",
        "-o", str(output_wav.with_suffix(".%(ext)s")),
        url
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if not output_wav.exists():
        # Check if yt-dlp appended an extension or created intermediate file
        possible = list(output_wav.parent.glob(f"{output_wav.stem}*"))
        if possible:
            for p in possible:
                if p.suffix.lower() in [".wav", ".m4a", ".mp3", ".opus"]:
                    shutil.move(str(p), str(output_wav))
                    return
        raise RuntimeError(f"Failed to slice audio section {section_arg}: {res.stderr}")


def print_dry_run_summary(segments: List[Dict[str, Any]], profile: str):
    """Displays estimated time metrics for the selected cut profile."""
    total_raw_sec = 0.0
    print("\n" + "=" * 78)
    print(f" DRY RUN SUMMARY: Profile [{profile.upper()}] ({len(segments)} segments)")
    print("=" * 78)
    print(f"{'#':<3} {'Start':<9} {'End':<9} {'Duration':<10} {'Title'}")
    print("-" * 78)

    for i, seg in enumerate(segments, 1):
        s_sec = parse_time_to_seconds(seg["start_time"])
        e_sec = parse_time_to_seconds(seg["end_time"])
        dur = e_sec - s_sec
        total_raw_sec += dur
        print(f"{i:<3} {seg['start_time']:<9} {seg['end_time']:<9} {format_seconds(dur):<10} {seg.get('title', 'Segment')}")

    est_trimmed_sec = total_raw_sec * 0.76  # ~24% average reduction from dead-air stripping
    est_bridges_sec = len(segments) * 9.5   # ~9.5s average per bridge
    final_est_sec = est_trimmed_sec + est_bridges_sec

    print("-" * 78)
    print(f"Raw Segment Audio Total : {format_seconds(total_raw_sec)}")
    print(f"Est. After Pause-Removal : ~{format_seconds(est_trimmed_sec)} (at -35dB / 0.45s threshold)")
    print(f"Est. AI Narrator Bridges : ~{format_seconds(est_bridges_sec)} ({len(segments)} bridges)")
    print(f"Estimated Final Duration : ~{format_seconds(final_est_sec)}")
    print("=" * 78 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Convert long YouTube podcasts into tight, listenable documentary MP3s."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="YouTube video URL to process end-to-end")
    group.add_argument("--edl", help="Path to pre-existing Edit Decision List (EDL JSON)")

    parser.add_argument("--profile", choices=["full", "tight"], default="tight",
                        help="Cut profile: 'tight' (core highlights) or 'full' (all curated chapters)")
    parser.add_argument("--output", help="Output MP3 filename")
    parser.add_argument("--keep-pauses", action="store_true",
                        help="Skip FFmpeg silence removal (preserves all natural dead air)")
    parser.add_argument("--skip-bridges", action="store_true",
                        help="Skip synthesizing AI narrator transitions between segments")
    parser.add_argument("--voice", default="en-IN-PrabhatNeural",
                        help="Narrator voice name (e.g. 'Puck' for Gemini TTS, or edge-tts voice)")
    parser.add_argument("--silence-threshold", type=float, default=-35.0,
                        help="Silence detection threshold in dB (default: -35.0)")
    parser.add_argument("--silence-duration", type=float, default=0.45,
                        help="Minimum silence duration in seconds to trim (default: 0.45)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate EDL, display duration projections, and exit")

    args = parser.parse_args()

    # Verify tool availability
    try:
        ytdlp_cmd = get_ytdlp_cmd()
    except FileNotFoundError as e:
        print(f"[!] Error: {e}")
        sys.exit(1)

    try:
        ffmpeg_bin = get_ffmpeg_path()
        # Ensure ffmpeg and python scripts directories are in PATH for all child processes
        ffmpeg_dir = str(Path(ffmpeg_bin).parent)
        py_scripts = str(Path(sys.executable).parent / "Scripts")
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{ffmpeg_dir};{py_scripts};{current_path}"
    except FileNotFoundError as e:
        if not args.dry_run:
            print(f"[!] Error: {e}")
            sys.exit(1)
        ffmpeg_bin = "ffmpeg"

    work_dir = Path(tempfile.mkdtemp(prefix="shortcast_"))

    try:
        # Load or generate EDL
        if args.url:
            raw_text, title, vid_id = extract_transcript_from_url(args.url, work_dir)
            edl = generate_edl_with_gemini(raw_text, title, vid_id, args.url)
            # Save generated EDL locally for reproducibility
            edl_filename = f"{vid_id}_edl.json"
            with open(edl_filename, "w", encoding="utf-8") as f:
                json.dump(edl, f, indent=2)
            print(f"[+] Saved generated Edit Decision List to: {edl_filename}")
        else:
            with open(args.edl, "r", encoding="utf-8") as f:
                edl = json.load(f)

        all_segments = edl.get("segments", [])
        if args.profile == "tight":
            selected_segments = [s for s in all_segments if s.get("include_in_tight_cut", True)]
        else:
            selected_segments = all_segments

        if not selected_segments:
            print("[!] No segments found matching the selected profile.")
            sys.exit(1)

        print_dry_run_summary(selected_segments, args.profile)
        if args.dry_run:
            print("[*] Dry run complete. No audio downloaded.")
            return

        # Prepare output path
        if args.output:
            final_output = Path(args.output)
        else:
            safe_title = re.sub(r'[^\w\-_\.]', '_', edl.get("title", "podcast"))[:40]
            final_output = Path(f"{safe_title}_{args.profile}.mp3")

        url = edl.get("url")
        if not url:
            vid_id = edl.get("video_id")
            if vid_id:
                url = f"https://www.youtube.com/watch?v={vid_id}"
            else:
                raise ValueError("EDL must include either 'url' or 'video_id'.")

        concat_list_path = work_dir / "concat_list.txt"
        concat_entries = []

        print(f"\n[*] Processing {len(selected_segments)} segments into final MP3: {final_output.name}")

        for idx, seg in enumerate(selected_segments, 1):
            seg_id = seg.get("id", idx)
            title = seg.get("title", f"Segment {idx}")
            s_time = seg["start_time"]
            e_time = seg["end_time"]
            bridge_text = seg.get("narrator_bridge", "")

            print(f"\n--- [{idx}/{len(selected_segments)}] {title} ({s_time} - {e_time}) ---")

            # 1. Narrator bridge
            if bridge_text and not args.skip_bridges:
                raw_bridge = work_dir / f"bridge_{idx}_raw.mp3"
                clean_bridge = work_dir / f"bridge_{idx}_clean.wav"
                print(f"    [*] Synthesizing bridge: \"{bridge_text[:60]}...\"")
                if generate_narrator_bridge(bridge_text, raw_bridge, args.voice):
                    process_bridge_audio(ffmpeg_bin, raw_bridge, clean_bridge)
                    concat_entries.append(clean_bridge)
                else:
                    print("    [-] Bridge synthesis skipped (TTS provider unavailable).")

            # 2. Slice audio section
            raw_slice = work_dir / f"slice_{idx}_raw.wav"
            clean_slice = work_dir / f"slice_{idx}_clean.wav"
            print(f"    [*] Slicing audio stream [{s_time} to {e_time}] via yt-dlp...")
            download_audio_section(ytdlp_cmd, ffmpeg_bin, url, s_time, e_time, raw_slice)

            # 3. Clean slice (pause removal + loudnorm)
            print(f"    [*] Applying silence removal and loudness normalization...")
            process_audio_slice(
                ffmpeg_bin,
                raw_slice,
                clean_slice,
                keep_pauses=args.keep_pauses,
                silence_duration=args.silence_duration,
                silence_threshold=args.silence_threshold
            )
            concat_entries.append(clean_slice)

        # 4. Final assembly
        print(f"\n[*] Concatenating {len(concat_entries)} audio clips into final broadcast MP3...")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for entry in concat_entries:
                # FFmpeg concat file requires escaped forward slashes
                normalized_path = str(entry.resolve()).replace("\\", "/")
                f.write(f"file '{normalized_path}'\n")

        assemble_cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_path),
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(final_output.resolve())
        ]
        subprocess.run(assemble_cmd, capture_output=True, check=True)
        print(f"\n[+] SUCCESS! Final condensed MP3 ready: {final_output.resolve()}")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
