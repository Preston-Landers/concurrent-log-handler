#!/bin/bash
# Lowell's release script

# Install setuptools with less fuss 
#python ez_setup.py --user 

REV=`bzr version-info --custom --template '{revision_id} {date} [{clean}]'`
VER=`grep '^VERSION' setup.py | sed -re 's/^VERSION = [^0-9]([0-9a-fA-F.]+)[^0-9]/\1/'`

echo "=================================================== "
echo "  Building version $VER"
echo "  Revision (BZR)   $REV"
echo "=================================================== "
sleep 3

echo "Updating source file"
sed -i src/cloghandler.py -re "s/^__revision__ .*/__revision__  = '$REV'/"
sed -i src/cloghandler.py -re "s/^__version__ .*/__version__  = '$VER'/"
sleep 1

python setup.py sdist bdist_egg
python2.6 setup.py bdist_egg
python2.7 setup.py bdist_egg
python3.2 setup.py bdist_egg


#python setup.py build register sdist bdist_egg upload
#python2.7 setup.py bdist_egg upload
#python3.2 setup.py bdist_egg upload


