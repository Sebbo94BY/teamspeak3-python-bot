#!/usr/bin/env bash

VENV_DIR="${1}"
REQUIREMENTS_HASH_FILE="${VENV_DIR}/.requirements.sha256"
REQUIREMENTS_HASH="$(sha256sum requirements.txt | awk '{print $1}')"

if [[ -x "${VENV_DIR}/bin/python" ]] \
    && [[ -f "${REQUIREMENTS_HASH_FILE}" ]] \
    && [[ "$(<"${REQUIREMENTS_HASH_FILE}")" == "${REQUIREMENTS_HASH}" ]]; then
    echo "Python virtual env is already up to date."
    exit 0
fi

echo "Setting up Python virtual env. This takes a moment, please wait..."
if [[ -d "${VENV_DIR}" ]]; then
    rm -rf "${VENV_DIR}"
fi

python3 -m venv "${VENV_DIR}"

if [[ $? -eq 0 ]]; then
    source "${VENV_DIR}/bin/activate"
else
    echo "Failed to set up the Python virtual env directory."
    exit 1
fi

# The `wheel` package needs to be installed before all other dependencies:
# https://stackoverflow.com/questions/74436681/deprecation-error-wheel-package-is-not-installed
pip install --quiet wheel
pip install --quiet -r requirements.txt

if [[ $? -ne 0 ]]; then
    echo "Failed to install the Python requirements in the virtual env."
    exit 1
fi

printf '%s\n' "${REQUIREMENTS_HASH}" > "${REQUIREMENTS_HASH_FILE}"

echo "Python virtual env has been successfully set up."

echo "You will find the application logs within the installation directory."
