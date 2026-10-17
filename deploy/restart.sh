#!/bin/sh
set -eu
systemctl restart xcpcboard.service
systemctl --no-pager status xcpcboard.service
