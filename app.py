import os
from flask import Flask, render_template, request, redirect, url_for, flash
import yt_dlp
import logging # For better logging

app = Flask(__name__)
# Use a persistent secret key in production, e.g., from environment variable
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24))

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- yt-dlp Options ---

# Options for initial info extraction: Get metadata and *list* of all formats
YDL_INFO_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'skip_download': True, # We only want metadata
    'forcejson': False, # Not needed when using library directly
    # 'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', # REMOVED - Get all formats first
    'noplaylist': True, # Process only the single video
    # Consider adding geo-bypass options if needed, though often ineffective against bot detection
    # 'geo_bypass': True,
    # 'geo_bypass_country': 'US', # Example
}

# --- Helper Function ---
def get_human_readable_size(size_bytes):
    """Converts bytes to a readable format (KB, MB, GB)."""
    if size_bytes is None:
        return "N/A"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes/1024:.1f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes/(1024**2):.2f} MB"
    else:
        return f"{size_bytes/(1024**3):.2f} GB"

@app.route('/', methods=['GET', 'POST'])
def index():
    video_info = None
    error_message = None
    submitted_url = request.args.get('submitted_url', '') # Get url from redirect if present

    if request.method == 'POST':
        url = request.form.get('url', '').strip()
        submitted_url = url # Keep url in the input box

        if not url:
            error_message = "Please enter a video URL."
        else:
            app.logger.info(f"Processing URL: {url}")
            try:
                # Use yt-dlp as a library to extract information
                with yt_dlp.YoutubeDL(YDL_INFO_OPTS) as ydl:
                    # Extract info. This now gets *all* formats.
                    info_dict = ydl.extract_info(url, download=False)

                    if not info_dict:
                         raise yt_dlp.utils.DownloadError("Could not extract video information (empty result). Check URL or try again.")

                    # Process formats - more detailed parsing
                    processed_formats = []
                    raw_formats = info_dict.get('formats', [])
                    app.logger.info(f"Found {len(raw_formats)} raw formats for {url}.")

                    for f in raw_formats:
                        # Skip formats without direct URLs (important!)
                        # Also skip common manifest formats unless specifically handled
                        if not f.get('url') or f.get('protocol') in ('m3u8', 'm3u8_native', 'http_dash_segments', 'rtsp', 'rtmp'):
                            continue

                        format_id = f['format_id']
                        ext = f.get('ext', 'N/A')
                        filesize = f.get('filesize') or f.get('filesize_approx')
                        readable_size = get_human_readable_size(filesize)
                        vcodec = f.get('vcodec', 'none')
                        acodec = f.get('acodec', 'none')
                        height = f.get('height')
                        width = f.get('width')
                        tbr = f.get('tbr') # Total bitrate
                        fps = f.get('fps')

                        # Determine quality label and type
                        quality_label = f.get('format_note', '')
                        type_label = ""

                        if vcodec != 'none' and acodec != 'none':
                            type_label = "Video+Audio"
                            if not quality_label and height: quality_label = f"{height}p"
                            elif not quality_label: quality_label = f.get('resolution', f"{width}x{height}" if width and height else 'Unknown')
                        elif vcodec != 'none' and acodec == 'none':
                            type_label = "Video Only"
                            if not quality_label and height: quality_label = f"{height}p"
                            elif not quality_label: quality_label = f.get('resolution', f"{width}x{height}" if width and height else 'Video')
                            if fps: quality_label += f" @{fps:.0f}fps"
                        elif vcodec == 'none' and acodec != 'none':
                            type_label = "Audio Only"
                            if not quality_label and tbr: quality_label = f"{tbr:.0f} kbps" # Bitrate for audio
                            elif not quality_label: quality_label = f.get('resolution', 'Audio')
                        else:
                            continue # Skip if neither audio nor video

                        if not quality_label: quality_label = f"ID: {format_id}" # Fallback

                        processed_formats.append({
                            'format_id': format_id,
                            'ext': ext,
                            'quality': quality_label.strip(),
                            'type': type_label,
                            'filesize_bytes': filesize or 0, # Store raw bytes for sorting
                            'readable_size': readable_size,
                            'vcodec': vcodec,
                            'acodec': acodec,
                            'height': height or 0, # Ensure numeric for sorting
                            'tbr': tbr or 0, # Ensure numeric for sorting
                        })

                    # Sort formats: Combined first, then Video, then Audio
                    # Within each type, sort by height (desc), then bitrate (desc)
                    processed_formats.sort(key=lambda x: (
                        0 if x['type'] == "Video+Audio" else 1 if x['type'] == "Video Only" else 2, # Type order
                        -x['height'], # Height descending
                        -x['tbr'] # Bitrate descending
                    ))

                    app.logger.info(f"Processed {len(processed_formats)} downloadable formats for {url}.")

                    if not processed_formats and not error_message:
                        # If yt-dlp succeeded but we filtered out all formats (e.g., only manifests found)
                        error_message = "No direct download formats found. Video might use streaming protocols not supported for direct link generation."


                    video_info = {
                        'title': info_dict.get('title', 'N/A'),
                        'thumbnail': info_dict.get('thumbnail', None),
                        'formats': processed_formats,
                        'original_url': url # Store original URL for download links
                    }

            # --- Enhanced Error Handling ---
            except yt_dlp.utils.DownloadError as e:
                err_str = str(e).lower() # Lowercase for easier matching
                app.logger.error(f"yt-dlp DownloadError for URL {url}: {e}")

                if "sign in to confirm" in err_str or "confirm you're not a bot" in err_str:
                    error_message = ("Download failed: Platform requires verification (Bot Detection). "
                                     "This often happens on servers like Render. "
                                     "Try a different video, wait, or download locally.")
                elif "unsupported url" in err_str:
                    error_message = "Error: Unsupported URL. Check the link or try YouTube/Facebook."
                elif "video unavailable" in err_str:
                    error_message = "Error: Video unavailable (private, deleted, or restricted)."
                elif "login required" in err_str or "account required" in err_str:
                     error_message = "Error: This video requires login, which is not supported here."
                elif "urlopen error" in err_str or "network error" in err_str or "connection timed out" in err_str:
                     error_message = "Error: Network problem connecting to the video provider."
                elif "unable to extract" in err_str or "could not find video data" in err_str:
                     error_message = "Error: Could not extract video data. URL might be wrong or platform changed."
                else:
                    # Generic yt-dlp error
                    error_message = f"Error processing link: {e}" # Show limited error info

            except Exception as e:
                app.logger.error(f"General error processing URL {url}: {e}", exc_info=True) # Log full traceback
                error_message = "An unexpected server error occurred. Please try again later or contact support."

    # Pass submitted_url back to the template for GET requests too (handles redirects)
    return render_template('index.html', video_info=video_info, error=error_message, submitted_url=submitted_url)


