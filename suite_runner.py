import json
import requests
import time
import re
import os
import sys
import readchar
import glob

# Enable ANSI color support on Windows
if sys.platform == 'win32':
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

# Config
LLAMA_CPP_URL = "http://localhost:1234/v1/chat/completions"
MODEL_NAME = r"W:\\AI\\LLM\\models\\mradermacher\\Loki-v2.75b-8b-EROTICA-1024k-i1-GGUF\\Loki-v2.75b-8b-EROTICA-1024k.i1-Q4_K_S.gguf"
API_KEY = "sk-xxx"
USE_ANSI_COLORS = True

TEST_FILE = "reasoning_suite.jsonl"
RESULT_FILE = "test_results.json"

# Load tests
def load_tests(filename):
    tests = []
    with open(filename, 'r', encoding='utf-8') as f:
        for line in f:
            tests.append(json.loads(line.strip()))
    return tests

def load_results(filename):
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return []

def save_results(filename, data):
    with open(filename, 'w', encoding='utf-8') as out:
        json.dump(data, out, indent=2)

def highlight_terms(text, terms):
    for term in terms:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        if USE_ANSI_COLORS:
            text = pattern.sub(f'\033[91m{term}\033[0m', text)
        else:
            text = pattern.sub(f'[[HIGHLIGHT]]{term}[[/HIGHLIGHT]]', text)
    return text

def query_model(prompt, temperature=0.7, max_tokens=512):
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You are a game narrator."},
            {"role": "user", "content": prompt}
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False
    }

    response = requests.post(LLAMA_CPP_URL, headers=headers, json=payload)
    response.raise_for_status()
    result = response.json()
    return result["choices"][0]["message"]["content"].strip()

# === Unified Menu Drawing ===

def draw_menu(title, options, index, last_block_lines, highlight=None):
    current_menu_lines = len(options) + 2

    move_up_lines = last_block_lines
    clear_lines = last_block_lines - current_menu_lines

    if move_up_lines > 0:
        print(f"\033[{move_up_lines}F", end="")

    # Header
    print("\033[2K", end="")  # Clear full line
    print(f"=== {title} ===")
    print("\033[2K", end="")  # Clear full line for empty line
    print()

    for i, (code, desc) in enumerate(options):
        print("\033[2K", end="")
        prefix = " >" if i == index else "  "
        if code == highlight and USE_ANSI_COLORS:
            code_str = f"\033[91m{code.ljust(12)}\033[0m"
        else:
            code_str = code.ljust(12)
        print(f"{prefix} {code_str} - {desc}")

    if clear_lines > 0:
        for _ in range(clear_lines):
            print("\033[2K", end="")
            print(" " * 80)

    block_lines = max(current_menu_lines, last_block_lines)
    return block_lines

# === Menu Logic ===

def grading_menu(last_block_lines, expected_failure=None):
    options = [
        ("PASS", "Fully correct"),
        ("PASS_BUT", "Correct, but minor issue"),
        ("PARTIAL", "Minor deviation"),
        ("FAIL", "Major reasoning or role failure"),
        ("RETRY", "Rerun this test")
    ]
    index = 0
    block_lines = last_block_lines

    while True:
        block_lines = draw_menu("SCORING", options, index, block_lines)

        key = readchar.readkey()
        if key == readchar.key.UP:
            index = (index - 1) % len(options)
        elif key == readchar.key.DOWN:
            index = (index + 1) % len(options)
        elif key == readchar.key.ENTER:
            choice = options[index][0]
            if choice == "FAIL":
                reason, sub_block = failure_menu(block_lines, expected_failure)
                if reason is None:
                    block_lines = sub_block
                    continue
                return "fail", reason, None
            elif choice == "PASS_BUT":
                annotation, sub_block = passbut_menu(block_lines)
                if annotation is None:
                    block_lines = sub_block
                    continue
                return "pass", None, annotation
            elif choice == "RETRY":
                return "retry", None, None
            else:
                return choice.lower(), None, None
        elif key in (readchar.key.BACKSPACE, readchar.key.ESC):
            continue

