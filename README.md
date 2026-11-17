# Shortcast 🎙️✂️

> **Turn 4-hour YouTube podcasts into concise, broadcast-grade MP3s using 100% authentic human voices. Zero AI commentary.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)](https://python.org)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-9.0%2B-green?style=flat-square&logo=ffmpeg)](https://ffmpeg.org)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-2026%2B-red?style=flat-square)](https://github.com/yt-dlp/yt-dlp)
[![Gemini](https://img.shields.io/badge/Google-Gemini%202.5%20Flash-orange?style=flat-square&logo=google)](https://deepmind.google/technologies/gemini/)

---

## The Problem

Long-form podcasts (2 to 5 hours) are packed with deep, high-signal masterclasses, but also loaded with:
* 30-minute opening sponsor plugs and irrelevant filler.
* Tangential rabbit holes and circular repetition.
* Dead-air pauses, hesitations, and silence gaps.

**Existing solutions fall short:**
* **Text / Executive Summaries**: Strip all nuance, emotional tone, and speaker personality.
* **NotebookLM / Synthetic AI Hosts**: Replace authentic guest voices with robotic AI voices.
* **Voice Cloning**: Unnatural cadence, licensing issues, and degraded audio fidelity.

---

## The Shortcast Solution: Pure Authentic Audio Clipping

Shortcast extracts, polishes, and concatenates only the most valuable segments using the podcast's original studio audio:

1. **Zero AI Commentary by Default**: 100% real human voices. No synthetic AI hosts, no AI voiceover interruptions.
2. **Authentic Host Intro**: Retains the podcast's own introduction and guest welcome directly from the original recording.
3. **Selective Stream Slicing**: Uses `yt-dlp --download-sections` to pull only high-signal minutes directly from YouTube media servers—no downloading 4-hour video files.
4. **Dead-Air & Silence Removal**: Strips internal conversational pauses (>0.45s at -35dB) with FFmpeg without altering pitch or natural speech rhythm.
5. **Loudness Normalization**: Calibrates all discussion clips to broadcast standard -16 LUFS with smooth boundary transitions.

---

## Architecture

```mermaid
flowchart TD
    A[YouTube URL] -->|yt-dlp --skip-download --write-auto-sub| B[VTT Subtitle Track]
    B -->|Deduplicate & Parse| C[Timestamped Transcript]
    C -->|Gemini 2.5 Flash| D[Edit Decision List EDL JSON]
    D -->|Segment 1: Host Intro & Keeper Timestamps| E[Targeted Stream Slicer]
    
    subgraph Audio Processing Pipeline
        E -->|yt-dlp --download-sections| F[Raw Audio Slices]
        F -->|FFmpeg silenceremove| G[Pause-Trimmed Audio]
        G -->|FFmpeg loudnorm -16 LUFS| H[Normalized Clips]
    end
    
    H -->|FFmpeg Concat Demuxer| I[Broadcast-Ready Master MP3 (100% Real Voices)]
```

---

## Benchmark Case Study

Tested on Nikhil Kamath's **WTF is Payments?** episode with PhonePe, Razorpay & NPCI:

| Metric | Original YouTube Video | Shortcast TIGHT Cut | Shortcast FULL Cut |
| :--- | :--- | :--- | :--- |
| **Duration** | **04:00:06** | **~00:49:15** (~49m) | **~01:08:00** (~68m) |
| **Time Saved** | 0% | **~79.5%** | **~71.7%** |
| **Segments** | Full Unedited Run | 6 Core Chapters | 11 Complete Chapters |
| **AI Commentary** | None | **Zero (100% Real Voices)** | **Zero (100% Real Voices)** |
| **Host Intro** | Standard Video | Authentic Nikhil Intro | Authentic Nikhil Intro |
| **Output Size** | ~4 GB (Video) | **~67 MB** (192 kbps MP3) | **~94 MB** (192 kbps MP3) |

---

## Installation & Prerequisites

### 1. Prerequisites
* **Python**: 3.10 or higher
* **FFmpeg**: Modern build on system PATH (e.g. Gyan.FFmpeg or official builds)
* **yt-dlp**: Required for audio section downloading

On Windows:
```powershell
# Install FFmpeg via winget
winget install Gyan.FFmpeg

# Install Python packages
pip install yt-dlp google-genai
```

---

## Usage

### Mode 1: Fully Automated from YouTube URL
Analyzes the subtitle track using Gemini 2.5 Flash, selects the host's natural intro and high-signal chapters, creates the EDL JSON, and renders the pure audio MP3:

```powershell
# Set your Gemini API key
$env:GEMINI_API_KEY="your-gemini-api-key"

# Run end-to-end (tight cut, 0 AI commentary)
python podcast_splicer.py --url "https://www.youtube.com/watch?v=hBvInWQWpZ0" --profile tight
```

### Mode 2: From Pre-Curated Edit Decision List (EDL)
Run from an existing EDL file (no API key required):

```powershell
# Dry run verification (displays duration projections without downloading)
python podcast_splicer.py --edl upi_podcast_edl.json --profile tight --dry-run

# Render final MP3 (100% authentic voices)
python podcast_splicer.py --edl upi_podcast_edl.json --profile tight --output "UPI_WTF_Payments_Tight.mp3"
```

---

## Cut Profiles

* **tight** (Default): Extracts only the essential, highest-signal chapters (typically ~40–50 mins). Perfect for high-density learning.
* **full**: Includes secondary founder discussions, technical deep dives, and macro trends (typically ~60–80 mins).

---

## CLI Options & Flags

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--url` | String | None | YouTube video URL to process end-to-end |
| `--edl` | Path | None | Path to pre-existing Edit Decision List (EDL JSON) |
| `--profile` | Choice | `tight` | Cut profile: `tight` (core highlights) or `full` |
| `--output` | Path | Auto | Output MP3 filename |
| `--keep-pauses` | Flag | `False` | Skip silence removal filter (retains natural dead air) |
| `--ai-bridges` | Flag | `False` | Opt-in to synthesize AI narrator bridges (default: 0 AI commentary) |
| `--silence-threshold` | Float | `-35.0` | Silence detection threshold in dB |
| `--silence-duration` | Float | `0.45` | Minimum silence duration in seconds to trim |
| `--dry-run` | Flag | `False` | Validate EDL, display duration projections, and exit |

---

## EDL JSON Format

You can inspect or customize any generated EDL:

```json
{
  "video_id": "hBvInWQWpZ0",
  "title": "If UPI Is Free, Who Pays for It? | WTF is Payments?",
  "url": "https://www.youtube.com/watch?v=hBvInWQWpZ0",
  "segments": [
    {
      "id": 1,
      "title": "History of Money & Panel Introductions",
      "start_time": "00:00:05",
      "end_time": "00:01:52",
      "include_in_tight_cut": true
    },
    {
      "id": 2,
      "title": "Anatomy of a 100-Rupee UPI Transfer",
      "start_time": "00:52:02",
      "end_time": "01:05:25",
      "include_in_tight_cut": true
    }
  ]
}
```

---

## License

MIT License. Built for high-signal learning and pure audio preservation.
