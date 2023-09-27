#!/bin/sh
# two options:
# 1. copy grub binary
# 2. copy grub modules and call cobbler mkloaders
# option 2 is more in line with cobbler and is tried first. if modules are missing and a binary exists, option 1 is used for this architecture

pkgs=$(zypper se tftpboot-installation | awk -F'|' '/tftp installation tree/ {print $2}')

linkOrCopy () {
	source=$1
	dest=$(dirname $2)
	# compare device number of both paths to see if a hardlink is possible
	if [ $(stat --format=%d $source) -eq $(stat --format=%d $dest) ]
	then
		cmd=ln
	else
		cmd=cp
	fi
	$cmd $source $dest
}

installIfMissing () {
	pkg=$1
	if [ $(rpm -q $pkg) -eq 1 ]
	then
		zypper install $pkg
	fi
}

containsGrubMods() {
	rpm -ql $1 | grep hello.mod
}

for pkg in $pkgs
do
	installIfMissing $pkg
	if containsGrubMods $pkg
	then
		# find dir with modules, then copy/link
	else
		# copy/link binaries
done

# it's okay if cobbler replaces copied bootloaders, we just want to make sure we have one per arch
cobbler mkloaders