@app.route('/download')
def download():
    url = request.args.get('url')
    format_id = request.args.get('format_id')

    if not url or not format_id:
        flash("Error: Missing video URL or format ID for download.", "danger")
        return redirect(url_for('index'))

    app.logger.info(f"Attempting download redirect for URL: {url}, Format: {format_id}")

    try:
        # Options specifically to get the direct download URL for the chosen format
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': format_id,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info *again*, focusing on the specific format to get its direct URL
            info_dict = ydl.extract_info(url, download=False)

            # Find the direct download URL. It might be top-level or in a nested format list.
            download_url = None
            if info_dict.get('url'): # Check top level first
                 # Verify if the top-level format matches the requested one, if format_id is available
                 if info_dict.get('format_id') == format_id or info_dict.get('requested_formats'):
                     # Sometimes 'requested_formats' holds the combined info
                     combined_format_info = info_dict.get('requested_formats', [{}])[0]
                     download_url = combined_format_info.get('url') or info_dict.get('url')
                 elif not info_dict.get('format_id') and not info_dict.get('requested_formats'):
                      # If no format_id at top level assume it's the correct one (less reliable)
                      download_url = info_dict.get('url')

            # If not found at top-level, search in 'formats' list
            if not download_url and 'formats' in info_dict:
                 for f in info_dict['formats']:
                     if f.get('format_id') == format_id and f.get('url'):
                         download_url = f['url']
                         break

            if download_url:
                app.logger.info(f"Redirecting user to download URL for format {format_id}")
                # Redirect the user's browser directly to the video file URL
                return redirect(download_url)
            else:
                app.logger.warning(f"Could not find direct download URL for format {format_id} in second extraction ({url}).")
                flash(f"Could not get direct download link for format {format_id}. It might have expired, be inaccessible, or require merging (not supported here).", "warning")
                return redirect(url_for('index', submitted_url=url)) # Pass url back

    # --- Enhanced Error Handling for Download Step ---
    except yt_dlp.utils.DownloadError as e:
        err_str = str(e).lower()
        app.logger.error(f"yt-dlp DownloadError during download step for URL {url}, format {format_id}: {e}")
        # Provide feedback via flash messages
        if "sign in to confirm" in err_str or "confirm you're not a bot" in err_str:
             flash("Download failed: Platform requires verification (Bot Detection). This is common on servers. Try again later or locally.", "danger")
        elif "video unavailable" in err_str:
             flash("Download failed: Video seems to have become unavailable.", "warning")
        elif "urlopen error" in err_str or "network error" in err_str or "timed out" in err_str:
             flash("Download failed: Network error getting the final download link.", "danger")
        elif "format is not available" in err_str: # Should be rare here, but possible
             flash(f"Download failed: Format {format_id} became unavailable.", "warning")
        else:
             flash(f"Error getting download link: {e}", "danger")
        return redirect(url_for('index', submitted_url=url)) # Pass url back
    except Exception as e:
        app.logger.error(f"General download error for URL {url} format {format_id}: {e}", exc_info=True)
        flash("An unexpected server error occurred preparing the download.", "danger")
        return redirect(url_for('index', submitted_url=url)) # Pass url back

if __name__ == '__main__':
    # Use environment variable for port if available (like Render does), default to 5000
    port = int(os.environ.get('PORT', 10000)) # Render uses 10000 for web services by default
    # Run with host='0.0.0.0' to be accessible externally (needed for Render)
    # debug=False is CRUCIAL for production/Render
    app.run(host='0.0.0.0', port=port, debug=False)