#!/bin/sh
set -eu
systemctl stop xcpcboard.service
systemctl --no-pager status xcpcboard.service
