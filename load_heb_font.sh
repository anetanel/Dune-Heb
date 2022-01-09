#!/bin/bash

../utils/font.py --load font_png/65L.png --output TMPCHAR.BIN --position 65 DUNECHAR.BIN
for i in {66..91}; do
	../utils/font.py --load font_png/${i}L.png --output TMPCHAR.BIN --position $i TMPCHAR.BIN
done

for i in 163 167 175 {193..219}; do
	../utils/font.py --load font_png/$i.png --output TMPCHAR.BIN --position $i TMPCHAR.BIN
done
for i in 34 35 39 45 {48..57}; do 
	../utils/font.py --load font_png/$i.png --output TMPCHAR.BIN --position $i TMPCHAR.BIN
done
