mkdir -p scripts/_old_scripts

mv scripts/3_auto_timing.py                               scripts/_old_scripts/
mv scripts/3_auto_timing_whisperx.py                      scripts/_old_scripts/
mv scripts/aligner.py                                     scripts/_old_scripts/
mv scripts/app_align.py                                   scripts/_old_scripts/
mv scripts/mp3_txt_to_timings.py                          scripts/_old_scripts/
mv scripts/convert_timings_to_index_secs_text.py          scripts/_old_scripts/
mv scripts/vocal_windows.py                               scripts/_old_scripts/

# Move ALL old merge scripts
mv scripts/4_merge.py scripts/_old_scripts/4_merge__deprecated.py

