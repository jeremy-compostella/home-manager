#!/bin/sh
# This is a helper script to prevent secrets to be committed

sed "s/^\(login\|password\|key\|to\|from\)[[:space:]]*=.*/\1=<secret>/" "$@"
