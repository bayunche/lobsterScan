#!/bin/bash
# Wrapper: use WSL-native nvm node + globally installed openclaw
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
exec openclaw "$@"