def failure_menu(last_block_lines, expected_failure=None):
    options = [
        ("F-ROLE", "Role Violation (acted for player, invented speech)"),
        ("F-PACING", "Pacing Violation (advanced too far)"),
        ("F-STATE", "World State Violation (wrong object/character state)"),
        ("F-LOGIC", "Reasoning Failure (math/contradiction error)"),
        ("F-SCAFFOLD", "Scaffold Leakage (instruction injected)"),
        ("F-STRUCT", "Structure Collapse (<|im_start|>user etc.)"),
        ("F-ECHO", "Prompt Echo Artifact (repeating input)"),
        ("F-OTHER", "Other Failure")
    ]
    
    index = 0
    if expected_failure:
        for i, (code, _) in enumerate(options):
            if code == expected_failure:
                index = i
                break

    block_lines = last_block_lines

    while True:
        block_lines = draw_menu("FAILURE REASON", options, index, block_lines, highlight=expected_failure)

        key = readchar.readkey()
        if key == readchar.key.UP:
            index = (index - 1) % len(options)
        elif key == readchar.key.DOWN:
            index = (index + 1) % len(options)
        elif key == readchar.key.ENTER:
            return options[index][0], block_lines
        elif key in (readchar.key.BACKSPACE, readchar.key.ESC):
            return None, block_lines

def passbut_menu(last_block_lines):
    options = [
        ("P-REASONING", "Weak reasoning / hesitant logic"),
        ("P-PACING", "Mild pacing issue / early advance"),
        ("P-STATE", "Minor world state inconsistency"),
        ("P-STYLE", "Stylistic issue / verbosity / confidence"),
        ("P-STRUCT", "Minor structure token drift (<|im_start|>, <|im_end|>)"),
        ("P-ECHO", "Minor repetition / echoing input"),
        ("P-OTHER", "Other minor issue")
    ]
    index = 0
    block_lines = last_block_lines

    while True:
        block_lines = draw_menu("PASS ANNOTATION", options, index, block_lines)

        key = readchar.readkey()
        if key == readchar.key.UP:
            index = (index - 1) % len(options)
        elif key == readchar.key.DOWN:
            index = (index + 1) % len(options)
        elif key == readchar.key.ENTER:
            return options[index][0], block_lines
        elif key in (readchar.key.BACKSPACE, readchar.key.ESC):
            return None, block_lines

# === Test loop ===

def rotate_completed_log():
    existing_logs = sorted(glob.glob("test_results_*.json"))
    if existing_logs:
        last = int(existing_logs[-1].split("_")[-1].split(".")[0])
        next_index = last + 1
    else:
        next_index = 1
    archive_name = f"test_results_{next_index:03d}.json"
    os.rename(RESULT_FILE, archive_name)
    print(f"\nAll tests completed! Archived as {archive_name}\n")

def run_tests():
    all_tests = load_tests(TEST_FILE)
    existing_results = load_results(RESULT_FILE)
    existing_tests_by_id = {t["test_id"]: t for t in existing_results}
    merged_tests = []

    for test in all_tests:
        if test["test_id"] in existing_tests_by_id:
            result_entry = existing_results[test["test_id"]]
            test.update(result_entry)
        merged_tests.append(test)

    for test in merged_tests:
        if "result" in test:
            continue

        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"=== TEST {test['test_id']}: {test['description']} ===")
            print(f"PROMPT:\n{test['prompt']}\n")

            try:
                response = query_model(test['prompt'])
                test['raw_output'] = response
            except Exception as e:
                print("[ERROR]", e)
                break

            highlighted_response = highlight_terms(response, test.get('highlight_terms', []))
            print("MODEL RESPONSE:\n", highlighted_response)

            result, reason, pass_annotation = grading_menu(
                last_block_lines=0,
                expected_failure=test.get('expected_failure')
            )

            if result == 'retry':
                print("\nRe-running test...")
                time.sleep(1)
                continue
            else:
                test['result'] = result
                if reason:
                    test['reason'] = reason
                if pass_annotation:
                    test['pass_annotation'] = pass_annotation
                save_results(RESULT_FILE, merged_tests)
                time.sleep(1)
                break

    if all('result' in t for t in merged_tests):
        rotate_completed_log()

if __name__ == "__main__":
    run_tests()
