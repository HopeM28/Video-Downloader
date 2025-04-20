import os
from flask import Flask, render_template, request, redirect, url_for, flash
import yt_dlp
import json # For potential debugging, though not strictly necessary for core logic

app = Flask(__name__)
# It's good practice to set a secret key for Flask, especially if using sessions/flash messages
app.secret_key = os.urandom(24) # Generates a random key each time the app starts

# --- Helper Function to Sanitize Filenames ---
# yt-dlp usually handles this, but as a fallback or for display:
def sanitize_filename(name):
    """Removes characters that are problematic in filenames."""
    # Remove characters that are illegal in most filesystems
    name = "".join(c for c in name if c.isalnum() or c in (' ', '.', '_', '-')).rstrip()
    # Replace multiple spaces with a single space
    name = ' '.join(name.split())
    return name if name else "downloaded_video"

# --- yt-dlp Options ---
# Common options for extracting info
YDL_INFO_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'skip_download': True, # We only want metadata first
    'forcejson': True, # Makes sure we get JSON output if using CLI mode (less relevant for library)
    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', # Prioritize mp4
}

# Options for getting the direct download URL for a specific format
# Using --get-url is much more efficient than downloading via the server
YDL_DOWNLOAD_URL_OPTS_TEMPLATE = {
    'quiet': True,
    'no_warnings': True,
    'format': '{format_id}', # Placeholder for the specific format ID
    # '_get_url': True, # yt-dlp library equivalent of --get-url (less direct than extract_info)
                        # Instead, we'll extract 'url' from the format info
}


@app.route('/', methods=['GET', 'POST'])
def index():
    video_info = None
    error_message = None
    submitted_url = ""

    if request.method == 'POST':
        url = request.form.get('url', '').strip()
        submitted_url = url # Keep url in the input box

        if not url:
            error_message = "Please enter a video URL."
        else:
            try:
                # Use yt-dlp as a library to extract information
                with yt_dlp.YoutubeDL(YDL_INFO_OPTS) as ydl:
                    info_dict = ydl.extract_info(url, download=False)

                    # Check if extraction was successful and info_dict is not None
                    if not info_dict:
                         raise yt_dlp.utils.DownloadError("Could not extract video information. The URL might be invalid or private.")

                    # Process formats - filter for video+audio or just video if needed
                    formats = []
                    for f in info_dict.get('formats', []):
                        # Basic filtering: Ensure it has a URL, and often prefer combined formats or MP4
                        # You might want more sophisticated filtering based on 'vcodec', 'acodec'
                        if f.get('url') and f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                             # Try to get resolution, format note, or format id as quality label
                            quality_label = f.get('format_note') or f.get('resolution') or f"{f.get('height', 'N/A')}p"
                            filesize = f.get('filesize') or f.get('filesize_approx')
                            formatted_size = f"{filesize / (1024*1024):.2f} MB" if filesize else "N/A"

                            formats.append({
                                'format_id': f['format_id'],
                                'ext': f.get('ext', 'N/A'),
                                'quality': quality_label,
                                'filesize': formatted_size,
                                'vcodec': f.get('vcodec', 'N/A'),
                                'acodec': f.get('acodec', 'N/A'),
                                # Storing the direct URL here is possible but less common
                                # as these URLs might expire quickly. Better to get it on demand.
                            })
                        # Add more conditions if you want audio-only or video-only formats

                    # Sort formats by quality (height) if possible, descending
                    formats.sort(key=lambda x: int(x['quality'].replace('p','')) if x['quality'][:-1].isdigit() else 0, reverse=True)


                    video_info = {
                        'title': info_dict.get('title', 'N/A'),
                        'thumbnail': info_dict.get('thumbnail', None),
                        'formats': formats,
                        'original_url': url # Store original URL for download links
                    }

            except yt_dlp.utils.DownloadError as e:
                app.logger.error(f"yt-dlp error for URL {url}: {e}")
                # Try to provide a user-friendly error
                if "Unsupported URL" in str(e):
                    error_message = "Unsupported URL. Please check the link or try a different video platform (YouTube, Facebook supported)."
                elif "urlopen error" in str(e):
                     error_message = "Network error. Could not reach the video URL."
                elif "Video unavailable" in str(e):
                    error_message = "This video is unavailable (private, deleted, or restricted)."
                else:
                    error_message = f"Could not process link: {e}" # Or a generic message
            except Exception as e:
                app.logger.error(f"General error processing URL {url}: {e}")
                error_message = "An unexpected error occurred. Please try again later."

    # Pass submitted_url back to the template to keep it in the input box
    return render_template('index.html', video_info=video_info, error=error_message, submitted_url=submitted_url)


@app.route('/download')
def download():
    url = request.args.get('url')
    format_id = request.args.get('format_id')

    if not url or not format_id:
        flash("Error: Missing video URL or format ID for download.", "danger")
        return redirect(url_for('index'))

    try:
        # Options specifically to get the direct download URL for the chosen format
        # We don't use the template directly here, but construct opts dynamically
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': format_id,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info again, but focused on the specific format
            # This will contain the direct, possibly temporary, download URL
            info_dict = ydl.extract_info(url, download=False)

            # Find the specific format details within the result
            chosen_format = None
            if 'formats' in info_dict:
                 # If format_id refers to a combined format generated by yt-dlp (like 'bestvideo+bestaudio')
                 # the direct URL might be on the main info_dict level
                if info_dict.get('format_id') == format_id and info_dict.get('url'):
                    chosen_format = info_dict
                else:
                    # Otherwise, find it in the formats list
                    for f in info_dict['formats']:
                        if f['format_id'] == format_id:
                            chosen_format = f
                            break
            elif info_dict.get('format_id') == format_id and info_dict.get('url'):
                 # Handle cases where 'formats' list might be absent but top-level url exists for the format
                 chosen_format = info_dict


            if chosen_format and chosen_format.get('url'):
                download_url = chosen_format['url']
                # Redirect the user's browser directly to the video file URL
                # This avoids proxying the download through our server
                return redirect(download_url)
            else:
                flash(f"Could not find download link for format {format_id}.", "danger")
                return redirect(url_for('index', url=url)) # Redirect back, maybe keep URL

    except yt_dlp.utils.DownloadError as e:
        app.logger.error(f"yt-dlp download error for URL {url} format {format_id}: {e}")
        flash(f"Error getting download link: {e}", "danger")
        return redirect(url_for('index', url=url))
    except Exception as e:
        app.logger.error(f"General download error for URL {url} format {format_id}: {e}")
        flash("An unexpected error occurred while preparing the download.", "danger")
        return redirect(url_for('index', url=url))

if __name__ == '__main__':
    # Use environment variable for port if available (like Render does), default to 5000
    port = int(os.environ.get('PORT', 5000))
    # Run with host='0.0.0.0' to be accessible externally (needed for Render)
    # debug=True is useful for development but should be False in production
    app.run(host='0.0.0.0', port=port, debug=False) # SET debug=False FOR PRODUCTION/RENDER