SCRIPT_PATH=${BASH_SOURCE-$0}
SCRIPT_PATH=$(dirname "$SCRIPT_PATH")
SCRIPT_PATH=$(realpath "$SCRIPT_PATH")

function qa_prepare_all {
    pip install ruff
}

function qa_check {
    ruff check --config "$SCRIPT_PATH/ruff.toml" "$1"
}

function qa_fix {
    ruff check --config "$SCRIPT_PATH/ruff.toml" --fix "$1"
}

function qa_device_check {
    qa_check device/
}

function qa_tools_check {
    qa_check tools/
}

function qa_tests_check {
    qa_check tests/
}

function qa_tests_run {
    python3 -m unittest discover -s tests -v
}
