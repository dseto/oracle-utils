#!/usr/bin/env bash
# gerado por harness-creator a partir de .harness/repo-profile.json - nao editar a mao
set -e

pip install -e .

pytest
