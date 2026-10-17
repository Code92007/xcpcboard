#!/bin/sh
set -eu
systemctl start xcpcboard.service
systemctl --no-pager status xcpcboard.service
