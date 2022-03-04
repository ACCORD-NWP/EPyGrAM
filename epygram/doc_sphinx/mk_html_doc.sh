#!/bin/bash
export FOOTPRINT_DOCSTRINGS=2
fmt="html"

echo "> Create cmaps..."
python3 dynamic/make_cmaps_png_for_doc.py

echo "> List external dependancies..."
python3 ../../list_external_dependancies.py

cd source
echo "> Build cheatsheet..."
pdflatex cheatsheet.tex
if [ ! -d _static ]
then
  mkdir _static
fi
cp -f cheatsheet.pdf _static/.
cp -f cheatsheet.pdf ../html/_downloads/cheatsheet.pdf

echo "> Build Sphinx doc..."
bld=`which sphinx-build-3`
if [ "$?" != "0" ]
then
  bld=`which sphinx-build`
fi
$bld -b $fmt . ../$fmt
cd ..
