import yt_dlp

def search_youtube(query, max_results=10):
    """
    Search YouTube for the given query and return a list of results sorted by view count.
    """
    ydl_opts = {'quiet': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            # Use ytsearch to get a list of video results for the query
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        except Exception as e:
            print(f"Search error: {e}")
            return []
    entries = info.get('entries', [])
    if not entries:
        return []
    # Sort results by view count (most popular first)
    for entry in entries:
        if entry.get('view_count') is None:
            entry['view_count'] = 0
    entries.sort(key=lambda x: x.get('view_count', 0), reverse=True)
    return entries

def download_audio(video_info):
    """
    Download the audio for the given video as an MP3 file using yt-dlp.
    Returns the output filename, or None if download failed.
    """
    video_url = video_info.get('webpage_url') or video_info.get('url')
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,  # Suppress verbose output, will handle messaging in script
        'postprocessors': [{  # Use FFmpeg to convert to mp3
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192'
        }],
        'outtmpl': '%(title)s.%(ext)s'  # Save file as "<title>.mp3"
    }
    output_file = None
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(video_url, download=True)
            # Construct the output file name from returned info (title and ext)
            title = info.get('title', 'audio')
            ext = info.get('ext', 'mp3')
            output_file = f"{title}.{ext}"
        except Exception as e:
            print(f"Error downloading audio: {e}")
    return output_file

def main():
    print("=== YouTube Audio Search & Download ===")
    while True:
        # Choose search mode
        mode = input("Enter '1' to search by artist and title, or '2' for a custom search query: ").strip()
        if mode == '1':
            artist = input("Artist name: ").strip()
            song = input("Song title: ").strip()
            query = f"{artist} {song}"
        else:
            query = input("Enter search query: ").strip()
        if not query:
            print("No search query entered. Exiting.")
            break

        # Search YouTube for the query
        results = search_youtube(query, max_results=10)
        if not results:
            print(f"No results found for '{query}'.")
            refine = input("No results. Would you like to try a different search? (y/n): ").strip().lower()
            if refine == 'y':
                continue  # restart the loop with a new query
            else:
                break     # exit the program

        print(f"\nFound {len(results)} results for \"{query}\". Showing the most viewed result first.")
        found = False
        exit_program = False
        refine_search = False

        # Iterate over the sorted results
        for idx, video in enumerate(results, start=1):
            title = video.get('title', 'Unknown Title')
            uploader = video.get('channel') or video.get('uploader', 'Unknown')
            views = video.get('view_count', 0)
            print(f"\nResult {idx}: \"{title}\" by {uploader} ({views} views)")
            choice = input("Download this audio? [y = yes, n = next, q = quit]: ").strip().lower()

            if choice == 'y':
                # Download the chosen video as MP3
                print("Downloading and converting to MP3...")
                file_name = download_audio(video)
                if file_name:
                    print(f"Downloaded: {file_name}")
                else:
                    print("Failed to download this result.")
                # Let user verify if this is the correct audio
                verify = input("Is this the correct audio you were looking for? (y/n): ").strip().lower()
                if verify == 'y':
                    print("Great! The audio has been saved. Enjoy your music!")
                    found = True
                    break  # exit the results loop
                else:
                    print("Understood. We'll try the next result...")
                    # continue to next result without breaking, i.e., loop continues
                    continue

            elif choice == 'n':
                # User wants to see the next result
                continue  # move to the next video in results list

            else:
                # User chose to quit or refine (any input other than 'y' or 'n')
                ref = input("Would you like to refine your search instead of exiting? (y/n): ").strip().lower()
                if ref == 'y':
                    refine_search = True   # flag to indicate we will refine search
                else:
                    exit_program = True    # flag to exit completely
                break  # break out of the for-loop

        # End of for-loop over results

        if found:
            # Desired audio found and confirmed; end the outer loop and program
            break
        if exit_program:
            # User chose to quit entirely
            print("Exiting the program. Goodbye!")
            break
        if refine_search:
            # User wants to refine the search query and try again
            print("\n*** Refine Search: Starting a new search... ***")
            continue  # go back to the beginning of the while loop for new input

        # If we exhausted all results without finding the correct audio
        if not found:
            refine = input("\nReached end of results. Would you like to refine your search and try again? (y/n): ").strip().lower()
            if refine == 'y':
                print("\n*** Starting a new search... ***")
                continue
            else:
                print("No more results to try. Exiting.")
                break

if __name__ == "__main__":
    main()

# end of youtube_audio_downloader.py