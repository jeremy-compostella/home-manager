#!/bin/sh
# This is a helper script to prevent secrets to be committed

sed "s/^\(login\|password\|key\|notify\|alert\|from\|email\|device_id\|address\)[[:space:]]*=.*/\1=<secret>/" "$@"
