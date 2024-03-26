import argparse
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from functools import partial

from conf import exclude_patterns

# Global constants
server_repo_path = os.getcwd()
server_docs_dir_path = os.path.join(os.getcwd(), "docs")

# Regex patterns
http_reg = r"^https?://"
tag_reg = "/(?:blob|tree)/main"
triton_repo_reg = rf"{http_reg}github.com/triton-inference-server"
triton_github_url_reg = rf"{triton_repo_reg}/([^/#]+)(?:{tag_reg})?/*([^#]*)\s*(?=#|$)"
relpath_reg = r"]\s*\(\s*([^)]+)\)"
# Hyperlink excluding embedded images in a .md file.
hyperlink_reg = r"((?<!\!)\[[^\]]+\]\s*\(\s*)([^)]+?)(\s*\))"

# Parser
parser = argparse.ArgumentParser(description="Process some arguments.")
parser.add_argument(
    "--repo-tag", action="append", help="Repository tags in format key:value"
)
parser.add_argument(
    "--backend", action="append", help="Repository tags in format key:value"
)
parser.add_argument("--github-organization", help="GitHub organization name")


def setup_logger():
    # Create a custom logger
    logger = logging.getLogger(__name__)

    # Set the log level
    logger.setLevel(logging.INFO)

    # Create handlers
    file_handler = logging.FileHandler("/tmp/docs.log")

    # Create formatters and add it to the handlers
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(formatter)

    # Add handlers to the logger
    logger.addHandler(file_handler)

    return logger


def log_message(message):
    # Setup the logger
    logger = setup_logger()

    # Log the message
    logger.info(message)


def run_command(command):
    print(command)
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        log_message(result.stdout)
    except subprocess.CalledProcessError as e:
        log_message(f"Error executing command: {e.cmd}")
        log_message(e.output)
        log_message(e.stderr)


def clone_from_github(repo, tag, org):
    # Construct the full GitHub repository URL
    repo_url = f"https://github.com/{org}/{repo}.git"
    print(repo_url)
    # Construct the git clone command
    if tag:
        clone_command = [
            "git",
            "clone",
            "--branch",
            tag[0],
            "--single-branch",
            repo_url,
        ]
    else:
        clone_command = ["git", "clone", repo_url]

    # Execute the git clone command
    try:
        subprocess.run(clone_command, check=True)
        log_message(f"Successfully cloned {repo}")
    except subprocess.CalledProcessError as e:
        log_message(f"Failed to clone {repo}. Error: {e}")


def parse_repo_tag(repo_tags):
    repo_dict = defaultdict(list)
    for tag in repo_tags:
        key, value = tag.split(":", 1)
        repo_dict[key].append(value)
    return dict(repo_dict)


def is_excluded(file_path):
    for exclude_pattern in exclude_patterns:
        file_abspath = os.path.abspath(file_path)
        exclude_pattern = os.path.abspath(exclude_pattern)
        if os.path.commonpath([file_abspath, exclude_pattern]) == exclude_pattern:
            return True
    return False


# Return the Git repo name of given file path
def get_git_repo_name(file_path):
    # Execute git command to get remote URL
    try:
        # Get the directory containing the file
        directory = os.path.dirname(file_path)
        # Execute git command with the file's directory as the cwd
        remote_url = (
            subprocess.check_output(
                ["git", "-C", directory, "remote", "get-url", "origin"]
            )
            .decode()
            .strip()
        )
    except subprocess.CalledProcessError:
        return None

    # Extract repository name from the remote URL.
    if remote_url.endswith(".git"):
        # Remove '.git' extension.
        remote_url = remote_url[:-4]
    repo_name = os.path.basename(remote_url)
    return repo_name


def replace_url_with_relpath(url, src_doc_path):
    """
    This function replaces Triton Inference Server GitHub URLs with relative paths in following cases.
    1. URL is a doc file not in exclude_patterns, e.g. ".md" file.
    2. URL is a directory which contains README.md and URL ends with a hashtag.
        README.md is not in exclude_patterns.
    """
    m = re.match(triton_github_url_reg, url)
    # Do not replace URL if it is not a Triton GitHub file.
    if not m:
        return url

    target_repo_name = m.group(1)
    target_relpath_from_target_repo = os.path.normpath(m.groups("")[1])
    section = url[len(m.group(0)) :]
    valid_hashtag = section not in ["", "#"] and section.startswith("#")

    if target_repo_name == "server":
        target_path = os.path.join(server_repo_path, target_relpath_from_target_repo)
    else:
        target_path = os.path.join(
            server_docs_dir_path, target_repo_name, target_relpath_from_target_repo
        )

    # Return URL if it points to a path outside server/docs.
    if os.path.commonpath([server_docs_dir_path, target_path]) != server_docs_dir_path:
        return url

    if (
        os.path.isfile(target_path)
        and os.path.splitext(target_path)[1] == ".md"
        and not is_excluded(target_path)
    ):
        pass
    elif (
        os.path.isdir(target_path)
        and os.path.isfile(os.path.join(target_path, "README.md"))
        and valid_hashtag
        and not is_excluded(os.path.join(target_path, "README.md"))
    ):
        target_path = os.path.join(target_path, "README.md")
    else:
        return url

    # The "target_path" must be a file at this line.
    relpath = os.path.relpath(target_path, start=os.path.dirname(src_doc_path))
    return re.sub(triton_github_url_reg, relpath, url, 1)


