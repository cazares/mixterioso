
#!/usr/bin/env python3
from mix_utils import choose_mp3, run_demucs

def run():
    mp3 = choose_mp3()
    run_demucs(mp3)

if __name__ == "__main__":
    run()
# end of 2_split.py
