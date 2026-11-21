# JetVideo — Automatic Video Publisher

[English](README.md) · [Українська](README.uk.md) · [Русский](README.ru.md)

**JetVideo turns long videos into numbered clips, adds timed banners, and schedules TikTok posts.** Use the built-in editor on its own or send your finished clips to a publishing queue.

**[Download JetVideo 1.0.0 for Windows](https://github.com/DaniloAdmin/JetVideo/releases/download/v4.3.0/JetVideo-1.0.0-Windows-x64.zip)** · [Release notes, checksums and source archives](https://github.com/DaniloAdmin/JetVideo/releases/tag/v4.3.0)

Extract the entire ZIP and open `JetVideo.exe` on Windows 10 or 11 (64-bit). Python, FFmpeg and FFprobe are included. The app and its illustrated help are available in English, Ukrainian and Russian.

| What you want to do | What JetVideo offers |
| --- | --- |
| Edit videos on your PC | Split videos by duration, name clips `Part 1`, `Part 2` and so on, and optionally add an image or video banner at a chosen time in each clip. No account setup is needed for editing. |
| Schedule TikTok posts | Queue clips for connected TikTok channels, choose publishing windows and time zones, add captions and hashtags, and review posting history. You'll need your own Buffer account with a connected TikTok channel and a Cloudflare R2 bucket. |

**Cloudflare R2 for TikTok publishing.** R2 Standard includes 10 GB of storage throughout the month and a monthly allowance of requests at no charge. To activate R2, add a supported bank card or another accepted payment method in Cloudflare. Cloudflare charges for storage or requests above the free limits. [R2 pricing](https://developers.cloudflare.com/r2/pricing/#free-tier) · [Cloudflare payment methods](https://developers.cloudflare.com/billing/get-started/create-billing-profile/)

## Quick start

**Split a video.** Open **Video Editor** and click **Add videos**. Select **Split into parts**. For two-minute clips, use `00:02:00` and `Part {part}`. Choose an output folder and click **Start splitting**.

**Schedule your first post.** Create a [Buffer API key](docs/BUFFER_SETUP.md) and set up your [Cloudflare R2 bucket](docs/R2_SETUP.md). Save the credentials in **Publishing → Connections**, link your TikTok channel under **Accounts**, then click **Test connections**. Add one video under **Dashboard** and click **Preview**.

Click the **globe** in the top right to switch languages. JetVideo remembers your choice. Click **?** or press **F1** to open this guide. Use the drop-down at the top to jump to a section.

## First run on Windows

1. Extract the entire ZIP to its own folder and open **JetVideo.exe**. The portable build runs on Windows 10/11 x64.
2. Select **Yes** on first run to create a workspace under `JetVideo Data` next to the app, or **No** to choose a different workspace. For editing only, select **No**, then cancel the folder picker.
3. The ZIP includes `tools/ffmpeg.exe` and `tools/ffprobe.exe`. JetVideo finds both after extraction. Keep `JetVideo.portable` beside the EXE so this copy stores its settings separately.
4. To use your own folders for a publishing profile, open **Publishing** → **Schedule** → **ready / scheduled / posted folders**. Give each profile its own folders. Changing a path leaves existing videos in their current location.

## Split videos

1. Click **Add videos**, or **From Publishing** to select files already in a profile's `ready` folder. Choose **Split into parts**.
2. Set **Part duration** and **Filename template**. Use seconds, `MM:SS` or `HH:MM:SS` for duration. For a series already in progress, change **Starting number**. `Part {part:03d}` creates `Part 001.mp4`, `Part 002.mp4` and so on. Numbering continues across source files in list order.
3. Choose **No banners**, **Overlay banners** or **Freeze-frame banners**. When using banners, click **Add banner**, pick a file and set its start time. `00:00:30` starts the banner 30 seconds into **each part**. An overlay keeps the clip's duration. A freeze-frame adds time to the clip.
4. To keep the short clip at the end, leave **Keep the final short clip** checked. A banner scheduled outside that clip is skipped. Check **Log** for details.
5. Pick the output folder and click **Start splitting**. When done, click **Open folder** or **Send to Publishing** to copy the output into one or more profiles' `ready` folders. Start scheduling from **Publishing**.

## Add banners to whole videos

1. Choose **Whole video with banners** and add source videos. Click **Add banner** to select a video, PNG or JPEG.
2. Set the time from the beginning of the source, position, size, opacity and duration. The default duration for an image is three seconds. The layout preview uses a 9:16 frame. Percentages apply to the source video's actual dimensions.
3. Use **Settings** to adjust H.264 quality, fitting, chroma key and freeze-frame audio. Select an output folder and click **Create video**.

You can add up to 40 banners per video. **Overlay** supports banners that appear at the same time and keeps the original audio and duration. **Freeze-frame** pauses the source, extends the output and optionally plays the banner's audio. Use one mode for all banners in the same job. Leave at least one frame on both sides of a freeze-frame insertion.

**Save template** and **Load template** are in the **Banners** section. A template stores paths to banner files. Check those paths when moving to another computer. **Cancel** stops encoding and keeps completed outputs. **Log** shows details and errors. Exports preserve the source files and use a new name when the output name is already taken.

<a name="connections"></a>
## Connect Buffer and Cloudflare R2

Open **Publishing → Connections**. Click **?** next to a field to see where to find its value. Both setup guides include screenshots. [Buffer API key](docs/BUFFER_SETUP.md) · [Cloudflare R2 setup](docs/R2_SETUP.md)

Open **Publishing** → **Connections** and fill in these fields.

| Field | Where to find the value |
| --- | --- |
| Buffer API Key | Personal key in Buffer Settings → API |
| R2 Endpoint | Your R2 account's HTTPS S3 endpoint |
| R2 Access Key ID / Secret Access Key | S3 credentials for the selected bucket |
| R2 Bucket | Bucket name |
| R2 Public URL | Public HTTPS base URL for the bucket, without a filename |

Buffer must be able to fetch the video at its public URL. Enable public access or configure your own domain on R2. Give JetVideo read, write and delete access scoped to the selected bucket. **Test connections** queries Buffer, writes a small test object to R2, checks public access and removes the object.

Keys are saved in the workspace's local `.env` file. Protect the folder with your Windows user account and keep the file and your backups private. [Blank configuration example](.env.example).

Open **Accounts**. Use **New account** to create a profile and **Link Buffer** → **Find in Buffer** to select and save a TikTok channel. Repeat for each channel. Profiles share Buffer and R2 connections and have separate queues, folders, schedules and history. Use **Archive / restore** to hide unused profiles. **Delete profile** removes the profile from JetVideo and leaves its files and Buffer posts in place.

## Schedule a post

1. Select a profile and open **Schedule**. Click **Time zone** and choose a city or region. You can search for `Kyiv`, `New York`, or `UTC`. Set publishing windows and a queue limit, then click **Save schedule**. JetVideo schedules one video per window at a random time within it.
2. Put a test video in the profile's `ready` folder or use **Add videos** on **Dashboard**. Wait until copying finishes and the file reaches the minimum age. By default this takes 15 seconds.
3. Set up a caption template and preview the result. [Captions and tags](#captions-and-tags)
4. Return to **Dashboard** and click **Preview**. To fill the queue after review, click **Current account** or **All accounts** and confirm. **Start WATCH** repeats this for the active profile at the configured interval while JetVideo is open.
5. Check the post in Buffer and TikTok. **Check Buffer** refreshes its state. A video goes from `scheduled` to `posted` after Buffer reports `sent`.

Open **Posting history** from **Dashboard** or the sidebar. Search by filename or part number. Double-click a row or select it and click **Details** to see the full filename, status, date and part number.

### Failed or uncertain posts

`uploaded` means the video reached R2. `scheduled` means Buffer accepted it into the queue. `sent` confirms publication. If the submission response is lost, the item is marked `uncertain`; the R2 copy and the submission record are kept, and automatic retries are blocked.

Stop WATCH and click **Check Buffer** to compare the saved caption, channel and submission time against posts in Buffer. A single clear match restores the record. If there is no match, check both TikTok and Buffer yourself before using **Return to ready**. A missing post in Buffer can be outside its available history. Use a separate fill-queue action when you are ready to try scheduling again.

Keep `state.json` and its backups. The history protects against duplicate submissions using saved part/post IDs and SHA-256 hashes for videos handled by this version. If the history is damaged, stop WATCH and restore a known-good backup.

<a name="captions-and-tags"></a>
## Captions and tags

Open **Publishing → Captions and tags** for the profile you want. Write the post caption on the left and the hashtag sets on the right.

### Caption templates

For a regular video, write a fixed caption or enter `{filename}` to use the filename without its extension. For a series of clips, use `{part}`. With `Part 7.mp4`, the template `Funny moment · Part {part}` produces “Funny moment · Part 7”.

Enter at least one template in **Caption templates**. If you want alternatives, put `---` on a line of its own between them. Here are two templates that include tags.

```text
Funny moment · Part {part}
{hashtags}
---
Highlights · Part {part}
{hashtags}
```

JetVideo recognises part numbers in names like `7.mp4`, `7_with_ad.mp4`, `Part 7.mp4`, `часть 7.mp4`, and `частина 7.mp4`. For other filenames, use `{filename}`. The `{season}` and `{episode}` variables insert the season and episode. Before using them, save the corresponding part ranges under **Seasons and episodes**. For example, assign parts 1 through 3 to season 1, episode 2.

### Hashtag sets

In **Hashtag sets**, prefix each tag with `#` and separate tags with spaces. Each line is one set. Put `{hashtags}` in the template where you want the tags to appear.

```text
#clips #funny
#video #highlights
```

If you enter multiple templates and sets, JetVideo picks one of each independently at random. Leave the right field empty to publish without tags.

Click **Caption preview** and enter a filename and a sample part number, such as `7`. If it looks right, click **Save captions and tags**. If you are setting up your first post, check that the services are connected. [Connect Buffer and Cloudflare R2](#connections)

## Statistics and sync

Open **Statistics** and click **Refresh all**. Metrics come from Buffer. A dash indicates missing data, and an asterisk marks an incomplete total. [Buffer metrics documentation](https://developers.buffer.com/guides/post-metrics).

Go to **Sync** → **Check all accounts** to compare saved posts with Buffer. Review the results before using **Apply Buffer statuses**. Use **Check Buffer** on **Dashboard** to check a single profile.

## Data, logs and updates

The portable ZIP keeps its settings in `JetVideo Data` beside the EXE, with `data` as the default workspace. Processing and publishing have separate logs. Builds without the portable marker use `%LOCALAPPDATA%\JOJO BOT` and `%APPDATA%\VideoAdInserter` for compatibility with previous versions.

Before replacing the EXE, stop WATCH, close the app and back up your workspace and editor preferences. Use **Activity log** → **Connect folder** to reconnect an existing workspace. **Move to another drive** copies the workspace and keeps the original folder. Keep the `tools` folder beside the updated EXE.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| No videos get picked up | File copy completed, minimum file age, profile history and matching SHA-256 |
| Buffer returns 401 or 403 | Personal API key permissions and TikTok channel connection |
| Buffer returns 429 | Service rate limits, queue and account plan |
| R2 upload works but the video URL fails | Public domain, direct HTTPS URL and bucket settings |
| Wrong publishing time | IANA time zone and configured windows |
| FFmpeg or FFprobe missing | Extract the full ZIP or select FFmpeg under **Settings** |
| Banner missing from a short clip | Set a start time within that clip and check **Log** |
| Workspace unavailable | Reconnect the original drive or restore the workspace from backup |

## Header buttons

- **?** opens this guide. **F1** opens it from either main screen.
- **$** is reserved for voluntary donations.
- The **headset** is reserved for technical support.
- The **globe** switches between English, Ukrainian and Russian.

## Development and license

The release uses CPython 3.14.10 x64 with Tkinter. `requirements-build.lock` contains the build dependencies. Run `python -m pytest -q` for tests or `build_windows.bat` to build the EXE and run its smoke test.

JetVideo 1.0.0 corresponds to the `v4.3.0` tag. Copyright © 2026 axegaoo. The JetVideo code is licensed under the GNU GPL, version 3 only (`GPL-3.0-only`). Keep the applicable notices when redistributing copies and make corresponding source available as required when distributing binaries. [License](LICENSE) · [Copyright](COPYRIGHT.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)