def replace_relpath_with_url(relpath, src_doc_path):
    """
    TODO: Need to update comment
    This function replaces relative paths with Triton Inference Server GitHub URLs in following cases.
    1. Relative path is pointing to a directory or file inside the same repo (excluding server).
    2. URL is a directory which contains README.md and URL has a hashtag.
    """
    target_path = relpath.rsplit("#")[0]
    section = relpath[len(target_path) :]
    valid_hashtag = section not in ["", "#"] and section.startswith("#")
    target_path = os.path.join(os.path.dirname(src_doc_path), target_path)
    target_path = os.path.normpath(target_path)
    src_git_repo_name = get_git_repo_name(src_doc_path)

    url = f"https://github.com/triton-inference-server/{src_git_repo_name}/blob/main/"
    if src_git_repo_name == "server":
        src_repo_abspath = server_repo_path
        # TODO: Assert the relative path not pointing to cloned repo, e.g. client.
        # This requires more information which may be stored in a global variable.
    else:
        src_repo_abspath = os.path.join(server_docs_dir_path, src_git_repo_name)

    # Assert target path is under the current repo directory.
    assert os.path.commonpath([src_repo_abspath, target_path]) == src_repo_abspath

    target_path_from_src_repo = os.path.relpath(target_path, start=src_repo_abspath)

    if os.path.exists(target_path) and (
        os.path.isdir(target_path)
        and valid_hashtag
        and not is_excluded(os.path.join(target_path, "README.md"))
        or os.path.isfile(target_path)
        and os.path.splitext(target_path)[1] == ".md"
        and not is_excluded(target_path)
    ):
        return relpath
    else:
        return url + target_path_from_src_repo + section

    # TODO: Compare which version is more concise
    # if not os.path.exists(target_path) or \
    #    os.path.isfile(target_path) and os.path.splitext(target_path)[1] != ".md" or \
    #    os.path.isdir(target_path) and not valid_hashtag:
    #     return url + target_path_from_src_repo + section
    # else:
    #     return relpath


def replace_hyperlink(m, src_doc_path):
    # TODO: Markdown allows <link>, e.g. <a href=[^>]+>. Whether we want to
    # find and replace the link depends on if they link to internal .md files
    # or allows relative paths. I haven't seen one such case in our doc so
    # should be safe for now.
    hyperlink_str = m.group(2)
    match = re.match(http_reg, hyperlink_str)

    if match:
        # Hyperlink is a URL.
        res = replace_url_with_relpath(hyperlink_str, src_doc_path)
    else:
        # Hyperlink is a relative path.
        res = replace_relpath_with_url(hyperlink_str, src_doc_path)

    # TODO: This can be improved. One way is to replace m.group(2) only.
    return m.group(1) + res + m.group(3)


def preprocess_docs(exclude_paths=[]):
    # Find all ".md" files inside the current repo.
    if exclude_paths:
        cmd = (
            ["find", server_docs_dir_path, "-type", "d", "\\("]
            + " -o ".join([f"-path './{dir}'" for dir in exclude_paths]).split(" ")
            + ["\\)", "-prune", "-o", "-type", "f", "-name", "'*.md'", "-print"]
        )
    else:
        cmd = ["find", server_docs_dir_path, "-name", ".md"]
    cmd = " ".join(cmd)
    result = subprocess.run(cmd, check=True, capture_output=True, text=True, shell=True)
    docs_list = list(filter(None, result.stdout.split("\n")))

    # Read, preprocess and write back to each document file.
    for doc_abspath in docs_list:
        if is_excluded(doc_abspath):
            continue

        content = None
        with open(doc_abspath, "r") as f:
            content = f.read()

        content = re.sub(
            hyperlink_reg,
            partial(replace_hyperlink, src_doc_path=doc_abspath),
            content,
        )

        with open(doc_abspath, "w") as f:
            f.write(content)


def main():
    args = parser.parse_args()
    repo_tags = parse_repo_tag(args.repo_tag) if args.repo_tag else {}
    backend_tags = parse_repo_tag(args.backend) if args.backend else {}
    github_org = args.github_organization
    print("Parsed repository tags:", repo_tags)
    print("Parsed repository tags:", backend_tags)

    # Change working directory to server/docs.
    os.chdir(server_docs_dir_path)

    if "client" in repo_tags:
        clone_from_github("client", repo_tags["client"], github_org)
    if "python_backend" in repo_tags:
        clone_from_github("python_backend", repo_tags["python_backend"], github_org)
    if "custom_backend" in backend_tags:
        clone_from_github("custom_backend", backend_tags["custom_backend"], github_org)

    # Preprocess documents in server_docs_dir_path after all repos are cloned.
    preprocess_docs()
    log_message("Running Docker CREATE")
    run_command("make html")

    # Clean up working directory.
    if "client" in repo_tags:
        run_command("rm -rf client")
    if "python_backend" in repo_tags:
        run_command("rm -rf python_backend")
    if "custom_backend" in backend_tags:
        run_command("rm -rf custom_backend")

    # Return to previous working directory server/.
    os.chdir(server_repo_path)


if __name__ == "__main__":
    main()
