# Campaign profiles

One JSON file per campaign in this folder. The clipper reads them with
`--campaign <name>` (single file) or `--batch-dir <folder>` (one
subfolder per campaign). **Adding a new campaign = adding one JSON
file. No code changes.**

A profile holds:

| Field | Meaning |
|---|---|
| `display_name` | Human name, printed at run start and in post packs |
| `output_subfolder` | Where this campaign's clips land (`output/<subfolder>/`, or `<batch>/output/<subfolder>/`) |
| `clip_length` | `{"default": 30, "min": 15, "max": null}` - runs are clamped to min/max |
| `layout`, `language`, `lead_in`, `min_gap` | Same meanings as the CLI flags; CLI flags win |
| `watermark` | `null`, or `{"file": "assets/x.png", "position": "bottom-right", "opacity": 0.85, "width": 200}` - file path is relative to this folder; drawn last, for the whole clip |
| `name_card` | `null`, or `{"enabled": true, "default_text": null, "duration": 2.0}` - guest name at the top from frame 0; text comes from `--name-card`, a batch folder's `name_card.txt`, or `default_text` |
| `hook` | `{"enabled": true, "notes": "..."}` |
| `post` | `{"youtube_tags": "...", "instagram_tags": "...", "text_template": "... {title} ..."}` - used to build each clip's `.post.txt` pack |
| `rules_notes` | Campaign rules, printed at run start |
| `post_notes` | Reminders copied into every post pack |

## Included profile

- `example-campaign`: a template with neutral placeholder values. Copy
  it, rename the file, and fill in a real campaign's rules, read off
  that campaign's own page, before use.

Real campaign profiles are kept private by design: they contain
campaign owners' briefs, terms, and links, which are not ours to
publish. The field table above is everything a profile can hold.

Nothing in this folder posts, submits or uploads anything. The tool
creates files; the owner reviews and posts from their own device.
